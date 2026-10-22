# v2変換器の仕様と制約

SMV生成は `compiler.py` と共通の `TransitionSystem` に一本化している．`--scenario` は外部条件の入力を指定するもので，backendの切替えではない．省略時は宣言されたeventと対応するファイル操作から有限の既定scenarioを作る．Pythonは構文・literal・候補関係を下げ，到達判定はSMVの状態遷移とNuSMVへ残す．

## パイプラインとモジュール

`WorkflowLoader / source spans → expression Node → Run/Job/Step・versioned ActionContract → TransitionSystem → SMV → NuSMV → property / trace_mapper`．

| Module | 責務 |
|---|---|
| `expressions.py` | AST，GitHubのtruthiness／loose equality，有限domainの共有参照を用いるlowering |
| `policies.py` | event filter，ordered glob subset，cache scope／key優先順位，permission規則 |
| `action_contracts.py` | ref別contract identity／runtime／metadata URL．共通効果subsetだけをdispatchへ共有 |
| `composites.py` | 明示registryのmetadataをSHA-256確認して展開．Actionコードは実行しない |
| `compiler.py` | 型付きrun/job/step，有限filesystemとobject候補，scheduler／contract遷移生成 |
| `scenario.py` | 既定の有限run条件とファイル内容domainの組立て |
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
| workflow_run | subset対応 | supplied upstream runのcompleted後．単一upstream jobのconclusion/event/head_branch参照 |
| workflow_call / reusable workflow | 未対応 | 宣言を認識するがcaller payload／permission／secret伝播はunsupported |
| types / branches / paths / ignore | subset対応 | ordered `!`，`*`，`**`，`?`．`[]`／`+`／escape，diff打切り条件はunsupported |
| needs / 複数job | 対応 | job PC，terminal status，既定success依存，interleaving．needs cycleは生成拒否 |
| job/step if・status関数 | subset対応 | success/failure/always/cancelled．暗黙successを加え，明示status時は加えない |
| failure / skip / cancel | subset対応 | 失敗履歴，step outcome/conclusion，cancel latch後のcleanup．外部runner停止やtimeoutは未対応 |
| continue-on-error | step literal対応 | 元outcome failureとconclusion successを区別．job-level／動的式はunsupported |
| strategy.matrix | static cartesian対応 | 最大32展開，fail-fast:false．include/exclude，max-parallel，動的matrixはunsupported |
| concurrency | 未対応 | キュー置換／cancel-in-progressのrun間意味を省略して安全判定しない |
| Boolean / 比較 / 括弧 / null / number | subset対応 | loose型変換，case-insensitive string比較．配列／objectのinstance equalityはunsupported |
| github / needs / steps | subset対応 | 有限context，step output/result，単一needs job output．matrix集約outputはunsupported |
| env / inputs / matrix / runner / strategy / vars | subset対応 | 有限scenario値，envのprecedence，静的matrix．完全domainがないcontextはunsupported |
| contains/startsWith/endsWith/fromJSON/toJSON/format | subset対応 | 有限値上の評価．invalid JSON等の実行時式エラーはunsupported |
| upload/download Artifact | subset対応 | objectごとの存在・taint，literal／静的context name/path，複数object，run/repository selector |
| Artifact outputs | 部分対応 | artifact-idは抽象object token，digestはopaque値．実ID／hashを知ったとは扱わない |
| artifact-ids | subset対応 | 抽象ID outputの取得．実APIの数値IDは未観測 |
| pattern / merge-multiple | 未完成 | pattern候補は作れるが複数取得先layout／mergeをunsupportedにする |
| overwrite / if-no-files-found | 未対応 | 指定があればunsupported．削除／再生成，warn/error差の全意味は未モデル化 |
| cache/save/restore | subset対応 | v4，combined v5.0.4の共通効果契約．主key，prefix，restore-keys順序，scope優先，最新保存順 |
| Cache共有 | subset対応 | repository／scope／opaque versionが対応する保存済みslotだけ取得．複数候補から同一slotを選択 |
| Cache post-save | subset対応 | successful job後，exact-hitで保存しない．複数combined actionのpost順序はunsupported |
| Cache key/path動的式 | 部分対応 | env/inputs/matrix等の有限値．step/job output依存keyの遅延候補展開，hashFiles，複数動的pathはunsupported |
| Cache version/protected/branch policy | 部分対応 | versionはscenarioのopaque同一性仮定．保護branchによる任意追加制限，backend hash生成，evictionは未対応 |
| Composite | subset対応 | hash確認済metadata，単純step展開．input/output／parent guard／内部output scopeはunsupported |
| Shell | 閉じた操作subset | literalファイル生成，cp，cat，git add，GITHUB_OUTPUT，literalログ，exit，限定sink．任意BashやGITHUB_ENVはunsupported |
| integrity | 有限Artifact内容のSHA-256比較 | 同じfile slotの内容からdigestとoutputを生成．検証read，検証後use，不一致後useを分離．別ファイル・書換え後へ検証状態を引き継がない．無制限の内容domainやCache bytesのdigest契約は未対応 |
| permissions | subset対応 | workflow/job明示値，省略scope none，fork write設定，repository defaultは共有unknown enum |
| repository write / package publish / deployment | 抽象sink対応 | 限定git push/npm publish/gh API地点．権限・認証・到達・成功を区別．外部操作は行わない |
| OIDC / secrets | 未対応 | request/credential exchangeとsecret可用性の一般契約が未実装．該当任意command/Actionはunsupported |

