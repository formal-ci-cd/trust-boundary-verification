#!/usr/bin/env python3
"""Bounded same-second approval race across a workflow and pinned composite Action.

This is a static, conditional capability model. It never executes the upstream
workflow, checkout, local Action, or attacker content.
"""
import argparse
from collections import deque
import hashlib
import json
from pathlib import Path
import re

import yaml

from approval_race_analysis import parse_timestamp_guard, permissions, replay_guard
from yaml_to_model import WorkflowLoader, load_models


def action_steps(path):
    source = path.read_text()
    data = yaml.load(source, Loader=WorkflowLoader)
    if data.get('runs', {}).get('using') != 'composite':
        return []
    ast = yaml.compose(source, Loader=WorkflowLoader)
    runs = next(v for k, v in ast.value if k.value == 'runs')
    nodes = next(v for k, v in runs.value if k.value == 'steps').value
    steps = []
    for i, (raw, node) in enumerate(zip(data['runs']['steps'], nodes)):
        steps.append(dict(index=i, line=node.start_mark.line + 1,
                          type='run' if 'run' in raw else 'uses',
                          id=raw.get('id', ''), condition=raw.get('if', ''),
                          shell=raw.get('shell', ''), command=raw.get('run', ''),
                          env=raw.get('env', {}), action=raw.get('uses', '')))
    return steps


def discover(root):
    manifest_path = root.parent / 'manifest.json'
    paired = json.loads(manifest_path.read_text()).get('upstreamPairedComparison', {}) if manifest_path.is_file() else {}
    snapshot = paired.get(root.name)
    if not snapshot:
        return []
    models = load_models(root)
    candidates = []
    for file, model in models.items():
        if 'issue_comment' not in [e['name'] for e in model['workflow']['events']]:
            continue
        for job in model['workflow']['jobs']:
            if ('github.event.issue.pull_request' not in job.get('condition', '')
                    or not any(permissions(model, job).get(p) == 'write'
                               for p in ('contents', 'pull-requests'))):
                continue
            for use in job['steps']:
                action = use.get('action', '')
                if use.get('condition') or not re.fullmatch(
                        r'[\w-]+/[\w-]+/\.github/actions/[\w-]+', action):
                    continue
                version = use.get('version', '')
                if not re.fullmatch(r'[0-9a-f]{40}', version):
                    continue
                path = root / '.github/actions' / action.rsplit('/', 1)[-1] / 'action.yml'
                if not path.is_file():
                    continue
                caller_path = root / file
                if (action + '@' + version != snapshot['actionRef']
                        or hashlib.sha256(path.read_bytes()).hexdigest() != snapshot['actionSHA256']
                        or hashlib.sha256(caller_path.read_bytes()).hexdigest() != snapshot['workflowSHA256']):
                    continue
                steps = action_steps(path)
                for guard_step in steps:
                    guard = parse_timestamp_guard(guard_step)
                    if not guard:
                        continue
                    authorized = next((s for s in steps[:guard_step['index']]
                                       if s['type'] == 'run'
                                       and 'steps.association.outputs.association' in s['condition']
                                       and all(x in s['condition'] for x in ('OWNER', 'COLLABORATOR', 'MEMBER'))
                                       and 'exit 1' in s['command']), None)
                    if not authorized:
                        continue
                    after = steps[guard_step['index'] + 1:]
                    checkout = next((s for s in after if s['type'] == 'run'
                                     and not s['condition'] and re.search(
                                         r'gh pr checkout \$\{\{ github\.event\.issue\.number \}\}',
                                         s['command'])), None)
                    validate = next((s for s in after if checkout
                                     and s['index'] > checkout['index']
                                     and s['type'] == 'run' and not s['condition']
                                     and s['env'].get('EXPECTED_SHA') == guard['outputRef']
                                     and 'git rev-parse HEAD' in s['command']
                                     and 'exit 1' in s['command']), None)
                    local = next((s for s in job['steps'] if s['index'] > use['index']
                                  and s.get('action', '').startswith('./')
                                  and not s.get('condition')), None)
                    if not (checkout and validate and local):
                        continue
                    candidates.append(dict(
                        id=f'{Path(file).stem}-{job["id"]}-{guard_step["id"]}',
                        rejectionOperator=guard['operator'],
                        sourceScript=guard['script'], timestampVariables=guard['variables'],
                        actionRef=action + '@' + version,
                        configuredWriteScopes=[p for p in ('contents', 'pull-requests')
                                               if permissions(model, job).get(p) == 'write'],
                        evidence=[dict(file=file, line=use['line'], role='pinned-composite-Action'),
                                  dict(file=str(path.relative_to(root)), line=authorized['line'], role='commenter-authorization'),
                                  dict(file=str(path.relative_to(root)), line=guard_step['line'], role='PR-fetch-and-timestamp-guard'),
                                  dict(file=str(path.relative_to(root)), line=checkout['line'], role='PR-checkout'),
                                  dict(file=str(path.relative_to(root)), line=validate['line'], role='HEAD-validation'),
                                  dict(file=file, line=local['line'], role='PR-local-Action')],
                        assumptions=[
                            'The pinned Action snapshot is the bytes at the caller commit; the manifest pins both source repositories.',
                            'An authorized maintainer comment exists and the attacker can update the same PR later within that timestamp second.',
                            'The PR API fetch returns head SHA and pushed_at from one state; timestamps are truncated to seconds.',
                            'The PR checkout and HEAD validation succeed; the chosen PR commit contains attacker-controlled local Action code.',
                            'The configured write token and local Action execution indicate capability, not successful exploitation or actual incident.'
                        ]))
    return candidates


