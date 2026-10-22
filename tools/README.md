# v2の入口

操作手順は [コンテナで段階的にv2を使う](../docs/container-guide.md) にまとめています．ルートの `Dockerfile` と `compose.yaml` でPython・NuSMV入りのコンテナを作ります．

| 段階 | コンテナ内の入口 |
|---|---|
| 回帰テスト | `python3 -m unittest discover -s tests -v` |
| 個別YAMLからモデル生成 | `python3 tools/workflow_to_smv_v2.py producer.yml consumer.yml --output /out/demo/model.smv` |
| SMV検証とログ保存 | `python3 -m workflow_model_v2.verification /out/demo/model.smv` |
| 全評価用の生成 | `python3 tools/evaluate_workflow_v2_extension.py generate --output /out/full` |
| 全評価モデルの検証 | `python3 -m workflow_model_v2.verification /out/full` |
| 判定・BFS照合・反例の集計 | `python3 tools/evaluate_workflow_v2_extension.py summarize --output /out/full` |

Pythonは直下4本とpackage内16本です．`workflow_to_smv.py` は共通parser，`workflow_shell_subset.py` は限定Shell parserとして必要です．packageはIR，遷移，SMV生成，Action契約，式評価，独立探索，ログ保存を担当します．

シェルrunnerを廃止し，モデル生成・検証・集計を明示的に分けました．自動圧縮・出力削除は行いません．任意の `evidence backup` も元ファイルを残してarchiveを追加するだけです．

旧評価系52本と専用テスト32本はこのブランチから削除済みです．旧論文の再現には `research/automatic-temporal-verification` を使ってください．整理直前の全ファイルは保存タグ `archive/v2-before-tool-pruning` に固定しています．[削除一覧と照合済みblob](../docs/v2-tool-removal-inventory.md)．過去の資料にある旧scriptの手順は保存版用です．
