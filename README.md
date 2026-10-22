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

python3 -m workflow_model_v2.verification /out/example/a1.smv
```

結果はホストの `outputs/runs/example/` に保存されます．`outputs/` はGit管理外です．`a1.smv.stdout` にpropertyの判定と反例，`a1.smv.map.json` に入力YAMLとの対応を出力します．

コンテナに入らずに実行する方法や全評価の手順は[操作手順](docs/container-guide.md)を参照してください．

## ドキュメント

- [操作手順](docs/container-guide.md)
- [モデルの仕様と対応範囲](docs/semantics.md)
- [検証結果と未対応項目](docs/evaluation.md)
- [構成と旧版の保存先](docs/repository-maintenance.md)

## 旧版

v2以前の変換器と論文評価は [research/automatic-temporal-verification](https://github.com/formal-ci-cd/trust-boundary-verification/tree/research/automatic-temporal-verification) にあります．
