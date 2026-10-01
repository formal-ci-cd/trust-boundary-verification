# 実際の侵害事例：UltralyticsのAction式展開からpip cache経由の公開まで

## 結論

2024年12月のUltralytics侵害には，外部PRから起動する整形workflow，その外部Action内の式展開，CIのcache，PyPI公開workflowが関わりました．[PyPI運営の分析](https://blog.pypi.org/posts/2024-12-11-ultralytics-attack-analysis/)は，最初の悪性公開2版がGitHub Actions経由であり，build中のcache攻撃だったことを確認しています．[zizmor作者の事件分析スライド](https://archive.fosdem.org/2025/events/attachments/fosdem-2025-6543-hunting-for-github-actions-bugs-with-zizmor/slides/238286/fosdem-20_6UrKP8W.pdf)は，攻撃入口の `git pull origin ${{ github.head_ref || github.ref }}` と `actions/setup-python` の `cache: pip` を示しています．後の2版は盗まれた古いPyPI API tokenを使った別経路なので，このモデルの対象に入れません．

提案側の新しい限定解析は，二つの公開repositoryのYAMLから入口と公開側cache利用を自動で結び，その間のcache書込み・同一object・公開jobの起動条件を未確認のまま残します．それらが成立する場合のNuSMV反例は得られ，独立BFSと一致しました．事件自体は確認済みですが，この形式モデルの反例が事件当日のcache IDや実行時権限を観測した証拠ではありません．

この事例では，既存ツールも重要な危険点を警告します．したがって「UltralyticsではCodeQLとzizmorの双方が見逃し，提案側だけが検出した」とは主張しません．その主張の対象となる公開事例は[marimoの承認時刻競合](marimo-approval-race-case-study.md)です．

## 入力の固定と検出された経路

研究用の`experiments/public-cases/ultralytics/manifest.json`は，事件前タグv8.3.40のコミット `dbdb451512ad4b3c636e6d61ad7af31e07301ece` の9 workflow，事件後のpublish job分離コミット `e0f8eda366c7cf08a1c311a17eaaaf87914f8012` の9 workflowを記録します．今回，事件直前に`ultralytics/actions@main`が指していたと公開履歴から推定される `eb1201bd933b9f6096c64525ccaee3684c91bf14` の `action.yml` を追加しました．初回悪性公開（2024-12-04 20:51 UTC）以前の同Actionの最終コミットです．`@main`は可変参照なので，実行時に厳密にこのSHAが使われたログは確認していません．事件後のAction修正 `89c1f380073ace366e4fc8f41f32427b6d644c3e` も対照として保存しました．各ファイルは原本のままでSHA-256照合します．

| 段階 | 原本から確認した構成 | 実行時の未確認部分 |
|---|---|---|
| 外部入力 | `format.yml`の`pull_request_target`から`ultralytics/actions@main`を呼ぶ | 事件当日のAction解決SHA |
| 攻撃入口 | 外部ActionのBash `git pull origin ${{ github.head_ref || github.ref }}` がPR branchを直接式展開する | 事件当日の注入コマンド・token取得の実行ログ |
| cache | `publish.yml`の`actions/setup-python@v5`が`cache: "pip"`を指定し，後に`pip install`する | 書込成功，実cache key・object ID，復元された具体的な内容 |
| build・公開 | 同jobが`python -m build`と`gh-action-pypi-publish`へ進む | actor条件の成立過程，外部OIDC設定，個別実行ログ |

実際の成果物公開とcache攻撃の関係は上記のPyPI分析が根拠です．YAMLの同じ`pip`文字列だけでは同じcache objectの利用は証明できません．`publish` jobの `github.actor == 'glenn-jocher'` 条件も原本に存在します．[事件分析スライド](https://archive.fosdem.org/2025/events/attachments/fosdem-2025-6543-hunting-for-github-actions-bugs-with-zizmor/slides/238286/fosdem-20_6UrKP8W.pdf)はこの条件の変更に触れますが，静的入力だけでその到達を確定しません．

`tools/ultralytics_cache_chain.py` は次の4組を同じルールで評価します．

| workflow / Action | 静的な経路 | NuSMV `AG !bad` | 解釈 |
|---|---|---|---|
| 事件前 / 事件前 | 入口とpip cache→build→publishの候補あり | 偽 | cache同一性・書込・公開条件が成立すると経路が可能 |
| 事件前 / Action修正版 | 対応する無引用の `git pull origin` 式展開なし | 真 | 対象の入口からの経路なし |
| publish job分離版 / 事件前Action | 対応するAction呼出しとpip cache公開経路なし | 真 | 対象経路なし |
| publish job分離版 / Action修正版 | 両方なし | 真 | 対象経路なし |

各モデルで独立BFSとNuSMVの判定が一致しました．事件前の反例は，外部状態 `sameCacheObject`，`cacheWriteSucceeded`，`publisherGuardPassed` を真と置いた場合の条件付き反例です．他の割当も探索しています．静的解析はこれらを`unknown`のまま報告し，実際の悪用成功の判断はPyPIの公開資料と混同しません．またBFSが同じ判定を出すので，この事例単独でNuSMVの必須性は示しません．

## 既存ツールとの公平な比較

初回比較ではUltralytics本体のworkflow9件だけを入力にしていました．今回，事件前Action定義もローカルの解析専用ディレクトリへ同じバイト列で配置し，CodeQL CLI 2.27.1 / `codeql/actions-queries@0.6.36` とzizmor 1.30.1 regularを再実行しました．CodeQLはGitHub Actionsファイル10件中10件を抽出しました．配置先は比較用で，実際のActionの呼出し先やファイル内容を変更していません．

| CodeQL入力 | default | security-and-quality | 対象の警告 |
|---|---:|---:|---|
| workflow9件のみ | 14件 | 38件 | defaultは権限指定不足のみ |
| workflow9件＋外部Action定義 | 14件 | 46件 | 広いsuiteでAction内の式展開4件（`git pull`を含む），untrusted checkout2件 |

外部Actionを含めたzizmor regularは計91件を出し，Action内の`git pull`行を含む式展開警告と，`publish.yml`の`cache: pip`に関する警告を出しました．CodeQL defaultは式展開を指摘しませんでしたが，広いsuiteは攻撃入口を警告します．zizmorも入口とcache利用をそれぞれ指摘します．いずれも「PR由来のコード実行から，事件当時の共有cacheを介し，公開buildへ至る」という一本の経路の成立条件までは警告に含めません．既存ツールの警告を無視して提案側だけの検出と数えません．全警告のSARIFは `results/ultralytics-cache-chain/` に無損失圧縮して保存しました．

## 再現と限界

```sh
docker run --rm --network none -v "$PWD:/repo:ro" -w /repo trust-boundary-analysis \
  python3 tools/ultralytics_cache_chain.py experiments/public-cases/ultralytics \
  --output /tmp/ultralytics-cache-chain
python3 tools/verify_ultralytics_models.py /path/to/ultralytics-cache-chain \
  --nusmv /path/to/NuSMV
```

前半をコンテナ内で行い，後半はNuSMVがあるホストで保存したモデルに適用します．`tools/materialize_ultralytics_combined.py` が原本と外部Actionから既存ツール用の同一入力を再構成します．第三者のworkflow，Action，パッケージ，事件当時の注入コードは実行していません．2024年のcache権限は事件分析から明示した条件であり，現在のGitHub Actionsのcache制約へそのまま一般化しません．
