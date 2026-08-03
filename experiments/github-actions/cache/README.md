# GitHub Actions Cache実験

このdirectoryは，GitHub ActionsのCacheを介した信頼境界を調べる最小実験の補助資料を置く．

## GHA-C1

`cache-c1-untrusted-default-context.yml`は，`pull_request_target`でPR headをcheckoutし，PRが変更可能な`.research-cache-input`をCacheへ保存する．

この構成は，未信頼な内容をdefault branch文脈のCacheへ書き込める可能性を持つため，CodeQLの`actions/cache-poisoning/direct-cache`が検出するかを確認する．

## GHA-C2

`cache-c2-pr-isolated.yml`は，通常の`pull_request`で実行し，Cache keyにPR番号を含める．

この構成では，C1のようにdefault branch文脈へCacheを書き込まない．したがって，形式モデルではproducerと特権consumerが同一objectへ到達しないSafe構成として扱う．

## 安全上の制約

- workflowはrepositoryの読取り権限だけを持つ．
- secret，OIDC，package publish，deploy権限は使用しない．
- PRの内容をcheckoutするが，PR由来scriptを実行しない．
- `.research-cache-input/payload.txt`は無害なtext fileである．
