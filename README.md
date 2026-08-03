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
- `experiments/`：危険構成と安全構成に使う入力データ及び補助script．
- `results/`：各ツールの実行結果を整理した記録．

最初に読む資料は[実験設計表](docs/experiment-matrix.md)と[脅威モデル](docs/threat-model.md)です．
Experimental artifacts for formal verification of CI/CD trust boundaries.
