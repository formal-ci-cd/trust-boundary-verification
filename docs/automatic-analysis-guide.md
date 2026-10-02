# 自動解析と比較実験の実行方法

研究の現状と限界は [2026-10-02の結果](research-status-2026-10-02.md) を参照してください．以下のコマンドはrepositoryのルートから実行します．入力workflowは解析対象であり，実行しません．

## Python解析

依存関係は `requirements-analysis.txt`，隔離環境は `experiments/analysis.Dockerfile` です．

```sh
docker build -f experiments/analysis.Dockerfile -t trust-boundary-analysis .
docker run --rm --network none -v "$PWD:/repo" -w /repo trust-boundary-analysis \
  python3 -m unittest discover -s tests -v

docker run --rm --network none -v "$PWD:/repo" -w /repo trust-boundary-analysis \
  python3 tools/automatic_artifact_analysis.py \
  experiments/public-cases/actions-attest/vulnerable --output /repo/results/attest-auto/vulnerable
```

`analysis.json` に抽出モデル，対応候補，ルール，元の行，未対応項目，unknownが残ります．候補があれば信頼経路JSONとSMVを生成します．手動の `artifact-chain-annotations.json` は使用しません．旧手順と結果は比較用として残しています．

YAML単独の抽出は `tools/yaml_to_model.py ROOT --output FILE`，CodeQL表との比較は `tools/compare_frontends.py ROOT TABLE.csv --output FILE` です．YAML readerは安全なloaderを使用し，`on`をboolにしません．重複キーを拒否し，run本文を一つのブロックとして保持します．GitHub YAML全仕様の代替検証器ではありません．mergeによる重複overrideや未対応構造は入力エラーになることがあります．式の値・Actionの内部挙動の評価はしません．

## CodeQLと原本の一括比較

Linux環境用CodeQL CLI **2.27.1** を別途用意します．公式配布物のchecksumを確認し，query pack **0.6.36** を使います．CodeQLとそのquery packの利用条件に従ってください．ツールは巨大なCLI本体・databaseをGitに入れません．

以下はLinuxコンテナへCLI，pack cache，出力先をそれぞれmountする例です．`CODEQL_DIR` はCLI本体のディレクトリ，`PACK_CACHE` は専用cache，`EVALUATION_OUT` は空の出力ディレクトリです．

```sh
docker run --rm \
  -v "$PWD:/repo" -w /repo \
  -v "$CODEQL_DIR:/opt/codeql:ro" \
  -v "$PACK_CACHE:/root/.codeql" \
  -v "$EVALUATION_OUT:/evaluation" \
  trust-boundary-analysis \
  python3 tools/evaluate_public_cases.py --codeql /opt/codeql/codeql --output /evaluation
```

初回は公式packの取得にネットワークが必要です．4版のchecksumを確認し，database，defaultとsecurity-and-qualityのSARIF，構造CSV，モデル，summaryを作ります．同じdatabaseを上書きしないため，新しい出力ディレクトリを使います．suiteやバージョンが異なる実行は別の実験として記録します．source snapshotはmanifestに記録したコミットのworkflowのみで，CodeQLによるAction内部の解析範囲も入力に依存します．

## 形式検証と局所再現

生成SMVはNuSMV 2.7.0で検査します．unknownは安全と決め打ちしないため，反例が出ても実行成功や侵害を観測したことにはなりません．

```sh
/path/to/NuSMV results/attest-auto/vulnerable/artifact-*.smv
python3 tools/temporal_verification.py --nusmv /path/to/NuSMV \
  --max-objects 3 --output /tmp/temporal-results

docker run --rm --network none -v "$PWD:/repo" -w /repo trust-boundary-analysis \
  python3 tools/replay_attest_case.py experiments/public-cases/actions-attest \
  --output /repo/results/attest-local-replay.json
```

局所再現はDocker内に限定し，元consumerのrun処理を管理された入力とgitスタブで実行します．並行ファイルモデルの設定・性質は [順序実験](../experiments/temporal/README.md) にあります．

## marimoの承認時刻競合を再実行する

