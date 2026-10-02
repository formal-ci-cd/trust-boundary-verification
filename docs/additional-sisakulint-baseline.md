# sisakulintの追加比較：承認時刻の差とAI入口

[sisakulint v0.3.7](https://github.com/sisaku-security/sisakulint/releases/tag/v0.3.7)を固定し，Linux arm64公式配布物のSHA-256を照合しました．ネットワーク無効のDockerで原本を読み取り専用にして `-fix off` で解析し，[完全なSARIFとchecksum](../results/additional-sisakulint-baseline/evidence-index.json)を保存しました．この版は2026年8月31日の公開です．**過去の事件当日に利用できたツールとして扱わない**遡及比較です．

| 入力 | 警告数 | 対象の性質について分かること |
|---|---:|---|
| marimo脆弱版の `marimo-bot.yml` | 13 | 未信頼PRのcheckoutやcacheの警告はあるが，同じ秒のPR更新を承認後に許してしまう時刻判定への警告はない |
| 同ファイルの `-gt` を `-ge` にした対照版 | 13 | 規則・ファイル・行・メッセージが脆弱版の13件と完全に一致．提案モデルはこの変更で対象反例が消える |
| Cline対策前の全15 workflow | 166 | IssueタイトルのAI prompt参照，非書込みユーザーの許可，Bash等の過大なツール許可を個別に警告 |
| Cline対策後の全12 workflow | 126 | 削除されたAI入口への対象警告はない |

marimoで比較したのは[公開GHSL-2026-226](marimo-approval-race-case-study.md)の**同秒承認という特定の性質**です．sisakulintはより広い危険を警告しており，「脆弱版に警告がない」という結果ではありません．同じファイルで比較演算子だけを変えた対照版に警告が残るため，この警告集合だけでは問題の修正を判定できません．ただしmarimoに実悪用の証拠はなく，これを「実事件で提案手法だけが侵害を検出した」と数えません．

[Cline](cline-incident-agent-cache-boundary.md)では，既存のPromptPwndに加えてsisakulintもAIへの入口を明確に指摘しました．警告はIssue→AI→別runのcache→夜間公開資格情報を一本の経路としては結んでいません．実際の攻撃者がその候補経路を使ったかは未確定です．総警告数には，原本に含めなかったrepository設定を要求するDependabot関連の警告も入るため，**総数だけでツールの精度や検出率を比較しません**．Cline入力には全workflowを含めましたが，`.github` 外のソースや実行ログは渡していません．
