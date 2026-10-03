#!/usr/bin/env python3
"""Limited, evidence-bearing approval timestamp -> artifact -> publishing analysis.

This recognizes one Bash idiom, not arbitrary shell control flow. It reports
reachability of unapproved bytes at an OIDC-enabled publish step, not successful
remote publication. Timestamp granularity and attacker scheduling are explicit
environment assumptions. Original workflows are never executed.
"""
import argparse
from collections import deque
import json
from pathlib import Path
import re
import subprocess
import yaml
import yaml_to_model


GUARD = re.compile(
    r'if \[\[ \$\(date -d "\$(\w+)" \+%s\) '
    r'(-gt|-ge) \$\(date -d "\$(\w+)" \+%s\) \]\]; then\n'
    r'(?P<body>.*?)\n\s*fi', re.S
)


def parse_timestamp_guard(step):
    """Recognize the supported GitHub PR timestamp guard in a run step.

    Both workflow and composite-Action callers use this parser. It does not
    interpret arbitrary Bash or prove the PR API response is genuine.
    """
    if (step.get('type') != 'run' or not step.get('id')
            or step.get('condition') or not re.match(r'^(bash|sh)(?:\s|$)', step.get('shell', ''))):
        return None
    command = step.get('command', '')
    guard = GUARD.search(command)
    if not guard or 'exit 1' not in guard['body']:
        return None
    pushed, comment = guard[1], guard[3]
    if step.get('env', {}).get(comment) != '${{ github.event.comment.created_at }}':
        return None
    if not re.search(r'gh api /repos/\$\{\w+\}/pulls/\$\{\w+\}', command):
        return None
    head = re.search(r'(\w+)="\$\(echo "\$(\w+)" \| jq -r \.head.sha\)"', command)
    stamp = re.search(r'(\w+)="\$\(echo "\$(\w+)" \| jq -r \.head.repo.pushed_at\)"', command)
    if not head or not stamp or stamp[1] != pushed or head[2] != stamp[2]:
        return None
    output = re.search(r'echo "([\w-]+)=\$' + re.escape(head[1]) + r'" >> \$GITHUB_OUTPUT', command)
    if not output or command.index(output[0]) < guard.end():
        return None
    return {'operator': guard[2], 'variables': [pushed, comment],
            'outputRef': '${{ steps.' + step['id'] + '.outputs.' + output[1] + ' }}',
            'script': command}


def evidence(file, job, step, role):
    return dict(file=file, job=job['id'], step=step['index'],
                line=step['line'], role=role)


def permissions(model, job):
    # Common permission records retain their job scope.
    records = model['workflow'].get('permissions', [])
    scoped = [p for p in records if p.get('jobId') == job['id']]
    if not job.get('permissionsDeclared'):
        scoped = [p for p in records if p.get('jobId') in [None, '-']]
    return {p['scope']: p['access'] for p in scoped}


def needs(job, producer):
    return producer in ([job.get('needs')] if isinstance(job.get('needs'), str)
                        else job.get('needs', []))


def resolve_input(value, inputs):
    match = re.fullmatch(r'\$\{\{\s*inputs\.([\w-]+)\s*\}\}', value or '')
    return inputs.get(match[1]) if match else value


def find_sinks(models, model, producer, upload):
    file = model['workflow']['file']
    name = upload.get('arguments', {}).get('name')
    if not name or '${{' in name:
        return []
    found = []
    for caller in model['workflow']['jobs']:
        if not needs(caller, producer['id']) or caller.get('condition'):
            continue
        contexts = [(model, caller, {})]
        if caller.get('uses'):
            target = caller['uses'].removeprefix('./')
            if target not in models or '@' in target:
                continue
            callee = models[target]
            contexts = [(callee, j, caller.get('arguments', {}))
                        for j in callee['workflow']['jobs']]
        for consumer_model, consumer, inputs in contexts:
            if consumer.get('condition'):
                continue
            download = None
            for step in consumer['steps']:
                if step.get('condition'):
                    # Conditional reads/publishes need expression semantics.
                    continue
                args = step.get('arguments', {})
                if step.get('action') == 'actions/download-artifact':
                    if args.get('run-id') or args.get('repository'):
                        continue
                    if resolve_input(args.get('name'), inputs) == name:
                        download = step
                    continue
                if not download:
                    continue
                path = resolve_input(download.get('arguments', {}).get('path', '.'), inputs)
                publish = step.get('action') == 'pypa/gh-action-pypi-publish'
                if publish:
                    # Standard action consumes dist/ unless overridden.
                    publish = path and path.rstrip('/') == args.get('packages-dir', 'dist/').rstrip('/')
                if step.get('type') == 'run':
                    command = step.get('command', '')
                    publish = ('npm publish --access public --provenance' in command
                               and path in ['.', './']
                               and resolve_input(step.get('workingDirectory'), inputs) in ['.', './']
                               and '--ignore-scripts' not in command)
                if not publish:
                    continue
                configured = permissions(consumer_model, consumer).get('id-token') == 'write'
                if caller.get('uses'):
                    configured = configured and permissions(model, caller).get('id-token') == 'write'
                found.append(dict(
                    artifact=name, configuredOIDC=configured,
                    environment=consumer.get('environment'),
                    callerJob=caller['id'], consumerJob=consumer['id'],
                    evidence=[evidence(file, producer, upload, 'upload'),
                              evidence(consumer_model['workflow']['file'], consumer, download, 'download'),
                              evidence(consumer_model['workflow']['file'], consumer, step, 'publish')]))
    return found


