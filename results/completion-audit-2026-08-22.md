# 完了条件の確認

確認日：2026年8月22日

| 完了条件 | 状態 | 根拠 |
| --- | --- | --- |
| CodeQL，zizmor，actionlintの検出範囲を整理する． | 完了 | `results/github-actions-artifact-static-analysis-2026-08-22.md`及び`results/github-actions-artifact-analyzer-summary.json`にversion，実行条件及び結果を記録した． |
| 未信頼な処理が保存した共有状態を後続処理が利用する危険構成を実装する． | 完了 | GHA-A producerとGHA-A1を実装し，PR #7のrun `32576681421`から`32576693376`への経路を確認した． |
| 保存側と利用側の同一性をモデルに含める． | 完了 | 成果物名，producer run ID，artifact ID，archive digest及びpayloadのSHA-256を照合した． |
| 保存，復元，利用，権限への到達をNuSMVで検証する． | 完了 | `model/nusmv/gha-a1.smv`で未信頼状態の模擬権限到達に反例が存在し，`model/nusmv/gha-a2.smv`では同じ性質が成立した． |
| 反例をworkflow，job，step，共有状態及び権限へ対応付ける． | 完了 | `results/gha-a1-nusmv-trace.md`に自動生成した対応表を保存した． |
| 危険構成と対になる安全構成を実装して比較する． | 完了 | GHA-A2で信頼済みdigestとの不一致を検出し，利用stepがskipped，模擬権限到達がfalseとなることを確認した． |
| 静的解析，実行結果及び形式検証の推論を区別する． | 完了 | `results/github-actions-artifact-runtime-2026-08-22.md`の比較表及び証拠の区分に記録した． |
| 第三者が再現できる実験方法，結果及び考察を残す． | 完了 | workflow，入力file，実行順序，機械可読な観測記録，変換script，NuSMVモデル及び結果文書をrepositoryに保存した． |
| 実在secretや公開権限を使用しない． | 完了 | consumerは読取り権限だけを持ち，step summaryとlogの`dummy_publish_authority_reached`だけを模擬権限に使用した． |
| GitHubのCache書込み拒否を静的解析と実行時制御に分けて記録する． | 完了 | GHA-C1のCodeQL結果，run `30803626815`の拒否及びNuSMV結果を別々に記録した． |
| テストを実行し，作業単位ごとにコミットする． | 完了 | 20件の単体テスト，NuSMV 2.7.0による2モデルの検査及び`git diff --check`を実行した． |

## 最終的な確認結果

GHA-A1では，静的解析が報告しなかった複数run間の経路について，未信頼PRの内容が同一成果物として保存・復元され，完全性確認なしに利用されて模擬公開権限へ到達した．GHA-A2では，同じ成果物を取得してもdigest不一致により利用を遮断した．NuSMVの危険側の反例と安全側の性質は，この実行結果と一致した．
