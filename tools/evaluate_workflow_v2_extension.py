#!/usr/bin/env python3
"""Container-only generation/collection for the v2 extension evaluation."""
from __future__ import annotations
import argparse
import copy
import hashlib
import json
from pathlib import Path
import platform
import re
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'tests'))
import yaml
from workflow_model_v2.builder import compile_pair_v2
from workflow_model_v2.extended import compile_extended
from workflow_model_v2.reference_cache import explore_cache
from workflow_model_v2.trace_mapper import map_extended_trace, validate_sources
from workflow_model_v2.evidence import read_prototype
from test_workflow_model_v2_extended import cache_pair, context


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def generate(out):
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"output directory must be empty: {out}")
    out.mkdir(parents=True, exist_ok=True)
    records = []
    producer = ROOT / '.github/workflows/artifact-a1-pr-producer.yml'
    for case, consumer in [('A1', 'artifact-a1-unsafe-consumer.yml'), ('A2', 'artifact-a2-safe-consumer.yml'),
                           ('A3', 'artifact-a3-download-only-consumer.yml'), ('A4', 'artifact-a4-no-authority-consumer.yml'),
                           ('A5', 'artifact-a5-attest-consumer.yml')]:
        p = ROOT / '.github/workflows/artifact-a5-attest-producer.yml' if case == 'A5' else producer
        c = ROOT / '.github/workflows' / consumer
        text, mapping = compile_pair_v2(p, c)
        target = out / 'baseline' / case
        target.parent.mkdir(parents=True, exist_ok=True)
        target.with_suffix('.smv').write_text(text)
        dump(target.with_suffix('.map.json'), mapping)
        records.append(dict(id=case, model=str(target.with_suffix('.smv').relative_to(out)), expected=None, engine='prototype'))
    from workflow_model_v2 import mutation_matrix
    mutation_matrix.OUT = out / 'mutations'
    mutation_matrix.main()
    for record in json.loads((mutation_matrix.OUT / 'matrix.json').read_text()):
        records.append(dict(id='legacy-' + record['mutation'], model='mutations/' + record['model'], expected=None, engine='prototype'))
    # A later rejected read must not erase a preceding read history.
    audit = out / 'audit-history'
    audit.mkdir(parents=True, exist_ok=True)
    a2 = (ROOT / '.github/workflows/artifact-a2-safe-consumer.yml').read_text()
    earlier = '''      - name: Read before verification
        env:
          ARTIFACT_FILE: ${{ runner.temp }}/trust-boundary-artifact/payload.txt
        run: |
          request=$(tr -d '\\r\\n' < "$ARTIFACT_FILE")

'''
    a2 = a2.replace('      - name: Verify artifact before use\n', earlier + '      - name: Verify artifact before use\n')
    (audit / 'consumer.yml').write_text(a2)
    text, mapping = compile_pair_v2(producer, audit / 'consumer.yml')
    text += '\nCTLSPEC AG !(integrity_blocked & artifact_dependent_use)\n'
    (audit / 'model.smv').write_text(text)
    dump(audit / 'model.map.json', mapping)
    records.append(dict(id='audit-use-before-later-block', engine='prototype', model='audit-history/model.smv', expected=None))

    p, c, scenario = cache_pair()
    variants = []
    def add(name, pp=p, cc=c, ss=scenario, expected=True, reference=True):
        variants.append((name, pp, cc, copy.deepcopy(ss), expected, reference))
    add('cache-prefix-unsafe')
    add('cache-key-mismatch', cc=c.replace('linux-pkg-', 'different-'), expected=False)
    s = copy.deepcopy(scenario)
    s['runs'][0].update(event='pull_request', scope='refs/pull/7/merge')
    add('cache-pr-scope-isolation', pp=p.replace('pull_request_target', 'pull_request'), ss=s, expected=False)
    s = copy.deepcopy(scenario)
    s['runs'][1]['cacheVersion']['data'] = 'version2'
    add('cache-version-mismatch', ss=s, expected=False)
    s = copy.deepcopy(scenario)
    s['runs'][1]['repository'] = 'other/repository'
    add('cache-repository-mismatch', ss=s, expected=False)
    add('cache-write-disabled', pp=p.replace('      - uses: actions/cache/save@v4', '      - if: false\n        uses: actions/cache/save@v4'), expected=False)
    add('cache-read-disabled', cc=c.replace('      - id: cache', '      - if: false\n        id: cache'), expected=False)
    add('cache-no-authority', cc=c.replace('contents: write', 'contents: read'), expected=False)
    add('cache-no-stage', cc=c.replace('      - run: git add data\n', ''), expected=False)
    s = copy.deepcopy(scenario)
    s['cacheIO'] = False
    add('cache-save-warning-no-object', ss=s, expected=False)
    add('cache-exact-hit', pp=p.replace('linux-pkg-new', 'linux-pkg-current'))
    add('cache-output-guard', cc=c.replace('      - run: git add data', "      - if: steps.cache.outputs.cache-hit == 'false'\n        run: git add data"))
    add('cache-output-contradiction', cc=c.replace('      - run: git add data', "      - if: steps.cache.outputs.cache-hit == 'false' && steps.cache.outputs.cache-hit != 'false'\n        run: git add data"), expected=False)
    recovered = c.replace('      - run: git add data', '      - run: exit 1\n      - if: failure()\n        run: git add data').replace('      - run: git push', '      - if: always()\n        run: git push')
    add('status-failure-always-recovery', cc=recovered)
    add('status-default-skip-after-failure', cc=c.replace('      - run: git add data', '      - run: exit 1\n      - run: git add data'), expected=False)
    add('status-cancelled-cleanup', cc=c.replace('      - run: git add data', '      - if: cancelled()\n        run: git add data').replace('      - run: git push', '      - if: cancelled()\n        run: git push'), reference=False)
    add('cache-post-save', pp=p.replace('actions/cache/save@v4', 'actions/cache@v4'))
    add('cache-post-job-failure', pp=p.replace('actions/cache/save@v4', 'actions/cache@v4') + '      - run: exit 1\n', expected=False)
    add('cache-post-continue-on-error', pp=p.replace('actions/cache/save@v4', 'actions/cache@v4') + '      - run: exit 1\n        continue-on-error: true\n')
    s = copy.deepcopy(scenario)
    s['cachePolicy'] = 'github-current-2026-10-22'
    add('cache-current-default-read', ss=s, expected=False)
    add('cache-current-explicit-write', pp=p.replace('permissions:', 'cache-mode: write\npermissions:'), ss=s)
    multiple = p + "      - run: echo 'clean' > data\n      - uses: actions/cache/save@v4\n        with: {key: linux-pkg-clean, path: data}\n"
    s = copy.deepcopy(scenario)
    s.update(operationOutcome=True, cancelRequests=False, cacheIO=True)
    add('cache-newest-prefix-is-clean', pp=multiple, ss=s, expected=False)
    add('cache-exact-before-newest-prefix', pp=multiple, cc=c.replace('key: linux-pkg-current', 'key: linux-pkg-new'), ss=s)
    add('unsupported-shell', cc=c.replace('cat data', 'eval input'), expected=None, reference=False)
    add('unsupported-action', pp=p.replace('actions/cache/save@v4', 'actions/cache/save@unregistered'), expected=None, reference=False)
    add('unsupported-cache-key-collation', pp=p.replace('linux-pkg-new', 'Linux-pkg-new'), expected=None, reference=False)
    add('static-matrix', pp=p.replace('    steps:', '    strategy:\n      fail-fast: false\n      matrix: {platform: [linux, windows]}\n    steps:'), reference=False)
    for name, pp, cc, ss, expected, reference in variants:
        target = out / 'extended' / name
        target.mkdir(parents=True, exist_ok=True)
        paths = [target / 'producer.yml', target / 'consumer.yml']
        for path, source in zip(paths, [pp, cc]):
            path.write_text(source)
        dump(target / 'scenario.json', ss)
        start = time.monotonic()
        text, mapping = compile_extended(paths, ss)
        duration = time.monotonic() - start
        (target / 'model.smv').write_text(text)
        dump(target / 'model.map.json', mapping)
        record = dict(id=name, engine='extended', model=str((target / 'model.smv').relative_to(out)), expected=expected,
                      generationSeconds=duration, stateVariables=mapping['stateVariables'], verdict=mapping['verdict'])
        if reference:
            start = time.monotonic()
            result = explore_cache(paths, ss)
            result['seconds'] = time.monotonic() - start
            dump(target / 'reference-bfs.json', result)
            record['bfs'] = result
        records.append(record)
    ap = '''name: Artifact Producer
on: pull_request
permissions: {contents: read}
jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: {persist-credentials: false}
      - id: upload
        uses: actions/upload-artifact@v4
        with: {name: payload, path: data}
'''
    ac = '''name: Artifact Consumer
on: push
permissions: {actions: read, contents: write}
jobs:
  read:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: payload
          path: data
          run-id: writer
          github-token: ${{ secrets.GITHUB_TOKEN }}
      - run: cat data
      - run: git add data
      - run: git push
'''
    ss = dict(cachePolicy='github-historical-2025', runs=[context(id='writer', event='pull_request', activity='opened', scope='refs/pull/1/merge'), context(id='reader', after='writer')])
    artifacts = [('artifact-cross-run', ap, ac, copy.deepcopy(ss), True),
                 ('artifact-name-mismatch', ap, ac.replace('name: payload', 'name: other'), copy.deepcopy(ss), False),
                 ('artifact-run-mismatch', ap, ac.replace('run-id: writer', 'run-id: unknown'), copy.deepcopy(ss), False),
                 ('artifact-token-permission', ap, ac.replace('actions: read', 'actions: none'), copy.deepcopy(ss), False)]
    ws = copy.deepcopy(ss)
    ws['runs'][1].update(event='workflow_run', activity='completed', upstreamName='Artifact Producer', upstreamRun='writer')
    workflow_run = ac.replace('on: push', 'on:\n  workflow_run:\n    workflows: [Artifact Producer]\n    types: [completed]').replace('  read:\n', "  read:\n    if: github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.event == 'pull_request'\n")
    artifacts.append(('artifact-workflow-run-context', ap, workflow_run, ws, True))
    two = ap + "      - run: echo 'clean' > clean\n      - uses: actions/upload-artifact@v4\n        with: {name: clean, path: clean}\n"
    artifacts.append(('artifact-two-object-isolation', two, ac.replace('name: payload', 'name: clean'), copy.deepcopy(ss), False))
    multi = ac.replace('  read:\n', '''  upload:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: {ref: pr-head, persist-credentials: false}
      - uses: actions/upload-artifact@v4
        with: {name: local, path: data}
  read:
    needs: upload
''').replace('name: payload', 'name: local').replace('          run-id: writer\n', '').replace('          github-token: ${{ secrets.GITHUB_TOKEN }}\n', '')
    ms = copy.deepcopy(ss)
    ms['runs'][1]['checkoutUntrusted'] = True
    artifacts.append(('artifact-multiple-jobs', ap, multi, ms, True))
    outputs = multi.replace('  upload:\n', '  upload:\n    outputs: {enabled: "${{ steps.flag.outputs.enabled }}"}\n').replace('      - uses: actions/upload-artifact@v4\n', '      - id: flag\n        run: echo "enabled=true" >> "$GITHUB_OUTPUT"\n      - uses: actions/upload-artifact@v4\n').replace('    needs: upload\n', "    needs: upload\n    if: needs.upload.outputs.enabled == 'true'\n")
    artifacts.append(('artifact-needs-output', ap, outputs, ms, True))
    package = ac.replace('      - run: cat data\n      - run: git add data\n      - run: git push', '      - run: npm publish data')
    ps = copy.deepcopy(ss)
    ps['runs'][1]['externalPackageCredential'] = True
    artifacts.append(('artifact-package-publish', ap, package, ps, True))
    for name, pp, cc, cfg, expected in artifacts:
        target = out / 'extended' / name
        target.mkdir(parents=True, exist_ok=True)
        paths = [target / 'producer.yml', target / 'consumer.yml']
        for path, source in zip(paths, [pp, cc]):
            path.write_text(source)
        dump(target / 'scenario.json', cfg)
        text, mapping = compile_extended(paths, cfg)
        (target / 'model.smv').write_text(text)
        dump(target / 'model.map.json', mapping)
        records.append(dict(id=name, engine='extended', model=str((target / 'model.smv').relative_to(out)), expected=expected,
                            stateVariables=mapping['stateVariables'], verdict=mapping['verdict']))
    # Real snapshots: parse with no synthetic replacement of pnpm or remote actions.
    tan = ROOT / 'experiments/public-cases/tanstack'
    for variant in ['pre-incident', 'mitigation']:
        paths = [tan / variant / '.github/workflows/bundle-size.yml', tan / variant / '.github/workflows/release.yml']
        if not all(path.exists() for path in paths):
            candidates = list((tan / variant / '.github/workflows').glob('*'))
            dump(out / 'tanstack' / (variant + '-missing.json'), {'status': 'not-generated', 'available': [str(p) for p in candidates]})
            continue
        producer_event = 'pull_request_target' if variant == 'pre-incident' else 'pull_request'
        ss = dict(cachePolicy='github-historical-2025', runs=[context(id='pr', event=producer_event, activity='opened', scope='refs/heads/main' if variant == 'pre-incident' else 'refs/pull/1/merge', checkoutUntrusted=True, changedPaths=['packages/example/src/index.ts']), context(id='release', after='pr')])
        registry = json.loads((tan / 'composite-contracts.json').read_text())
        ss['compositeContracts'] = {ref: dict(path=str(ROOT / spec['path']), sha256=spec['sha256']) for ref, spec in registry.items()}
        text, mapping = compile_extended(paths, ss)
        target = out / 'tanstack' / variant
        target.parent.mkdir(parents=True, exist_ok=True)
        target.with_suffix('.smv').write_text(text)
        dump(target.with_suffix('.map.json'), mapping)
        records.append(dict(id='tanstack-' + variant, engine='extended', model=str(target.with_suffix('.smv').relative_to(out)), expected=None, verdict=mapping['verdict']))
    dump(out / 'inventory.json', records)
    dump(out / 'runtime.json', dict(python=sys.version, pyyaml=yaml.__version__, platform=platform.platform(), machine=platform.machine()))
    print('Generated', len(records), 'models')


