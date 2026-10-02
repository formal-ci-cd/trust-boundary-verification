# SpotBugs実侵害に対する条件付きcheckout経路の検証

## 主張できる結果

2024年12月6日の `spotbugs/sonar-findbugs` への[攻撃PR #1116](https://github.com/spotbugs/sonar-findbugs/pull/1116)は `mvnw` を変更した．PR直前の[上流workflow](https://github.com/spotbugs/sonar-findbugs/blob/e240bc1aca68337b2b40e100fb24552d27eeb8cc/.github/workflows/sonarqube.yml)は `pull_request_target` でPRのmerge refを外部Actionの条件付き出力からcheckoutし，secretを設定して `./mvnw` を実行する．[Unit 42の事件調査](https://unit42.paloaltonetworks.com/github-actions-supply-chain-attack/)は，このPRによるPATの漏洩と，そのPATを用いた後続侵入を保守者に確認している．本研究が独立に確認したのは，公開原本と攻撃PRの差分にある以下の対応である．

| 根拠 | 確認した事実 | 限界 |
|---|---|---|
| [固定workflowとmanifest](../experiments/public-cases/spotbugs-chain/manifest.json) | `pull_request_target` →条件付きPR merge ref→checkout→secret付き`./mvnw` | `haya14busa/action-cond@v1` の事件時SHAは未確認 |
| [固定外部Actionの隔離実行](../results/spotbugs-screening/external-action-runtime.json) | 配布JSをdigest固定のNode 20コンテナでダミー入力により実行すると，条件真でPR merge ref，偽でbase refを出力 | 固定したJSの動作確認であり，事件当日の可変タグの指先は証明しない |
| [攻撃PRの公開差分](../experiments/public-cases/spotbugs-chain/attack-pr-files.json)と[自動照合](../results/spotbugs-screening/attack-pr-alignment.json) | 変更された唯一のファイルが `mvnw` で，冒頭付近に外部スクリプトを取得して `bash` に渡す無条件の行が追加された | 外部スクリプト本文と当時のCIログは取得できない．差分内の命令は実行していない |
| [形式モデルの原本結果](../results/spotbugs-screening/model-original/analysis.json) | 固定した外部Action実体と実行成功を仮定すると，NuSMVと独立BFSはともにPR側の実行ファイルへsecretが渡る反例を出す | 実際の実行・漏洩をモデル単体が証明した結果ではない |

GitHubの[イベント仕様](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#pull_request_target)では `pull_request_target` の `GITHUB_SHA` と `GITHUB_REF` はbase側を指す．[安全な利用の説明](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target)は，PR head/merge refを明示してcheckoutした後にbuild scriptを実行することを危険な構成としている．この仕様に基づき，`if_true` のPR merge refだけを `${{ github.sha }}` に変えた研究用の[1箇所対照例](../experiments/public-cases/spotbugs-chain/safe-ref-control/.github/workflows/sonarqube.yml)を作った．これは上流が採用した修正ではない．固定した外部Action実体が実行されるという条件下では，対照例の[モデル結果](../results/spotbugs-screening/model-safe-ref-control/analysis.json)に対象の反例はない．

| 固定版・同一入力範囲の比較 | 原本 | 研究用1箇所対照例 | 対象経路を区別 |
|---|---:|---:|---|
| CodeQL 2.27.1 / Actions pack 0.6.36 通常suite | 0件 | 0件 | しない |
| 同，広いsuite | 未固定タグ1件 | 同じ未固定タグ1件 | しない |
| zizmor 1.30.1 regular | 8件 | 同じ8件 | しない |
| sisakulint 0.3.7 | 15件 | 同じ15件 | しない |
| 本研究の限定解析と形式モデル | 条件付き経路・反例あり | 対象経路・反例なし | する |

[CodeQLの完全なSARIFと比較](../results/spotbugs-screening/codeql-safe-ref-control-comparison.json)，[zizmor・sisakulintの完全なSARIFと比較](../results/spotbugs-screening/additional-control-scanners-comparison.json)を保存した．sisakulintは**両方**に未信頼checkoutと後続local scriptへの警告を出す．この比較が示すのは，検査した既存ツールのこの版・設定が区別しなかった**PR参照選択による対象経路の差**を，提案側が区別したことである．危険な設定の入口を提案側だけが発見した，とは主張しない．

## 解釈の境界

このCodeQL比較は**事後の遡及実験**である．CodeQL Actions解析の[公開プレビューは2024年12月17日](https://github.blog/changelog/2024-12-17-find-and-fix-actions-workflows-vulnerabilities-with-codeql-public-preview/)に始まり，攻撃日の後である．従って「当時のCodeQLが見逃した」という歴史的主張はしない．現在の公開APIで攻撃PRの[check-run](../results/spotbugs-screening/attack-pr-head-check-runs.json)と攻撃日の[対象workflow run](../results/spotbugs-screening/attack-day-public-runs.json)は確認できず，0件という現在の応答を当時未実行だった証拠にはしない．漏洩の歴史的事実はUnit 42と保守者の調査に依拠する．

2025年4月の[上流変更 #1237](https://github.com/spotbugs/sonar-findbugs/pull/1237)では `pull_request_target` が除かれたが，変更直前にはPR作成者属性で実行環境を選ぶ条件がある．環境の承認設定は公開YAMLから分からないため，その[実変更直前の解析](../results/spotbugs-upstream-change/before-static.json)は未対応とし，「危険→安全」の証明には使わない．変更直後の[対象イベントなし](../results/spotbugs-upstream-change/after-static.json)とは区別する．

有限状態モデルは各160状態で，独立BFSがNuSMVと同じ判定を出した．この実験からモデル検査器固有の必須性や大規模解析の優位性は導けない．再実行方法は[解析手順](automatic-analysis-guide.md)に記録した．
