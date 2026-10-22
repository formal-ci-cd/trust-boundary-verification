# コンテナで段階的にv2を使う

PythonとNuSMVを同じコンテナに入れています．YAMLを読んでSMVを生成する処理と，SMVをNuSMVで検証する処理は別のコマンドです．YAMLに書かれたGitHub Actionsのjobを実行するものではありません．

## 1．イメージを作る（ホスト側）

DockerとComposeを用意し，リポジトリのルートで実行します．

```sh
mkdir -p outputs/runs
docker compose build model
```

ルートの `Dockerfile` がPython 3.12.11，PyYAML 6.0.3，NuSMV 2.6.0を用意します．NuSMVのx86_64バイナリを使うため，実行環境は `linux/amd64` にしています．初回build時にはイメージとPython依存の取得が必要です．実行中のコンテナはネットワークを使いません．

## 2．コンテナを起動する（ホスト側）

```sh
docker compose up -d model
docker compose ps
```

`model` は操作待ちの状態で起動します．

| ホストの場所 | コンテナ内 | 用途 |
|---|---|---|
| このrepository | `/repo` | ソースと入力YAML．読取り専用 |
| `outputs/runs/` | `/out` | 生成したSMV，対応表，ログ，検証結果．書込み可能 |
| 一時領域 | `/tmp` | コンテナ内の一時ファイル |

ソースを変更すると，起動中のコンテナからも変更が見えます．Python等の依存を変更した場合は再buildし，`docker compose up -d model` でコンテナを更新します．

## 3．コンテナへ入る（ホスト側）

```sh
docker compose exec model bash
```

ここからはコンテナ内のコマンドです．作業場所は `/repo` です．

```sh
pwd
python3 --version
python3 -m unittest discover -s tests -v
```

最後のコマンドは変換器の回帰テストです．入力YAMLを個別に調べる操作とは分かれています．

## 4．入力YAMLからSMVを生成する（コンテナ内）

例として，未信頼側のA1 producerと，成果物を使うA1 consumerを指定します．

```sh
python3 tools/workflow_to_smv_v2.py \
  .github/workflows/artifact-a1-pr-producer.yml \
  .github/workflows/artifact-a1-unsafe-consumer.yml \
  --output /out/demo-001/a1.smv
```

この段階で作られるのは次の2ファイルです．まだNuSMVは実行していません．

- `/out/demo-001/a1.smv`：生成した有限状態モデル．
- `/out/demo-001/a1.smv.map.json`：元YAMLの場所，モデルの変数，対応範囲・未対応事項など．

```sh
head -40 /out/demo-001/a1.smv
cat /out/demo-001/a1.smv.map.json
```

別の入力を使う場合はproducerとconsumerのパスを置き換えます．`--scenario 入力.json` で外部条件を明示し，必要な追加runは `--workflow 別のworkflow.yml` で指定します．シナリオはrunのevent，repository，scope，ファイル内容などの有限な外部条件です．省略時も同じbackendで既定条件を使います．対応範囲は [拡張仕様](semantics.md) を参照してください．既存の結果を比較できるよう，別の実験には `demo-002` 等の新しい出力先を使います．

## 5．生成したSMVを検証する（コンテナ内）

```sh
python3 -m workflow_model_v2.verification /out/demo-001/a1.smv
```

このコマンドはNuSMVを呼び，画面に終了コードを表示して，以下を個別に保存します．自動圧縮・削除は行いません．既存の検証ログがあれば上書きせず停止します．

| ファイル末尾 | 内容 |
|---|---|
| `.smv.stdout` | propertyのtrue/falseと反例trace |
| `.smv.stderr` | NuSMVの診断 |
| `.smv.exitcode` | NuSMVの終了コード |
| `.smv.seconds`，`.smv.timing` | 検証時間 |
| `.smv.commands` | 到達可能状態の確認に使ったNuSMVコマンド |
| `.smv.stats.stdout`，`.smv.stats.stderr`，`.smv.stats.exitcode` | 状態数確認の結果と終了コード |

検証器を直接呼んで，画面で結果を見ることもできます．こちらは結果ファイルを自動保存しません．

```sh
NuSMV -ctt /out/demo-001/a1.smv
```

## 6．結果を読む（コンテナ内）

```sh
cat /out/demo-001/a1.smv.exitcode
grep -- '-- specification' /out/demo-001/a1.smv.stdout
cat /out/demo-001/a1.smv.stderr
cat /out/demo-001/a1.smv.stats.stdout
```

