# Trust Boundary Verification for CI/CD

GitHub ActionsのCache・artifactを介した信頼境界を，YAMLから有限状態モデルへ変換して検証する研究用repositoryです．

この `feat/workflow-model-v2` ブランチは現行v2専用です．旧評価系のPythonと専用テストは削除し，必要なparser・生成器・独立探索・評価・証拠保存だけを残しています．

- コンテナの起動・入室・段階実行：[操作手順](docs/container-guide.md)
- 実装の入口：[tools/README.md](tools/README.md)
- 対応範囲と制限：[拡張仕様](docs/next-model-design/extension-semantics.md)
- 保存済み評価：[最新評価](outputs/model-v2/INDEX.md)
- ブランチと出力の管理：[管理方針](docs/repository-maintenance.md)
- 削除した旧Pythonの保存先：[照合済み一覧](docs/v2-tool-removal-inventory.md)

## v2を使う

PythonとNuSMVを同梱したコンテナを用意しています．ホスト側で次を実行すると，コンテナへ入れます．

```sh
mkdir -p outputs/runs
docker compose build model
docker compose up -d model
docker compose exec model bash
```

コンテナ内でYAML→SMV生成，NuSMV検証，結果確認を別々に実行します．外から1コマンドずつ呼ぶ方法と全評価の手順も[操作手順](docs/container-guide.md)に記載しています．生成した入力・SMV・ログ・反例は `outputs/runs/` にそのまま残します．自動圧縮・出力削除は廃止し，圧縮していた過去の出力も元の配置へ復元しました．

## 旧論文・旧評価の保存先

旧論文評価系は [research/automatic-temporal-verification](https://github.com/formal-ci-cd/trust-boundary-verification/tree/research/automatic-temporal-verification) に保存しています．整理直前の全コードは保存タグ [archive/v2-before-tool-pruning](https://github.com/formal-ci-cd/trust-boundary-verification/tree/archive/v2-before-tool-pruning) から復元できます．`main` は公開側の基準です．

過去の `docs/`・`results/`・`experiments/` は研究記録として保持しています．そこにある旧scriptの実行手順は上記の旧版用です．旧評価の結果を，v2で再現できた結果として扱わないでください．v2の未対応構文やTanStackの未達成項目は[実装報告](docs/next-model-design/extension-report-2026-10-22.md)に記録しています．
