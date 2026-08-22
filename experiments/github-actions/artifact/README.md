# GitHub Actions成果物実験

## 1．目的

未信頼なPull Requestが保存した成果物を，後続の`workflow_run`が取得し，模擬公開判断へ使用する経路を確認する．GHA-A1では内容を検証せずに使用し，GHA-A2ではmain側で信頼したSHA-256 digestと一致する場合だけ使用する．

GitHubの公式文書は，`workflow_run`で起動したworkflowが他のworkflowの成果物を扱う場合，その内容を慎重に扱う必要があるとしている．CodeQLにも成果物汚染のqueryがあるが，主な対象は安全でない展開や特権jobでの未信頼コード実行である．

- [GitHub Actionsの安全な利用](https://docs.github.com/en/actions/reference/security/secure-use)
- [CodeQLの成果物汚染query](https://codeql.github.com/codeql-query-help/actions/actions-artifact-poisoning-critical/)

## 2．構成

| ID | workflow | 役割 |
| --- | --- | --- |
| 共通producer | `artifact-a1-pr-producer.yml` | PR側の`payload.txt`を`trust-boundary-build-output`として保存する． |
| GHA-A1 | `artifact-a1-unsafe-consumer.yml` | 起動元runの成果物を取得し，hashを確認せずに`publish=true`を模擬公開判断へ渡す． |
| GHA-A2 | `artifact-a2-safe-consumer.yml` | 同じ成果物を取得するが，信頼済みdigestと一致する場合だけ模擬公開判断へ渡す． |

producerとconsumerは同じ成果物名を使用する．consumerの`run-id`には`github.event.workflow_run.id`を指定するため，単に同名の成果物を探すのではなく，consumerを起動したproducer runの成果物を取得する．

## 3．模擬入力

通常の入力は次の内容である．

```text
publish=false
```

危険側の確認では，新しいPull Requestで次の内容へ変更する．

```text
publish=true
```

この文字列は本物の公開処理を起動しない．GHA-A1は`dummy_publish_authority_reached=true`をstep summaryへ記録するだけである．

## 4．実行順序

1. 3本の実験workflowをdefault branchへ反映する．
2. `.research-artifact-input/payload.txt`を`publish=true`へ変更するPull Requestを作成する．
3. producerのrun ID，head SHA，artifact ID及びartifact digestを記録する．
4. GHA-A1及びGHA-A2のstep summaryを確認する．
5. GHA-A1では模擬権限へ到達し，GHA-A2ではdigest不一致によって遮断されることを確認する．
6. run IDと結果を`model/observations/`及び`results/`へ記録する．

## 5．安全上の制約

- GitHub-hosted runnerだけを使用する．
- consumerの権限は`actions: read`及び`contents: read`へ制限する．
- 標準の短期`GITHUB_TOKEN`は起動元runの成果物取得だけに使用し，repositoryへの書込みには使用しない．
- repository secret，OIDC，package公開及びdeployは使用しない．
- PR由来のscriptを実行せず，決められた文字列を模擬判断へ渡すだけにする．

## 6．現時点の状態

workflow，CodeQLから共通モデルへの変換，NuSMVモデル及び反例の対応付けまでは作成済みである．GitHub上の複数runを使った実測は，workflowをdefault branchへ反映した後に行う．したがって，現時点の実行結果は`planned`であり，実証済みではない．
