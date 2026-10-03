#!/usr/bin/env python3
"""Bounded model of a supported PR-ref selector and later local executable.

The modeled finding is conditional on the supplied external Action matching
its mutable tag at run time. The workflow, local executable and payload are
never executed. This small sequential model does not establish a performance
or expressiveness advantage over a direct script.
"""
import argparse
from collections import deque
import json
from pathlib import Path
import re

import conditional_checkout_chain as chain
import yaml_to_model


BASE_SHA = re.compile(r'\$\{\{\s*github\.sha\s*\}\}')
BASE_REF = re.compile(r'\$\{\{\s*github\.ref\s*\}\}')


def extract(root, external, external_name, external_version):
    if not chain.action_semantics(external / 'action.yml', external / 'index.js'):
        return None
    for file, model in yaml_to_model.load_models(root).items():
        events = {e['name'] for e in model['workflow']['events']}
        if 'pull_request_target' not in events:
            continue
        for job in model['workflow']['jobs']:
            if job.get('environment'):
                continue  # Approval and environment selection are outside this model.
            if not re.fullmatch(r"github\.repository == '[\w-]+/[\w-]+'", job.get('condition', '')):
                continue
            steps = job['steps']
            for selector in steps:
                if (selector.get('action') != external_name
                        or selector.get('version') != external_version
                        or not selector.get('id') or selector.get('condition')):
                    continue
                args = selector.get('arguments', {})
                if not chain.COND.fullmatch(args.get('cond', '')) or not BASE_REF.fullmatch(args.get('if_false', '')):
                    continue
                pr_ref = bool(chain.PR_MERGE.fullmatch(args.get('if_true', '')))
                if not pr_ref and not BASE_SHA.fullmatch(args.get('if_true', '')):
                    continue
                for checkout in steps:
                    if (checkout['index'] <= selector['index']
                            or checkout.get('action') != 'actions/checkout'
                            or checkout.get('condition')
                            or checkout.get('arguments', {}).get('ref')
                            != '${{ steps.' + selector['id'] + '.outputs.value }}'):
                        continue
                    for sink in steps:
                        if (sink['index'] <= checkout['index']
                                or sink.get('type') != 'run' or sink.get('condition')
                                or sink.get('workingDirectory') not in ('.', './')
                                or not chain.LOCAL_EXEC.search(sink.get('command', ''))):
                            continue
                        if any(s.get('action') == 'actions/checkout'
                               and checkout['index'] < s['index'] < sink['index'] for s in steps):
                            continue
                        secrets = sorted({name for value in sink.get('env', {}).values()
                                          for name in chain.SECRET.findall(value)})
                        if not secrets:
                            continue
                        return dict(file=file, job=job['id'], event='pull_request_target',
                                    prRefSelected=pr_ref, selectorRef=args['if_true'],
                                    localCommand=chain.LOCAL_EXEC.search(sink['command'])[0].strip(),
                                    configuredSecrets=secrets,
                                    evidence=[chain.evidence(file, job, selector, 'selector'),
                                              chain.evidence(file, job, checkout, 'checkout'),
                                              chain.evidence(file, job, sink, 'local-executable')])
    return None


def unsupported_environment_jobs(root):
    return [dict(file=file, job=job['id'])
            for file, model in yaml_to_model.load_models(root).items()
            if any(e['name'] == 'pull_request_target' for e in model['workflow']['events'])
            for job in model['workflow']['jobs'] if job.get('environment')]


def fork_excluded_jobs(root, external, external_name, external_version):
    result = chain.discover(root, external, external_name, external_version)
    return [dict(file=record['file'], job=record['job'])
            for record in result.get('forkExcludedJobs', [])]


def successors(state, pr_ref):
    # pc, event is PR, selector chooses PR, checkout contains PR,
    # merge exists, checkout succeeds, secret available, command succeeds, bad
    pc, is_pr, selected, checked, merge, checkout_ok, secret, executed, bad = state
    if pc == 4:
        return
    nxt = list(state)
    nxt[0] = pc + 1
    if pc == 0:
        nxt[2] = is_pr and pr_ref
    elif pc == 1:
        nxt[3] = selected and merge and checkout_ok
    elif pc == 2:
        nxt[8] = checked and secret and executed
    yield ['select-ref', 'checkout-ref', 'run-local-executable', 'finish'][pc], tuple(nxt)


def explore(pr_ref):
    initial = [(0, is_pr, False, False, merge, checkout_ok, secret, executed, False)
               for is_pr in (False, True)
               for merge in (False, True)
               for checkout_ok in (False, True)
               for secret in (False, True)
               for executed in (False, True)]
    parents = {s: None for s in initial}
    queue = deque(initial)
    target = None
    while queue:
        state = queue.popleft()
        if state[-1] and target is None:
            target = state
        for action, next_state in successors(state, pr_ref):
            if next_state not in parents:
                parents[next_state] = (state, action)
                queue.append(next_state)
    trace = []
    while target is not None and parents[target] is not None:
        previous, action = parents[target]
        trace.append(dict(action=action, state=list(target)))
        target = previous
    return dict(verdict='unsafe' if trace else 'safe',
                reachableStates=len(parents), counterexample=list(reversed(trace)))


def render(pr_ref):
    return f'''MODULE main
IVAR advance : boolean;
FROZENVAR
  pr_event : boolean;
  merge_exists : boolean;
  checkout_succeeds : boolean;
  secret_available : boolean;
  command_succeeds : boolean;
VAR
  pc : 0..4;
  selected_pr : boolean;
  checked_pr : boolean;
  bad : boolean;
ASSIGN
  init(pc) := 0;
  init(selected_pr) := FALSE;
  init(checked_pr) := FALSE;
  init(bad) := FALSE;
  next(pc) := case advance & pc < 4 : pc + 1; TRUE : pc; esac;
  next(selected_pr) := case advance & pc = 0 : pr_event & {'TRUE' if pr_ref else 'FALSE'}; TRUE : selected_pr; esac;
  next(checked_pr) := case advance & pc = 1 : selected_pr & merge_exists & checkout_succeeds; TRUE : checked_pr; esac;
  next(bad) := bad | (advance & pc = 2 & checked_pr & secret_available & command_succeeds);
CTLSPEC AG !bad
'''


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('workflow_root', type=Path)
    p.add_argument('external_root', type=Path)
    p.add_argument('--external-name', required=True)
    p.add_argument('--external-version', required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    config = extract(args.workflow_root, args.external_root,
                     args.external_name, args.external_version)
    args.output.mkdir(parents=True, exist_ok=True)
    unsupported = unsupported_environment_jobs(args.workflow_root)
    excluded = fork_excluded_jobs(args.workflow_root, args.external_root,
                                  args.external_name, args.external_version)
    result = dict(status='analyzed' if config else
                  'unsupported-environment-gate' if unsupported else
                  'fork-excluded' if excluded else 'no-supported-path',
                  config=config)
    if unsupported:
        result['unsupportedJobs'] = unsupported
    if excluded:
        result['forkExcludedJobs'] = excluded
    if config:
        result['property'] = ('PR-controlled local executable never runs with a '
                              'configured secret in a pull_request_target job.')
        result['assumptions'] = [
            'The mutable external Action tag resolves to the supplied implementation at run time.',
            'The job repository condition holds; PR merge ref exists; checkout and local command may succeed.',
            'The configured secret may be available. This model does not assert exfiltration or actual CI execution.'
        ]
        result['bfs'] = explore(config['prRefSelected'])
        (args.output / 'conditional-checkout.smv').write_text(render(config['prRefSelected']))
    (args.output / 'analysis.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
