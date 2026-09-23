# 成果物経路GHA-A3からGHA-A5の検証

実施日：2026年9月23日

## 1．目的

GHA-A3及びGHA-A4をGitHub上で実行し，形式モデルで安全と判定した遮断条件が実行時にも成立するかを確認する．また，研究用に作った比較構成だけでなく，実在する成果物汚染の脆弱性を簡略化したGHA-A5を作成し，同じ形式検証で危険な経路を検出できるかを確認する．

## 2．GHA-A5の基となる事例

GHA-A5は，GitHub Security Labが`actions/attest`で報告した`GHSL-2026-225`を基にした．この脆弱性では，未信頼な`pull_request`側が成果物へ`dist/`と対象branch情報を保存し，`workflow_run`側がその値を信用してrepositoryのbranchへpushしていた．対象branchが起動元repository及びPull Requestと対応するかを確認していないため，PR作成者が用意した内容を権限側の処理へ渡せる構造であった．

ただし，公開情報から実際の悪用は確認できない．したがって，GHA-A5は「有名な攻撃事件の再現」ではなく，「実在リポジトリで発見・修正された脆弱性の再現」である．実被害を避けるため，本物の`contents: write`と`git push`は使用せず，repository更新相当の地点への到達だけを記録する．

- [GHSL-2026-225](https://securitylab.github.com/advisories/GHSL-2026-225_actions_attest/)
- [GitHub Actionsの安全な利用](https://docs.github.com/en/actions/reference/security/secure-use)

なお，Ultralyticsは実際に悪用された有名な事例であるが，PyPIの分析では攻撃対象はGitHub Actionsのキャッシュであり，今回の成果物汚染とは区別する．

- [PyPIによるUltralytics攻撃の分析](https://blog.pypi.org/posts/2024-12-11-ultralytics-attack-analysis/)

## 3．形式検証結果

検査した性質は，「未信頼な成果物を保持したまま権限へ到達しない」である．NuSMV 2.7.0でA1からA5を検査した結果は次のとおりである．

| 構成 | 主な差分 | 期待結果 | NuSMV |
| --- | --- | --- | --- |
| GHA-A1 | 完全性確認なしで利用し，模擬公開権限へ渡す． | 危険 | 反例あり |
| GHA-A2 | digest不一致時に利用を遮断する． | 安全 | 反例なし |
| GHA-A3 | 取得するが，成果物の内容を後続処理へ渡さない． | 安全 | 反例なし |
| GHA-A4 | 成果物を読むが，公開・更新権限を持つ処理がない． | 安全 | 反例なし |
| GHA-A5 | PR由来の`dist/`と対象branchをrepository更新相当の処理へ渡す． | 危険 | 反例あり |

A5では，`start`，`object_written`，`object_restored`，`object_used`，`authority_reached`の順に進む反例が得られた．これは，未信頼PRの内容が成果物として保存され，後続workflowに取得・利用され，repository更新相当の地点へ到達する経路を表す．

## 4．現時点の区分

| 対象 | workflow作成 | NuSMV | GitHub上の実行 |
| --- | --- | --- | --- |
| GHA-A3 | 完了 | 安全 | run `35823235871`で取得成功，利用なしを確認 |
| GHA-A4 | 完了 | 安全 | run `35823235868`で取得・読取り成功，権限なしを確認 |
| GHA-A5 | 完了 | 危険 | producer run `35824000044`とconsumer run `35824013582`で経路成立を確認 |

A5の形式モデルは，公開された脆弱性の説明と作成したworkflowを人手で対応付けたものである．CodeQLの中間表現から再抽出したものではないため，CodeQLによる構造抽出と人手による意味付けを区別する．

GHA-A3及びGHA-A4は，producer run `35823220725`が保存したartifact ID `10734380218`を取得した．GHA-A3は`artifact_used=false`及び`artifact_value_forwarded=false`，GHA-A4は`artifact_value=publish=true`及び`authority_available=false`を記録した．したがって，A3は利用前，A4は権限到達前で経路が遮断され，NuSMVの判定と一致した．

GHA-A5は，producer run `35824000044`がartifact ID `10734401185`を保存し，consumer run `35824013582`が同じID及びdigestを取得した．取得側では`artifact_target_branch=main`，`integrity_verified=false`，`dummy_repository_update_reached=true`となった．したがって，未信頼PRの成果物が検証されないままrepository更新相当の地点へ到達する経路が実行時にも成立し，NuSMVの反例と一致した．ただし，実際の権限は`contents: read`であり，checkout，commit及びpushは行っていない．
