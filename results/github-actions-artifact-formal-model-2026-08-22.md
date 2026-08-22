# GitHub Actions成果物経路の形式検証結果

実施日：2026年8月22日

## 1．モデル生成経路

```text
GitHub Actions workflow
  → CodeQL database
  → WorkflowStructure.qlの表形式出力
  → workflowごとの共通モデル
  → producerとconsumerを同一成果物で結合
  → NuSMVモデル
  → workflow，job，step付きの反例表
```

producerとconsumerの結合では，成果物名だけでなく，consumerが`github.event.workflow_run.id`を`run-id`へ指定していることを同一性の根拠に含めた．初回のproducer run `32575878081`で保存成功を確認し，PR #7ではproducer run `32576681421`とartifact ID `9476722196`を両consumerの取得logと対応付けた．

## 2．GHA-A1

GHA-A1は成果物をhash確認なしで模擬公開判断へ使用する．producer run `32576681421`とconsumer run `32576693376`で保存及び取得の成功を確認したため，`writeAuthorized`，`writeSucceeded`及び`readSucceeded`は`true`とした．

NuSMVの結果は次のとおりである．

```text
-- specification AG !(stage = authority_reached & object_tainted) is false
```

反例は次の順序になった．

```text
未信頼PRの成果物保存
  → 同じ成果物をworkflow_runが取得
  → 内容を完全性確認なしで利用
  → publish=trueによって模擬公開権限へ到達
```

この反例は[workflow，job，stepへの対応表](gha-a1-nusmv-trace.md)へ自動変換した．PR #7の実行では，反例と同じ保存，復元，利用及び模擬公開権限到達を確認した．

## 3．GHA-A2

GHA-A2は同じ成果物を取得するが，信頼済みdigestとの比較に成功した内容だけを利用する．NuSMVの結果は次のとおりである．

```text
-- specification AG !(stage = authority_reached & object_tainted) is true
```

安全側では，`object_restored`から完全性確認へ進み，digestが一致しなければ`blocked`となる．PR #7ではdigest不一致を確認し，利用stepはskippedとなった．一致した場合だけ`object_tainted`を解除してから利用するため，未信頼な状態の成果物による模擬公開権限への到達は成立しない．

## 4．静的解析との差

CodeQLのbuilt-in queryは両構成を報告せず，zizmorは両方へ同じ`dangerous-triggers`を報告した．形式モデルでは，成果物名とrun IDによるobject同一性，完全性確認，未信頼状態及び模擬権限を明示したため，GHA-A1とGHA-A2を異なる結果として検証できた．

## 5．producerの実行結果

PR #6のproducer run `32575878081`で，次の事実を確認した．詳細は`model/observations/gha-a-producer-run-32575878081.json`へ記録した．

- workflowは`pull_request`で起動し，successで完了した．
- `trust-boundary-build-output`がartifact ID `9476526432`として保存された．
- GitHub APIが返したarchive digestは`sha256:22c2299d2769a8d52659e2b88fa02d5ac4be3db773b2571122f19400ebe49d15`であった．
- 取得した`payload.txt`は`publish=false`であり，fileのSHA-256は`c94960cc45713534adb51efee8152237985399b2963cf364817d8362b2a2dc1b`であった．

この結果は成果物の保存成功を示すが，consumerによる取得や模擬権限到達を示すものではない．

## 6．consumerの実行結果

PR #7で`publish=true`を保存したproducer run `32576681421`に対し，GHA-A1とGHA-A2が起動した．両consumerは同じartifact ID `9476722196`を取得した．GHA-A1は完全性確認なしで利用して模擬公開権限到達を記録し，GHA-A2はdigest不一致によって利用stepをskipした．詳細は[実行結果](github-actions-artifact-runtime-2026-08-22.md)に記録した．
