# 論文用YAML境界マトリクス集計

NuSMV 2.7.0を実行し，独立探索と一致を確認した68構成．期待値はテストで先に定義した．

| 群 | 件数 | 反例あり | 対象反例なし | unknown/unsupported | 期待値との一致 |
|---|---:|---:|---:|---:|---:|
| artifact-single-axis | 32 | 13 | 9 | 10 | 32/32 |
| artifact-boolean-4 | 16 | 1 | 15 | 0 | 16/16 |
| artifact-digest-guard-3 | 8 | 0 | 8 | 0 | 8/8 |
| a5-sink | 2 | 1 | 1 | 0 | 2/2 |
| tanstack-cache-control | 10 | 3 | 5 | 2 | 10/10 |

候補0件と未対応構文はunknown/unsupportedとして集計する．条件付き反例は実行時成功の証明ではない．
詳細な各構成・事実値は[evidence-index.json](evidence-index.json)を参照する．