def discover(root):
    models = yaml_to_model.load_models(root)
    candidates = []
    for file, model in models.items():
        if 'issue_comment' not in [e['name'] for e in model['workflow']['events']]:
            continue
        for job in model['workflow']['jobs']:
            if 'github.event.comment.author_association' not in job.get('condition', ''):
                continue
            for step in job['steps']:
                parsed = parse_timestamp_guard(step)
                if not parsed:
                    continue
                ref = parsed['outputRef']
                checkouts = [s for s in job['steps'] if s['index'] > step['index']
                             and s.get('action') == 'actions/checkout'
                             and s.get('arguments', {}).get('ref') == ref and not s.get('condition')]
                if len(checkouts) != 1:
                    continue
                checkout = checkouts[0]
                builds = [s for s in job['steps'] if s['index'] > checkout['index']
                          and s.get('action', '').startswith('./') and not s.get('condition')]
                if not builds:
                    continue
                uploads = [s for s in job['steps'] if s['index'] > builds[0]['index']
                           and s.get('action') == 'actions/upload-artifact' and not s.get('condition')]
                sinks = [sink for upload in uploads for sink in find_sinks(models, model, job, upload)]
                if not sinks:
                    continue
                candidates.append(dict(
                    id=f"{Path(file).stem}-{job['id']}-{step['id']}",
                    rejectionOperator=parsed['operator'], sourceScript=parsed['script'],
                    timestampVariables=parsed['variables'],
                    evidence=[evidence(file, job, step, 'PR-fetch-and-timestamp-guard'),
                              evidence(file, job, checkout, 'checkout-output-SHA'),
                              evidence(file, job, builds[0], 'PR-local-action')],
                    sinks=sinks,
                    assumptions=[
                        'An authorized maintainer comment exists; its authorization concerns the prior PR commit.',
                        'An external PR author can update their PR after that comment within the same API timestamp second.',
                        'API timestamps have one-second resolution; fetch atomically returns head SHA and repository pushed_at.',
                        'Supported build, upload and download steps succeed; downloaded bytes belong to this run.',
                        'The PR-controlled local action can influence uploaded output; this is a capability model, not shell feasibility proof.',
                        'OIDC permission is configured; remote trust policies, environment approvals and publication success are unknown.'
                    ]))
    return candidates


def successors(state, operator, enabled=True):
    # pc, time tick, pushed stamp (-1 = old), writer done, current head,
    # fetched SHA taint, fetched stamp, artifact taint, bad
    pc, tick, stamp, written, head, chosen, checked_stamp, artifact, bad = state
    if tick == 0:
        yield 'clock:next-second', (pc, 1, stamp, written, head, chosen, checked_stamp, artifact, bad)
    if not written:
        yield 'attacker:update-PR', (pc, tick, tick, True, True, chosen, checked_stamp, artifact, bad)
    if pc == 6:
        return
    nxt = list(state)
    nxt[0] = pc + 1
    actions = ['fetch-current-PR', 'timestamp-guard', 'checkout-and-build',
               'upload', 'download', 'privileged-publish-use']
    if pc == 0:
        nxt[5], nxt[6] = head, stamp
    elif pc == 1:
        reject = checked_stamp > 0 if operator == '-gt' else checked_stamp >= 0
        if reject:
            nxt[0] = 6
    elif pc == 3:
        nxt[7] = chosen
    elif pc == 5:
        nxt[8] = bad or (artifact and enabled)
    yield actions[pc], tuple(nxt)


