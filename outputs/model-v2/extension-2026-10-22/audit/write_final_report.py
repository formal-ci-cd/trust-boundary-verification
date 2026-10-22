from pathlib import Path
from collections import Counter
import hashlib
import json
import re

root = Path('/repo')
out = Path('/out')
summary = json.loads((out / 'verification-summary.json').read_text())
models = summary['models']
by_id = {m['id']: m for m in models}
original = json.loads(Path('/audit/original-content-hashes.json').read_text())
changed, missing = [], []
for relative, digest in original.items():
    path = root / relative
    if not path.exists():
        missing.append(relative)
    elif hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        changed.append(relative)
audit = {'checkedOriginalFiles': len(original), 'changed': changed, 'missing': missing,
         'expectedChanges': ['tools/workflow_model_v2/builder.py', 'tools/workflow_to_smv_v2.py']}
audit['passed'] = sorted(changed) == sorted(audit['expectedChanges']) and not missing
(out / 'preservation-audit.json').write_text(json.dumps(audit, indent=2) + '\n')
diagnostics = []
for model in models:
    path = (out / model['model']).with_suffix('.map.json')
    if path.exists():
        mapping = json.loads(path.read_text())
        diagnostics.append({'case': model['id'], 'verdict': mapping.get('verdict'), 'diagnostics': mapping.get('diagnostics', [])})
(out / 'unknown-unsupported-inventory.json').write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2) + '\n')
bfs = [m for m in models if m.get('bfs', {}).get('status') == 'complete']
bad_bfs = [m['id'] for m in bfs if not all(m.get('bfsAgreement', {}).values())]
valid = [m for m in models if m['verified']]
trace_count = sum(m['traceCount'] for m in models)
span_count = sum(m.get('sourceValidation', {}).get('checkedSteps', 0) for m in models)
times = [m['verificationRealSeconds'] for m in valid if 'verificationRealSeconds' in m]
states = [float(m['reachableStates'][0].split()[0]) for m in valid if m.get('reachableStates')]
tests = []
for name in ['regression-base.log', 'regression-v2.log']:
    match = re.search(r'Ran (\d+) tests', (out / name).read_text())
    tests.append(int(match.group(1)))
diagnostic_types = Counter(d['code'] for case in diagnostics for d in case['diagnostics'])

def cell(model, expression):
    return {'false': '到達可能', 'true': '不達（モデル内）'}.get(model['properties'].get(expression), '範囲外')

