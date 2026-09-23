# 研究引き継ぎ資料

更新日：2026年9月23日

## 1．この資料の目的

この資料は，CI/CDパイプラインの信頼境界を形式的に検証する研究を，初めて見る人でも現在地点から継続できるようにまとめたものである．研究目的だけでなく，実装の役割，検証済みの事実，まだ人手に依存する部分，安全上の制約及び次に行う作業を区別して記載する．

## 2．研究テーマ

研究テーマは，CI/CDパイプラインにおける信頼境界の形式的検証である．主な対象は，未信頼なPull Request又はMerge Requestが作ったキャッシュや成果物を，後続の権限を持つ処理が検証せずに利用する構成である．

既存の静的解析は，危険な起動契機，Actionの使い方又は既知の危険パターンを警告できる．しかし，未信頼な共有物が別のworkflow又はrunへ渡り，どの処理で利用され，最終的に公開，デプロイ，リポジトリ更新又はsecret利用へ到達するかを，複数workflowにまたがる状態遷移として常に示すわけではない．

本研究では，静的解析から得た構造，実行時の観測及び人手で与えた意味を有限状態モデルへ変換し，未信頼な共有物が権限へ到達可能かをNuSMVで検査する．

## 3．現在の研究上の主張

現時点で確認できたのは，次の限定された主張である．

1. CodeQLからGitHub Actionsの起動契機，job，step，Action及び引数を抽出できる．
2. 成果物名，起動元runの指定及び`workflow_run`の対象名から，成果物の保存側と取得側を自動的に対応付けられる．
3. 実行結果と人手注釈を加えた信頼経路JSONから，NuSMV用の状態遷移モデルを生成できる．
4. 研究用のA1からA5では，期待した危険・安全の分類とNuSMVの結果が5件全て一致した．
5. A3，A4及びA5では，GitHub Actions上の実行結果とNuSMVの遮断又は到達結果が一致した．

これは一般的な検出精度を示す結果ではない．評価対象は小規模な5構成であり，シェルの意味には人手判断が残る．

## 4．絶対に混同しない4種類の情報

| 区分 | 意味 | 例 |
| --- | --- | --- |
| 静的構造 | YAMLへ記述された内容 | `upload-artifact`，成果物名，`workflow_run` |
| 実行時観測 | GitHub Actionsで実際に起きたこと | 保存成功，同じartifact IDの取得 |
| 人手判断 | 現在の解析で自動化できていない意味 | 成果物利用，完全性確認，権限到達 |
| 形式検証による推論 | 与えたモデル内で到達可能か | NuSMVの反例あり又はなし |

CodeQLが保存Actionを見つけても，保存成功を証明したことにはならない．NuSMVが安全と判定しても，入力した事実又はモデル化していない挙動まで安全と証明したことにはならない．

## 5．リポジトリ

- GitHub：`https://github.com/formal-ci-cd/trust-boundary-verification`
- 既定ブランチ：`main`
- 研究用Organization：`formal-ci-cd`
- 主なマージ済みPR：#1から#11
- A3・A4・A5の追加：PR #10
- A5の実行結果：PR #11

ローカルの通常checkoutには，2026年9月23日時点で日本語コメント追加などの未コミット変更があり，`main`が`origin/main`より古い状態であった．既存変更を失わないよう，最新の`origin/main`から別worktree及びブランチで本資料を作成した．通常checkoutへ無理にpull又はresetしてはいけない．

## 6．最初に読むファイル

1. `README.md`：研究目的とリポジトリ全体．
2. `docs/codeql-to-nusmv-guide.md`：実際のコード例を使った変換処理の説明．
3. `docs/threat-model.md`：想定する攻撃者と信頼境界．
4. `docs/experiment-matrix.md`：実験構成．
5. `model/README.md`：共通JSONとNuSMVモデル．
6. `results/artifact-case-study-2026-09-23.md`：A3からA5の最新結果．
7. `results/github-actions-artifact-runtime-2026-08-22.md`：A1及びA2の実行結果．

## 7．ディレクトリの役割

| 場所 | 内容 |
| --- | --- |
| `.github/workflows/` | CodeQL，actionlint，zizmor及びA1からA5の実験workflow |
| `codeql/queries/` | GitHub Actionsの構造を抽出する独自CodeQL query |
| `tools/` | CSV，JSON，SMV間の変換及び評価script |
| `model/examples/` | ワークフロー単位の共通JSON例 |
| `model/generated-chains/` | A1からA4の自動生成済み信頼経路JSON |
| `model/case-studies/` | 実在脆弱性を簡略化したA5のモデル |
| `model/observations/` | GitHub Actionsの実行時証拠 |
| `model/nusmv/` | 生成したSMVモデル |
| `results/` | 実験結果，反例対応表及び評価結果 |
| `tests/` | 変換ロジックの単体テスト |

## 8．CodeQLからNuSMVまでの実装

### 8.1 全体フロー

```text
YAML
  → CodeQL Extractor
  → CodeQL DB
  → WorkflowStructure.ql
  → BQRS
  → CSV
  → codeql_csv_to_model.py
  → workflow単位JSON
  → discover_artifact_chains.py
  → 信頼経路JSON
  → chain_to_nusmv.py
  → SMV
  → NuSMV
```

