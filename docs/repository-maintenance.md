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

このブランチは現行v2専用です．旧評価系52本と専用テスト32本は削除し，Pythonは `tools/` が70本から20本（直下4本，package内16本），`tests/` が36本から4本になりました．旧変換器のYAML loaderだけを `workflow_model_v2/workflow_loader.py` へ切り出し，旧評価系への依存をなくしました．

旧論文評価は既存の `research/automatic-temporal-verification` に残っています．整理直前の全84ファイルは保存タグ `archive/v2-before-tool-pruning`（`9827794`）ともblobを照合済みです．[削除一覧と保存先](v2-tool-removal-inventory.md)．`tools/legacy/` も削除しました．

CIはv2の回帰テストへ合わせ，削除した旧replayや事例別scriptの呼出しを除いています．過去の資料に残る旧scriptの手順は保存版用です．現行の入口は [tools/README.md](../tools/README.md) を参照してください．

## 評価結果

出力を圧縮して個別ファイルを削除する方針は，ユーザーの指示により撤回しました．過去の3,313ファイルをSHA-256照合付きで元の配置へ復元し，直近3回のrunの全詳細も復元しています．archiveも追加の保存用として維持します．旧 `results/` と研究YAMLはそのまま保持します．

新しい評価は `outputs/runs/` に入力・SMV・対応表・ログ・反例をそのまま残します．任意のarchive作成も原本を削除しません．`outputs/runs/` はGit管理外ですが，コンテナを停止してもホストに残ります．採用する証拠は明示的にGitへ追加します．[保存済み出力の索引](../outputs/model-v2/INDEX.md)．

シェルrunnerを廃止し，ルートの `Dockerfile` と `compose.yaml` へ移行しました．起動・入室・モデル生成・NuSMV検証・集計は[コンテナ操作手順](container-guide.md)に分けて記載しています．

## 退避

整理前の全branch/stashのbundleと未コミット作業ツリー全体は，この作業のチャットworkspaceにある `maintenance-backups/2026-10-22/` へ保存しました．元ref名とSHAも同じ場所に記録しています．作業ツリー退避はGit未追跡だったLunaのコードと出力も含みます．

既存stashは削除していません．CommitとPushは今回の明示許可に基づき実施します．ブランチ削除は最新確認時のSHAが変わっていないことを条件にし，新規開発ブランチの公開と同時に反映します．削除対象のコミットは研究基準ブランチに含まれ，削除前のbundleにも保存されています．

## 前回の整理時点の確認（出力の削除は後に撤回）

- ローカルブランチ：14本から4本．基準branchと未統合backupは保持．
- 保存済み評価：展開済み3,313ファイルから，要約・archive・索引の13ファイル．全3,313 memberを再照合済み．
- 出力のdisk使用量：約34MBから約3.9MB（新しいGit除外runも含む）．
- 整理後のDocker検証：49テスト成功，65モデル中64モデルでproperty結果取得，意図した構文不正対照1件．BFS22ケース一致．
- 新規run：詳細1,316ファイルを検証済archiveへまとめ，通常見える出力は6ファイル．`outputs/runs/repository-cleanup-check/` に保存し，Gitへ追加しない．
- 前回の整理では `tools/legacy/` の6 CLIの起動を確認した．今回は保存タグへ固定して削除した．

旧採用結果latestの45テストはv2機能の評価時点の数で，整理後の49テストには無損失圧縮の4テストを加えている．モデルの判定結果を変える整理ではない．

## 前回のv2専用化時点の確認

- Dockerで全テスト探索を実行し，残した4テストファイルの49テストが成功した．
- `outputs/runs/v2-tools-only-check/` で全評価を再実行した．65モデル中64件の検証と意図した構文不正対照1件を確認し，全65件のproperty判定・終了コードが削除前と一致した．独立BFS22ケースとの照合も一致し，予期しない失敗は0件だった．
- v2 CLIの起動とCI YAMLの読込みを確認した．GitHub上のCI実行結果を示すものではない．
- 削除前に残存コードの静的importと49テストの実行時importを確認し，旧52ツールが依存に含まれないことを検査した．削除前に自動承認レビューが指摘した旧CIのscript参照は，v2用CIへの変更で解消した．
- 追加評価の出力は6ファイルに圧縮し，Git管理外とした．既存の採用済み評価・研究資料は変更していない．

## コンテナ操作と出力保持への変更

- `Dockerfile` と `compose.yaml` を追加し，Python 3.12.11・PyYAML 6.0.3・NuSMV 2.6.0を同梱した `linux/amd64` イメージをbuildした．
- 常駐コンテナの起動・対話入室，外部からの `exec`，単発 `run --rm`，ソースのホストマウントなしの `docker run` で生成と検証を確認した．手順は [container-guide.md](container-guide.md) に記載した．
- シェルrunner2本を廃止し，YAML→SMV生成，NuSMV検証，集計を別コマンドにした．個別モデルの検証でもstdout・stderr・終了コード・実行時間・状態数を保存する．
- 過去の3,313ファイルと直近3回のrunの詳細各1,316ファイルを，archiveと全memberのSHA-256照合後に元の配置へ復元した．当時のREADME原本も復元し，現在の案内は `outputs/model-v2/INDEX.md` に分けた．archiveはそのまま残した．
- 自動圧縮・原本削除を廃止した．任意のarchive作成も追加コピーだけを作る．モデル生成，検証ログ，集計は既存出力がある場合に上書きせず停止する．
- 統合コンテナの51テストが成功した．65モデル中64件が検証完了，構文不正対照1件は意図した失敗，予期しない失敗0件，独立BFS22ケース一致．入力ファイルの出力パスに由来する識別子を正規化した比較で，全65件のproperty判定と終了コードが前回と一致した．
- 新しい全評価は `outputs/runs/container-full-check/` に個別ファイルのまま保存した．モデルや反例の解釈・未対応範囲は従来のv2と変わらない．
