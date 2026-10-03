# 中心仮説と証拠の対応

論文の節，六つの表，Research Question，実装への追跡先は[研究整理](paper-ready-synthesis.md)に集約した．表の数値は[再解析で生成した索引](../results/paper-evidence/evidence-index.json)と照合する．

## 論文の中心仮説

[Threat Model](threat-model.md)で定義するGitHub ActionsのCache／artifact経路について，CI/CD構成と明示した実行時仮定から有限状態モデルを生成し，複数workflow又はrunを跨ぐ安全性propertyを検査すれば，**未信頼producerから同一共有状態を経てprivileged consumerの権限操作地点まで至る条件付き到達可能性**を一つの反例として提示できる．既存スキャナが危険な入口や出口を警告することと両立する主張である．

本研究の差分は，対象経路の各部分を一つのpropertyと反例で接続し，有効な境界条件を変更したときに対象反例が消えることを示す点にある．NuSMV固有の不可欠性は主張しない．独立BFSとの一致はモデル生成・探索の照合とする．`unknown`を真に割り当てて得た反例は可能性であり，実侵害の実行証拠ではない．

## 中心評価：A1～A5とTanStack

| 対象 | 中心経路／遮断点 | propertyの結果と証拠の強さ |
|---|---|---|
| A1 | PR producer→artifact→別runのconsumer→未検証利用→模擬publish | 反例あり．同一artifact IDを実行記録で確認 |
| A2 | A1のconsumerが信頼済みdigest不一致で停止 | 対象反例なし．同一artifact IDの取得と停止を確認 |
| A3 | A1のconsumerが取得内容を利用しない | 対象反例なし．実行時にも非利用を確認 |
| A4 | A1のconsumerに対象権限がない | 対象反例なし．実行時にも権限なしを確認 |
| A5 | 公開脆弱性を基にしたPR producer→artifact→別runのconsumer→模擬repository更新 | 固定YAMLからの限定自動解析で条件付き反例．同一artifact ID／digestと模擬到達を観測．旧モデル入力には人手対応が残る |
| TanStack | 実侵害のPR側Cache保存候補→外部Setup Actionの復元→別workflow/runのOIDC job | 事件前に条件付き反例，実対策版で対象経路なし．順序を変えると到達runが変わる．実効Cache entry／byteの全同一性は未観測 |

[A1～A5のYAML自動再評価](../results/core-artifact-subset/analysis.json)では，現実のpublish権限でなくdummy markerへの到達を検査している．A1～A4の実行観測は[成果物実験](../results/github-actions-artifact-runtime-2026-08-22.md)と[A3～A5の検証](../results/artifact-case-study-2026-09-23.md)，TanStackの固定原本・反例・時刻の対応は[事例検証](tanstack-incident-cache-boundary.md)を参照する．A1／A5の[一境界ずつのモデル対照](../results/core-boundary-controls.json)はNuSMVと独立した列挙で18構成を照合した．さらに[YAML境界マトリクス](yaml-boundary-matrix-evaluation.md)でSource・upload・download・useの16通り，digest guardを持つ8通り，単独軸対照，A5とTanStackのsafe controlを抽出から検査まで通した．これらは**全てのYAML表現や実行時条件の網羅ではない**．[抽出範囲と評価契約](scope-and-extraction.md)に残作業を明示した．

## property-levelの既存ツール比較

比較単位は対象入力に対する**一つのfinding**である．入口は未信頼event／checkout／保存，出口は権限・公開・deploy地点を指す．両者が別々に出ても「全経路を接続」としない．全経路とするには，同じfindingがproducer，Cache／artifact，別workflow/runのconsumer，privileged authorityへの依存を提示する必要がある．

