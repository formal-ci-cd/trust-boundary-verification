# 2026-10-02時点の実装・実験結果

今回，4つの課題に対する実装と比較実験の基礎を追加しました．ただし，「CodeQLでは検出できない実際の侵害を提案手法だけが検出した」という結果は，まだ得られていません．その結論に合わせて評価を変えることはせず，対応範囲と未達の項目を残しています．

## 何ができるようになったか

| 課題 | 今回できたこと | 残る課題 |
|---|---|---|
| 人手の注釈をなくす | 限定したartifact metadata → step output → checkout ref → git pushの経路と，consumerのfork除外条件・設定されたcontents権限を自動で取得し，根拠付きJSON・SMVを生成 | 任意のshell，独自Action，reusable workflow，実行時の成功・artifact ID，検査の一般的な意味解析は未対応．既存A1～A5の手動注釈を全て置換したわけではない |
| モデル検査の意義を調べる | 既存の固定事実モデルを直接判定するbaselineと比較．さらに書換え可能な共有ファイルの検査・置換・利用順序をNuSMV/BFS/無害なファイル操作で比較 | BFSでも同じ判定ができる．モデル検査の必須性・大規模な実workflowでの優位性は未証明．並行モデルはYAMLから自動生成していない |
| YAMLを直接読む | PyYAMLによる入力経路を追加．`on`キー，全文runブロック，job/stepの条件・env・行位置を保持．CodeQL側も全文を抽出できるよう修正 | GitHubの式やActionの意味までYAMLライブラリが理解するわけではない．フロントエンドの構造一致は意味解析全般の同等性ではない |
| 実在事例で比較する | actions/attestの脆弱版・修正版と，実際に侵害されたUltralyticsの事件前・対策版をcloneから取得し，同一の固定CodeQL設定で比較 | attestにはCodeQLも関連警告を出す．Ultralyticsのcache経路は提案側が未対応．実事件に対する優位性は未達 |

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
| Ultralytics 事件前 `dbdb451`（v8.3.40） | 14 | 38 | artifact結合候補0件，cache経路は未対応 | 対応する形式モデルがないため判定しない |
| Ultralytics publish job分離 `e0f8eda` | 15 | 41 | artifact結合候補0件，cache経路は未対応 | 対応する形式モデルがないため判定しない |

attestのdefault警告はcode-injection/criticalが3件，untrusted-checkout/highが1件です．広いsuiteではproducerのcode-injection/mediumが1件増えます．これらの警告がbranch選択の認可不足を正確に説明するか，文字種検査で抑止されるinjectionを警告しているかは別の評価項目です．少なくとも「CodeQL警告0件の脆弱版」としては使えません．

Ultralyticsのdefault警告はpermissions指定の不足です．広いsuiteではAction tag固定に関する警告も含まれます．この警告数だけで，事件のcache poisoningが検出されたとは言いません．今回のsnapshotには第三者Actionの実装を取り込んでいないため，workflow YAMLだけの解析範囲も明示します．対策版はpublish jobを分離したコミットであり，全警告が消える版ではありません．事件当時のcache内容，権限設定，API tokenは再現していません．

attestは[公開された脆弱性](https://securitylab.github.com/advisories/GHSL-2026-225_actions_attest/)であり，実際の悪用は確認していません．Ultralyticsは[PyPI運営が侵害を報告した事例](https://blog.pypi.org/posts/2024-12-11-ultralytics-attack-analysis/)です．この区別は各manifestにも保存しています．

attestでは両フロントエンドの比較対象構造が一致しました．UltralyticsのCI workflowでは，YAML readerがrunnerのmatrix式をそのまま保持する一方，CodeQLは候補runner labelを列挙するという差があります．この差も一括評価summaryへ残し，意味解釈まで同等とはしていません．

原本YAMLは `experiments/public-cases/`，全警告のSARIFは `results/*codeql*.sarif`，自動生成結果は `results/attest-auto/` と `results/ultralytics-auto/` に保存しました．原本はSHA-256で照合しています．

attestの局所再現は，原本consumerのrun本文に対して，download/checkoutを入力準備へ置き換え，式を管理した値へ展開し，gitを記録専用スタブへ置換しています．ネットワーク無効のDocker内で実行し，実際のrepository書込みはしません．結果は `results/attest-local-replay.json` です．実際のGitHub branch protectionや外部PR受付を突破した実験ではありません．

## モデル検査について分かったこと

既存の7段階・固定事実モデルは，未知値の取り得る組合せを考慮しても，直接スクリプトで同じ判定ができます．旧A1～A5と新しいattest 2版の計7モデルでNuSMVと一致しました．結果は `results/static-model-baseline-2026-10-02.json` です．

新しい並行モデルは，**書換え可能な共有ファイル**を検査した後にwriterが置き換える順序を扱います．GitHubのimmutable artifactを上書きできるというモデルではありません．物理的に同じ共有ファイルを使うことは実験の明示的な仮定で，複数YAMLが存在することだけから推定していません．

`restore → verify → replace → use` の順序で違反に到達します．同じsnapshotを検査・利用する構成と，利用時に再検査する構成では，このモデル上の違反はありません．1～3 object・3方式の計9条件でNuSMVと独立BFSの判定が一致し，mutable方式の反例は実際の無害なファイル操作でも再生できました．最大で1,662状態の探索なので，大規模性はまだ主張できません．

この実験で言えるのは「検査をしたという固定フラグだけでは，検査後の置換を表現できない」です．BFSも反例を出せるため，「NuSMVでなければ不可能」とは言えません．今後の比較軸は，性質の記述，状態管理，反例の説明，モデル変更の負担と規模です．

## 確認した範囲と次の研究課題

51件のunit test，JSON Schema・根拠の検査，attest両版のCodeQL/YAML構造と全文run一致，9条件のNuSMV/BFS一致，7条件の固定事実モデル/baseline一致，原本checksum，局所再現を確認しました．一括公開事例評価CLIも4版に適用しました．[GitHub CI](https://github.com/formal-ci-cd/trust-boundary-verification/actions/runs/36887435413)でも標準Pythonコンテナのbuild，51件のテスト，無害な局所再現が成功しました．CodeQL，zizmor，actionlintのPRチェックも成功しています．NuSMV自体の再実行は，今回のローカル検証で行っています．

次の研究課題は，並行遷移を実workflowと実行観測から生成すること，cacheや独自Actionの意味を広げること，実行観測を自動取得してunknownを減らすこと，そして提案側が説明できて既存検査側が見逃す事例を同一入力条件で見つけることです．今回の実装はその比較を再実行できる基礎であり，修士研究全体の完成ではありません．
