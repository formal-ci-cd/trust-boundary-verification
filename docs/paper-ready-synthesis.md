# 論文本文に対応する研究・評価索引

本資料は[Threat Model](threat-model.md)，[中心仮説と証拠](thesis-claim-and-evidence.md)，[Supported Subset](scope-and-extraction.md)，[YAML境界マトリクス](yaml-boundary-matrix-evaluation.md)を変更せず，論文の各節から入力・実装・結果へ辿るための索引である．数値は[機械可読な評価索引](../results/paper-evidence/evidence-index.json)と[生成した集計表](../results/paper-evidence/yaml-matrix-table.md)に対応する．

## 背景，問題設定，研究の差分

GitHub Actionsでは低信頼のPull Request側runと特権側runがCache又はartifactを介して間接的に接続される．入口の危険性と出口の権限を別々に警告するだけでは，両者が同じ共有状態の版を通じて接続し得るか，どの境界を切れば対象経路が消えるかを一つの結果として説明しにくい．本研究は限定したYAML・shell・Action構文から有限状態モデルを生成し，`AG !bad`の反例と元YAML位置を提示する．反例に残る実行時事実は仮定として明示する．NuSMVは検査backendで，独立BFS／事実列挙は照合に用いる．

### 表1：Threat Modelの成立条件と遮断例

| 条件 | 違反到達に必要な状態 | 確実な遮断境界の例 |
|---|---|---|
| Source | 外部PR等がproducerの保存byteへ影響 | Sourceが信頼済み，又はfork入力が有効に除外される |
| Producer／Write | 保存呼出しがあり，許可・成功する | 保存呼出し不可，又は保存が確実に失敗 |
| SameObject | 同じ実体版をconsumerが選ぶ | 異なるartifact ID又は参照不能なCache scope |
| Read／Order | 保存後の別workflow/runが取得に成功 | 取得不可，又はrestoreがsaveに先行し対象entryがない |
| Use／Execute | 取得byteが利用又は実行に影響 | 取得しても利用しない |
| Verify | 信頼済み完全性照合で汚染を排除していない | 信頼済み基準との不一致で利用を停止 |
| Authority／Sink | 対象権限を持つ処理地点に汚染が到達 | 権限がない，又はsinkが共有状態に依存しない |

全条件の連言は**定義した有限モデル内**の`bad`到達の必要十分条件である．構成上の候補一致はSameObjectの実証ではない．詳しい定義と安全性propertyは[Threat Model](threat-model.md)にある．

## 手法と実装の対応

| 論文概念 | 実装の入口・主要関数 | 結果内の証拠 |
|---|---|---|
| YAML構造と元行 | [`yaml_to_model.py`](../tools/yaml_to_model.py) `load_workflow`，`load_models` | workflow，job，step，line |
| Source／Producer | [`evaluate_artifact_subset.py`](../tools/evaluate_artifact_subset.py) `source_fact`，[`evaluate_cache_subset.py`](../tools/evaluate_cache_subset.py) `checkout_before` | `source`，`producer`，`facts.producerUntrusted` |
| Cache／artifact操作 | [`extract_shared_operations.py`](../tools/extract_shared_operations.py) `extract`，[`discover_artifact_chains.py`](../tools/discover_artifact_chains.py) `artifact_operations` | `sharedObject`，元Action位置 |
| SameObject候補 | `pair_cache`，`discover_candidates` | key/name互換性，run selector，`sameObject` |
| Consumer／Order | `evaluate_cache_subset.evaluate`，`evaluate_artifact_subset.evaluate` | `consumer`，`order`，保存・復元位置 |
| use／execute | `supported_dummy_sink`，`metadata_dummy_sink`，`composite_use_after` | `traceMap.object_used`又は`use`，unknown仮定 |
| Integrity Verification | `digest_guard`，`guarded_uses_only` | `facts.integrityCheckPresent`，照合位置 |
| Privileged Authority／sink | `supported_dummy_sink`，`metadata_dummy_sink`，`permission`と`git push`候補 | `traceMap.authority_reached`又は`privilegedAuthority` |
| `AG !bad`と反例 | [`chain_to_nusmv.py`](../tools/chain_to_nusmv.py) `render_model`，[`cache_two_run_model.py`](../tools/cache_two_run_model.py) `render` | 生成SMV，NuSMV出力，反例段階 |
| 独立照合 | [`static_chain_baseline.py`](../tools/static_chain_baseline.py) `check`，`cache_two_run_model.explore` | 一致しなければ評価器が失敗 |

