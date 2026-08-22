# 実験設計表

## 目的

既存の静的解析ツールと提案する形式モデルが，CI/CD pipelineの信頼境界違反をどこまで区別できるかを比較する．

ここでいう信頼境界違反は，未信頼producerが書き込めるCache又はartifactが，後続のprivileged consumerで完全性確認なしに実行又は利用され，repositoryへの書込み，secretへのアクセス，publish，deployなどの権限に影響する経路である．

## 共通の確認項目

各構成について，次を記録する．

1. CodeQLのdefault query suiteの結果．
2. CodeQLのsecurity-extended query suiteの結果．
3. zizmorの結果．
4. actionlintの結果．
5. 形式モデルによるSafe又はUnsafeの判定．
6. 判定根拠となるproducer，保存object，consumer，権限，完全性検証の有無．

## GitHub Actionsの最小構成

| ID | 対象 | 構成 | 期待する形式判定 | 主な比較点 |
|---|---|---|---|---|
| GHA-C1 | Cache | `pull_request_target`で未信頼入力を取得し，default branch文脈のCacheへ保存を試みる． | 現行GitHubではBlocked | 2026年6月26日以降は低信頼な起動契機のCache tokenがread-onlyとなり，実測でも保存を拒否された． |
| GHA-C2 | Cache | producerとconsumerのCache scope又はkeyを分離する． | Safe | 同じ文字列のkeyだけではobject同一性を決められないこと． |
| GHA-C3 | Cache | Cacheを共有するが，consumerは復元内容を実行又は権限利用に使わない． | Safe | 保存objectへの到達と危険な利用を区別できるか． |
| GHA-C4 | Cache | Cacheを共有するが，consumerはhash又はsignatureを確認してから利用する． | Safe | 完全性検証をcontrolとして扱えるか． |
| GHA-A1 | artifact | PR workflowのartifactを後続の`workflow_run`が取得し，workspaceへ展開して利用する． | Unsafe候補 | artifact poisoning queryとの比較． |
| GHA-A2 | artifact | artifactをtemporary directoryへ取得し，形式及び内容を確認してから限定的に利用する． | Safe | artifactの安全な受渡しを区別できるか． |

`Unsafe候補`は，実在secretやdeploy権限を与えるという意味ではない．研究用のdummy authorityを使い，設定上の到達可能性だけを再現する．

## GitLab CI/CDの比較構成

| ID | 構成 | 期待する形式判定 | 確認する点 |
|---|---|---|---|
| GL-C1 | Docker executorのDynamic Docker volume．protected／non-protectedを分離する． | Safe | 同一keyでもphysical storage namespaceが異なる． |
| GL-C2 | host bind mountでCacheを共有する． | Unsafe候補 | 未信頼producerとconsumerが同じobjectへ到達するか． |
| GL-C3 | host bind mountで共有するが，consumerでhash検証を行う． | Safe | 完全性検証がcontrolになるか． |

## 実施順序

1. GHA-C1及びGHA-C2を作成し，CodeQLのcache poisoning queryを確認する．GHA-C1はCodeQLに検出されたが，実行時のCache保存はGitHub側の権限制御により拒否された．GHA-C2はdefault branch側consumerを追加し，`lookup-only`による実行確認を残している．
2. GHA-A1及びGHA-A2を作成し，CodeQLのartifact poisoning queryを確認する．
3. 同じ構成にzizmor及びactionlintを適用する．
4. `results/`にツールごとの検出結果を表として残す．
5. GitLabのGL-C1からGL-C3と共通の形式モデルで比較する．
