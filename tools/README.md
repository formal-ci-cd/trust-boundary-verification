# v2の入口

このブランチの `tools/` は現行v2の生成・評価・証拠保存に必要なものだけを置きます．

| 操作 | 入口 |
|---|---|
| 回帰・モデル生成・NuSMV・BFS照合・圧縮保存 | `sh tools/run_workflow_v2_extension.sh outputs/runs/<新規名>` |
| 個別のYAML→SMV生成 | `workflow_to_smv_v2.py`（Docker内で実行） |
| 評価用モデル生成・結果集計 | `evaluate_workflow_v2_extension.py`（runnerから呼出し） |
| NuSMV実行 | `verify_v2_extension_models.sh`（runnerから呼出し） |

Pythonは直下4本とpackage内15本です．直下の `workflow_to_smv.py` はv2のparser・共通処理，`workflow_shell_subset.py` は限定Shell parserとして必要です．`workflow_model_v2/` はIR，遷移，SMV生成，Action契約，式評価，独立探索，証拠保存を担当します．`workflow_loader.py` は旧評価系から独立させたYAML loaderです．

旧評価系52本とその専用テスト32本をこのブランチから削除しました．旧論文の再現はGitHubの `research/automatic-temporal-verification` を使用してください．整理直前の配置を含む全ファイルは保存タグ `archive/v2-before-tool-pruning` に固定しています．[削除一覧と照合済みblob](../docs/v2-tool-removal-inventory.md)．

過去の報告・資料にある削除済みscriptのコマンドは，保存先の版で実行する手順です．現行v2の対応範囲は [拡張仕様](../docs/next-model-design/extension-semantics.md) を参照してください．新機能は原則package内へ追加し，評価ケースごとの単独Pythonを増やしません．