### 表5：固定した自動化範囲

| 関係 | 自動で扱う構文・情報 | unknown | unsupported／実行時観測 |
|---|---|---|---|
| Source | `pull_request`の暗黙ref checkout，限定した`pull_request_target`のPR refとfork条件 | 他の条件式・byte由来 | 任意のcheckout refと任意のjob条件 |
| Producer／artifact | 既知`upload-artifact`，literal相対pathと限定`cp`，`workflow_run`＋元run IDのdownload | 保存許可・成功，ID・digest | 動的artifact name，任意producer shell |
| Cache | 既知`actions/cache`系と照合済みComposite Actionのread/write | 実効key・scope・version・entry，保存／復元成功 | 未照合Action内部，任意JavaScript／Docker Action |
| use／execute | 固定`tr`読取り，A5 metadata→step output，Composite内の固定`pnpm install`候補 | 実際に使われたbyteと実行成否 | 任意shell・Actionのデータフロー |
| verify | A2のliteral SHA-256とoutput guardを含む固定形 | 実際の照合結果 | 任意hash／signature処理，未認識guard |
| sink | 模擬publish／更新marker，明示`contents: write`＋`git push`候補 | 実権限・外部認可・操作成功 | 任意publish／deploy／OIDC呼出しの汎用解析 |
| Order | 固定`workflow_run`受渡し，Cacheの2 runモデル | 実際の時刻・一般的並行run | 未対応trigger・scheduler |

表の詳細な構文形，`unknown/unsupported`への分岐，safeの意味は[Supported Subset](scope-and-extraction.md)を優先する．

## 評価

### 表2：A1～A5の一境界対照

| 例 | 原本YAMLと変えた境界 | 生成propertyの結果 | 独立照合・実行時観測 |
|---|---|---|---|
| A1 | [`producer`](../.github/workflows/artifact-a1-pr-producer.yml)→[`consumer`](../.github/workflows/artifact-a1-unsafe-consumer.yml)，未検証利用 | 条件付き反例 | NuSMV／独立列挙一致．実行記録で同一artifact ID |
| A2 | 同じproducer→[`consumer`](../.github/workflows/artifact-a2-safe-consumer.yml)，信頼済みdigest guard | 対象反例なし | 両者一致．同一artifact ID取得後に不一致で停止 |
| A3 | 同じproducer→[`consumer`](../.github/workflows/artifact-a3-download-only-consumer.yml)，取得後に非利用 | 対象反例なし | 両者一致．実行記録でも非利用 |
| A4 | 同じproducer→[`consumer`](../.github/workflows/artifact-a4-no-authority-consumer.yml)，対象権限なし | 対象反例なし | 両者一致．模擬到達なし |
| A5 | [`producer`](../.github/workflows/artifact-a5-attest-producer.yml)→[`consumer`](../.github/workflows/artifact-a5-attest-consumer.yml)，公開脆弱性由来のmetadata経路 | 条件付き反例 | 両者一致．同一artifact ID／digestと模擬到達を観測 |

機械可読結果・元YAML行・仮定は[生成結果](../results/core-artifact-subset/analysis.json)，実行時観測は[観測1](../results/github-actions-artifact-runtime-2026-08-22.md)と[観測2](../results/artifact-case-study-2026-09-23.md)にある．A1～A4は同じ基本構造からconsumer側の境界を一つずつ変えた対照である．A5の`git push`相当は研究用模擬markerであり，実repository writeを行っていない．

### 表3：YAML境界マトリクス

[生成表](../results/paper-evidence/yaml-matrix-table.md)に単独軸32，4要因全16，digest guard付き3要因全8，A5の2，TanStackの10を集計した．合計68構成で，テストに事前指定した期待結果と68/68一致した．これは**固定した入力構文と構成集合に対する一致**であり，任意GitHub ActionsのRecall／Precisionを表す値ではない．同名upload複数候補とCache逆順は別途テストし，68件には含めない．`unsafe-counterexample`はunknownを可能側へ割り当てた結果を含む．

