#!/usr/bin/env python3
"""Bounded, configuration-only check for an issue-agent to release-cache path.

This deliberately supports one documented action interface. It does not claim
to predict an LLM response, cache eviction, or execution of cached bytes.
Those events remain nondeterministic in the generated model.
"""
import argparse
from collections import deque
import hashlib
import itertools
import json
from pathlib import Path
import re

import yaml
import yaml_to_model


ISSUE_TITLE = re.compile(r"github\.event\.issue\.title")
SECRET = re.compile(r"secrets\.([A-Z][A-Z0-9_]*)")
CACHE_REF = re.compile(r"^actions/cache@")
AGENT_REF = re.compile(r"^anthropics/claude-code-action@")
PUBLISH_COMMAND = re.compile(r'\b(?:npm publish|publish:marketplace|vsce publish|ovsx publish)\b')


def read_workflow(path):
    return yaml.load(path.read_text(), Loader=yaml_to_model.WorkflowLoader)


def events(workflow):
    value = workflow.get('on', {})
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        return {name: None for name in value}
    if isinstance(value, str):
        return {value: None}
    raise ValueError('Unsupported workflow event shape')


def source(path, root, needle, role):
    body = path.read_text()
    offset = body.find(needle)
    if offset < 0:
        raise ValueError(f'Missing source evidence: {path}: {needle}')
    return {'file': path.relative_to(root).as_posix(),
            'line': body.count('\n', 0, offset) + 1, 'role': role}


def find_producers(path, root):
    workflow = read_workflow(path)
    issue_event = events(workflow).get('issues')
    if issue_event is None or not isinstance(issue_event, dict):
        return []
    event_types = issue_event.get('types', [])
    if 'opened' not in event_types:
        return []
    found = []
    for job_id, job in workflow.get('jobs', {}).items():
        for step in job.get('steps', []):
            if not AGENT_REF.match(str(step.get('uses', ''))):
                continue
            params = step.get('with', {})
            prompt = str(params.get('prompt', ''))
            args = str(params.get('claude_args', ''))
            if (str(params.get('allowed_non_write_users', '')) == '*'
                and ISSUE_TITLE.search(prompt)
                and re.search(r'--allowedTools\s+[^\n]*\bBash\b', args)):
                found.append({
                    'workflow': path.relative_to(root).as_posix(), 'job': job_id,
                    'event': 'issues.opened', 'action': step['uses'],
                    'evidence': [
                        source(path, root, 'types: [opened]', 'public-issue-event'),
                        source(path, root, 'allowed_non_write_users:', 'public-agent-access'),
                        source(path, root, 'github.event.issue.title', 'untrusted-prompt-input'),
                        source(path, root, '--allowedTools', 'agent-shell-capability'),
                    ]})
    return found


def find_consumers(path, root):
    workflow = read_workflow(path)
    triggers = events(workflow)
    if 'schedule' not in triggers:
        return []
    found = []
    for job_id, job in workflow.get('jobs', {}).items():
        steps = job.get('steps', [])
        if not isinstance(steps, list):
            continue
        for index, step in enumerate(steps):
            if not CACHE_REF.match(str(step.get('uses', ''))):
                continue
            params = step.get('with', {})
            key, cache_path = str(params.get('key', '')), str(params.get('path', ''))
            if not key or not cache_path:
                continue
            publishing_secrets = set()
            for later in steps[index + 1:]:
                if PUBLISH_COMMAND.search(str(later.get('run', ''))):
                    publishing_secrets.update(SECRET.findall(json.dumps(later)))
            if not publishing_secrets:
                continue
            found.append({
                'workflow': path.relative_to(root).as_posix(), 'job': job_id,
                'cacheKeyExpression': key, 'cachePath': cache_path,
                'secretsAtPublicationStep': sorted(publishing_secrets),
                'evidence': [
                    source(path, root, 'uses: actions/cache@', 'release-cache-restore'),
                    source(path, root, 'key: ' + key, 'release-cache-key'),
                    source(path, root, 'secrets.' + sorted(publishing_secrets)[0],
                           'later-publication-secret-use'),
                ]})
            break  # one path per job; the first matching cache precedes publication
    return found


def scan(root, variant):
    paths = sorted((root / variant / '.github/workflows').glob('*.y*ml'))
    producers = [record for path in paths for record in find_producers(path, root)]
    consumers = [record for path in paths for record in find_consumers(path, root)]
    return {'variant': variant, 'workflowCount': len(paths), 'producers': producers,
            'consumers': consumers,
            'staticVerdict': ('conditional-path' if producers and consumers
                              else 'no-path-in-supported-model')}


