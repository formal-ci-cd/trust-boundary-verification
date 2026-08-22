# GitHub Actions成果物経路の実行結果

実施日：2026年8月22日

## 1．実験条件

PR #7で，未信頼側から変更できる`.research-artifact-input/payload.txt`を`publish=true`とした．本物のsecret，公開処理及びデプロイ権限は使用せず，`dummy_publish_authority_reached`という文字列だけを模擬権限として記録した．

## 2．同一成果物の確認

producer run `32576681421`は，PRの内容を`trust-boundary-build-output`として保存した．GitHub API及び各consumerのdownload logから，次の識別情報がすべて一致することを確認した．

| 識別情報 | producer | GHA-A1 | GHA-A2 |
| --- | --- | --- | --- |
| producer run ID | `32576681421` | `32576681421` | `32576681421` |
| artifact ID | `9476722196` | `9476722196` | `9476722196` |
| artifact名 | `trust-boundary-build-output` | 同左 | 同左 |
| archive digest | `sha256:7b0e1395c4f12c70302f23d1176778d7c4a924739ad09f2e1c26391493c38a61` | 同左 | 同左 |
| `payload.txt`のSHA-256 | `4c1594ac5a818f3f8dda9dee58fafbc84eb3af633d17efef08e1fbde07078e8d` | 同左 | 同左 |

したがって，両consumerが取得したのは，同名というだけではなく，同じproducer runが保存した同一artifact IDの成果物である．

## 3．危険側GHA-A1

consumer run `32576693376`は成果物を取得し，SHA-256を信頼済み値と比較せずに`publish=true`を読み取った．利用stepはsuccessとなり，logへ次を記録した．

```text
producer_run_id=32576681421
artifact_sha256=4c1594ac5a818f3f8dda9dee58fafbc84eb3af633d17efef08e1fbde07078e8d
integrity_verified=false
dummy_publish_authority_reached=true
```

これにより，未信頼PRの内容が保存，別runでの復元，完全性確認なしの利用を経て，模擬公開権限へ到達する経路が実行時にも成立した．

## 4．安全側GHA-A2

consumer run `32576693203`も同じ成果物を取得した．しかし，信頼済みの`publish=false`のSHA-256と一致しなかったため，利用stepはskippedとなった．拒否記録は次のとおりである．

```text
producer_run_id=32576681421
artifact_sha256=4c1594ac5a818f3f8dda9dee58fafbc84eb3af633d17efef08e1fbde07078e8d
integrity_verified=false
dummy_publish_authority_reached=false
reason=digest_mismatch
```

したがって，同じ未信頼成果物を取得しても，利用前の完全性確認により模擬公開権限への到達を遮断できた．

## 5．静的解析と形式検証との比較

| 手段 | GHA-A1 | GHA-A2 | この実験で確認できた範囲 |
| --- | --- | --- | --- |
| CodeQL built-in query | 新規alertなし | 新規alertなし | PR #7のCodeQL checkは成功したが，後続runの模擬権限到達は報告しなかった． |
| zizmor | `dangerous-triggers` | `dangerous-triggers` | `workflow_run`の利用を両方へ報告するが，digest確認による差は区別しなかった． |
| actionlint | findingなし | findingなし | workflowの構文及び式に問題がないことを確認した． |
| 実行結果 | 模擬権限へ到達 | digest不一致で遮断 | 保存・復元・利用結果と同一artifact IDを確認した． |
| NuSMV | 未信頼状態での権限到達に反例あり | 反例なし | GHA-A1とGHA-A2の差を完全性確認と未信頼状態によって表現した． |

静的解析は危険な構成要素又は実行契機を報告できるが，今回の実行では，同じ成果物が後続runでどこまで到達したか，完全性確認によって遮断されたかまでは示さなかった．形式検証の反例は，この「警告の先」を保存，復元，利用，模擬権限という状態経路として示しており，実行結果とも一致した．

## 6．証拠の区分

- 静的解析による事実：workflowの起動契機，成果物操作，zizmorの`dangerous-triggers`及び各checkの結果．
- 実行による事実：run ID，artifact ID，digest，stepのsuccess又はskipped，模擬権限到達のmarker．
- 形式検証による推論：未確認値を含む構成でも到達可能か，未信頼状態が権限まで維持されるかという状態空間上の判定．

機械可読な実行記録は`model/observations/gha-a-runtime-pr7.json`に保存した．