### 8.2 `WorkflowStructure.ql`

`import actions`でGitHub Actions用のCodeQLライブラリを読み込む．CodeQL ExtractorがYAMLから作った内部表現を，`Workflow`，`Event`，`Job`，`UsesStep`，`Run`などの型で検索する．

`describeNode`は，型ごとに`kind`，`name`，`detail`を決める．例えば`UsesStep`はAction名とバージョンを`uses-step`として出し，研究上必要な`with:`引数を`uses-argument`として出す．最後の`select`で，ファイル，行，ワークフロー名，job ID，step番号を加えた表にする．

このqueryは危険性を判定しない．保存・取得の記述と位置を出すだけである．

### 8.3 `codeql_csv_to_model.py`

CSVをワークフロー名で絞り，`(jobId, stepIndex)`が同じ行を1個のstepへ戻す．Action名が`actions/upload-artifact`なら`artifactWriteIntent`，`actions/download-artifact`なら`artifactReadIntent`を作る．

`Intent`は記述の存在であり，実行時の許可又は成功ではない．静的解析だけで不明な検証事実は`unknown`にする．

### 8.4 `discover_artifact_chains.py`

次の3条件を満たす保存側と取得側を結合する．

1. 成果物名が一致する．
2. 取得側の`run-id`が`github.event.workflow_run.id`である．
3. `workflow_run.workflows`が保存側のワークフロー名を指定する．

候補が複数ある場合は`ambiguous`として停止する．結合後，`model/artifact-chain-annotations.json`から実行結果と人手判断を加える．全モデル要素に情報源が1件ずつあることも検査する．

### 8.5 `build_trust_chain.py`

保存側，取得側，共有成果物，検証用の真偽値及び反例位置を1個の信頼経路JSONへまとめる．`producerUntrusted`は保存側eventの外部起動可能性，`privilegedConsumer`は取得側eventの特権分類から機械的に求める．`hasAuthority`は実際に被害相当の処理へ進めるかを表す別の値である．

### 8.6 `chain_to_nusmv.py`

`true`と`false`はSMVの`DEFINE`へ，`unknown`は`FROZENVAR`へ変換する．状態は次の7個である．

```text
start
object_written
object_restored
integrity_checked
object_used
authority_reached
blocked
```

必要条件がそろえば次の状態へ進み，そろわなければ`blocked`へ進む．成果物は未信頼producerから作られた時点で`object_tainted=true`となり，完全性確認が成功した場合だけ`false`になる．

検査式は次の通りである．

```smv
CTLSPEC AG !(stage = authority_reached & object_tainted)
```

意味は，「どの経路でも，未信頼な成果物のまま権限へ到達しない」である．違反する場合，NuSMVは到達までの状態列を反例として返す．

## 9．現在の実験構成

| 構成 | 役割 | 実行結果 | NuSMV |
| --- | --- | --- | --- |
| GHA-A1 | 完全性確認なしで利用し，模擬公開へ到達 | 到達を確認 | 反例あり |
| GHA-A2 | digest不一致時に利用前で遮断 | 遮断を確認 | 反例なし |
| GHA-A3 | 取得するが内容を利用しない | 利用なしを確認 | 反例なし |
| GHA-A4 | 内容は読むが権限を持たない | 権限なしを確認 | 反例なし |
| GHA-A5 | `actions/attest`の成果物汚染を安全に簡略化 | 模擬更新地点への到達を確認 | 反例あり |

A1からA4は，条件を1個ずつ変えた研究用の最小比較構成であり，特定事件の忠実な再現ではない．A5はGitHub Security Labが報告した`GHSL-2026-225`の構造を簡略化した．公開情報から実際の悪用は確認できないため，実在脆弱性であり，実被害のあった事件とは表現しない．

Ultralyticsは実際に悪用された事例だが，攻撃対象はGitHub Actionsのキャッシュであり，成果物を扱うA1からA5とは区別する．

## 10．最新の実行証拠

### A3及びA4

- producer run：`35823220725`
- artifact ID：`10734380218`
- archive digest：`sha256:e0d76073d3310001d32dcbba62fc8f89317ee6bdc10a278b3ea1af3f796624f6`
- A3 run：`35823235871`
- A3：`artifact_used=false`，`artifact_value_forwarded=false`
- A4 run：`35823235868`
- A4：`artifact_value=publish=true`，`authority_available=false`

根拠は`model/observations/gha-a3-a4-runtime-pr10.json`にある．

### A5

- producer run：`35824000044`
- consumer run：`35824013582`
- artifact ID：`10734401185`
- archive digest：`sha256:1bee486f09a3e2a039e7552dc7007bfada223b32d7aadde863c27fd9abe18c0f`
- `artifact_target_branch=main`
- `integrity_verified=false`
- `dummy_repository_update_reached=true`

本物の被害を防ぐため，実際の権限は`contents: read`であり，checkout，commit及びpushは行っていない．根拠は`model/observations/gha-a5-runtime-pr11.json`にある．

## 11．既存静的解析との関係