def explore(enabled):
    # Unknown events: agent obeys issue, vacant cache slot, save, matching key,
    # restore, and invocation of attacker-controlled bytes in the release job.
    seen, witness = set(), None
    labels = ('issue-agent-shell', 'cache-slot-available', 'poisoned-cache-save',
              'release-cache-restore', 'release-code-execution', 'secret-reachable')
    for choices in itertools.product((False, True), repeat=6):
        start = (0, False, False)
        queue = deque([(start, [])])
        while queue:
            state, trace = queue.popleft()
            keyed = (choices, state)
            if keyed in seen:
                continue
            seen.add(keyed)
            phase, tainted, bad = state
            if bad and witness is None:
                witness = {'assumptions': dict(zip(
                    ('agentExecutesIssueInstruction', 'cacheSlotAvailable',
                     'poisonedCacheSaveSucceeds', 'effectiveKeyMatches',
                     'poisonedEntryRestored', 'poisonedBytesExecute'), choices)),
                    'trace': trace}
            if phase == 6:
                continue
            next_tainted, next_bad = tainted, bad
            if phase == 0:
                next_tainted = enabled and choices[0]
            elif phase == 1:
                next_tainted = tainted and choices[1]
            elif phase == 2:
                next_tainted = tainted and choices[2]
            elif phase == 3:
                next_tainted = tainted and choices[3] and choices[4]
            elif phase == 4:
                next_tainted = tainted and choices[5]
            else:
                next_bad = tainted
            nxt = (phase + 1, next_tainted, next_bad)
            queue.append((nxt, trace + [{'step': labels[phase], 'state': list(nxt)}]))
    return {'reachableStates': len(seen),
            'verdict': 'possible' if witness else 'no-path-in-supported-model',
            'conditionalCounterexample': witness}


def render(enabled):
    active = 'TRUE' if enabled else 'FALSE'
    return f'''MODULE main
FROZENVAR
  agent_executes_issue_instruction : boolean;
  cache_slot_available : boolean;
  poisoned_cache_save_succeeds : boolean;
  effective_key_matches : boolean;
  poisoned_entry_restored : boolean;
  poisoned_bytes_execute : boolean;
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
    phase = 0 : {active} & agent_executes_issue_instruction;
    phase = 1 : tainted & cache_slot_available;
    phase = 2 : tainted & poisoned_cache_save_succeeds;
    phase = 3 : tainted & effective_key_matches & poisoned_entry_restored;
    phase = 4 : tainted & poisoned_bytes_execute;
    TRUE : tainted;
  esac;
  next(bad) := bad | (phase = 5 & tainted);
CTLSPEC AG !bad
'''


def check_manifest(root):
    manifest = json.loads((root / 'manifest.json').read_text())
    expected_paths = {record['path'] for record in manifest['files']}
    actual_paths = {path.relative_to(root).as_posix()
                    for path in root.rglob('*') if path.is_file()
                    and path.name not in ('manifest.json', 'incident-observation.json')}
    if actual_paths != expected_paths:
        raise ValueError('Snapshot file list differs from manifest')
    for record in manifest['files']:
        path = root / record['path']
        if hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256']:
            raise ValueError(f'Snapshot checksum mismatch: {path}')
    for variant, record in manifest['snapshots'].items():
        actual = len(list((root / variant / '.github/workflows').glob('*.y*ml')))
        if actual != record['workflowCount']:
            raise ValueError(f'Workflow count mismatch: {variant}')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    manifest = check_manifest(root)
    args.output.mkdir(parents=True, exist_ok=True)
    cases = []
    for variant in manifest['snapshots']:
        row = scan(root, variant)
        enabled = bool(row['producers'] and row['consumers'])
        row['bfs'] = explore(enabled)
        (args.output / f'{variant}.smv').write_text(render(enabled))
        cases.append(row)
    report = {
        'schemaVersion': 'agent-cache-boundary-0.1',
        'property': ('A public issue must not influence code in a release job '
                     'that later receives publication credentials through a shared cache.'),
        'method': 'Pinned YAML snapshots; bounded action-interface semantics; runtime events unknown',
        'historicalCachePolicy': 'February 2026: issues runs on default branch could write its cache scope',
        'cases': cases,
    }
    (args.output / 'analysis.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
