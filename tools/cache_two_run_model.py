#!/usr/bin/env python3
"""Finite two-run cache handoff with independent BFS and NuSMV rendering."""
from collections import deque
from itertools import product

FACTS = ('writeAuthorized', 'writeSucceeded', 'producerTaintsCache',
         'producerCallEnabled', 'consumerCallEnabled', 'sameObject', 'readSucceeded',
         'producerActionMatches', 'consumerActionMatches', 'useSucceeded',
         'integrityVerified',
         'hasAuthority', 'sinkReachable')


def assignments(facts):
    unknown = [name for name in FACTS if facts[name] == 'unknown']
    fixed = {name: facts[name] == 'true' for name in FACTS if facts[name] != 'unknown'}
    for values in product((False, True), repeat=len(unknown)):
        setting = fixed | dict(zip(unknown, values))
        if setting['writeSucceeded'] and not setting['writeAuthorized']:
            continue
        yield setting


def initial(setting):
    # producer PC, consumer PC, tainted cache, tainted restore, violation, frozen facts
    return (0, 0, False, False, False, *(setting[name] for name in FACTS))


def transition(state, actor):
    producer, consumer, cache, restored, bad, *values = state
    facts = dict(zip(FACTS, values))
    if actor == 'producer':
        if producer >= 2:
            return None
        if producer == 1:
            cache = cache or (facts['writeAuthorized'] and facts['writeSucceeded']
                              and facts['producerTaintsCache'] and facts['producerCallEnabled']
                              and facts['producerActionMatches'])
        return (producer + 1, consumer, cache, restored, bad, *values)
    if actor == 'consumer':
        if consumer >= 3:
            return None
        if consumer == 0:
            restored = (cache and facts['consumerCallEnabled'] and facts['sameObject'] and facts['readSucceeded']
                        and facts['consumerActionMatches'])
        elif consumer == 1:
            restored = restored and facts['useSucceeded'] and not facts['integrityVerified']
        else:
            bad = bad or (restored and facts['hasAuthority'] and facts['sinkReachable'])
        return (producer, consumer + 1, cache, restored, bad, *values)
    raise ValueError(actor)


def explore(facts):
    roots = [initial(setting) for setting in assignments(facts)]
    parents = {state: None for state in roots}
    queue = deque(roots)
    violation = None
    while queue:
        state = queue.popleft()
        if state[4]:
            violation = state
            break
        for actor in ('producer', 'consumer'):
            nxt = transition(state, actor)
            if nxt is not None and nxt not in parents:
                parents[nxt] = (state, actor)
                queue.append(nxt)
    trace = []
    witness = None
    if violation:
        witness = dict(zip(FACTS, violation[5:]))
        state = violation
        while parents[state]:
            previous, actor = parents[state]
            trace.append({'actor': actor, 'producerPC': state[0],
                          'consumerPC': state[1], 'cacheTainted': state[2],
                          'restoredTainted': state[3], 'bad': state[4]})
            state = previous
        trace.reverse()
    return {'verdict': 'unsafe' if violation else 'safe',
            'reachableStatesBeforeFirstViolation': len(parents),
            'witnessFacts': witness, 'counterexample': trace}


def render(facts, evidence):
    for name in FACTS:
        if facts[name] not in ('true', 'false', 'unknown'):
            raise ValueError(f'Invalid fact {name}: {facts[name]}')
    lines = ['-- One producer run and one consumer run. No runtime success is inferred.',
             '-- SameObject is an object identity fact, never key-string equality.']
    for item in evidence:
        lines.append('-- EVIDENCE {role}: {file}:{line}'.format(**item))
    lines += ['MODULE main', 'IVAR', '  actor : {producer, consumer};']
    unknown = [name for name in FACTS if facts[name] == 'unknown']
    if unknown:
        lines.append('FROZENVAR')
        lines.extend(f'  {name} : boolean;' for name in unknown)
    lines += ['VAR', '  producer_pc : 0..2;', '  consumer_pc : 0..3;',
              '  cache_tainted : boolean;', '  restored_tainted : boolean;',
              '  bad : boolean;']
    fixed = [name for name in FACTS if facts[name] != 'unknown']
    if fixed:
        lines.append('DEFINE')
        lines.extend(f"  {name} := {'TRUE' if facts[name] == 'true' else 'FALSE'};"
                     for name in fixed)
    lines += [
        'INVAR writeSucceeded -> writeAuthorized',
        'ASSIGN',
        '  init(producer_pc) := 0;', '  init(consumer_pc) := 0;',
        '  init(cache_tainted) := FALSE;', '  init(restored_tainted) := FALSE;',
        '  init(bad) := FALSE;',
        '  next(producer_pc) := case',
        '    actor = producer & producer_pc < 2 : producer_pc + 1;',
        '    TRUE : producer_pc;', '  esac;',
        '  next(consumer_pc) := case',
        '    actor = consumer & consumer_pc < 3 : consumer_pc + 1;',
        '    TRUE : consumer_pc;', '  esac;',
        '  next(cache_tainted) := case',
        '    actor = producer & producer_pc = 1 : cache_tainted | (writeAuthorized & writeSucceeded & producerTaintsCache & producerCallEnabled & producerActionMatches);',
        '    TRUE : cache_tainted;', '  esac;',
        '  next(restored_tainted) := case',
        '    actor = consumer & consumer_pc = 0 : cache_tainted & consumerCallEnabled & sameObject & readSucceeded & consumerActionMatches;',
        '    actor = consumer & consumer_pc = 1 : restored_tainted & useSucceeded & !integrityVerified;',
        '    TRUE : restored_tainted;', '  esac;',
        '  next(bad) := bad | (actor = consumer & consumer_pc = 2 & restored_tainted & hasAuthority & sinkReachable);',
        'CTLSPEC AG !bad', '']
    return '\n'.join(lines)