### 表6：TanStackの実事例と順序

| 入力・順序 | PR側Source／共有状態／権限側 | 対象propertyの結果 | 留保 |
|---|---|---|---|
| 事件前構成 | `bundle-size.yml`のPR checkout→Setup Action内Cache候補→別runの`release.yml`→明示`git push`地点 | 条件付き反例 | 実効Cache entryとbyte，sink成功はunknown |
| 対策版 | 同じ構造で歴史的scope規則を入力 | 対象反例なし | policyをunknownにすると条件付き反例に戻る |
| consumerが先，producerが後 | 同じ2 run事実の逆順 | 対象反例なし | 初期Cache清浄・第三者更新なしの固定モデル |

[共通解析の事件前](../results/tanstack-cache-chain/common-path-pre/analysis.json)，[対策版](../results/tanstack-cache-chain/common-path-mitigation/analysis.json)，[順序モデル](../results/tanstack-cache-chain/interleaving/evidence-index.json)を分離する．実際のnpm公開までの因果経路を自動証明した結果ではない．

### 表4：保存済み既存ツールとのproperty-level比較

| 固定入力 | CodeQL | zizmor | sisakulint | Poutine | 提案手法 |
|---|---|---|---|---|---|
| A1～A5の7 workflow | 対象findingなし | 入口のみ | 対象findingなし | 対象findingなし | A1・A5の経路を一つの条件付き反例として接続．A2～A4は同propertyで反例なし |
| TanStack事件前の固定入力 | 入口のみ | 入口のみ | 入口のみ | 対象findingなし | PR側Cache候補から別runの権限地点まで条件付き反例 |

A1～A5の固定版はCodeQL CLI 2.27.1＋公式pack 0.6.36のdefault／security-and-quality，zizmor 1.30.1，sisakulint 0.3.7，Poutine 1.1.6である．Poutineは研究repository全体，その他は7 workflowを入力した．TanStackの版・suite・合成入力・SARIF checksumは[比較索引](../results/tanstack-cache-chain/property-level-comparison.json)を参照する．対象外の局所警告があっても対象経路のfindingに数えない．この分類は[保存済みA1～A5出力](../results/core-artifact-tool-baselines/property-level-comparison.json)と[保存済みTanStack出力](../results/tanstack-cache-chain/property-level-comparison.json)についてのもので，ツール一般の能力差を主張しない．

## Research Questionと対応する証拠

| RQ | この成果で答える範囲 | 証拠 |
|---|---|---|
| RQ1：限定した共有状態経路をYAMLから有限状態モデルへ接続できるか | A1・A5，TanStack事件前ではunknownを明示した条件付き反例を得た | 表2・6，生成SMVと独立照合，元YAML行 |
| RQ2：安全境界の変化を同じpropertyで区別できるか | A2～A4，A5 sink control，TanStack対策版と68構成で期待結果に一致 | 表2・3・6，境界マトリクス |
| RQ3：保存済み既存ツール結果に対し，end-to-end情報を追加できるか | 固定版・suite・入力では入口等の局所findingと一つの経路反例を区別できた | 表4，両比較索引のSARIFとfinding位置 |

## 考察，制約，今後の課題

本結果は共有状態の候補を跨いで権限地点まで説明する**経路の構成能力**を示す．同じname/keyだけでは実体同一性を確定せず，保存・復元・権限行使のunknownを反例の仮定に残す．safe controlで反例が消えたのは対象property・入力モデル内であり，repository全体の安全性とは異なる．固定行列68/68を一般的なRecall 100%又はPrecision 100%へ外挿しない．

現在の制約は，任意Bash，任意JavaScript／Docker Action，動的artifact name，任意GitHub式，実効Cache key／scope／versionとentry，保存・復元成功，外部token・認可，一般的並行run，実侵害全経路の自動証明に対応していないことである．これらは今後の課題として分離し，今回の中心評価には追加しない．外部Composite Actionの可変参照も事件時の実体を証明しない．

第三者による再実行手順は[README](../README.md)から[中心評価の再現手順](automatic-analysis-guide.md)へ辿れる．入力workflowは解析対象として読むだけで実行しない．
