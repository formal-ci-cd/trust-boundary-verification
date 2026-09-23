# GitHub Actions成果物実験

## 1．目的

未信頼なPull Requestが保存した成果物を，後続の`workflow_run`が取得し，模擬公開判断へ使用する経路を確認する．GHA-A1からGHA-A4は経路の成立条件を比較するための最小構成である．GHA-A5は，`actions/attest`で報告された成果物metadata汚染の脆弱性を安全に簡略化した事例である．

GitHubの公式文書は，`workflow_run`で起動したworkflowが他のworkflowの成果物を扱う場合，その内容を慎重に扱う必要があるとしている．CodeQLにも成果物汚染のqueryがあるが，主な対象は安全でない展開や特権jobでの未信頼コード実行である．

- [GitHub Actionsの安全な利用](https://docs.github.com/en/actions/reference/security/secure-use)
- [CodeQLの成果物汚染query](https://codeql.github.com/codeql-query-help/actions/actions-artifact-poisoning-critical/)

## 2．構成

| ID | workflow | 役割 |
| --- | --- | --- |
| 共通producer | `artifact-a1-pr-producer.yml` | PR側の`payload.txt`を`trust-boundary-build-output`として保存する． |
| GHA-A1 | `artifact-a1-unsafe-consumer.yml` | 起動元runの成果物を取得し，hashを確認せずに`publish=true`を模擬公開判断へ渡す． |
| GHA-A2 | `artifact-a2-safe-consumer.yml` | 同じ成果物を取得するが，信頼済みdigestと一致する場合だけ模擬公開判断へ渡す． |
| GHA-A3 | `artifact-a3-download-only-consumer.yml` | 同じ成果物を取得するが，内容を後続処理へ渡さない． |
| GHA-A4 | `artifact-a4-no-authority-consumer.yml` | 同じ成果物を読むが，公開・更新権限を持つ処理を置かない． |
| GHA-A5 producer | `artifact-a5-attest-producer.yml` | PR側の`dist/`，対象branch及びcommit情報を`gha-a5-rebuilt-dist`として保存する． |
| GHA-A5 consumer | `artifact-a5-attest-consumer.yml` | 成果物内の対象branchと`dist/`を信用し，repository更新相当の処理へ渡す．本物のpushは行わない． |

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
- GHA-A5は実在脆弱性の経路だけを再現し，`contents: write`，checkout，commit及びpushを行わない．

## 6．GHA-A5と実在脆弱性の対応

GitHub Security Labの`GHSL-2026-225`では，`actions/attest`の`pull_request`側が生成した成果物に`dist/`，対象branch及びcommit情報が含まれ，`workflow_run`側がそれを取得してbranchへpushしていた．対象branchが起動元repository及びPull Requestと対応するかを確認していなかったため，PR作成者が指定したbranchへPR由来の`dist/`をpushできる構造であった．

GHA-A5は，次の部分を残して簡略化した．

1. 未信頼PRが`dist/`と対象branchを成果物へ保存する．
2. `workflow_run`が起動元runの成果物を取得する．
3. 対象branchとcommit SHAの形式だけを確認し，起動元repository及びPull Requestとの対応は確認しない．
4. PR由来の`dist/`と対象branchがrepository更新相当の処理へ到達する．

一方，本物のrepositoryを変更しないよう，権限は`contents: read`のままとし，最終地点では`dummy_repository_update_reached=true`を記録するだけにした．したがって，これは攻撃実行ではなく，危険な情報経路の再現である．また，公開情報から悪用の事実は確認できないため，「実事件の再現」ではなく「実在脆弱性の再現」として扱う．

- [GHSL-2026-225: Artifact metadata injection in actions/attest](https://securitylab.github.com/advisories/GHSL-2026-225_actions_attest/)

## 7．現時点の状態

workflow，CodeQLから共通モデルへの変換，NuSMVモデル，反例の対応付け及びGitHub上の実行確認まで完了した．PR #7のproducer run `32576681421`が保存したartifact ID `9476722196`を両consumerが取得した．GHA-A1は完全性確認なしで模擬公開権限へ到達し，GHA-A2はdigest不一致によって利用を遮断した．実行記録は`model/observations/gha-a-runtime-pr7.json`，結果と比較は`results/github-actions-artifact-runtime-2026-08-22.md`に保存した．

その後，CodeQLの抽出結果から保存側と取得側を自動結合する処理を追加した．GHA-A3及びGHA-A4も静的構成として追加し，危険構成1件と安全構成3件をNuSMVで区別できることを確認した．さらに，実在脆弱性を基にGHA-A5を追加し，未信頼な成果物がrepository更新相当の処理へ到達する反例をNuSMVで確認した．GHA-A5は現段階では事例から人手で対応付けた形式モデルであり，CodeQLの再抽出結果からの自動生成は未実施である．GitHub上の実行結果は別途記録する．
