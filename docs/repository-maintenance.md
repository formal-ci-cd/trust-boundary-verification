# ブランチ・ツール・評価出力の管理

## ブランチ

| 名前 | 役割 |
|---|---|
| `main` | 公開側の基準．今回更新しない |
| `research/paper-baseline` | 既存の論文評価基準 `8ecb091` を固定 |
| `feat/workflow-model-v2` | Luna prototypeとv2拡張，今回の整理を保存する開発ブランチ |
| `archive/comment-backup-2026-09-23` | 未統合commitと既存stashに関係する退避．廃棄しない |

新規作業は `feat/<機能名>`，不具合修正は `fix/<対象>` を使います．A1等の評価ケースごと，出力結果だけのためにはブランチを作りません．統合済みの作業ブランチは整理し，未統合のものは内容確認なしに消しません．`research/` は固定比較基準，`archive/` は必要な退避に限定します．

今回，元の全refをGit bundleへ退避・検証してから，現在の研究HEADへ統合済みだったローカル11ブランチを削除しました．消失済み `/private/tmp/tbv-a5-worktree` のmetadataも除きました．その後，ユーザーのPush許可を受けてGitHubの最新refと進行中PRを確認し，統合済みのリモート12ブランチも整理しました．`feat/workflow-model-v2` を公開し，GitHub側は15本から4本になります．PR #15が使う `research/automatic-temporal-verification` は元の名前で保持し，未統合commitが1件ある `experiment/github-actions-artifact-runtime` も残します．`main` と合わせ，GitHub側に残すのはこの4本です．ローカルの `research/paper-baseline` は既存PRのheadを動かさずに基準を示す名前です．

## Python

このブランチは現行v2専用です．旧評価系52本と専用テスト32本は削除し，Pythonは `tools/` が70本から19本（直下4本，package内15本），`tests/` が36本から4本になりました．旧変換器のYAML loaderだけを `workflow_model_v2/workflow_loader.py` へ切り出し，旧評価系への依存をなくしました．

旧論文評価は既存の `research/automatic-temporal-verification` に残っています．整理直前の全84ファイルは保存タグ `archive/v2-before-tool-pruning`（`9827794`）ともblobを照合済みです．[削除一覧と保存先](v2-tool-removal-inventory.md)．`tools/legacy/` も削除しました．

CIはv2の回帰テストへ合わせ，削除した旧replayや事例別scriptの呼出しを除いています．過去の資料に残る旧scriptの手順は保存版用です．現行の入口は [tools/README.md](../tools/README.md) を参照してください．

## 評価結果

採用した要約は `outputs/model-v2/latest/`．全詳細は `outputs/model-v2/archive/` に無損失保存します．過去の3,313ファイルはmember単位で往復hash検査を行った後に，展開済みコピーだけを整理しました．旧 `results/` と研究YAMLの内容は変更していません．

新しい評価は `outputs/runs/` へ出し，通常は成功後に自動圧縮します．要約JSON・runtime・Docker image情報を直接読めるよう残し，SMV／trace／source map／ログ／一時YAMLは1つのdetails archiveへまとめます．Gitへは採用した要約とarchiveだけを明示して追加し，全runを追加しません．

archive作成後は全memberのhashを照合し，その後にのみ展開済みファイルを除きます．照合失敗や既存archive衝突時は原本を残して停止します．archiveの削除はこの自動処理の対象にしません．

古い報告にある元パスはarchiveの中で復元できます．[保存場所・展開方法](../outputs/model-v2/README.md)．

## 退避

整理前の全branch/stashのbundleと未コミット作業ツリー全体は，この作業のチャットworkspaceにある `maintenance-backups/2026-10-22/` へ保存しました．元ref名とSHAも同じ場所に記録しています．作業ツリー退避はGit未追跡だったLunaのコードと出力も含みます．

既存stashは削除していません．CommitとPushは今回の明示許可に基づき実施します．ブランチ削除は最新確認時のSHAが変わっていないことを条件にし，新規開発ブランチの公開と同時に反映します．削除対象のコミットは研究基準ブランチに含まれ，削除前のbundleにも保存されています．

## 整理後の確認

- ローカルブランチ：14本から4本．基準branchと未統合backupは保持．
- 保存済み評価：展開済み3,313ファイルから，要約・archive・索引の13ファイル．全3,313 memberを再照合済み．
- 出力のdisk使用量：約34MBから約3.9MB（新しいGit除外runも含む）．
- 整理後のDocker検証：49テスト成功，65モデル中64モデルでproperty結果取得，意図した構文不正対照1件．BFS22ケース一致．
- 新規run：詳細1,316ファイルを検証済archiveへまとめ，通常見える出力は6ファイル．`outputs/runs/repository-cleanup-check/` に保存し，Gitへ追加しない．
- 前回の整理では `tools/legacy/` の6 CLIの起動を確認した．今回は保存タグへ固定して削除した．

旧採用結果latestの45テストはv2機能の評価時点の数で，整理後の49テストには無損失圧縮の4テストを加えている．モデルの判定結果を変える整理ではない．

## v2専用化後の確認

- Dockerで全テスト探索を実行し，残した4テストファイルの49テストが成功した．
- `outputs/runs/v2-tools-only-check/` で全評価を再実行した．65モデル中64件の検証と意図した構文不正対照1件を確認し，全65件のproperty判定・終了コードが削除前と一致した．独立BFS22ケースとの照合も一致し，予期しない失敗は0件だった．
- v2 CLIの起動とCI YAMLの読込みを確認した．GitHub上のCI実行結果を示すものではない．
- 削除前に残存コードの静的importと49テストの実行時importを確認し，旧52ツールが依存に含まれないことを検査した．削除前に自動承認レビューが指摘した旧CIのscript参照は，v2用CIへの変更で解消した．
- 追加評価の出力は6ファイルに圧縮し，Git管理外とした．既存の採用済み評価・研究資料は変更していない．