- CodeQL標準queryは，A1型の危険なキャッシュ又は成果物の扱いを既知パターンとして警告できる場合がある．本研究の独自queryは警告用ではなく，構造抽出用である．
- zizmorは，危険な起動契機，固定されていないAction参照，資格情報の保持などを検査する．今回の成果物の最終到達先を追う専用検査ではない．
- actionlintは，構文，式，Actionの入出力及びシェルなどの誤りを検査する．複数run間の信頼経路をモデル検査するものではない．

## 12．安全上の制約

- 実在するsecret，公開token，deploy key又はパッケージ公開権限を使わない．
- 本物の`contents: write`，commit，push，deploy又はpublishを行わない．
- 攻撃payloadの代わりに`publish=true`，dummy file及び模擬到達markerを使う．
- self-hosted runnerを使わない．
- 実験を拡張するときも，実被害のない模擬到達で止める．

## 13．テストと再現

Pythonの単体テストは次で実行する．

```bash
python3 -m unittest discover -s tests -v
```

成果物経路を再生成する．

```bash
python3 tools/discover_artifact_chains.py \
  results/codeql-actions-model-v2.26.3.csv \
  --annotations model/artifact-chain-annotations.json \
  --catalog /tmp/artifact-chain-candidates.json \
  --output-dir /tmp/generated-chains
```

SMVを生成してNuSMVで検査する．

```bash
python3 tools/chain_to_nusmv.py \
  model/generated-chains/gha-a1-auto.json \
  --output /tmp/gha-a1-auto.smv

NuSMV /tmp/gha-a1-auto.smv
```

A1からA5の一括評価設定は`model/artifact-chain-evaluation.json`にある．

## 14．既知の制約及び注意すべき実装

1. 同じ`run:` stepから複数のコマンドが抽出されると，`codeql_csv_to_model.py`は同じキーを上書きし，共通JSONには最後の1行だけを残す．CSV自体には全行がある．
2. `upload-artifact`及び`download-artifact`をAction名の完全一致で探す．ラッパーAction又は独自Action内部の操作は対象外である．
3. `sameObject=true`は静的な3条件の一致であり，実行時のartifact ID一致とは異なる．
4. `producerUntrusted`と`privilegedConsumer`はCodeQLのevent分類に基づく．実際の細かなtoken権限は別途確認する．
5. `consumerUsesObject`，`integrityCheckPresent`及び`hasAuthority`には人手判断が残る．
6. A5はCodeQLの最新CSVから自動生成したモデルではなく，人手で作ったcase studyモデルである．
7. `model_to_nusmv.py`は初期のキャッシュ実験用であり，成果物の複数workflow経路では`chain_to_nusmv.py`を使う．

## 15．次に行う作業

優先度の高い作業は次の2点である．

### 15.1 A5をCodeQLから再抽出する

現在のCSVはA5追加前の状態である．最新の`main`からCodeQLデータベースとCSVを再生成し，A5の保存側と取得側が現在の3条件で自動結合されるか確認する．その後，人手作成のA5モデルと，自動生成した構造を項目単位で比較する．

完了条件は次の通りである．

- A5の`upload-artifact`と`download-artifact`がCSVに存在する．
- 保存側と取得側が一意に結合される．
- workflow，job，step，成果物名及びrun IDの対応が元YAMLと一致する．
- 追加する人手注釈と自動抽出部分が明確に分離される．
- 生成SMVの反例が既存A5モデルと同じ状態列になる．

### 15.2 シェルの意味付けをどこまで自動化できるか調査する

まず`run:`の複数コマンドを上書きせず配列で保持する．その後，次の限定パターンから始める．

- 取得した成果物パスを`cat`，`tr`，`cp`又は変数代入で読む処理．
- `sha256sum`等の計算値を信頼済みの期待値と比較する処理．
- `git push`，release作成，package publish又はdeploy Actionへ成果物由来の値を渡す処理．

完全なシェル意味解析を一度に目指さず，自動判定できた項目と人手判断のままの項目を情報源付きで区別する．

## 16．面談資料

2026年9月24日の面談資料は次にある．

```text
/Users/bob/Documents/GitHub/IISEC/99_研究関連/面談資料/9:24/924.tex
/Users/bob/Documents/GitHub/IISEC/99_研究関連/面談資料/9:24/924.pdf
```

資料には，変換フロー，検出対象，実在事例，A1からA5の位置付け及び結果を記載している．TeXはユーザーが文言を調整しているため，編集時は必ずその時点の最新版から差分を作り，過去のコピーで上書きしない．句読点は原則として`，．`を使用する．

## 17．引き継ぎ後の判断基準

- 「抽出した」「実行で観測した」「人手で判断した」「モデルから推論した」を文章と表で分ける．
- 実行成功だけで変換の正しさを主張せず，中間CSV，JSON，SMV及び元YAMLを対応付ける．
- `unknown`を`false`として安全側へ処理しない．
- 小規模な5構成の一致を一般的な検出率又は正確性として表現しない．
- 実在脆弱性と実際に悪用された事件を区別する．
- 実験の安全境界を広げず，模擬権限到達で止める．
