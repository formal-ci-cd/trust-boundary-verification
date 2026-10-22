# 次期 YAML→SMV 変換器：検証計画と受入条件

この計画は将来実装の受入条件であり、今回実行したテスト結果ではない．既存A1～A5の保存済み結果は従来の抽出器／JSON事実モデルに対する結果である．新変換器が実際に生成・検証し、反例traceとsource mapまで照合するまでは再現済みと記載しない．

## 1. 判定とtraceの共通受入条件

各ケースで少なくとも以下を記録する．

- Parserがon/jobs/needs/steps/if/uses/with/permissionsを型付きIRへ変換し、各nodeのsource spanを保持する．
- SMV stateにworkflow/job/step順序、run relation、Artifact object identity、dataflow、integrity、sink、effective authorityが現れる．Pythonが最終hazard Booleanを作らない．
- NuSMVのproperty結果だけでなく、対象transitionとYAML spanを対応付けたtraceを出す．反例内のunknown選択はoriginとdomainを表示する．
- unsupportedがproperty slice内にあるケースでsafe-within-modelを出さない．supportedでないShell/Actionを無害とみなさない．
- unknown相関のmutation pairを比較し、同一symbolを別々にnondeterministic化した実装を拒否する．
- 必要なら小さい独立scheduler/BFSを用いてSMVのbounded model結果と照合する．独立探索一致は実装照合であり、NuSMVだけが提供する正しさの証明とは主張しない．

## 2. 必須fixture matrix

| ID | 入力と意味 | 期待するモデル／結果 |
|---|---|---|
| A1 | PR由来fileをuploadし、workflow_run後に同一objectをdownload、限定Shell ruleが内容を読み、研究用dummy publish markerへ条件付き到達 | upload/download/use/sinkの順序、同一object ID、source taint、未検証をtraceに表示する．simulated-path propertyは反例候補になり得る．現行YAMLのcontents:readとmarkerだけから実write authorityを主張しない． |
| A2 | 信頼済みliteral digestをArtifactの同一objectに利用前照合し、失敗ならuse stepをskip | verify→guard→useの順をSMVに示す．mismatch pathはsinkへ到達しない．trusted digestと一致するpathはcollision-resistance assumptionの下で検証対象valueのtaintを除去し、A2 fixtureのpublish=false内容ではsinkへ届かない．digestの存在だけをtrust証明としない． |
| A3 | A1と同じ取得だがArtifactを後続stepが参照しない | download成功traceは存在し得るが、artifact_dependent_useとcontent-dependent sinkはfalseのまま．A1のsink counterexampleと混同しない． |
| A4 | Artifactは読まれるが模擬sink条件がfalse、又は有効target permissionが存在しない | useとauthority/sink stateを分離する．real badは不成立．利用済みという理由だけでunsafeとしない． |
| A5 | PR由来dist/metadataをuploadし、consumerがmetadataをstep outputへ設定し、模擬更新markerへ渡す | object field→step output→後続sinkのprovenanceと順序をtrace化する．markerを実repository update/contents:writeと解釈しない． |
| Trigger mismatch | workflow_run workflows/name、types、branchのどれかが一致しない | Consumer runが生成されない．workflow_run completedでもjob ifのconclusionがfailureならsuccess guardを通らない． |
| Upload以前のdownload | workflow_run requested/in_progressでConsumerがProducer uploadより先にdownloadを試す | そのtransitionのdownloadはfailureのみ．後続upload成功を過去のdownloadへ結ばない．completed-onlyの場合はProducer terminal後に限りConsumerが起動する． |
| Shared context | 同じworkflow_run.event又は同じsteps outputを複数条件で参照 | canonical symbol一つを共有し、x == aとx != aの同時成立等の不可能traceが無い． |
| Upload/download outcome | success/failureをそれぞれ選ぶ | successでは対象objectを追加/参照、failureでは無い/取得されない．成功結果は選択後にlatchし、条件ごとに再抽選しない． |
| Unknown Action分類 | contract済み無関係Action、property-relevant未対応Action、影響不明Actionを個別に入力 | proven-irrelevantはsliceから除外、property-relevant-unsupportedとunknown-impactはdiagnostic付きでsafe verdictを禁止する．任意副作用の捏造も禁止する． |

trigger subsetにはpull_requestの既定activity types、push branches、workflow_run types/workflows/branches、literal branch/path filterを含む．wildcard filterを初期版に含めない場合はunsupportedを返すfixtureを置き、拡張時にGitHub公式patternの境界値を追加する．workflow_runのrequested/in_progress/completedを混同しない．

## 3. A1～A5に関する報告区分

- Historical baseline: 保存済み結果が対象とするのは現行実装と固定YAMLである．この新方式の実行結果として再掲しない．
- Fixture-preservation: 新方式が各YAMLの実step、条件、Artifact object、dataflow、markerをどう表すかを確認する．
- Generalized capability: 任意YAMLへ一般化できるとは、同じIR rule/contractを異なる構文例に適用し、trace、source map、unsupported診断を含めて確認した後でのみ主張する．
- Real-world authority: 現行A1/A5のmarkerは実publish/updateではない．実効permissionsと操作Action/command contractが別途そろわない限り、実被害propertyの成立としない．

## 4. 受入基準

プロトタイプのrelease candidateは以下すべてを満たす．

1. 必須fixture matrixを自動実行し、期待state transition、property結果、trace source mapping、diagnosticを固定fixtureと比較する．
2. 不正YAML、duplicate key、needs cycle、unknown expression/action、unsupported shellがfalse/safeへ丸められない．
3. A1とA3をdownloadだけで区別せず、A1はcontent-dependent use/simulated sink edgeを持ち、A3は持たない．
4. A2のverification barrierはtrusted origin、same object、prior order、mismatch stop、後続guardの相関が揃う時だけ成立する．
5. A5のmetadata outputとsink間のdependency edgeが保持される．
6. upload成功前のobject存在、queryに一致しないobjectのdownload成功、distinct run条件を欠くcross-run property等の不可能stateが生成されない．
7. 同一context/step output/object tokenの相関を示すmutation testが通る．
8. unsupportedを含むproperty sliceでsafe-within-modelを出さず、診断に原因step/action/spanを含める．
9. NuSMV反例traceの各stepが元YAMLのfile/job/step/lineへ戻る．生成内部stateにはYAML nodeを捏造せずinternal labelを示す．
10. SMV parse/model-checkに失敗した場合はsuccess扱いせず、失敗診断を保存する．Container内に必要依存が無ければホストへ導入せず、未実施と記録する．

既存評価結果や論文の変更はこの受入条件に含めない．新方式の出力を評価へ追加する場合は別途変更許可と再現手順を定める．
