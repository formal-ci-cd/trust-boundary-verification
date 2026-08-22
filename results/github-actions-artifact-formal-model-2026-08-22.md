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

producerとconsumerの結合では，成果物名だけでなく，consumerが`github.event.workflow_run.id`を`run-id`へ指定していることを同一性の根拠に含めた．実測後は，artifact ID，producer run ID，取得結果及びdigestも追加する．

## 2．GHA-A1

GHA-A1は成果物をhash確認なしで模擬公開判断へ使用する．保存及び取得の実行結果は未確認であるため，`writeAuthorized`，`writeSucceeded`及び`readSucceeded`を`unknown`として検査した．

NuSMVの結果は次のとおりである．

```text
-- specification AG stage != authority_reached is false
```

反例は次の順序になった．

```text
未信頼PRの成果物保存
  → 同じ成果物をworkflow_runが取得
  → 内容を完全性確認なしで利用
  → publish=trueによって模擬公開権限へ到達
```

この反例は[workflow，job，stepへの対応表](gha-a1-nusmv-trace.md)へ自動変換した．保存及び取得の未確認値が成立する場合の経路であり，実測結果ではない．

## 3．GHA-A2

GHA-A2は同じ成果物を取得するが，信頼済みdigestとの比較に成功した内容だけを利用する．NuSMVの結果は次のとおりである．

```text
-- specification AG stage != authority_reached is true
```

未信頼な内容が完全性確認を通過しない限り，`object_used`から`integrity_checked`へ遷移し，未検証内容による模擬公開権限への到達は成立しない．

## 4．静的解析との差

CodeQLのbuilt-in queryは両構成を報告せず，zizmorは両方へ同じ`dangerous-triggers`を報告した．形式モデルでは，成果物名とrun IDによるobject同一性，完全性確認及び模擬権限を明示したため，GHA-A1とGHA-A2を異なる結果として検証できた．

## 5．残る確認

- producerがPR側の成果物を実際に保存できたか．
- consumerが起動元runの同じartifact IDを実際に取得したか．
- `publish=true`のPRで，GHA-A1だけが模擬権限到達を記録したか．
- GHA-A2がdigest不一致を記録し，利用stepを実行しなかったか．

これらはworkflowをdefault branchへ反映した後，GitHub上のrun IDとlogを使って確認する．
