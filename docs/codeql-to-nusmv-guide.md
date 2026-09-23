# CodeQLからNuSMVまでの変換を理解するための資料

## 1．最初に結論

この実装は，CodeQLの警告をSMVへ変換しているわけではない．次の4種類の情報を1本の信頼経路へまとめ，有限状態モデルへ変換している．

| 情報 | 例 | 情報源 |
| --- | --- | --- |
| ワークフローの静的構造 | `pull_request`，`workflow_run`，`upload-artifact`，成果物名 | CodeQL |
| 実行時の事実 | 保存成功，取得成功，同じartifact ID | GitHub Actionsのrun |
| 現在は人手で判断する意味 | 成果物を利用する処理，完全性確認，権限到達 | 注釈JSON |
| 検証規則 | どの条件で次の状態へ進むか | Pythonが生成するSMV |

全体の流れは次の通りである．

```text
GitHub ActionsのYAML
  ↓ CodeQL Extractor
CodeQLデータベース
  ↓ WorkflowStructure.ql
BQRS → CSV
  ↓ codeql_csv_to_model.py
ワークフロー単位のJSON
  ↓ discover_artifact_chains.py + artifact-chain-annotations.json
保存側と取得側を結んだ信頼経路JSON
  ↓ chain_to_nusmv.py
SMVモデル
  ↓ NuSMV
安全性の判定と反例
```

以下では，GHA-A1を例に，実際のコードが何をしているかを順番に説明する．

## 2．入力となるGHA-A1

保存側は，未信頼なPull Requestのファイルを成果物として保存する．

```yaml
name: GHA-A Producer - untrusted PR artifact
on:
  pull_request:

steps:
  - uses: actions/upload-artifact@...
    with:
      name: trust-boundary-build-output
      path: .research-artifact-input/payload.txt
```

取得側は，保存側の完了後に起動し，起動元runの同名成果物を取得する．

```yaml
name: GHA-A1 Unverified artifact consumer
on:
  workflow_run:
    workflows: ["GHA-A Producer - untrusted PR artifact"]
    types: [completed]

steps:
  - uses: actions/download-artifact@...
    with:
      name: trust-boundary-build-output
      run-id: ${{ github.event.workflow_run.id }}
```

この2ファイルが同じ成果物を受け渡すことを，後段で機械的に対応付ける．

## 3．`WorkflowStructure.ql`のロジック

### 3.1 CodeQLライブラリがすでにYAMLを型付き要素へ変換している

```ql
import actions
```

`actions`ライブラリを読み込むと，単なるYAML文字列ではなく，`Workflow`，`Event`，`Job`，`UsesStep`，`Run`，`Permissions`などの型として検索できる．`WorkflowStructure.ql`は，これらの型を研究用の表へ投影するクエリである．

### 3.2 各要素へ所属情報を付ける

例えば，Actionの引数だけを取り出しても，どのワークフロー，job，stepに属するか分からない．そこで補助関数が所属情報を付ける．

```ql
private string getWorkflowName(AstNode node) {
  result = node.getEnclosingWorkflow().getName()
}

private string getJobId(AstNode node) {
  node instanceof Job and result = node.(Job).getId()
  or
  not node instanceof Job and
  result = node.getEnclosingJob().getId()
  or
  not exists(node.getEnclosingJob()) and result = "-"
}
```

QLの`or`は，いずれかの条件を満たす結果を列挙する．`getJobId`は，対象自身がjobならそのID，stepなどjob内の要素なら親jobのID，job外なら`-`を返す．`getStepIndex`も同様に，親の`StepsContainer`から0始まりの位置を求める．

### 3.3 型ごとに`kind`，`name`，`detail`へ変換する

中心となるのは`describeNode`である．1個のAST要素を，表へ出すための3項目へ変換する．

```ql
private predicate describeNode(
  AstNode node, string kind, string name, string detail
) {
  exists(UsesStep step |
    node = step and
    kind = "uses-step" and
    name = getStepId(step) and
    detail = step.getCallee() + "@" + step.getVersion()
  )
  or
  exists(UsesStep step, string argumentName |
    node = step and
    argumentName = getModelArgumentName() and
    exists(step.getArgument(argumentName)) and
    kind = "uses-argument" and
    name = argumentName and
    detail = step.getArgument(argumentName)
  )
}
```

