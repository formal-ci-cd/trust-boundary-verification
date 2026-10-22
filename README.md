# Trust Boundary Verification for CI/CD

GitHub ActionsのCache・artifactを介した信頼境界を，YAMLから有限状態モデルへ変換して検証する研究用repositoryです．

この `feat/workflow-model-v2` ブランチは現行v2専用です．旧評価系のPythonと専用テストは削除し，必要なparser・生成器・独立探索・評価・証拠保存だけを残しています．

- 操作と実装：[tools/README.md](tools/README.md)
- 対応範囲と制限：[拡張仕様](docs/next-model-design/extension-semantics.md)
- 保存済み評価：[最新評価](outputs/model-v2/latest/README.md)
- ブランチと出力の管理：[管理方針](docs/repository-maintenance.md)
- 削除した旧Pythonの保存先：[照合済み一覧](docs/v2-tool-removal-inventory.md)

## v2の再評価

Dockerを使用します．通常の生成先はGit管理外の `outputs/runs/` です．成功したrunは要約と検証済み詳細archiveへ自動でまとめます．

```sh
sh tools/run_workflow_v2_extension.sh outputs/runs/v2-001
```

GitHub CIはv2の回帰テストを実行します．NuSMVと独立探索の照合を含む全評価には，上記runnerを使います．原本workflow・外部Action・実際のpublish操作を実行する評価ではありません．

## 旧論文・旧評価の保存先

旧論文評価系は [research/automatic-temporal-verification](https://github.com/formal-ci-cd/trust-boundary-verification/tree/research/automatic-temporal-verification) に保存しています．整理直前の全コードは保存タグ [archive/v2-before-tool-pruning](https://github.com/formal-ci-cd/trust-boundary-verification/tree/archive/v2-before-tool-pruning) から復元できます．`main` は公開側の基準です．

過去の `docs/`・`results/`・`experiments/` は研究記録として保持しています．そこにある旧scriptの実行手順は上記の旧版用です．旧評価の結果を，v2で再現できた結果として扱わないでください．v2の未対応構文やTanStackの未達成項目は[実装報告](docs/next-model-design/extension-report-2026-10-22.md)に記録しています．
