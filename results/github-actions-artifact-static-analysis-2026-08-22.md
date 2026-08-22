# GitHub Actions成果物実験の静的解析結果

実施日：2026年8月22日

対象：GHA-A producer，GHA-A1，GHA-A2

## 1．確認したツール

| ツール | version又はquery pack | 実行条件 |
| --- | --- | --- |
| CodeQL CLI | `2.26.3` | `actions-code-scanning.qls`をローカルdatabaseへ実行した． |
| `codeql/actions-queries` | `0.6.33` | 成果物汚染queryを含むdefault suiteである． |
| zizmor | `1.29.0` | regular persona，offlineで3本の実験workflowを解析した． |
| actionlint | `1.7.12` | リポジトリ内の全workflowを解析した． |

## 2．CodeQL

default suiteは18件のqueryを実行し，9件のworkflowを全て抽出した．`actions/artifact-poisoning/critical`も実行対象に含まれていたが，今回の3本についてalertは0件であった．

一方，独自の構造抽出queryでは次を取得できた．

- producerの`actions/upload-artifact`，成果物名及び保存path．
- consumerの`actions/download-artifact`，成果物名，取得先及び起動元run ID．
- producerの`pull_request`が外部起動可能かつnot-privilegedであること．
- consumerの`workflow_run`が外部起動可能かつprivilegedであること．
- 安全側にdigest比較と，検証結果によるstep条件分岐が存在すること．

CodeQLの成果物汚染queryは，攻撃者が制御できる成果物の危険な展開やコード実行を主なsinkとして扱う．今回のGHA-A1は一時directoryへ安全に展開した後，file内容を模擬公開の判断値として利用する．このため，単純なfile上書きやコード実行とは異なる業務上の権限判断まで，built-in queryは結び付けなかったと考えられる．

## 3．zizmor

regular personaは，GHA-A1とGHA-A2の両方に次のfindingを出した．

```text
dangerous-triggers: workflow_run is almost always used insecurely
```

zizmorは危険側と安全側のどちらにも同じ`dangerous-triggers`を報告した．今回の確認範囲では，成果物をhash確認せずに模擬公開判断へ渡すGHA-A1と，digestが一致した場合だけ使用するGHA-A2を区別しなかった．

この結果はzizmorの誤りを意味しない．zizmorのfindingは，`workflow_run`という注意が必要な起動契機を広く知らせる役割を持ち，今回検証する成果物の内容，完全性確認及び後続の権限判断を追跡するruleではない．

## 4．actionlint

actionlintの出力は0件であった．すなわち，workflowの構文，式，Action入力及びshellに関する記述上の問題は検出されなかった．actionlintはsecurity専用toolではないため，この結果からGHA-A1が安全であるとは判断できない．

## 5．形式モデルとの比較

| 構成 | CodeQL built-in | zizmor | actionlint | NuSMV |
| --- | --- | --- | --- | --- |
| GHA-A1 | alertなし | `dangerous-triggers` | findingなし | 模擬権限へ到達する反例あり． |
| GHA-A2 | alertなし | `dangerous-triggers` | findingなし | 完全性確認によって反例なし． |

NuSMVではGHA-A1とGHA-A2を区別できたが，保存成功及び取得成功はまだ`unknown`である．GHA-A1の反例は，現在の静的構成で危険な場合を排除できないことを示すものであり，GitHub上で攻撃経路が実際に成立した証拠ではない．

## 6．現時点で分かったこと

1. CodeQLの内部表現から，成果物の保存側と取得側を構成する静的情報を取得できる．
2. built-in security queryは，今回の「未信頼なdataが公開判断を制御する」経路を報告しなかった．
3. zizmorは`workflow_run`の危険性を報告するが，完全性確認の有無による2構成の差は表現しなかった．
4. actionlintは記述の正しさを確認する基準として使えるが，信頼境界の安全性は判定しない．
5. 形式モデルは，静的解析が抽出した操作へobject同一性，実行結果，利用方法及び権限を追加することで，危険側と安全側を異なる検証結果として表現できる．