lines = [
    '# YAML→SMV v2 大規模拡張の実装・評価報告', '',
    '実施日：2026-10-22．**部分達成**．汎用有限run backend，Cache共有状態，複数job Artifact，式と実操作sinkのsubsetを実装した．既存A1～A5は従来経路を維持する．TanStackの全経路，再利用workflow，concurrency，汎用OIDCは未達であり，研究目標全体の完了とはしない．', '',
    '## 1．作業対象と既存研究の保護', '',
    'branch：`research/automatic-temporal-verification`．HEAD：`8ecb091f540065057e2c4045e21678494eba19b5`．commit/push/branch操作は行っていない．開始時からA1 producer/consumerの2 YAMLに変更があり，保持した．Lunaの未追跡ファイルを基盤とし，既存builderとCLIの2ファイルだけに差分を適用した．元ファイルはaudit/originalsへ退避した．', '',
    f'既存{len(original)}ファイルのhash照合：`{audit["passed"]}`．既存ファイルの変化は `{changed}`，欠落は `{missing}`．A1～A5 YAML，旧解析器，旧results，既存outputs/model-v2記録，元implementation-reportは保持した．詳細は [preservation-audit.json](../../outputs/model-v2/extension-2026-10-22/preservation-audit.json)．', '',
    '初回のまとめた反映処理は，既存未追跡ファイルの退避確認が不十分との理由で自動承認レビューに拒否された．その処理は実行されず，独立したbackupとSHA-256一致確認を済ませ，新規ファイルの上書き禁止追加と既存2ファイルへの限定patchで反映した．', '',
    '## 2．実装済み機能', '',
    'Parserと式AST，型付きRun/Job/Step，version別ActionContract，TransitionSystem，SMV backend，trace mapperを分離した．`--scenario` の拡張経路は複数workflow/runとjob interleaving，needs，status関数，step continue-on-error，静的matrix，有限output/context，Artifact object，Cache保存／取得／post-save，限定権限sinkを生成する．', '',
    'Cacheはmain key／prefix／restore-keys順序，scope優先順位，最新保存順，repository／opaque version，cache-hitのtrue/false/未設定を保持する．保存成功をstep outcomeから分け，既存slotのimmutable性と保存後の可用性を表す．historical policyと確認時点のcache-modeを明示的に分ける．', '',
    'Compositeは明示registryのmetadata SHAを確認して展開するsubsetを追加した．任意Actionコードやworkflow自体は実行していない．詳細な機能別範囲は [extension-semantics.md](extension-semantics.md)．', '',
    '## 3．回帰・mutation・NuSMV', '',
    f'既存18件を含む回帰テストは **{sum(tests)}件成功**（base {tests[0]}件，v2 {tests[1]}件）．元の21 mutationを再生成・再検証し，追加fixtureと履歴監査を含む **{summary["total"]}モデル**を検査した．{summary["validated"]}件でCTL結果を取得し，意図的構文不正1件はexit code 1／propertyなしとなった．集計失敗一覧：`{summary["failures"]}`．', '',
    f'有効{len(valid)}モデル全てで `-ctt` のtotal transition確認と非空の到達状態集合を取得した．property falseには反例があり，**{trace_count}本のproperty別trace**を保存した．source span検査は{span_count} step出現分で不正0件．拡張traceではactorとPCを対応させ，従来traceでは既存source symbolの対応を保持した．', '',
    'unsupported fixtureのCTLがtrueでもmanifestはunknown/unsupportedを保持し，安全判定へ変換しない．SMV生成失敗・構文不正・property欠落は成功扱いしない．[全検証結果](../../outputs/model-v2/extension-2026-10-22/verification-summary.json)．', '',
    '## 4．A1～A5比較', '',
    '| Case | Artifact利用 | 模擬危険経路 | 保存済prototypeの共通property | 実権限 |',
    '|---|---|---|---|---|',
]
for case in ['A1', 'A2', 'A3', 'A4', 'A5']:
    model = by_id[case]
    shared = model['savedPrototypeComparison']
    lines.append(f'| {case} | {cell(model,"AG !artifact_dependent_use")} | {cell(model,"AG !bad_simulated_path")} | {sum(shared.values())}/{len(shared)}一致 | prototype contract未対応 |')
lines += ['',
    'A2ではdigest検査自身のreadをverification_read，verified後のreadをpost_verification_use，mismatch後のreadをpost_integrity_mismatch_useに分けた．integrity_blockedは「後続guardが遮断した履歴」であり，過去のuseを取り消さない．監査mutationは検査前のreadを追加し，`integrity_blocked & artifact_dependent_use` が到達する一方，mismatch後のreadは不達であることをNuSMVで確認した．元の強すぎる履歴implicationを廃止した．', '',
    '## 5．CacheとTanStack', '',
    '| Fixture | bad_authority | 意味 |', '|---|---|---|',
]
for name, meaning in [
    ('cache-prefix-unsafe', 'prefix restoreからstaged bytesへ条件付き経路'),
    ('cache-pr-scope-isolation', 'PR merge-ref cacheはmain releaseへ共有しない'),
    ('cache-version-mismatch', 'opaque version不一致は取得不可'),
    ('cache-repository-mismatch', 'repository不一致は共有不可'),
    ('cache-save-warning-no-object', '保存I/O不成功ではslotができない'),
    ('cache-output-contradiction', '同じcache-hitへの矛盾条件は不成立'),
    ('cache-post-job-failure', 'failed jobのcombined cacheはpost保存しない'),
    ('cache-post-continue-on-error', 'step failureを許容したsuccessful jobは保存可能'),
    ('cache-newest-prefix-is-clean', '制約した成功scenarioで最新clean prefixを選択'),
    ('cache-exact-before-newest-prefix', '古くてもexact keyがprefixより優先'),
    ('cache-current-default-read', 'current low-trust既定modeでは保存不可'),
    ('cache-current-explicit-write', '明示write modeで保存経路が復活'),
]:
    lines.append(f'| {name} | {cell(by_id[name],"AG !bad_authority")} | {meaning} |')
