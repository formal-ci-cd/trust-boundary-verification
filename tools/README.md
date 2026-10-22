# 実行コマンド

以下はコンテナ内で実行します．起動方法は[コンテナ操作手順](../docs/container-guide.md)を参照してください．

| 処理 | コマンド |
|---|---|
| 回帰テスト | `python3 -m unittest discover -s tests -v` |
| YAML→SMV | `python3 tools/workflow_to_smv_v2.py producer.yml consumer.yml --output /out/example/model.smv` |
| SMV検証・ログ保存 | `python3 -m workflow_model_v2.verification /out/example/model.smv` |
| 全評価のモデル生成 | `python3 tools/evaluate_workflow_v2_extension.py generate --output /out/full` |
| 全評価の検証 | `python3 -m workflow_model_v2.verification /out/full` |
| 判定・反例の集計 | `python3 tools/evaluate_workflow_v2_extension.py summarize --output /out/full` |

`workflow_model_v2/compiler.py` が唯一のSMV生成入口です．`scenario.py` が外部条件，`shell_lowering.py` が内容・完全性・利用履歴を扱い，`transition.py` へ下げます．`frontend.py` と `workflow_shell_subset.py` が構文と行位置を保持します．`--scenario` の有無でbackendは変わりません．
