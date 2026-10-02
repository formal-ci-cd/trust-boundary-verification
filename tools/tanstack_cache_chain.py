#!/usr/bin/env python3
"""Trace a fork-PR cache boundary across pinned workflows and a composite Action.

The input is configuration only. Runtime cache identity, save success, restored
bytes, execution and npm publication are separate observations; the static
model treats them as unknown until independent incident evidence is compared.
"""
import argparse
from collections import deque
import hashlib
import json
from pathlib import Path
import re

import yaml
import yaml_to_model


def load(path):
    return yaml.load(path.read_text(), Loader=yaml_to_model.WorkflowLoader)


def verify_sources(root, manifest):
    for record in manifest['files'] + manifest['externalAction']['files']:
        path = root / record['snapshot']
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != record['sha256']:
            raise ValueError(f'Snapshot checksum mismatch: {path}')


def at(path, root, needle, role):
    source = path.read_text()
    offset = source.find(needle)
    if offset < 0:
        raise ValueError(f'Missing source evidence: {path}: {needle}')
    return {'file': path.relative_to(root).as_posix(),
            'line': source.count('\n', 0, offset) + 1, 'role': role}


def event_names(workflow):
    value = workflow.get('on', {})
    if isinstance(value, dict):
        return set(value)
    if isinstance(value, str):
        return {value}
    if isinstance(value, list):
        return set(value)
    raise ValueError('Unsupported event definition')


def job_accepts_event(job, event):
    condition = str(job.get('if', ''))
    if not condition:
        return True
    match = re.search(r"github\.event_name\s*==\s*['\"]([^'\"]+)['\"]", condition)
    return match is None or match.group(1) == event


def action_cache(action):
    steps = action.get('runs', {}).get('steps', [])
    if not isinstance(steps, list):
        raise ValueError('Expected composite Action steps')
    for index, step in enumerate(steps):
        if str(step.get('uses', '')).startswith('actions/cache@'):
            params = step.get('with', {})
            if not isinstance(params, dict) or not params.get('key') or not params.get('path'):
                continue
            later_commands = [s.get('run', '') for s in steps[index + 1:]]
            return {'keyExpression': str(params['key']), 'pathExpression': str(params['path']),
                    'laterCommands': later_commands}
    return None


def checkout_of_pr(steps, event):
    for step in steps:
        if not str(step.get('uses', '')).startswith('actions/checkout@'):
            continue
        ref = str(step.get('with', {}).get('ref', ''))
        if 'refs/pull/' in ref and 'github.event.pull_request.number' in ref:
            return True
        if event == 'pull_request' and not ref:
            return True
    return False


def action_use_index(steps, reference):
    return next((i for i, step in enumerate(steps)
                 if step.get('uses') == reference), None)


def analyze(root, variant, manifest):
    workflow_root = root / variant / '.github/workflows'
    action_record = manifest['externalAction']
    reference = action_record['workflowReference']
    action_path = root / 'external-setup/.github/setup/action.yml'
    cache = action_cache(load(action_path))
    if cache is None:
        raise ValueError('Referenced composite Action has no supported cache step')
    producers = []
    consumers = []
    for path in sorted(workflow_root.glob('*.yml')):
        workflow = load(path)
        for job_id, job in workflow.get('jobs', {}).items():
            if not isinstance(job, dict):
                continue
            steps = job.get('steps', [])
            if not isinstance(steps, list):
                continue
            setup_index = action_use_index(steps, reference)
            if setup_index is None:
                continue
            events = event_names(workflow)
            for event in ('pull_request_target', 'pull_request'):
                if (event in events and job_accepts_event(job, event)
                    and checkout_of_pr(steps[:setup_index], event)
                    and any('pnpm install' in run for run in cache['laterCommands'])
                    and any('pnpm nx' in str(s.get('run', ''))
                            for s in steps[setup_index + 1:])):
                    producers.append({'workflow': path, 'job': job_id, 'event': event,
                                      'setupIndex': setup_index})
            permissions = job.get('permissions', workflow.get('permissions', {}))
            if (('push' in events and job_accepts_event(job, 'push'))
                and isinstance(permissions, dict)
                and permissions.get('id-token') == 'write'
                and any('pnpm' in str(s.get('run', '')) for s in steps[setup_index + 1:])):
                consumers.append({'workflow': path, 'job': job_id,
                                  'setupIndex': setup_index})
    if len(producers) != 1 or len(consumers) != 1:
        raise ValueError('Expected exactly one supported producer and OIDC consumer')
    producer, consumer = producers[0], consumers[0]
    # May 2026 event semantics: fork pull_request caches are scoped to the PR
    # merge ref; pull_request_target ran in the base default-branch namespace.
    # This historical rule must not be projected onto current cache policy.
    same_scope = producer['event'] == 'pull_request_target'
    producer_path = producer['workflow']
    consumer_path = consumer['workflow']
    evidence = [
        at(producer_path, root, producer['event'] + ':', 'fork-pr-event'),
        at(producer_path, root, 'ref: refs/pull/${{ github.event.pull_request.number }}/merge',
           'untrusted-pr-checkout'),
        at(producer_path, root, 'uses: ' + reference, 'producer-external-setup'),
        at(action_path, root, 'uses: actions/cache@', 'shared-action-cache'),
        at(action_path, root, 'key: ' + cache['keyExpression'], 'cache-key-expression'),
        at(action_path, root, 'run: pnpm install --frozen-lockfile', 'untrusted-install'),
        at(producer_path, root, 'run: pnpm nx run @benchmarks/bundle-size:build',
           'untrusted-build'),
        at(consumer_path, root, 'id-token: write', 'oidc-authority'),
        at(consumer_path, root, 'uses: ' + reference, 'consumer-external-setup'),
        at(consumer_path, root, 'run: pnpm run test:ci', 'privileged-use-after-restore'),
    ]
    return {
        'variant': variant,
        'producer': {'workflow': producer_path.relative_to(root).as_posix(),
                     'job': producer['job'], 'event': producer['event']},
        'consumer': {'workflow': consumer_path.relative_to(root).as_posix(),
                     'job': consumer['job'], 'permission': 'id-token: write'},
        'compositeActionReference': reference,
        'cache': {'keyExpression': cache['keyExpression'],
                  'pathExpression': cache['pathExpression'],
                  'sameExpressionAtBothCallSitesInPinnedSnapshot': True,
                  'sameDefaultBranchScopePossibleForFork': same_scope,
                  'effectiveKeyAndObject': 'unknown from YAML'},
        'staticVerdict': ('potential-cross-workflow-cache-path' if same_scope
                          else 'fork-cache-isolated-from-default-branch'),
        'unknownRuntimeFacts': {
            'cacheSaveSucceeded': 'unknown from YAML',
            'effectiveKeysEqual': 'unknown from YAML',
            'releaseRestoredPoisonedEntry': 'unknown from YAML',
            'poisonedCodeExecuted': 'unknown from YAML'},
        'evidence': evidence,
    }


