# Trust Boundary Verification for CI/CD

CI/CD pipelineにおける信頼境界を形式的に検証するための研究用repositoryです．

## 中心仮説と現在地

本研究は，GitHub Actionsで未信頼producerがCache又はartifactへ書き込み，別workflow又は別runのconsumerが同じ共有状態を完全性確認なしに利用して，privileged authorityへ至る**一つの経路**を有限状態モデルと反例で提示することを中心とします．既存ツールが入口を警告することと両立する主張です．

対象と必要十分条件は[Threat Model](docs/threat-model.md)，A1～A5・TanStackの評価と既存ツールとのproperty-level比較は[中心仮説と証拠](docs/thesis-claim-and-evidence.md)，自動抽出できる範囲と未完了項目は[抽出範囲と評価契約](docs/scope-and-extraction.md)にまとめています． A1～A4は[限定したYAML自動評価](results/core-artifact-subset/analysis.json)で注釈なしの再生成を確認しました．A5は同じ評価器では`unknown/unsupported`とし，旧来の人手対応結果と区別します．SpotBugs，Jupyter，marimo，Clineなど既存の探索結果は保存し，中心評価・補助事例・scope外に区分しています．

## 研究目的

GitHub Actionsの外部Pull Request等に由来するCacheやartifactが，後続の特権を持つjobやworkflowで検証されずに利用される構成を対象にします．

既存の静的解析ツールは，cache poisoningやartifact poisoningに関する既知の危険パターンを検出できます．本研究ではさらに，複数workflow及び複数runをまたぐ状態遷移として，未信頼producerから特権consumerへの到達可能性を検証します．

## このrepositoryで扱うもの

- GitHub ActionsのCache及びartifactを介した信頼境界．
- CodeQL，zizmor，actionlintなどの既存ツールとの比較．
- 危険構成と安全構成を対にした再現可能な最小実験．
- 既存のGitLab CI/CD比較記録は保存しますが，現在の中心評価には含めません．

## 安全な実験範囲

- 実験workflowは公開repository上で動かします．
- 実在するsecret，access token，deploy key，publish権限は使用しません．
- 攻撃payloadの代わりに，安全な文字列及びdummy fileを使用します．
- self-hosted runnerは使用しません．
- 実験の目的は，設定上の到達可能性と既存ツールの検出範囲を比較することです．

## 構成

- `docs/`：実験設計及び脅威モデル．
- `.github/workflows/`：CodeQL及びGitHub Actions実験用workflow．
- `experiments/`：危険構成と安全構成に使う入力データ及び実行手順．
- `results/`：各ツールの実行結果を整理した記録．
- `codeql/`：CodeQLの内部表現を取り出す独自query．
- `model/`：静的情報と実行時情報を分離したCI/CD共通モデル．
- `tools/`：解析結果を共通モデルへ変換する補助script．

最初に読む資料は[CodeQLからNuSMVまでの変換](docs/codeql-to-nusmv-guide.md)です．実験設計は[実験設計表](docs/experiment-matrix.md)と[脅威モデル](docs/threat-model.md)に記録しています．CodeQLの内部表現に関する調査は[CodeQLによるGitHub Actions解析の内部表現](docs/codeql-actions-internal-model.md)，形式検証へ渡す中間形式は[CI/CD共通モデル](model/README.md)を参照してください．

成果物を複数run間で受け渡すGHA-A1からGHA-A5の構成は，[GitHub Actions成果物実験](experiments/github-actions/artifact/README.md)に記録しています．A1及びA2の結果は[成果物経路の実行結果](results/github-actions-artifact-runtime-2026-08-22.md)，A3からA5の結果は[成果物経路GHA-A3からGHA-A5の検証](results/artifact-case-study-2026-09-23.md)を参照してください．CodeQL抽出結果からの自動結合方法と形式モデルは[CI/CD共通モデル](model/README.md)に記録しています．
Experimental artifacts for formal verification of CI/CD trust boundaries.
