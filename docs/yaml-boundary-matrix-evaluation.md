# Supported SubsetのYAML境界マトリクス

[中心Threat Model](threat-model.md)に対し，[実行可能な評価表](../tests/test_supported_subset_yaml_matrix.py)で期待結果を先に定義し，YAML読取り，workflow/run結合，事実生成，NuSMV，独立BFS／事実列挙，元行の存在確認を一連で実施する．workflowは入力として読むだけで実行しない．

| 境界軸 | YAML対照 | 期待・確認した対象結果 |
|---|---|---|
| Source | fork PR入力／`push`のみ／checkout内の別literal file | PR入力は条件付き反例，`push`のみは対象反例なし，別fileでもPR由来を認識 |
| write intentと実行条件 | uploadあり／削除／`if: true`／`if: false`／未対応式 | 有効時は条件付き反例，削除は候補0件としてunknown，falseは対象反例なし，未対応式はunknown |
| write許可・成功 | upload呼出し可能／呼出し不可能／実効許可不明 | `if: false`は呼出しを遮断．token許可・保存成功はYAMLから真にせずunknown |
| SameObject | 同名／異なるliteral name／動的name／同名upload複数 | 同名は候補で実体同一性unknown，異名はfalse，動的nameはunsupported，同名複数は各経路を条件付き反例として保持 |
| run順序 | `workflow_run`でproducer完了後／逆向き・無関係trigger | 前者は対象候補，後者はSupported Subset外としてunknown．Cacheの逆順schedulerは独立探索で違反なし |
| read | downloadあり／削除／`if: false` | ありは対象候補，削除は候補0件としてunknown，falseは対象反例なし |
| use | artifact読取りあり／summaryのみ／`if: false` | 読取りありは条件付き反例，非利用・無効化で反例なし |
| Integrity Verification | なし／hash記録のみ／信頼済みliteral digest guard／認識不能な照合 | なしは条件付き反例，hash記録だけでは検証済みとしない．guardは対象反例なし，認識不能はunknown |
| Privileged Authority | 模擬sinkあり／権限なし | sinkへの依存があると条件付き反例，権限なしでは反例なし |
| sink依存 | artifact由来の判断／独立した固定marker／A5 metadata由来の模擬更新を無効化 | 依存ありは条件付き反例，独立markerと無効化は反例なし |
| 構文対応 | 固定shell形／未対応Action／早期終了・追加`if`／動的name | 対応形は解析，未対応形はunknown/unsupportedでSafeにしない |

Source，upload呼出し，download呼出し，use呼出しの真偽**全16通り**では，全てが有効な1構成だけに対象反例が出た．信頼済みdigest guardを加えたSource／upload／downloadの**全8通り**では対象反例は出なかった．A5のmetadata経路は原本で条件付き反例，模擬sinkの一箇所を無効化すると反例が消えた．TanStackは事件前，fork除外，Cache呼出し，repository write権限・sink，対策版のscope，未対応Composite Actionについて，同じ経路propertyを比較した．

この評価が示すのは，**テストで固定したSupported Subsetの構文と境界の組合せ**での一致である．任意のBash，JavaScript／Docker Action，動的artifact name，実効Cache key・scope・version，artifact ID，実際の保存・復元成功，外部認可成功の完全性や検出率を証明しない．`safe-within-model`は対象経路だけの結論であり，候補0件又は未対応構文をrepository全体の安全とはしない．NuSMVは反例生成backendであり，BFS一致は独立照合である．

再実行は[解析手順](automatic-analysis-guide.md)に従う．このテストはNuSMV 2.7.0がある研究用macOS環境で実行した．通常CIのDocker imageにはNuSMVがなく，その環境では行列テストがskipされるため，CIのgreenだけをこのNuSMV結果の証拠とはしない．
