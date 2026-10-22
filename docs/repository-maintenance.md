# ブランチ・ツール・評価出力の管理

## ブランチ

| 名前 | 役割 |
|---|---|
| `main` | 公開側の基準．今回更新しない |
| `research/paper-baseline` | 既存の論文評価基準 `8ecb091` を固定 |
| `feat/workflow-model-v2` | Luna prototypeとv2拡張，今回の整理を保存する開発ブランチ |
| `archive/comment-backup-2026-09-23` | 未統合commitと既存stashに関係する退避．廃棄しない |

新規作業は `feat/<機能名>`，不具合修正は `fix/<対象>` を使います．A1等の評価ケースごと，出力結果だけのためにはブランチを作りません．統合済みの作業ブランチは整理し，未統合のものは内容確認なしに消しません．`research/` は固定比較基準，`archive/` は必要な退避に限定します．

今回，元の全refをGit bundleへ退避・検証してから，現在の研究HEADへ統合済みだったローカル11ブランチを削除しました．消失済み `/private/tmp/tbv-a5-worktree` のmetadataも除きました．リモートブランチはPush禁止の範囲を維持して変更していません．名前やupstream設定はローカルで整理したもので，GitHub側をrenameしたものではありません．

## Python

現行の入口と依存関係は [tools/README.md](../tools/README.md) を参照してください．source/test/docs等の参照を調べ，未参照の単独CLI6本を `tools/legacy/` へまとめました．「未参照」は未使用の証明ではなく，過去の再現用として価値があるため，コードの破棄はしていません．

既存 `build_trust_chain.py`，`chain_to_nusmv.py` 等は現行解析・テストから参照されています．rootの見た目だけを理由に一括削除・移動すると再現性を壊すため維持します．新しいv2機能はpackage内へ追加し，rootに実験ごとのscriptを増やさない運用へ切り替えます．

## 評価結果

採用した要約は `outputs/model-v2/latest/`．全詳細は `outputs/model-v2/archive/` に無損失保存します．過去の3,313ファイルはmember単位で往復hash検査を行った後に，展開済みコピーだけを整理しました．旧 `results/` と研究YAMLの内容は変更していません．

新しい評価は `outputs/runs/` へ出し，通常は成功後に自動圧縮します．要約JSON・runtime・Docker image情報を直接読めるよう残し，SMV／trace／source map／ログ／一時YAMLは1つのdetails archiveへまとめます．Gitへは採用した要約とarchiveだけを明示して追加し，全runを追加しません．

archive作成後は全memberのhashを照合し，その後にのみ展開済みファイルを除きます．照合失敗や既存archive衝突時は原本を残して停止します．archiveの削除はこの自動処理の対象にしません．

古い報告にある元パスはarchiveの中で復元できます．[保存場所・展開方法](../outputs/model-v2/README.md)．

## 退避

整理前の全branch/stashのbundleと未コミット作業ツリー全体は，この作業のチャットworkspaceにある `maintenance-backups/2026-10-22/` へ保存しました．元ref名とSHAも同じ場所に記録しています．作業ツリー退避はGit未追跡だったLunaのコードと出力も含みます．

既存stashは削除していません．Commitは今回の明示許可に基づくローカル保存のみで，Pushは行いません．

## 整理後の確認

- ローカルブランチ：14本から4本．基準branchと未統合backupは保持．
- 保存済み評価：展開済み3,313ファイルから，要約・archive・索引の13ファイル．全3,313 memberを再照合済み．
- 出力のdisk使用量：約34MBから約4.2MB（新しいGit除外runも含む）．
- 整理後のDocker検証：49テスト成功，65モデル中64モデルでproperty結果取得，意図した構文不正対照1件．BFS22ケース一致．
- 新規run：詳細1,316ファイルを検証済archiveへまとめ，通常見える出力は6ファイル．`outputs/runs/repository-cleanup-check/` に保存し，Gitへ追加しない．
- `tools/legacy/` の6 CLIは移動後の `--help` 起動を確認した．

旧採用結果latestの45テストはv2機能の評価時点の数で，整理後の49テストには無損失圧縮の4テストを加えている．モデルの判定結果を変える整理ではない．
