# Model v2 実装・検証出力

このディレクトリは，既存の `results/` と分離した，今回のstateful YAML→SMV prototypeの生成物である．**部分達成**であり，実GitHub Actions runの再現や実侵害の証明ではない．

## 実行結果

- Docker内の回帰テスト：18件すべて成功．
- A1～A5のSMV：5件生成し，NuSMV 2.6.0で全件exit code 0．
- Mutation：21種類を別々の一時YAMLまたはSMV入力制約で生成．20件がNuSMV exit code 0，構文不正の対照1件はexit code 1．
- 遷移全域性：構文が有効な25モデルは `NuSMV -ctt` がすべてexit code 0．
- 初期状態：有効な25モデルすべてで，到達可能状態数が0より大きいことを確認．
- 全26モデルについてCTL propertyの結果，NuSMV stdout/stderr/exit code，SMVを保存．counterexampleが出たpropertyはtraceとstep単位のYAML source mapを保存．
- A1/A3のdownload/useに限る独立Python BFSを実施し，固定シナリオでNuSMVのreachability判定と両caseとも一致．integrity／sinkを含む全モデルの独立照合は未達．

## A1～A5の要約

| Case | Download | Artifact依存利用 | 完全性状態 | 模擬sink | 実権限sink |
|---|---|---|---|---|---|
| A1 | 到達可能 | 到達可能 | 未検証 | 条件付きArtifact依存反例あり | unsupported |
| A2 | 到達可能 | 到達可能 | verified／mismatch／blockedが到達可能．mismatch guardは利用を遮断 | site到達あり，Artifact依存sinkは到達しない | unsupported |
| A3 | 到達可能 | 到達しない | 未検証 | 到達しない | unsupported |
| A4 | 到達可能 | 到達可能 | 未検証 | 到達しない | unsupported |
| A5 | 到達可能 | 到達可能 | 未検証 | metadata/output依存の条件付き反例あり | unsupported |

「到達しない」は生成した有限抽象モデル内の結果である．unknown／unsupported診断が残るケースを全体としてsafeとは扱わない．特に `bad_real_authority_path` は実権限を使うsink contractが未登録のため，propertyが真でも実権限の安全性を検証した結果ではない．

## 主なファイル

- `cases/A1.smv`～`cases/A5.smv`：生成SMV
- `cases/*.map.json`：Typed IR，provenance，source span，診断を含むmanifest
- `cases/*.stdout.txt`，`*.stderr.txt`，`*.exitcode`：NuSMV出力
- `cases/*.cex.*.trace.txt`，`*.source-map.json`：counterexampleとYAML step対応
- `cases/verification-summary.json`：A1～A5のCTL結果
- `mutations/matrix.json`：21 mutationの入力差分・model・診断一覧
- `mutations/verification-summary.json`：mutation別CTL結果
- `bfs/*.bfs.json`，`bfs/verification-summary.json`：独立BFSとNuSMV対照結果
- `bfs/*.smv`，`bfs/*.stdout.txt`，`*.stderr.txt`，`*.exitcode`：固定シナリオNuSMV入力と実行ログ
- `verification-summary.json`：全26モデルの統合結果
- `regression-tests.stdout`，`regression-tests.stderr`，`regression-tests.exitcode`：回帰テスト
- `runtime-environment.txt`，`nusmv-version.txt`：runtime情報

完全な制約，caseごとのproperty結果，Mutation結果，未達事項は [implementation-report.md](../../docs/next-model-design/implementation-report.md) を参照．
