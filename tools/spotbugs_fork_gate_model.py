#!/usr/bin/env python3
"""Check SpotBugs's PR gate against the GitHub pull_request_target context.

The base-repository name is fixed under pull_request_target. The PR head's
repository differs for an external fork. Runtime Action identity, checkout,
secret availability and command execution remain explicit unknown facts.
"""
import argparse
from collections import deque
import hashlib
import json
from pathlib import Path

import conditional_checkout_chain as chain
import yaml_to_model


BASE_GATE = "github.repository == 'spotbugs/sonar-findbugs'"
FORK_GATE = 'github.event.pull_request.head.repo.full_name == github.repository'


def gate_for(root):
    models = yaml_to_model.load_models(root)
    matches = [(path, job) for path, workflow in models.items()
               if any(event['name'] == 'pull_request_target'
                      for event in workflow['workflow']['events'])
               for job in workflow['workflow']['jobs']
               if job['id'] == 'build']
    if len(matches) != 1:
        raise ValueError('Expected exactly one supported pull_request_target build job')
    path, job = matches[0]
    condition = job['condition']
    if condition not in (BASE_GATE, FORK_GATE):
        raise ValueError(f'Unsupported gate: {condition}')
    return path, condition


def explore(gate):
    # pc, external fork, merge, checkout, secret, command, selected, checked, bad
    roots = [(0, fork, merge, checkout, secret, command, False, False, False)
             for fork in (False, True) for merge in (False, True)
             for checkout in (False, True) for secret in (False, True)
             for command in (False, True)]
    queue = deque(roots)
    parents = {state: None for state in roots}
    target = None
    while queue:
        state = queue.popleft()
        pc, fork, merge, checkout, secret, command, selected, checked, bad = state
        if fork and bad and target is None:
            target = state
        if pc == 3:
            continue
        nxt = list(state)
        nxt[0] += 1
        if pc == 0:
            # github.repository is the base repo in either fork case.
            gate_holds = gate == BASE_GATE or not fork
            nxt[6] = gate_holds
        elif pc == 1:
            nxt[7] = selected and merge and checkout
        else:
            nxt[8] = checked and secret and command
        nxt = tuple(nxt)
        if nxt not in parents:
            parents[nxt] = state
            queue.append(nxt)
    trace = []
    while target is not None:
        trace.append({'pc': target[0], 'externalFork': target[1],
                      'selectedPR': target[6], 'checkedPR': target[7],
                      'bad': target[8]})
        target = parents[target]
    return {'possibleExternalForkPath': bool(trace),
            'reachableStates': len(parents),
            'counterexample': list(reversed(trace))}


def render(gate):
    gate_expr = 'TRUE' if gate == BASE_GATE else '!external_fork'
    return f'''-- github.repository is the base repo under pull_request_target.
-- external_fork means PR head repo != base repo.
MODULE main
IVAR
  advance : boolean;
FROZENVAR
  external_fork : boolean;
  merge_exists : boolean;
  checkout_succeeds : boolean;
  secret_available : boolean;
  command_succeeds : boolean;
VAR
  pc : 0..3;
  selected_pr : boolean;
  checked_pr : boolean;
  bad : boolean;
ASSIGN
  init(pc) := 0;
  init(selected_pr) := FALSE;
  init(checked_pr) := FALSE;
  init(bad) := FALSE;
  next(pc) := case advance & pc < 3 : pc + 1; TRUE : pc; esac;
  next(selected_pr) := case advance & pc = 0 : {gate_expr}; TRUE : selected_pr; esac;
  next(checked_pr) := case advance & pc = 1 : selected_pr & merge_exists & checkout_succeeds; TRUE : checked_pr; esac;
  next(bad) := bad | (advance & pc = 2 & checked_pr & secret_available & command_succeeds);
CTLSPEC AG !(external_fork & bad)
'''


def analyze(original, control, external, output):
    original_file, original_gate = gate_for(original)
    control_file, control_gate = gate_for(control)
    if original_gate != BASE_GATE or control_gate != FORK_GATE:
        raise ValueError('Original/control gates do not match expected semantics')
    original_text = (original / original_file).read_text()
    control_text = (control / control_file).read_text()
    if control_text != original_text.replace(BASE_GATE, FORK_GATE):
        raise ValueError('Control must differ only in the job gate')
    if not chain.action_semantics(external / 'action.yml', external / 'index.js'):
        raise ValueError('External selector semantics unsupported')
    source = chain.discover(original, external, 'haya14busa/action-cond', 'v1')
    safe = chain.discover(control, external, 'haya14busa/action-cond', 'v1')
    if len(source['findings']) != 1 or safe['status'] != 'fork-excluded':
        raise ValueError('Expected original path and fork-excluded control')
    output.mkdir(parents=True, exist_ok=True)
    cases = []
    for label, root, path, gate in [('original', original, original_file, original_gate),
                                     ('fork-excluded-control', control, control_file, control_gate)]:
        model = render(gate)
        (output / (label + '.smv')).write_text(model)
        cases.append({'label': label, 'workflow': str(root / path),
                      'workflowSHA256': hashlib.sha256((root / path).read_bytes()).hexdigest(),
                      'gate': gate, 'model': label + '.smv',
                      'modelSHA256': hashlib.sha256(model.encode()).hexdigest(),
                      'bfs': explore(gate)})
    report = {'schemaVersion': 'spotbugs-fork-gate-0.1',
              'property': 'External fork PR bytes never reach a secret-bearing local executable',
              'sourceActionSHA256': hashlib.sha256((external / 'index.js').read_bytes()).hexdigest(),
              'semantics': 'Under pull_request_target, github.repository names the base repository; an external fork has a different PR head repository.',
              'cases': cases}
    (output / 'analysis.json').write_text(json.dumps(report, indent=2) + '\n')
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('original', type=Path)
    p.add_argument('control', type=Path)
    p.add_argument('external', type=Path)
    p.add_argument('--output', required=True, type=Path)
    args = p.parse_args()
    analyze(args.original, args.control, args.external, args.output)


if __name__ == '__main__':
    main()
