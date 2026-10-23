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
python3 tools/workflow_model_v2/verification.py /out/demo-001/a1.smv
```

検証プログラムの実ファイルは `/repo/tools/workflow_model_v2/verification.py` です．上の相対パスは，コンテナ内で `/repo` にいる場合の指定です．SMVの `/out/demo-001/a1.smv` は手順4で指定した生成先であり，別名で生成した場合はそのパスに置き換えます．

以前の案内の次の書き方も，同じプログラムを起動します．

```sh
python3 -m workflow_model_v2.verification /out/demo-001/a1.smv
```

`-m` は「Pythonのmoduleを名前で探して起動する」という指定です．`.` はpackageの区切りで，`workflow_model_v2.verification.py` という名前のファイルがあるわけではありません．Dockerfileの `PYTHONPATH=/repo/tools` により，`/repo/tools/workflow_model_v2/verification.py` が見つかります．

このコマンドはNuSMVを呼び，検証記録を `a1.smv.log` の1ファイルへ保存します．画面には終了コードとログの場所を表示します．既存の検証ログがあれば上書きせず停止します．

ログには，次の順序で記録します．

1. 対象モデル，起動コマンド，開始時刻，時間上限．
2. CTL検査・遷移全域性のコマンド，標準出力（propertyのtrue/falseと反例trace），標準エラー，終了時刻，終了コード，実行時間．
3. 到達状態数計算のコマンドと入力コマンド列，標準出力，標準エラー，終了時刻，終了コード，実行時間．CTL検査が失敗した場合は実行しません．
4. 全体の終了状態と合計実行時間．
5. 自動集計用のJSON情報．生出力は重複保存せず，ログ内のbyte位置で参照します．

時刻はUTC，時間は秒で記録します．NuSMVの起動失敗や時間切れも同じログに残し，時間切れまでに得た出力を保持します．状態数計算の一時command fileは終了後に削除し，内容はログに残します．SMV本体とYAML対応表は別ファイルです．

旧形式の9ファイル（`.stdout`，`.stderr`，`.exitcode`，`.seconds`，`.timing`，`.commands`，`.stats.stdout`，`.stats.stderr`，`.stats.exitcode`）は新規生成しません．保存済みの旧形式は集計時に引き続き読めます．既存ファイルの移行・削除は行いません．

NuSMV呼出しごとの既定上限は120秒です．変更する場合は`--timeout 45`のように指定します．時間切れは終了コード124と診断を保存し，未完了として扱います．CTL検査と状態数の列挙にはそれぞれ上限が適用されます．

検証器を直接呼んで，画面で結果を見ることもできます．こちらは結果ファイルを自動保存しません．

```sh
NuSMV -ctt /out/demo-001/a1.smv
```

## 6．結果を読む（コンテナ内）

```sh
cat /out/demo-001/a1.smv.log
# propertyの判定だけを見る場合．
grep -- '-- specification' /out/demo-001/a1.smv.log
```

終了コード0は，検証器が処理を完了したことを示します．propertyがすべてtrueだったという意味ではありません．たとえば `AG !bad_simulated_path is false` は，この有限モデルに模擬的な危険経路の反例が存在することを示します．実際のGitHub上での侵害成功の証明ではありません．対応表のunsupportedも確認し，未対応を安全と解釈しないでください．

到達状態列挙では，ログの「到達状態数の計算」に正の `reachable states:` があることと，標準エラーの内容を確認します．wrapperも正の状態数を確認し，NuSMVが0終了しても状態数を得られなければ判定用終了コードを1にします．日本語を含むモデルパスはUTF-8のまま渡します．末尾の集計情報がないログは未完了として扱います．

ホスト側では同じファイルを `outputs/runs/demo-001/` から開けます．コンテナを出るときは `exit` と入力します．

## 7．コンテナに入らず，ホストから同じ処理を呼ぶ

起動済みのコンテナに対して，手順4・5と同じコマンドを外から指定できます．この例は新しい出力先 `outside-001` を使います．

```sh
docker compose exec -T model python3 tools/workflow_to_smv_v2.py \
  .github/workflows/artifact-a1-pr-producer.yml \
  .github/workflows/artifact-a1-unsafe-consumer.yml \
  --output /out/outside-001/a1.smv

docker compose exec -T model python3 tools/workflow_model_v2/verification.py \
  /out/outside-001/a1.smv

