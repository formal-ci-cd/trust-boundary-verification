#!/usr/bin/env python3
"""Explore two independently scheduled runs in the pinned TanStack cache case.

The producer/consumer roles and cache scope come from the YAML analyzer.
Cache writes, effective keys, restore outcome, and execution remain explicit
unknown facts. This models one fork-PR run and one release run, not the whole
GitHub cache service or the actual incident timeline.
"""
import argparse
from collections import deque
import json
from itertools import product
from pathlib import Path

import tanstack_cache_chain


FACTS = ('tainted_cache_saved', 'same_effective_key',
         'poisoned_entry_restored', 'poisoned_code_invoked')


def initial(facts):
    # producer pc, consumer pc, cache taint, restored taint, bad, facts
    return (0, 0, False, False, False, *facts)


def transition(state, actor, compatible):
    producer, consumer, cache, restored, bad, save, key, read, execute = state
    if actor == 'producer':
        if producer == 4:
            return None
        if producer == 3:
            cache = cache or (compatible and save)
        return (producer + 1, consumer, cache, restored, bad,
                save, key, read, execute)
    if actor == 'consumer':
        if consumer == 2:
            return None
        if consumer == 0:
            restored = cache and key and read
        else:
            bad = bad or (restored and execute)
        return (producer, consumer + 1, cache, restored, bad,
                save, key, read, execute)
    raise ValueError(actor)


def simulate(schedule, compatible, facts=(True, True, True, True)):
    state = initial(facts)
    trace = []
    for actor in schedule:
        nxt = transition(state, actor, compatible)
        if nxt is None:
            raise ValueError(f'actor already finished: {actor}')
        state = nxt
        trace.append({'actor': actor, 'producerPC': state[0],
                      'consumerPC': state[1], 'cacheTainted': state[2],
                      'restoredTainted': state[3], 'bad': state[4]})
    return {'bad': state[4], 'trace': trace}


def explore(compatible):
    roots = [initial(facts) for facts in product((False, True), repeat=len(FACTS))]
    parents = {state: None for state in roots}
    queue = deque(roots)
    violation = None
    while queue:
        state = queue.popleft()
        if state[4] and violation is None:
            violation = state
        for actor in ('producer', 'consumer'):
            nxt = transition(state, actor, compatible)
            if nxt is not None and nxt not in parents:
                parents[nxt] = (state, actor)
                queue.append(nxt)
    path = []
    facts = None
    if violation is not None:
        facts = dict(zip(FACTS, violation[5:]))
        state = violation
        while parents[state] is not None:
            previous, actor = parents[state]
            path.append({'actor': actor, 'producerPC': state[0],
                         'consumerPC': state[1], 'cacheTainted': state[2],
                         'restoredTainted': state[3], 'bad': state[4]})
            state = previous
        path.reverse()
    return {'verdict': 'possible' if violation else 'no-path-in-supported-model',
            'reachableStates': len(parents), 'witnessFacts': facts,
            'conditionalCounterexample': path}


def render(compatible, evidence):
    scope = 'TRUE' if compatible else 'FALSE'
    lines = [
        '-- One producer run and one consumer run; scheduling is nondeterministic.',
        '-- producer_pc: 0 before PR checkout, 1 checked out, 2 Setup, 3 build, 4 post-job cache save.',
        '-- consumer_pc: 0 before cache restore, 1 restored, 2 code use in OIDC-enabled job.',
        '-- bad: PR-tainted bytes were used by the OIDC-enabled job.',
    ]
    for item in evidence:
        lines.append('-- EVIDENCE {role}: {file}:{line}'.format(**item))
    lines += [
        'MODULE main',
        'IVAR',
        '  actor : {producer, consumer};',
        'FROZENVAR',
        '  tainted_cache_saved : boolean;',
        '  same_effective_key : boolean;',
        '  poisoned_entry_restored : boolean;',
        '  poisoned_code_invoked : boolean;',
        'VAR',
        '  producer_pc : 0..4;',
        '  consumer_pc : 0..2;',
        '  cache_tainted : boolean;',
        '  restored_tainted : boolean;',
        '  bad : boolean;',
        'ASSIGN',
        '  init(producer_pc) := 0;',
        '  init(consumer_pc) := 0;',
        '  init(cache_tainted) := FALSE;',
        '  init(restored_tainted) := FALSE;',
        '  init(bad) := FALSE;',
        '  next(producer_pc) := case actor = producer & producer_pc < 4 : producer_pc + 1; TRUE : producer_pc; esac;',
        '  next(consumer_pc) := case actor = consumer & consumer_pc < 2 : consumer_pc + 1; TRUE : consumer_pc; esac;',
        f'  next(cache_tainted) := cache_tainted | (actor = producer & producer_pc = 3 & {scope} & tainted_cache_saved);',
        '  next(restored_tainted) := case',
        '    actor = consumer & consumer_pc = 0 : cache_tainted & same_effective_key & poisoned_entry_restored;',
        '    TRUE : restored_tainted;',
        '  esac;',
        '  next(bad) := bad | (actor = consumer & consumer_pc = 1 & restored_tainted & poisoned_code_invoked);',
        'CTLSPEC AG !bad',
        '',
    ]
    return '\n'.join(lines)


def analyze(root, output):
    manifest = json.loads((root / 'manifest.json').read_text())
    tanstack_cache_chain.verify_sources(root, manifest)
    output.mkdir(parents=True, exist_ok=True)
    cases = []
    for variant in ('pre-incident', 'mitigation'):
        source = tanstack_cache_chain.analyze(root, variant, manifest)
        if not source.get('producer') or not source.get('consumer'):
            raise ValueError(f'No supported source path: {variant}')
        compatible = source['cache']['sameDefaultBranchScopePossibleForFork']
        bfs = explore(compatible)
        name = variant + '.smv'
        (output / name).write_text(render(compatible, source['evidence']))
        cases.append({'variant': variant,
                      'producer': source['producer'],
                      'consumer': source['consumer'],
                      'cacheScopeCompatible': compatible,
                      'sourceEvidence': source['evidence'],
                      'model': name,
                      'bfs': bfs,
                      'fixedFactSchedules': {
                          'producerSavesBeforeConsumerRestore': simulate(
                              ['producer'] * 4 + ['consumer'] * 2, compatible),
                          'consumerRestoresBeforeProducerSave': simulate(
                              ['consumer'] + ['producer'] * 4 + ['consumer'], compatible),
                      }})
    report = {
        'schemaVersion': 'cross-run-interleaving-0.1',
        'scope': 'One fork PR run and one release run; four unknown facts; no cache eviction or third run',
        'property': 'PR-tainted bytes are not executed in the OIDC-enabled release job',
        'source': 'YAML-derived roles via tanstack_cache_chain.analyze; incident observations not used as inputs',
        'modelAssumptions': [
            'A completed producer build can produce a tainted cache entry if its post-job save succeeds.',
            'The consumer restores only the cache state present at its restore step.',
            'The consumer does not re-restore or revalidate before its local code use.',
            'Action success, effective key equality, restored entry and code execution remain unconstrained Boolean facts.',
        ],
        'cases': cases,
    }
    (output / 'analysis.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    analyze(args.root, args.output)


if __name__ == '__main__':
    main()