## 値と相関

同じrun/context参照には同じFROZENVAR，同じstep outputには同じVARを使う．repository default permissionはrepository／scope単位の共有enumであり，各jobが独立にwriteかどうかを選ばない．同一runの同じoutputを矛盾する条件で参照したfixtureは不達になる．

有限context domainはscenarioの仮定である．literal以外の全実文字列を網羅したと主張しない．未指定contextを便宜的な「その他」1値へ押し込めず，診断を残す．式積の上限8192を超えた場合もunsupportedである．

filesystemはjobごとのliteral path slotに `absent/clean/tainted` を保持する．Artifact/Cache slotは別の同じ3値domainを持ち，保存時のfilesystem値を保持する．restoreは選択したslotの値だけを同じjob filesystemへ移す．実byte列，path内の個別file layout，digestの等価性は表していない．

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

[コンテナ操作手順](container-guide.md)に従い，YAML→SMV生成，NuSMV検証，集計を別々に実行する．CLI・全評価・対照構成はすべて `compile_workflows` を呼ぶ．`--scenario` を指定する場合は，各runのcontextをscenario.runsへ記述する．追加runは `--workflow third.yml` で渡す．省略時の既定値は観測済みの実行条件ではないため，実事例の評価では条件を明示する．

source mapの `/repo` は読取り専用repository，`/out` は専用出力を指す．NuSMV inputのactorとPCからYAML stepへ対応させる．stepをscheduleしたこと，ifの成立，処理成功は区別する．入力・SMV・対応表・ログ・反例は個別ファイルのまま残し，既存出力の上書きは拒否する．

## 確認した公式資料

確認日：2026-10-22．仕様から導入した具体的な規則は上表の限定範囲である．

- [Workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)
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

## 既定scenarioの範囲

既定値は，対応するgrepの有限候補とcopy/upload/downloadの接続から内容domainを組み立てる．metadataの文字種と長さは有限代表値で検査する．`checkoutValues` で各入力ファイルの有限内容を明示できる．内容domainは仮定であり，任意の入力値を網羅した証明ではない．

`workflowTrusted` はconsumerの定義を信頼済みの検証基準として扱えるかを指定する．省略時は共有の未知Booleanとし，信頼できない定義にあるliteral digestをtrust anchorとして認めない．単にdigest文字列が一致したというログと，信頼済み定義による検証を区別する．

構造化Shellのlocal変数・if/else・case・終了は順序を保って下げる．同一step内のdigest比較後のreadも，先に得た同じファイルの検証状態を使う．依存するファイル書換えの連鎖，未対応の展開や失敗後の継続を省略する場合はunsupportedを残す．