cat outputs/runs/outside-001/a1.smv.log
```

`exec` は起動中のコンテナでコマンドを実行します．常駐させずに1コマンドだけ実行したい場合は `run --rm -T` を使います．処理終了後にそのコンテナが終了し，出力はホストに残ります．

```sh
docker compose run --rm -T model python3 tools/workflow_to_smv_v2.py \
  .github/workflows/artifact-a1-pr-producer.yml \
  .github/workflows/artifact-a1-unsafe-consumer.yml \
  --output /out/oneshot-001/a1.smv

docker compose run --rm -T model python3 tools/workflow_model_v2/verification.py \
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
# 生成：入力の対照構成，101モデル，対応表，独立BFSの結果を作る．
docker compose exec -T model python3 tools/evaluate_workflow_v2_extension.py \
  generate --output /out/full-001

# 検証：生成した各SMVをNuSMVで検証し，ログと状態数を保存する．
docker compose exec -T model python3 tools/workflow_model_v2/verification.py /out/full-001

# 集計：property判定，BFSとの一致，反例traceとYAML対応表を保存する．
docker compose exec -T model python3 tools/evaluate_workflow_v2_extension.py \
  summarize --output /out/full-001
```

生成先が空でなければ生成を停止します．次の評価には新しい名前を使ってください．集計結果は `outputs/runs/full-001/verification-summary.json`，各入力・SMV・ログ・反例は同じディレクトリ以下にそのまま残ります．`report-metrics.json`に時間・状態数，`unknown-unsupported-inventory.json`に診断・仮定を保存します．構文不正の対照1件は意図した失敗として扱い，集計で確認します．

## 9．停止する（ホスト側）

```sh
docker compose down
```

`outputs/runs/` の結果はホストに残ります．次回は `docker compose up -d model` で再起動します．

## 出力の保存方針

入力YAML・SMV・検証ログ・反例は個別ファイルで保存します．archiveは必要な場合だけ追加で作成します．`outputs/` はGit管理外の生成結果で，コード・依存ライブラリの配置先ではありません．不要な実験directoryは削除しても変換器自体は動きます．ただし，削除した入力コピー・scenario・ログ・反例・時間計測をGitから戻すことはできません．再実行には元のYAML・scenario・snapshot・契約metadataと実行環境が必要で，経過時間等まで同じになるわけではありません．残したい研究記録は別の場所へ退避してから，不要な実験単位で整理してください．実行中の保存先は削除しないでください．`outputs/runs/`自体を削除した場合は，後述の復旧手順に従い，保存先の再作成とコンテナの再作成を行います．

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

## コードを読む順序

1. `tools/workflow_to_smv_v2.py`：入力と出力先を受け取る入口．
2. `scenario.py`と`workflow_loader.py`：有限の入力条件とYAMLの読取り．
3. `compiler.py`：run/job/step，値の解決，scheduler，保存・復元の規則．
4. `dataflow.py`と`shell_lowering.py`：実行時env，output，内容・由来・taint・依存・検証履歴．
5. `transition.py`：共通の状態宣言と更新規則からSMVを出力．
6. `verification.py`と`evaluate_workflow_v2_extension.py`：NuSMVの実行，property・反例・BFSの照合．

上記の補助moduleは`tools/workflow_model_v2/`にあります．Python，Dockerfile，Composeのコメントに，各処理の目的と前提を日本語で記載しています．`guard`は状態を更新する条件，`domain`は有限の値候補，`PC`はjob内の実行位置，`latch`は一度起きた事象を保持する履歴です．

`examples/`には，同じProducerとscenarioでArtifactの利用へ到達するConsumerと，到達しないsafe Consumerがあります．実行方法は[examplesの入力を試す場合](#examplesの入力を試す場合)を参照してください．`.github/workflows/`には置かず，全101モデルの評価とは別に回帰テストで確認します．

## outputsを整理するときの注意と復旧

`/out`はコンテナ内部だけのdirectoryではなく，ホストの`outputs/runs/`をbind mountしたものです．起動中にホスト側の`outputs/runs/`自体を削除すると，コンテナが削除前のdirectoryを参照したままとなり，`Operation not permitted`が発生することがあります．この状態は，変換器が過去の結果を入力として必要としているという意味ではありません．

通常は`outputs/runs/`自体を残し，処理が終わった不要な実験directoryだけを整理してください．現在実行中の処理が使う保存先は削除しません．mount元自体を消す場合は先にコンテナを停止し，保存先を作り直した後にコンテナを再作成します．

既に`PermissionError: [Errno 1] Operation not permitted: '/out/...'`となった場合は，コンテナのシェルから出て，ホストのリポジトリルートで次を実行します．コンテナの再作成は，解析処理が動いていない時に行ってください．開いているコンテナのシェルも終了します．

```sh
mkdir -p outputs/runs
docker compose up -d --force-recreate model
docker compose exec model bash
```

`mkdir`だけでは，起動済みコンテナの古いmount参照が残る場合があります．`--force-recreate`で新しいdirectoryへ接続し直します．この操作はイメージの再buildではなく，ソースやホストに残っている出力を削除する操作でもありません．

復旧後，コンテナ内で保存先を確認し，新しい出力先を指定して変換します．

```sh
ls -ld /out
python3 tools/workflow_to_smv_v2.py \
  .github/workflows/artifact-a1-pr-producer.yml \
  .github/workflows/artifact-a1-unsafe-consumer.yml \
  --output /out/retry-001/a1.smv
