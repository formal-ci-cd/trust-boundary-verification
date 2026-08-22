# CI/CD共通モデルのNuSMV検証結果

実施日：2026年8月22日

## 1．目的

CodeQLの内部表現と実行時の観測結果を統合した共通JSONからNuSMVモデルを生成し，未信頼な内容が共有状態を介して権限へ到達しないことを検証する．

## 2．検証環境

| 項目 | 内容 |
| --- | --- |
| モデル検査器 | NuSMV 2.7.0 |
| 変換処理 | `tools/model_to_nusmv.py` |
| 入力 | `model/examples/gha-c1.json`及び`gha-c2.json` |
| 出力 | `model/nusmv/gha-c1.smv`及び`gha-c2.smv` |

## 3．状態遷移

最小モデルでは，次の状態遷移を扱う．

```text
start
  → object_written
  → object_restored
  → object_used
  → authority_reached
```

書込みが許可されない，保存に失敗する，同じobjectへ到達しない，又は後続jobが利用しない場合は`blocked`へ遷移する．完全性確認が行われる場合は`integrity_checked`へ遷移し，未検証内容による権限到達を遮断する．

また，保存成功は保存操作と書込み許可の両方を必要とする制約を置き，`WriteSucceeded`だけが単独で成立する不可能な組合せを除外した．

検証したCTL性質は次のとおりである．

```smv
CTLSPEC AG stage != authority_reached
```

これは，全ての到達可能状態で，未検証内容による権限到達状態にならないことを表す．

## 4．GHA-C1

GHA-C1では，次の事実が入力されている．

- キャッシュ保存操作が記述されている．
- 実行時のキャッシュ書込み権限は拒否された．
- 保存処理は失敗した．
- consumerに関する情報は未確認である．

NuSMVの結果は次のとおりである．

```text
-- specification AG stage != authority_reached is true
```

consumerに関する情報が未確認でも，キャッシュobjectが作成される前に書込みが遮断されるため，このrunを起点とする権限到達は成立しない．これはworkflowの構成自体が安全という意味ではなく，2026年8月3日の実行と当時のplatform policyに基づく`blocked`判定である．

## 5．GHA-C2

GHA-C2ではキャッシュ保存操作及びPR番号を含むkeyを取得できたが，保存結果，consumer，同一objectへの到達，完全性確認及び権限は未確認である．これらの`unknown`をNuSMVの`FROZENVAR`として真偽の両方で検査した．

NuSMVの結果は次のとおりである．

```text
-- specification AG stage != authority_reached is false
```

反例では，未確認の値が次のように選択された．

```text
write_authorized = TRUE
write_succeeded = TRUE
same_object = TRUE
consumer_uses_object = TRUE
integrity_verified = FALSE
privileged_consumer = TRUE
has_authority = TRUE
```

その結果，`start → object_written → object_restored → object_used → authority_reached`という経路が示された．これはGHA-C2で実際に攻撃が成立した証拠ではない．現在の入力情報だけでは，危険な場合を排除できず，安全性を証明できないことを示す反例である．したがって，現段階の判定は`incomplete`である．

## 6．分かったこと

1. 静的な保存操作と実行時の書込み可否を分離すると，GHA-C1を`blocked`として表現できる．
2. 未確認値を`false`として扱わず，真偽の両方を検査することで，情報不足による誤った安全判定を避けられる．
3. GHA-C2をSafeと確定するには，後続consumerを用意し，キャッシュscope及びkeyによるobject同一性，復元結果，利用方法，完全性確認及び権限を記録する必要がある．

## 7．現在の制限

- 1つのキャッシュ保存操作と1つのconsumer経路だけを扱う．
- `SameObject`はまだCodeQL出力から自動判定していない．
- 複数workflow及び複数runの時系列を直接生成していない．
- GitHub Actions以外のplatform semanticsをまだ実装していない．

次は，キャッシュを復元するconsumerを実験構成へ追加し，`SameObject`，利用方法及び権限に関する事実を共通モデルへ取り込む．
