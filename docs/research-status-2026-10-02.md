# 2026-10-02時点の実装・実験結果

追加選別：[Trivyの実侵害](incident-candidate-screening.md)について，攻撃直前に変更されたworkflowを固定し，事件当時に公開済みのCodeQL 2.24.1通常suiteが未信頼checkoutを警告することを確認した．排他的検出候補から除外する．

追加追試：[Jupyterの公開脆弱性GHSL-2026-203](jupyter-composite-approval-race-case-study.md)について，外部複合Actionに対応する限定解析を加え，上流の対策前・対策後の実コミットを自動で区別した．CodeQL通常suiteは双方0件，広いsuiteは双方8件で未信頼checkoutの警告を含む．zizmorは双方2件，sisakulintとactionlintは双方0件だった．NuSMVと独立BFSは対策前だけ同秒更新の反例を出した．これは公開PoCの再検証で，第三者による実侵害の証拠ではない．

追記：同日，[marimoの公開脆弱性GHSL-2026-226](marimo-approval-race-case-study.md)で，対象の承認時刻競合を既存ツールが指摘しない原本を確認し，提案側の限定した自動モデルが反例を出しました．[実際のUltralytics侵害](ultralytics-incident-cache-chain.md)では外部Actionを含む条件付きcache経路を生成しました．[実際のTanStack侵害](tanstack-incident-cache-boundary.md)では事後報告にあるcache保存・復元を別証拠として照合し，事件前と対策版でfork→main cache経路を区別しました．さらに[Clineの実侵害](cline-incident-agent-cache-boundary.md)で，事件前後の全workflowに対してCodeQL・zizmor・actionlintが対象経路を指摘せず，限定モデルが事件前だけ条件付き反例を出す比較を得ました．marimoは悪用確認済みではなく，TanStackでは事件当時のCodeQLが実際のPR攻撃入口を警告し，対策後には警告しません．現在のCodeQLで見えた手動起動イベント由来の警告差は，事件当時の見逃しの証拠ではありません．Clineは事件当時に利用可能だったCodeQL 2.24.1でも対象経路の警告がなく，AIへの入口は既存のPromptPwndルールが事件前3件・対策後0件で検出し，実際の攻撃者がこの入口を使ったかも未確定です．「既存ツール一般が見逃す」または「実際の侵入経路を再構成した」とは主張しません．モデル検査器固有の優位性も未達です．以下の表は最初の比較記録で，追試は各事例研究にまとめています．

今回，4つの課題に対する実装と比較実験の基礎を追加しました．Clineでは実際に無断公開が起きたプロジェクトの対策前設定に存在した**成立し得る複数workflow経路**についてCodeQL・zizmor・actionlintとの検出差を得ました．一方で，Agentが指示に従うことやcache保存・実行の成否はモデル内のunknownであり，無条件に侵害を証明したわけではありません．当該事件の初期侵入経路も未確定です．事件当時のcache権限では反例が出ますが，2026年6月以降の読取り専用権限を同じYAMLに適用するとモデル内の反例は消えます．marimoで示したのは実悪用が未確認の公開脆弱性です．UltralyticsではCodeQLの広いsuiteやzizmorが重要な警告を出し，TanStackの現在版CodeQLでは通常suiteが事件前と対策版に同じcache警告を出しますが，事件当時の版ではPR攻撃入口を事件前だけ警告しました．

## 何ができるようになったか

| 課題 | 今回できたこと | 残る課題 |
|---|---|---|
| 人手の注釈をなくす | 限定したartifact metadata → step output → checkout ref → git pushの経路と，consumerのfork除外条件・設定されたcontents権限を自動で取得し，根拠付きJSON・SMVを生成 | 任意のshell，独自Action，reusable workflow，実行時の成功・artifact ID，検査の一般的な意味解析は未対応．既存A1～A5の手動注釈を全て置換したわけではない |
| モデル検査の意義を調べる | 既存の固定事実モデルを直接判定するbaselineと比較．書換え可能な共有ファイルの検査・置換・利用順序をNuSMV/BFS/無害なファイル操作で比較．TanStackでは固定YAMLから得た二つのrunの役割を使って保存・復元の順序を探索 | BFSでも同じ判定ができる．モデル検査の必須性・大規模な実workflowでの優位性は未証明．一般的な並行モデルのYAML自動生成には未対応 |
| YAMLを直接読む | PyYAMLによる入力経路を追加．`on`キー，全文runブロック，job/stepの条件・env・行位置を保持．CodeQL側も全文を抽出できるよう修正 | GitHubの式やActionの意味までYAMLライブラリが理解するわけではない．フロントエンドの構造一致は意味解析全般の同等性ではない |
| 実在事例で比較する | attest，marimo，Ultralytics，TanStack，Cline，SpotBugsの公開原本を取得．Clineでは無断公開が起きたプロジェクトの対策前設定にあった候補経路にCodeQL・zizmor・actionlintの対象警告がなく，限定モデルが事件前だけ条件付き反例．TanStackは公開されたcache保存・復元記録と照合．SpotBugsではCodeQL通常suiteの警告0件に対し，限定解析が実攻撃入口からsecretを設定した実行stepまでを接続 | ClineのAI入口はPromptPwndが，SpotBugsの未信頼checkoutはsisakulintが警告．モデル内のruntime条件は未観測．ツール一般に対する排他的優位性は未達 |