```

## ファイルが見つからない場合

最初に，ホストから次でコンテナ内の配置を確認できます．ホストへのPython依存の導入は不要です．

```sh
docker compose exec -T model pwd
docker compose exec -T model ls -l /repo/tools/workflow_model_v2/verification.py
docker compose exec -T model python3 tools/workflow_model_v2/verification.py --help
```

| 表示・状況 | 確認すること |
|---|---|
| `No module named workflow_model_v2` | コンテナ内で実行しているか．`-m`はファイル名ではないこと．必要なら上記の実ファイル指定を使う |
| `can't open file ...verification.py` | 実ファイルの位置は`/repo/tools/workflow_model_v2/verification.py`．相対指定の作業場所は`/repo` |
| `FileNotFoundError: /out/...smv` | 生成時の`--output`と検証対象が同じか．手順のdemo-001は固定の必須名ではない |
| `result already exists` | 検証ログが既にある．既存記録を残し，新しい出力先へ再生成して検証する |
| `No module named workflow_model_v2.verification` | 現行ソースにそのファイルがあるか，古いイメージだけで起動していないかを確認する |

`PYTHONPATH`を調べる場合は，コンテナ内で `printenv PYTHONPATH` を実行します．現行イメージでは `/repo/tools` です．イメージを更新する必要がある場合は手順1・2を使い，解析中の処理がない時に更新してください．

## examplesの入力を試す場合

短いProducerと2つのConsumerを，同じ`artifact-scenario.json`で比較します．コマンドはコンテナ内の`/repo`で実行します．出力先が既にある場合は別名にしてください．

```sh
python3 tools/workflow_to_smv_v2.py examples/artifact-producer.yml examples/artifact-consumer.yml --scenario examples/artifact-scenario.json --output /out/example-unsafe-001/model.smv
python3 tools/workflow_model_v2/verification.py /out/example-unsafe-001/model.smv

python3 tools/workflow_to_smv_v2.py examples/artifact-producer.yml examples/artifact-safe-consumer.yml --scenario examples/artifact-scenario.json --output /out/example-safe-001/model.smv
python3 tools/workflow_model_v2/verification.py /out/example-safe-001/model.smv

grep -- '-- specification AG !dependent_use' /out/example-unsafe-001/model.smv.log
grep -- '-- specification AG !dependent_use' /out/example-safe-001/model.smv.log
```

| Consumer | `AG !transfer` | `AG !dependent_use` | 意味 |
|---|---|---|---|
| `artifact-consumer.yml` | false | false | PR由来のArtifactを取得し，汚染データの利用へ到達する経路がある |
| `artifact-safe-consumer.yml` | false | true | Artifactの取得には到達するが，PR由来の場合の読取りはifで遮断される |

Producerはcheckoutした`examples/payload.txt`をそのままuploadします．Consumerはdownload先の`payload/payload.txt`を読み，`tr`で改行を除いて`value`へ格納します．この読取りは対応済みの構造化Shell契約を使い，Artifact memberの内容・由来を追跡します．safe版との処理上の差は，読取りstepの`if`だけです．

scenarioはProducerがPR由来であること，checkoutで得るファイル在庫と有限の内容候補，Consumerと上流runの対応を明示します．両Consumerへ同じscenarioを渡してください．`--scenario`を省略するとcheckout在庫を確定できず，未対応診断になるため，この比較の前提を満たしません．これは有限モデルの条件であり，実際のGitHub runを観測した記録ではありません．

両方とも対応表の`diagnostics`が空であることを回帰テストで確認しています．safeはこのPR入力での利用遮断を指し，あらゆるイベントに対する安全性を意味しません．権限sinkはこのデモに含めていません．
