# CodeQL独自クエリ

`queries/WorkflowStructure.ql`は，GitHub Actionsのworkflowから，形式検証用モデルの入力候補となる構造を表として出力する．

## 対象とする情報

- workflow及び起動契機．
- eventに対するCodeQLの外部起動可能性及びprivileged分類．
- job，runner及びpermission．
- step，Action名及びversion．
- checkout及びキャッシュに関係する主な`with`引数．
- `${{ ... }}`式と正規化された参照．

## 実行方法

CodeQL Bundleを展開し，リポジトリのrootで次のように実行する．`/path/to/codeql`と出力先は実際の環境に合わせて変更する．

```bash
/path/to/codeql database create /tmp/actions-db \
  --language=actions \
  --source-root=.

/path/to/codeql query run codeql/queries/WorkflowStructure.ql \
  --database=/tmp/actions-db \
  --output=/tmp/workflow-structure.bqrs

/path/to/codeql bqrs decode /tmp/workflow-structure.bqrs \
  --format=csv \
  --output=/tmp/workflow-structure.csv
```

2026年8月22日にCodeQL CLI `2.26.3`で確認した出力は，[解析結果](../results/codeql-actions-model-v2.26.3.md)及び[全件CSV](../results/codeql-actions-model-v2.26.3.csv)に保存している．
