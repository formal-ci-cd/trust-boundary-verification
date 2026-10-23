# 実行コマンド

以下はコンテナ内の `/repo` で実行します．ホスト側で直接Pythonを起動する案内ではありません．起動方法は[コンテナ操作手順](../docs/container-guide.md)を参照してください．

| 処理 | コマンド |
|---|---|
| 回帰テスト | `python3 -m unittest discover -s tests -v` |
| YAML→SMV | `python3 tools/workflow_to_smv_v2.py producer.yml consumer.yml --output /out/example/model.smv` |
| SMV検証・ログ保存 | `python3 tools/workflow_model_v2/verification.py /out/example/model.smv` |
| 全評価のモデル生成 | `python3 tools/evaluate_workflow_v2_extension.py generate --output /out/full` |
| 全評価の検証 | `python3 tools/workflow_model_v2/verification.py /out/full` |
| 判定・反例の集計 | `python3 tools/evaluate_workflow_v2_extension.py summarize --output /out/full` |

`workflow_model_v2/compiler.py` が唯一のSMV生成入口です．`scenario.py` が外部条件，`dataflow.py` が実行時env・output・hashFiles，`shell_lowering.py` が内容・完全性・利用履歴を扱い，`transition.py` へ下げます．`frontend.py` と `workflow_shell_subset.py` が構文と行位置を保持します．`--scenario` の有無でbackendは変わりません．

NuSMVの実行記録は`model.smv.log`の1ファイルです．コマンド，標準出力・標準エラー，反例，時間，終了コード，到達状態数を確認できます．集計処理はこのログと保存済みの旧形式の両方を読めます．詳細は[結果の読み方](../docs/container-guide.md#6結果を読むコンテナ内)を参照してください．

## ファイル名とモジュール名

検証プログラムの実体は `tools/workflow_model_v2/verification.py` です．コマンド表はこのファイルを直接指定しています．

`python3 -m workflow_model_v2.verification` は，同じファイルをPythonのmodule検索で起動する別の書き方です．`workflow_model_v2.verification.py` というファイルを探す指定ではありません．Dockerfileが `PYTHONPATH=/repo/tools` を設定しているため，コンテナ内でこの名前を解決できます．

`producer.yml` と `consumer.yml` は指定する入力パスの例です．実際に存在する2つのYAMLへ置き換えてください．`examples/` のArtifactデモは共通scenarioを指定し，到達版とsafe版を比較できます．詳細は[操作手順](../docs/container-guide.md#examplesの入力を試す場合)を参照してください．