def summarize(out):
    if (out / "verification-summary.json").exists():
        raise FileExistsError(f"summary already exists; use a new output directory: {out}")
    inventory = json.loads((out / 'inventory.json').read_text())
    failed = []
    for record in inventory:
        path = out / record['model']
        stdout = Path(str(path) + '.stdout').read_text() if Path(str(path) + '.stdout').exists() else ''
        stderr = Path(str(path) + '.stderr').read_text() if Path(str(path) + '.stderr').exists() else ''
        exit_path = Path(str(path) + '.exitcode')
        exitcode = int(exit_path.read_text()) if exit_path.exists() else None
        properties = dict(re.findall(r'^-- specification (.*?)\s+is (true|false)\s*$', stdout, re.M))
        for name, expression in {'no_transfer': 'AG !transfer', 'no_use': 'AG !dependent_use', 'no_sink_site': 'AG !sink_site',
                                 'no_success': 'AG !sink_succeeded', 'no_bad_authority': 'AG !bad_authority'}.items():
            if expression in properties:
                properties[name] = properties[expression]
        intentional = record['id'] == 'legacy-nusmv-invalid-input'
        record.update(exitcode=exitcode, properties=properties, verified=exitcode == 0 and bool(properties))
        record['transitionTotal'] = 'The transition relation is total: No deadlock state exists' in stdout
        if record['verified'] and not record['transitionTotal']:
            failed.append(record['id'] + ':transition-totality')
        if record['id'] in {'A1', 'A2', 'A3', 'A4', 'A5'}:
            saved_path = ROOT / 'outputs/model-v2/cases' / (record['id'] + '.stdout.txt')
            saved_text = read_prototype(ROOT / 'outputs/model-v2', 'cases/' + record['id'] + '.stdout.txt')
            saved = dict(re.findall(r'^-- specification (.*?)\s+is (true|false)\s*$', saved_text, re.M))
            shared = sorted(saved.keys() & properties.keys())
            record['savedPrototypeComparison'] = {name: properties[name] == saved[name] for name in shared}
            if not all(record['savedPrototypeComparison'].values()):
                failed.append(record['id'] + ':saved-prototype')
        if record['id'] == 'audit-use-before-later-block':
            record['historyAuditPassed'] = properties.get('AG !(integrity_blocked & artifact_dependent_use)') == 'false' and properties.get('AG !post_integrity_mismatch_use') == 'true'
            if not record['historyAuditPassed']:
                failed.append(record['id'] + ':history-audit')
        record['syntaxFailureControlPassed'] = intentional and exitcode not in (None, 0) and not properties
        if not record['verified'] and not record['syntaxFailureControlPassed']:
            failed.append(record['id'])
        if record.get('expected') is not None and record['verified']:
            actual = properties.get('no_bad_authority', properties.get('AG !bad_authority'))
            record['expectationMatched'] = actual == ('false' if record['expected'] else 'true')
            if not record['expectationMatched']:
                failed.append(record['id'] + ':expectation')
        if record.get('bfs', {}).get('status') == 'complete' and record['verified']:
            comparisons = {}
            for bfsname, prop in [('transfer', 'no_transfer'), ('use', 'no_use'), ('sink_site', 'no_sink_site'), ('success', 'no_success'), ('bad_authority', 'no_bad_authority')]:
                actual = properties.get(prop)
                comparisons[bfsname] = actual == ('false' if record['bfs']['reachable'][bfsname] else 'true')
            record['bfsAgreement'] = comparisons
            if not all(comparisons.values()):
                failed.append(record['id'] + ':bfs')
        # Preserve every counterexample and map any visible state symbol to its
        # source; mapping is a set of possible sites, not guessed active lines.
        mapping_path = path.with_suffix('.map.json')
        mapping = json.loads(mapping_path.read_text()) if mapping_path.exists() else {}
        if mapping:
            record['sourceValidation'] = validate_sources(mapping)
            if record['sourceValidation']['invalidSpans']:
                failed.append(record['id'] + ':source-map')
        source_symbols = mapping.get('symbols', {})
        traces = re.split(r'(?=^-- specification )', stdout, flags=re.M)
        trace_count = 0
        for trace in traces:
            match = re.match(r'-- specification (.*?) is false', trace)
            if not match:
                continue
            trace_count += 1
            stem = str(path) + '.cex.' + str(trace_count)
            Path(stem + '.trace.txt').write_text(trace)
            mapped = []
            for sym, source in source_symbols.items():
                if re.search(r'\b' + re.escape(sym) + r'\s*=', trace):
                    mapped.append(dict(symbol=sym, source=source))
            dump(Path(stem + '.source-map.json'), map_extended_trace(trace, mapping) if mapping.get('modelVersion') == '2-extended-bounded' else mapped)
        stats_path = Path(str(path) + '.stats.stdout')
        if stats_path.exists():
            stats = stats_path.read_text()
            record['reachableStates'] = re.findall(r'reachable states:\s*([^\n]+)', stats, re.I)
            record['nonemptyReachableSet'] = bool(record['reachableStates']) and float(record['reachableStates'][0].split()[0]) > 0
            status_path = Path(str(path) + '.stats.exitcode')
            record['stateEnumerationSucceeded'] = status_path.exists() and status_path.read_text().strip() == '0'
        if record['verified'] and (not record.get('nonemptyReachableSet') or not record.get('stateEnumerationSucceeded')):
            failed.append(record['id'] + ':state-enumeration')
        elapsed = Path(str(path) + '.seconds')
        if elapsed.exists():
            record['verificationWallSeconds'] = int(elapsed.read_text())
        timing_path = Path(str(path) + '.timing')
        if timing_path.exists():
            match = re.search(r'real\s+(\d+)m\s*([0-9.]+)s', timing_path.read_text() + '\n' + stderr)
            if match:
                record['verificationRealSeconds'] = int(match.group(1)) * 60 + float(match.group(2))
        record['traceCount'] = trace_count
    summary = dict(models=inventory, failures=failed, total=len(inventory),
                   validated=sum(r['verified'] for r in inventory),
                   syntaxControls=sum(r['syntaxFailureControlPassed'] for r in inventory))
    dump(out / 'verification-summary.json', summary)
    print(json.dumps({k: v for k, v in summary.items() if k != 'models'}, indent=2))
    return 1 if failed else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['generate', 'summarize'])
    parser.add_argument('--output', type=Path, default=Path('/out'))
    args = parser.parse_args()
    if args.mode == 'generate':
        generate(args.output)
    else:
        raise SystemExit(summarize(args.output))
