# GHA-A5のNuSMV検証経路

安全性：違反する反例あり

| 順序 | 状態 | workflow | job | step | 意味 |
| --- | --- | --- | --- | ---: | --- |
| 1 | `start` | `.github/workflows/artifact-a5-attest-producer.yml` | `rebuild-dist` | 2 | 未信頼PRがdistと対象branchを成果物へ保存する． |
| 2 | `object_written` | `.github/workflows/artifact-a5-attest-producer.yml` | `rebuild-dist` | 2 | PRが制御する成果物の保存に成功する． |
| 3 | `object_restored` | `.github/workflows/artifact-a5-attest-consumer.yml` | `consume-untrusted-dist` | 0 | workflow_runが起動元runの成果物を取得する． |
| 4 | `object_used` | `.github/workflows/artifact-a5-attest-consumer.yml` | `consume-untrusted-dist` | 2 | 成果物内のdistと対象branchをrepository更新相当の処理へ渡す． |
| 5 | `authority_reached` | `.github/workflows/artifact-a5-attest-consumer.yml` | `consume-untrusted-dist` | 2 | PR由来のdistと対象branchがrepository更新相当の処理へ到達する． |
