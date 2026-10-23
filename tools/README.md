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

`workflow_model_v2/compiler.py` が唯一のSMV生成入口です．`external_conditions.py`が条件IR・候補生成・制限とguard付き遷移の結合，`scenario.py`が従来の固定context・file候補，`dataflow.py` が実行時env・output・hashFiles，`shell_lowering.py` が内容・完全性・利用履歴を扱い，`transition.py` へ下げます．`frontend.py` と `workflow_shell_subset.py` が構文と行位置を保持します．`--scenario`の有無でbackendは変わりません．完全な旧scenarioは固定contextとして互換実行し，部分JSONは自動候補への制限として扱います．`conditional_verification.py`が反例の条件を復号し，Booleanの反対条件を同じログ内で追加検証します．

NuSMVの実行記録は`model.smv.log`の1ファイルです．コマンド，標準出力・標準エラー，反例，時間，終了コード，到達状態数を確認できます．集計処理はこのログと保存済みの旧形式の両方を読めます．詳細は[結果の読み方](../docs/container-guide.md#6結果を読むコンテナ内)を参照してください．

## ファイル名とモジュール名

検証プログラムの実体は `tools/workflow_model_v2/verification.py` です．コマンド表はこのファイルを直接指定しています．

`python3 -m workflow_model_v2.verification` は，同じファイルをPythonのmodule検索で起動する別の書き方です．`workflow_model_v2.verification.py` というファイルを探す指定ではありません．Dockerfileが `PYTHONPATH=/repo/tools` を設定しているため，コンテナ内でこの名前を解決できます．

`producer.yml` と `consumer.yml` は指定する入力パスの例です．実際に存在するYAMLへ置き換えてください．Consumerは省略できます．`examples/` のArtifactデモはYAMLだけを指定し，到達版とsafe版を比較できます．scenarioは任意です．自動で置いた有限条件は対応表と検証ログへ記録します．詳細は[操作手順](../docs/container-guide.md#examplesの入力を試す場合)を参照してください．

## 条件の入口

生成CLIは`--event`，`--activity`，`--fork true|false`，`--head-branch`，`--base-branch`，`--default-branch`，`--repository`，`--repository-visibility public|private|internal`，`--runner-os`，`--runner-arch`，`--send-write-tokens true|false`，`--workflow-present true|false`を受け付けます．`--run run0`等で対象を指定し，JSONとの競合はエラーにします．生成とNuSMV検証は別コマンドです．検証CLIは`--property dependent_use`等で条件別報告のpropertyを選べます．

詳細な部分JSON，探索上限，反例中の値と必要条件の違いは[操作手順](../docs/container-guide.md#外部条件の自動探索と条件指定)，有限domainと未対応の範囲は[仕様](../docs/semantics.md#外部条件irと探索)を参照してください．
