> 保管場所更新：この報告は整理前の実装・評価記録です．最新の配置と保存方針は [リポジトリ管理](../repository-maintenance.md)，要約は [latest](../../outputs/model-v2/latest/README.md)．当時の全詳細はarchiveから復元できます．

# 次期YAML→SMV変換器 v2 prototype 実装・検証報告

**実施日：2026-10-22**\
**状態：部分達成．** A1～A5の有限状態モデル生成，NuSMV検査，21 mutation，YAML source mapを作成した．A1/A3のdownload/use部分は独立BFSとも照合した．一方，実権限sink contract，実Artifact ID／run ID同一性の証明，integrity／sinkを含む独立探索は未実装であり，研究目的全体の完了とはしない．

## 0. 作業範囲とGit状態

対象branchは `research/automatic-temporal-verification`，HEADは作業開始時・終了時ともに `8ecb091f540065057e2c4045e21678494eba19b5`．commit，push，branch操作は行っていない．

作業開始時に記録したstatus：

```text
## research/automatic-temporal-verification...origin/research/automatic-temporal-verification
 M .github/workflows/artifact-a1-pr-producer.yml
 M .github/workflows/artifact-a1-unsafe-consumer.yml
?? docs/next-model-design/
?? experiments/model-v2-container/
?? outputs/
?? tests/test_workflow_model_v2.py
?? tests/test_workflow_to_smv.py
?? tools/workflow_model_v2/
?? tools/workflow_shell_subset.py
?? tools/workflow_to_smv.py
?? tools/workflow_to_smv_v2.py
```

この検証を再開する直前のstatusでは `experiments/model-v2-container/` は既に存在せず，そのほかの項目は残っていた．終了時の正確なstatusと `git diff --stat` は，本報告の末尾に追記する．既存A1～A5 YAMLはこの作業では変更していない．A1の2 YAMLには開始前からコメント等の未コミット差分があり，そのまま保持した．`results/`，旧Artifact/Cache解析器，論文TeX/PDF，既存期待値も変更していない．新規出力は `outputs/model-v2/` に分離した．

## 1. 実装ファイル

| ファイル | 役割 |
|---|---|
| `tools/workflow_to_smv.py:418` | 既存workflow parser／schedulerを維持し，step prerequisite，Action outcome，upload/download状態を生成SMVへ追加．step guardとfailure guardは同ファイルの `:513`, `:517`, `:1122` 付近． |
| `tools/workflow_model_v2/ir.py:41` | `ValueIR`，`ProvenanceEdge`，`StepIR`，`ArtifactObjectIR`，`PairIR` を定義．`:125` のAction contract registry，`:153` のstep lowering，`:189` のpair IR生成でsource span，action ref，条件AST，shell operation，診断を保持． |
| `tools/workflow_shell_subset.py:46` | source span付きの限定Shell IR．`parse_shell` は `:102`．認識しないnon-empty statementがあればscript全体をunsupportedにする． |
| `tools/workflow_model_v2/builder.py:48` | Typed IRと既存schedulerを結び，値状態，output，integrity state，simulated sink stateを生成．Download成功を前提とするfile read guardは `:214`，状態遷移生成は `:419`，CTL propertiesは `:707`． |
| `tools/workflow_model_v2/independent_bfs.py` | YAML structureと限定Shell read opsから独立した逐次遷移を構成し，A1/A3のdownload／use reachabilityだけをBFSで探索．生成SMVのASSIGN式は読まない． |
| `tools/workflow_to_smv_v2.py:18` | Producer／Consumer YAMLからSMVとmanifestを出すCLI． |
| `tools/workflow_model_v2/mutation_matrix.py:199` | 元YAMLを編集せず，`/out/mutations/` に一時コピーと21 mutationを作る． |
| `tests/test_workflow_model_v2.py:96` | file-derived useにはdownload成功状態が必要という回帰条件を検査．独立BFSのA1/A3差，限定Shell，A5 output，unrelated markerも検査． |
| `tests/test_workflow_to_smv.py` | 既存変換器回帰． |

Pythonは最終的な危険経路の真偽を計算しない．IRと抽象値の対応を生成し，`bad_simulated_path` 等はSMV上のstate式として定義する．Counterexample／property結果はNuSMVの出力から収集した．manifestの `verdict` はunsupportedの存在を示す診断値で，危険経路判定ではない．

## 2. 対応したYAML・Shell構文

