# CI/CD共通モデル

## 1．目的

CodeQLなどの静的解析から得られるworkflowの構造と，CI/CD platformの実行時情報を分離して記録する．この中間形式を，今後作成する形式検証モデルへの入力にする．

## 2．モデルの構成

| 部分 | 内容 | 主な情報源 |
| --- | --- | --- |
| `workflow` | 起動契機，権限，job，step，式 | CodeQL database |
| `sharedStateOperations` | キャッシュ又はartifactに対する操作の記述 | CodeQL database及び独自query |
| `verificationFacts` | object同一性，利用方法，完全性確認，consumerの権限 | 静的解析，実行設定及び追加調査 |
| `runtimeObservations` | platformの権限判定，保存結果，根拠 | GitHub Actionsの実行記録及び公式仕様 |

`cacheWriteIntent`は，workflowにキャッシュ保存操作が記述されていることだけを表す．これだけでは，実行時にキャッシュを書き込めることを意味しない．

成果物については，`artifactWriteIntent`及び`artifactReadIntent`を使用する．`name`，`path`及び`runId`を保持し，異なるworkflowの保存側と取得側を後段で結合する．

## 3．書込みに関する関係

キャッシュ書込みは，次の3段階に分ける．

```text
WriteIntent
  workflowに保存操作が記述されている

WriteAuthorized
  実行時のplatform policy及びtokenが保存を許可する

WriteSucceeded
  対象runで保存処理が成功したことを観測した
```

状態遷移としてキャッシュobjectが生成又は更新されるのは，少なくとも`WriteIntent`と`WriteAuthorized`が成立し，保存処理が成功する場合である．情報が不足する場合は，Safe又はUnsafeへ無理に分類せず`incomplete`として扱う．

`verificationFacts`の各値には`true`，`false`又は`unknown`を使用する．`unknown`を`false`と同一視すると，未調査の経路を存在しないものとして扱い，誤って安全と判定する可能性がある．形式モデルでは，`unknown`を真偽の両方があり得る値として扱う．

## 4．現時点の判定

| 対象 | 静的な保存操作 | 実行時の許可 | 保存結果 | 判定 |
| --- | --- | --- | --- | --- |
| GHA-C1 | あり | 拒否 | 失敗 | `blocked` |
| GHA-C2 | あり | 未確認 | 未確認 | `incomplete` |

GHA-C1では，CodeQLから`cacheWriteIntent`を取得できた一方，実行`30803626815`ではキャッシュ書込みが拒否された．したがって，`WriteIntent`は成立するが，`WriteAuthorized`及び`WriteSucceeded`は成立しない．`blocked`という判定は入力JSONへ記載せず，これらの事実から導く検証結果として扱う．

GHA-C2はPRごとにkeyが分かれていることを静的に確認できる．また，default branch側から同じ形式のkeyを`lookup-only`で検索するconsumer workflowを用意し，`cacheReadIntent`への変換まで確認した．実行時のlookup結果はまだ確認していないため，現段階ではSafeと確定しない．

## 5．変換方法

`tools/codeql_csv_to_model.py`を使用し，CodeQLの表形式出力をJSONへ変換する．GHA-C1では実行時の観測記録も結合する．

```bash
python3 tools/codeql_csv_to_model.py \
  results/codeql-actions-model-v2.26.3.csv \
  --workflow "GHA-C1 Cache write from untrusted PR in default context" \
  --observation model/observations/gha-c1-run-30803626815.json \
  --output model/examples/gha-c1.json
```

GHA-C2は実行時情報が未確認であるため，静的情報だけを変換する．

```bash
python3 tools/codeql_csv_to_model.py \
  results/codeql-actions-model-v2.26.3.csv \
  --workflow "GHA-C2 PR-isolated cache" \
  --output model/examples/gha-c2.json
```

## 6．NuSMVへの変換

共通JSONは`tools/model_to_nusmv.py`でNuSMVモデルへ変換する．`unknown`の事実は`FROZENVAR`となり，実行中は一定のまま，真偽の両方が検査される．

```bash
python3 tools/model_to_nusmv.py \
  model/examples/gha-c1.json \
  --output model/nusmv/gha-c1.smv

NuSMV model/nusmv/gha-c1.smv
```

初回の検証結果と解釈は，[CI/CD共通モデルのNuSMV検証結果](../results/nusmv-common-model-2026-08-22.md)に記録している．

## 7．複数run間の信頼経路

workflowごとの共通モデルは，`tools/build_trust_chain.py`で1本の信頼経路へ結合する．GHA-A1の例は次のとおりである．

```bash
python3 tools/build_trust_chain.py \
  model/chain-inputs/gha-a1.json \
  --output model/chains/gha-a1.json

python3 tools/chain_to_nusmv.py \
  model/chains/gha-a1.json \
  --output model/nusmv/gha-a1.smv
```

`model/trust-chain.schema.json`は，保存側，取得側，共有objectの同一性，実行時の事実及び反例の位置対応を定義する．保存成功などが未確認の場合は`unknown`とし，NuSMVで真偽の両方を検査する．

NuSMVが反例を出した場合は，`tools/explain_nusmv_trace.py`で各状態を元のworkflow，job及びstepへ対応付ける．GHA-A1の結果は[反例対応表](../results/gha-a1-nusmv-trace.md)に保存している．

## 8．成果物実験の現時点の判定

| 対象 | 静的構成 | 実行時の保存・取得 | NuSMV | 判定 |
| --- | --- | --- | --- | --- |
| GHA-A1 | 完全性確認なしで模擬公開判断へ使用する． | 保存成功，取得は未確認 | 反例あり | `incomplete` |
| GHA-A2 | digest一致後だけ模擬公開判断へ使用する． | 保存成功，取得は未確認 | 反例なし | 静的には安全側，実測待ち |

PR #6のproducer run `32575878081`では成果物の保存成功を確認した．GHA-A1の反例は，取得以降の未確認値が成立する場合の経路である．GitHub上で取得及び模擬権限到達を観測するまでは，実証済みの危険構成とは扱わない．
