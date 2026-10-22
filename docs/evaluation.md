# v2の検証結果と未対応項目

現行の対応範囲は[仕様](semantics.md)，再実行は[コンテナ操作](container-guide.md)を参照してください．過去の計画書や実装途中の報告は保存タグ `archive/v2-before-repository-pruning` に残しています．

## 確認済みの結果

- Docker内の回帰テスト：51件成功．出力保持・復元・破損検出の検査を含む．
- 全評価：65モデル．64件でNuSMVのCTL結果・遷移全域性・非空の到達状態集合を取得．意図した構文不正の対照1件を区別する．
- 独立BFS：22ケースについてtransfer/use/sink/success/bad-authorityの5項目を照合し，全件一致．
- A1～A5と旧21 mutationの判定は維持．未対応をunknown/unsupportedとして残し，CTLがtrueだっただけで安全と扱わない．

採用済みの詳細は[出力索引](../outputs/model-v2/INDEX.md)，[65モデルの保存済み集計](../outputs/model-v2/latest/verification-summary.json)から確認できます．新規runは `outputs/runs/` に入力・SMV・ログ・反例・対応表を個別ファイルのまま保持します．

## Artifactの評価

| ケース | 成果物に依存する利用 | 模擬危険経路 |
|---|---|---|
| A1 | 到達可能 | 到達可能 |
| A2 | 到達可能 | 不達（モデル内） |
| A3 | 不達（モデル内） | 不達（モデル内） |
| A4 | 到達可能 | 不達（モデル内） |
| A5 | 到達可能 | 到達可能 |

A2では検証前のreadを追加した履歴監査も行い，後の遮断が過去の利用を消さないことを確認しています．この表は模擬地点の到達性であり，実GitHub上の公開やrepository更新の成功を示しません．

## Cacheと実事例

Cacheのprefix一致，scope隔離，version・repository不一致，書込み無効化，権限欠落，ステージング欠落，post-save，状態関数，有限context・output相関などを対照構成で確認しています．

TanStackは事件前と対策版のworkflow，明示的にhashを固定したComposite metadataを入力として保持しています．ただし原本にあるpnpmや外部Actionの意味を完全には扱えず，現行v2では全経路の再現に達していません．旧変換器の結果をv2の結果として流用しません．

## 残っている制約

Reusable workflowのcaller側意味論，concurrency，任意Bash，GITHUB_ENV，汎用hashFiles，OIDC・secret伝播，動的matrix，Artifact overwriteや複数download layoutなどは未対応です．詳細な境界は[対応範囲表](semantics.md#対応範囲表)を参照してください．

旧評価の論文用68構成，CodeQL等との比較，個別事件の旧解析は保存版の研究成果です．現行の65モデルとは別の評価系です．
