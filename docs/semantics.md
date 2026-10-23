# v2変換器の仕様と制約

SMV生成は `compiler.py` と共通の `TransitionSystem` に一本化している．`--scenario` は外部条件の入力を指定するもので，backendの切替えではない．省略時は外部条件IRから合法な有限context候補を作り，共通backendの規則を候補選択guard付きで結合する．Pythonは構文・literal・候補関係を下げ，到達判定はSMVの状態遷移とNuSMVへ残す．

## パイプラインとモジュール

`WorkflowLoader / source spans → expression Node → Run/Job/Step・versioned ActionContract → TransitionSystem → SMV → NuSMV → property / trace_mapper`．

| Module | 責務 |
|---|---|
| `expressions.py` | AST，GitHubのtruthiness／loose equality，有限domainの共有参照を用いるlowering |
| `policies.py` | event filter，ordered glob subset，cache scope／key優先順位，permission規則 |
| `action_contracts.py` | ref別contract identity／runtime／metadata URL．共通効果subsetだけをdispatchへ共有 |
| `composites.py` | 明示registryのmetadataをSHA-256確認して展開．Actionコードは実行しない |
| `compiler.py` | 型付きrun/job/step，有限filesystemとobject候補，scheduler／contract遷移生成 |
| `scenario.py` | 従来の固定run条件とファイル内容domainの組立て |
| `external_conditions.py` | 条件IR，有限候補，CLI/JSONの共通制約，共有repository設定，既存backendの遷移IR結合 |
| `conditional_verification.py` | 反例条件の復号，Boolean両値での到達性再検査，有限モデル内の必要条件の区別 |
| `dataflow.py` | GITHUB_ENV，output，入力contextの共有値と依存ラベル，snapshotとhashFilesの有限計算 |
| `shell_lowering.py` | ファイル内容，SHA-256比較，metadata検査，step outputと利用履歴の遷移化 |
| `transition.py` | 状態宣言／guard付き更新／制約／CTLのIRとSMV出力 |
| `reference_cache.py` | YAML契約から別実装したCache明示状態探索．生成SMVやtransition式を読まない |
| `trace_mapper.py` | NuSMV state/inputの差分を保持し，実際のactor／PCをYAML stepへ戻す |

## 対応範囲表

「対応」は以下に記した有限scenario／閉じた契約の範囲に限る．

