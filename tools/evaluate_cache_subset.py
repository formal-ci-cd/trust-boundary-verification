#!/usr/bin/env python3
"""Join supported YAML cache candidates to PR source, use and write sink."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

import cache_two_run_model
import extract_shared_operations as shared
import yaml_to_model


def event_status(condition, event):
    """Only exact event equality (or mismatch within AND) is decided."""
    condition = condition.strip()
    if condition.startswith('${{') and condition.endswith('}}'):
        condition = condition[3:-2].strip()
    if not condition:
        return 'true'
    if '||' in condition:
        return 'unknown'
    parts = [p.strip() for p in condition.split('&&')]
    matched = False
    unsupported = False
    for part in parts:
        match = re.fullmatch(r"github\.event_name\s*(==|!=)\s*['\"]([^'\"]+)['\"]", part)
        if match:
            matched = True
            actual = event == match.group(2)
            if actual != (match.group(1) == '=='):
                return 'false'
        else:
            unsupported = True
    return 'unknown' if unsupported or not matched else 'true'



def step_enabled(condition, event):
    condition = condition.strip()
    if condition.startswith('${{') and condition.endswith('}}'):
        condition = condition[3:-2].strip()
    if condition in ('', 'true', 'True'):
        return 'true'
    if condition in ('false', 'False'):
        return 'false'
    return event_status(condition, event)


def job_for(model, job_id):
    return next(job for job in model['workflow']['jobs'] if job['id'] == job_id)


def permission(model, job, scope):
    rows = model['workflow']['permissions']
    explicit_job = job.get('permissionsDeclared', False)
    selected = [p for p in rows if p['jobId'] == job['id']] if explicit_job else [
        p for p in rows if p['jobId'] is None]
    if any(p['scope'] in {scope, '*'} and p['access'] == 'write' for p in selected):
        return 'true'
    if explicit_job or model['workflow'].get('permissionsDeclared'):
        return 'false'
    return 'unknown'


def checkout_before(job, index, event):
    for step in job['steps']:
        if step['index'] >= index or step.get('action') != 'actions/checkout':
            continue
        if step.get('condition'):
            return None
        ref = step.get('arguments', {}).get('ref', '')
        if (ref == 'refs/pull/${{ github.event.pull_request.number }}/merge'
            or (event == 'pull_request' and not ref)):
            return step
    return None


def composite_use_after(operation, contracts):
    """The recognized package-store use is still only a candidate for code execution."""
    location = operation['location']
    source = location.get('compositeFile')
    if not source:
        return None
    contract = next((c for c in contracts.values() if c['path'] == source), None)
    if not contract:
        return None
    for index, step in enumerate(contract['body']['runs']['steps']):
        if index <= location['compositeStep']:
            continue
        if step.get('shell') in {'bash', 'sh'} and str(step.get('run', '')).strip() == 'pnpm install --frozen-lockfile':
            return {'file': source, 'line': contract['stepLines'][index],
                    'role': 'candidate-cache-derived-use',
                    'workflowCall': location['workflow'], 'job': location['job'],
                    'step': location['step']}
    return None


def line_of(root, workflow_file, needle):
    path = root / workflow_file
    for number, line in enumerate(path.read_text().splitlines(), 1):
        if line.strip() == needle:
            return number
    return None


def evidence_location(root, operation, role):
    loc = operation['location']
    return {'file': loc['workflow'], 'line': loc['line'], 'job': loc['job'],
            'step': loc['step'], 'compositeFile': loc.get('compositeFile'),
            'compositeLine': loc.get('compositeLine'), 'role': role}


def evaluate(root, models, inventory, contracts, nusmv, output, policy):
    operations = inventory['operations']
    results = []
    for pair in inventory['cacheCandidates']:
        write = next(o for o in operations if o['kind'] == 'cache.write' and
                     o['location'] == pair['producer'])
        read = next(o for o in operations if o['kind'] == 'cache.read' and
                    o['location'] == pair['consumer'])
        pm = models[pair['producer']['workflow']]
        cm = models[pair['consumer']['workflow']]
        pj = job_for(pm, pair['producer']['job'])
        cj = job_for(cm, pair['consumer']['job'])
        producer_events = {e['name'] for e in pm['workflow']['events']}
        consumer_events = {e['name'] for e in cm['workflow']['events']}
        if 'push' not in consumer_events:
            continue
        source = None
        for event in ('pull_request_target', 'pull_request'):
            if event not in producer_events or event_status(pj.get('condition', ''), event) == 'false':
                continue
            checkout = checkout_before(pj, write['location']['step'], event)
            if checkout:
                source = (event, checkout)
                break
        if not source or event_status(cj.get('condition', ''), 'push') == 'false':
            continue
        # A selected cache consumer needs a use after restore and an explicit
        # privileged sink in the same job. Unknown control-flow remains unknown.
        producer_use = composite_use_after(write, contracts)
        use = composite_use_after(read, contracts)
        sinks = [s for s in cj['steps'] if s['index'] > read['location']['step']
                 and s['type'] == 'run' and re.search(r'(?m)^\s*git\s+push(?:\s|$)', s['command'])]
        if not sinks:
            continue
        sink = sinks[0]
        authority = permission(cm, cj, 'contents')
        event, checkout = source
        if pair['keyCompatibility'] == 'literal-key-different':
            same = 'false'
        elif (policy == 'historical-pre-2026-06-26' and event == 'pull_request'
              and 'main' in next((e['properties'].get('branches', []) for e in cm['workflow']['events']
                                  if e['name'] == 'push'), [])):
            # Historical PR merge-ref caches are not restorable from main.
            same = 'false'
        else:
            same = 'unknown'
        facts = {name: 'unknown' for name in cache_two_run_model.FACTS}
        facts['sameObject'] = same
        facts['hasAuthority'] = authority
        write_step = next(s for s in pj['steps'] if s['index'] == write['location']['step'])
        read_step = next(s for s in cj['steps'] if s['index'] == read['location']['step'])
        facts['producerCallEnabled'] = step_enabled(write_step.get('condition', ''), event)
        facts['consumerCallEnabled'] = step_enabled(read_step.get('condition', ''), 'push')
        if step_enabled(sink.get('condition', ''), 'push') == 'false':
            facts['sinkReachable'] = 'false'
        evidence = [
            {'file': pm['workflow']['file'],
             'line': line_of(root, pm['workflow']['file'], event + ':'),
             'role': 'untrusted-pr-event'},
            {'file': pm['workflow']['file'], 'line': checkout['line'],
             'role': 'untrusted-pr-checkout'},
            {'file': write['location']['workflow'], 'line': write['location']['line'],
             'role': 'producer-cache-write-call'},
            {'file': read['location']['workflow'], 'line': read['location']['line'],
             'role': 'consumer-cache-read-call'},
            {'file': cm['workflow']['file'], 'line': sink['line'],
             'role': 'repository-write-sink'},
        ]
        if producer_use:
            evidence.append({'file': producer_use['file'], 'line': producer_use['line'],
                             'role': 'candidate-pr-influenced-cache-store'})
        if use:
            evidence.append({'file': use['file'], 'line': use['line'], 'role': use['role']})
        identifier = hashlib.sha256(('|'.join((
            event, pair['producer']['workflow'], pair['producer']['job'],
            str(pair['producer']['step']), pair['consumer']['workflow'],
            pair['consumer']['job'], str(pair['consumer']['step']),
            str(pair['writeKeyExpression']), str(pair['readKeyExpression']),
        ))).encode()).hexdigest()[:12]
        model_path = output / f'cache-{identifier}.smv'
        model_path.write_text(cache_two_run_model.render(facts, evidence))
        run = subprocess.run([str(nusmv), str(model_path)], check=True,
                             capture_output=True, text=True)
        formal = re.search(r'-- specification AG !bad\s+is (true|false)', run.stdout)
        if not formal:
            raise RuntimeError('NuSMV property result missing')
        verdict = 'safe' if formal.group(1) == 'true' else 'unsafe'
        if verdict == 'unsafe' and 'Trace Type: Counterexample' not in run.stdout:
            raise RuntimeError('NuSMV reported unsafe without a counterexample')
        nusmv_path = output / f'cache-{identifier}.nusmv.txt'
        nusmv_path.write_text("\n".join(line.rstrip() for line in run.stdout.splitlines()) + "\n")
        independent = cache_two_run_model.explore(facts)
        if independent['verdict'] != verdict:
            raise RuntimeError('NuSMV/independent BFS disagreement')
        status = ('unknown/unsupported' if not use else
                  'unsafe-counterexample' if verdict == 'unsafe' else 'safe-within-model')
        results.append({'id': identifier, 'source': {'event': event,
                         'checkout': evidence[1]},
                        'producer': evidence_location(root, write, 'cache.write'),
                        'sharedObject': {'kind': 'cache', 'writeKey': pair['writeKeyExpression'],
                            'readKey': pair['readKeyExpression'],
                            'restoreKeys': pair['restoreKeysExpression'],
                            'keyCompatibility': pair['keyCompatibility'],
                            'sameObject': same, 'scopePolicy': policy,
                            'producerScope': ('PR merge-ref scope' if event == 'pull_request'
                                              else 'base/default branch scope possible'),
                            'consumerScope': 'main push possible',
                            'branchProtection': 'unknown',
                            'version': 'unknown', 'candidateEntry': 'unknown'},
                        'consumer': evidence_location(root, read, 'cache.read'),
                        'producerUse': producer_use or 'unknown/unsupported',
                        'use': use or 'unknown/unsupported',
                        'verify': {'trustedIntegrityVerification': 'unknown',
                            'reason': 'No supported trusted digest guard was established from this cache path'},
                        'privilegedAuthority': {'kind': 'repository-write',
                            'configured': authority, 'sink': {'file': cm['workflow']['file'],
                            'job': cj['id'], 'step': sink['index'], 'line': sink['line']},
                            'actualOperationSucceeded': 'unknown'},
                        'facts': facts, 'unknownAssumptions': {
                            name: independent['witnessFacts'][name] if independent['witnessFacts']
                            else 'unresolved' for name, value in facts.items() if value == 'unknown'},
                        'order': {'producerAndConsumerRunsDistinct': True,
                            'saveBeforeRestore': 'possible, not observed',
                            'counterexample': independent['counterexample']},
                        'status': status, 'modelVerdict': verdict,
                        'evidence': evidence, 'smv': model_path.name,
                        'nusmvOutput': nusmv_path.name,
                        'limitations': ['A mutable composite Action reference was represented by a checksum-matched snapshot, not proven runtime bytes',
                                        'Cache saved/restored bytes, effective keys and use are unobserved']})
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path)
    p.add_argument('--composites', type=Path, required=True)
    p.add_argument('--policy', choices=('unknown', 'historical-pre-2026-06-26'),
                   default='unknown')
    p.add_argument('--nusmv', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    models = yaml_to_model.load_models(args.root)
    contracts = shared.load_contracts(args.composites)
    inventory = shared.extract(models, contracts)
    inventory['cacheCandidates'] = shared.pair_cache(inventory['operations'])
    results = evaluate(args.root, models, inventory, contracts,
                       args.nusmv, args.output, args.policy)
    report = {'scope': 'limited fork PR cache to explicit repository write sink; no actual compromise inferred',
              'policy': args.policy, 'workflowCount': len(models),
              'cachePairCandidates': len(inventory['cacheCandidates']),
              'supportedPathCandidates': len(results),
              'emptyCandidateMeaning': 'unknown/unsupported; never safe',
              'repositorySafety': 'unknown/unsupported; per-path safe verdicts do not cover other Actions or paths',
              'unsupportedActions': inventory['unsupportedActions'],
              'policyEvidence': 'docs/github-actions-cache-object-identity.md (historical assumptions require date-specific validation)',
              'results': results}
    (args.output / 'analysis.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print([(r['producer']['file'], r['consumer']['file'], r['status']) for r in results])


if __name__ == '__main__':
    main()