### YAMLとworkflow構造

- Producer eventは `pull_request` と `push`，Consumer eventは `workflow_run` を有限domainへ下げる．PR activity，literal branch filter，workflow名，`workflow_run.types` を表す．`paths` filterは未知のBoolean入力であり，固定しない．
- `workflow_run.types: [completed]` のConsumerはProducer jobがterminalになってから開始できる．Producer開始・完了・結論はSMV状態変数である．
- job program counterとstep program counterで，YAMLに実在するstepを順番に進める．既存base schedulerの`needs` edgeは保持する．v2のArtifact/value side effectは各workflow 1 jobを対象とし，複数jobではunsupported診断を付ける．
- `if` は既存式parserのASTを有限domain比較，論理演算，status／needs／step output参照へ下げる．未対応比較はunknownまたはunsupportedとして残す．同じstep output参照は同じSMV変数を使う．
- `uses` contractは次のpinに限定する：`actions/checkout@11d596a...`，`actions/upload-artifact@ea165f8...`，`actions/download-artifact@d3f86a1...`．未登録refはunsupported．
- `with.name` とliteral upload/download pathを結合候補として使う．Downloadには明示GITHUB_TOKEN参照，`actions: read/write`，および `run-id: ${{ github.event.workflow_run.id }}` を要求する．他のrun-id式はunsupported．`runner.temp` 配下の安全なliteral suffixはパス照合に対応する．GitHubのcontext仕様は [公式contexts reference](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts) を参照．
- workflow／jobの明示permissionsをscope別に保持する．一部scopeが書かれた場合に省略scopeがnoneとなる規則は [公式workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#permissions) と整合する．宣言がないときの実効権限はrepository／organization設定に依存するためunknownにする．

### 限定Shell規則

認識対象は、A1～A5のrun本文で使われる構文の閉じた部分集合である．literal `mkdir -p`／`cp`／`grep -Eq`，literal／限定変数代入，Artifact pathを指す `tr -d` 読取り，`sha256sum | cut`，限定 `if [ "$x" = "literal" ]`／変数比較，限定`case`，`test -f`／文字列長，`exit`，`GITHUB_OUTPUT`へのprintf，固定marker／summary出力を扱う．operationにreads／writes／path／condition／source spanを保持する．

完全なscriptをparseできない場合，残りのShellを無視して安全扱いしない．A1のproperty関連stepへ`eval`を挿入したmutationは`shell-subset` unsupportedとなり，そのstepのartifact-dependent useを生成しなかった．これは安全判定ではなく，検証範囲外である．

## 3. 生成SMVの状態と遷移

### 主な状態

- Producer：`producer_phase`，`producer_has_started`，`producer_conclusion`．job／step PCと失敗latchはworkflow YAMLから生成する．
- Artifact object：uploadごとの`not_uploaded`／`upload_failed`／object token．upload stepがsuccess outcomeの時だけobject slotを設定する．
- Download：`not_attempted`／`download_failed`／`download_succeeded`と`download_object`．同名候補，run selector，token scopeが合い，Action outcomeがsuccessの時に成功する．
- Data：`producer_payload`は`publish_true`／`publish_false`／`other`の抽象値．Consumer側に`artifact_read`，`artifact_dependent_use`，`consumer_payload`，metadata，output状態がある．
- A2 integrity：`integrity_not_run`／`integrity_verified`／`integrity_mismatch`／`integrity_unverifiable`と`integrity_blocked`．`verified` output，後続guard，selected payloadは同じ有限domain値を共有する．
- Sink：`simulated_sink_site_reached`，`artifact_dependent_sink`，`simulated_sink_result_true`，`real_authority_sink_site_reached`，`authority_operation_succeeded`を分ける．`effective_contents_write`と`effective_authority_available`も別defineである．
- Inputs：trigger context／payload／metadata／trusted workflow flagはFROZENVAR，scheduler／各step outcomeはIVAR．step outcomeは成功・失敗を非決定的に選ぶ．そのため成功・失敗は実際のGitHub実行結果ではなく，可能な経路を残す抽象化である．

### Artifactとdata flow

ProducerのPR由来checkoutをsource provenance候補として登録する．認識したpre-upload copyのdestinationがupload path内ならfile value→Artifact objectのedgeを作る．Downloadはupload object tokenを`download_object`へ代入する．Consumer file variableが同じdownload pathに対応するとき，read／digest／file testのSMV更新guardへ`download_state = download_succeeded`を必須にする．このため「downloadした」だけでは`artifact_dependent_use`を立てない．

step outputは生成stepの実行とsuccess outcomeを条件としてstateへ一度設定し，後続の`steps.<id>.outputs.<name>`参照が同じsymbolを読む．A5ではmetadata read→Shell変数→`GITHUB_OUTPUT`→後続env／if→simulated update markerの関係をprovenance edgeでつなぐ．単なる文字列一致はdependencyとしない．

**Artifact identityの限界：**SMV object tokenは，静的なworkflow名／artifact名／run selectorから選んだ抽象候補である．GitHubの実artifact IDやruntime digestを取得・照合したものではない．実Artifact APIはartifact ID，digest，workflow_run情報を別々に返すため，[公式Artifact REST API](https://docs.github.com/en/rest/actions/artifacts)で得るruntime情報との照合は別作業である．現在の`same_object`は候補モデル内の同一slotを表す．

### 完全性確認の限界

A2では`consumer_workflow_trusted`を独立FROZENVARとして明示し，trueの場合にのみliteral digestをtrust anchorとして扱う．downloadした同じ抽象objectのselected payloadと，有限値`publish_false`のdigest-equal relationからverifiedを作る．`publish_true`はmismatchとなり，verification stepのoutputがfalse，次step guardが同一outputを見てuseを遮断する．

これはsha256sumのShell構文を認識して状態へ下げるが，byte列上のSHA-256計算や実digest値を再計算した結果ではない．`consumer_workflow_trusted = TRUE`とSHA-256 collision resistanceを仮定する有限domain契約である．A2で`artifact_dependent_use`が到達可能なのはdigest検査自身がArtifact bytesを読むためであり，検査失敗後のsink利用が通るという意味ではない．

## 4. A1～A5のNuSMV結果

各モデルの初期状態は非空で，NuSMVで到達状態数を数えた．`AG !X`がfalseで反例traceを出す場合，Xへ少なくとも一つの経路で到達したことを意味する．trueはこの抽象モデル内での不達のみを示す．

| Case | Download | Dependent Use | Integrity Barrier | Simulated Sink | Real Authority | Verdict |
|---|---|---|---|---|---|---|
| A1 | 到達可能 | 到達可能，download後 | 未検証のまま | `bad_simulated_path`反例あり | unsupported | 条件付きsimulated経路あり．実publishの証明ではない |
| A2 | 到達可能 | 到達可能．digest検査readを含む | verified／mismatch／blocked全て到達．`integrity_blocked -> !artifact_dependent_use`はtrue | marker siteは到達するがArtifact依存sinkは不達 | unsupported | 抽象trust anchor下でbad simulated path反例なし |
| A3 | 到達可能 | 不達 | 対象なし | 不達 | unsupported | download-onlyでArtifact依存read/useなし |
| A4 | 到達可能 | 到達可能 | 対象なし | 不達 | unsupported | Artifact利用とsink/siteを分離できた |
| A5 | 到達可能 | 到達可能 | 対象なし | metadata/output依存の`bad_simulated_path`反例あり | unsupported | 条件付きdummy update marker到達．実repository writeではない |

全ケースで`AG (artifact_dependent_use -> download_success_any)`と`AG (artifact_dependent_sink -> artifact_dependent_use)`はtrueだった．A1／A5は`AG !bad_simulated_path`がfalse，A2～A4はtrue．ただしA2～A4でもreal-authority propertyはsink contract欠如により評価範囲外である．

代表counterexample：

- A1：[trace（archive内 cases/A1.cex.AG_bad_simulated_path.trace.txt）](../../outputs/model-v2/archive/prototype-2026-10-22.tar.gz)／[source map（archive内 cases/A1.cex.AG_bad_simulated_path.source-map.json）](../../outputs/model-v2/archive/prototype-2026-10-22.tar.gz)．SMVはPR trigger，path-filter unknown=true，Producer各step outcome=success，upload object生成，Producer terminal success，`workflow_run: completed` Consumer開始，download success，payload=`publish_true`，simulated marker=trueを選ぶ．step PCはProducer行25／31／37／47，Consumer行32／42へ対応する．
- A2：[mismatch／blocked trace（archive内 cases/A2.cex.AG_integrity_blocked.trace.txt）](../../outputs/model-v2/archive/prototype-2026-10-22.tar.gz)．`consumer_workflow_trusted=TRUE`，selected payload=`publish_true`でmismatch，output false，guardで後続利用stepをskipしblockedを立てる．反対にpayload=`publish_false`ならverified outputから後続stepへ進めるが，publish=true sink条件にはならない．
- A5：[trace（archive内 cases/A5.cex.AG_bad_simulated_path.trace.txt）](../../outputs/model-v2/archive/prototype-2026-10-22.tar.gz)／[source map（archive内 cases/A5.cex.AG_bad_simulated_path.source-map.json）](../../outputs/model-v2/archive/prototype-2026-10-22.tar.gz)．`producer_metadata_branch=branch_other`，`producer_metadata_sha=sha_other_valid`，両outputが同じdownloaded metadataから保存され，Artifact file test成功後に固定dummy markerへ到達する．step PCはProducer行18／25／33／42，Consumer行24／33／59へ対応する．

traceのunknown割当ては許容されたFROZENVAR／IVAR値である．その具体値が実GitHub上で起きたという意味ではない．

## 5. Simulated sink・権限・実権限

- `simulated_sink_site_reached`はmarkerを含むstepが実行されたことを表す．A2では条件値falseでもecho行は実行されるためsite到達がある．
- `artifact_dependent_sink`は認識したprovenanceがsink条件へつながった場合にのみ立てる．固定文字列markerだけではこのstateを立てない．
- A1の`dummy_publish_authority_reached`とA5の`dummy_repository_update_reached`は研究用markerであり，実publish／repository updateではない．
- workflow permissions宣言，scope別のeffective permission，site到達，operation成功を分離した．A1/A5のconsumerはcontents writeを宣言していない．Artifact取得の`actions: read`はrepository contents writeではない．
- `real_authority_sink_site_reached`と`authority_operation_succeeded`を更新するreal sink contractは未登録．そのため`AG !bad_real_authority_path`のtrueを実侵害不成立や安全証明とは読めない．manifestには全基準ケースで`real-authority-sink-contract-missing`が出る．

GitHub公式仕様ではjob／workflow `permissions`がGITHUB_TOKENへ適用され，repository／organizationのdefaultも影響する．今回の有限モデルはYAML明示値だけを固定し，organization policy，fork設定，実token，Action内部の追加権限利用は観測しない．[permissions仕様](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#permissions)を参照．`workflow_run`はworkflow fileがdefault branchにある場合だけ起動し，実行chain深度にも制限があるが，現在の状態機械はdefault-branch配置やchain深度までは検査しない．[workflow_run公式仕様](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run)を参照．

## 6. Mutation test結果

全mutationは元YAMLを変更せず，`outputs/model-v2/mutations/<id>/inputs/`にコピーしたYAMLを使った．failure/digest-domainの一部はYAML編集ではなくmanifestに記録したSMV input restrictionである．各caseの`input.diff`と`result.json`に差分／制約を保存した．

| Mutation | 観測結果 |
|---|---|
| download-disabled | Download／use／sink不達． |
| use-disabled | Download到達，Artifact依存use／sink不達． |
| sink-disabled | Download／use到達，simulated site／sink不達． |
| upload-name-mismatch | 対応object候補なし，Download不達． |
| workflow-run-mismatch | Consumer triggerが不成立，Download不達． |
| run-id-mismatch | Download不達，dynamic-artifact-input unsupported． |
| upload-failure | `TRANS outcome=<upload>=failure`制約下でDownload不達． |
| download-failure | `TRANS outcome=<download>=failure`制約下でuse不達． |
| if-false-upload | upload stepの`if: false`でupload／download不達． |
| same-output-reference-twice | 同一output symbolを2回参照し，条件相関を維持．useは到達，dependent sinkは不達． |
| contradictory-same-output | 同一outputがtrueかつnot-trueを要求するためuse不達． |
| integrity-check-disabled | Download到達，outputがunsetのためuse guardが通らずuse不達． |
| digest-success | trust=true／payload=false制約でverifiedとuse到達，publish sink不達． |
| digest-mismatch | trust=true／payload=true制約でmismatch／blocked到達，use不達． |
| a5-invalid-metadata | download／read siteは到達するが，metadata由来dependent sinkは不達． |
| a5-output-reference-changed | output参照をliteralへ置換．dependency contract unsupported，dependent sink不達． |
| unrelated-fixed-marker | 無関係なmarker siteは観測するがArtifact dependencyは作らない．unsupported診断あり． |
| unsupported-shell | property関連Shellに`eval`を挿入．whole-script parseがunsupportedとなり，安全扱いしない． |
| unregistered-action-ref | pinned action refを未登録refへ置換．unsupported，upload／download結合不成立． |
| permission-missing | explicit `actions: read`を除去．Download／use不達． |
| nusmv-invalid-input | SMV末尾を意図的に壊し，NuSMV batch exit code 1とsyntax errorを確認．propertyなしをsafe扱いしない． |

成功・失敗mutationはSMV内のstep outcomeを制約するため，実Actionの故障を再現したものではない．全21シナリオは一意で，件数を増やす重複入力はない．

## 7. unknown／unsupported

基準A1～A4では主に`action-outcome`（upload／download成功はruntime不明）2件と`path-filter` 1件がunknown．A5はpath filterがないためaction outcome 2件がunknown．全ケースにreal-authority-sink-contract-missingがunsupportedで残る．mutationによりdynamic run-id，unsupported Shell，unregistered action，output-flow不一致などの診断が追加される．全diagnosticは各manifestに保存．

同じoutput／Artifact slotは共有stateを参照し，相関を保つ．`unknown`をtrue/falseへ先に決めず，SMV入力・finite domainとして扱う．一方，Action outcome同士の実行時相関，実artifact ID，GitHub設定，任意Shell，複数job value flowはまだ抽象化外である．unsupported caseのCTL propertyがtrueでも安全とはしない．

## 8. NuSMV・初期状態・独立検証

- 5基準＋21 mutationの計26モデルをNuSMV batch modeで実行．25モデルはexit code 0，意図的syntax error 1件はexit code 1．各stdout／stderr／exit codeを保存．加えて，固定シナリオのBFS対照SMVをA1/A3の2件実行し，両方exit code 0．
- 有効な25モデルで`NuSMV -ctt`がexit code 0．到達可能状態数も各モデルで1以上：基準A1 14,418，A2 31,158，A3 14,904，A4 14,418，A5 15,066．全モデルの値はverification summaryと`*.reachable.txt`に保存．
- `AG (download_succeeded -> download_object != no_object)`は全基準でtrue．`AG (artifact_dependent_use -> download_success_any)`と`AG (artifact_dependent_sink -> artifact_dependent_use)`も全基準でtrue．
- A2の`AG (integrity_blocked -> !artifact_dependent_use)`はtrue．A2のverified／mismatch／blocked到達性もそれぞれ確認．
- 反例ごとにNuSMV traceを保存し，job PCのactive step enumをmanifestのstep symbolへ照合してYAML spanを出した．counterexample source mapは基準A1/A5および他の失敗propertyにも生成．
- NuSMV interactive modeで意図的syntax-errorモデルを調べた際は，interactive wrapperがexit 0でもsyntax errorをstderrへ出しstate countを出さなかった．このcaseはbatch modeのexit 1／Parser errorを正式結果とし，初期状態検査対象から除外した．
- 独立Python BFSはA1/A3のdownload/use部分へ限定して実装した．BFSはYAML上の逐次job／step，固定scenario（PR trigger，path-filter=true，step outcome success，workflow_run completed），upload/download条件，Artifact file-readを別の5-field stateとして探索する．A1はDownload/useともreachable，A3はDownload reachable／use unreachable．同じscenarioをSMVのINVAR／TRANSに固定したNuSMV結果と，両caseのreachable／unreachable propertyが一致した（A1 BFS projected states 11，NuSMV全state 2,592；A3は12対2,754）．状態数そのものは，NuSMVが全variableを数え，BFSは5-field projectionを数えるため直接比較していない．比較結果は`outputs/model-v2/bfs/verification-summary.json`に保存．
- BFSはintegrity，sink，payload domain，runtime ID，GitHub実行環境を扱わず，A2/A4/A5をcross-checkしていない．従って独立検証は範囲限定であり，全stateful modelの独立照合は未達である．

## 9. コンテナ環境

コード実行，18件のunittest，モデル生成，NuSMV検査，集計をDocker内に限定した．コンテナは非root UID 501:20，`--network none`，read-only root filesystem，`--cap-drop ALL`，`no-new-privileges`，tmpfs `/tmp`，repo read-only mount，`outputs/model-v2`のみ書込みmount．Docker socket，credential，host portは渡していない．

| 項目 | 記録 |
|---|---|
| Docker Engine | 29.6.2，Docker Desktop，host architecture aarch64 |
| Python image | `ubuntu-dev:latest`，`sha256:41b49010e550155658f6f3c45cd6b10e1a555b6fa9892b28f3b8b7c5819ce397`，linux/arm64 |
| Python / YAML | Python 3.12.3，PyYAML 6.0.1．詳細は`outputs/model-v2/runtime-environment.txt` |
| NuSMV image | `badouralix/nusmv:latest`，`sha256:96ac90ba3ae63f9423ad26611ac4c6f8774fe383baff73bc3fbc09456246992e`，linux/amd64をarm64上で実行 |
| NuSMV | 2.6.0（2015 build）．全検査で既存imageを使用．version bannerとstdout/stderrは保存済み |

## 10. 未実装・完了基準

次は未実装または未検証である：

1. 実際のNuSMV Artifact object ID／Producer run ID／Consumer run IDの同一性確認．現在は抽象候補slot．
2. 実bytesを使ったSHA-256再計算と暗号学的なtrust rootの自動証明．A2は有限値relationと明示trust仮定．
3. 実権限を必要とするAction／Shell/API sinkのcontract，effective token policy，操作成功．そのためreal authority propertyはunsupported．
4. A5 simulated markerを実repository writeへ接続すること．実操作は対象外．
5. A1/A3のdownload/use projection以外（特にA2 integrityとA1/A5 sink）の独立BFS照合．
6. GitHub-hosted／self-hosted runner差，repository/organization policy，fork／Dependabot token制限，`workflow_run` default-branch配置とchain深度など全仕様条件．公式仕様にない意味論は推定しない．
7. 複数jobを横断するArtifact/value状態，任意Shell，動的path／name／run-id，Action内部実装の意味解析．

したがって完了基準のうち，A1～A5の有限prototype再現，A4のuse／sink分離，unknown／unsupportedの明示，source mapping，主要mutationの感度，A1/A3のdownload/use独立照合は確認できた．しかし，実権限sinkとruntime identityを含む研究目標，integrity／sinkを含む独立BFS照合は未達である．A1/A5のcounterexampleは生成抽象モデル上の条件付きsimulated pathであり，実被害ではない．

## 11. 従来方式との比較

従来の`results/core-artifact-subset/analysis.json`はartifact dummy-publish subsetの静的事実列を作り，save/read success，same object，authority等のunknownをシナリオ条件に固定してpropertyへ渡す．その結果は既存研究用の比較結果であり，今回変更していない．

v2ではjob／step PC，Artifact upload／download state，有限payload／metadata，検証output，sink-site stateをSMVへ渡す．そのためA1の「PR入力→upload→workflow_run完了→download→payload read→dummy marker」，A2のverification→guard，A3のdownload-only，A4のread-only，A5のmetadata→output→dummy markerを順序付きtraceで説明できる．同じoutput参照の相関も共有symbolで残す．

ただしv2もGitHub Actions全体の実行意味論ではない．Action outcomeやtrigger filterはunknown input，値とdigestは有限抽象，Artifact同一性はcandidate slot，real sinkはunsupportedである．従来方式との結果一致を正当性基準にはせず，SMV trace上で何が仮定され，どの状態が更新されたかを比較材料として保存した．

## 12. git diff --stat／終了時git status

終了時に再取得した正確な結果を以下に示す．新規ファイルは未追跡のまま保持し，stage／commitしない．

```text
git diff --stat:
 .github/workflows/artifact-a1-pr-producer.yml     | 14 +++++++-------
 .github/workflows/artifact-a1-unsafe-consumer.yml | 11 ++++++-----
 2 files changed, 13 insertions(+), 12 deletions(-)

git status --short --branch:
## research/automatic-temporal-verification...origin/research/automatic-temporal-verification
 M .github/workflows/artifact-a1-pr-producer.yml
 M .github/workflows/artifact-a1-unsafe-consumer.yml
?? docs/next-model-design/
?? outputs/
?? tests/test_workflow_model_v2.py
?? tests/test_workflow_to_smv.py
?? tools/workflow_model_v2/
?? tools/workflow_shell_subset.py
?? tools/workflow_to_smv.py
?? tools/workflow_to_smv_v2.py
```

2 YAMLの変更は作業開始時からの差分である．`git diff --stat` はtracked fileのみを表示するため，新規実装・報告・出力が含まれない．それらは上記statusのとおり未追跡で保持している．
