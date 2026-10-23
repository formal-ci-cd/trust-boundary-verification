# GitHub Actions YAML→SMV変換器

GitHub ActionsのYAMLからNuSMVの有限状態モデルを生成します．ArtifactやCacheを経由するworkflow間のデータ利用と，権限を伴う操作への到達可能性を調べます．通常の入力はYAMLだけです．event・fork・権限設定などの有限候補をSMVで探索し，候補の根拠と限界を対応表・検証ログへ記録します．SMVの生成は1つのbackendで行い，`--scenario`とCLIの条件指定は候補を制限する任意入力です．最初のworkflowは宣言に整合して開始済みとし，後続workflowの起動関係を検証します．

## 使い方

リポジトリのルートでコンテナを起動します．

```sh
mkdir -p outputs/runs
docker compose build model
docker compose up -d model
docker compose exec model bash
```

コンテナ内で入力YAMLを指定し，モデルを生成・検証します．次はArtifactの利用に到達する短い例です．

```sh
python3 tools/workflow_to_smv_v2.py \
  examples/artifact-producer.yml \
  examples/artifact-consumer.yml \
  --output /out/example/a1.smv

python3 tools/workflow_model_v2/verification.py /out/example/a1.smv
```

検証用の実ファイルは `tools/workflow_model_v2/verification.py` です．上のコマンドはコンテナ内の `/repo` で実行します．`python3 -m workflow_model_v2.verification` でも同じ処理を呼べますが，こちらはファイル名ではなくPythonのモジュール名を指定する書き方です．

結果はホストの `outputs/runs/example/` に保存されます．`outputs/` はGit管理外の生成結果で，変換器の動作には不要です．不要な実験結果は削除できますが，残したいログ・反例・scenarioは先に別の場所へ保存してください．起動中のmount元である`outputs/runs/`自体は残し，不要な実験directoryだけを整理します．mount元を削除して`/out`へアクセスできなくなった場合は，[保存先とコンテナの再接続手順](docs/container-guide.md#outputsを整理するときの注意と復旧)を参照してください．`a1.smv.log` に実行コマンド・propertyの判定・反例・エラー・実行時間・終了状態，`a1.smv.map.json` に入力YAMLとの対応を出力します．

safe版はConsumerを`examples/artifact-safe-consumer.yml`へ替え，別の出力先へ生成します．両方とも取得には到達し，`AG !dependent_use`が到達版ではfalse，safe版ではtrueになります．[デモの前提と比較手順](docs/container-guide.md#examplesの入力を試す場合)を確認してください．不達は記録された有限条件内の結果であり，任意の入力への安全保証ではありません．

コンテナに入らずに実行する方法や全評価の手順は[操作手順](docs/container-guide.md)を参照してください．

## 条件を絞って検証する

条件指定を省略すると，宣言された複数eventとPRのfork/non-forkなどを同じモデルで探索します．たとえばforkのPRに限定する場合は，生成コマンドへ次を追加します．

```sh
python3 tools/workflow_to_smv_v2.py \
  examples/artifact-producer.yml examples/artifact-consumer.yml \
  --event pull_request --fork true --output /out/fork-001/model.smv
python3 tools/workflow_model_v2/verification.py /out/fork-001/model.smv --property dependent_use
```

`--run run1`または一意なworkflow名で指定先を変更できます．省略時の指定先は最初のYAMLです．YAMLは1本だけでも変換できます．結果の`witnessConditions`は一つの反例中の値です．Booleanの両側を追加検査して成立したものだけを，記録した有限モデル内の必要条件として報告します．[条件指定と結果の読み方](docs/container-guide.md#外部条件の自動探索と条件指定)を参照してください．

branchやファイル内容は有限代表値であり，任意のGitHub Actions実行を網羅しません．未対応・不完全なdomainが残る場合，モデル内の不達を実環境の安全保証とは扱いません．

## ドキュメント

- [操作手順](docs/container-guide.md)
- [モデルの仕様と対応範囲](docs/semantics.md)
- [検証結果と未対応項目](docs/evaluation.md)
- [構成と旧版の保存先](docs/repository-maintenance.md)

## 旧版

v2以前の変換器と論文評価は [research/automatic-temporal-verification](https://github.com/formal-ci-cd/trust-boundary-verification/tree/research/automatic-temporal-verification) にあります．
