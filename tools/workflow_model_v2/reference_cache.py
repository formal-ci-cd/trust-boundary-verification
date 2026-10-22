"""Independent explicit-state interpreter for a bounded cache/shell subset.

Reads YAML contracts, never generated SMV, transition rules or cache helpers.
Unsupported reference cases fail instead of claiming agreement.
"""
from collections import deque
from itertools import product
from pathlib import Path
import yaml
from yaml_to_model import WorkflowLoader
from . import expressions as ex


def explore_cache(paths, scenario, limit=300000):
    workflows = [yaml.load(Path(p).read_text(), Loader=WorkflowLoader) for p in paths]
    if any(len(w['jobs']) != 1 for w in workflows):
        return {'status': 'unsupported', 'reason': 'reference interpreter requires one job per run'}
    jobs = [next(iter(w['jobs'].values())) for w in workflows]
    steps = [j['steps'] for j in jobs]
    # pc (-1=pending, -2=success, -3=failure, -4=cancelled), failure,
    # file data state (0 absent, 1 clean, 2 tainted), staged taint,
    # per-step outcomes/cache-hit outputs, then cache list and history bits.
    initial = tuple((-1, False, 0, False, tuple('' for _ in ss), tuple('' for _ in ss)) for ss in steps)
    root = (initial, (), False, False, False, False)
    queue, seen = deque([root]), {root}
    parents = {root: None}
    reached = dict(transfer=False, use=False, sink_site=False, success=False, bad_authority=False)
    witness = None
    policy = scenario['cachePolicy']

    def condition(raw, state, ri, si):
        if raw is None:
            return not state[ri][1]
        node = ex.parse(raw)
        refs = {}
        for n in ex.walk(node):
            if n.kind != 'ref':
                continue
            ref = str(n.value)
            if ref.startswith('steps.'):
                parts = ref.split('.')
                index = next(k for k, s in enumerate(steps[ri]) if s.get('id') == parts[1])
                if parts[2:] == ['outputs', 'cache-hit']:
                    refs[ref] = state[ri][5][index]
                elif parts[2:] in (['outcome'], ['conclusion']):
                    refs[ref] = state[ri][4][index]
                else:
                    raise ValueError('reference expression outside subset')
            else:
                raise ValueError('reference expression outside subset')
        status = dict(success=not state[ri][1], failure=state[ri][1], cancelled=False)
        value = ex.truth(ex.evaluate(node, refs, status))
        explicit = any(n.kind == 'call' and n.value in {'always', 'failure', 'cancelled', 'success'} for n in ex.walk(node))
        return value and (explicit or not state[ri][1])

    while queue:
        state = queue.popleft()
        actors, entries, transfer, use, site, bad = state
        reached['transfer'] |= transfer
        reached['use'] |= use
        reached['sink_site'] |= site
        reached['bad_authority'] |= bad
        if bad and witness is None:
            witness = state
        for ri, actor in enumerate(actors):
            pc, failed, data, staged, outcomes, hits = actor
            cfg = scenario['runs'][ri]
            if pc < -1:
                continue
            after = cfg.get('after')
            if pc == -1:
                if after:
                    prior = next(i for i, c in enumerate(scenario['runs']) if c.get('id') == after)
                    if actors[prior][0] >= -1:
                        continue
                events = workflows[ri].get('on', {})
                if cfg['event'] not in (events if isinstance(events, (dict, list)) else [events]):
                    continue
                choices = [(0, failed, data, staged, outcomes, hits, entries, transfer, use, site, bad)]
            elif pc == len(steps[ri]):
                updated_entries = list(entries)
                if not failed:
                    for si, step in enumerate(steps[ri]):
                        if step.get('uses') != 'actions/cache@v4' or outcomes[si] != 'success' or hits[si] == 'true':
                            continue
                        inp = step['with']
                        mode = jobs[ri].get('cache-mode', workflows[ri].get('cache-mode', cfg.get('cacheMode')))
                        allowed = policy == 'github-historical-2025' or mode in {'write', 'write-only'} or (mode is None and cfg['event'] in {'push', 'pull_request', 'workflow_dispatch', 'schedule'})
                        entry = (ri, inp['key'], cfg.get('cacheVersion', {}).get(inp['path']), data)
                        if allowed and scenario.get('cacheIO', True) and data and not any(scenario['runs'][e[0]]['scope'] == cfg['scope'] and e[1:3] == entry[1:3] for e in updated_entries):
                            updated_entries.append(entry)
                choices = [(-3 if failed else -2, failed, data, staged, outcomes, hits, tuple(updated_entries), transfer, use, site, bad)]
            else:
                step = steps[ri][pc]
                choices = []
                if not condition(step.get('if'), actors, ri, pc):
                    oo = list(outcomes)
                    oo[pc] = 'skipped'
                    choices.append((pc + 1, failed, data, staged, tuple(oo), hits, entries, transfer, use, site, bad))
                else:
                    for ok, save_io in product([scenario['operationOutcome']] if 'operationOutcome' in scenario else (False, True),
                                               [scenario['cacheIO']] if 'cacheIO' in scenario else (False, True)):
                        dd, st, ee, tt, uu, ss, bb, hh = data, staged, list(entries), transfer, use, site, bad, list(hits)
                        uses = step.get('uses', '')
                        inp = step.get('with', {})
                        if uses == 'actions/checkout@v4' and ok:
                            dd = 2 if cfg.get('checkoutUntrusted', cfg['event'] == 'pull_request') else 1
                        elif uses in {'actions/cache/save@v4'} and ok:
                            mode = jobs[ri].get('cache-mode', workflows[ri].get('cache-mode', cfg.get('cacheMode')))
                            allowed = policy == 'github-historical-2025' or mode in {'write', 'write-only'} or (mode is None and cfg['event'] in {'push', 'pull_request', 'workflow_dispatch', 'schedule'})
                            entry = (ri, inp['key'], cfg.get('cacheVersion', {}).get(inp['path']), dd)
                            if allowed and save_io and dd and not any(scenario['runs'][e[0]]['scope'] == cfg['scope'] and e[1:3] == entry[1:3] for e in ee):
                                ee.append(entry)
                        elif uses in {'actions/cache/restore@v4', 'actions/cache@v4'} and ok:
                            candidates = []
                            mode = jobs[ri].get('cache-mode', workflows[ri].get('cache-mode', cfg.get('cacheMode')))
                            for age, entry in enumerate(entries):
                                owner, key, version, contents = entry
                                wc = scenario['runs'][owner]
                                allowed_scopes = {cfg['scope'], cfg['defaultBranch']}
                                if cfg['event'] in {'pull_request', 'pull_request_target'}:
                                    allowed_scopes.add(cfg['baseBranch'])
                                if wc['repository'] != cfg['repository'] or wc['scope'] not in allowed_scopes or version != cfg.get('cacheVersion', {}).get(inp['path']):
                                    continue
                                scope_rank = 0 if wc['scope'] == cfg['scope'] else 1 if wc['scope'] == cfg['baseBranch'] else 2
                                prefixes = [inp['key'], *str(inp.get('restore-keys', '')).splitlines()]
                                if key == inp['key']:
                                    rank = 0
                                elif key.startswith(inp['key']):
                                    rank = 1
                                else:
                                    rank = next((i + 2 for i, p in enumerate(prefixes[1:]) if key.startswith(p)), None)
                                if rank is not None:
                                    candidates.append(((scope_rank, rank, -age), entry))
                            if policy == 'github-current-2026-10-22' and mode in {'none', 'write-only'}:
                                candidates = []
                            if candidates:
                                rank, entry = min(candidates)
                                dd, tt = entry[3], True
                                hh[pc] = 'true' if rank[1] == 0 else 'false'
                            else:
                                hh[pc] = ''
                                if inp.get('fail-on-cache-miss') is True:
                                    ok = False
                        elif 'run' in step:
                            command = step['run'].strip()
                            if command == 'cat data' and ok:
                                uu |= dd == 2
                            elif command == 'git add data' and ok:
                                st |= dd == 2
                                uu |= dd == 2
                            elif command == 'git push':
                                ss = True
                                permission = jobs[ri].get('permissions', workflows[ri].get('permissions', {})).get('contents', 'none')
                                if cfg['event'] == 'pull_request' and cfg['fork'] and not cfg.get('sendWriteTokens'):
                                    permission = 'read'
                                if ok and permission == 'write' and cfg.get('tokenAuthenticated', {}).get('contents') is True:
                                    reached['success'] = True
                                    bb |= st
                            elif command == 'exit 1':
                                ok = False
                            elif command == "echo 'clean' > data" and ok:
                                dd = 1
                            elif command == "echo 'clean' > data":
                                pass
                            elif command not in {'cat data', 'git add data'}:
                                return {'status': 'unsupported', 'reason': 'reference shell: ' + command}
                        oo = list(outcomes)
                        oo[pc] = 'success' if ok else 'failure'
                        choices.append((pc + 1, failed or (not ok and not step.get('continue-on-error', False)), dd, st, tuple(oo), tuple(hh), tuple(ee), tt, uu, ss, bb))
                # Cancellation has no additional effects in this subset.
                if scenario.get('cancelRequests') is not False:
                    choices.append((-4, failed, data, staged, outcomes, hits, entries, transfer, use, site, bad))
            for npc, nf, nd, ns, no, nh, ne, nt, nu, nsite, nb in choices:
                updated = list(actors)
                updated[ri] = (npc, nf, nd, ns, no, nh)
                nxt = (tuple(updated), ne, nt, nu, nsite, nb)
                if nxt not in seen:
                    seen.add(nxt)
                    parents[nxt] = (state, ri)
                    queue.append(nxt)
                    if len(seen) > limit:
                        return {'status': 'incomplete', 'states': len(seen), 'reason': 'explicit state bound exceeded'}
    trace = []
    while witness is not None and parents[witness] is not None:
        prior, actor = parents[witness]
        trace.append({'run': actor, 'pc': witness[0][actor][0], 'cache': witness[1], 'bad': witness[-1]})
        witness = prior
    return {'status': 'complete', 'states': len(seen), 'reachable': reached, 'counterexample': trace[::-1],
            'scope': 'closed cache/checkout/read/stage/push contracts; excludes arbitrary Shell, matrix and multi-job'}
