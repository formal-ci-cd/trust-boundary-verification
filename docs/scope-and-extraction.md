# 中心Threat Modelの抽出範囲と評価契約

[Threat Model](threat-model.md)で定義したCache／artifact経路だけを対象とする．ここでは**現行実装で確認できること**と**必要な限定拡張**を分ける．新しい攻撃クラス，汎用shell／Action意味解析，LLM判定を研究課題にしない．

## 現行の抽出とunknown

| 関係 | 現行で自動取得／判定できる範囲 | unknown又は人手／外部証拠が必要な範囲 |
|---|---|---|
| Source／Producer | YAMLからevent，job/step条件，checkout ref，既知Actionの保存意図を抽出．限定したAND型fork除外を認識 | 任意のGitHub式，外部Action内の隠れた保存，run時の入力，実際のCache token許可と保存成功 |
| Shared Object／SameObject | artifactのname・run-id式とdownloadを構造的に候補結合．TanStack専用解析は固定した外部composite ActionのCache key／pathを照合 | 実効key，scope，version，既存Cacheとの競合，実際の復元entry，artifact ID／digest．構造的対応を実体同一としない |
| Consumer use | `automatic_artifact_analysis.py`は限定した同一jobのartifact file→step output→checkout ref→`git push`構文を認識．TanStack専用解析はSetup Action後の既知の`pnpm`実行を識別 | 任意shellのデータフロー，任意Action内部，復元byteの実際の実行．認識できない場合はunknown |
| Integrity Verification | A2の固定YAMLのliteral digest，対象ファイルのsha256sum，照合結果output，後続useのguardを限定規則で認識可能．旧モデルでは照合成否を手入力可能 | 一般のhash/signature呼び出しの信頼済み基準・順序・失敗時停止は自動証明しない．未解析の照合を「なし」「成功」と決めない |
| Privileged Authority | workflow/jobの明示`permissions`と，既知の`git push`等のsink候補．TanStack専用解析は`id-token: write`の設定を抽出 | 権限設定だけではOIDC発行・publish成功を証明しない．外部token，環境保護，secret参照の実効権限はunknown |
| Order／property | `chain_to_nusmv.py`は保存→復元→利用→権限到達の有限状態モデルと`AG !bad`を生成．TanStack専用モデルはrun間順序を展開 | 一般の並行run全体，実際のsave/restore時刻，第三者Actionの時点依存挙動 |

YAML reader (`yaml_to_model.py`) はCodeQLを構文解析の前提とせず，`on`，複数行`run`，元の行位置を保持する．`automatic_artifact_analysis.py` はartifactについて注釈なしで限定経路を解析する．さらに[中心artifact部分集合の評価器](../tools/evaluate_artifact_subset.py)は，A1～A5の固定YAMLから，保存・取得の結合，信頼済みdigest guard，非利用，artifact metadata→step output→模擬更新markerを注釈なしで抽出し，NuSMVと独立した事実列挙で照合する．[生成結果](../results/core-artifact-subset/analysis.json)ではA1・A5に条件付き反例，A2～A4に対象反例なし．A5のYAMLにある文字種チェックは完全性照合ではない．実repository writeは行わない．`tanstack_cache_chain.py` の事例専用結果は保存する．新しい限定Cache経路解析は複数workflowのPR・Cache・明示repository write地点を再構成するが，実効Cache objectと実被害の因果関係は証明しない．`model/artifact-chain-annotations.json`やA5は旧来の人手対応を含む．

## 現在主張できるSupported Subset

