# v2評価出力

- [latest/](latest/README.md)：採用した評価の要約．65モデルの結果，BFS照合，未対応一覧．
- [archive/](archive/)：初期prototypeと拡張評価の全詳細を保存した2つのarchive．memberごとのSHA-256付き．
- [migration.json](migration.json)：展開済みファイルをまとめた際の件数と照合記録．

以前の `cases/`，`mutations/`，`extension-2026-10-22/final/` などはarchive内へ移しました．古い報告にあるこれらのパスは当時の配置です．必要時だけ作業用ディレクトリへ展開してください．

```sh
mkdir -p /tmp/tbv-evidence
tar -xzf outputs/model-v2/archive/prototype-2026-10-22.tar.gz -C /tmp/tbv-evidence
tar -xzf outputs/model-v2/archive/extension-2026-10-22.tar.gz -C /tmp/tbv-evidence
```

通常の再生成先は `outputs/runs/<新規名>`（Git管理外）です．run成功後は，要約数ファイル・`details.tar.gz`・`pack-manifest.json` に自動でまとめます．全ログ・SMV・YAML・反例・source mapはarchiveに無損失で残ります．`V2_KEEP_RAW=1` は詳細を直接調べるときだけ指定します．既存runは上書きしません．

`results/` は論文の固定済み証拠なので今回の整理対象から外しました．
