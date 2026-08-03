# GHA-C1：zizmorによる初回検出結果

## 実験の目的

GHA-C1及びGHA-C2を含むGitHub Actions workflowにzizmorを適用し，CodeQLとの検出範囲の違いを確認する．

## 実行条件

| 項目 | 内容 |
| --- | --- |
| 実行日 | 2026年8月3日 |
| 対象repository | `formal-ci-cd/trust-boundary-verification` |
| 対象directory | `.github/workflows` |
| 実行方法 | GitHub Actions上の`zizmor.yml` |
| zizmor Action | `zizmorcore/zizmor-action`，`v0.5.6`に対応するcommitへ固定 |
| online audit | 無効．workflow定義だけを用いる初回比較とした． |
| 結果の保存先 | GitHub Security and qualityのCode scanning alerts |

## 観測結果

zizmor workflow自体は正常終了した．Code scanning画面では，zizmorが検出した10件のopen alertと，既存のCodeQL alert 1件が表示された．

| 分類 | 件数 | 対象 | 解釈 |
| --- | ---: | --- | --- |
| `pull_request_target`の危険な利用 | 1 | GHA-C1 | 未信頼なPRを扱う実験構成の起動契機を検出した． |
| SHA固定されていないaction参照 | 8 | GHA-C1，GHA-C2，CodeQL，zizmor workflow | `@v4`のようなtag参照を，commit hash固定ではないとして検出した． |
| checkout時のcredential persistence | 1 | CodeQL workflow | `persist-credentials: false`が設定されていないcheckoutを検出した． |

zizmorのalert一覧には，CodeQLが報告した`Cache Poisoning via caching of untrusted files`に対応する，Cache保存経路そのものを示すalertは観測されなかった．

## CodeQLとの比較

| ツール | GHA-C1に関する主な検出 | この段階で分かること |
| --- | --- | --- |
| CodeQL | 未信頼なPR内容をdefault branch文脈のCacheへ保存する経路をHighとして検出した． | 未信頼入力とCache書込みの組合せを検出できる． |
| zizmor | `pull_request_target`の危険な利用を検出した． | 危険なevent及びworkflow設定上の問題を検出できる． |

両ツールとも，後続workflowが同一Cache objectをrestoreし，復元内容を完全性確認なしに利用するかまでは示していない．

## 取り扱い方針

- GHA-C1の`pull_request_target` alertは，検出実験で意図的に置いた構成に対応する．実験結果を確定するまでdismissしない．
- SHA固定されていないaction参照及びCodeQL workflowのcredential persistenceは，Cache信頼境界の比較対象とは別の設定上の改善点である．実験結果の記録後，別PRで修正する．
- 次のCache実験では，producerだけでなくconsumerによるrestore及び利用を明示し，ツールの検出範囲と形式モデルの判定範囲を比較する．
