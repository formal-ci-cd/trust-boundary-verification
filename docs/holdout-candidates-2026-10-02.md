# 未使用事例による適用範囲の確認（2026-10-02）

既存事例に合わせて検出器を実装した後で同じ事例を再検出するだけでは，別の事例へ一般化できることは示せない．以下の公開事例を，**検出器を変更する前に**評価候補として記録する．この文書は事例選定であり，検出結果ではない．

| 事例 | 公開された事実 | 現行検出器との関係 | 次の判定 |
|---|---|---|---|
| [GHSL-2026-167: mongodb/js-bson](https://securitylab.github.com/advisories/GHSL-2026-167_mongodb_js-bson_repository/) | maintainerの起動権限確認後，mutableな `refs/pull/<number>/head` をcheckoutし，PR由来のスクリプトをwrite権限のあるjobで実行する．脆弱版の固定commitは `9272c9aeddd1dfb606ecd9e9ad770201afa28225`．修正は #929． | 現行の承認競合検出器は，同秒 `pushed_at` 比較を用いる限定パターンを対象とする．この事例にはその比較がなく，同じ検出器の陽性として数えられない． | 固定原本・修正版を取得し，変更前の検出器をそのまま実行．未検出ならfalse negativeとして記録し，別途「承認したcommitとcheckoutしたcommitの同一性」を扱う一般化を検討する． |
| [GHSL-2025-038: github/branch-deploy](https://securitylab.github.com/advisories/GHSL-2025-038_github_branch-deploy_action/) | deploy承認後にPR headを再取得し，攻撃者が指定可能なcommit author dateで更新を判定する．公開PoCはあるが，第三者による実侵害を示す資料ではない．branch protection等で成否が変わる． | 同秒比較とも単純なmutable ref checkoutとも異なる．Action内部の時刻の出所を追う必要がある． | 特権を持つ利用workflowとAction実装の版を固定してから，現行検出器の対応可否と既存ツールの警告を評価する． |

これらを「実際に侵害されたプロジェクト」と呼ばない．GHSL-2026-167は公開脆弱性報告であり，資料に実悪用の確認はない．また，公開報告そのものを検出入力へ流用すると正解が漏れるため，解析入力は固定したGitHub原本のみとし，報告は結果照合に使う．未対応を安全判定へ置き換えない．

## 変更前の検出器によるholdout実行

`mongodb/js-bson` の上記commitから `.github/workflows/release_notes.yml` をGitHub Contents APIで取得した．原本のSHA-256は `d611a4add86299aeedf8d470ddba7b5236a56ec8a57f13e24e60959193d32872`．検出器を変更せず，ネットワークを切った既存の解析コンテナで `python3 tools/approval_race_analysis.py /input --output /output` を実行したところ，出力は `status: no-supported-path`，`candidates: []` だった．したがってこの公開脆弱性は現行の同秒時刻比較ルールでは**検出できない**．これは「安全」の判定ではなく，検出器の適用範囲外という実測である．原本には承認後のmutable PR ref checkoutと後続の `node .github/scripts/*.mjs` 実行が存在するが，時刻比較guard・artifact経由のpublishという現行ルールの必須構造はない．

同じworkflowをCodeQL CLI 2.27.1と`codeql/actions-queries@0.6.36`で解析した．1/1 Actionsファイルを抽出し，通常suiteは `actions/untrusted-checkout/high` をcheckout行に1件，広いsuiteは同じ警告と外部Actionの未固定タグを計2件出した．従ってこの事例は**CodeQLが攻撃入口を検出している**．新しいルールを追加しても，「CodeQLが検出できないのに提案側だけが検出する」事例には数えない．今回の未検出結果は拡張前のholdout結果として保持する．

再検証用の[固定workflow](../experiments/public-cases/mongodb-js-bson-holdout/.github/workflows/release_notes.yml)と，[解析器のJSONおよびCodeQLの全SARIF](../results/mongodb-js-bson-holdout/)を保存した．GitHub原本以外のAction実装やrepositoryスクリプトはCodeQLの今回の入力に含まれない．