同じ`UsesStep`から，Action本体を示す`uses-step`行と，`name`や`path`などを示す`uses-argument`行が複数出る．引数は何でも出すのではなく，`getModelArgumentName()`に列挙した研究上必要な名前だけを対象にする．

起動契機については，CodeQLライブラリの判定を文字列へ変換する．

```ql
private string getEventTrust(Event event) {
  event.isExternallyTriggerable() and
  result = "externally-triggerable"
  or
  not event.isExternallyTriggerable() and
  result = "not-externally-triggerable"
}
```

ここで`pull_request`は外部から起動可能，`workflow_run`は特権を持ち得る起動契機として抽出される．これはCodeQLライブラリの分類であり，実際のjobが`contents: write`を持つことまで示すものではない．

### 3.4 最後に全要素を表として出す

```ql
from AstNode node, string filePath, int line,
     string workflowName, string jobId, string stepIndex,
     string kind, string name, string detail
where
  describeNode(node, kind, name, detail) and
  filePath = node.getLocation().getFile().getRelativePath() and
  line = node.getLocation().getStartLine() and
  workflowName = getWorkflowName(node) and
  jobId = getJobId(node) and
  stepIndex = getStepIndex(node)
select filePath, line, workflowName, jobId, stepIndex,
       kind, name, detail
```

`from`は候補となる変数，`where`は成立すべき条件，`select`は出力列である．つまり，`describeNode`で説明できる全要素について，元ファイルの位置と所属情報を付けて1行ずつ出す．

GHA-A1の保存stepは，CSVでは概ね次の複数行になる．

```text
jobId            step  kind           name  detail
produce-artifact 2     uses-step      ...   actions/upload-artifact@...
produce-artifact 2     uses-argument  name  trust-boundary-build-output
produce-artifact 2     uses-argument  path  .research-artifact-input/payload.txt
```

重要なのは，この時点では「危険」「保存成功」と判定していないことである．YAMLに何が記述されているかを表へ変換しただけである．

## 4．CSVをワークフロー単位のJSONへ戻すロジック

`codeql_csv_to_model.py`は，CSVの独立した行を，`(jobId, stepIndex)`をキーにして同じstepへ戻す．

```python
step = steps.setdefault(
    (job_id, step_index),
    {
        "index": int(step_index),
        "type": "uses",
        "action": "",
        "version": "",
        "arguments": {},
    },
)
step["arguments"][row["name"]] = row["detail"]
```

`setdefault`は，そのstepがまだなければ箱を作り，すでにあれば同じ箱を返す．そのため，別々のCSV行にある`uses-step`，`name`，`path`を1個のstepへまとめられる．

次に，Action名の完全一致で，共有状態に対する操作へ変換する．

```python
if step.get("action") == "actions/upload-artifact":
    shared_state_operations.append(
        {
            "id": f"artifact-write:{job['id']}:{step['index']}",
            "kind": "artifactWriteIntent",
            "jobId": job["id"],
            "stepIndex": step["index"],
            "path": step["arguments"].get("path"),
            "name": step["arguments"].get("name"),
        }
    )
```

先ほどのCSVは，次のJSONになる．

```json
{
  "id": "artifact-write:produce-artifact:2",
  "kind": "artifactWriteIntent",
  "jobId": "produce-artifact",
  "stepIndex": 2,
  "name": "trust-boundary-build-output",
  "path": ".research-artifact-input/payload.txt"
}
```

`Intent`は，保存操作が記述されているという意味である．GitHubが保存を許可したことは`writeAuthorized`，実際に成功したことは`writeSucceeded`として別に扱う．

## 5．保存側と取得側を結合するロジック

`discover_artifact_chains.py`は，全ワークフローから`artifactWriteIntent`と`artifactReadIntent`を集め，取得側ごとに保存側候補を探す．

```python
if not has_event(consumer_model, "workflow_run"):
    continue
if normalize_expression(read.get("runId")) \
        != "github.event.workflow_run.id":
    continue

matches = [
    (producer_model, write)
    for producer_model, write in writes
    if write.get("name") == read.get("name")
    and producer_model["workflow"]["name"] in targets
]
```

