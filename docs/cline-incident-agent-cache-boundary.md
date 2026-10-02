# 実侵害Cline：IssueのAIトリアージから公開用cacheへ至る経路

## 結論と主張の範囲

[Clineの公式事後報告](https://cline.bot/blog/post-mortem-unauthorized-cline-cli-npm)は，公開Issueを処理するAIトリアージがshellを使えたこと，夜間公開jobとdefault branchのcache scopeを共有したこと，公開tokenが侵害され，2026年2月17日に `cline@2.3.0` が無断公開されたことを記録しています．追加された `postinstall` はOpenClawをインストールするもので，ClineはCLI本体の悪性改変やユーザーデータ流出を認めていません．[発見者の報告](https://adnanthekhan.com/posts/clinejection/)は，研究者自身のPoCをミラー上で行い，別の人物が実リポジトリを攻撃したと区別しています．

**実際の攻撃者がIssueトリアージを入口にしたかは未確定です．** 発見者は当時のcache汚染を疑う実行記録を示す一方，初期侵入経路は不明と明記しています．[Clineの公式報告](https://cline.bot/blog/post-mortem-unauthorized-cline-cli-npm)によればソースリポジトリ自体は侵害されていません．本事例は「無断公開が起きたプロジェクトの対策前設定に存在した，CodeQLが対象警告を出さない条件付き経路」の検証であり，「実際の侵入経路を提案手法が再構成した」という証拠ではありません．

2026年10月2日に公開GitHub Actions APIも再確認しました．研究者が異常を報告した1月31日～2月3日の期間には，[2月3日の夜間公開失敗run](https://github.com/cline/cline/actions/runs/21629559332/attempts/2)と[2月2日のnpm夜間公開失敗run](https://github.com/cline/cline/actions/runs/21601946486/attempts/1)などのrunメタデータが残っています．しかし，前者のjob APIではstep一覧が空で，詳細ログはHTTP 410でした．後者もjob APIのstep一覧は空です．従って公開APIから当時の `Post Checkout` 異常やcache復元を独立に再検証できず，失敗runの存在だけを侵害の証拠にはしません．この限界は発見者の画像による観測と区別します．

2月9日の対策前の**全15 workflow**と[公式対策PR #9211](https://github.com/cline/cline/pull/9211)のマージ時の**全12 workflow**を固定し，同じ解析設定で比較しました．CodeQL CLI 2.27.1の標準・広いsuite，zizmor 1.30.1 regular，actionlint 1.7.12の警告には，対象の **公開Issue→AIのBash→default branch cache→夜間公開jobの資格情報** という経路を指摘するものはありません．提案側の限定YAML解析は，事件前にこの経路の**条件付き反例**を作り，対策後には経路なしと判定しました．NuSMV 2.7.0と独立BFSが一致します．

事件前に利用可能だった**CodeQL CLI 2.24.1（2026年2月5日公開）と対応する `codeql/actions-queries@0.6.19`** でも同じ原本を再解析しました．標準suiteの警告数は事件前・対策後とも1件，広いsuiteでは8件・6件で，現在版と同じ対象外の規則・位置です．両suiteとも候補経路を結ぶ警告はありません．これは当時**利用可能だった版による遡及検査**であり，事件当日にClineがCodeQLを導入・実行していたことを意味しません．[完全なSARIFと検査条件](../results/cline-agent-cache-boundary/historical-codeql-2026-02/evidence-index.json)を保存しました．

この差は**CodeQL・zizmor・actionlintと対象経路**についての結果です．事件前から公開されていた[AikidoのPromptPwndルール](https://github.com/AikidoSec/opengrep-rules/blob/12b001b4b1d65532b1a988b2f57a44468ad50445/rules/github_workflow_prompt_injection/github_workflow_prompt_injection.yaml)をOpengrep 1.30.0で実行すると，事件前に3件，対策後に0件を検出しました．事件前の1件はまさに `claude-issue-triage.yml:50` の入口です．従って**「既存ツール一般が見逃した」または「提案手法だけが入口を見つけられる」は誤り**です．本比較で追加できたのは，この入口と**別runの公開用cache・資格情報**を一つの条件付き反例へ接続する説明です．

## 原本と検出入力

`experiments/public-cases/cline/manifest.json` のSHA-256とGitコミットで原本を固定しています．事件前は `7bdbf0a9a745f6abc09483fe9b08874c80fb44f3`，対策後は `84fef6fe1f38a3cb98723ed909a62258f03f21b9` です．後者は[対策PR](https://github.com/cline/cline/pull/9211)のマージコミットです．Apache-2.0の原本ライセンスも保存しました．攻撃payload，実際のcache内容，侵害tokenは取得・保存していません．

`tools/agent_cache_boundary.py` はmanifestでworkflow名や危険判定を与えず，全workflowを走査します．対象とする入口は，`issues.opened`，`anthropics/claude-code-action`，非書込みユーザーの許可，Issueタイトルのprompt参照，Bash許可が同じjobにある構成です．対象とする出口は，夜間workflowのUbuntu jobで `actions/checkout` の後に `actions/cache` を復元し，後続の公開stepがsecretを使う構成です．事件前の入口1件と出口2件（`npm-nightly.yaml`，`publish-nightly.yml`）を抽出し，対策後は両方0件でした．[Cacheractの技術説明](https://adnanthekhan.com/2024/12/21/cacheract-the-monster-in-your-build-cache/)では細工したcacheアーカイブの展開でcheckoutの `action.yml` を上書きし，job末尾のpost stepを乗っ取ります．そのため `npm ci` が復元した `node_modules` を実行するという前提は置きません．cache対象を `docs/static-cache` に変えるだけの対照例も候補として残り，checkoutを除く対照例はこの機構の対象外になります．ただし，実際のアーカイブがcheckoutを上書きできたかは原本YAMLから分かりません．任意のAI Actionやcache展開実装を理解する汎用検出器ではありません．PromptPwndは入口を既に検出できるため，入口検出の新規性は主張しません．

| 段階 | 原本から読めること | 原本だけでは分からないこと |
|---|---|---|
| 入口 | Issueタイトルがpromptに入り，外部ユーザーもBashを持つAgentを起動できる | 実際にAgentが悪意ある指示に従うか |
| cache | `issues` runは当時default branchで動き，夜間workflowはcheckout後に`node_modules`をcache復元する | slotの空き・退避，汚染entryの保存，実効key一致と復元，checkoutのpost step改変 |
| 公開 | cache復元後の夜間jobが `NPM_RELEASE_TOKEN` またはVS Code/OpenVSXの公開secretを使用する | 汚染内容が実行され，後続の資格情報へ届くか |

モデルでは，Agentの命令実行，cache枠の空き，保存成功，実効key一致，復元，汚染内容の実行を未観測の真偽値として探索します．事件前は全条件が成立する場合に `AG !bad` が偽となり，対策後は入口と公開jobのcacheがなく `AG !bad` が真です．BFSも同じ結果です．反例は**起こり得る経路**を示すもので，YAMLだけで当日の具体的な実行を証明しません．実事件の事後観測は `incident-observation.json` に，検出入力と分けて記録しました．

## 同一入力での比較

CodeQLは両版の全workflowを抽出しました．事件当時版はLinux x64公式配布物のSHA-256を検証し，ネットワーク無効の解析用Dockerで実行しました．zizmorは `--offline --no-config --no-ignores --persona=regular`，actionlintとOpengrepも全workflowを走査しました．PromptPwndルールは事件前の2025年12月3日のコミットに固定しました．完全な警告，行番号，checksum，NuSMV出力は `results/cline-agent-cache-boundary/evidence-index.json` と同ディレクトリに保存しています．「対象経路0件」は全警告を確認した結果で，総警告数が0という意味ではありません．

| 評価 | 事件前15件 | 対策後12件 | 対象経路との関係 |
|---|---:|---:|---|
| CodeQL default（現行版・事件当時版の双方） | 1件 | 1件 | 両版とも別workflowの権限警告．対象経路0件 |
| CodeQL security-and-quality（現行版・事件当時版の双方） | 8件 | 6件 | 未固定Actionの警告など．事件前の入口workflowにもタグ未固定警告はあるが，Issue→AI→cacheの警告は0件 |
| zizmor regular | 128件 | 109件 | 入口workflowの警告はcheckout認証残存と未固定Actionだけ．3件のcache警告は別の `publish.yml`．対象経路0件 |
| actionlint | 2件 | 2件 | 古いActionと未定義step参照．対象経路0件 |
| Poutine 1.1.6 | 13件 | 11件 | 入口の外部Action参照は警告．Issue→AI→cache→公開jobの接続は指摘せず |
| sisakulint 0.3.7 | 166件 | 126件 | 事件後の版による追試．入口のprompt・権限・ツール許可を直接警告．候補の複数run経路への接続は警告に含まない |
| PromptPwnd / Opengrep | 3件 | 0件 | 事件前のIssue→AI入口を検出．cacheから公開jobまでの接続は警告に含まない |
| 提案側の対象経路 | 条件付き反例 | 経路なし | 入口と夜間公開jobを別workflow・別runとして接続 |

Poutineの[完全な出力と評価条件](additional-poutine-baseline.md)と[sisakulintの追加比較](additional-sisakulint-baseline.md)は別に保存しました．sisakulintの総数には原本に含まないrepository設定を要求する警告も入るため，件数の大小を検出性能とみなしません．いずれも事件後の版による追試です．

2026年2月のcache権限をモデル化しています．[GitHubは2026年6月に低信頼イベントのdefault branch cache tokenを読取り専用に変更](https://github.blog/changelog/2026-06-26-read-only-actions-cache-for-untrusted-triggers/)しました．同じ事件前YAMLにこの現行権限を当てる感度分析では，書込み遷移が塞がれ，NuSMVとBFSの双方で反例が消えます．これは権限仕様を追加した反実仮想であり，実際のGitHub環境を再実行した結果ではありません．この歴史的な反例を現在のGitHub Actionsで実行可能な攻撃とみなしてはいけません．事件当時版CodeQLの追試を含め，過去の設定を後から再解析した結果であり，事件当日に各ツールが導入されていた証拠ではありません．

## 再実行と残る限界

原本workflowを実行せず，ネットワーク無効の解析用Dockerでモデルを生成します．NuSMVを生成モデルに対して実行します．

```sh
mkdir -p /tmp/cline-boundary-analysis
docker run --rm --network none -v "$PWD:/repo:ro" \
  -v /tmp/cline-boundary-analysis:/analysis -w /repo trust-boundary-analysis \
  python3 tools/agent_cache_boundary.py experiments/public-cases/cline \
  --output /analysis
NuSMV /tmp/cline-boundary-analysis/pre-incident.smv
NuSMV /tmp/cline-boundary-analysis/mitigation.smv
NuSMV /tmp/cline-boundary-analysis/pre-incident-current-policy.smv
```

検出器はYAMLライブラリを直接使い，CodeQLの構文抽出を前処理に必要としません．ただしActionの設定値から「Agentが悪意ある指示に従い得る」という可能性を置き，細工したcacheアーカイブによるpost step改変，キャッシュの実効同一性，攻撃成功は検証していません．**Clineの事件と設定を確認した後でこの限定規則を実装**したため，未知の事例に対する検出率を示す結果でもありません．BFSでも同じ結果が出るため，**NuSMV固有の必然性，大規模性，汎用自動化はまだ示せていません**．この事例は，無断公開が起きたプロジェクトに存在した複数workflowの条件付き経路を説明します．実際の攻撃者がこの入口と経路を利用した証拠は得られていません．