| 対象 | CodeQL | zizmor | sisakulint | Poutine | 提案側 |
|---|---|---|---|---|---|
| A1～A4 | 七つの中心workflowに対するCLI 2.27.1／公式pack 0.6.36のdefault・security-and-quality両suiteで対象findingなし | consumer側の`dangerous-triggers`のみ | 対象propertyのfindingなし（Node実行環境の警告のみ） | 対象workflowのfindingなし | A1の全経路反例とA2～A4の遮断点を同じpropertyで区別 |
| A5 | 同じ二suiteで対象findingなし | consumer側の`dangerous-triggers`のみ（producer側の`template-injection`は別property） | 対象propertyのfindingなし（Node実行環境の警告のみ） | 対象workflowのfindingなし | 条件付きの全経路反例．一部のruntime事実はunknown |
| TanStack | 当時版2.25.4は**PR側入口を警告**．保存SARIFに公開側まで結ぶ同一結果なし | 入口警告あり．同一結果で公開側まで接続せず | 入口の未信頼checkoutを警告．同一結果で公開側まで接続せず | 外部Action等への警告あり．同一結果でCache経路を接続せず | producer→Cache→別runのOIDC側を条件付き反例として接続 |

TanStackについては[一つのfindingの位置とflowを再計算した比較](../results/tanstack-cache-chain/property-level-comparison.json)を用いる．新しい共通解析は，旧事例専用モデルのOIDC設定経路とは別に，PR側Cache候補からrelease workflow内の明示`git push`地点までを[条件付き反例](../results/tanstack-cache-chain/common-path-pre/analysis.json)として構成した．対策版では2026年5月のscope規則により[同じpropertyの対象反例がない](../results/tanstack-cache-chain/common-path-mitigation/analysis.json)．これは実際のnpm公開やrepository更新成功を示さない．A1～A5のCodeQL 2.27.1，zizmor 1.30.1，sisakulint 0.3.7，Poutine 1.1.6の固定出力と入力範囲は[比較記録](../results/core-artifact-tool-baselines/property-level-comparison.json)に保存した．CodeQLの二suiteはこの七入力で結果0件であり，カスタムqueryを含むCodeQL一般の能力については主張しない．版・suite・入力に依存した**この保存済み出力**に関する比較であり，各ツール一般にその能力がないという証明ではない．警告件数は検出率・優劣の指標にしない．

## 保存するが中心評価から外す事例

| 区分 | 事例 | 理由 |
|---|---|---|
| 拡張可能性を示す補助事例 | [Cline](cline-incident-agent-cache-boundary.md) | Issue→AI→Cache→夜間公開資格情報の**候補経路**は共有状態の形を取るが，AIの入力からCache byteへの因果関係と実際の侵入経路が未確定．AI prompt injectionを中心手法へ追加しない |
| scope外の探索結果 | [SpotBugs](spotbugs-incident-case-study.md) | 外部PR refをsecret付き`./mvnw`で実行する経路で，Cache／artifactを介する別workflow/runの受渡しではない．実侵害の検証記録として保存 |
| scope外の探索結果 | [Jupyter](jupyter-composite-approval-race-case-study.md)，[marimo](marimo-approval-race-case-study.md) | 承認時刻の境界という時間的性質で，中心の共有状態経路とは異なる．公開脆弱性の検証記録として保存 |
| scope外の探索結果 | [Trivy，Elementary，AsyncAPI，codfish等](incident-candidate-screening.md) | 既存ツールの入口警告を含む候補選別．中心評価へ加えない |

[Ultralytics](ultralytics-incident-cache-chain.md)はCache経路の参考資料として保存するが，A1～A5とTanStackの中心評価を拡張する材料にはしない．

## 論文に置ける結論文

> GitHub Actionsで未信頼producerがCache又はartifactに保存し，別workflow又はrunのconsumerが同じ共有状態を検証せずに権限付き処理へ利用する経路を有限状態モデルで検査した．A1～A4では，限定したSupported Subset内でYAMLから注釈なしに，未検証利用の条件付き反例と，完全性照合・非利用・権限不在による反例消失を同じpropertyで区別した．A5では固定YAMLから限定したmetadata経路を注釈なしで再生成した反例を，TanStackでは既存ツールが警告したPR側入口を別runのOIDC側へ接続する条件付き反例と順序依存性を示した．この成果は対象モデルと保存した比較出力に限定され，任意のAction／shellの自動解析，実侵害の全経路証明，既存ツール一般の見逃し，又はNuSMV固有の優位性を意味しない．