| 項目 | 状態 | 範囲・未対応理由 |
|---|---|---|
| pull_request / pull_request_target / push | subset対応 | supplied event，activity，base/head branch，tag，完全なchangedPathsでfilter |
| workflow_dispatch / repository_dispatch | subset対応 | 配信済みイベントのscenario．外部API／認証は実行しない |
| schedule | 部分対応 | cron宣言と配信scenario．時刻計算，遅延，配信dropは非モデル化 |
| workflow_run | subset対応 | supplied upstream runの全Job終端を待つcompleted順序．event/head_branchと，終端結果を集約したconclusion参照 |
| workflow_call / reusable workflow | 未対応 | 宣言を認識するがcaller payload／permission／secret伝播はunsupported |
| types / branches / paths / ignore | subset対応 | ordered `!`，`*`，`**`，`?`．`[]`／`+`／escape，diff打切り条件はunsupported |
| needs / 複数job | 対応 | job PC，terminal status，既定success依存，interleaving．needs cycleは生成拒否 |
| job/step if・status関数 | subset対応 | success/failure/always/cancelled．暗黙successを加え，明示status時は加えない |
| failure / skip / cancel | subset対応 | 失敗履歴，step outcome/conclusion，cancel latch後のcleanup．外部runner停止やtimeoutは未対応 |
| continue-on-error | step literal対応 | 元outcome failureとconclusion successを区別．job-level／動的式はunsupported |
| strategy.matrix | static cartesian対応 | 既定上限32展開，include/exclude，fail-fast:false．max-parallel，動的matrix，fail-fastによる取消はunsupported |
| concurrency | 未対応 | キュー置換／cancel-in-progressのrun間意味を省略して安全判定しない |
| Boolean / 比較 / 括弧 / null / number | subset対応 | loose型変換，case-insensitive string比較．配列／objectのinstance equalityはunsupported |
| github / needs / steps | subset対応 | 有限context，step output/result，単一needs job output．matrix集約outputはunsupported |
| env / inputs / matrix / runner / strategy / vars | subset対応 | 有限scenario値，envのprecedenceとGITHUB_ENV，静的matrix，dispatchのchoice/boolean．自由文字列や未指定varsは診断付き共有代表値 |
| contains/startsWith/endsWith/fromJSON/toJSON/format | subset対応 | 有限値上の評価．invalid JSON等の実行時式エラーはunsupported |
| upload/download Artifact | subset対応 | objectごとの存在・taint，literal／静的context name/path，複数object，run/repository selector |
| Artifact outputs | 部分対応 | artifact-idは抽象object token，digestはopaque値．実ID／hashを知ったとは扱わない |
| artifact-ids | subset対応 | 抽象ID outputの取得．実APIの数値IDは未観測 |
| pattern / merge-multiple | 未完成 | pattern候補は作れるが複数取得先layout／mergeをunsupportedにする |
| overwrite | 未対応 | 有効化を検出した保存規則でdiagnostic．削除／再生成による同一性の変化は非モデル化 |
| if-no-files-found / retention-days | 意味未実装 | withキーとしては受理するが，warn/error差・期限の効果は生成しない．指定だけで必ずdiagnosticになる実装ではなく，対応済みとは判断しない |
| cache/save/restore | subset対応 | v4，combined v5.0.4の共通効果契約．主key，prefix，restore-keys順序，scope優先，最新保存順 |
| Cache共有 | subset対応 | repository／scope／opaque versionが対応する保存済みslotだけ取得．複数候補から同一slotを選択 |
| Cache post-save | subset対応 | successful job後，exact-hitで保存しない．複数combined actionのpost順序はunsupported |
| Cache key/path動的式 | 部分対応 | env/inputs/matrix/step/job output依存のkeyと単一pathを有限候補へ展開．値とpathの条件を共有する．複数path・glob layoutはunsupported |
| Cache version/protected/branch policy | 部分対応 | versionはscenarioのopaque同一性仮定．保護branchによる任意追加制限，backend hash生成，evictionは未対応 |
| Composite | subset対応 | hash確認済metadata，単純step展開．input/output／parent guard／内部output scopeはunsupported |
| Shell | 閉じた操作subset | literalファイル生成，cp，cat，git add，GITHUB_OUTPUT，literalログ，exit，限定sink．GITHUB_ENVの単純な1行書込みと引用済み変数展開を追加．任意Bash，複数行環境ファイル，command substitutionはunsupported |
| integrity | 有限Artifact内容のSHA-256比較 | 同じfile slotの内容からdigestとoutputを生成．検証read，検証後use，不一致後useを分離．別ファイル・書換え後へ検証状態を引き継がない．無制限の内容domainやCache bytesのdigest契約は未対応 |
| permissions | subset対応 | workflow/job明示値，省略scope none，fork write設定，repository defaultは共有unknown enum |
| repository write / package publish / deployment | 抽象sink対応 | 限定git push/npm publish/gh API地点．権限・認証・到達・成功を区別．外部操作は行わない |
| OIDC / secrets | 未対応 | request/credential exchangeとsecret可用性の一般契約が未実装．該当任意command/Actionはunsupported |

## 値と相関

同じrun/context参照には同じFROZENVAR，同じstep outputには同じVARを使う．repository default permissionはrepository／scope単位の共有enumであり，各jobが独立にwriteかどうかを選ばない．同一runの同じoutputを矛盾する条件で参照したfixtureは不達になる．

有限context domainはscenarioの仮定である．literal以外の全実文字列を網羅したと主張しない．未指定contextを便宜的な「その他」1値へ押し込めず，診断を残す．式積の上限8192を超えた場合もunsupportedである．

filesystemはjobごとのliteral path slotに `absent/clean/tainted` を保持する．Artifact/Cache slotは別の同じ3値domainを持ち，保存時のfilesystem値を保持する．restoreは選択したslotの値だけを同じjob filesystemへ移す．構造化ShellとhashFilesで扱うfileには，有限のUTF-8内容候補を別に保持する．Artifactの対応member以外の汎用layoutや，Cacheの実byte列は表していない．