結合条件は次の3個である．

1. 保存側と取得側の成果物名が同じである．
2. 取得側の`run-id`が`github.event.workflow_run.id`である．
3. `workflow_run.workflows`の対象名が保存側のワークフロー名と同じである．

A1では，成果物名`trust-boundary-build-output`，起動元run，起動元ワークフロー名が一致するため，保存step 2と取得step 0が結ばれる．一致候補が複数ある場合は`ambiguous`とし，勝手に1個を選ばない．

ここで設定される`sameObject=true`は，3個の静的条件が一致したという意味である．実行時に同じartifact IDだったという意味ではない．artifact IDの一致は実行記録で別に確認する．

## 6．CodeQLだけで決まらない値を加える

CodeQLがシェルコマンドを抽出しても，次の意味までは現在の実装で自動判定していない．

- `payload.txt`の値を後続判断へ利用しているか．
- SHA-256の比較を完全性確認として扱えるか．
- どのstepを公開，デプロイ又はリポジトリ更新への到達とみなすか．

そこで`model/artifact-chain-annotations.json`に，値，情報源，判断根拠を記録する．

```json
"consumerUsesObject": {
  "value": "true",
  "sourceKind": "runtime-observation",
  "source": "model/observations/gha-a-runtime-pr7.json",
  "claim": "取得したpublish=trueが模擬公開判断に使われた．"
},
"integrityCheckPresent": {
  "value": "false",
  "sourceKind": "manual-judgment",
  "source": ".github/workflows/artifact-a1-unsafe-consumer.yml",
  "claim": "利用前にdigest比較を行うstepがない．"
}
```

情報源は`static-analysis`，`runtime-observation`，`manual-judgment`に分ける．`false`は不成立を確認した値，`unknown`は未確認の値であり，同じ意味ではない．

`build_trust_chain.py`は，静的構造と注釈を次の`facts`へまとめる．

```text
producerUntrusted       未信頼な起動元か
writeIntent             保存処理が記述されているか
writeAuthorized         保存が許可されたか
writeSucceeded          保存に成功したか
sameObject              同じ成果物として結合できたか
readSucceeded           取得に成功したか
consumerUsesObject      内容を後続処理が利用するか
integrityCheckPresent   完全性確認が存在するか
integrityCheckPassed    完全性確認に成功したか
privilegedConsumer      特権を持ち得る起動文脈か
hasAuthority            被害に相当する処理へ進めるか
```

同時に`traceMap`を作り，`object_written`などの状態を元のworkflow，job，stepへ対応付ける．

## 7．信頼経路JSONをSMVへ変換するロジック

`chain_to_nusmv.py`では，JSON名をSMV用の名前へ対応付ける．

```python
FACT_MAPPING = {
    "producer_untrusted": "producerUntrusted",
    "write_succeeded": "writeSucceeded",
    "consumer_uses_object": "consumerUsesObject",
    "integrity_check_present": "integrityCheckPresent",
    "has_authority": "hasAuthority",
}
```

確定した値は`DEFINE`で定数にし，`unknown`は`FROZENVAR`にする．`FROZENVAR`は実行途中で変化しないが，初期値として真偽の両方をNuSMVが検査する．

```smv
DEFINE
  producer_untrusted := TRUE;
  write_succeeded := TRUE;
  integrity_check_present := FALSE;
  has_authority := TRUE;
```

処理の位置は`stage`，成果物が未信頼なままかは`object_tainted`で表す．

```smv
VAR
  stage : {start, object_written, object_restored,
           integrity_checked, object_used,
           authority_reached, blocked};
  object_tainted : boolean;

ASSIGN
  init(stage) := start;
  init(object_tainted) := producer_untrusted;
```

遷移は，上から順に条件を調べる`case`として生成する．

