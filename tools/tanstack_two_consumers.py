#!/usr/bin/env python3
"""Explore one YAML-derived cache producer and two independent release runs.

The two consumers reflect the number of release runs in the public postmortem.
Each consumer has independent key, restore and execution outcomes. This is a
bounded scheduling model, not a replay of the incident's cache service.
"""
import argparse
from collections import deque
from itertools import product
import json
from pathlib import Path

FACTS = ('save', 'key1', 'restore1', 'execute1', 'key2', 'restore2', 'execute2')
ACTORS = ('producer', 'consumer1', 'consumer2')


def initial(facts):
    # Producer PC, consumer PCs, cache, restored flags, bad flags, frozen facts.
    return (0, 0, 0, False, False, False, False, False, *facts)


def transition(state, actor, compatible):
    producer, c1, c2, cache, restored1, restored2, bad1, bad2, *facts = state
    save, key1, read1, execute1, key2, read2, execute2 = facts
    if actor == 'producer':
        if producer == 4:
            return None
        cache = cache or (producer == 3 and compatible and save)
        producer += 1
    elif actor == 'consumer1':
        if c1 == 2:
            return None
        if c1 == 0:
            restored1 = cache and key1 and read1
        else:
            bad1 = bad1 or (restored1 and execute1)
        c1 += 1
    elif actor == 'consumer2':
        if c2 == 2:
            return None
        if c2 == 0:
            restored2 = cache and key2 and read2
        else:
            bad2 = bad2 or (restored2 and execute2)
        c2 += 1
    else:
        raise ValueError(actor)
    return (producer, c1, c2, cache, restored1, restored2, bad1, bad2, *facts)


def simulate(schedule, compatible, facts=(True,) * 7):
    state = initial(facts)
    trace = []
    for actor in schedule:
        state = transition(state, actor, compatible)
        if state is None:
            raise ValueError(f'actor already finished: {actor}')
        trace.append({'actor': actor, 'producerPC': state[0],
                      'consumer1PC': state[1], 'consumer2PC': state[2],
                      'cacheTainted': state[3], 'restored1': state[4],
                      'restored2': state[5], 'bad1': state[6], 'bad2': state[7]})
    return {'bad1': state[6], 'bad2': state[7], 'trace': trace}


def explore(compatible):
    roots = [initial(facts) for facts in product((False, True), repeat=7)]
    queue = deque(roots)
    parents = {state: None for state in roots}
    violation = None
    while queue:
        state = queue.popleft()
        if (state[6] or state[7]) and violation is None:
            violation = state
        for actor in ACTORS:
            nxt = transition(state, actor, compatible)
            if nxt is not None and nxt not in parents:
                parents[nxt] = (state, actor)
                queue.append(nxt)
    trace = []
    facts = None
    if violation is not None:
        facts = dict(zip(FACTS, violation[8:]))
        state = violation
        while parents[state] is not None:
            prior, actor = parents[state]
            trace.append({'actor': actor, 'producerPC': state[0],
                          'consumer1PC': state[1], 'consumer2PC': state[2],
                          'cacheTainted': state[3], 'restored1': state[4],
                          'restored2': state[5], 'bad1': state[6], 'bad2': state[7]})
            state = prior
        trace.reverse()
    return {'verdict': 'possible' if violation else 'no-path-in-supported-model',
            'reachableStates': len(parents), 'witnessFacts': facts,
            'conditionalCounterexample': trace}


def render(compatible, evidence):
    scope = 'TRUE' if compatible else 'FALSE'
    lines = ['-- One producer and two independently scheduled release runs.',
             '-- All cache outcomes are frozen unknown facts; initial cache is clean.']
    lines += ['-- EVIDENCE {role}: {file}:{line}'.format(**item) for item in evidence]
    lines += ['MODULE main', 'IVAR', '  actor : {producer, consumer1, consumer2};',
              'FROZENVAR']
    lines += [f'  {name} : boolean;' for name in FACTS]
    lines += ['VAR', '  producer_pc : 0..4;', '  consumer1_pc : 0..2;',
              '  consumer2_pc : 0..2;', '  cache_tainted : boolean;',
              '  restored1 : boolean;', '  restored2 : boolean;',
              '  bad1 : boolean;', '  bad2 : boolean;', 'ASSIGN',
              '  init(producer_pc) := 0;', '  init(consumer1_pc) := 0;',
              '  init(consumer2_pc) := 0;', '  init(cache_tainted) := FALSE;',
              '  init(restored1) := FALSE;', '  init(restored2) := FALSE;',
              '  init(bad1) := FALSE;', '  init(bad2) := FALSE;',
              '  next(producer_pc) := case actor = producer & producer_pc < 4 : producer_pc + 1; TRUE : producer_pc; esac;',
              '  next(consumer1_pc) := case actor = consumer1 & consumer1_pc < 2 : consumer1_pc + 1; TRUE : consumer1_pc; esac;',
              '  next(consumer2_pc) := case actor = consumer2 & consumer2_pc < 2 : consumer2_pc + 1; TRUE : consumer2_pc; esac;',
              f'  next(cache_tainted) := cache_tainted | (actor = producer & producer_pc = 3 & {scope} & save);']
    for n in (1, 2):
        lines += [f'  next(restored{n}) := case',
                  f'    actor = consumer{n} & consumer{n}_pc = 0 : cache_tainted & key{n} & restore{n};',
                  f'    TRUE : restored{n};', '  esac;',
                  f'  next(bad{n}) := bad{n} | (actor = consumer{n} & consumer{n}_pc = 1 & restored{n} & execute{n});']
    lines += ['CTLSPEC AG !(bad1 | bad2)', '']
    return '\n'.join(lines)


def analyze(root, output):
    import tanstack_cache_chain

    manifest = json.loads((root / 'manifest.json').read_text())
    tanstack_cache_chain.verify_sources(root, manifest)
    output.mkdir(parents=True, exist_ok=True)
    cases = []
    for variant in ('pre-incident', 'mitigation'):
        source = tanstack_cache_chain.analyze(root, variant, manifest)
        if not source.get('producer') or not source.get('consumer'):
            raise ValueError(f'No supported source path: {variant}')
        compatible = source['cache']['sameDefaultBranchScopePossibleForFork']
        model_name = variant + '.smv'
        (output / model_name).write_text(render(compatible, source['evidence']))
        cases.append({'variant': variant, 'producer': source['producer'],
                      'consumer': source['consumer'], 'cacheScopeCompatible': compatible,
                      'sourceEvidence': source['evidence'], 'model': model_name,
                      'bfs': explore(compatible), 'fixedFactSchedules': {
                          'saveBeforeBothRestores': simulate(['producer'] * 4 + ['consumer1', 'consumer2'] * 2, compatible),
                          'saveBetweenRestores': simulate(['consumer1'] + ['producer'] * 4 + ['consumer2', 'consumer1', 'consumer2'], compatible),
                          'saveAfterBothRestores': simulate(['consumer1', 'consumer2'] + ['producer'] * 4 + ['consumer1', 'consumer2'], compatible),
                      }})
    report = {'schemaVersion': 'cross-run-two-consumers-0.1',
              'scope': 'One PR producer and two release consumers from the same YAML-derived roles; initial cache clean; no eviction or third producer',
              'property': 'Neither release run executes PR-tainted bytes in its OIDC-enabled job',
              'source': 'YAML-derived roles; incident observations used only to choose two consumers, not to decide the verdict',
              'unknownFacts': list(FACTS), 'cases': cases}
    (output / 'analysis.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    analyze(args.root, args.output)


if __name__ == '__main__':
    main()
