# 公開事例の入力

`actions-attest` は公開された脆弱性 GHSL-2026-225 の報告対象コミットと修正マージコミットです．`ultralytics` は実際の供給網侵害が報告されたプロジェクトの事件直前タグ v8.3.40 と，publish jobを分離した対策コミットです．Ultralyticsの対策版は，全workflowの全問題が修正された版という意味ではありません．

`marimo` は公開脆弱性 GHSL-2026-226 の報告対象コミットと，問題のworkflowを削除したコミットです．確認された実際の侵害ではありません．原本の `.github/workflows` と `.github/actions` を収録し，`-gt` を `-ge` に1箇所変える本研究の対照版は解析時に別途生成します．[比較実験の詳細](../../docs/marimo-approval-race-case-study.md)に，警告と形式モデルの対応を示します．

各 `manifest.json` に取得元・コミット・パス・SHA-256を保存しました．元リポジトリをcloneし，指定コミットから取り出したYAMLを改変せず配置しています．コード本体，依存関係，悪性パッケージは実行しません．各プロジェクトのLICENSEも同梱しています．

このディレクトリ内の `.github/workflows` は解析入力です．研究リポジトリのルートにある `.github/workflows` に移して実行しないでください．原本の静的解析結果と，gitスタブ等を使用した局所再現の結果は別に保存します．

- attest: [公開アドバイザリ](https://securitylab.github.com/advisories/GHSL-2026-225_actions_attest/)，[修正PR](https://github.com/actions/attest/pull/488)．実際の悪用は未確認．公開当時，外部PRの受付設定とbranch protectionによる制約があるため，YAMLだけで実際の任意branch書き込み成功を主張しない．
- Ultralytics: [PyPI運営による事件分析](https://blog.pypi.org/posts/2024-12-11-ultralytics-attack-analysis/)，[publish job分離](https://github.com/ultralytics/ultralytics/commit/e0f8eda366c7cf08a1c311a17eaaaf87914f8012)．事件前タグの静的解析であり，事件当時のcache内容・API token・runner状態を再現したものではない．

Ultralyticsには，事件前の外部Actionの固定版と事件後の修正版も追加しました．`manifest.json`の`externalAction` / `externalActionFixed`は本体workflowとは異なるrepositoryから取得した原本で，`@main`の事件当日実行SHAを保証するものではありません．[二つのrepositoryを結ぶ評価](../../docs/ultralytics-incident-cache-chain.md)に入力と未確認事項を記録します．
