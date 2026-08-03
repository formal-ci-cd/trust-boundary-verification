# GHA-C1：CodeQLによるCache Poisoning検出結果

## 実験の目的

GitHub Actionsにおいて，未信頼なPull Request由来の内容をdefault branch文脈のCacheへ保存する構成を，CodeQLが検出できるか確認する．

## 対象構成

| 項目 | 内容 |
| --- | --- |
| 実験ID | GHA-C1 |
| 対象workflow | `.github/workflows/cache-c1-untrusted-default-context.yml` |
| Pull Request | [#2](https://github.com/formal-ci-cd/trust-boundary-verification/pull/2) |
| 起動契機 | `pull_request_target`（`opened`，`synchronize`，`reopened`） |
| 未信頼な入力 | `github.event.pull_request.head.sha`でcheckoutするPRのhead commit |
| Cacheへの保存対象 | `.research-cache-input` |
| Cache key | `trust-boundary-c1-default-context-v1` |
| 権限 | `contents: read`のみ |

このworkflowは，検出実験のために意図的に危険候補の構成を含めている．実在のsecret，deploy権限，self-hosted runner，任意script実行は使用していない．

## 観測結果

2026年8月3日，Pull Request #2に対するCodeQL analysisが完了し，次のHigh severityの指摘が表示された．

| 項目 | 内容 |
| --- | --- |
| ツール | GitHub CodeQL |
| 指摘名 | `Cache Poisoning via caching of untrusted files` |
| 深刻度 | High |
| 指摘箇所 | `cache-c1-untrusted-default-context.yml`の`actions/cache/save@v4`によるCache保存ステップ |
| 指摘の要旨 | `pull_request_target`で未信頼なPRのheadをcheckoutした後，default branch文脈でCacheを保存しているため，Cache poisoningの可能性がある． |

PR画面には，`Potential cache poisoning in the context of the default branch due to privilege checkout of untrusted code. (pull_request_target)`と表示された．

## 解釈

GHA-C1は，未信頼なPR内容を取得する経路と，default branch文脈でCacheを書き込む経路を同一workflow内に持つ．CodeQLはこの組合せを検出できた．

ただし，この結果だけでは，後続の特権workflowが同一Cache objectを復元して利用することまでは示されない．後続実験では，Cache key及びscopeの一致，後続workflowによるrestore，復元内容の利用，及びGitHub ActionsのCache隔離仕様を確認する．これは，静的解析の検出範囲と，研究で目指す実行間の信頼境界検証との差分として扱う．

## 対応方針

PR上の自動修正提案は，Cache保存ステップを削除する内容である．通常の運用workflowなら採用を検討するが，本実験では検出対象を保持する必要があるため採用しない．

比較対象のGHA-C2では，`pull_request`を用い，PR番号を含むCache keyで保存する．GHA-C2に同種の指摘が出ないかを次に確認する．
