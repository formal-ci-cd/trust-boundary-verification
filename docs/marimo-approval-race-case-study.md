# 公開脆弱性GHSL-2026-226における承認時刻と成果物の信頼境界

## 結論と評価対象

[GitHub Security Labの公開報告](https://securitylab.github.com/advisories/GHSL-2026-226_marimo-team_marimo_repository/)が対象にしたmarimoのコミット `b4181abaf6bd896b6b0dac5490c9cf08269b590c` では，承認コメント後にPRを同じ秒のうちに更新すると，`pushed_at > comment.created_at` の検査を通過します．更新後のSHAをcheckoutし，そのコードが生成した成果物がTestPyPIとnpm用の後続jobへ渡ります．本研究の限定した自動解析は，この経路と，同じ秒の更新がnpm公開手順に届く反例を検出しました．

評価する性質は「承認コメント後に更新されたPRの内容が，`id-token: write`を設定し環境保護を宣言していない公開手順に届かない」です．実際にnpmで公開できたか，外部のOIDC信頼設定が成立したか，悪意ある者が本事件で攻撃したかは評価対象の外です．[修正PR #10765](https://github.com/marimo-team/marimo/pull/10765)では問題のworkflow全体が削除されています．maintainerは削除理由で，そのworkflowが以前から機能していなかったと説明しています．そのためこの事例を「実際に侵害された事件」とは扱いません．

## 入力と比較条件

`experiments/public-cases/marimo/manifest.json` に原本の脆弱版・削除版のコミット，`.github/workflows`と`.github/actions`の全取得ファイル，LICENSE，SHA-256を記録しました．取得元をcloneした後にコミットを固定し，YAMLとlocal Action定義を無改変で取り出しています．CodeQLは対象のGitHub Actionsファイル32件中32件を抽出しました．repository内のbuild scriptや外部Actionの実装は入力に含めていません．

比較用に，脆弱版の `if [[ ... -gt ... ]]` だけを `-ge` に変える対照版を生成しました．それ以外のファイルと行は一致します．対照版は本研究の介入であり，upstreamの修正ではありません．`-ge` は同じ秒の正当な更新も拒否し得るため，運用上の最適な対策を示すものでもありません．安全性の因果比較だけに使います．

| 入力 | CodeQL default / security-and-quality | zizmor regular | actionlint | 提案側の判定 |
|---|---:|---:|---:|---|
| 原本 `b4181ab` | 2 / 5件．`marimo-bot.yml`は0件 | 110件．同workflowには別問題の7件 | 0件 | 同じ秒の更新からnpm公開手順への反例あり |
| `-ge` 対照版 | 2 / 5件．原本と同じ警告 | 110件．原本と同じ警告 | 0件 | 上記の反例なし |
| 削除版 `296e39a` | 2 / 5件．他workflowの同じ警告 | 103件 | 0件 | 対象経路なし |

CodeQL CLI 2.27.1，公式 `codeql/actions-queries@0.6.36` の `actions-code-scanning.qls` と `actions-security-and-quality.qls` を，省略なしの原本ファイル群へ適用しました．原本と対照版のdefault警告2件は別workflowの権限宣言不足，広いsuiteの追加3件も別workflowの式展開です．zizmor 1.30.1は`--offline --no-config --no-ignores`でregular，auditor，pedanticを実行しました．原本と対照版の `marimo-bot.yml` に出た警告は，credential保存，権限過大，式展開，local Action指定に関するものです．承認時刻の同秒問題を指摘する警告はありません．zizmorの総警告数が0だったという主張ではありません．actionlint 1.7.12はshellcheckとpyflakesを無効にした構文検査です．

公式Actionsライブラリと同じ `codeql/actions-all@0.6.2` を使った診断クエリでは，原本・対照版のcheckout ref `${{ steps.pr.outputs.head_sha }}` がともに `MutableRefCheckoutStep` / `SHACheckoutStep` と分類され，先行する時刻比較が `untrusted-checkout-toctou` を保護すると評価されました．[公式TOCTOUクエリ](https://github.com/github/codeql/blob/main/actions/ql/src/Security/CWE-367/UntrustedCheckoutTOCTOUCritical.ql) は，この保護があると警告しません．診断は公式クエリの結果を改変せず，その分類を観測したものです．このライブラリ版の時刻検査認識は `-gt` と `-ge` の意味の差を見ていません．

## 自動解析と形式モデル

`tools/approval_race_analysis.py` は手動の信頼注釈を入力せず，YAMLから承認者条件，PR情報の取得と時刻検査，step outputからcheckout refへの接続，local Action，成果物名，`needs`，reusable workflow内のdownload，公開コマンドとjob権限を結びます．対応するのは報告事例で使われたBash記法と1段のlocal reusable workflowであり，任意のshellやActionの意味解析ではありません．一致しない経路から安全結論を出しません．各根拠のファイルと行を保存しています．

生成したNuSMVモデルは「PR更新」「秒の進行」「承認後のPR取得」「時刻検査」「build」「upload」「download」「公開手順での利用」の順序を探索します．承認時点の旧commitから，PR作者が同じ秒に更新した後，GitHub APIが新しいSHAと秒単位の `pushed_at` を返す実行を仮定しています．原本の `AG !bad` は偽，対照版は真です．独立したPython BFSでも原本49状態で反例，対照版41状態で反例なしとなり，NuSMVと一致しました．BFSでも検出できるため，この結果だけで「モデル検査器が不可欠」または大規模性の優位は示しません．時間順序と守りたい性質を明示できたことが，固定条件の単純な照合との差です．

ネットワーク無効のDockerでは，原本の `Get PR Info` のrun本文を，ローカルのGitHub API・`jq`スタブに接続して実行しました．実際のcheckout・build・成果物転送・publishは，無害なmarkerと記録専用の代替処理です．原本は旧commitを受け入れ，同じ秒に更新されたcommitも受け入れ，次の秒の更新は拒否しました．対照版は旧commitだけを受け入れます．原本の同秒更新では `UNAPPROVED_HARMLESS_MARKER` が後続手順のスタブに渡りました．これは時刻条件と仮定した成果物経路の局所再現であり，GitHub上の競合や実際の公開成功を確認したものではありません．

## 再現と残る課題

`tools/evaluate_approval_race.py` がmanifestのchecksumを確認し，原本から対照版を生成します．`tools/verify_approval_models.py` でNuSMVとBFSの判定一致を確認します．`tools/approval_race_baselines.py` は，原本・対照版・削除版をCodeQL，zizmor，actionlintに同じ設定で渡し，全警告をSARIF等として保存します．使用版と依存関係は `codeql/approval-race-diagnostics/codeql-pack.lock.yml` および評価記録に固定しています．回帰テストは時刻の同秒/翌秒，誤ったartifact名，`needs`の不一致，OIDC権限の欠如，reusable workflowの結合を含みます．

結果は `results/marimo-approval-race/` に保存しました．モデル・反例・全警告・診断分類を残し，読み手が「対象の穴について警告がない」という判定を再確認できるようにしています．実際に侵害された[Ultralyticsのcache経路](ultralytics-incident-cache-chain.md)にも限定ルールを追加しましたが，実cache objectと権限の到達は未確認で，既存ツールも関連する危険点を警告します．この事例を提案側だけの検出としては数えません．
