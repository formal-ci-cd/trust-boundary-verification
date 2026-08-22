# GHA-A1のNuSMV検証経路

安全性：違反する反例あり

| 順序 | 状態 | workflow | job | step | 意味 |
| --- | --- | --- | --- | ---: | --- |
| 1 | `start` | `.github/workflows/artifact-a1-pr-producer.yml` | `produce-artifact` | 2 | 未信頼なworkflowが共有objectへの保存を試みる． |
| 2 | `object_written` | `.github/workflows/artifact-a1-pr-producer.yml` | `produce-artifact` | 2 | 共有objectの保存が成功する． |
| 3 | `object_restored` | `.github/workflows/artifact-a1-unsafe-consumer.yml` | `consume-without-verification` | 0 | 同じ共有objectを後続workflowが取得する． |
| 4 | `object_used` | `.github/workflows/artifact-a1-unsafe-consumer.yml` | `consume-without-verification` | 1 | 取得した内容を後続処理が利用する． |
| 5 | `authority_reached` | `.github/workflows/artifact-a1-unsafe-consumer.yml` | `consume-without-verification` | 1 | 成果物のpublish=trueを模擬公開権限到達として記録する． |
