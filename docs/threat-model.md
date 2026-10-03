# 中心Threat Model：共有状態を介するGitHub Actionsの信頼境界

## 対象と中心仮説

対象は，外部Pull Request等に由来する未信頼producerがGitHub ActionsのCache又はartifactに書き込み，**別workflow又は別run**のconsumerが同じ共有状態を読み，十分な完全性確認なしに実行又は権限付き処理へ使用する構成に限る．repository write，package publish，deploy，secret又はOIDCを用いる操作をprivileged authorityとする．単に権限を設定したjobが存在するだけでなく，その共有状態に依存する利用から権限操作までの経路を要する．

中心仮説は，この経路をCI/CD構成から有限状態モデルにし，`AG !bad` を検査することで，既存スキャナが入口や出口を個別に警告する場合でも，**producer→共有object→別workflow/runのconsumer→privileged authority**を一つの条件付き到達可能性と反例として提示できる，というものである．特定スキャナの無検出やNuSMV固有の性能を仮説に含めない．

## 用語と観測単位

| 要素 | 定義 | 静的記述だけで確定しない点 |
|---|---|---|
| Source | 外部fork PR等の低信頼eventと，その文脈から選択・取得する攻撃者制御のref又は入力．単に`pull_request_target`があるだけでは未信頼コードの利用としない | 実際の入力，条件式の評価，fork除外の有効性 |
| Producer | Sourceに影響されたjob/runが，Cache又はartifactへ保存を試みる主体 | tokenによる保存許可，保存成功，保存したbyte |
| Shared Object | Cache entry又はartifact IDで識別され，run間で持続する対象 | 実効key，scope，cache version，選択されたentry，artifact ID／digest |
| Consumer | **producerとは別workflow又は別run**でShared Objectを復元・取得するjob/run | 実際の復元成功，展開先と読まれたbyte |
| Integrity Verification | 信頼済み基準に結び付いたhash／signature等を**利用より前**に照合し，不一致時に利用を止める処理．攻撃者がobjectと一緒に指定できるdigestの比較だけでは足りない | 基準の信頼性，成功／失敗，失敗後の分岐 |
| Privileged Authority | repository write，publish，deploy，secret又はOIDCを使用する実行地点と，そこに必要な権限 | 外部token，環境保護，実行時の実効権限，操作成功 |
| SameObject | producerが書いた**実際の版**をconsumerが読める関係．名前やkeyの文字列一致は候補結合に過ぎない | Cacheのscope・version・既存entry・検索順，artifactのrun ID・ID・digest |
| Order | `source→write→save success→restore/download→verify/use→privileged sink` の順序と，別runの前後関係 | saveとrestoreの実時刻，並行runのinterleaving，既存Cacheの選択 |

Cacheの具体的なscopeは[object同一性](github-actions-cache-object-identity.md)に従う．artifactではnameだけでなくproducer run ID，artifact ID／digest及びdownload元runを照合する．同じIDとdigestの観測があれば実行時のSameObjectを強く裏付けるが，YAMLのname一致だけでは証明しない．

## 成立条件と安全境界

固定したplatform仕様・入力・run集合・対象権限について，`bad` は以下を**すべて満たす実行列が少なくとも一つ存在すること**と定義する．この有限モデルの中では，この連言が違反到達の必要十分条件である．現実の侵害，外部サービスでの認可成功，任意のシェルやActionの全挙動についての必要十分条件ではない．

1. 外部Sourceがproducerの保存byte又は保存対象を制御でき，producerの実行条件が満たされる．
2. 保存意図だけでなく，そのrunで保存が許可され，成功する．
3. producerとは別workflow又は別runのconsumerが，**保存後**に同じShared Objectの版を復元・取得する．
4. consumerがその内容を実行，又は内容に依存した値を権限付き処理へ渡す．
5. 利用前の信頼済み完全性照合が成功して汚染を除去することも，照合失敗による停止もない．
6. consumerに対象のprivileged authorityがあり，汚染内容に依存する処理がその権限操作地点へ到達する．

安全性propertyは `AG !(authority_reached & object_tainted)` と表せる．反例は上記を満たす**モデル内の可能な実行**である．いずれか一条件が全実行で確実に偽と立証されれば対象経路は遮断される．例は，有効なfork除外，保存tokenの拒否，参照不能なCache scope，異なるartifact ID，復元前の保存不成立，利用をしないこと，信頼済みdigest不一致で停止，対象権限がないこと．単なるkey/name不一致や警告0件は十分な安全根拠ではない．

真偽を確定できない事実は`unknown`とする．`unknown`を両値に展開して反例が出たら**条件付き到達可能性**，反例が出なくても対象範囲・抽出漏れ・モデル化した事実を明記する．候補0件，未対応Action，未解析シェル，又は未観測の実行時事実をSafeへ変換しない．`Safe`は，定義した有限モデルと特定の対象経路において反例がないという意味に限る．

## 抽出境界

YAML loaderはevent，job/step条件，permissions，`uses`と`with`，run本文と行位置を直接取得できる．既知の`actions/cache`，`upload-artifact`，`download-artifact`，限定されたcheckout ref，明示的なfork除外，既知のshellパターンとsinkは規則ベースで扱う．外部composite Actionは**固定した定義を入手し照合できた場合**に既知のCache/Artifact操作を読む．任意のAction内部，任意の式・シェル，外部token，実効key，save/restore成否，実行されたbyteは一般には自動確定できない．詳しい現状と必要最小の設計は[抽出範囲とunknown](scope-and-extraction.md)に記す．LLMは抽出・判定に組み込まない．

## 対象外

一般的なTOCTOU，AI prompt injection，runner escape，単純なshell injection，依存ライブラリの脆弱性は中心対象に加えない．既存の[Jupyter](jupyter-composite-approval-race-case-study.md)，[marimo](marimo-approval-race-case-study.md)，[SpotBugs](spotbugs-incident-case-study.md)，[Cline](cline-incident-agent-cache-boundary.md)の記録は削除せず，[事例の位置付け](thesis-claim-and-evidence.md)に区分する．
