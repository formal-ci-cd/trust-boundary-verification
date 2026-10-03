# 中心Threat Modelの抽出範囲と評価契約

[Threat Model](threat-model.md)で定義したCache／artifact経路だけを対象とする．ここでは**現行実装で確認できること**と**必要な限定拡張**を分ける．新しい攻撃クラス，汎用shell／Action意味解析，LLM判定を研究課題にしない．

## 現行の抽出とunknown

| 関係 | 現行で自動取得／判定できる範囲 | unknown又は人手／外部証拠が必要な範囲 |
|---|---|---|
| Source／Producer | YAMLからevent，job/step条件，checkout ref，既知Actionの保存意図を抽出．限定したAND型fork除外を認識 | 任意のGitHub式，外部Action内の隠れた保存，run時の入力，実際のCache token許可と保存成功 |
| Shared Object／SameObject | artifactのname・run-id式とdownloadを構造的に候補結合．TanStack専用解析は固定した外部composite ActionのCache key／pathを照合 | 実効key，scope，version，既存Cacheとの競合，実際の復元entry，artifact ID／digest．構造的対応を実体同一としない |
| Consumer use | `automatic_artifact_analysis.py`は限定した同一jobのartifact file→step output→checkout ref→`git push`構文を認識．TanStack専用解析はSetup Action後の既知の`pnpm`実行を識別 | 任意shellのデータフロー，任意Action内部，復元byteの実際の実行．認識できない場合はunknown |
| Integrity Verification | 既存のA2等には手作業で確認した照合成否をモデル入力として設定可能 | 一般のhash/signature呼び出しの信頼済み基準・順序・失敗時停止は自動証明しない．未解析の照合を「なし」「成功」と決めない |
| Privileged Authority | workflow/jobの明示`permissions`と，既知の`git push`等のsink候補．TanStack専用解析は`id-token: write`の設定を抽出 | 権限設定だけではOIDC発行・publish成功を証明しない．外部token，環境保護，secret参照の実効権限はunknown |
| Order／property | `chain_to_nusmv.py`は保存→復元→利用→権限到達の有限状態モデルと`AG !bad`を生成．TanStack専用モデルはrun間順序を展開 | 一般の並行run全体，実際のsave/restore時刻，第三者Actionの時点依存挙動 |

YAML reader (`yaml_to_model.py`) はCodeQLを構文解析の前提とせず，`on`，複数行`run`，元の行位置を保持する．`automatic_artifact_analysis.py` はartifactについて注釈なしで限定経路を解析する．`tanstack_cache_chain.py` と複数runモデルは**固定事例専用**であり，現時点で汎用Cache解析器が完成したと読んではならない．`model/artifact-chain-annotations.json`やA5は旧来の人手対応を含む．

## 限定拡張の設計契約

実装を広げる場合も，次の決定論的な操作語彙に限る．`cache.write`／`cache.read`，`artifact.upload`／`artifact.download`，`use`／`execute`，`verify`，`privileged_sink`．各抽出結果にはworkflow，job，step，元行，認識規則，対象object式，証拠の種類を付す．既知の`uses@version`と固定・照合済みcomposite Action内の`uses`を展開する．未知のActionやshell表現をブラックボックスのままSafeにしない．

`verify`は単なる`sha256sum`の存在では確定しない．信頼済み基準値，対象byte，利用前の順序，不一致で停止する条件が揃ったときだけ安全境界として真にする．`privileged_sink`はpermission宣言と実行地点を別々に記録する．Cacheは作成・復元時のscope，version，key，実行順を，artifactはrun ID，ID／digestを可能な範囲で照合する．値が得られなければunknownのまま状態空間に残す．

出力は`unsafe-counterexample`，`safe-within-model`，`unknown/unsupported`を区別する．反例にはproducerからsinkまでの各位置と，unknownから真を選んだ前提を列挙する．候補0件を`safe-within-model`へ自動変換しない．BFSとNuSMVの一致は独立した実装照合であり，NuSMVでしか得られない性質とは主張しない．

## 評価の完了条件と現在地

評価単位は警告件数でなく，**一つのfinding又は反例に同じproducer，共有状態，別workflow/runのconsumer，権限地点が接続されるか**である．既存ツールの出力を「入口のみ」「出口のみ」「全経路を接続」「対象findingなし」に分類し，対象版，suite／rule，入力workflow範囲を明記する．関連する複数警告を研究者が後から結んでも「一つのfinding」とは数えない．

| 対象 | 現在確認済み | 中心仮説に対する不足 |
|---|---|---|
| A1 | 未検証artifactを模擬publish地点へ渡し反例．実行時に同一artifact IDを確認 | 実publishは使っていない |
| A2 | 信頼済みdigest不一致時に停止し対象反例なし．同一artifact IDを確認 | 一般の照合抽出は未実装 |
| A3 | 取得しても利用せず対象反例なし．実行時にも利用なし | 全shellの非利用証明ではない |
| A4 | 内容を読んでも権限を持たず対象反例なし．実行時にも権限なし | 研究用dummy権限の範囲 |
| A5 | 未検証artifact→模擬repository更新地点の反例．同一ID／digestを観測 | モデル入力に人手対応が残る．[一境界ずつのモデル対照](../results/core-boundary-controls.json)は検査済み．YAMLからの自動再抽出・実行観測は未完了 |
| TanStack | 事件前に条件付きCache反例，実対策版で対象経路なし．保存／復元順序を複数runモデルで区別．CodeQL等は入口を警告 | 対策版は複数変更を含む．既存テストにはjob条件，checkout ref，OIDC設定，外部ActionのCacheを各一箇所変えた構造対照があるが，実効Cache key／復元byteは未観測 |

A1／A5については[研究用の一境界対照](../results/core-boundary-controls.json)で，各原本と八つの遮断条件の合計18構成をNuSMVと独立した事実列挙で照合した．保存許可と保存成功など，因果上同時に変えるべき事実は一つの**境界条件**として扱う．これはJSON事実モデルの感度検査であり，YAML抽出精度，実行時のsave／restore，又は実際の対策効果を検証したものではない．TanStackは[既存の構造対照テスト](../tests/test_tanstack_cache_chain.py)で四つの独立変更を扱う．順序，完全性検証，実効Cache entryの全組合せを実証済みとは言わない．