def explore(scope_compatible):
    seen = set()
    witness = None
    for saved in (False, True):
        for equal in (False, True):
            for restored in (False, True):
                for invoked in (False, True):
                    start = (0, saved, equal, restored, invoked, False, False)
                    queue = deque([(start, [])])
                    while queue:
                        state, trace = queue.popleft()
                        if state in seen:
                            continue
                        seen.add(state)
                        phase, did_save, same_key, did_restore, did_invoke, tainted, bad = state
                        if bad and witness is None:
                            witness = trace
                        if phase == 5:
                            continue
                        next_tainted = tainted
                        next_bad = bad
                        step = ['fork-pr-code-executes', 'cache-save', 'release-cache-restore',
                                'release-code-executes', 'OIDC-authority-reached'][phase]
                        if phase == 1:
                            next_tainted = did_save
                        elif phase == 2:
                            next_tainted = tainted and scope_compatible and same_key and did_restore
                        elif phase == 3:
                            next_tainted = tainted and did_invoke
                        elif phase == 4:
                            next_bad = tainted
                        nxt = (phase + 1, did_save, same_key, did_restore, did_invoke,
                               next_tainted, next_bad)
                        queue.append((nxt, trace + [{'step': step, 'state': list(nxt)}]))
    return {'verdict': 'possible' if witness else 'no-path-in-supported-model',
            'reachableStates': len(seen), 'conditionalCounterexample': witness or []}


def render(scope_compatible):
    scope = 'TRUE' if scope_compatible else 'FALSE'
    return f'''MODULE main
FROZENVAR
  cache_saved : boolean;
  same_effective_key : boolean;
  poisoned_entry_restored : boolean;
  poisoned_code_invoked : boolean;
VAR
  phase : 0..5;
  tainted : boolean;
  bad : boolean;
ASSIGN
  init(phase) := 0;
  init(tainted) := FALSE;
  init(bad) := FALSE;
  next(phase) := case phase < 5 : phase + 1; TRUE : phase; esac;
  next(tainted) := case
    phase = 1 : cache_saved;
    phase = 2 : tainted & {scope} & same_effective_key & poisoned_entry_restored;
    phase = 3 : tainted & poisoned_code_invoked;
    TRUE : tainted;
  esac;
  next(bad) := bad | (phase = 4 & tainted);
CTLSPEC AG !bad
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    manifest = json.loads((args.root / 'manifest.json').read_text())
    verify_sources(args.root, manifest)
    args.output.mkdir(parents=True, exist_ok=True)
    cases = []
    for variant in ('pre-incident', 'mitigation'):
        row = analyze(args.root, variant, manifest)
        scope = row['cache']['sameDefaultBranchScopePossibleForFork']
        row['bfs'] = explore(scope)
        model = args.output / (variant + '.smv')
        model.write_text(render(scope))
        cases.append(row)
    report = {
        'schemaVersion': 'cross-workflow-cache-chain-0.1',
        'property': ('Fork PR code must not influence code executed in an '
                     'id-token: write release job through the default-branch cache.'),
        'method': 'YAML-only static path; runtime incident observations are separate',
        'historicalPolicy': ('May 2026: pull_request_target could write the base '
                             'default-branch cache; pull_request fork caches were '
                             'scoped to refs/pull/.../merge'),
        'cases': cases}
    (args.output / 'analysis.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
