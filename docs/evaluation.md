# v2の検証結果と未対応項目

現行の対応範囲は[仕様](semantics.md)，再実行は[コンテナ操作](container-guide.md)を参照してください．過去の計画書や実装途中の報告は保存タグ `archive/v2-before-repository-pruning` に残しています．

## 確認済みの結果

- Docker内の回帰テスト：55件成功．出力保持・復元・破損検出の検査を含む．
- 全評価：71モデル．70件でNuSMVのCTL結果・遷移全域性・非空の到達状態集合を取得．意図した構文不正の対照1件を区別する．
- 独立BFS：22ケースについてtransfer/use/sink/success/bad-authorityの5項目を照合し，全件一致．
- 全モデルを同じbackendで生成する．A1～A5の保存済み50項目は一致．旧21 mutationも固定した期待値と照合し，不正metadata時の後続stepだけは下記の修正を適用する．unsupportedをCTLのtrueだけで安全と扱わない．

全評価の生成・検証・集計を実行すると，指定した出力先の `verification-summary.json` に判定と照合結果を保存します．`outputs/` はGit管理外です．回帰比較に必要なA1～A5の期待値は `tests/fixtures/prototype-properties.json` にあります．

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

旧評価の論文用68構成，CodeQL等との比較，個別事件の旧解析は保存版の研究成果です．現行の71モデルとは別の評価系です．

## backend統合の検査

旧Artifact builderを削除し，CLI・Artifact・Cache・対照構成を共通schedulerとオブジェクト状態へ移した．旧21 mutationはSMV変数への文字列注入をやめ，`stepOutcomes`，`checkoutValues`，`workflowTrusted` を入力条件として指定する．

追加の6ケースで，Shell変数の改名，guardを外した不一致後利用，別ファイルへの検証状態の漏れ，検証後の書換え，consumerを先に宣言した場合，同一step内の検証後利用を確認する．

旧版との差は `a5-invalid-metadata` の `AG !simulated_sink_site_reached` で，falseからtrueになる．文字種・長さの検査失敗でShell stepが失敗し，後続の既定success条件のstepが実行されなくなるためである．危険経路の到達判定が変わったものではなく，旧版で残っていた模擬地点への到達を抑える修正である．比較用fixtureに変更理由と変更前後の期待値を記録している．
