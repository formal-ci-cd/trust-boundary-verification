# v2評価出力の索引

出力を圧縮後に削除する方針は撤回しました．保存済みarchiveの全3,313ファイルをSHA-256照合付きで元の配置へ復元しています．元のarchiveとmanifestも残しています．

- [README.md](README.md)：初期prototypeの当時の評価報告．復元した原本．
- [cases/](cases/)・[mutations/](mutations/)：初期prototypeのSMV・ログ・反例など．
- [extension-2026-10-22/final/](extension-2026-10-22/final/)：拡張評価時の個別ファイル．
- [latest/](latest/README.md)：採用済み評価の要約．
- [archive/](archive/)：追加の保存用archiveとSHA-256 manifest．
- [restoration-2026-10-22.json](restoration-2026-10-22.json)：原配置へ復元した全ファイルの照合記録．
- [migration.json](migration.json)：以前に圧縮した時点の記録．現在のファイル数ではない．

直近3回の `outputs/runs/` の全詳細も復元済みです．新しいrunは生成したまま保持します．[コンテナでの操作](../../docs/container-guide.md)で，モデル生成・検証・集計を段階的に実行できます．`outputs/runs/` は引き続きGit管理外ですが，コンテナ停止後もホストに残ります．