def successors(s, operator):
    # pc, second, pushed stamp (-1 = preapproval), update done, head tainted,
    # chosen head, fetched stamp, checked-out head, reached local action
    pc, tick, stamp, written, head, chosen, fetched, checked_out, bad = s
    if tick == 0:
        yield 'clock:next-second', (pc, 1, stamp, written, head, chosen, fetched, checked_out, bad)
    if not written:
        yield 'attacker:update-PR', (pc, tick, tick, True, True, chosen, fetched, checked_out, bad)
    if pc == 5:
        return
    n = list(s)
    n[0] = pc + 1
    if pc == 0:
        n[5], n[6] = head, stamp
    elif pc == 1:
        if fetched > 0 if operator == '-gt' else fetched >= 0:
            n[0] = 5
    elif pc == 2:
        n[7] = head
    elif pc == 3:
        if checked_out != chosen:
            n[0] = 5
    elif pc == 4:
        n[8] = chosen
    yield ['fetch-current-PR', 'timestamp-guard', 'checkout-PR',
           'validate-HEAD-SHA', 'run-local-Action'][pc], tuple(n)


def explore(operator):
    start = (0, 0, -1, False, False, False, -1, False, False)
    seen = {start: None}
    queue = deque([start])
    target = None
    while queue:
        s = queue.popleft()
        if s[-1] and target is None:
            target = s
        for action, n in successors(s, operator):
            if n not in seen:
                seen[n] = (s, action)
                queue.append(n)
    trace = []
    while target is not None and seen[target] is not None:
        previous, action = seen[target]
        trace.append(dict(action=action, state=list(target)))
        target = previous
    return dict(verdict='unsafe' if trace else 'safe',
                reachableStates=len(seen), counterexample=list(reversed(trace)))


def render(operator):
    reject = 'fetched_stamp > 0' if operator == '-gt' else 'fetched_stamp >= 0'
    return f'''MODULE main
IVAR action : {{advance, update, consumer}};
VAR
  pc : 0..5;
  tick : 0..1;
  stamp : -1..1;
  written : boolean;
  head_tainted : boolean;
  chosen_tainted : boolean;
  fetched_stamp : -1..1;
  checked_out_tainted : boolean;
  bad : boolean;
ASSIGN
  init(pc) := 0;
  init(tick) := 0;
  init(stamp) := -1;
  init(written) := FALSE;
  init(head_tainted) := FALSE;
  init(chosen_tainted) := FALSE;
  init(fetched_stamp) := -1;
  init(checked_out_tainted) := FALSE;
  init(bad) := FALSE;
  next(pc) := case
    action = consumer & pc = 1 & ({reject}) : 5;
    action = consumer & pc = 3 & checked_out_tainted != chosen_tainted : 5;
    action = consumer & pc < 5 : pc + 1;
    TRUE : pc;
  esac;
  next(tick) := case action = advance : 1; TRUE : tick; esac;
  next(stamp) := case action = update & !written : tick; TRUE : stamp; esac;
  next(written) := written | action = update;
  next(head_tainted) := head_tainted | action = update;
  next(chosen_tainted) := case action = consumer & pc = 0 : head_tainted; TRUE : chosen_tainted; esac;
  next(fetched_stamp) := case action = consumer & pc = 0 : stamp; TRUE : fetched_stamp; esac;
  next(checked_out_tainted) := case action = consumer & pc = 2 : head_tainted; TRUE : checked_out_tainted; esac;
  next(bad) := bad | (action = consumer & pc = 4 & chosen_tainted);
CTLSPEC AG !bad
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--replay-guard', action='store_true')
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    candidates = discover(args.root)
    for c in candidates:
        c['property'] = ('After the authorized comment, a later PR update must not '
                         'reach a PR-controlled local Action in a write-token job.')
        c['bfs'] = explore(c['rejectionOperator'])
        c['status'] = ('potential-risk' if c['bfs']['verdict'] == 'unsafe'
                       else 'safe-in-supported-model')
        if args.replay_guard:
            c['guardReplay'] = replay_guard(c)
        (args.output / (c['id'] + '.smv')).write_text(render(c['rejectionOperator']))
    summary = dict(schemaVersion='composite-approval-race-0.1',
                   status='analyzed' if candidates else 'no-supported-path',
                   candidates=candidates)
    (args.output / 'analysis.json').write_text(json.dumps(summary, indent=2) + '\n')


if __name__ == '__main__':
    main()
