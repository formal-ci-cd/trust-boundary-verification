# 検査・置換・利用の順序

この実験は，書き換え可能な共有ファイルの抽象モデルです．GitHubのimmutable artifact自体を上書きできるという仮定は置いていません．YAMLから並行実行や物理共有を推定する機能は，まだ含めていません．

各objectに未信頼writerとconsumerを置き，全actorの進行順序を検査します．consumerはrestore，verify，useを実施します．writerは一度だけ共有ファイルを未信頼版へ置換できます．初期状態は信頼版です．verifyは信頼版を受理し，失敗時は停止します．外部から信頼版のdigestを与えることは，明示した検査ポリシーの設定です．

- `mutable`: verifyとuseが共有ファイルをそれぞれ読む．検査後の置換で `bad` へ到達する．
- `snapshot`: restore時のsnapshotをverifyとuseの両方に使用する．このモデルでは到達しない．
- `revalidate`: 共有ファイルを利用時にも検査する．このモデルでは到達しない．

`AG !bad` をNuSMVで検査し，同じ遷移を独立実装したBFSでも完全探索します．1～3 objectの9条件では判定が一致しました．mutableの反例は実際の無害なファイル置換・SHA-256比較でも再生します．

この結果は，実行順序を表現する必要性を示します．探索スクリプトも同じ結果を出せるため，モデル検査ツールが必須だという証明ではありません．1～3 objectは規模比較の基礎であり，大規模workflowでの性能優位性は示していません．

再実行例:

```sh
python3 tools/temporal_verification.py --nusmv /path/to/NuSMV \
  --max-objects 3 --output /tmp/temporal-results
```

保存結果は `results/temporal-2026-10-02/` です．秒数は実行環境依存であり，反例・到達状態数・判定を主な比較対象にします．
