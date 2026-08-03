# 脅威モデル

## 登場要素

| 要素 | 意味 |
|---|---|
| producer | Cache又はartifactを書き込むjob又はworkflow． |
| consumer | 保存されたCache又はartifactを復元又は取得するjob又はworkflow． |
| object | Cache entry又はartifactとして保存されるデータ． |
| untrusted input | 外部Pull Request，Merge Request，又は外部から起動可能なeventに由来する入力． |
| privileged authority | repositoryへの書込み，secret，OIDC，package publish，deployなどの権限． |
| integrity control | hash，signature，schema，許可listなどによる完全性確認． |

## 検証したい性質

次の経路が存在しないことを検証する．

```text
Untrusted producer
  -> writes object
  -> SameCacheObject又はSameArtifactObject
  -> privileged consumer restores or downloads object
  -> consumer executes or uses object without integrity verification
  -> consumer reaches privileged authority
```

形式的には，違反状態を次のように表す．

```text
exists u, o, p, r:
  Untrusted(u)
  and CanWrite(u, o)
  and SameObject(u, p, o)
  and CanRead(p, o)
  and ExecutesOrUses(p, o)
  and not IntegrityVerified(p, o)
  and HasAuthority(p, r)
```

## Cacheにおけるobject同一性

Cache keyが文字列として同じでも，同じCache objectとは限らない．少なくとも次を考慮する．

- Cache key及びrestore key．
- branch又はprotected状態によるscope．
- Cache backend．
- runner executor．
- physical storage namespace．
- 保存及び復元のrun順序．

## artifactにおけるobject同一性

artifactでは，少なくとも次を考慮する．

- artifact name．
- producer workflow及びrun ID．
- artifactを取得するconsumer workflow．
- 取得先directory．
- 展開後に実行又は利用されるfile．
- content validationの有無．

## 本研究での安全判定

次のいずれかが成り立つ場合，対象の経路はSafeとして扱う．

1. producerが未信頼inputを扱わない．
2. producerとconsumerが同じobjectに到達できない．
3. consumerがobjectを実行又は権限利用に使わない．
4. consumerが完全性検証に成功したobjectだけを利用する．
5. consumerが影響対象となるprivileged authorityを持たない．

この定義は今後の実験結果に基づいて更新する．