def explore(operator, enabled=True):
    start = (0, 0, -1, False, False, False, -1, False, False)
    parents = {start: None}
    queue = deque([start])
    violated = None
    while queue:
        state = queue.popleft()
        if state[-1] and violated is None:
            violated = state
        for action, nxt in successors(state, operator, enabled):
            if nxt not in parents:
                parents[nxt] = (state, action)
                queue.append(nxt)
    trace = []
    while violated is not None and parents[violated] is not None:
        prev, action = parents[violated]
        trace.append(dict(action=action, state=list(violated)))
        violated = prev
    return dict(verdict='unsafe' if trace else 'safe', reachableStates=len(parents),
                counterexample=list(reversed(trace)))


def render(operator, enabled):
    rejection = 'fetched_stamp > 0' if operator == '-gt' else 'fetched_stamp >= 0'
    return f'''MODULE main
IVAR action : {{advance, update, consumer}};
VAR
  pc : 0..6;
  tick : 0..1;
  stamp : -1..1;
  written : boolean;
  head_tainted : boolean;
  chosen_tainted : boolean;
  fetched_stamp : -1..1;
  artifact_tainted : boolean;
  bad : boolean;
ASSIGN
  init(pc) := 0;
  init(tick) := 0;
  init(stamp) := -1;
  init(written) := FALSE;
  init(head_tainted) := FALSE;
  init(chosen_tainted) := FALSE;
  init(fetched_stamp) := -1;
  init(artifact_tainted) := FALSE;
  init(bad) := FALSE;
  next(pc) := case
    action = consumer & pc = 1 & ({rejection}) : 6;
    action = consumer & pc < 6 : pc + 1;
    TRUE : pc;
  esac;
  next(tick) := case action = advance : 1; TRUE : tick; esac;
  next(stamp) := case action = update & !written : tick; TRUE : stamp; esac;
  next(written) := written | action = update;
  next(head_tainted) := head_tainted | action = update;
  next(chosen_tainted) := case action = consumer & pc = 0 : head_tainted; TRUE : chosen_tainted; esac;
  next(fetched_stamp) := case action = consumer & pc = 0 : stamp; TRUE : fetched_stamp; esac;
  next(artifact_tainted) := case action = consumer & pc = 3 : chosen_tainted; TRUE : artifact_tainted; esac;
  next(bad) := bad | (action = consumer & pc = 5 & artifact_tainted & {'TRUE' if enabled else 'FALSE'});
CTLSPEC AG !bad
'''


def replay_guard(candidate):
    guard = GUARD.search(candidate['sourceScript'])[0]
    if '${{' in guard or '`' in guard or '$(' in guard.replace('$(date -d', ''):
        raise ValueError('Unsupported command substitution in isolated guard')
    # Execute only the timestamp predicate and its rejection body, in offline
    # Docker. Do not execute gh, checkout, build code, package lifecycle or publish.
    # A deterministic harmless guard preserves the exact predicate, while omitting
    # upstream logging to avoid treating arbitrary shell bodies as safe.
    predicate = guard.split('; then', 1)[0].removeprefix('if ')
    script = 'if ' + predicate + '; then exit 23; fi\n'
    results = []
    import os
    for label, pushed_at in [('before', '2026-09-04T00:00:00Z'),
                             ('same-second-after', '2026-09-04T00:00:01Z'),
                             ('next-second-after', '2026-09-04T00:00:02Z')]:
        env = dict(os.environ)
        env.update(zip(candidate['timestampVariables'], [pushed_at, '2026-09-04T00:00:01Z']))
        result = subprocess.run(['bash', '-c', script], env=env, capture_output=True, text=True)
        if result.returncode not in [0, 23]:
            raise ValueError(result.stderr)
        results.append(dict(case=label, accepted=result.returncode == 0,
                            timestampOnly=True, subsecondOrdering='assumed by test input label'))
    return results


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--replay-guard', action='store_true')
    args = p.parse_args()
    candidates = discover(args.root)
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for candidate in candidates:
        enabled = any(s['configuredOIDC'] and not s['environment'] for s in candidate['sinks'])
        candidate['bfs'] = explore(candidate['rejectionOperator'], enabled)
        candidate['property'] = 'No unapproved PR content reaches a publish step configured with id-token: write and no declared environment.'
        candidate['status'] = 'potential-risk' if enabled and candidate['bfs']['verdict'] == 'unsafe' else 'unknown'
        (args.output / (candidate['id'] + '.smv')).write_text(render(candidate['rejectionOperator'], enabled))
        if args.replay_guard:
            candidate['guardReplay'] = replay_guard(candidate)
        results.append(candidate)
    summary = dict(schemaVersion='approval-race-0.1',
                   scope='Limited Bash timestamp idiom and one-level local reusable workflow; no arbitrary shell proof',
                   status='analyzed' if results else 'no-supported-path', candidates=results)
    (args.output / 'analysis.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
