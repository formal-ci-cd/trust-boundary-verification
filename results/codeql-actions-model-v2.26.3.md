# CodeQLの内部表現を独自クエリで列挙した結果

実施日：2026年8月22日

## 1．目的

CodeQLがGitHub Actionsのworkflowから構成した内部表現を独自クエリで取り出し，形式検証用モデルを生成する前処理として利用できるか確認する．今回は，workflow，起動契機，権限，job，step，Actionの引数及び式を表として出力した．

## 2．実行環境

| 項目 | 内容 |
| --- | --- |
| CodeQL Bundle | `v2.26.3` |
| CodeQL CLI | `2.26.3` |
| `codeql/actions-all` | `0.5.0` |
| 解析対象 | リポジトリ内のGitHub Actions workflow 4件 |
| 独自クエリ | `codeql/queries/WorkflowStructure.ql` |
| 全出力 | `results/codeql-actions-model-v2.26.3.csv` |

CodeQL BundleはGitHub公式releaseから取得した`codeql-bundle-osx64.tar.zst`を使用した．取得したファイルのSHA-256は`378b0f9c63c5e970b552dec418942ba4cd267baddc8345dd0d3b959ce3e77536`であり，公式releaseに表示されたdigestと一致した．CodeQL本体，database及びcompile cacheは`/private/tmp`に置き，ホストへ常設する形では導入していない．

## 3．GHA-C1から得られた情報

独自クエリにより，GHA-C1から次の関係を取得できた．

| 種類 | 取得値 | モデル上の意味の候補 |
| --- | --- | --- |
| 起動契機 | `pull_request_target` | 外部から起動可能であり，CodeQL上ではprivilegedと分類される． |
| workflow権限 | `contents: read` | 明示されたGitHub tokenの権限である． |
| job | `cache-untrusted-input`，`ubuntu-latest` | 実行単位とrunnerである． |
| Action | `actions/checkout@v4` | リポジトリ内容を取得する操作である． |
| `ref`引数 | `${{ github.event.pull_request.head.sha }}` | Pull Request側のcommitを参照する式である． |
| `persist-credentials`引数 | `false` | checkout後に認証情報を残さない設定である． |
| Action | `actions/cache/save@v4` | キャッシュへの保存を試みる操作である． |
| `path`引数 | `.research-cache-input` | 保存対象のpathである． |
| `key`引数 | `trust-boundary-c1-default-context-v1` | キャッシュ識別子の一部である． |

この結果から，CodeQL databaseには，少なくとも「どの起動契機で，どのjobが，どの参照先を取得し，どのpathをどのkeyでキャッシュへ保存しようとするか」を構成するための静的情報が格納されていることを確認できた．

## 4．GHA-C2との比較

同じクエリは，GHA-C2から`pull_request`を外部起動可能かつnot-privilegedとして取得した．また，キャッシュkeyにPull Request番号を含む式`${{ github.event.pull_request.number }}`も取得できた．したがって，C1とC2の起動契機及びkey設計の差を，同一形式の表として比較できる．

ただし，`externally-triggerable`及び`privileged`はCodeQLのquery libraryによる静的な分類である．実行時に実際に与えられた権限又はキャッシュ書込みの成否を証明する値ではない．

## 5．取得できない情報

今回の出力だけでは，次の情報は得られない．

- 実行時にキャッシュ保存が成功したか．
- あるキャッシュkeyが，GitHub内部でどのキャッシュobjectへ解決されたか．
- 後続runがどのキャッシュを復元し，その内容を利用したか．
- 複数run間でキャッシュ状態がどのように変化したか．
- 実行日時点のGitHub側の権限制御及び仕様version．

実際のGHA-C1では静的な保存操作を取得できる一方，実行時の保存は`cache write denied`によって拒否されている．したがって，静的な`保存操作が記述されている`ことと，実行時の`保存可能である`ことは別の関係として扱う必要がある．

## 6．現時点での考察

CodeQLの内部表現は，モデル検査器へそのまま渡せる完成済みの状態遷移モデルではない．一方で，YAMLを独自に再解析しなくても，workflowの構造，起動契機，権限，Action，引数及び式を関係として取得できる．そのため，本研究ではCodeQLを，形式検証用モデルを生成するための前処理として利用できる可能性がある．

次は，今回取得できた静的な関係を小さな共通モデルへ変換し，CodeQLから得られない実行時のキャッシュ状態と複数run間の遷移を，どのように追加するかを整理する．
