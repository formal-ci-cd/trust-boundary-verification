# 実侵害TanStack：fork PRのcacheが公開jobへ届いた経路

## 検証結果

[TanStackの事後報告](https://old.tanstack.com/blog/npm-supply-chain-compromise-postmortem)によると，2026年5月11日にfork PR上のコードが `pull_request_target` のjobで実行され，mainのcache scopeへ汚染したpnpm storeが保存されました．報告にはcache key，保存時刻，後で復元したrelease runが記録されています．二つのrelease runでは，宣言された `Publish Packages` stepは失敗したテストのためskipされましたが，実行された悪性コードがOIDCを使って直接npmへ公開し，42パッケージの計84版が影響を受けました．[GitHubの公式アドバイザリ](https://github.com/TanStack/router/security/advisories/GHSA-g7cv-rxg3-hmpx)も，fork PR，cache，OIDCの連鎖を記録しています．

この事例を，事件前設定と同日の対策版で比較しました．限定したYAML解析は，事件前に `bundle-size.yml` の `benchmark-pr` から別repositoryのSetup Actionを経て `release.yml` の `id-token: write` jobへ届く**条件付き経路**を生成し，対策版ではfork PRからmainのcacheへ書く経路を除外しました．NuSMVの `AG !bad` と独立BFSは両版で一致しました．実際のcache保存・復元と悪性公開は事後報告から得た検証用の証拠であり，検出器の入力には与えていません．

## 原本と自動解析の範囲

`experiments/public-cases/tanstack/manifest.json` は，事件前の `TanStack/router` コミット `b1c061aff9185cdf5fdc08c0136382a9dce0302f` と，対策後の `5d92d5aea3020e9db90b872a8c394c6fd0847c6d` から，各7 workflowとLICENSEを保存します．別repository `TanStack/config` の `.github/setup/action.yml` は，事件前の公開履歴上で最新だったmainコミット `1ba8c18d3eb01c208a59fc27c3076c9d10bdc9a8` の定義です．Actionファイルの最後の変更は `d8cef73c4209d91ca578274ce6f83770f64752d3` で，両コミットの内容が同じことを確認しました．ただし `@main` は可変参照で，事件当日の実行ログによる厳密な解決SHAは未確認です．全原本をSHA-256で照合します．攻撃payloadや汚染cacheの中身は保存していません．

`tools/tanstack_cache_chain.py` は，両版の**全7 workflow**を走査し，イベント・job条件・PR merge refへのcheckout・外部Action呼出し・Action内のcache key式とinstall・公開jobのOIDC権限と後続コマンドから入口と出口を見つけます．manifestから対象workflow名の指定を削除し，名前を変えた対照例でも入口を発見することを確認しました．ただし，**外部Actionの原本取得と参照先の対応付けには人手が残ります**．任意のActionやシェルの意味解析を行う汎用検出器ではありません．

| 段階 | YAMLから得られる事実 | YAMLだけでは不明なこと |
|---|---|---|
| PR側 | `pull_request_target` がforkのmerge refをcheckoutし，Setup Action後にPRのコードを実行する | 当日のAction解決SHAと悪性コードの具体的動作 |
| cache | 両workflowは同じ外部Actionを呼び，同じ `runner.os` / `hashFiles('**/pnpm-lock.yaml')` key式を使う | 実効keyの一致，保存成功，復元されたcacheの内容 |
| 公開側 | mainへのpushで起動する `release.yml` は `id-token: write` を持ち，同Actionでcacheを復元した後にテスト等を行う | 汚染されたコードの実行とOIDC token取得 |

事件前の `pull_request_target` はmainのcache scopeを使いました．対策コミットは該当workflowを `pull_request` に変えています．forkの `pull_request` cacheは[PRのmerge refに限定](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching)され，mainの公開workflowへ保存できません．現在のGitHub Actionsでは，さらに[2026年6月の低信頼イベント向けcache書込み制限](https://github.blog/changelog/2026-06-26-read-only-actions-cache-for-untrusted-triggers/)が導入されています．ここでの判定は**事件当時の2026年5月の仕様**に限定し，現在の実行可否へそのまま一般化しません．

## CodeQL・zizmorとの同一入力比較

本体の7 workflowと外部Setup Actionを，内容を変えず一つの解析専用ディレクトリへ配置しました．**事件当時に公開済みだったCodeQL** CLI 2.25.4（2026年5月7日公開）/ `codeql/actions-queries@0.6.27` をLinux x64の隔離環境で実行し，3入力ともActionsファイル8件中8件を抽出しました．事件前原本では `bundle-size.yml:45` に `actions/cache-poisoning/poisonable-step` が1件あり，警告の起点は**実際の攻撃イベント `pull_request_target`**です．手動起動イベント1行を外した合成対照でも同じ起点で1件，対策版では0件でした．[当時の全SARIFとchecksum](../results/tanstack-cache-chain/historical-codeql-2026-05/evidence-index.json)を保存しています．

| 入力 | 当時のCodeQL通常suite | 提案側の対象経路 |
|---|---|---|
| 事件前原本 | 1件，`pull_request_target` 起点 | 条件付き反例あり |
| 手動起動1行削除の合成対照 | 1件，`pull_request_target` 起点 | 同じ条件付き反例あり |
| 対策版 | 0件 | 対象経路なし |

**したがってTanStackを「CodeQLが実際の攻撃入口を見逃し，提案手法だけが検出した事例」とすることはできません．** 提案モデルが追加するのは，CodeQLが警告した入口と，別workflow・別runのcache復元およびOIDC権限を一つの条件付き経路としてつなぐ説明です．

この差を[再計算可能な比較表](../results/tanstack-cache-chain/property-level-comparison.json)にしました．[比較スクリプト](../tools/compare_tanstack_outputs.py)は各SARIF resultの主位置・関連位置・code flowにあるファイルを数え，入口 `bundle-size.yml` と公開側 `release.yml` を**同一の結果が指すか**を検査します．事件前の当時版CodeQL通常suiteは「入口を指す結果1件，公開側0件，同一結果0件」です．現在版CodeQLの広いsuiteは「入口4件，公開側1件，同一結果0件」，zizmor regularは「8件，4件，0件」，sisakulintは「8件，4件，0件」です．提案側の条件付き反例は入口・外部Setup Action・公開側の3ファイルを根拠に持ちます．対策後は提案側の対象反例がなく，当時版CodeQLの入口警告も0件です．

**同一SARIF結果0件は，保存した版・入力・suiteの出力に関する観測**です．各ファイルを指す警告の件数は位置だけの集計で，それぞれが対象のcache経路を警告した件数ではありません．複数の警告を人が総合して危険を推測できないことや，既存ツールに独自query・ruleを追加して同じ性質を検査できないことを意味しません．また，提案側の反例も実行時のcache同一性などを未知とした条件付き判定です．この比較は「入口を発見したか」と「別workflowの権限まで経路として提示したか」を別の評価項目にします．

固定した現在のCodeQL CLI 2.27.1 / `actions-queries@0.6.36` でも同じ原本を再評価しました．事件前・対策版とも通常suiteは1件で，警告の起点は対象jobの条件が許さない `workflow_dispatch` でした．1行対照では0件です．広いsuiteは順に14・14・13件で，入口workflowの別種の警告は残ります．これは**現在の解析器・query packによる過去設定の再評価**です．当時のCodeQLが攻撃入口を見逃した証拠として扱いません．[GitHubは2026年6月に低信頼イベントのcache書込みを制限](https://github.blog/changelog/2026-06-26-read-only-actions-cache-for-untrusted-triggers/)し，[CodeQLも同年8月に関連queryを変更](https://github.blog/changelog/2026-08-19-codeql-2-26-3-improves-github-actions-queries-and-javascript-modeling/)しています．古いquery packだけを新しいCLIのdatabaseに適用しても手動起動由来の警告となったため，CLIに含まれる抽出器とquery packを**当時の組合せで揃える必要**がありました．変更理由をその一要因だけへ断定しません．

zizmor 1.30.1 regularは事件前50件，対策後49件で，事件前の `pull_request_target` に危険なtrigger警告を出します．[Poutine 1.1.6](additional-poutine-baseline.md)は両版とも15件で外部Action参照等を指摘します．[sisakulint 0.3.7](additional-sisakulint-baseline.md)も事件前の未信頼checkoutを警告し，対策後にはその警告が消えました（全警告49件・48件）．これらのツールは攻撃入口を検出しますが，保存された出力には対象の**fork PR→main cache→OIDC job**を一つの経路として接続する警告はありません．zizmorの全SARIFと現在のCodeQLの全SARIFは[比較索引](../results/tanstack-cache-chain/evidence-index.json)に，sisakulintの全SARIFは[追加比較索引](../results/additional-sisakulint-baseline/tanstack-evidence-index.json)に保存しました．Poutineとsisakulintのこの版は事件後の公開版なので遡及的な比較です．

### 手動起動イベント1行の合成対照と検出器の感度

事件前の全7 workflowと外部Actionを保ち，`bundle-size.yml` の `workflow_dispatch:` **1行だけ**を削除した対照例を生成しました．これはupstreamの版でも実際の事件観測でもありません．PRの `pull_request_target`，`benchmark-pr` jobの条件，PR merge refへのcheckout，cacheを使う外部Action，公開側のOIDC jobはすべて不変です．[生成器](../tools/materialize_tanstack_combined.py)の `--without-dispatch-control` で再作成でき，[差分](../results/tanstack-cache-chain/without-workflow-dispatch-control/source.patch)も保存しています．現在のCodeQL通常suiteは原本1件・対照0件ですが，当時のCodeQL通常suiteは両方1件でPR起点を指します．[現在の対照結果](../results/tanstack-cache-chain/without-workflow-dispatch-control/comparison.json)は，その版に限った結果として残します．

提案モデルでは原本と1行対照の両方で，`pull_request_target`→main cache→OIDC jobの**同じ条件付き反例**が残ります．SMVのSHA-256も一致し，NuSMVとBFSは `AG !bad` が偽で一致しました．検出器側の感度も，原本から一要素ずつ変えた4つの合成対照で確認しました．`benchmark-pr` のjob条件を手動起動だけにする，PR merge refではなくmainをcheckoutする，公開jobから `id-token: write` を外す，外部Setup Actionからcache stepを外す，の各条件では対象経路を生成しません．これは**fork PRコードからOIDC jobへの当該経路**についての結果であり，他の資格情報や攻撃経路まで安全とする判定ではありません．未対応のjob条件式は保守的に経路候補として残します．事件前・対策後の原本から再生成したSMVは保存済み結果とbyte単位で一致しました．

## 反例と実事件の照合

提案モデルは「cache保存成功」「実効key一致」「公開側で汚染entry復元」「汚染コード実行」をunknownのまま探索します．事件前は四条件が成立する実行で `AG !bad` が偽となり，対策後はPRのcache scopeが分離され `AG !bad` が真です．各96状態の独立BFSとも一致しました．事後報告にある `Linux-pnpm-store-6f9233a50def742c09fde54f56553d6b449a535adf87d4083690539f49ae4da11` の11:29 UTC保存，二つのrelease runでの復元と悪性公開は，事件前反例の外部条件を裏付けます．詳細は `incident-observation.json` に**検出入力とは別に**記録しました．

GitHubの公開[job 75429692202](https://github.com/TanStack/router/actions/runs/25613093674/attempts/4)と[job 75430579447](https://github.com/TanStack/router/actions/runs/25691781302/attempts/1)のREST APIメタデータも別途取得しました．両方で `Setup Tools` は成功，`Run Tests` は失敗，通常の `Publish Packages` はskipでした．これは事後報告のstep到達順序を独立に裏付けますが，**通常の公開stepを通らずに悪性公開が起きたこと自体の証明は事後報告に依拠**します．公開[jobメタデータの保存結果](../results/tanstack-cache-chain/public-run-metadata.json)には各stepの結果と時刻を残しています．詳細ログのAPIは2026年10月2日時点でHTTP 410となり，cache entryの実体・復元ログ・外部Actionの実行時SHAはこの追加調査では確認できませんでした．

この事例は，複数workflowと外部Actionにまたがる信頼境界を，守りたい性質と反例として説明できることを示します．BFSも同じ判定を出すため，NuSMVという製品固有の必須性や大規模性はまだ示していません．実runner上で攻撃を再実行した結果でもありません．

### 二つのrunの順序を独立に探索する追加モデル

従来のモデルは「PR側の保存→release側の復元」を固定順序としていました．追加した [`tanstack_interleaving.py`](../tools/tanstack_interleaving.py) は，同じ固定YAMLから抽出したproducer・consumerとcache scopeを使い，**一つのPR runと一つのrelease runを別々に進める**NuSMVモデルを生成します．PR側はcheckout・Setup・build・終了時のcache保存，release側はcache復元・コード利用の進行点を持ちます．保存成功を含む汚染entryの作成，実効key一致，復元成功，汚染コード実行は未知の真偽値のままです．初期cacheは清浄で，cacheの削除や別runからの書込みは扱いません．

[生成モデルとNuSMV/BFSの結果](../results/tanstack-cache-chain/interleaving/evidence-index.json)では，事件前原本で244状態を探索し `AG !bad` に反例，対策版で240状態を探索し反例なしとなりました．独立BFSとNuSMVの判定は一致します．四つの未知条件をすべて成立させても，**PR側が保存してからrelease側が復元すると到達**し，**release側が先に復元するとそのrunでは到達しません**．前者が事後報告で記録された保存・復元の順序と整合しますが，モデルが実行時刻やcache実体を発見したわけではありません．これにより，単に「両workflowに同じkey式がある」という固定条件だけでは結果を決められず，run間の順序を表す必要があることを示しました．一方，探索スクリプトでも同じ結果を出せるため，NuSMVの必須性・性能優位・大規模workflowへの一般化は依然として主張しません．

### 公開側2件を独立に進める追加検査

[TanStackの事後報告](https://tanstack.com/blog/npm-supply-chain-compromise-postmortem)には，汚染cacheの保存後に復元した公開側runが2件記録されている．そこで，検出器が実YAMLから得たproducer・consumerの役割を保ち，公開側だけを2インスタンスに増やした[有界モデル](../tools/tanstack_two_consumers.py)を生成した．各公開側runの実効key一致・復元成功・汚染コード実行を**別々の未知値**とし，PR側の保存成否は共有する．実際のrun時刻やcache内容を判定入力には渡していない．

| 固定した全成功条件での順序 | 事件前モデルの到達 | 対策版モデルの到達 |
|---|---|---|
| PR側の保存→両公開側の復元 | 1件目・2件目とも到達 | ともになし |
| 1件目の復元→PR側の保存→2件目の復元 | 2件目のみ到達 | ともになし |
| 両公開側の復元→PR側の保存 | ともになし | ともになし |

[モデル・反例・全状態探索の結果](../results/tanstack-cache-chain/two-consumers/analysis-native.json)では，事件前はNuSMVの `AG !(bad1 | bad2)` が偽，対策版は真で，独立BFSと一致した．到達状態はそれぞれ5,968・5,760状態である．事後報告の保存時刻11:29 UTCと二つの復元runは1行目の順序に整合する．ただし，2件をモデル化しても各runの実cache entryや悪性コードの実行をモデルが観測したわけではなく，個別runの実行結果の立証は事後報告と公開jobメタデータに依拠する．この追加検査は**複数runを別々の状態として扱う必要性**を具体化するが，BFSも同じ判定を出すためNuSMV自体の不可欠性は示さない．

観測記録を検出入力から分けたまま，`tools/verify_tanstack_observation_alignment.py` で[事後報告と公開stepメタデータの照合](../results/tanstack-cache-chain/two-consumers/observation-alignment.json)も自動化した．報告されたcache保存は11:29 UTC，公開stepメタデータの `Setup Tools` 開始は二つのrunで19:16:20 UTCと19:21:17 UTCで，両runとも保存後に始まった．同stepは両方成功し，`Run Tests` は失敗，通常の `Publish Packages` はskipだった．これは**時系列とstep結果がモデルの条件付き実行に整合する**ことを示す．stepメタデータだけでは，実際にどのcache entryを復元したか，汚染byteを実行したか，OIDCを使って直接公開したかは独立に確認できない．

## 再実行

原本を実行せず，ネットワーク無効の解析用Dockerでモデルを生成します．NuSMV 2.7.0の確認は生成物に対してホストで行います．

```sh
mkdir -p /tmp/tanstack-analysis
docker run --rm --network none -v "$PWD:/repo:ro" \
  -v /tmp/tanstack-analysis:/analysis -w /repo trust-boundary-analysis \
  python3 tools/tanstack_cache_chain.py experiments/public-cases/tanstack \
  --output /analysis
python3 tools/verify_tanstack_models.py /tmp/tanstack-analysis \
  --nusmv /path/to/NuSMV

mkdir -p /tmp/tanstack-interleaving
docker run --rm --network none -v "$PWD:/repo:ro" \
  -v /tmp/tanstack-interleaving:/analysis -w /repo trust-boundary-analysis \
  python3 tools/tanstack_interleaving.py experiments/public-cases/tanstack \
  --output /analysis
python3 tools/verify_tanstack_interleaving.py /tmp/tanstack-interleaving \
  --nusmv /path/to/NuSMV

mkdir -p /tmp/tanstack-two-consumers
docker run --rm --network none -v "$PWD:/repo:ro" \
  -v /tmp/tanstack-two-consumers:/analysis -w /repo trust-boundary-analysis \
  python3 tools/tanstack_two_consumers.py experiments/public-cases/tanstack \
  --output /analysis
python3 tools/verify_tanstack_two_consumers.py /tmp/tanstack-two-consumers \
  --nusmv /path/to/NuSMV
python3 tools/verify_tanstack_observation_alignment.py \
  /tmp/tanstack-two-consumers/analysis-native.json \
  experiments/public-cases/tanstack/incident-observation.json \
  results/tanstack-cache-chain/public-run-metadata.json \
  --output /tmp/tanstack-two-consumers/observation-alignment.json
```

比較用のSARIFとそのchecksum・警告内訳は `results/tanstack-cache-chain/evidence-index.json` に記録しています．既存ツールへ渡した入力は原本workflow7件と外部Action定義1件を同じバイト列で解析専用ディレクトリに配置したものです．