終了コード0は，検証器が処理を完了したことを示します．propertyがすべてtrueだったという意味ではありません．たとえば `AG !bad_simulated_path is false` は，この有限モデルに模擬的な危険経路の反例が存在することを示します．実際のGitHub上での侵害成功の証明ではありません．対応表のunsupportedも確認し，未対応を安全と解釈しないでください．

ホスト側では同じファイルを `outputs/runs/demo-001/` から開けます．コンテナを出るときは `exit` と入力します．

## 7．コンテナに入らず，ホストから同じ処理を呼ぶ

起動済みのコンテナに対して，手順4・5と同じコマンドを外から指定できます．この例は新しい出力先 `outside-001` を使います．

```sh
docker compose exec -T model python3 tools/workflow_to_smv_v2.py \
  .github/workflows/artifact-a1-pr-producer.yml \
  .github/workflows/artifact-a1-unsafe-consumer.yml \
  --output /out/outside-001/a1.smv

docker compose exec -T model python3 -m workflow_model_v2.verification \
  /out/outside-001/a1.smv

cat outputs/runs/outside-001/a1.smv.stdout
```

`exec` は起動中のコンテナでコマンドを実行します．常駐させずに1コマンドだけ実行したい場合は `run --rm -T` を使います．処理終了後にそのコンテナが終了し，出力はホストに残ります．

```sh
docker compose run --rm -T model python3 tools/workflow_to_smv_v2.py \
  .github/workflows/artifact-a1-pr-producer.yml \
  .github/workflows/artifact-a1-unsafe-consumer.yml \
  --output /out/oneshot-001/a1.smv

docker compose run --rm -T model python3 -m workflow_model_v2.verification \
  /out/oneshot-001/a1.smv
```

自分の別フォルダにあるYAMLを使う場合は，そのフォルダも読取り専用で渡せます．ホスト側のパスを自分の保存先へ置き換えてください．

```sh
docker compose run --rm -T -v "$PWD/workflows":/input:ro model \
  python3 tools/workflow_to_smv_v2.py /input/producer.yml /input/consumer.yml \
  --output /out/custom-001/model.smv
```

## 8．全評価も3段階に分けて実行する（ホスト側）

```sh
# 生成：入力の対照構成，71モデル，対応表，独立BFSの結果を作る．
docker compose exec -T model python3 tools/evaluate_workflow_v2_extension.py \
  generate --output /out/full-001

# 検証：生成した各SMVをNuSMVで検証し，ログと状態数を保存する．
docker compose exec -T model python3 -m workflow_model_v2.verification /out/full-001

# 集計：property判定，BFSとの一致，反例traceとYAML対応表を保存する．
docker compose exec -T model python3 tools/evaluate_workflow_v2_extension.py \
  summarize --output /out/full-001
```

生成先が空でなければ生成を停止します．次の評価には新しい名前を使ってください．集計結果は `outputs/runs/full-001/verification-summary.json`，各入力・SMV・ログ・反例は同じディレクトリ以下にそのまま残ります．構文不正の対照1件は意図した失敗として扱い，集計で確認します．

## 9．停止する（ホスト側）

```sh
docker compose down
```

`outputs/runs/` の結果はホストに残ります．次回は `docker compose up -d model` で再起動します．

## 出力の保存方針

入力YAML・SMV・検証ログ・反例は個別ファイルで保存します．archiveは必要な場合だけ追加で作成します．`outputs/` はGit管理外です．

明示的に追加のarchiveを作りたい場合だけ，次を実行できます．この操作でも元ファイルは削除しません．

```sh
docker compose exec -T model python3 -m workflow_model_v2.evidence backup /out/full-001
```

Composeを使わず，build済みのイメージだけでも単発実行できます．イメージ内に同梱したソースを使うため，ソース変更後は先に再buildします．

```sh
docker run --rm --platform linux/amd64 --network none --read-only \
  --user 1000:1000 --cap-drop ALL --security-opt no-new-privileges \
  --memory 2g --cpus 2 --pids-limit 128 \
  --tmpfs /tmp:rw,nosuid,nodev,size=512m \
  -v "$PWD/outputs/runs:/out:rw" tbv-model-v2:local \
  python3 tools/workflow_to_smv_v2.py \
  .github/workflows/artifact-a1-pr-producer.yml \
  .github/workflows/artifact-a1-unsafe-consumer.yml \
  --output /out/image-only-001/a1.smv
```