stepはatomic契約で，操作結果は非決定的な入力である．同一Shell中のwrite後read等，microstepが必要な依存はunsupportedにする．Cache保存のI/O成功はstep outcomeと別入力であり，保存がwarningで終わってもstepがsuccessとなる状態を残す．保存順clockは有限step数から上限を取り，slot作成は1回以下なので到達状態でwrapしない．

## Cache policyの分離

`github-historical-2025` は既存研究との比較用に明示選択する過去のbranch/ref scope契約である．当時の任意platform設定を再現したものではない．`github-current-2026-10-22` は確認時点の `cache-mode: read/write/write-only/none` を分ける．同じYAMLでもpolicyによって保存可能性が変わる．対象policyの選択自体が研究上の仮定である．

Cache versionは `cacheVersion: {literal-path: opaque-version-token}` で与える．path文字列が同じというだけで実サービス上のversion同一性を断定しない．GitHub仕様にないprotected-cache境界を追加しない．未知のplatform制限はこのモデルの保証外である．

Cache Actionのexact-hit判定はlocale比較を使うため，caseだけが異なるkeyや非ASCII keyをPythonの単純一致で安全扱いしない．serviceのkey探索との対応が未確認の組合せはcache-key-collation unsupportedとする．[確認したutility実装](https://github.com/actions/cache/blob/v4/src/utils/actionUtils.ts)．

## 権限sinkの意味

`sink_site` はcommand地点到達履歴，`authority_available` は必要scopeと認証条件，`sink_succeeded` はさらに非決定的操作成功，`bad_authority` はそれらとtainted file依存が同時に成立した履歴である．権限だけで成功させない．git pushはgit add由来のtaintを要求し，npm publishは指定pathと外部registry credentialを用いる．認証の事実はscenarioまたは共有unknown入力であり，実credentialは渡さない．

`id-token: write` だけではOIDC利用成功とはしない．TanStackのOIDC propertyをこの限定sink結果へ置換しない．

## unknown/unsupportedと健全性

全manifestにproperty relevance付きdiagnosticsを保存する．unsupportedが一つでも残れば `verdict=unknown/unsupported` とし，CTLのtrueをglobal safeに変換しない．unknownのcredential/policy入力も観測値ではない．入力が真の反例は条件付き経路に限る．この実装全体についてGitHub意味論への健全性証明を行ったわけではない．

schedule配信，registry snapshot選択，同時刻token policy，shell atomicity，cache version，同一object候補範囲は明示した有限scenarioの仮定である．BFS一致は下記subsetの二つの実装間の一致であり，外部platformの正しさの証明ではない．

## 実行と保存

[コンテナ操作手順](container-guide.md)に従い，YAML→SMV生成，NuSMV検証，集計を別々に実行する．CLI・全評価・対照構成はすべて `compile_workflows` を呼ぶ．`--scenario`は部分的な`conditions`または旧形式の完全な`runs`を受け付ける．追加runは `--workflow third.yml` で渡す．省略時の既定値は観測済みの実行条件ではないため，実事例の評価では条件を明示する．

source mapの `/repo` は読取り専用repository，`/out` は専用出力を指す．NuSMV inputのactorとPCからYAML stepへ対応させる．stepをscheduleしたこと，ifの成立，処理成功は区別する．SMVと対応表を保存し，反例・コマンド・診断・時間は1つの検証ログへまとめる．既存出力の上書きは拒否する．

## 確認した公式資料

確認日：2026-10-23．仕様から導入した具体的な規則は上表の限定範囲である．

- [Workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)
- [Hosted runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
- [Repository Actions settings](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-github-actions-settings-for-a-repository)
- [Expressions](https://docs.github.com/en/actions/reference/workflows-and-actions/expressions)
- [Trigger events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)
- [Dependency caching](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching)
- [Cache v4 metadata](https://github.com/actions/cache/blob/v4/action.yml)，[save implementation](https://github.com/actions/cache/blob/v4/src/saveImpl.ts)，[restore implementation](https://github.com/actions/cache/blob/v4/src/restoreImpl.ts)
- [Cache v5.0.4 metadata](https://github.com/actions/cache/blob/v5.0.4/action.yml)，[save implementation](https://github.com/actions/cache/blob/v5.0.4/src/saveImpl.ts)，[restore implementation](https://github.com/actions/cache/blob/v5.0.4/src/restoreImpl.ts)
- [Upload v4 metadata](https://github.com/actions/upload-artifact/blob/v4/action.yml)，[Download v4 metadata](https://github.com/actions/download-artifact/blob/v4/action.yml)

タグはimmutable SHAへ新たに解決していない．manifestには使用refとcontract identityを保存し，v4／v5のruntimeとmetadata参照を分けた．TanStack external setupは既存registryのmetadata SHAを再確認するが，実際のincident runが同じremote SHAを使った証明ではない．

## Artifactの値と完全性

`frontend.py` と `WorkflowLoader` がYAMLの行位置を扱い，`workflow_shell_subset.py` が限定Shellを解析する．`compiler.py` のRun/Job/Stepと `shell_lowering.py` の内容・由来・検証状態を共通の遷移IRへ下げる．旧builderと専用IRは使用しない．

producer/consumerのjob・step順序，needs，成果物候補の同一性，download結果，成果物に依存したread，digest検査，模擬権限地点を区別する．`verification_read`，`post_verification_use`，`post_integrity_mismatch_use` は別の履歴であり，後から検査で遮断されても過去のreadを取り消さない．

| 主なproperty対象 | 意味 |
|---|---|
| `artifact_dependent_use` | downloadした成果物に依存する利用地点 |
| `simulated_sink_site_reached` | 研究用の模擬権限地点 |
| `bad_simulated_path` | 未信頼成果物から模擬権限地点への経路 |
| `bad_real_authority_path` | 実権限契約に基づく経路．模擬markerから実操作の成功を推論しない |
| `bad_authority` | scenarioと閉じた操作契約に基づく権限経路 |

Pythonが最終的な危険到達Booleanを事前計算してSMVへ埋め込む構成ではない．到達性は生成した状態遷移をNuSMVで検査する．独立BFSの一致は対応subset内の実装間照合であり，GitHub全体や実際の侵害成功の証明ではない．

## 外部条件IRと探索

最初に指定したworkflowは，宣言eventとfilterに整合する実行が開始済みとする．その起動可否はpropertyにしない．Jobのif・needs・stepの順序と成否は通常どおり探索する．後続workflowはYAMLのworkflow名・activity・branch filter・default branch上のworkflow存在条件・上流Jobの終端状態を開始guardへ下げる．初期workflowが開始済みでも，schedulerが必ず進むfairnessは追加しない．

`ExternalCondition`は条件名，型，domain，共有単位，根拠，網羅性，制約，SMV記号と復号表を保持する．`ConditionSpace`は同じrun内で整合するevent/activity/fork/head/base/ref/runner候補をprofileへまとめる．`FROZENVAR external_run_*`でprofileを選び，実行途中でeventやforkが変わらないようにする．

Pythonは各合法profileを既存の`WorkflowCompiler`で遷移IRへ下げる．`compile_space`は同じPC・file・object変数を共有し，profileのguardで更新規則・DEFINE・TRANSを結合する．異なるbackendや別々の検証モデルを選ぶ方式ではない．caseの優先順を保つため，同一の規則列全体だけをまとめる．初期値がprofile間で異なる未対応ケースは黙って併合せず生成を拒否する．

| 条件 | 自動解析での扱い | 制約と限界 |
|---|---|---|
| event/activity | YAMLの全宣言event，明示typesまたは対応済み既定types | pushにfork=trueを組み合わせない．未知eventの意味は診断を残す |
| PRのfork/head/base/ref | fork両値と対応するrepository役割，PR merge refとPR_target base refを一組で選ぶ | 実PR番号や全head repositoryを列挙しない．checkoutのtaint規則は既存契約 |
| branch/tag/path filter | filter literal・対応if式のliteral・一致/非一致の有限例 | globや任意文字列・変更集合の完全分割ではなくunsupportedを記録する |
| default branch/repository | 同一repository役割を共有し，明示default branchを全runへ伝播する | 未指定default branchはmainという代表値．実repository設定を取得しない |
| token defaults | 参照されたscopeに対してrestricted/permissive profileをrepositoryで共有 | contents/packages readまたは対応scope write，id-token既定none．組織の追加上限制約は未対応 |
| fork write token | repository共有BooleanとvisibilityをINITで制約する | publicではprivate/internal用write-token設定を許さない．明示した矛盾は入力エラー |
| workflow_run | 同じrepositoryで名前が合う全供給runを上流候補とする | completedは上流全Job終端を待つ．requested/in_progressは順序意味が未対応 |
| runner | 既知runs-on labelと静的matrixのOS/architectureをJob別に解決する | 未知labelは有限候補と診断．異種OSの複数Jobへのrun単位OS制限は曖昧として拒否 |
| Artifact/Cache | 各profileのrepository/ref/scopeを既存の取得・保存・selector契約へ接続する | Cache policy/versionの実環境からの自動発見や完全なversion導出は未対応 |
| credential/trust/file存在 | 利用する既存契約のFROZENVARを条件IRへ登録する | 可用性は未知の抽象Booleanであり，実認証や外部交換を再現しない |

repository既定permissionsはworkflow/jobの明示permissionsとは別階層である．明示permissionsが既定値と違うだけで矛盾にはしない．同じrepositoryの外部設定を別runへ相反する値で指定した場合はエラーにする．自動の標準permission profile外の設定は，完全な旧scenarioで明示する必要がある．

`bounds.conditionConfigurations`の既定値256は，全runのprofile直積の上限である．超過時は候補を切り落とさず生成エラーにする．自由文字列，default branch代表値，file内容，未対応認証等の制約は`externalConditions.coverage`へ保存する．`coverage.complete`がfalseのとき，不達をGitHub全体の安全保証にはしない．

完全な旧scenarioは従来の固定contextとして同じbackendへ渡す．既存実験の互換性のため，旧scenarioのtrigger規則や生成SMVを維持する．新しい開始済みrootの契約と自動候補探索は，自動/部分条件モードの範囲である．`mode: automatic`を付ければ，`runs`が完全に見える場合でも自動候補の制約として解釈する．

## 条件付き到達性

全候補の中に到達経路があるかは`AG !target`の反例で判断する．複数の初期状態のすべてで評価される`EF target`のfalseを，全候補で不達という意味には使わない．反例中に表示された固定条件だけを`witnessConditions`へ復号し，未表示値を推測しない．

Boolean条件は両側を一時SMVのINITで制限し，非空状態集合・遷移全域性・NuSMV完了を確認する．片側だけ到達した場合に限り，その有限モデル内で必要な値と呼ぶ．両側到達なら必要条件ではない．timeout・空の制限domain・未完了は必要性未確定とする．Boolean条件の既定追加検査数は8で，残りは未検査として記録する．複数条件の最小必要集合や文字列条件の必要性は計算しない．

`conditionalReport`は到達性，検証完了，coverage，unsupported/unknown，反例値，Boolean再検査，確認済みの必要条件を分けて記録する．`--property`で報告対象を選び，`--no-condition-checks`で追加検査を省ける．生成モデルのproperty自体は削除しない．コマンド・出力・エラー・時間は追加検査も含めて同じ`.smv.log`へ保存する．

## 自動file候補と既存契約の範囲

通常のCLI入力はYAMLだけでよい．部分JSONとCLIは候補を制限し，競合時は上書きせずエラーにする．対応表の`externalConditions`が候補とSMV記号を，`automaticAssumptions`がfile候補を保持する．`inputMode`は`automatic`，`constrained-automatic`，`explicit-scenario`を区別する．

checkoutのあるJobが静的な単一相対pathをuploadし，そのfileが既存のcopy/grep候補や先行書込みから供給されない場合，そのpathをcheckout候補へ追加する．`optionalCheckoutFiles`の各pathにはrun・pathで共有するBooleanのFROZENVARを宣言する．TRUEならcheckout成功時にfileと内容を供給し，FALSEなら存在しない初期状態を保つ．後のuploadは存在条件を要求するため，非存在を勝手に保存成功へ置き換えない．

追加fileの内容は空文字と`untrusted\n`の有限代表値である．任意の内容を覆う抽象値ではなく，代表値を本物のrepository内容とも扱わない．PR由来のcheckoutはtainted，その他は既存のeventベースの信頼規則を使う．単一fileという解釈，内容domain，存在分岐，根拠のworkflow・job・stepを`automaticAssumptions`へ残す．単純なcatの場合もArtifact memberの相対名を保持してdownload先のfileへ対応付ける．

`workflow_run.workflows`の名前と供給YAMLの`name`が一意に一致する場合，上流runとして結ぶ．入力順は名前による上流選択の根拠にしない．自動条件では複数候補を探索し，候補なしは`external-workflow-run`，旧固定scenarioの導出では候補なし・複数候補を`automatic-workflow-run`，directory末尾指定・glob・動的式・複数path等を単一checkout fileへ導出できない場合は`automatic-checkout-path`の未対応診断を残す．既存のShell・Actionの未対応診断も維持する．

自動モードは全宣言eventとPRのfork両値を探索するが，main default branch，合成repository，無限文字列の有限代表値などは仮定として残る．この有限内容domainやcontextの外側へ不達を一般化しない．明示scenarioを指定すれば，入力fileの在庫や内容，event等を固定できる．自動条件を土台に内容を差し替えた場合は明示値を優先し，そのfileの未知存在条件を外す．snapshotを指定した場合も自動代表値を実ファイルより優先せず，変更理由を`automaticAssumptionOverrides`へ記録する．

検証ログの冒頭に，対応表から解析条件・自動仮定・未対応を表示する．SMV本文の`modelSha256`を照合し，別モデルの対応表なら条件を根拠として表示しない．古いhashなし対応表は未照合と明示する．未対応があれば画面とログにも警告し，NuSMVのpropertyのtrueとモデル全体の解釈を分ける．

既定値は，対応するgrepの有限候補とcopy/upload/downloadの接続から内容domainを組み立てる．metadataの文字種と長さは有限代表値で検査する．`checkoutValues` で各入力ファイルの有限内容を明示できる．内容domainは仮定であり，任意の入力値を網羅した証明ではない．

`workflowTrusted` はconsumerの定義を信頼済みの検証基準として扱えるかを指定する．省略時は共有の未知Booleanとし，信頼できない定義にあるliteral digestをtrust anchorとして認めない．単にdigest文字列が一致したというログと，信頼済み定義による検証を区別する．

構造化Shellのlocal変数・if/else・case・終了は順序を保って下げる．同一step内のdigest比較後のreadも，先に得た同じファイルの検証状態を使う．依存するファイル書換えの連鎖，未対応の展開や失敗後の継続を省略する場合はunsupportedを残す．

## 2026-10-23のデータフロー拡張

`GITHUB_ENV`の単純な1行書込みをjobごとの状態に保持する．同じstep内の参照には書込み前の値を使い，後続stepへ反映する．未設定と空文字の設定を区別する．step-level envは実行時envより優先し，実行時envはjob/workflowのenvより優先する．別jobへは自動継承しない．同じstep内の無条件の複数書込みは後勝ちとする．

`GITHUB_OUTPUT`，job outputs，`needs.<job>.outputs`へ同じ有限値と条件を引き継ぐ．完了したjobのoutputは失敗後の`always()`経路からも参照できる．step/job output，実行時envを用いるArtifact name・Cache keyと単一pathの候補は，domainの収束後に宣言する．combined Cacheのpost-saveはrestore時点のkey/path条件を保持し，後続stepの値変更で保存先を変えない．

値，Artifact由来の印，taint，データ依存，制御依存は別の状態である．`dataflowLabels`にoutput/envの各ラベルを記録し，`runtimeEnvironment`に値のdomainと設定済みフラグを保存する．由来ラベルはArtifact由来かを表すBooleanであり，全source集合を列挙する汎用provenance graphではない．どのobjectが選ばれたかは反例の選択入力とobject/source mapから確認する．

環境ファイルの複数行delimiter，予約変数への書込み，未認識のリダイレクト，Shellの注入効果を伴う式展開は対応済みとは扱わない．これらにはdiagnosticを残す．Shellを実行して内容を取得することはない．

### hashFilesとsnapshot

literal patternの`hashFiles()`を，呼出し時点のfile存在・有限内容候補に依存する式へ変換する．各fileのSHA-256のbyte列を連結し，再度SHA-256を計算する．一致fileがなければ空文字となる．同じ呼出しを複数回参照しても，別々の無関係なBooleanにはしない．対応するfile書換え後は，更新後の内容状態から計算する．

scenarioの各runへ`repositorySnapshot`を指定すると，そのディレクトリのUTF-8通常fileを読み，内容hashと在庫を`hashInputs`へ記録する．`.git`は除外し，symlinkとbinaryは未対応診断にする．これは「指定snapshotが実行時checkoutを表す」という外部仮定であり，現在のcheckoutを過去の実行の内容と自動的に同一視しない．明示した`checkoutValues`はsnapshotより優先する．

globには`fileInventoryComplete`による完全な在庫の仮定が必要である．Linuxの限定globを使い，複数fileの集約順はpathのソート順という仮定を記録する．Windows・複雑なpattern・Cache復元後のbyte列・完全な内容domainがない場合は診断を残す．不明なhashの有限代表値は実hashではなく，`hashfiles-domain`があるモデルから安全を結論しない．

### 入力と上限

workflow_dispatchのchoiceは宣言されたoptions，booleanはtrue/falseを共有FROZENVARへ変換する．明示scenarioの値と`domains`を優先する．自由文字列・数値や未指定repository varsには完全なdomainがないため，代表値とunsupported診断を使う．

scenarioの`bounds`で`runs`，`jobs`，`objects`，`matrix`，`domainPasses`，`expressionProducts`，`hashFiles`，`conditionConfigurations`を指定できる．run/job/object上限を超えた場合は生成を拒否する．matrixや式の探索範囲を表現できない場合もdiagnosticを残し，切り落とした経路を安全と扱わない．

今回確認した規則は[環境ファイル](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-commands#setting-an-environment-variable)，[hashFiles](https://docs.github.com/en/actions/reference/workflows-and-actions/expressions#hashfiles)，[Cache探索](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching)，[matrix include/exclude](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/run-job-variations)を参照した．hashの集約は[toolkit実装](https://github.com/actions/toolkit/blob/main/packages/glob/src/internal-hash-files.ts)も確認した．これらのリンク先全体を実装したことは意味しない．

## 検証コマンドと出力の注意

検証プログラムの実体は`tools/workflow_model_v2/verification.py`です．コンテナの`/repo`から`python3 tools/workflow_model_v2/verification.py /out/example/model.smv`の形で起動できます．`python3 -m workflow_model_v2.verification`は同じmoduleを検索して起動する別表記です．具体的な英数字の出力先と手順は[コンテナ操作手順](container-guide.md)を参照してください．

wrapperは実行コマンド，CTLと状態数列挙の生出力・診断，時刻，実行時間，終了コードを`model.smv.log`へまとめる．末尾のJSONは集計情報と生出力のbyte位置を持つ．末尾情報のないログは未完了とし，状態列挙はNuSMVの0終了に加えて正の到達状態数を要求する．日本語パスはUTF-8で渡す．旧形式の個別ログも読めるが，新規生成はしない．この保存形式の変更はSMVの変換規則を変更しない．

`outputs/`はGit管理外の生成結果で，解析器の依存ではありません．削除すると過去の入力コピー・外部条件・検証証拠も失うため，必要な実験を退避してから不要分を整理します．

ホストの`outputs/runs/`はコンテナの`/out`のmount元です．起動中にそのdirectory自体を削除すると接続が無効になり，変換前の出力先確認でもPermissionErrorになる場合があります．不要な結果の削除とmount元directoryの削除を区別し，後者は[保存先とコンテナの再接続手順](container-guide.md#outputsを整理するときの注意と復旧)に従います．
