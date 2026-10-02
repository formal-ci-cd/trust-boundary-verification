# 実侵害候補の除外記録

「実際に使われた経路を既存ツールが指摘せず，提案モデルが指摘した」という主張には，事件の成立，原本の固定，同じ入力での既存ツール検査，提案側の経路判定がすべて必要です．候補の攻撃入口をCodeQLやzizmorが既に指摘する場合は，警告の有無だけを根拠に「既存ツールが見逃した」と数えません．

## Elementary Data CLI v0.23.3：候補から除外

[Elementaryの事後報告](https://www.elementary-data.com/post/security-incident-report-malicious-release-of-elementary-oss-python-cli-v0-23-3)は，2026年4月24日に攻撃者のPRコメントがGitHub Actionsのシェルへ挿入され，無断のPyPI公開につながったと記録しています．[脆弱workflowを削除したコミット](https://github.com/elementary-data/elementary/commit/edd283c7f7216a9d940132b772c4c7ea03a4d445)の親 `e5af7e7a569e0af691672831524b372e304faf12` から，`update_pylon_issue.yml` の原本を取得しました．同ファイルの最後の変更コミット `c7f611ff1d647c10ce440e00d47543c2da4a5ac3` とSHA-256が一致します．Apache-2.0のLICENSEとchecksumを[manifest](../experiments/public-cases/elementary-screening/manifest.json)に保存しました．攻撃payloadや第三者のworkflowは実行していません．

CodeQL CLI 2.27.1 / `codeql/actions-queries@0.6.36` はこの1ファイルを抽出し，通常suiteと広いsuiteの**両方で17行目の `github.event.comment.body` を `actions/code-injection/critical` として警告**しました．zizmor 1.30.1 regularも同じ行を `template-injection` として警告しました．完全な警告は[結果索引](../results/elementary-screening/evidence-index.json)から無損失圧縮ファイルを確認できます．これは全repositoryのスキャン結果ではなく，**実際に悪用された1 workflowについての候補除外試験**です．

再現する場合は固定原本に対してCodeQL Actions databaseを作成し，`actions-code-scanning.qls` と `actions-security-and-quality.qls` を実行します．zizmorには `--offline --no-config --no-ignores --persona regular` を指定します．出力の総警告数だけでなく，17行目の対象警告を確認します．

## AsyncAPI 2026年7月の無断公開：攻撃対象workflowを候補から除外

[Microsoftの調査](https://www.microsoft.com/en-us/security/blog/2026/07/15/unpacking-asyncapi-npm-supply-chain-compromise-import-time-payload-delivery/)によれば，攻撃者のPR #2155に対応する `Docs Preview (Netlify)` workflowが未信頼PRのcommitを処理し，その後bot名義のpushと正規の公開workflowから不正パッケージが公開されました．ただし公開ログだけでは，その実行が成功して認証情報を盗んだことまでは立証されていません．

攻撃直前の公開commit `ff010ef38e1be1ffc4112c8903f43637ecbf4041` の `manual-netlify-preview.yml` を固定しました．最後にこのファイルを変更したcommit `2c57fee8bbbb026c9d2797aaaf5c485c4f03f31e` の原本ともSHA-256が一致します．[原本manifest](../experiments/public-cases/asyncapi-screening/manifest.json)にApache-2.0ライセンスを保存しました．攻撃payloadは取得・実行していません．

事件前に公開済みだったCodeQL CLI **2.25.4** / `codeql/actions-queries@0.6.27` の通常suiteで，この**攻撃対象1 workflowだけ**をネットワーク無効のDockerで解析しました．1/1ファイルを抽出し，44行と47行を `actions/untrusted-checkout/critical` として警告し，14行には権限の警告を出しました．[完全なSARIFと条件](../results/asyncapi-screening/evidence-index.json)を保存しました．これは全repositoryや提案モデルとの比較ではありません．少なくとも「攻撃対象の入口をCodeQLが警告しない」事例ではないため，排他的検出の候補から除外します．

## SpotBugsからreviewdogへの侵害：CodeQLには警告なし，提案側は未対応

[Unit 42の調査](https://unit42.paloaltonetworks.com/github-actions-supply-chain-attack/)と[攻撃者のPR #1116](https://github.com/spotbugs/sonar-findbugs/pull/1116)によれば，2024年12月6日に攻撃者は `spotbugs/sonar-findbugs` の `mvnw` を変更しました．PR直前のbase commit `e240bc1aca68337b2b40e100fb24552d27eeb8cc` にある [`sonarqube.yml`](https://github.com/spotbugs/sonar-findbugs/blob/e240bc1aca68337b2b40e100fb24552d27eeb8cc/.github/workflows/sonarqube.yml) は，`pull_request_target` でPR側のmerge refを `haya14busa/action-cond@v1` の出力を介してcheckoutし，続く `./mvnw` にPAT等のsecretを渡します．Unit 42は，このPRで漏れたPATが後の `spotbugs/spotbugs` への侵入に使われたことを保守者に確認しています．公開原本はGit blob `176cd5cd4cbd7c7a05f1e9730c1e40542ebe64c9`，SHA-256 `e7d3acaa4633e114aa901fba6b05eb236bbe67aa89a84b148db0c0db96ff5f74` で固定しました．攻撃payloadは実行していません．

この**対象1ファイル**をネットワーク無効のDockerで解析すると，CodeQL CLI 2.25.4 / `codeql/actions-queries@0.6.27` とCLI 2.27.1 / pack 0.6.36の通常suiteはいずれも1/1ファイルを抽出しましたが，警告は0件でした．ただし，これは**事件後の遡及比較**です．[GitHubの公開記録](https://github.blog/changelog/2024-12-17-find-and-fix-actions-workflows-vulnerabilities-with-codeql-public-preview/)ではActions解析のpublic preview開始が2024年12月17日で，攻撃日の後です．「事件当時のCodeQLが見逃した」とは言いません．

同じ原本に対し，zizmor 1.30.1 regularは `dangerous-triggers` など8件，sisakulint 0.3.7は45行目の `untrusted-checkout` を含む15件を出しました．いずれも2026年の遡及比較で，総警告数を検出精度とは扱いません．[完全なSARIFと条件](../results/spotbugs-screening/evidence-index.json)を保存しました．現行の提案実装はYAML構造を抽出できますが，外部 `action-cond` の出力からcheckout refへの伝播と `./mvnw` の実行を自動で結ぶ規則を持ちません．したがって，**CodeQL通常suiteの見逃しを確認した実侵害**ではあるものの，現時点で「提案手法が検出した」事例ではなく，既存ツール一般に対する排他的検出例でもありません．

ElementaryとAsyncAPIは実侵害であっても，既存ツールの対象警告があるため，排他的な検出優位性の証拠には使えません．[TanStack](tanstack-incident-cache-boundary.md)も実侵害ですが，事件当時に公開済みだったCodeQLは実際のPR攻撃入口を警告し，対策版では警告しません．現在のCodeQL版だけによる1行対照例の差は，当時の検出優位性の証拠に使えません．[Cline](cline-incident-agent-cache-boundary.md)ではCodeQL等が複数workflowの候補経路を指摘しないものの，PromptPwndが入口を検出し，実際の攻撃者がその入口を使ったかは未確定です．[marimo](marimo-approval-race-case-study.md)は対象の時刻競合について既存ツールとの差がありますが，実悪用は未確認です．現状，「実際に使われた経路を既存ツール一般が見逃し，提案手法だけが検出した」という条件を満たす事例は確認できていません．
