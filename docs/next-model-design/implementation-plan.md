# 次期 YAML→SMV 変換器：実装作業単位

これはSol Midへ渡す将来の実装分割案であり、今回の作業ではコード、既存評価、論文を変更しない．順序は依存関係を表す．各段階はdocs/next-model-design/minimal-semantics.mdとverification-plan.mdを規範とする．

## 0. Branch safetyとfixture inventory

作業内容：
- 作業対象branch、HEAD、既存worktree差分を記録する．
- A1～A5 YAML、保存結果、現行抽出器を読み取り専用で棚卸しする．
- Action immutable refと適用する公式metadata/contract版を一覧化する．

受入条件：
- 未コミットの既存変更を上書きしない計画がある．
- A1～A5の「現行実装で実証済み」と「新方式の目標」を別表にする．
- 未登録action ref、必要な公式仕様、利用可能なNuSMV/containerが列挙される．

## 1. Source-preserving YAML parserとTyped IR

作業内容：
- on/jobs/needs/steps/if/uses/with/permissionsのsubset parserを追加する．on keyを文字列として扱い、node spanと原文を保持する．
- duplicate key、type error、未解決needs、cycleを診断する．
- AST、IR id、source map、knownness/diagnostic schemaを定義する．

受入条件：
- A1～A5の全対象nodeを行・列付きでIR化できる．
- parser outputに危険経路の最終Booleanを含めない．
- 未対応構文を黙って破棄せず、source span付きでunknown/unsupported/invalidを分類する．

## 2. Trigger、expression、job/step execution semantics

作業内容：
- pull_request/push/workflow_runとliteral types/branches/paths subsetを実装する．
- workflow_run run identity、requested/in_progress/completed、producer conclusion、branch relationを表す．
- if expression tokenizer/parser、canonical context symbol、status-check default、needs/job/step順序を実装する．

受入条件：
- verification-planのTrigger mismatch、upload前download、shared-context correlation fixtureが期待通りの状態遷移を持つ．
- workflow_run completedとproducer successを混同しない．
- default permission/filter/environmentが不明な場合にfalseへしない．

## 3. Artifact registry、permissionとversioned Action contracts

作業内容：
- upload/download/checkoutのaudited ref contractを登録する．
- upload success/failureとdownload query、actions:read、GITHUB_TOKEN、name候補、有限object IDを状態として扱う．
- workflow/job permissionsを有効scopeへ変換するが、外部repository policy、PAT等はunknownとして残す．
- Action effect/dependency sliceとproven-irrelevant/property-relevant-unsupported/unknown-impactを実装する．

受入条件：
- Artifact objectは成功upload transitionより前に存在しない．
- downloadはその時点のmatching candidateからしか成功せず、name一致をsame-object証明にしない．
- unregistered refや外部credentialを理由なくsupported/safeへ分類しない．

## 4. Shell subsetとValueIR provenance

作業内容：
- A1～A5で必要なlimited shell grammarを一ruleずつ実装する．
- Explicit StepContractとのどちらで扱ったかを記録する．
- file content、Artifact metadata field、env、GITHUB_OUTPUT、needs/steps output、integrity結果、sink間のdata/control provenanceを表現する．

受入条件：
- A1対A3を利用edgeとsink markerで区別する．
- A2のguardが同一object/verification outputを参照する．
- A5のmetadata→step output→simulated update flowがtraceで辿れる．
- grammar外のtoken、unreadable shell、未契約Actionがproperty sliceに残る時はsafe verdictを禁止する．

## 5. SMV generator、property、trace map

作業内容：
- IRからjob/step PC、run status、Artifact registry、permissions、outputs/provenance、guardを生成する．
- static DEFINEとstate transitionを分離する．同一unknown/canonical referenceを共有変数へlowerする．
- CTL propertyとmodel.map.jsonを出力する．

受入条件：
- SMVをNuSMV parserへ入力できる．
- AG !bad_realとfixture専用simulated sink propertyが混同されない．
- 生成変数/transition/trace stateから元YAML spanへ戻れる．
- Python側にhazard verdictを先に決めるpredicateがない．

## 6. Container内回帰、独立照合、unsupported診断

作業内容：
- 依存が既存containerに揃うか先に調べ、NuSMVと受入fixtureをcontainer内で実行する．
- SMV結果とbounded independent BFSを照合する．
- Mutation test、SMV failure handling、trace map freshness、diagnostic stabilityを確認する．

受入条件：
- verification-plan.mdの全must-have caseが通る．
- container依存不足時はテストを飛ばし、ホストへpackageを追加せず未実施理由を報告する．
- unsupportedケースでsafe-within-modelを一件も誤出力しない．
- 既存論文/評価を更新する場合は本作業から分離し、別途承認と再評価を行う．

## 依存順と停止条件

依存は 0 → 1 → 2 → 3 → 4 → 5 → 6 とする．各段階の受入条件を満たす前に次段階のverdictを信頼しない．特に4のdata/control dependencyと5のtrace mapが未完成の間は、counterexampleを根拠付きfindingとして報告せず、safe結果も出さない．実装が仕様にないGitHub Actions挙動を必要としたら、公式仕様と対象platformを確認し、確認できない箇所はunknown/unsupportedのまま設計レビューへ戻す．
