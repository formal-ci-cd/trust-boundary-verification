# 公開事例の入力

`actions-attest` は公開された脆弱性 GHSL-2026-225 の報告対象コミットと修正マージコミットです．`ultralytics` は実際の供給網侵害が報告されたプロジェクトの事件直前タグ v8.3.40 と，publish jobを分離した対策コミットです．Ultralyticsの対策版は，全workflowの全問題が修正された版という意味ではありません．

各 `manifest.json` に取得元・コミット・パス・SHA-256を保存しました．元リポジトリをcloneし，指定コミットから取り出したYAMLを改変せず配置しています．コード本体，依存関係，悪性パッケージは実行しません．各プロジェクトのLICENSEも同梱しています．

このディレクトリ内の `.github/workflows` は解析入力です．研究リポジトリのルートにある `.github/workflows` に移して実行しないでください．原本の静的解析結果と，gitスタブ等を使用した局所再現の結果は別に保存します．

- attest: [公開アドバイザリ](https://securitylab.github.com/advisories/GHSL-2026-225_actions_attest/)，[修正PR](https://github.com/actions/attest/pull/488)．実際の悪用は未確認．公開当時，外部PRの受付設定とbranch protectionによる制約があるため，YAMLだけで実際の任意branch書き込み成功を主張しない．
- Ultralytics: [PyPI運営による事件分析](https://blog.pypi.org/posts/2024-12-11-ultralytics-attack-analysis/)，[publish job分離](https://github.com/ultralytics/ultralytics/commit/e0f8eda366c7cf08a1c311a17eaaaf87914f8012)．事件前タグの静的解析であり，事件当時のcache内容・API token・runner状態を再現したものではない．