## 人手を使わない経路

`YAML → 共通モデル → workflow/成果物名/run-selectorで結合 → 限定ルールによる解析 → 根拠付き信頼経路JSON → SMV` を追加しました．新しいCLIは手動注釈ファイルを読みません．判定を入力するJSONも要求しません．

対象は外部fork由来の成果物です．consumerの単純なAND条件に `workflow_run.head_repository.full_name == github.repository` が含まれる場合，この外部fork経路は起動しないと扱います．OR，否定，引用文字列や関数引数内の同じ文字列からは，その結論を導きません．same-repositoryの書込み権限を持つ攻撃者は，この脅威モデルの外です．

シェル解析は正規表現による限定した候補経路の取得です．任意の分岐の実行可能性を証明するものではありません．形式モデルのunknownもそのまま残します．`potential-risk` とNuSMVの反例は，未確定の成否や同一objectが成立する場合の可能な経路であり，実際の侵害成功ではありません．`unknown`，候補0件，未対応resourceを安全判定として扱いません．

設定されたGITHUB_TOKENのcontents書込み権限は，成功したgit pushとは区別します．id-tokenのwrite権限をcontentsのwrite権限と混同しません．別のsecret tokenを使う場合，その権限はunknownになります．

旧CodeQL表のコマンド集合は全件保存しますが，元の実行順序を保証しません．新しい `run-script` 行がある場合は元のrun本文を優先します．同名workflowと複数runner labelの上書きも修正しました．

## 公開事例の結果

CodeQL CLI **2.27.1**，公式query pack **codeql/actions-queries 0.6.36** を固定しました．通常の `actions-code-scanning.qls` と，広い `actions-security-and-quality.qls` を分けて適用しています．構造抽出queryのlibraryは既存設定の **actions-all 0.5.0** とlockで固定しています．

| 入力 | default警告数 | security-and-quality警告数 | 新しい自動解析 | 原本に対するNuSMV / 局所再現 |
|---|---:|---:|---|---|
| attest 脆弱版 `60c8019` | 4 | 5 | 成果物のhead-refがcheckout refとgit pushへ流れる候補を検出 | unknownを含む抽象モデルで反例．無害な成果物を使う局所再現でdummy pushへ到達 |
| attest 修正版 `df58aac` | 0 | 0 | consumerの外部fork除外条件を検出 | この外部forkモデルでは安全．局所再現もorigin条件で停止 |
| Ultralytics 事件前 `dbdb451`（v8.3.40） | 14 | 38 | 旧artifact結合候補0件．新しい外部Action＋cache限定解析で条件付き経路を検出 | 3つの外部条件が成立する場合の反例を生成 |
| Ultralytics publish job分離 `e0f8eda` | 15 | 41 | 旧artifact結合候補0件．新しい限定解析では対象経路なし | 対象モデルでは反例なし |

attestのdefault警告はcode-injection/criticalが3件，untrusted-checkout/highが1件です．広いsuiteではproducerのcode-injection/mediumが1件増えます．これらの警告がbranch選択の認可不足を正確に説明するか，文字種検査で抑止されるinjectionを警告しているかは別の評価項目です．少なくとも「CodeQL警告0件の脆弱版」としては使えません．

この表のUltralytics警告数は，本体workflowだけを入力にした初回結果です．外部Actionを追加するとCodeQLの広いsuiteは式展開を含む46件を報告し，zizmorは式展開とpublish jobのpip cacheを別々に警告しました．詳しい比較は[Ultralytics事例研究](ultralytics-incident-cache-chain.md)にあります．この結果は全経路の成立証明でも既存ツールの見逃しでもありません．対策版はpublish jobを分離したコミットであり，全警告が消える版ではありません．事件当時のcache内容，権限設定，API tokenは再現していません．