- YAML frontendは`.github/workflows/*.yml`／`*.yaml`を安全なloaderで読み，`on`，job，step，`if`，`permissions`，`env`，`uses`，`with`，複数行`run`とstep元行を保持する．式全体を評価するとは主張しない．
- 中心artifact評価器は`pull_request`の暗黙ref checkout，A1のcheckout内literal file又はA5の限定`cp`列からupload対象へのbyte由来，`actions/upload-artifact`，`workflow_run.workflows`で指定された別workflow，`${{ github.event.workflow_run.id }}`を渡す`actions/download-artifact`を候補結合する．同一producer workflow内でliteral artifact nameが異なれば対象ペアの`SameObject=false`とし，一致は候補に過ぎず実体同一性はunknownに保つ．動的name，任意のproducer shellや絶対pathでbyte由来を追えない場合，またはconsumer job内に未対応Actionがある場合は`unknown/unsupported`とする．artifact ID，digest，取得成功は静的にはunknownである．
- shellはA1～A5に現れる**限定した形**だけを認識する．artifact pathを指す`ARTIFACT_FILE`からの`tr`読取り，literal digestに対する`sha256sum`と`verified` output，そのoutputを条件とする後続use，summary-only出力，研究用`dummy_publish_authority_reached` markerを扱う．guardに余分なコマンドがあれば検証済みとみなさない．模擬markerは実publish権限ではない．
- shell AST導入は検討したが，現段階の主張範囲は上記の固定した数形のコマンドとguardに限る．未認識の早期終了や追加`if`を`unknown/unsupported`へ落とす限定文法・回帰テストを優先した．任意Bashの制御フローやデータフローを認識したという主張はしない．
- artifact部分集合の条件式はliteral `true`／`false`と，A1～A5の`workflow_run`成功・PR起動guardだけを確定する．他のjob／upload／download／use条件はunknownとし，Safeへ変換しない．`push`だけのproducerはこのThreat ModelのPR Sourceを持たない．upload許可・成功はYAMLから確定しない．
- `contents: write`／`id-token: write`等の明示permissionは構造として取得できるが，権限の**使用**と同一視しない．`git push`は既存の限定metadata flowで候補とする．secret，外部token，外部認可の成功はunknownである．
- Cacheは[共通操作inventory](../tools/extract_shared_operations.py)で，既知の`actions/cache`／`save`／`restore`と照合済みComposite Action snapshot内の操作を全workflowから抽出し，key式を比較した候補対を列挙する．[TanStack原本の候補](../results/tanstack-cache-chain/common-inventory-pre.json)にはPR側とrelease側が含まれる．さらに[限定Cache経路解析](../tools/evaluate_cache_subset.py)は，PR checkout→Cache操作→別runの復元→固定Composite Action内`pnpm install`→明示`git push`を結び，repository write地点への条件付き反例を構成する．jobとstepの条件は限定したAND型event／fork述語だけを確定し，それ以外はunknownにする．fork PRを明示的に除外するjobからは，そのSourceの候補を作らない．[事件前](../results/tanstack-cache-chain/common-path-pre/analysis.json)と[対策版](../results/tanstack-cache-chain/common-path-mitigation/analysis.json)を同じpropertyで比較する．これはこのコマンド・Action contractの部分集合に限り，任意のCache利用・publish・OIDC使用の汎用解析ではない．可変tagの時点の内容，実効key，scope，version，復元entryはunknownである．
- `pull_request_target`のfork入力は単なるevent存在で確定しない．限定したcheckout refと有効なfork除外を追加評価する必要がある．任意の`if`式，JavaScript／Docker Action，任意Bash，固定できないComposite Actionはunsupportedとして扱う．

## 限定拡張の設計契約

実装を広げる場合も，次の決定論的な操作語彙に限る．`cache.write`／`cache.read`，`artifact.upload`／`artifact.download`，`use`／`execute`，`verify`，`privileged_sink`．各抽出結果にはworkflow，job，step，元行，認識規則，対象object式，証拠の種類を付す．既知の`uses@version`と固定・照合済みcomposite Action内の`uses`を展開する．未知のActionやshell表現をブラックボックスのままSafeにしない．

