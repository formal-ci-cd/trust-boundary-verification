# 構成と旧版の保存先

## 構成

| 場所 | 内容 |
|---|---|
| `tools/` | YAML読込み，モデル生成，検証，結果の集計 |
| `tests/` | 回帰テストと固定した対照fixture |
| `examples/` | YAMLだけでArtifactの到達・不達を比較する短い例．scenarioは任意．専用回帰テストで確認 |
| `.github/workflows/` | Artifactの評価入力とCI |
| `experiments/public-cases/tanstack/` | v2で使用するworkflowとCompositeのsnapshot |
| `outputs/` | 生成した入力・モデル・ログ・反例．Git管理外 |
| `Dockerfile`，`compose.yaml` | PythonとNuSMVの実行環境 |

操作は[コンテナ手順](container-guide.md)，モデルの意味は[仕様](semantics.md)，確認済みの結果は[評価記録](evaluation.md)を参照してください．

## 出力

生成した入力YAML，SMV，対応表は個別ファイルで保存します．モデルごとのNuSMV実行記録は`model.smv.log`の1ファイルにまとめ，反例traceもそのログ内に残します．全評価の集計では，反例と元YAMLの対応を`verification-summary.json`内の各モデルの`counterexamples`へ記録し，反例ごとの別ファイルは生成しません．検証結果の自動削除は行いません．コンテナを停止しても `outputs/runs/` のファイルは残ります．

別の評価には新しい出力先を指定してください．既存のモデル・検証ログ・集計結果は上書きしません．`outputs/` はGitとDocker buildの対象から外しています．回帰評価の期待値は `tests/fixtures/prototype-properties.json` として管理し，保存済み出力がなくてもbuild・評価できる構成です．

### 不要な出力を削除する場合

`outputs/`は変換器の入力ソース・依存ライブラリではないため，不要な結果を削除してもbuild・新規変換・回帰テストは可能です．ただしGit管理外なので，削除した実験記録をGitで復元することはできません．旧版のブランチやタグにも，このローカル出力が自動保存されているわけではありません．

保存したい入力コピー・scenario・snapshot・検証ログ・反例・対応表は別の場所へ退避し，解析終了後に不要な実験directory単位で整理します．同じ条件を再実行できても，時間計測や実行記録そのものまで同一に戻るとは限りません．通常はmount元の`outputs/runs/`自体を残し，不要な実験directoryだけを整理します．mount元自体を消す場合は先にコンテナを停止してください．既に削除した場合は，ホストで`mkdir -p outputs/runs`を実行した後，解析が動いていないことを確認して`docker compose up -d --force-recreate model`で接続し直します．directoryの再作成だけでは，起動済みコンテナの`/out`が`Operation not permitted`のままになる場合があります．[復旧手順](container-guide.md#outputsを整理するときの注意と復旧)を参照してください．

## 旧版

| 版 | 保存先 |
|---|---|
| v2以前の変換器・論文評価 | [research/automatic-temporal-verification](https://github.com/formal-ci-cd/trust-boundary-verification/tree/research/automatic-temporal-verification)（`8ecb091`） |
| 旧ツール整理前 | タグ `archive/v2-before-tool-pruning`（`9827794`） |
| コンテナ対応後・旧資料整理前 | タグ `archive/v2-before-repository-pruning`（`26e720a`） |

旧研究ブランチには `yaml_to_model.py`，`codeql_csv_to_model.py`，`build_trust_chain.py`，`model_to_nusmv.py`，`chain_to_nusmv.py` があります．旧版の再評価はその版の手順を使用してください．