attestは[公開された脆弱性](https://securitylab.github.com/advisories/GHSL-2026-225_actions_attest/)であり，実際の悪用は確認していません．Ultralyticsは[PyPI運営が侵害を報告した事例](https://blog.pypi.org/posts/2024-12-11-ultralytics-attack-analysis/)です．この区別は各manifestにも保存しています．

attestでは両フロントエンドの比較対象構造が一致しました．UltralyticsのCI workflowでは，YAML readerがrunnerのmatrix式をそのまま保持する一方，CodeQLは候補runner labelを列挙するという差があります．この差も一括評価summaryへ残し，意味解釈まで同等とはしていません．

原本YAMLは `experiments/public-cases/`，全警告のSARIFは `results/*codeql*.sarif`，自動生成結果は `results/attest-auto/` と `results/ultralytics-auto/` に保存しました．原本はSHA-256で照合しています．

attestの局所再現は，原本consumerのrun本文に対して，download/checkoutを入力準備へ置き換え，式を管理した値へ展開し，gitを記録専用スタブへ置換しています．ネットワーク無効のDocker内で実行し，実際のrepository書込みはしません．結果は `results/attest-local-replay.json` です．実際のGitHub branch protectionや外部PR受付を突破した実験ではありません．

## モデル検査について分かったこと

既存の7段階・固定事実モデルは，未知値の取り得る組合せを考慮しても，直接スクリプトで同じ判定ができます．旧A1～A5と新しいattest 2版の計7モデルでNuSMVと一致しました．結果は `results/static-model-baseline-2026-10-02.json` です．

新しい並行モデルは，**書換え可能な共有ファイル**を検査した後にwriterが置き換える順序を扱います．GitHubのimmutable artifactを上書きできるというモデルではありません．物理的に同じ共有ファイルを使うことは実験の明示的な仮定で，複数YAMLが存在することだけから推定していません．

`restore → verify → replace → use` の順序で違反に到達します．同じsnapshotを検査・利用する構成と，利用時に再検査する構成では，このモデル上の違反はありません．1～3 object・3方式の計9条件でNuSMVと独立BFSの判定が一致し，mutable方式の反例は実際の無害なファイル操作でも再生できました．最大で1,662状態の探索なので，大規模性はまだ主張できません．

この実験で言えるのは「検査をしたという固定フラグだけでは，検査後の置換を表現できない」です．BFSも反例を出せるため，「NuSMVでなければ不可能」とは言えません．今後の比較軸は，性質の記述，状態管理，反例の説明，モデル変更の負担と規模です．

実際の[TanStack原本](tanstack-incident-cache-boundary.md)から検出したproducer・consumerとcache scopeを使い，二つのrunを独立に進める追加モデルも生成しました．事件前はNuSMV/BFSとも条件付き反例あり，対策版は反例なしです．未知条件を同じ値に固定しても，producerのcache保存がconsumerの復元より先なら到達し，復元が先ならそのrunでは到達しません．これは「両workflowに同じcache key式がある」という固定条件に欠ける**順序情報**の必要性を示します．探索対象は一組のPR runとrelease runで，初期cacheは清浄，第三のrunやcache削除は未対応です．244/240状態の小規模実験であり，BFSでも判定が一致するためモデル検査器固有の必要性や性能優位は示しません．TanStack事後報告の公開側2 runに合わせた追加モデルでは，各runのcache復元と汚染コード実行を別の未知値にし，1件のPR側保存との順序を探索しました．事件前は5,968状態で条件付き反例，対策版は5,760状態で反例なしとなり，NuSMVとBFSが一致します．保存が二つの復元の間なら後のrunだけが到達します．事後報告の保存時刻と二つの公開stepメタデータを検出入力とは別に自動照合し，両runの `Setup Tools` が保存後に開始・成功したことを確認しました．cache entry実体や悪性コードの実行はこのメタデータからは未確認です．この増分もNuSMV固有の必須性や大規模性能を示すものではありません．

## 確認した範囲と次の研究課題

追試までにネットワーク無効Dockerで84件のunit testを確認しました．初回には，JSON Schema・根拠の検査，attest両版のCodeQL/YAML構造と全文run一致，9条件のNuSMV/BFS一致，7条件の固定事実モデル/baseline一致，原本checksum，局所再現を確認しています．一括公開事例評価CLIも4版に適用しました．[初回のGitHub CI](https://github.com/formal-ci-cd/trust-boundary-verification/actions/runs/36887435413)では標準Pythonコンテナのbuild，51件のテスト，無害な局所再現が成功しました．marimo，Ultralytics，TanStackの追試結果・実行記録は各事例研究を参照してください．NuSMV自体の再実行はローカルで行っています．

追加候補の[ElementaryとAsyncAPIの実侵害](incident-candidate-screening.md)は，攻撃対象workflowにCodeQLの対象警告があったため，排他的検出の比較対象から除外しました．Elementaryは現在版CodeQLとzizmor，AsyncAPIは事件前に公開済みのCodeQLで確認しています．TanStackの解析はmanifestによる入口・出口workflow名の指定をなくし，全7 workflowから同じ経路を抽出するよう変更しました．事件当時のCodeQL CLI 2.25.4とquery pack 0.6.27を組み合わせて再解析すると，事件前原本と手動起動1行を除いた対照例の両方で実際のPR攻撃イベントを警告し，対策版では0件でした．現在版だけの比較で生じた差は，検出優位性に数えません．さらに[Poutine 1.1.6](additional-poutine-baseline.md)を固定原本に追加適用し，TanStackでは事件前・対策後とも15件，Clineでは13件・11件の警告を保存しました．対象の複数workflow経路を結ぶ警告はありませんが，外部Action参照等の警告は出ています．外部Action原本の取得と対応付けには人手が残ります．[sisakulint 0.3.7の追加比較](additional-sisakulint-baseline.md)では，ClineのAI入口とTanStackの未信頼checkoutを同ツールも警告し，marimoの同秒時刻判定を1箇所修正した前後では同じ13件の警告でした．TanStackは事件前49件・対策後48件で，対象の入口警告が消えますが，保存された警告にfork PRから別workflowのcache復元・OIDC権限までの接続はありません．

次の研究課題は，並行遷移を実workflowと実行観測から生成すること，cacheや独自Actionの意味を広げること，実行観測を自動取得してunknownを減らすこと，PromptPwndが検出した入口からcache・公開jobまでの接続を，他の専門的な検査とも比較することです．Clineでは無断公開が起きたプロジェクトの対策前設定にあった候補経路について検出差を示しましたが，今回の実装は限定パターンであり，修士研究全体の完成ではありません．

[SpotBugsからreviewdogへの実侵害](incident-candidate-screening.md)も追加確認しました．公開PR #1116のbase SHAと変更ファイル `mvnw` をモデルの `./mvnw` sinkに自動照合し，実際の攻撃入力との対応を強めました．さらに公開PR差分を固定し，`mvnw` 冒頭に外部スクリプトを取得して `bash` に渡す命令が追加されたことも静的に照合しました．参照先スクリプトの本文とCI実行ログは未取得です．攻撃直前の対象1 workflowを事後のCodeQL 2版で解析すると，両版とも1/1ファイル抽出，通常suiteの警告0件，広いsuiteは未固定タグ警告1件でした．現在版に外部ActionのYAMLと配布JSも与えた再解析でも，Actionsファイル2/2抽出，通常suiteは0件，広いsuiteは同じタグ警告1件です．提案側の限定静的解析は，外部Actionの条件付き出力からPR側checkoutとsecret付きの `./mvnw` までを接続し，PR参照だけ変えた対照版では対象経路0件でした．同じ固定YAMLから生成した小規模な有限状態モデルでも，原本はNuSMVと独立BFSが条件付き反例を出し，研究用対照版は両者とも反例なしでした．CodeQL 2.27.1を研究用の1箇所対照例にも実行すると，原本と同じく通常0件・広いsuiteの未固定タグ1件で，提案側の限定解析とモデルはこの条件付き経路の有無を区別しました．zizmor 1.30.1とsisakulint 0.3.7も原本・対照例に各8件・15件の同一警告を出し，sisakulintは両方の未信頼checkoutと後続local scriptを警告しました．実際の上流変更 #1237 の前後も固定しました．変更後は `pull_request_target` が消えますが，変更前には環境選択の条件があり，承認設定が未取得なので解析器は未対応と判定します．これを実事件の修正前後の安全性証明には数えません．これはCodeQLとの**遡及的かつ性質を限定した**差です．攻撃日はCodeQL Actions解析のpublic preview開始前で，sisakulintは未信頼checkoutを警告します．固定した外部Action配布JSはネットワーク無効・秘密情報なしのNodeコンテナでダミー入力を実行し，条件真でPR側参照，偽でbase側参照を出力することも確認しました．外部Actionの可変タグが攻撃当時に指したコミットは未確認であり，形式モデルも各160状態で，直接BFSが同じ判定をするため，モデル検査器の必須性は示しません．

2026年10月3日の追加holdoutでは，[MongoDBの承認後mutable PR参照とSPIREのartifact上書き](holdout-candidates-2026-10-02.md)を既存解析器を変えずに評価した．前者は提案側 `no-supported-path`・CodeQL通常suiteが未信頼checkoutを警告，後者は提案側 `unknown`・CodeQL通常suiteがartifact poisoningを警告した．両例とも提案手法の汎化や排他的優位性を裏付けず，未対応の実測として保持する．
