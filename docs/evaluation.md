# v2の検証結果と未対応項目

現行の対応範囲は[仕様](semantics.md)，再実行は[コンテナ操作](container-guide.md)を参照してください．過去の計画書や実装途中の報告は保存タグ `archive/v2-before-repository-pruning` に残しています．

## 保存済み評価で確認した結果（2026-10-23）

- Docker内の回帰テスト：111件成功．Artifactデモの到達・不達2件と，YAML単独入力の自動仮定・存在分岐・ログ・CLI・未対応保持・明示入力とsnapshot優先の12件を含む．出力保持・復元・破損検出と，統合ログの正常・異常・時間切れ・旧形式互換の検査を含む．
- 全評価：101モデル．100件でNuSMVのCTL結果・遷移全域性・非空の到達状態集合を取得．意図した構文不正の対照1件を区別する．
- 独立BFS：22ケースについてtransfer/use/sink/success/bad-authorityの5項目を照合し，全件一致．
- 全モデルを同じbackendで生成する．A1～A5の保存済み50項目は一致．旧21 mutationも固定した期待値と照合し，不正metadata時の後続stepだけは下記の修正を適用する．unsupportedをCTLのtrueだけで安全と扱わない．

統合ログへの変更後も，全101モデルの生成・検証・集計，独立BFS22ケースの一致を再確認した．新規の検証記録は101ログで，旧形式の個別ログと反例ごとの重複ファイルは生成されなかった．

この件数は保存済み評価の結果であり，文書を更新するたびに全101モデルを再検証したことを示すものではありません．対応する出力を削除した場合，過去の検証ログそのものは残りません．

全評価の生成・検証・集計を実行すると，指定した出力先の `verification-summary.json` に判定と照合結果を保存します．各モデルの`verificationLog`は統合ログの相対パス，`counterexamples`はpropertyと元YAMLへの対応です．生の反例traceはモデルの`.smv.log`に残し，`.cex.*.trace.txt`や`.cex.*.source-map.json`へ重複出力しません．旧形式を集計する場合は`verificationLog`がnullになります．`outputs/` はGit管理外です．回帰比較に必要なA1～A5の期待値は `tests/fixtures/prototype-properties.json` にあります．

## Artifactの評価

| ケース | 成果物に依存する利用 | 模擬危険経路 |
|---|---|---|
| A1 | 到達可能 | 到達可能 |
| A2 | 到達可能 | 不達（モデル内） |
| A3 | 不達（モデル内） | 不達（モデル内） |
| A4 | 到達可能 | 不達（モデル内） |
| A5 | 到達可能 | 到達可能 |

保存された集計ではA1～A4にunsupportedが残っています．表は各propertyのモデル内到達性であり，case全体の安全判定ではありません．

A2では検証前のreadを追加した履歴監査も行い，後の遮断が過去の利用を消さないことを確認しています．この表は模擬地点の到達性であり，実GitHub上の公開やrepository更新の成功を示しません．

## Cacheと実事例

Cacheのprefix一致，scope隔離，version・repository不一致，書込み無効化，権限欠落，ステージング欠落，post-save，状態関数，有限context・output相関などを対照構成で確認しています．

TanStackは事件前と対策版のworkflow，明示的にhashを固定したComposite metadataを入力として保持しています．ただし原本にあるpnpmや外部Actionの意味を完全には扱えず，現行v2では全経路の再現に達していません．旧変換器の結果をv2の結果として流用しません．

## 残っている制約

