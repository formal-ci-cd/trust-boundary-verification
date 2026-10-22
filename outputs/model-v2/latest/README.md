# 最新の採用評価

2026-10-22のv2拡張評価：45回帰テスト成功，65モデル中64件のpropertyを取得，意図的構文不正対照1件．独立BFS22ケースで到達可能性が一致した．A1～A5／既存21 mutationの共通結果も一致．TanStackはunknown/unsupported．

- [property結果](verification-summary.json)
- [unknown/unsupported](unknown-unsupported-inventory.json)
- [集計](report-metrics.json)
- [実装・評価報告](../../../docs/next-model-design/extension-report-2026-10-22.md)
- [詳細archiveと復元手順](../README.md)

この評価を採用した際のGit状態や保存確認は当時の記録であり，後のファイル再配置を監査した結果ではない．全trace，source map，SMV，入力YAML，runtimeログ，監査backupは extension-2026-10-22.tar.gz 内の元パスに保持している．
