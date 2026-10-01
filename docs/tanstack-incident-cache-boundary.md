# 実侵害TanStack：fork PRのcacheが公開jobへ届いた経路

## 検証結果

[TanStackの事後報告](https://old.tanstack.com/blog/npm-supply-chain-compromise-postmortem)によると，2026年5月11日にfork PR上のコードが `pull_request_target` のjobで実行され，mainのcache scopeへ汚染したpnpm storeが保存されました．報告にはcache key，保存時刻，後で復元したrelease runが記録されています．二つのrelease runでは，宣言された `Publish Packages` stepは失敗したテストのためskipされましたが，実行された悪性コードがOIDCを使って直接npmへ公開し，42パッケージの計84版が影響を受けました．[GitHubの公式アドバイザリ](https://github.com/TanStack/router/security/advisories/GHSA-g7cv-rxg3-hmpx)も，fork PR，cache，OIDCの連鎖を記録しています．

この事例を，事件前設定と同日の対策版で比較しました．限定したYAML解析は，事件前に `bundle-size.yml` の `benchmark-pr` から別repositoryのSetup Actionを経て `release.yml` の `id-token: write` jobへ届く**条件付き経路**を生成し，対策版ではfork PRからmainのcacheへ書く経路を除外しました．NuSMVの `AG !bad` と独立BFSは両版で一致しました．実際のcache保存・復元と悪性公開は事後報告から得た検証用の証拠であり，検出器の入力には与えていません．

## 原本と自動解析の範囲

`experiments/public-cases/tanstack/manifest.json` は，事件前の `TanStack/router` コミット `b1c061aff9185cdf5fdc08c0136382a9dce0302f` と，対策後の `5d92d5aea3020e9db90b872a8c394c6fd0847c6d` から，各7 workflowとLICENSEを保存します．別repository `TanStack/config` の `.github/setup/action.yml` は，事件前の公開履歴上で最新だったmainコミット `1ba8c18d3eb01c208a59fc27c3076c9d10bdc9a8` の定義です．Actionファイルの最後の変更は `d8cef73c4209d91ca578274ce6f83770f64752d3` で，両コミットの内容が同じことを確認しました．ただし `@main` は可変参照で，事件当日の実行ログによる厳密な解決SHAは未確認です．全原本をSHA-256で照合します．攻撃payloadや汚染cacheの中身は保存していません．

`tools/tanstack_cache_chain.py` は，manifestで対象として指定した `bundle-size.yml` と `release.yml` から，イベント・job条件・PR merge refへのcheckout・外部Action呼出し・Action内のcache key式とinstall・公開jobのOIDC権限と後続コマンドを読みます．リスクの有無をmanifestで注釈することはありませんが，**対象workflowの選択と外部Actionの原本取得には人手が残ります**．任意のActionやシェルの意味解析を行う汎用検出器ではありません．

| 段階 | YAMLから得られる事実 | YAMLだけでは不明なこと |
|---|---|---|
| PR側 | `pull_request_target` がforkのmerge refをcheckoutし，Setup Action後にPRのコードを実行する | 当日のAction解決SHAと悪性コードの具体的動作 |
| cache | 両workflowは同じ外部Actionを呼び，同じ `runner.os` / `hashFiles('**/pnpm-lock.yaml')` key式を使う | 実効keyの一致，保存成功，復元されたcacheの内容 |
| 公開側 | mainへのpushで起動する `release.yml` は `id-token: write` を持ち，同Actionでcacheを復元した後にテスト等を行う | 汚染されたコードの実行とOIDC token取得 |

事件前の `pull_request_target` はmainのcache scopeを使いました．対策コミットは該当workflowを `pull_request` に変えています．forkの `pull_request` cacheは[PRのmerge refに限定](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching)され，mainの公開workflowへ保存できません．現在のGitHub Actionsでは，さらに[2026年6月の低信頼イベント向けcache書込み制限](https://github.blog/changelog/2026-06-26-read-only-actions-cache-for-untrusted-triggers/)が導入されています．ここでの判定は**事件当時の2026年5月の仕様**に限定し，現在の実行可否へそのまま一般化しません．

## CodeQL・zizmorとの同一入力比較

本体の7 workflowと外部Setup Actionを，内容を変えず一つの解析専用ディレクトリへ配置しました．CodeQL CLI 2.27.1 / `codeql/actions-queries@0.6.36` は両版ともActionsファイル8件中8件を抽出しました．zizmor 1.30.1は `--offline --no-config --no-ignores --persona=regular` で検査しました．これは**固定した現在のツール版による過去の設定の再評価**であり，2026年5月当日に各ツールが導入・実行されていた証拠ではありません．全警告は `results/tanstack-cache-chain/` に無損失圧縮して保存しています．

| ツール・評価 | 事件前 | 対策後 | 対象の読み取り |
|---|---:|---:|---|
| CodeQL default | 1件 | 1件 | 両版とも `bundle-size.yml:45` のcache poisoning警告 |
| CodeQL security-and-quality | 14件 | 14件 | 両版とも対象workflowの同じcache警告とuntrusted checkout警告 |
| zizmor regular | 50件 | 49件 | 事件前の `pull_request_target` に危険なtrigger警告．対象workflowへのcache poisoning警告は両版とも0件 |
| 提案側の対象経路 | 条件付き反例あり | 対象経路なし | fork PRからmainのcacheを介してOIDC jobへ届く性質を区別 |

CodeQLは事件前の危険な入口を**検出しています**．「CodeQLが見逃した実侵害」とは主張しません．一方で，対策後も同一のcache警告が残り，対象とした**fork→main cache→OIDC job**という性質について両版を区別しません．zizmorは危険なtriggerの有無を区別しますが，対象のcache経路自体は警告に含めていません．これが本事例で示せた追加の説明力です．CodeQLの警告は別の性質について有用な場合があるため，対策後の警告全体を誤検知とは扱いません．

## 反例と実事件の照合

提案モデルは「cache保存成功」「実効key一致」「公開側で汚染entry復元」「汚染コード実行」をunknownのまま探索します．事件前は四条件が成立する実行で `AG !bad` が偽となり，対策後はPRのcache scopeが分離され `AG !bad` が真です．各96状態の独立BFSとも一致しました．事後報告にある `Linux-pnpm-store-6f9233a50def742c09fde54f56553d6b449a535adf87d4083690539f49ae4da11` の11:29 UTC保存，二つのrelease runでの復元と悪性公開は，事件前反例の外部条件を裏付けます．詳細は `incident-observation.json` に**検出入力とは別に**記録しました．

この事例は，複数workflowと外部Actionにまたがる信頼境界を，守りたい性質と反例として説明できることを示します．BFSも同じ判定を出すため，NuSMVという製品固有の必須性や大規模性はまだ示していません．実runner上で攻撃を再実行した結果でもありません．

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
```

比較用のSARIFとそのchecksum・警告内訳は `results/tanstack-cache-chain/evidence-index.json` に記録しています．既存ツールへ渡した入力は原本workflow7件と外部Action定義1件を同じバイト列で解析専用ディレクトリに配置したものです．
