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

## 今回の実行環境

Python解析・CodeQLはLinux arm64 Docker内，NuSMVは既存のmacOS arm64版2.7.0を使用しました．ホストのPythonへ依存関係は追加していません．

標準PythonベースのDocker buildはDocker Hubのmetadata取得がtimeoutしたため，本検証では `--build-arg ANALYSIS_BASE=ubuntu-dev:latest` と既存の隔離用イメージでbuildしました．ベースの識別子は `sha256:41b49010e550155658f6f3c45cd6b10e1a555b6fa9892b28f3b8b7c5819ce397`，Pythonは3.12.3です．publicな標準ベースでのbuild成功は，本実行では確認していません．依存関係の固定と評価CLIは，構築したイメージで確認済みです．
