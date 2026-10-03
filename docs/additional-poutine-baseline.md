# Poutineによる追加比較：TanStackとCline

GitHub Actions向け既存スキャナ[Poutine](https://github.com/boostsecurityio/poutine) v1.1.6を，既に固定した実侵害リポジトリの原本に追加適用した．公式Linux arm64配布物のSHA-256は `460c90300c6329106b551c150682d12e457365f6436a6cbbd08fe79eb9a98131` で，公式checksumと一致した．実行時の版・コミットは `1.1.6` / `8918c66db19ecfd12b2f8379e445c3da4589e599`．ネットワーク無効のDockerで `analyze_local /input --disable-version-check --format json` を実行し，カスタムルールや除外設定は使っていない．2026年5月22日公開版による**遡及的な評価**であり，事件当時の配備状況は示さない．

TanStackは[合成入力の生成器](../tools/materialize_tanstack_combined.py)で，本体workflow全7件と別repositoryのSetup Action定義1件を同一解析ディレクトリへ置いた．事件前・対策後・手動起動イベントを1行だけ除いた対照例に同じ方法を適用した．Clineは[manifest](../experiments/public-cases/cline/manifest.json)で固定した事件前15件・対策後12件のworkflowディレクトリを入力した．第三者のworkflowや攻撃payloadは実行していない．

再実行時は公式Linux arm64バイナリをchecksum照合して展開し，`POUTINE_BIN` にその絶対パスを指定する．事件前TanStackの例は以下の通り．対策版は `--variant mitigation`，1行対照例は `--without-dispatch-control` を使い，毎回新しい出力ディレクトリを指定する．

```sh
docker run --rm --network none -v "$PWD:/repo:ro" -v /tmp:/scratch -w /repo \
  trust-boundary-analysis python3 tools/materialize_tanstack_combined.py \
  experiments/public-cases/tanstack --variant pre-incident \
  --output /scratch/tanstack-poutine-pre
docker run --rm --network none \
  -v "$POUTINE_BIN:/poutine:ro" -v /tmp/tanstack-poutine-pre:/input:ro \
  trust-boundary-analysis /poutine analyze_local /input \
  --disable-version-check --format json
```

| 入力 | 全警告 | 対象の複数workflow経路 |
|---|---:|---|
| TanStack事件前 | 15件 | 指摘なし |
| TanStack対策後 | 15件 | 指摘なし |
| TanStackの1行対照例 | 15件 | 指摘なし |
| Cline事件前 | 13件 | 指摘なし |
| Cline対策後 | 11件 | 指摘なし |

TanStackの各入力では14件が「未検証作成者のAction」，1件が「pinが効きにくいAction」だった．入口の `benchmark-pr` と公開側の `release` が同じ外部Actionを参照することには警告するが，**fork PR→main cache→OIDC job**の接続や，対策後にその経路がなくなることは警告にない．Cline事件前では，AIトリアージ入口のAction参照に同じ未検証作成者の警告が1件ある．`injection` 2件は別の `publish.yml` の手動入力に対する警告で，**Issue→AI→共有cache→夜間公開job**の接続は示していない．全警告のルール・場所・内容は[無損失圧縮した結果とchecksum](../results/additional-poutine-baseline/evidence-index.json)に保存した．

この比較は**対象経路を明示した警告があるか**の評価であり，「危険を示唆する警告がない」という意味ではない．とくにTanStackではzizmorが `pull_request_target` を，ClineではPromptPwndがAIへの入口を検出している．提案側の反例にも実行時の未観測条件がある．また，Poutineの今回の出力には解析済みworkflow件数が記されないため，入力ディレクトリの件数と検出されたAction参照を確認した範囲で結果を扱う．Poutineを含む**すべての既存ツール一般**に対する優位性を主張する資料ではない．[事件当時のCodeQL](tanstack-incident-cache-boundary.md)はTanStackの実際のPR攻撃入口を検出するため，TanStackを排他的検出事例には数えない．
