#!/usr/bin/env python3
"""Scale the TanStack cache scheduling model to synthetic release-run counts.

N=2 corresponds to the two public release runs. Other counts are sensitivity
experiments, not claims about the incident. Source roles and cache scope come
from the pinned incident YAML through tanstack_cache_chain.
"""
import argparse
from collections import deque
from itertools import product
import json
from pathlib import Path
import time


def facts_for(count):
    return ('save',) + tuple(f'{kind}{n}' for n in range(1, count + 1)
                              for kind in ('key', 'restore', 'execute'))


def initial(count, facts):
    return (0, *(0 for _ in range(count)), False,
            *(False for _ in range(count)), *(False for _ in range(count)), *facts)


def transition(state, actor, count, compatible):
    values = list(state)
    cache_at = count + 1
    restored_at = count + 2
    bad_at = 2 * count + 2
    facts_at = 3 * count + 2
    if actor == 0:
        if values[0] == 4:
            return None
        if values[0] == 3 and compatible and values[facts_at]:
            values[cache_at] = True
        values[0] += 1
    else:
        n = actor - 1
        if values[actor] == 2:
            return None
        if values[actor] == 0:
            values[restored_at + n] = (values[cache_at] and
                                       values[facts_at + 1 + 3 * n] and
                                       values[facts_at + 2 + 3 * n])
        else:
            values[bad_at + n] = (values[bad_at + n] or
                                   (values[restored_at + n] and
                                    values[facts_at + 3 + 3 * n]))
        values[actor] += 1
    return tuple(values)


def explore(count, compatible, frozen_unknown=True):
    names = facts_for(count)
    roots = [initial(count, facts) for facts in
             (product((False, True), repeat=len(names)) if frozen_unknown
              else [(True,) * len(names)])]
    queue = deque(roots)
    seen = set(roots)
    first_bad = None
    started = time.perf_counter()
    while queue:
        state = queue.popleft()
        if first_bad is None and any(state[2 * count + 2:3 * count + 2]):
            first_bad = state
        for actor in range(count + 1):
            nxt = transition(state, actor, count, compatible)
            if nxt is not None and nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return {'consumers': count, 'frozenUnknownFacts': frozen_unknown,
            'reachableStates': len(seen), 'possible': first_bad is not None,
            'witnessFacts': dict(zip(names, first_bad[3 * count + 2:]))
            if first_bad is not None else None,
            'seconds': round(time.perf_counter() - started, 3)}


def render(count, compatible, evidence, frozen_unknown=True):
    actors = ['producer'] + [f'consumer{n}' for n in range(1, count + 1)]
    lines = [f'-- One YAML-derived producer and {count} independent consumers.',
             '-- Counts other than two are synthetic sensitivity experiments.']
    lines += ['-- EVIDENCE {role}: {file}:{line}'.format(**item) for item in evidence]
    lines += ['MODULE main', 'IVAR', '  actor : {' + ', '.join(actors) + '};']
    if frozen_unknown:
        lines += ['FROZENVAR']
        lines += [f'  {name} : boolean;' for name in facts_for(count)]
    lines += ['VAR', '  producer_pc : 0..4;']
    lines += [f'  consumer{n}_pc : 0..2;' for n in range(1, count + 1)]
    lines += ['  cache_tainted : boolean;']
    for kind in ('restored', 'bad'):
        lines += [f'  {kind}{n} : boolean;' for n in range(1, count + 1)]
    lines += ['ASSIGN', '  init(producer_pc) := 0;']
    lines += [f'  init(consumer{n}_pc) := 0;' for n in range(1, count + 1)]
    lines += ['  init(cache_tainted) := FALSE;']
    for kind in ('restored', 'bad'):
        lines += [f'  init({kind}{n}) := FALSE;' for n in range(1, count + 1)]
    lines += ['  next(producer_pc) := case actor = producer & producer_pc < 4 : producer_pc + 1; TRUE : producer_pc; esac;']
    lines += [f'  next(consumer{n}_pc) := case actor = consumer{n} & consumer{n}_pc < 2 : consumer{n}_pc + 1; TRUE : consumer{n}_pc; esac;'
              for n in range(1, count + 1)]
    value = 'save' if frozen_unknown else 'TRUE'
    scope = 'TRUE' if compatible else 'FALSE'
    lines += [f'  next(cache_tainted) := cache_tainted | (actor = producer & producer_pc = 3 & {scope} & {value});']
    for n in range(1, count + 1):
        key = f'key{n}' if frozen_unknown else 'TRUE'
        restore = f'restore{n}' if frozen_unknown else 'TRUE'
        execute = f'execute{n}' if frozen_unknown else 'TRUE'
        lines += [f'  next(restored{n}) := case',
                  f'    actor = consumer{n} & consumer{n}_pc = 0 : cache_tainted & {key} & {restore};',
                  f'    TRUE : restored{n};', '  esac;',
                  f'  next(bad{n}) := bad{n} | (actor = consumer{n} & consumer{n}_pc = 1 & restored{n} & {execute});']
    lines += ['CTLSPEC AG !(' + ' | '.join(f'bad{n}' for n in range(1, count + 1)) + ')', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--consumers', type=int, default=2)
    parser.add_argument('--fixed-true', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.consumers <= 8:
        parser.error('--consumers must be between 1 and 8')
    import tanstack_cache_chain
    manifest = json.loads((args.root / 'manifest.json').read_text())
    tanstack_cache_chain.verify_sources(args.root, manifest)
    args.output.mkdir(parents=True, exist_ok=True)
    report = {'schemaVersion': 'tanstack-n-consumers-0.1',
              'scope': 'One YAML-derived producer; N synthetic consumers except N=2 public-run count; clean initial cache; no eviction',
              'consumers': args.consumers, 'frozenUnknownFacts': not args.fixed_true,
              'cases': []}
    for variant in ('pre-incident', 'mitigation'):
        source = tanstack_cache_chain.analyze(args.root, variant, manifest)
        if not source.get('producer') or not source.get('consumer'):
            raise ValueError(f'No supported source path: {variant}')
        compatible = source['cache']['sameDefaultBranchScopePossibleForFork']
        model = render(args.consumers, compatible, source['evidence'],
                       not args.fixed_true)
        (args.output / f'{variant}.smv').write_text(model)
        report['cases'].append({'variant': variant, 'cacheScopeCompatible': compatible,
                                'sourceEvidence': source['evidence'],
                                'bfs': explore(args.consumers, compatible,
                                               not args.fixed_true)})
    (args.output / 'analysis.json').write_text(json.dumps(report, indent=2) + '\n')


if __name__ == '__main__':
    main()