公開原本と一箇所だけ変更した対照版の解析・無害な再現は，ネットワーク無効のDockerで行います．`/tmp/marimo-evaluation`には生成SMV，原本run本文を使ったスタブ再現，独立BFSの結果ができます．`/tmp/marimo-control`にはCodeQL等へ入力する同一ファイル群の対照版ができます．いずれもコンテナごと破棄して構いません．必要な場合は別の空ディレクトリをmountして保存してください．

```sh
docker run --rm --network none -v "$PWD:/repo:ro" -w /repo trust-boundary-analysis \
  python3 tools/evaluate_approval_race.py experiments/public-cases/marimo \
  --output /tmp/marimo-evaluation --replay
```

保存済みの[全警告・反例](../results/marimo-approval-race/evidence-index.json)は，CodeQL CLI 2.27.1，`codeql/actions-queries@0.6.36`，zizmor 1.30.1，actionlint 1.7.12，NuSMV 2.7.0から作成しました．再実行用の引数と評価上の制約は[事例研究](marimo-approval-race-case-study.md)を参照してください．CodeQL・zizmor・actionlintを一括比較する場合は，公式配布の各CLIと生成した対照版を `tools/approval_race_baselines.py` に指定します．`tools/collect_approval_case.py` は全SARIFを無損失圧縮し，CodeQL databaseと実行ファイルを除いた共有用結果を作ります．

NuSMVの検証は，生成した `.smv` を保存したホストで次のように行います．

```sh
python3 tools/verify_approval_models.py /path/to/marimo-evaluation \
  --nusmv /path/to/NuSMV
```

## 実際のUltralytics侵害への適用

事件前のUltralytics本体と外部Action，それぞれの対策版をSHA-256で照合し，入口からpip cache経由の公開までの候補を解析します．cache objectの同一性，書込成功，公開jobの条件通過は未確認値として残します．再現方法と既存ツールとの比較は[事例研究](ultralytics-incident-cache-chain.md)に記録しています．

```sh
docker run --rm --network none -v "$PWD:/repo:ro" -w /repo trust-boundary-analysis \
  python3 tools/ultralytics_cache_chain.py experiments/public-cases/ultralytics \
  --output /tmp/ultralytics-cache-chain
python3 tools/verify_ultralytics_models.py /path/to/ultralytics-cache-chain \
  --nusmv /path/to/NuSMV
```

`tools/materialize_ultralytics_combined.py`は，本体workflowと外部Actionの原本からCodeQL・zizmor用の入力を再構成します．その合成先は解析専用であり，実際のworkflowを実行しません．

## TanStack実侵害のcache境界を再実行する

[TanStack事例研究](tanstack-incident-cache-boundary.md)では，原本workflow7件と別repositoryのSetup Actionを結び，事件前と対策版を比較しています．モデル入力はYAMLだけで，事後報告から転記した保存・復元の観測値は `incident-observation.json` に別保存しています．

```sh
mkdir -p /tmp/tanstack-analysis
docker run --rm --network none -v "$PWD:/repo:ro" \
  -v /tmp/tanstack-analysis:/analysis -w /repo trust-boundary-analysis \
  python3 tools/tanstack_cache_chain.py experiments/public-cases/tanstack \
  --output /analysis
python3 tools/verify_tanstack_models.py /tmp/tanstack-analysis \
  --nusmv /path/to/NuSMV
```

`tools/materialize_tanstack_combined.py experiments/public-cases/tanstack --variant pre-incident --output /tmp/tanstack-codeql-input` でCodeQL・zizmor用の合成入力を作ります．対策版は `--variant mitigation` と別の空の出力先を指定します．同じCodeQL CLI 2.27.1 / `actions-queries@0.6.36` の通常・広いsuite，zizmor 1.30.1 regularで得た全警告とchecksumは `results/tanstack-cache-chain/evidence-index.json` に保存しました．**事件当時との比較には，別途CodeQL CLI 2.25.4と`actions-queries@0.6.27`を組み合わせ，新規databaseを作成する必要があります．** Linux x64公式CLI配布物のSHA-256，3入力の全SARIF，警告起点は[当時の比較索引](../results/tanstack-cache-chain/historical-codeql-2026-05/evidence-index.json)に保存しました．CLIとquery packを混在させた結果は当時の性能として扱いません．既存の `evaluate_public_cases.py` はworkflow単体を入力にするため，この二repository事例を一括評価には含めません．

