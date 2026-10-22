# 構成と旧版の保存先

## 構成

| 場所 | 内容 |
|---|---|
| `tools/` | YAML読込み，モデル生成，検証，結果の集計 |
| `tests/` | 回帰テスト |
| `.github/workflows/` | Artifactの評価入力とCI |
| `experiments/public-cases/tanstack/` | v2で使用するworkflowとCompositeのsnapshot |
| `outputs/` | 生成した入力・モデル・ログ・反例．Git管理外 |
| `Dockerfile`，`compose.yaml` | PythonとNuSMVの実行環境 |

操作は[コンテナ手順](container-guide.md)，モデルの意味は[仕様](semantics.md)，確認済みの結果は[評価記録](evaluation.md)を参照してください．

## 出力

入力YAML，SMV，対応表，検証ログ，反例を個別ファイルで保存します．検証結果の自動削除は行いません．コンテナを停止しても `outputs/runs/` のファイルは残ります．

別の評価には新しい出力先を指定してください．既存のモデル・検証ログ・集計結果は上書きしません．`outputs/` はGitとDocker buildの対象から外しています．回帰評価の期待値は `tests/fixtures/prototype-properties.json` として管理し，保存済み出力がなくてもbuild・評価できる構成です．

## 旧版

| 版 | 保存先 |
|---|---|
| v2以前の変換器・論文評価 | [research/automatic-temporal-verification](https://github.com/formal-ci-cd/trust-boundary-verification/tree/research/automatic-temporal-verification)（`8ecb091`） |
| 旧ツール整理前 | タグ `archive/v2-before-tool-pruning`（`9827794`） |
| コンテナ対応後・旧資料整理前 | タグ `archive/v2-before-repository-pruning`（`26e720a`） |

旧研究ブランチには `yaml_to_model.py`，`codeql_csv_to_model.py`，`build_trust_chain.py`，`model_to_nusmv.py`，`chain_to_nusmv.py` があります．旧版の再評価はその版の手順を使用してください．