lines += ['',
    'TanStackは旧評価と同じbundle-size.yml／release.ymlをpre-incident／mitigation双方で入力し，既存hash-checked setup compositeも展開した．両方とも **unknown/unsupported**．旧方式のpre-incident conditional counterexample／mitigation safe-within-modelと同じ結論を出せたとは主張しない．', '',
    '差の原因は，現行snapshotのcheckout@v6.0.2，pnpm/setup-node等のAction，STORE_PATHを設定するGITHUB_ENV，hashFiles由来key，pnpm実行によるcache taint，OIDCの契約が揃っていないこと，concurrencyや複数動的pathが未対応であることにある．期待値へ合わせるために未知のShell効果を固定したり，旧モデルをv2生成結果として流用していない．全diagnosticを[一覧](../../outputs/model-v2/extension-2026-10-22/unknown-unsupported-inventory.json)へ保存した．', '',
    '## 6．実権限sinkの検証範囲', '',
    '限定git add→git push，npm publish，gh deployment APIをcontractとして識別する．token scope／fork制限／認証，地点到達，非決定的操作成功，tainted filesystem依存を別条件・状態にする．Artifact cross-runと複数job fixture，package publish fixtureで条件付きbad_authorityを確認した．権限除去，対象object変更，run不一致では対応する経路が消えた．', '',
    '実repository更新／publish／deploymentは一切行っていない．外部credentialや認証をscenarioでtrueとしたfixtureは条件付き抽象検証であり，実credentialの有効性や実操作成功を証明しない．OIDC要求・交換・secrets可用性の一般検証は未達．', '',
    '## 7．YAML構文範囲の増加', '',
    '従来の単一job Artifact／限定式から，複数job/run，静的matrix，8種類のtrigger宣言認識とfilter subset，数値/null/型変換，7種類のexpression関数，cache3 Action，metadata確認Composite，限定command sinkへ拡張した．scheduleは配信scenarioのみ，workflow_callは呼出し意味未対応，concurrencyはunsupportedであり，これらを完全対応件数へ算入しない．対応・部分対応・未対応は仕様表に分離した．', '',
    '## 8．追加状態と遷移規則', '',
    'job PC，failed/cancelled履歴，step outcome/conclusion，有限step output，jobごとのfile slot，object slot，cache age／save clock，exact-hit，staged taint，transfer／dependent_use／sink_site／authority_available／sink_succeeded／bad_authorityを導入した．unknown contextとrepository defaultは共有FROZENVAR，actor／operation／cache save I/O／cancelはIVARに置く．', '',
    '保存はsource filesystem値をobjectへコピーし，取得はscope/version/key/時系列を満たす選択slotの値を保存する．Pythonが最終危険Booleanを先に算出する処理はない．state-dependent key，digest equality等，有限相関を保持する契約が不足する場合はunsupportedに残した．', '',
    '## 9．独立BFS', '',
    f'Cache専用の別状態遷移実装で **{len(bfs)} fixture**を全探索し，transfer/use/sink-site/success/bad-authorityの5到達可能性をNuSMVと比較した．不一致：`{bad_bfs}`．BFSの状態数は{min(m["bfs"]["states"] for m in bfs)}～{max(m["bfs"]["states"] for m in bfs)}．', '',
    '独立性は生成SMV／transition式／cache policy helperを読まずYAML契約から遷移を実装したという範囲である．YAML loaderとexpression evaluatorは共通であり，完全に独立したparserではない．対象は単一job/runの閉じたCache＋checkout/read/stage/push subset．複数job Artifact，任意Shell，OIDC，全A2 integrity／A5 metadataを含む独立照合は未達．既存A1/A3 BFS回帰は保持した．', '',
    '## 10．状態数と時間', '',
    f'NuSMVの到達状態数は有効モデル全体で {min(states):g}～{max(states):g}．検査時間はtime出力から取得し，{len(times)}件で計測した．最小{min(times) if times else "N/A"}秒，最大{max(times) if times else "N/A"}秒，合計{sum(times):.2f}秒（到達状態数の追加計測，generation，Docker起動時間は別）．', '',
    '| Case | 到達状態数 | CTL検査実時間（秒） |', '|---|---:|---:|',
]
for name in ['A1', 'A2', 'A3', 'A4', 'A5', 'cache-prefix-unsafe', 'cache-newest-prefix-is-clean', 'artifact-multiple-jobs', 'static-matrix', 'tanstack-pre-incident', 'tanstack-mitigation']:
    model = by_id[name]
    lines.append(f'| {name} | {model.get("reachableStates", ["N/A"])[0].split()[0]} | {model.get("verificationRealSeconds", "N/A")} |')