## Cline実侵害のAIトリアージ・cache境界を再実行する

[Cline事例研究](cline-incident-agent-cache-boundary.md)では，事件前の全15 workflowと公式対策後の全12 workflowを保存しています．限定解析はmanifestのchecksumを確認し，workflowを実行せず，2版と現行cache権限の感度分析についてJSON・SMVを生成します．事件後の観測は `incident-observation.json` に分離しています．実際の攻撃者がIssueトリアージを入口にしたかは未確定です．

```sh
mkdir -p /tmp/cline-boundary-analysis
docker run --rm --network none -v "$PWD:/repo:ro" \
  -v /tmp/cline-boundary-analysis:/analysis -w /repo trust-boundary-analysis \
  python3 tools/agent_cache_boundary.py experiments/public-cases/cline \
  --output /analysis
NuSMV /tmp/cline-boundary-analysis/pre-incident.smv
NuSMV /tmp/cline-boundary-analysis/mitigation.smv
NuSMV /tmp/cline-boundary-analysis/pre-incident-current-policy.smv
```

CodeQL CLI 2.27.1，`actions-queries@0.6.36` の通常・広いsuite，zizmor 1.30.1 regular，actionlint 1.7.12の**全警告**とNuSMV出力は `results/cline-agent-cache-boundary/evidence-index.json` から追跡できます．既存ツールには各版の全workflowを入力し，提案側には同じworkflow原本を与えています．事件前から公開されていたAikidoのPromptPwndルールもOpengrep 1.30.0で実行し，事件前3件・対策後0件でした．入口の検出は既存手法でも可能です．

追加の既存スキャナPoutineによるTanStackとClineの比較条件・全警告は[追加比較](additional-poutine-baseline.md)に記録しています．

## SpotBugs実侵害の条件付きcheckoutを再実行する

[原本と外部Actionのmanifest](../experiments/public-cases/spotbugs-chain/manifest.json)のSHA-256を照合したうえで，ネットワーク無効のDocker内で静的解析します．外部Actionの可変タグが事件当時に指したSHAは不明です．以下は固定したAction実体が使用された場合の潜在経路を示し，workflowもActionも実行しません．

```sh
mkdir -p /tmp/spotbugs-analysis
docker run --rm --network none -v "$PWD:/repo:ro" \
  -v /tmp/spotbugs-analysis:/analysis -w /repo trust-boundary-analysis \
  python3 tools/conditional_checkout_chain.py \
  experiments/public-cases/spotbugs-chain \
  experiments/public-cases/spotbugs-chain/external-action-cond \
  --external-name haya14busa/action-cond --external-version v1 \
  --output /analysis/conditional-chain-analysis.json
```

CodeQLとの比較では，まず原本workflowだけを入力した通常suite・広いsuiteを実行します．加えて外部Actionの `action.yml` と `dist/index.js` を `.github/actions/action-cond/` に置いた合成入力を用います．合成入力でもworkflowの `uses: haya14busa/action-cond@v1` は書き換えません．CodeQL Actionsが抽出したのは2件のYAMLで，JSファイルの意味解析まで確認したものではありません．両入力の完全なSARIFと条件は[比較索引](../results/spotbugs-screening/evidence-index.json)に保存しました．

## 今回の実行環境

Python解析・CodeQLはLinux arm64 Docker内，NuSMVは既存のmacOS arm64版2.7.0を使用しました．ホストのPythonへ依存関係は追加していません．

標準PythonベースのDocker buildはDocker Hubのmetadata取得がtimeoutしたため，本検証では `--build-arg ANALYSIS_BASE=ubuntu-dev:latest` と既存の隔離用イメージでbuildしました．ベースの識別子は `sha256:41b49010e550155658f6f3c45cd6b10e1a555b6fa9892b28f3b8b7c5819ce397`，Pythonは3.12.3です．その後，[GitHub CI](https://github.com/formal-ci-cd/trust-boundary-verification/actions/runs/36887435413)では標準のPython 3.12.11ベースでbuild，51件のテスト，無害な局所再現が成功しました．同CIで解決されたベースdigestは `sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7` です．依存関係の固定と一括評価CLIはローカルの解析イメージでも確認済みです．