```smv
next(stage) := case
  stage = start &
    (!producer_untrusted | !write_intent |
     !write_authorized | !write_succeeded) : blocked;
  stage = start : object_written;

  stage = object_written & same_object & read_succeeded
    : object_restored;
  stage = object_written : blocked;

  stage = object_restored &
    integrity_check_present & integrity_check_passed
    : integrity_checked;
  stage = object_restored & integrity_check_present
    : blocked;
  stage = object_restored & consumer_uses_object
    : object_used;
  stage = object_restored : blocked;

  stage = object_used & privileged_consumer & has_authority
    : authority_reached;
  stage = object_used : blocked;
  TRUE : stage;
esac;
```

意味は単純である．必要条件がそろえば次へ進み，そろわなければ`blocked`へ進む．完全性確認が存在するのに失敗した場合は利用へ進めない．完全性確認が成功した場合だけ`object_tainted`を`FALSE`へ変更する．

## 8．検査式とA1の反例

検査式は次の1本である．

```smv
CTLSPEC AG !(stage = authority_reached & object_tainted)
```

これは，「全ての経路の全ての時点で，未信頼な成果物のまま権限へ到達する状態が存在しないこと」を要求する．

A1では，主要な事実が全て次へ進める値なので，次の反例が成立する．

```text
start
  → object_written
  → object_restored
  → object_used
  → authority_reached

この間，object_tainted = TRUEのまま
```

A2は完全性確認に失敗して`blocked`，A3は`consumerUsesObject=false`で`blocked`，A4は`hasAuthority=false`で`blocked`となる．A5はA1と同様に，未検証の成果物が模擬リポジトリ更新地点へ到達する反例を持つ．

## 9．どこまでが自動変換か

| 項目 | 現在の扱い |
| --- | --- |
| YAMLの構文，Action，引数，起動契機 | CodeQLで自動抽出 |
| 保存側と取得側の対応付け | 3個の静的条件で自動結合 |
| 保存・取得成功，同じartifact ID | GitHub Actionsの実行結果から確認 |
| シェルが成果物を利用する意味 | 人手注釈 |
| ハッシュ比較が完全性確認であること | 人手注釈 |
| 権限到達とみなす処理 | 人手注釈 |
| 状態遷移とCTL式 | Pythonで自動生成 |
| 状態空間の全探索と反例 | NuSMV |

したがって，「CodeQLから完全自動でNuSMVへ変換した」と言うのは正確ではない．現在は，「CodeQLから静的構造を抽出し，実行結果及び人手判断を根拠付きで補ってから，SMVを自動生成している」が正確である．

## 10．現在の制約

- 同じ`run:` stepから複数のシェル命令が抽出されると，共通JSONでは最後に処理した1行だけが残る．CSVには全行がある．
- Action名の完全一致で保存・取得操作を探すため，ラッパーActionや独自Actionの内部は検出できない．
- 1ワークフローにつき起動契機を1件に特定できることを前提としている．
- ワークフロー名が重複すると，行のグループ化や結合が曖昧になる可能性がある．
- `sameObject=true`は静的な結合結果であり，artifact ID一致の実行時証拠ではない．
- A5は現時点でCodeQLのCSVから再抽出した自動生成モデルではなく，実在脆弱性と実験ワークフローを人手で対応付けたモデルである．

## 11．再実行方法

```bash
python3 tools/discover_artifact_chains.py \
  results/codeql-actions-model-v2.26.3.csv \
  --annotations model/artifact-chain-annotations.json \
  --catalog /tmp/artifact-chain-candidates.json \
  --output-dir /tmp/generated-chains

python3 tools/chain_to_nusmv.py \
  /tmp/generated-chains/gha-a1-auto.json \
  --output /tmp/gha-a1-auto.smv

NuSMV /tmp/gha-a1-auto.smv
```

変換の正しさを確認するときは，最終判定だけを見るのではなく，次の対応を順に確認する．

1. YAMLのActionと引数がCSVへ残っているか．
2. 同じstepのCSV行が共通JSONで正しく再結合されたか．
3. 意図した保存側と取得側だけが結合されたか．
4. `facts`の値と情報源が正しいか．
5. 各`facts`がSMVの定数又は変数へ正しく変換されたか．
6. `next(stage)`が意図した遮断条件を表しているか．
7. NuSMVの反例が`traceMap`を通じて元のYAML位置へ戻るか．