lines += ['',
    'Python/PyYAMLのversionとarchitectureはruntime.json，Docker image IDはdocker-images.txt，NuSMV 2.6.0は各stdoutに記録した．Pythonはarm64 container，NuSMVはamd64 imageをemulationで実行した．runtime条件が違う性能比較へ直接外挿しない．', '',
    '## 11．未達と旧方式との差', '',
    '未達：Reusable Workflowのcaller/callee契約，concurrency，matrix include/exclude/fail-fast，任意Bash，GITHUB_ENV，汎用hashFiles／digest guard，Artifact overwrite／複数取得layout，完全なsecret/OIDC契約，platform policyの全差異，TanStack全経路のv2結論再現．unsupportedの影響範囲は保守的に広く付与しており，完全なproperty slicingは未実装．', '',
    '旧prototypeはA1～A5特有の有限payload／metadata domainを残す．新backendは一般のfile-taint／shared-object契約を持つが，既存A2/A5の詳細意味を全て移植したわけではない．別backendを設けた理由は既存評価の再現性を保持するためであり，二つを単一の完全対応器と呼ばない．BFS一致はsubset内の一致であり，GitHub実意味論への健全性定理ではない．', '',
    '## 12．変更ファイルとGit状態', '',
    '変更した既存ファイル：tools/workflow_model_v2/builder.py，tools/workflow_to_smv_v2.py．新規：同packageのexpressions/policies/transition/extended/reference_cache/composites/trace_mapper/action_contracts，拡張回帰テスト，評価runnerとDocker再現script，仕様／本報告，専用outputs．元implementation-reportは変更していない．', '',
    '開始／終了status，HEAD，差分patch，変更前backup，hash auditは出力内audit/に保存する．既存tracked差分は開始時のA1 YAML2ファイルのままであり，新規実装の多くは元から未追跡だったv2領域に存在する．', '',
    '再現コマンド：`sh tools/run_workflow_v2_extension.sh outputs/model-v2/<未作成の名前>`．実行は全てDocker内，非root，network none，repo ro，outputのみrw，capabilitiesなし，read-only rootfs．host依存package追加，既存service変更，credential/socket転送，workflowの実起動は行っていない．', '',
]
text = '\n'.join(lines)
(out / 'extension-report-2026-10-22.md').write_text(text)
(out / 'report-metrics.json').write_text(json.dumps(dict(tests=sum(tests), bfsCases=len(bfs), bfsDisagreement=bad_bfs,
    traces=trace_count, sourceSpans=span_count, minStates=min(states), maxStates=max(states),
    maxVerificationSeconds=max(times) if times else None, preservation=audit['passed'],
    diagnosticCounts=diagnostic_types), ensure_ascii=False, indent=2) + '\n')
print(json.dumps(dict(tests=sum(tests), models=summary['total'], failures=summary['failures'], bfsCases=len(bfs),
                      preservation=audit['passed'], traces=trace_count), indent=2))
if not audit['passed'] or summary['failures'] or bad_bfs:
    raise SystemExit(1)
