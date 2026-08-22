# Trust Boundary Verification for CI/CD

CI/CD pipelineにおける信頼境界を形式的に検証するための研究用repositoryです．

## 研究目的

未信頼なPull Request又はMerge Requestに由来するCacheやartifactが，後続の特権を持つjobやworkflowで検証されずに利用される構成を対象にします．

既存の静的解析ツールは，cache poisoningやartifact poisoningに関する既知の危険パターンを検出できます．本研究ではさらに，複数workflow及び複数runをまたぐ状態遷移として，未信頼producerから特権consumerへの到達可能性を検証します．

## このrepositoryで扱うもの

- GitHub ActionsのCache及びartifactを介した信頼境界．
- CodeQL，zizmor，actionlintなどの既存ツールとの比較．
- 危険構成と安全構成を対にした再現可能な最小実験．
- 将来的なGitLab CI/CDとの比較に使う共通の脅威モデル．

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

最初に読む資料は[実験設計表](docs/experiment-matrix.md)と[脅威モデル](docs/threat-model.md)です．CodeQLの解析方法に関する調査は[CodeQLによるGitHub Actions解析の内部表現](docs/codeql-actions-internal-model.md)，形式検証へ渡す中間形式は[CI/CD共通モデル](model/README.md)に記録しています．

成果物を複数run間で受け渡すGHA-A1及びGHA-A2の構成は，[GitHub Actions成果物実験](experiments/github-actions/artifact/README.md)に記録しています．静的解析の比較は[成果物実験の静的解析結果](results/github-actions-artifact-static-analysis-2026-08-22.md)，形式検証は[成果物経路の形式検証結果](results/github-actions-artifact-formal-model-2026-08-22.md)，GitHub上での確認結果は[成果物経路の実行結果](results/github-actions-artifact-runtime-2026-08-22.md)を参照してください．
Experimental artifacts for formal verification of CI/CD trust boundaries.
