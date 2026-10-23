# Cacheの限定subsetをYAMLから直接解釈する，独立した参照用の幅優先探索．
# 生成SMVや変換器のCache helperを再利用せず，同じ誤りを共有する可能性を減らす．
# 未対応の入力や探索上限への到達を，照合成功として扱わない．
"""Cacheの閉じた操作subsetを，独立の明示状態探索で照合する．

入力はworkflowのPath列とscenario，上限であり，出力は到達履歴・状態数・条件付き反例である．
YAMLを直接読み，runごとの実行位置と保存entryをtupleにして幅優先探索（BFS）を行う．
生成SMV，TransitionSystemの更新式，compilerのCache helperを読まずに参照計算する．
1 job/runと限定command等を前提とし，非対応はunsupported，上限超過はincompleteで返す．
evaluate_workflow_v2_extensionが対応ケースだけをNuSMVのpropertyと照合する．
"""
from collections import deque
from itertools import product
from pathlib import Path
import yaml
from .workflow_loader import WorkflowLoader
from . import expressions as ex

# 有限stateを待ち行列で探索し，保存・復元・利用・権限操作の到達履歴を求める．
def explore_cache(paths, scenario, limit=300000):
    """独立の状態tupleを用い，Cacheの限定操作の到達性を探索する．"""
    # 生成SMVを使わずYAMLを直接読み，Cache契約の独立した参照計算を始める．
    workflows = [yaml.load(Path(p).read_text(), Loader=WorkflowLoader) for p in paths]
    # この参照実装は各runが1 Jobの入力に限定し，複数Jobはunsupportedとして返す．
    if any((len(w['jobs']) != 1 for w in workflows)):
        return {'status': 'unsupported', 'reason': 'reference interpreter requires one job per run'}
    # 各workflowで唯一のJobを取り出し，run番号でアクセスできる順序に並べる．
    jobs = [next(iter(w['jobs'].values())) for w in workflows]
    # 各Jobのstep列を保持し，PCから現在の操作を選べるようにする．
    steps = [j['steps'] for j in jobs]
    # run状態tupleの順番：PC（-1待機，-2成功，-3失敗，-4取消），失敗履歴，
    # file状態（0不在，1 clean，2 tainted），stage済みtaint，
    # stepの成否列，cache-hit列．
    # 全体状態には保存entry列と利用等の履歴を加える．
    initial = tuple(((-1, False, 0, False, tuple(('' for _ in ss)), tuple(('' for _ in ss))) for ss in steps))
    # runごとの局所状態，保存entry列，転送・利用・sink・危険履歴を全体状態tupleへまとめる．
    root = (initial, (), False, False, False, False)
    # dequeは先頭からの取出し，setは訪問済み判定，parentsは反例の逆追跡に使う．
    queue, seen = (deque([root]), {root})
    parents = {root: None}
    # 到達済みの履歴をすべてfalseから始め，探索中にORで累積する．
    reached = dict(transfer=False, use=False, sink_site=False, success=False, bad_authority=False)
    # 最初に危険履歴へ到達した状態を，後で反例を逆追跡するために保存する．
    witness = None
    policy = scenario['cachePolicy']

    def condition(raw, state, ri, si):
        """参照探索の具体stateを使いstepのifを評価する．"""
        # if省略時はこのrunに失敗履歴がない場合だけ実行する．
        if raw is None:
            return not state[ri][1]
        node = ex.parse(raw)
        # 今回の具体stateから式が参照する値を取り出し，式評価へ渡す辞書を作る．
        refs = {}
        for n in ex.walk(node):
            if n.kind != 'ref':
                continue
            ref = str(n.value)
            if ref.startswith('steps.'):
                parts = ref.split('.')
                # steps.id参照に一致するstep番号を探し，outcome/cache-hitのtupleから値を読む．
                index = next((k for k, s in enumerate(steps[ri]) if s.get('id') == parts[1]))
                if parts[2:] == ['outputs', 'cache-hit']:
                    # run状態の6番目にあるcache-hit列から，参照stepの値を取り出す．
                    refs[ref] = state[ri][5][index]
                elif parts[2:] in (['outcome'], ['conclusion']):
                    # run状態の5番目にあるstep結果列から，参照stepの成否を取り出す．
                    refs[ref] = state[ri][4][index]
                else:
                    raise ValueError('reference expression outside subset')
            else:
                raise ValueError('reference expression outside subset')
        status = dict(success=not state[ri][1], failure=state[ri][1], cancelled=False)
        value = ex.truth(ex.evaluate(node, refs, status))
        # status関数の明示があるかを調べ，通常のifには暗黙success条件を追加する．
        explicit = any((n.kind == 'call' and n.value in {'always', 'failure', 'cancelled', 'success'} for n in ex.walk(node)))
        return value and (explicit or not state[ri][1])
    # 全体状態からrunを1つ選び，その局所状態だけを更新した後継を列挙する．
    while queue:
        # 待ち行列へ先に追加された状態から取り出し，幅優先で後継状態を列挙する．
        state = queue.popleft()
        # 全体tupleをrun状態・保存物・各履歴へ分け，現在状態から可能な更新を計算する．
        actors, entries, transfer, use, site, bad = state
        reached['transfer'] |= transfer
        reached['use'] |= use
        reached['sink_site'] |= site
        reached['bad_authority'] |= bad
        # 最初の危険到達状態だけを保存し，最短の探索経路を親表から復元する．
        if bad and witness is None:
            witness = state
        # どのrunを進めるかを列挙し，各後継状態では選ばれたrunの局所状態だけを変更する．
        for ri, actor in enumerate(actors):
            # 局所tupleの6要素を取り出し，実行位置・失敗・file・stage・結果・hitを区別する．
            pc, failed, data, staged, outcomes, hits = actor
            cfg = scenario['runs'][ri]
            # 成功・失敗・取消の終端runは，この状態からさらにstepを実行しない．
            if pc < -1:
                continue
            after = cfg.get('after')
            if pc == -1:
                if after:
                    prior = next((i for i, c in enumerate(scenario['runs']) if c.get('id') == after))
                    # 上流runがまだ待機中または実行中なら，after指定のあるrunを開始しない．
                    if actors[prior][0] >= -1:
                        continue
                events = workflows[ri].get('on', {})
                if cfg['event'] not in (events if isinstance(events, (dict, list)) else [events]):
                    continue
                choices = [(0, failed, data, staged, outcomes, hits, entries, transfer, use, site, bad)]
            elif pc == len(steps[ri]):
                # immutableな保存entry列を作業用リストへ複製し，元の探索状態を変更しない．
                updated_entries = list(entries)
                if not failed:
                    for si, step in enumerate(steps[ri]):
                        if step.get('uses') != 'actions/cache@v4' or outcomes[si] != 'success' or hits[si] == 'true':
                            continue
                        inp = step['with']
                        mode = jobs[ri].get(
                            'cache-mode',
                            workflows[ri].get('cache-mode', cfg.get('cacheMode'))
                        )
                        allowed = policy == 'github-historical-2025' or mode in {'write', 'write-only'} or (mode is None and cfg['event'] in {'push', 'pull_request', 'workflow_dispatch', 'schedule'})
                        # 保存元run・key・opaque version・抽象内容状態を組にし，独立の保存物表現を作る．
                        entry = (ri, inp['key'], cfg.get('cacheVersion', {}).get(inp['path']), data)
                        if allowed and scenario.get('cacheIO', True) and data and (not any((scenario['runs'][e[0]]['scope'] == cfg['scope'] and e[1:3] == entry[1:3] for e in updated_entries))):
                            updated_entries.append(entry)
                choices = [(
                    -3 if failed else -2,
                    failed,
                    data,
                    staged,
                    outcomes,
                    hits,
                    tuple(updated_entries),
                    transfer,
                    use,
                    site,
                    bad
                )]
            else:
                step = steps[ri][pc]
                # 現在のstepに対するskip・成否・取消等の後継候補をためる．
                choices = []
                if not condition(step.get('if'), actors, ri, pc):
                    oo = list(outcomes)
                    oo[pc] = 'skipped'
                    choices.append((
                        pc + 1,
                        failed,
                        data,
                        staged,
                        tuple(oo),
                        hits,
                        entries,
                        transfer,
                        use,
                        site,
                        bad
                    ))
                else:
                    # step成否とCache I/O成否を独立に列挙し，scenarioで固定されている次元は候補1つにする．
                    for ok, save_io in product(
                        [scenario['operationOutcome']] if 'operationOutcome' in scenario else (False, True),
                        [scenario['cacheIO']] if 'cacheIO' in scenario else (False, True)
                    ):
                        # 後継作業用の値を複製し，entries/hitsのリスト更新が元stateへ及ばないようにする．
                        dd, st, ee, tt, uu, ss, bb, hh = (
                            data,
                            staged,
                            list(entries),
                            transfer,
                            use,
                            site,
                            bad,
                            list(hits)
                        )
                        uses = step.get('uses', '')
                        inp = step.get('with', {})
                        if uses == 'actions/checkout@v4' and ok:
                            dd = 2 if cfg.get('checkoutUntrusted', cfg['event'] == 'pull_request') else 1
                        elif uses in {'actions/cache/save@v4'} and ok:
                            mode = jobs[ri].get(
                                'cache-mode',
                                workflows[ri].get('cache-mode', cfg.get('cacheMode'))
                            )
                            allowed = policy == 'github-historical-2025' or mode in {'write', 'write-only'} or (mode is None and cfg['event'] in {'push', 'pull_request', 'workflow_dispatch', 'schedule'})
                            # 保存元run・key・opaque version・抽象内容状態を組にし，独立の保存物表現を作る．
                            entry = (ri, inp['key'], cfg.get('cacheVersion', {}).get(inp['path']), dd)
                            if allowed and save_io and dd and (not any((scenario['runs'][e[0]]['scope'] == cfg['scope'] and e[1:3] == entry[1:3] for e in ee))):
                                ee.append(entry)
                        elif uses in {'actions/cache/restore@v4', 'actions/cache@v4'} and ok:
                            candidates = []
                            mode = jobs[ri].get(
                                'cache-mode',
                                workflows[ri].get('cache-mode', cfg.get('cacheMode'))
                            )
                            for age, entry in enumerate(entries):
                                # 保存entryを4要素へ分け，保存元scope・名前・version・抽象内容を別々に照合する．
                                owner, key, version, contents = entry
                                wc = scenario['runs'][owner]
                                # 同一scopeと既定branchのscopeを取得候補とし，PR系なら次の分岐でbaseも加える．
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
                                    rank = next(
                                        (i + 2 for i, p in enumerate(prefixes[1:]) if key.startswith(p)),
                                        None
                                    )
                                if rank is not None:
                                    # scope→key→新しさの順で比較できるtupleを付け，ageには負値を使って新しい方を小さくする．
                                    candidates.append(((scope_rank, rank, -age), entry))
                            if policy == 'github-current-2026-10-22' and mode in {'none', 'write-only'}:
                                candidates = []
                            if candidates:
                                # tupleは先頭の要素から比較される．
                                # scope，key順位，負のageの順で最優先候補を取る．
                                rank, entry = min(candidates)
                                # 選ばれたCacheの内容状態をworkspaceへ写し，転送履歴をtrueへする．
                                dd, tt = (entry[3], True)
                                # exact keyならtrue，prefixならfalseという文字列をcache-hitへ記録する．
                                hh[pc] = 'true' if rank[1] == 0 else 'false'
                            else:
                                # Cache missを空文字で表し，prefix hitの文字列falseとは区別する．
                                hh[pc] = ''
                                if inp.get('fail-on-cache-miss') is True:
                                    ok = False
                        elif 'run' in step:
                            command = step['run'].strip()
                            if command == 'cat data' and ok:
                                # 汚染内容を読んだ履歴をORで累積し，後のclean書込みでも過去の利用を消さない．
                                uu |= dd == 2
                            elif command == 'git add data' and ok:
                                # 汚染内容がstageされた履歴を保持し，git push時の依存判定に使う．
                                st |= dd == 2
                                # 汚染内容を読んだ履歴をORで累積し，後のclean書込みでも過去の利用を消さない．
                                uu |= dd == 2
                            elif command == 'git push':
                                ss = True
                                permission = jobs[ri].get('permissions', workflows[ri].get('permissions', {})).get('contents', 'none')
                                if cfg['event'] == 'pull_request' and cfg['fork'] and (not cfg.get('sendWriteTokens')):
                                    permission = 'read'
                                if ok and permission == 'write' and (cfg.get( 'tokenAuthenticated', {} ).get('contents') is True):
                                    reached['success'] = True
                                    # write権限・認証・成功がそろったpushで，汚染stageに依存する危険履歴を残す．
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
                        # 成否の今回候補をstep結果列へ記録し，後続stepのoutcome参照へ渡す．
                        oo[pc] = 'success' if ok else 'failure'
                        choices.append((
                            pc + 1,
                            failed or (not ok and (not step.get('continue-on-error', False))),
                            dd,
                            st,
                            tuple(oo),
                            tuple(hh),
                            tuple(ee),
                            tt,
                            uu,
                            ss,
                            bb
                        ))
                # この参照subsetの取消はrun終端への移行だけで，追加cleanup効果は扱わない．
                if scenario.get('cancelRequests') is not False:
                    choices.append((
                        -4,
                        failed,
                        data,
                        staged,
                        outcomes,
                        hits,
                        entries,
                        transfer,
                        use,
                        site,
                        bad
                    ))
            for npc, nf, nd, ns, no, nh, ne, nt, nu, nsite, nb in choices:
                # run状態列をコピーし，今回選んだrunだけを新しい局所状態へ差し替える．
                updated = list(actors)
                # PC・失敗・file・stage・step結果・hitをまとめて，選択runの次状態を作る．
                updated[ri] = (npc, nf, nd, ns, no, nh)
                # 保存物と履歴も含め，setのキーに使えるimmutableな全体状態へ戻す．
                nxt = (tuple(updated), ne, nt, nu, nsite, nb)
                # 初めて現れた状態だけを探索対象へ加え，同じ状態の循環で探索が終わらなくなるのを防ぐ．
                if nxt not in seen:
                    seen.add(nxt)
                    # 発見元の状態と進めたrunを保存し，危険状態からの逆追跡を可能にする．
                    parents[nxt] = (state, ri)
                    # 新しい状態を末尾へ追加し，現在の深さの状態を先に処理する．
                    queue.append(nxt)
                    # 探索を打ち切った場合はincompleteで返す．
                    # 未探索の危険経路がないと結論しない．
                    if len(seen) > limit:
                        return {
                            'status': 'incomplete',
                            'states': len(seen),
                            'reason': 'explicit state bound exceeded'
                        }
    trace = []
    # 親状態をさかのぼって反例を作り，最後の[::-1]で実行順に戻す．
    while witness is not None and parents[witness] is not None:
        # 反例状態の1つ前と，その遷移で進んだrunを親表から取り出す．
        prior, actor = parents[witness]
        trace.append({'run': actor, 'pc': witness[0][actor][0], 'cache': witness[1], 'bad': witness[-1]})
        # 初期状態へ向かって1段戻り，最後に逆順のtraceを実行順へ並べ直す．
        witness = prior
    return {
        'status': 'complete',
        'states': len(seen),
        'reachable': reached,
        'counterexample': trace[::-1],
        'scope': 'closed cache/checkout/read/stage/push contracts; excludes arbitrary Shell, matrix and multi-job'
    }
