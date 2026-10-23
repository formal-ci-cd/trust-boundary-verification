# GitHub Actions YAML→SMV変換器

GitHub ActionsのYAMLからNuSMVの有限状態モデルを生成します．ArtifactやCacheを経由するworkflow間のデータ利用と，権限を伴う操作への到達可能性を調べます．SMVの生成は1つのbackendで行い，`--scenario` で評価条件を指定できます．

## 使い方

リポジトリのルートでコンテナを起動します．

```sh
mkdir -p outputs/runs
docker compose build model
docker compose up -d model
docker compose exec model bash
```

コンテナ内で入力YAMLを指定し，モデルを生成・検証します．

```sh
python3 tools/workflow_to_smv_v2.py \
  .github/workflows/artifact-a1-pr-producer.yml \
  .github/workflows/artifact-a1-unsafe-consumer.yml \
  --output /out/example/a1.smv

python3 tools/workflow_model_v2/verification.py /out/example/a1.smv
```

検証用の実ファイルは `tools/workflow_model_v2/verification.py` です．上のコマンドはコンテナ内の `/repo` で実行します．`python3 -m workflow_model_v2.verification` でも同じ処理を呼べますが，こちらはファイル名ではなくPythonのモジュール名を指定する書き方です．

結果はホストの `outputs/runs/example/` に保存されます．`outputs/` はGit管理外の生成結果で，変換器の動作には不要です．不要な実験結果は削除できますが，残したいログ・反例・scenarioは先に別の場所へ保存してください．起動中のmount元である`outputs/runs/`自体は残し，不要な実験directoryだけを整理します．mount元を削除して`/out`へアクセスできなくなった場合は，[保存先とコンテナの再接続手順](docs/container-guide.md#outputsを整理するときの注意と復旧)を参照してください．`a1.smv.log` に実行コマンド・propertyの判定・反例・エラー・実行時間・終了状態，`a1.smv.map.json` に入力YAMLとの対応を出力します．

コンテナに入らずに実行する方法や全評価の手順は[操作手順](docs/container-guide.md)を参照してください．

## ドキュメント

- [操作手順](docs/container-guide.md)
- [モデルの仕様と対応範囲](docs/semantics.md)
- [検証結果と未対応項目](docs/evaluation.md)
- [構成と旧版の保存先](docs/repository-maintenance.md)

## 旧版

v2以前の変換器と論文評価は [research/automatic-temporal-verification](https://github.com/formal-ci-cd/trust-boundary-verification/tree/research/automatic-temporal-verification) にあります．
