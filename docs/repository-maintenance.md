# 保存先とリポジトリの方針

## 現行ブランチ

`feat/workflow-model-v2` はv2の実装・テスト・コンテナ操作に絞ります．CodeQL query/packとそのCI，旧zizmor比較，v2が読まない実験入力，旧schema・入力JSON，途中計画や旧事件別資料は現行ブランチから削除しました．

`tools/` はv2の生成器・独立探索・検証・出力保存，`tests/` は現行回帰テストです．`.github/workflows/` のArtifact用7 YAMLと `experiments/public-cases/tanstack/` の最小snapshotはv2の入力なので保持します．actionlintとv2回帰CIも保持します．入力YAMLの既存配置は変更していません．

`docs/` は次の4本です．手順と仕様・評価・保管情報の重複を減らし，途中の計画や日付別報告は保存タグへ分けます．

| 文書 | 内容 |
|---|---|
| [container-guide.md](container-guide.md) | 起動，入室，生成，検証，結果確認，外からの単発実行 |
| [semantics.md](semantics.md) | モデルの意味，対応範囲，未対応・仮定 |
| [evaluation.md](evaluation.md) | 現行の検証結果と未達成項目 |
| この文書 | 旧版の保存先，出力保持，整理の方針 |

## 旧SMV変換器の保存先

| 版 | 保存先 | 内容 |
|---|---|---|
| v2以前の論文評価系 | [research/automatic-temporal-verification](https://github.com/formal-ci-cd/trust-boundary-verification/tree/research/automatic-temporal-verification) | `8ecb091`．旧YAML/CodeQL→共通モデル→SMV変換器，旧評価と資料 |
| 同じ固定基準のローカル名 | `research/paper-baseline` | 上と同じ `8ecb091` |
| Python整理直前 | [archive/v2-before-tool-pruning](https://github.com/formal-ci-cd/trust-boundary-verification/tree/archive/v2-before-tool-pruning) | `9827794`．旧評価ツールとv2が同居した版 |
| 今回の資産・文書整理直前 | [archive/v2-before-repository-pruning](https://github.com/formal-ci-cd/trust-boundary-verification/tree/archive/v2-before-repository-pruning) | `26e720a`．コンテナ対応v2，全旧docs・CodeQL・旧実験入力を含む |

旧研究ブランチには `tools/yaml_to_model.py`，`tools/codeql_csv_to_model.py`，`tools/build_trust_chain.py`，`tools/model_to_nusmv.py`，`tools/chain_to_nusmv.py` が残っています．v2以前のSMV変換器を使う場合はその版で実行してください．整理直前のコンテナ対応v2は今回の保存タグから再現できます．

過去の削除一覧・日付別報告は保存タグ内の `docs/` にあります．現行の手順へ古いコマンドを混ぜません．`main` と旧研究ブランチは更新せず，変更はv2ブランチにCommit・Pushします．未統合の退避branchとstashは保持します．

## 出力は削除しない

`outputs/` と `results/` は今回の削除対象に含めません．旧 `model/nusmv/` の生成SMV，`model/chains/`・`model/generated-chains/`，評価JSON・観測JSON，事件の観測記録も元の配置に保持します．旧schemaや入力例を取り除いても，過去の結果は残します．

以前圧縮した3,313ファイルと直近3回のrunの詳細は復元済みです．[照合記録](../outputs/model-v2/restoration-2026-10-22.json)と[出力索引](../outputs/model-v2/INDEX.md)を参照してください．archiveは追加の保存用に残しています．

新規runは `outputs/runs/` に個別ファイルのまま残し，自動圧縮・削除を行いません．既存モデル・検証ログ・集計の上書きは拒否します．このディレクトリはGit管理外ですが，コンテナを停止してもホストに残ります．採用する結果は明示的にGitへ追加します．

## 整理時の確認

削除対象は整理前の保存タグと内容を照合済みです．削除後のイメージをbuildし，ホストのソースをマウントせずに51テストが成功しました．`outputs/runs/v2-assets-pruning-check/` の65モデルは従来とproperty判定・終了コードが一致し，独立BFS22ケースも全件一致しました．docs4本とREADMEのリンク切れも検査済みです．旧版への移動が必要な場合も，未保存の変更がある作業ディレクトリをそのまま切り替えず，保存タグ・ブランチの別checkoutを使用してください．