`verify`は単なる`sha256sum`の存在では確定しない．信頼済み基準値，対象byte，利用前の順序，不一致で停止する条件が揃ったときだけ安全境界として真にする．`privileged_sink`はpermission宣言と実行地点を別々に記録する．Cacheは作成・復元時のscope，version，key，実行順を，artifactはrun ID，ID／digestを可能な範囲で照合する．値が得られなければunknownのまま状態空間に残す．

出力は`unsafe-counterexample`，`safe-within-model`，`unknown/unsupported`を区別する．反例にはproducerからsinkまでの各位置と，unknownから真を選んだ前提を列挙する．候補0件を`safe-within-model`へ自動変換しない．BFSとNuSMVの一致は独立した実装照合であり，NuSMVでしか得られない性質とは主張しない．

## 評価の完了条件と現在地

評価単位は警告件数でなく，**一つのfinding又は反例に同じproducer，共有状態，別workflow/runのconsumer，権限地点が接続されるか**である．既存ツールの出力を「入口のみ」「出口のみ」「全経路を接続」「対象findingなし」に分類し，対象版，suite／rule，入力workflow範囲を明記する．関連する複数警告を研究者が後から結んでも「一つのfinding」とは数えない．

| 対象 | 現在確認済み | 中心仮説に対する不足 |
|---|---|---|
| A1 | 固定YAMLから注釈なしで未検証artifact→模擬publish地点を結合し条件付き反例．実行時に同一artifact IDを確認 | 実publishは使っていない |
| A2 | 固定YAMLの信頼済みdigest guardを認識し対象反例なし．同一artifact IDを確認 | 一般の照合抽出は未実装 |
| A3 | 固定YAMLの限定的なsummary-only shellを認識して対象反例なし．実行時にも利用なし | 全shellの非利用証明ではない |
| A4 | 固定YAMLの読取りと模擬publish=falseを認識して対象反例なし．実行時にも権限なし | 研究用dummy権限の範囲 |
| A5 | 固定YAMLからmetadata→step output→模擬repository更新地点を注釈なしで結合し条件付き反例．同一ID／digestを観測 | 旧モデル入力には人手対応が残る．自動評価は模擬markerまでで，実権限は推論しない．[一境界ずつのモデル対照](../results/core-boundary-controls.json)は検査済み |
| TanStack | 事件前に条件付きCache反例，実対策版で対象経路なし．保存／復元順序を複数runモデルで区別．CodeQL等は入口を警告 | 共通解析では事件前のPR→Cache→releaseの明示`git push`地点に条件付き反例，対策版では同じpropertyに反例なし．ただし，2026年5月のscope規則を入力とし，実効key／復元byte，npm公開への実際の因果経路は未観測．既存テストにはjob条件，checkout ref，OIDC設定，外部ActionのCacheを各一箇所変えた構造対照がある |

A1／A5については[研究用の一境界対照](../results/core-boundary-controls.json)で，各原本と八つの遮断条件の合計18構成をNuSMVと独立した事実列挙で照合した．保存許可と保存成功など，因果上同時に変えるべき事実は一つの**境界条件**として扱う．これはJSON事実モデルの感度検査であり，YAML抽出精度，実行時のsave／restore，又は実際の対策効果を検証したものではない．TanStackは[既存の構造対照テスト](../tests/test_tanstack_cache_chain.py)で四つの独立変更を扱う．順序，完全性検証，実効Cache entryの全組合せを実証済みとは言わない．

[YAML境界マトリクス](yaml-boundary-matrix-evaluation.md)は，artifactの32個の単独軸対照，PR Source／upload／download／useの全16通り，digest guardを持つ8通り，A5のsink遮断，TanStackの10個のYAML対照を，抽出→NuSMV→独立探索まで通す．同名uploadが複数ある場合も各候補の反例を残す．期待値をテスト表で先に固定し，unsupported又は候補0件は`unknown/unsupported`として扱う．ここで確認したのは**宣言した具体的な構文形とその組合せ**であり，任意のYAMLや実行時の許可・実体同一性の網羅的証明ではない．Cacheでは逆順のschedulerに違反がないことも，抽出済み事実から照合する．