GITHUB_ENVの単純な1行書込み，output経由の有限値伝播，literal patternと有限内容に限定したhashFilesは対応しています．一方，Reusable workflowのcaller側意味論，concurrency，任意Bash，環境ファイルの複数行delimiter，完全な実行時在庫・内容がない汎用hashFiles，OIDC・secret伝播，動的matrix，Artifact overwriteや複数download layoutなどは未対応です．詳細な境界は[対応範囲表](semantics.md#対応範囲表)を参照してください．

旧評価の論文用68構成，CodeQL等との比較，個別事件の旧解析は保存版の研究成果です．現行の101モデルとは別の評価系です．

## backend統合の検査

旧Artifact builderを削除し，CLI・Artifact・Cache・対照構成を共通schedulerとオブジェクト状態へ移した．旧21 mutationはSMV変数への文字列注入をやめ，`stepOutcomes`，`checkoutValues`，`workflowTrusted` を入力条件として指定する．

追加の6ケースで，Shell変数の改名，guardを外した不一致後利用，別ファイルへの検証状態の漏れ，検証後の書換え，consumerを先に宣言した場合，同一step内の検証後利用を確認する．

旧版との差は `a5-invalid-metadata` の `AG !simulated_sink_site_reached` で，falseからtrueになる．文字種・長さの検査失敗でShell stepが失敗し，後続の既定success条件のstepが実行されなくなるためである．危険経路の到達判定が変わったものではなく，旧版で残っていた模擬地点への到達を抑える修正である．比較用fixtureに変更理由と変更前後の期待値を記録している．

## 2026-10-23の拡張評価

既存71モデルに，`tests/fixtures/dataflow-cases.json`の30対照を追加した．全101モデルについて，正常100件と意図した構文不正1件を区別し，propertyの期待値，反例，source map，非空の到達状態集合を照合した．A1～A5の50項目，旧21対照，移植検査6ケース，独立BFS22ケースも一致した．テスト件数に含まれる2件は検証wrapperの時間切れ処理で，実NuSMVの時間切れ発生を成果として数えるものではない．

追加対照は，環境変数の反映時点・優先順位・job分離・後勝ち・引用，予約変数・複数行の未対応，step/job output依存Cache key，単一の動的path，post-saveのkey捕捉，既知・未知・書換え後のhash，Artifact由来とtaintの区別，データ依存と制御依存，静的matrixのinclude/exclude，dispatch input，job失敗後のoutputを含む．各具体値のprobeには到達可能性のprobeも付け，実行されないstepの含意だけを成功に数えない．

各評価先へ`verification-summary.json`，`report-metrics.json`，`unknown-unsupported-inventory.json`を保存する．modelごとの生成時間，CTL検査時間，状態数，反例数，診断，scenario仮定を記録する．CTLがtrueでもunsupportedが残るモデルは`unknown/unsupported`のままとする．

### TanStack

事件前・対策版ともにComposite metadataをhash照合して展開し，共通backendでモデルを生成・検査した．今回の入力条件では両者とも約254万の到達状態があるが，どちらも`unknown/unsupported`である．事件前のconditional counterexampleと対策版のsafe-within-modelは再現できていない．

不足は具体的に次のとおりである．

- checkout v6，setup-node，pnpm/action-setup等の外部Action契約．
- `pnpm store path`によるSTORE_PATHの導出，installやbuildによるfile生成・利用．
- lockfileの完全な実行時在庫と内容，Cache復元byte列に対するhashの関係．
- concurrencyのrun間順序と取消．
- OIDC要求，credential exchange，クラウド側の信頼条件とpublish契約．

`GITHUB_ENV`という構文を追加しただけでは，`$(pnpm store path --silent)`の意味は得られない．今回の評価ではpnpmを実行せず，内容やcredentialを仮定で成功させる置換もしていない．モデル内の不達をmitigationの証明へ読み替えない．

### 未完了の拡張と再開位置

大規模拡張の全項目は完了していない．次の項目は引き続き未対応である．既存の診断を維持しており，対応済み件数には含めない．

| 項目 | 残っている作業 |
|---|---|
| Composite | input/default/output，内部step scope，親guardを保持するcall frameと対照 |
| Reusable Workflow | caller/calleeの実行順，入力，secret，権限の上限制約，output |
| OIDC・secret | 可用性，要求，外部認証，権限付与，publishを分けた契約と外部assumption |
| 実行制御 | concurrency，cancel-in-progress，動的matrix，fail-fast，max-parallel，job-level continue-on-error |
| Artifact | overwrite，再生成後のID，複数download，pattern/merge-multipleのlayout |
| Cache | 複数pathとmemberごとの内容，eviction，完全なversion導出 |
| provenance | Booleanラベルから全source集合と依存edgeを追えるgraphへの拡張 |
| Shell/Action | 任意Bashではなく，pnpm等の必要操作を出典付きの共通操作契約へ追加 |

再開時は`compiler.py`と`dataflow.py`を基準にし，機能別backendを増やさない．新しい契約には正常・遮断・未対応の対照を追加し，既存の期待値を目的の結論へ合わせて変更しない．

## YAMLだけを入力する場合の自動仮定

`examples/`の到達版とsafe版を`--scenario`なしで生成し，両方で`AG !transfer`がfalse，`AG !dependent_use`と`AG !artifact_dependent_use`が到達版ではfalse，safe版ではtrueになることを実NuSMVで確認する．自動で補ったfileの存在はFROZENVARの両側を持ち，非存在なら転送しないこともCTLで照合する．実ファイル内容を観測したり，任意の入力値の安全を証明したりする評価ではない．

Consumerを先に入力する場合の名前接続，単純catのmember復元，曖昧な上流・未対応path・Shellの診断維持，明示scenarioの互換性，ログ内の仮定とSMVのhash照合を回帰テストへ含める．

変更後に全101モデルを再生成・検証・集計し，100件の正常検証と1件の意図的不正構文対照，property期待値，反例数，独立BFS22ケースの一致を維持した．既存SMVは100件で一致し，`legacy-workflow-run-mismatch`だけは名前が一致しない上流へ自動接続しなくなったため本文が変わるが，property結果は変わらない．全ログで対応表と最終SMVのhashが一致することも確認した．

## 外部条件探索の追加検証（2026-10-23）

既存88件に23件を追加し，条件IR，event/fork/branch/runnerの実NuSMV到達性，後続workflowの上流完了待機，CLIと部分JSONの制限・矛盾，共有repository設定，条件付き反例の復号とBoolean再検査を確認した．timeout対照では必要性未確定を保つ．fixtureは`tests/fixtures/external-conditions/`に置く．

既存101モデルは従来の固定contextで比較した．正常100件と意図した構文不正1件を区別し，CTL結果・反例数・モデル解釈・非空状態集合・SMV本文は変更前と一致した．独立BFS22ケースも一致した．評価スクリプトのA1〜A5等には従来の`default_scenario`を明示し，過去の固定条件の比較と，新しい自動候補モデルの比較を混同しない．

新しい自動解析では，`on: [push, pull_request]`の両eventとPRのfork両値，release filterに合う開始済みroot，Windowsのrunner条件，workflow_runの完了順序を検査した．Artifactデモでは取得と利用を区別し，到達版とsafe版の`dependent_use`の差を維持した．

条件付きsink fixtureは，forkへのwrite token提供を無効にしcontents認証をtrueへ制限した場合，`AG !bad_authority`がfork=false側でfalse，fork=true側でtrueとなる．自動候補全体ではprivate/internal fork policyによるfork=true側の経路もあり，fork=falseを必要条件とすることはできない．必要条件という説明は，Boolean両側の非空・検査完了を確認した有限モデルに限る．file内容等の未対応診断は消していない．

default branchの代表値，任意branch/path文字列の分割，Cacheの外部policy/versionの自動導出，secret/OIDC交換，組織の追加権限制約等は引き続き完全対応ではない．探索domainと未対応はmanifest・統合ログへ記録し，不達を実GitHub Actions全体の安全判定へ変換しない．
