#!/usr/bin/env python3
"""Limited two-repository cache chain analysis for the 2024 Ultralytics case.

Source, cache identity and execution observations remain distinct. The model
searches a potential path under explicit unknown external-state assignments;
it does not attest that a cache write, release or compromise occurred.
"""
import argparse
from collections import deque
import hashlib
import json
from pathlib import Path
import re
import yaml_to_model
import yaml


ENTRY_REF = '${{ github.head_ref || github.ref }}'


def checked(manifest, root):
    for record in (manifest['files'] + manifest['externalAction']['files']
                   + manifest['externalActionFixed']['files']):
        path = root / record['snapshot']
        if hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256']:
            raise ValueError('Source checksum mismatch: ' + str(path))


def line_of(path, text):
    source = path.read_text()
    where = source.find(text)
    if where < 0:
        raise ValueError(f'Expected evidence absent: {path}: {text}')
    return source.count('\n', 0, where) + 1


def evidence(path, root, needle, role):
    return {'file': path.relative_to(root).as_posix(),
            'line': line_of(path, needle), 'role': role}


def analyze(root, repository_variant, action_variant):
    # YAML is parsed as data, never executed. Reuse the frontend's strict loader.
    format_path = root / repository_variant / '.github/workflows/format.yml'
    publish_path = root / repository_variant / '.github/workflows/publish.yml'
    action_path = root / action_variant / 'action.yml'
    fmt = yaml.load(format_path.read_text(), Loader=yaml_to_model.WorkflowLoader)
    pub = yaml.load(publish_path.read_text(), Loader=yaml_to_model.WorkflowLoader)
    action = yaml.load(action_path.read_text(), Loader=yaml_to_model.WorkflowLoader)
    if not isinstance(action.get('runs', {}).get('steps'), list):
        raise ValueError('Expected composite action')
    format_call = [s for j in fmt['jobs'].values() for s in j.get('steps', [])
                   if s.get('uses', '').startswith('ultralytics/actions@')]
    format_trigger = 'pull_request_target' in fmt.get('on', {})
    privileged_event = bool(format_trigger and format_call)
    entry = [s for s in action['runs']['steps'] if
             ENTRY_REF in s.get('run', '') and
             re.search(r'git pull origin\s+\$\{\{\s*github\.head_ref\s*\|\|\s*github\.ref\s*\}\}', s.get('run', ''))]
    entry_capable = bool(privileged_event and entry)
    publisher_rows = []
    for jid, job in pub['jobs'].items():
        steps = job.get('steps', [])
        for i, step in enumerate(steps):
            if (not step.get('uses', '').startswith('actions/setup-python@')
                or step.get('with', {}).get('cache') != 'pip'):
                continue
            install = next((k for k in range(i + 1, len(steps))
                            if 'pip install' in steps[k].get('run', '')), None)
            build = next((k for k in range((install or i) + 1, len(steps))
                          if 'python -m build' in steps[k].get('run', '')), None)
            publish = next((k for k in range((build or i) + 1, len(steps))
                            if steps[k].get('uses', '').startswith('pypa/gh-action-pypi-publish@')), None)
            if None in [install, build, publish]:
                continue
            publisher_rows.append({'job': jid, 'guard': job.get('if', ''),
                                   'cacheStep': i, 'installStep': install,
                                   'buildStep': build, 'publishStep': publish})
    selected = publisher_rows[0] if publisher_rows else None
    events = []
    if privileged_event:
        events.append(evidence(format_path, root, 'pull_request_target:', 'external-PR-trigger'))
        events.append(evidence(format_path, root, 'uses: ultralytics/actions@', 'mutable-external-action-call'))
    if entry:
        events.append(evidence(action_path, root, 'git pull origin ' + ENTRY_REF, 'unquoted-branch-expression'))
    if selected:
        events.append(evidence(publish_path, root, 'cache: "pip"', 'implicit-pip-cache-restore'))
        events.append(evidence(publish_path, root, 'pip install ultralytics-actions build twine toml', 'dependency-install'))
        events.append(evidence(publish_path, root, 'run: python -m build', 'build'))
        events.append(evidence(publish_path, root, 'uses: pypa/gh-action-pypi-publish@', 'publish'))
    return {
        'repositoryVariant': repository_variant, 'actionVariant': action_variant,
        'entryCapable': entry_capable, 'publisherCachePath': bool(selected),
        'configuredPublisher': selected,
        'status': ('potential-chain' if entry_capable and selected
                   else 'no-supported-entry' if not entry_capable else 'no-supported-cache-path'),
        'staticEvidence': events,
        'unknownExternalFacts': {
            'sameCacheObject': 'unknown: setup-python key and actual cache object were not observed',
            'cacheWriteSucceeded': 'unknown: writer activity is not specified in YAML',
            'publisherGuardPassed': 'unknown: actor/branch checks and any later workflow modification need runtime evidence',
            'releaseSucceeded': 'unknown from configuration; confirm separately from PyPI provenance'},
        'historicalAssumption': ('2024 GitHub Actions runtime cache capability after code execution; '
                                'the current platform policy must not be projected backwards'),
        'sources': [
            'https://blog.pypi.org/posts/2024-12-11-ultralytics-attack-analysis/',
            'https://archive.fosdem.org/2025/events/attachments/fosdem-2025-6543-hunting-for-github-actions-bugs-with-zizmor/slides/238286/fosdem-20_6UrKP8W.pdf']}


