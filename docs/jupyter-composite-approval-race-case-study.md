# Jupyter公開脆弱性：外部Action内の承認時刻競合

[GitHub Security LabのGHSL-2026-203](https://securitylab.github.com/advisories/GHSL-2026-203_jupyter_notebook_repository/)は，`jupyter/notebook` の保守者によるコメントを受け，`jupyterlab/maintainer-tools` の複合ActionがPRをcheckoutする経路を報告した．承認コメント後にPR作者が**同じ秒**のうちに更新すると，`pushed_at -gt COMMENT_AT` は偽となり，更新後のSHAが検査を通る．報告者はPoCの成功を記しているが，第三者による実侵害の確認ではない．

## 上流原本の対応付け

固定した[対策前の利用側workflow](https://github.com/jupyter/notebook/blob/5e8eecdb666fee5f6d503f93dcc558042e543072/.github/workflows/playwright-update.yml)は，外部Action `@94b14ddfbdd1ed651e9f9343ae2409ab5c73a6a8` を呼ぶ．[対策後の利用側workflow](https://github.com/jupyter/notebook/blob/937519a9d7ed0b1a7b6eaf0fedb57dc6e3004a3e/.github/workflows/playwright-update.yml)は `@21b1cac622018fd37ec13a1c8081da814e10d94e` を呼ぶ．対応する外部Actionの[対策前](https://github.com/jupyterlab/maintainer-tools/blob/94b14ddfbdd1ed651e9f9343ae2409ab5c73a6a8/.github/actions/update-snapshots-checkout/action.yml)と[対策後](https://github.com/jupyterlab/maintainer-tools/blob/21b1cac622018fd37ec13a1c8081da814e10d94e/.github/actions/update-snapshots-checkout/action.yml)の当該ファイルの差は `-gt` から `-ge` の1箇所だけである．利用側workflowでは3つの外部Action参照が更新され，write権限とlocal `build-dist` Actionは同じである．これは研究側が合成した修正対照ではなく，上流の実際の変更である．両リポジトリの原本・SHA-256・ライセンスは[manifest](../experiments/public-cases/jupyter-notebook-screening/manifest.json)に記録した．

## 対象性質と検証

性質は「保守者の承認コメント後に更新されたPRのコードが，`contents: write`と`pull-requests: write`のjob内でlocal Actionとして実行されない」である．[限定解析器](../tools/composite_approval_race.py)は，利用側workflowの固定SHA参照，外部Action内の承認者検査，PR APIからのSHA・時刻取得，時刻比較，`gh pr checkout`，HEAD SHA照合，利用側jobのlocal ActionをYAMLから結ぶ．manifestのハッシュと参照が一致しない入力は受け付けない．任意のBash，外部Actionの全機能，実際のGitHub実行までは解析しない．

コメント直後を初期状態とし，PR作者の更新，秒の進行，API取得，検査，checkout，HEAD照合，local Action実行の順序を探索した．更新を**API取得より前**に置くと，HEAD照合が一致しても同じ秒の更新後コードに到達する．API取得後に更新する経路はHEAD照合で止まる．対策前はNuSMVの `AG !bad` が偽で独立BFSも48状態から反例を発見した．対策後はNuSMVが真でBFSも42状態に反例はなかった．ネットワーク無効のDockerで時刻比較だけを無害な入力で実行した結果も，対策前が旧commit・同秒更新を受け入れ，翌秒更新を拒否し，対策後は旧commitだけを受け入れる．[モデル・反例・判定](../results/jupyter-notebook-screening/evidence-index.json)を保存した．同じ秒の正当な更新も対策後は拒否され得る．

成立は，PR作者がコメント後に同じ秒で更新できること，APIの秒単位時刻とSHAが同一のPR状態を表すこと，checkoutとlocal Actionの実行が成功することを条件とする．これはwrite権限を持つjobで攻撃者コードに到達する**可能性**であり，実際の資格情報の漏洩，攻撃者の成功，第三者の侵害を証明しない．PythonのBFSでも検証できるので，この事例単独でNuSMVの不可欠性も主張しない．

## 既存ツールとの同条件比較

同じ2ファイルずつをネットワーク無効の隔離環境で検査した．版と全警告のSARIFは[結果索引](../results/jupyter-notebook-screening/evidence-index.json)に保存した．

| ツール | 対策前 | 対策後 | 対象の同秒条件を区別 |
|---|---:|---:|---|
| CodeQL 2.27.1 / Actions query pack 0.6.36，通常suite | 0件，2/2ファイル抽出 | 0件，2/2ファイル抽出 | しない |
| 同，広いsuite | 8件 | 8件 | しない |
| zizmor 1.30.1 regular | 2件 | 2件 | しない |
| sisakulint 0.3.7 | 0件 | 0件 | しない |
| actionlint 1.7.12 | 0件 | 0件 | しない |
| 本研究の限定モデル | 同秒更新の反例あり | 反例なし | する |

CodeQLの広いsuiteには`actions/untrusted-checkout/medium`が1件あり，`gh pr checkout`を警告している．残り7件は式展開の警告である．zizmorは認証情報の保持とlocal Action参照を警告した．既存ツールの警告集合を規則・行・メッセージで照合すると，対策前後で一致した．したがって比較上の差は**承認時刻の同秒という対象性質**に限る．「既存ツールはこのworkflowの危険を一切検出しなかった」または「実際の侵害で提案手法だけが検出した」とは言えない．

再現には `python3 tools/composite_approval_race.py experiments/public-cases/jupyter-notebook-screening/upstream-before --output <新規出力先> --replay-guard` を用い，対策後も同様に実行する．`tests/test_composite_approval_race.py` は実原本の差，HEAD不一致による遮断，manifest改変，local Action不在を検査する．
