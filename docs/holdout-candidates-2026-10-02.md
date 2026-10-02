# 未使用事例による適用範囲の確認（2026-10-02）

既存事例に合わせて検出器を実装した後で同じ事例を再検出するだけでは，別の事例へ一般化できることは示せない．以下の公開事例を，**検出器を変更する前に**評価候補として記録する．この文書は事例選定であり，検出結果ではない．

| 事例 | 公開された事実 | 現行検出器との関係 | 次の判定 |
|---|---|---|---|
| [GHSL-2026-167: mongodb/js-bson](https://securitylab.github.com/advisories/GHSL-2026-167_mongodb_js-bson_repository/) | maintainerの起動権限確認後，mutableな `refs/pull/<number>/head` をcheckoutし，PR由来のスクリプトをwrite権限のあるjobで実行する．脆弱版の固定commitは `9272c9aeddd1dfb606ecd9e9ad770201afa28225`．修正は #929． | 現行の承認競合検出器は，同秒 `pushed_at` 比較を用いる限定パターンを対象とする．この事例にはその比較がなく，同じ検出器の陽性として数えられない． | 固定原本・修正版を取得し，変更前の検出器をそのまま実行．未検出ならfalse negativeとして記録し，別途「承認したcommitとcheckoutしたcommitの同一性」を扱う一般化を検討する． |
| [GHSL-2025-038: github/branch-deploy](https://securitylab.github.com/advisories/GHSL-2025-038_github_branch-deploy_action/) | deploy承認後にPR headを再取得し，攻撃者が指定可能なcommit author dateで更新を判定する．公開PoCはあるが，第三者による実侵害を示す資料ではない．branch protection等で成否が変わる． | 同秒比較とも単純なmutable ref checkoutとも異なる．Action内部の時刻の出所を追う必要がある． | 特権を持つ利用workflowとAction実装の版を固定してから，現行検出器の対応可否と既存ツールの警告を評価する． |

これらを「実際に侵害されたプロジェクト」と呼ばない．GHSL-2026-167は公開脆弱性報告であり，資料に実悪用の確認はない．また，公開報告そのものを検出入力へ流用すると正解が漏れるため，解析入力は固定したGitHub原本のみとし，報告は結果照合に使う．未対応を安全判定へ置き換えない．