def explore(entry, reader):
    # Enumerate external-state combinations as possibilities, not observations.
    # Each state: phase, same-cache, write-succeeded, guard-passed, taint, bad.
    seen = set()
    bad_trace = None
    for same_cache in [False, True]:
        for write_succeeded in [False, True]:
            for guard_passed in [False, True]:
                start = (0, same_cache, write_succeeded, guard_passed, False, False)
                queue = deque([(start, [])])
                while queue:
                    state, trace = queue.popleft()
                    if state in seen:
                        continue
                    seen.add(state)
                    pc, same, write_ok, guard, tainted, bad = state
                    if bad and bad_trace is None:
                        bad_trace = trace
                    if pc >= 6:
                        continue
                    nxt_taint = tainted
                    nxt_bad = bad
                    action = ['untrusted-PR-runs-external-action', 'attacker-cache-write',
                              'publisher-cache-restore', 'pip-install', 'package-build',
                              'publish-use'][pc]
                    if pc == 1:
                        nxt_taint = entry and write_ok
                    if pc == 2:
                        nxt_taint = nxt_taint and reader and same
                    if pc == 5:
                        nxt_bad = bool(tainted and guard and reader)
                    nxt = (pc + 1, same, write_ok, guard, nxt_taint, nxt_bad)
                    queue.append((nxt, trace + [{'action': action, 'state': list(nxt)}]))
    return {'verdict': 'possible' if bad_trace else 'no-path-in-supported-model',
            'reachableStates': len(seen), 'conditionalCounterexample': bad_trace or []}


def render(entry, reader):
    return f'''MODULE main
FROZENVAR
  same_cache : boolean;
  write_succeeded : boolean;
  publisher_guard_passed : boolean;
VAR
  phase : 0..6;
  tainted : boolean;
  bad : boolean;
ASSIGN
  init(phase) := 0;
  init(tainted) := FALSE;
  init(bad) := FALSE;
  next(phase) := case phase < 6 : phase + 1; TRUE : phase; esac;
  next(tainted) := case
    phase = 1 : {'TRUE' if entry else 'FALSE'} & write_succeeded;
    phase = 2 : tainted & {'TRUE' if reader else 'FALSE'} & same_cache;
    TRUE : tainted;
  esac;
  next(bad) := bad | (phase = 5 & tainted & publisher_guard_passed & {'TRUE' if reader else 'FALSE'});
CTLSPEC AG !bad
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    manifest = json.loads((args.root / 'manifest.json').read_text())
    checked(manifest, args.root)
    args.output.mkdir(parents=True, exist_ok=True)
    cases = [('pre-incident', 'external-action-2024-12-04'),
             ('pre-incident', 'external-action-fixed-2024-12-05'),
             ('mitigation', 'external-action-2024-12-04'),
             ('mitigation', 'external-action-fixed-2024-12-05')]
    results = []
    for repository, action in cases:
        result = analyze(args.root, repository, action)
        result['bfs'] = explore(result['entryCapable'], result['publisherCachePath'])
        name = repository + '__' + action
        (args.output / (name + '.smv')).write_text(render(result['entryCapable'], result['publisherCachePath']))
        results.append(result)
    (args.output / 'analysis.json').write_text(json.dumps({
        'schemaVersion': 'ultralytics-cache-chain-0.1',
        'property': ('Untrusted PR code must not influence bytes used by the package build '
                     'through a shared pip cache before publishing.'),
        'modelStatus': 'conditional reachability; cache object identity and publisher guard unknown',
        'confirmedIncident': manifest['confirmedExploitation'],
        'cases': results}, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
