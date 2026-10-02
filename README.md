# Trust Boundary Verification for CI/CD

CI/CD pipelineにおける信頼境界を形式的に検証するための研究用repositoryです．

## 最新の進捗

[Clineの実侵害](docs/cline-incident-agent-cache-boundary.md)では，事件前後の全workflowを同条件で比較しました．事件当時に利用可能だった版を含むCodeQL・zizmor・actionlintはIssue→AI→共有cache→公開用資格情報の対象経路を指摘せず，限定した提案モデルは事件前だけ条件付き反例を出しました．一方，既存のPromptPwndルールはAIへの入口を検出しました．実際の攻撃者がこの入口を使ったかは未確定で，優位性の主張は複数workflowの条件付き経路説明に限定します．[marimoの公開脆弱性](docs/marimo-approval-race-case-study.md)も比較しています．[TanStackの実侵害](docs/tanstack-incident-cache-boundary.md)では事件当時のCodeQLも実際のPR攻撃入口を警告することを確認しました．提案モデルは，この入口から別workflowのcache復元とOIDC権限までの条件付き経路を示します．さらに実YAMLから得た二つのrunを独立に進めるモデルで，保存と復元の順序により到達が変わることをNuSMVと独立BFSで確認しました．[保存済みSARIFから再計算した比較](results/tanstack-cache-chain/property-level-comparison.json)では，既存ツールの個別警告と複数workflow経路の提示を分けて評価しています．[Ultralyticsの実侵害](docs/ultralytics-incident-cache-chain.md)も比較しています．[Elementary・AsyncAPI・Trivy・SpotBugsの実侵害候補の選別](docs/incident-candidate-screening.md)も残しました．SpotBugsでは事後版CodeQLの通常suiteが0件，広いsuiteも外部Actionの未固定タグだけを警告し，追加した限定解析は実際のPR攻撃入口からsecretを設定した実行stepまでをつなぎ，固定YAMLから生成した小規模モデルもNuSMVと独立BFSで条件付き反例を出しました．sisakulintは未信頼checkoutを警告するため，既存ツール一般の見逃しとは主張しません．[Jupyterの公開脆弱性](docs/jupyter-composite-approval-race-case-study.md)では，従来の解析器が未対応だった外部Action内の時刻検査を追加し，上流の実際の修正前後を区別しました．既存ツールの広い検査には未信頼checkoutなどの警告があり，実侵害の排他的検出例ではありません．[Poutineを追加した比較](docs/additional-poutine-baseline.md)ではTanStackとClineで対象経路を結ぶ警告がないことを確認しました．[sisakulintを追加した比較](docs/additional-sisakulint-baseline.md)では，ClineのAI入口，TanStackの未信頼checkoutへの警告と，marimoの同秒承認を直す1箇所の変更で警告が変わらないことを確認しました．[研究の現状](docs/research-status-2026-10-02.md)と[再実行方法](docs/automatic-analysis-guide.md)を参照してください．既存ツール一般に対する排他的検出，汎用的な自動化，モデル検査器固有の優位性はまだ示せていません．

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

最初に読む資料は[CodeQLからNuSMVまでの変換](docs/codeql-to-nusmv-guide.md)です．実験設計は[実験設計表](docs/experiment-matrix.md)と[脅威モデル](docs/threat-model.md)に記録しています．CodeQLの内部表現に関する調査は[CodeQLによるGitHub Actions解析の内部表現](docs/codeql-actions-internal-model.md)，形式検証へ渡す中間形式は[CI/CD共通モデル](model/README.md)を参照してください．

成果物を複数run間で受け渡すGHA-A1からGHA-A5の構成は，[GitHub Actions成果物実験](experiments/github-actions/artifact/README.md)に記録しています．A1及びA2の結果は[成果物経路の実行結果](results/github-actions-artifact-runtime-2026-08-22.md)，A3からA5の結果は[成果物経路GHA-A3からGHA-A5の検証](results/artifact-case-study-2026-09-23.md)を参照してください．CodeQL抽出結果からの自動結合方法と形式モデルは[CI/CD共通モデル](model/README.md)に記録しています．
Experimental artifacts for formal verification of CI/CD trust boundaries.
