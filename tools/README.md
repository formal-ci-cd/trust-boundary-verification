# ツールの入口

## 現在のv2開発

| 操作 | 入口 |
|---|---|
| Docker内で回帰・モデル生成・NuSMV・BFS照合・圧縮保存 | `sh tools/run_workflow_v2_extension.sh outputs/runs/<新規名>` |
| YAMLから個別モデルを生成 | `workflow_to_smv_v2.py`（container内で実行） |
| 実装 | `workflow_model_v2/` |
| 限定Shell parser | `workflow_shell_subset.py` |

`workflow_to_smv.py` はv2が継承するscheduler/parserでもあり，不要な旧ファイルではありません．`workflow_model_v2/` 内でParser，契約，遷移，SMV backend，独立探索，evidence保存を分けています．

## 論文の固定評価系

主入口は `build_paper_evidence.py`．`yaml_to_model.py`，`automatic_artifact_analysis.py`，`evaluate_artifact_subset.py`，`evaluate_cache_subset.py` とその依存を使います．A1～A5／TanStackや他事例の比較・再現用scriptもテスト・資料の参照があるため維持しています．ファイル名が古いだけで削除してはいけません．

## 履歴用

`legacy/` は現在のsource/test/docsから参照されていなかった6本の単独CLIをまとめたものです．過去の収集・比較・検証の再実行用として保持し，不要と断定して削除しません．移動後も `python3 tools/legacy/<名前>.py --help` が使えます．

| 履歴用CLI | 役割 |
|---|---|
| `collect_cline_case.py` | 過去のCline評価出力の収集 |
| `collect_tanstack_case.py` | 固定TanStack評価出力の収集 |
| `evaluate_core_boundary_controls.py` | 旧モデルの1因子対照評価 |
| `inspect_codeql_repository_check.py` | 固定CodeQL packの規則確認 |
| `summarize_core_tool_baselines.py` | 固定A1～A5ツール出力の集計 |
| `verify_tanstack_n_consumers.py` | 保存済みN-consumerモデルの再検査 |

新規の機能実装は原則 `workflow_model_v2/` に追加し，root直下には利用者向けの入口だけを追加します．評価ケースごとのPython，途中版の `*_new.py`／`*_final.py` は作りません．
