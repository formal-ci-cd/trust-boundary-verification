# GitHub ActionsにおけるCache object同一性

調査日：2026年8月22日

## 1．目的

producerとconsumerが同じ文字列のkeyを指定した場合に，同じCache objectへ到達できる条件を整理する．`SameCacheObject`はkeyの文字列比較だけでは決めず，GitHub Actionsのcache scope及びcache versionを含めて判定する．

公式仕様：

- [Dependency caching reference](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching)
- [Dependency caching](https://docs.github.com/en/actions/concepts/workflows-and-actions/dependency-caching)

## 2．公式仕様から確認した規則

GitHub ActionsのCacheには，少なくとも次の規則がある．

1. Cache検索では，keyだけでなくcache versionも使用する．cache versionにはpath及び圧縮ツールに関する情報が含まれる．
2. workflow runは，現在のbranch又はdefault branchで作成されたCacheを復元できる．
3. Pull Requestのrunは，base branchのCacheも復元できる．
4. 親branchのrunは，子branchで作成されたCacheを復元できない．sibling branch間でも復元できない．
5. `pull_request`で作成されたCacheは`refs/pull/<number>/merge`にscopeされ，同じPull Requestの再実行からだけ復元できる．base branch及び他のPull Requestからは復元できない．
6. 異なるworkflowでも，同じrepository及びaccess可能なscopeであればCacheを共有できる．
7. 既存Cacheの内容は上書きできず，同じkeyが存在する場合は新しい内容へ置換されない．

## 3．共通モデルで必要な情報

Cache objectの候補は，次の組で表す．

```text
CacheObject =
  repository
  × scope
  × resolved key
  × cache version
  × backend
  × creation order
```

ここで`scope`は，少なくとも次を区別する．

- default branch．
- 通常branch．
- `refs/pull/<number>/merge`．
- tag．
- 未確認．

`SameCacheObject(producer, consumer)`は，次の全てを確認できた場合に`true`とする．

1. 同じrepository及びcache backendである．
2. producerのscopeをconsumerから参照できる．
3. key又はrestore keyの検索結果がproducerのCacheを選択する．
4. cache versionが一致する．
5. producerによる作成がconsumerによる復元より前である．

scope規則によって到達不能と確定した場合は`false`とする．必要な値が不足する場合は`unknown`とし，存在しないものとして扱わない．

## 4．GHA-C2への適用

GHA-C2のproducerは`pull_request`で起動するため，作成されたCacheは`refs/pull/<number>/merge`にscopeされる．追加したconsumerは，`workflow_dispatch`で同じ形式のkeyを指定し，`lookup-only: true`で検索結果だけを取得する．

consumerをdefault branch上で実行した場合，公式仕様上，default branchからPull Requestのmerge refにscopeされたCacheは復元できない．したがって，keyを同じ値へ展開できた場合でも，次の関係になると予想する．

```text
producer scope = refs/pull/<number>/merge
consumer scope = default branch
SameCacheObject = false
```

ただし，`workflow_dispatch`は実行対象のrefを選択できる．実験結果には，入力したPull Request番号，実行時の`GITHUB_REF`，Cache lookup結果及びrun IDを記録する．これらを確認するまでは，C2の実測結果をSafeと確定しない．

## 5．静的解析との関係

CodeQLの独自queryから，producer及びconsumerのAction，keyの式，path及び起動契機を取得できる．一方，式を展開した実際のkey，実行時のref，cache version，Cache検索結果及びrun順序は，今回の静的な表だけでは確定できない．

したがって，本研究では次の分担とする．

```text
CodeQL
  → Action，key式，path，eventなどの静的情報

実行記録及びplatform semantics
  → resolved key，scope，version，lookup結果，run順序

形式モデル
  → SameCacheObjectと権限到達可能性の検証
```
