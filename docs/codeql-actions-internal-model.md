# CodeQLによるGitHub Actions解析の内部表現

調査日：2026年8月22日

対象：GitHub公式`github/codeql`リポジトリのGitHub Actions向け実装

確認したsource revision：`20f36e01ca899f634dd74abfcc381682daeffeac`

## 1．調査目的

CodeQLがGitHub Actionsのworkflowをどのような内部表現へ変換し，キャッシュ汚染のqueryをどのように判定しているかを確認する．その上で，CodeQLの内部表現を形式検証用モデルの土台として利用できる部分と，別途追加する必要がある部分を整理する．

今回確認した主な公式sourceは次のとおりである．

- [`Ast.qll`](https://github.com/github/codeql/blob/20f36e01ca899f634dd74abfcc381682daeffeac/actions/ql/lib/codeql/actions/Ast.qll)
- [`ast/internal/Ast.qll`](https://github.com/github/codeql/blob/20f36e01ca899f634dd74abfcc381682daeffeac/actions/ql/lib/codeql/actions/ast/internal/Ast.qll)
- [`CachePoisoningQuery.qll`](https://github.com/github/codeql/blob/20f36e01ca899f634dd74abfcc381682daeffeac/actions/ql/lib/codeql/actions/security/CachePoisoningQuery.qll)
- [`CachePoisoningViaDirectCache.ql`](https://github.com/github/codeql/blob/20f36e01ca899f634dd74abfcc381682daeffeac/actions/ql/src/Security/CWE-349/CachePoisoningViaDirectCache.ql)
- [`CachePoisoningViaPoisonableStep.ql`](https://github.com/github/codeql/blob/20f36e01ca899f634dd74abfcc381682daeffeac/actions/ql/src/Security/CWE-349/CachePoisoningViaPoisonableStep.ql)
- [`CachePoisoningViaCodeInjection.ql`](https://github.com/github/codeql/blob/20f36e01ca899f634dd74abfcc381682daeffeac/actions/ql/src/Security/CWE-349/CachePoisoningViaCodeInjection.ql)

## 2．内部表現の構成

### 2.1 YAMLの構文情報

GitHub Actions用extractorは，`.yml`及び`.yaml`を解析対象とし，JavaScript extractorへ処理を転送する設定になっている．抽出後のdatabaseには，YAMLのnode，scalar，mapping，sequence，位置情報などが関係として格納される．

この段階では，まだ「このmappingはjobである」又は「このstepはキャッシュへ書き込む」といったGitHub Actions固有の意味は付いていない．

### 2.2 GitHub Actionsの抽象構文木

`ast/internal/Ast.qll`は，YAMLの配置及びkeyを基に，GitHub Actions固有のclassを構成する．主なclassは次のとおりである．

| class | 表すもの | 主な取得情報 |
| --- | --- | --- |
| `Workflow` | workflow全体 | name，event，job，permission |
| `Event` | `on`に指定された起動契機 | event名，type，branch条件，外部起動可能性 |
| `Job` | workflow内のjob | ID，`needs`，permission，runner，environment |
| `Step` | job内のstep | 前後関係，`if`，環境変数 |
| `UsesStep` | Actionを呼び出すstep | Action名，version，`with`引数 |
| `Run` | shell commandを実行するstep | script及び式 |
| `Expression` | `${{ ... }}`式 | context及びfield参照 |
| `Permissions` | workflow又はjobの権限 | `contents: read`などのscopeと権限 |

`TWorkflowNode`は，YAML documentのtop levelに`jobs` mappingが存在することを条件としている．`TJobNode`及び`TStepNode`も，それぞれ`jobs`及び`steps`内の配置から判定される．したがって，この抽象構文木は一般的なYAML parserの出力に，GitHub Actionsの構造をquery library側で重ねたものである．

### 2.3 stepの実行順序とdata flow

CodeQLは，job内のstepの並びや`needs`，composite Action及びreusable workflowの呼出しを基にcontrol flowを構成する．また，GitHub event context，式，環境変数，step output，job outputなどをnodeとしてdata flow及びtaint trackingを行う．

ただし，全てのキャッシュ汚染queryが同じdata flow解析を使うわけではない．今回確認した3種類のqueryでは，次のように判定方法が異なる．

| query | 主な判定方法 |
| --- | --- |
| `direct-cache` | 未信頼なcheckout又はartifact取得，step順序，保存pathの包含関係を組み合わせる． |
| `poisonable-step` | 未信頼なcheckout又はartifact取得の後に，既知の危険なActionやcommandがあるか確認する． |
| `code-injection` | 外部入力をsource，script式をsinkとするglobal taint trackingを行う． |

## 3．`direct-cache` queryの判定内容

GHA-C1を検出した`actions/cache-poisoning/direct-cache`は，概ね次の条件を組み合わせている．

```text
外部から起動可能なeventである
かつ
default branchのキャッシュへ書き込めるeventである
かつ
未信頼なPull Requestのheadをcheckoutする，又は未信頼なartifactを取得する
かつ
access checkで保護されていない
かつ
job内にキャッシュ書込みstepがある
かつ
キャッシュ保存pathが未信頼側から変更可能なpathと重なる，又はpathが不明である
```

現在の`CacheWritingStep`は，少なくとも`actions/cache`，`actions/cache/save`及びcacheを有効化した`ruby/setup-ruby`を認識する．

ここで重要なのは，このqueryがキャッシュの`key`，`restore-keys`，version又は後続の利用jobを判定条件にしていないことである．また，`path-problem`として画面に表示される経路は，このqueryでは主に`Step.getNextStep()`で接続した同一job内のstep列である．したがって，表示された経路は未信頼な取得処理からキャッシュ保存stepまでの説明であり，異なる実行の利用jobまでを結ぶ反例ではない．

## 4．GHA-C1の結果を再確認して分かったこと

### 4.1 GitHub側の仕様変更

GitHubは2026年6月26日，外部から起動できる低信頼なeventがdefault branch文脈で実行される場合，default branchのキャッシュに対してread-onlyのtokenを発行するよう変更した．現在の公式文書では，default branchのキャッシュを作成又は上書きできる起動契機を次に限定している．

- `push`．
- `workflow_dispatch`．
- `repository_dispatch`．
- `delete`．
- `registry_package`．
- `page_build`．
- `schedule`．

`pull_request_target`，`issue_comment`及び`workflow_run`などは，default branchのキャッシュを復元できるが，新規作成又は上書きはできない．

参考：

- [Read-only Actions cache for untrusted triggers](https://github.blog/changelog/2026-06-26-read-only-actions-cache-for-untrusted-triggers/)
- [Dependency caching reference](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching#cache-access-for-low-trust-workflow-triggers)

### 4.2 CodeQL側の追従

CodeQLはcommit[`761de929ca15540ff4e9c200a0e94a273904f5ad`](https://github.com/github/codeql/commit/761de929ca15540ff4e9c200a0e94a273904f5ad)で，低信頼なeventのread-only cache accessを考慮するようqueryを変更した．commit日は2026年7月30日である．

現在の`hasDefaultBranchCacheWriteAccess`は，eventがdefault branchで実行されるかだけでなく，そのeventにキャッシュ書込み権限があるかを確認する．このため，現在のqueryでは`pull_request_target`を起動契機とするGHA-C1は，`direct-cache`の条件を満たさないと考えられる．

### 4.3 2026年8月3日の実行記録

GHA-C1の実行`30803626815`をGitHub APIから再確認した．job及び`actions/cache/save` stepは`success`であったが，check runのannotationには次の警告が残っていた．

```text
Cache reservation failed: cache write denied: token has no writable scopes
Cache save failed.
```

`actions/cache/save`は保存に失敗してもjobを失敗終了させないため，緑色のcheckだけではキャッシュ保存の成否を判断できない．GHA-C1では，CodeQLが危険な静的構成をHighとして報告した一方，実行時にはGitHub側の権限制御によってキャッシュ書込みが遮断されていた．

8月3日のCodeQL実行がどのversionのquery packを使用したかは，現時点では実行ログから確定できていない．したがって，「CodeQLの旧queryで検出した」と断定するのではなく，「実行時に使用されたqueryはGHA-C1を検出したが，現在の公式sourceでは判定条件が変更されている」と記録する．

## 5．形式検証用モデルへの利用可能性

### 5.1 利用できる可能性が高い部分

- workflow，event，job，stepの構造．
- job間の`needs`及びstepの実行順序．
- Action呼出しと引数．
- GitHub context及び式の参照関係．
- permission，secret access，environmentなどの権限情報．
- composite Action及びreusable workflowの呼出し関係．
- 外部入力からscriptまでのdata flow．
- 未信頼なcheckout，artifact取得，キャッシュ書込みなどのquery固有の意味付け．

これらは，GitHub Actionsのworkflowから形式検証用モデルを作る際のfrontend又は意味解析として再利用できる可能性がある．

### 5.2 そのままでは不足する部分

- キャッシュ保存が実行時に成功したか．
- キャッシュのkey，restore key，version及びscopeから決まるobject同一性．
- 複数のworkflow及び複数回のrunをまたぐキャッシュ状態．
- 保存，復元，上書き，削除及びevictionの時間的な変化．
- 後続の利用jobがどのキャッシュを復元したか．
- 復元内容を実行又は権限利用に使用したか．
- hash又はsignatureによる完全性確認．
- GitHub側の仕様変更及びquery pack versionによる意味の変化．

したがって，CodeQLの内部表現はモデル検査器へそのまま入力できる完成済みの状態遷移モデルではない．一方で，YAMLを解析してworkflowの構造，権限及びdata flowを関係として保持しているため，共通モデルを生成するための入力として利用する価値がある．

## 6．現時点での結論

今回確認した範囲では，CodeQLは次の層に分けてGitHub Actionsを解析している．

```text
YAMLの構文情報
  → GitHub Actions固有の抽象構文木
  → control flow及びdata flow
  → query固有のsource，sink，event及び権限条件
  → alertと説明経路
```

本研究では，このうち抽象構文木，control flow，data flow及び権限推論を参考にできる．その上で，CodeQLの単一snapshot中心の表現に，実行時の共有状態と複数run間の状態遷移を追加する方向が考えられる．

また，GHA-C1は現在のGitHub上ではキャッシュ書込みに成功していなかった．この結果から，形式モデルにはworkflowの記述だけでなく，実行日時におけるplatformの権限制御と，静的解析queryのversionを含める必要がある．

## 7．次に確認する事項

独自queryによる列挙結果は，[CodeQLの内部表現を独自クエリで列挙した結果](../results/codeql-actions-model-v2.26.3.md)に記録した．`Workflow`，`Event`，`Job`，`Step`，Actionの引数及び`Expression`を表として取得できたため，次は次の2点を確認する．

1. 取得した静的な関係を，形式検証用の小さな共通モデルへ変換する．
2. CodeQLから得られない実行時のキャッシュ状態及び複数run間の遷移を，共通モデルへ追加する．
