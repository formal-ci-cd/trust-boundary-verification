# 次期 YAML→SMV 変換器：最小意味論仕様

Status: 初期設計案．2026-10-22時点の対象checkoutには、この設計案を修正する元文書 docs/next-model-design/minimal-semantics.md と、必読指定の docs/next-model-design/current-model-audit.md が存在しなかった．そのため本書は既存仕様の改訂ではなく、依頼文と参照できた docs/threat-model.md、docs/scope-and-extraction.md、および現行A1～A5 YAMLから新規に記述した提案である．欠落した監査文書の内容は推測していない．

## 1. 目的と判定対象

入力workflowを、構文と実行順序を保つIRへ変換し、そのIRから有限状態機械を生成する．Python frontendは構文検査、静的な定数評価、参照解決、unsupported診断までを行うが、危険経路の最終Boolean判定を作らない．状態到達性はSMVとNuSMVで判定する．

### 静的処理と状態探索の境界

構文妥当性、needs graphのcycle検査、literal workflow/action名の照合、固定文字列や型の比較、Artifact候補集合の生成は静的処理で済ませる．これらのためだけにNuSMVを呼ばない．event payloadが未確定ならfilter predicateをIRへ残し、Pythonでtrigger成立を確定しない．名前一致はcandidate生成であり、object同一性ではない．

NuSMVへ渡す対象は、job/step実行順、並行jobのinterleaving、workflow_runとproducer runの時相関係、Artifact保存前後と選択ID、条件分岐、検証・利用・sink到達、unknown outcomeを含む実行列である．この分離によって、モデル検査が必要な状態探索と単純な静的検査を区別する．

中心となる実被害propertyは、次の全条件が同一の可能な実行列で成立することとする．

1. pull_request等の未信頼sourceがArtifactの対象byte又はmetadataへ影響する．
2. Producerがuploadを実行し、保存に成功する．
3. 別workflow又は別runのConsumerが、Producerが保存した後に、同一Artifact objectを取得する．
4. 取得objectに由来する値が後続処理へdata dependency又はcontrol dependencyとして流れる．
5. 同一object/valueに対する利用前検証が、信頼済み基準への一致によって未信頼provenanceを除去していない．照合失敗で停止した場合もsinkへ到達しない．
6. 未信頼provenanceが残る値に依存した処理が、対象権限を必要とするsinkへ到達し、モデル上の実効authorityがそのsinkに与えられている．

Propertyの概略は次の通りである．

    bad_real :=
      source_taint_reaches_object
      & upload_succeeded
      & consumer_run_is_distinct
      & download_succeeded
      & downloaded_object_id = uploaded_object_id
      & artifact_dependent_use
      & untrusted_value_reaches_sink_after_verification
      & content_dependent_sink_reached
      & effective_authority_for_sink

    CTLSPEC AG !bad_real

これは宣言した有限モデル内の到達可能性であり、外部token、実publish成功、攻撃成立、任意shellの意味を証明しない．モデル外のcredential又はActionがproperty sliceに残る場合はsafe verdictを出さない．

研究用markerは実sinkと区別する．stateには少なくとも次を別々に持つ．

- sink_site_reached：内容依存の処理がsink位置へ到達した．
- sink_kind：repository_write、publish、deploy、oidc等の要求authority種別．
- effective_authority：GITHUB_TOKENのworkflow/job設定からモデル化できる権限．
- simulated_sink：研究用markerへ到達しただけで、実操作は行っていない．
- integrity_verified_object_id / integrity_rejection_stopped_execution：検証結果と停止を同じobject/valueへbindする．成功したtrusted checkは明示したcollision-resistance assumptionの下で検証対象valueのuntrusted lineageを除去する．
- authority_operation_succeeded：外部認可を含む実操作成功．最小prototypeではunknown又はunsupportedであり、sink到達から推論しない．

従って、simulated_sink用の補助propertyはA1/A5等のfixtureの経路比較に使えるが、実被害propertyの代替にしない．現在のA1/A5はcontents:readと研究用markerを使うため、同fixtureから実repository write authorityがあるとは結論しない．この区別は docs/threat-model.md の成立条件（特に5–37行）と docs/scope-and-extraction.md のA1～A5記述（43–50行）に合わせる．

## 2. 入力構文とTyped IR

### 2.1 YAML frontend

安全なYAML parserは元のYAML node、scalar種別、workflow/job/stepの階層、行・列spanを保持する．YAML 1.1 loaderがonをBooleanに変える差異を避け、top-levelのonを文字列keyとして扱う．重複key、型違い、壊れたneeds参照、循環needsは黙って正規化せずdiagnosticを出す．

IRは最低限、次の情報を保持する．これは説明用の概念schemaであり、直ちに特定のJSON形式へ固定しない．

- WorkflowIR: workflow名、trigger集合、workflow permissions、jobs、source fileとnode span．
- TriggerIR: event名、activity type、branch/path filter、filter対象context、既定値が適用された根拠．
- JobIR: job id、needs集合、if AST、job permissions、runs-onの原文、ordered steps．
- StepIR: YAML上のindexとid、uses repository/ref又はrun本文、with/env、if AST、step outcome、Action/Shell contract id、node span．
- ExprIR: literal、reference、unary、binary、status-check、interpolationを型付きASTとして保持する．
- ValueIR: 値domain、producer expression、data/control dependency、taint provenance、Artifact object origin、trust origin、knownnessを保持する．
- ContractIR: 固定Action又は限定Shell構文の入力・出力・状態効果・失敗効果・完全性・sink effectと、そのcontractを適用するsource nodeを保持する．
- Diagnostic: known / unknown / unsupported / invalidの分類、理由、property relevance、source spanを保持する．

IRにprecomputed unsafe/safe Booleanを入れない．同一のcontext参照はcanonical symbolへ解決し、別の式に出た同一参照も同じsymbolを使う．step outputは定義stepが実行された時に一度値を設定し、後続参照は同じoutput stateを読む．

### 2.2 最小trigger subset

初期subsetはpull_request、push、workflow_runとする．複数eventはORである．各eventに付くfilterはそのevent専用に保持し、workflow全体で混ぜない．

| YAML | 初期意味論 |
|---|---|
| pull_request | activity typeは明示typesのいずれか．省略時のGitHub既定値はopened、synchronize、reopened．branchesはPRのbase/target branchを照合する．初期実装はliteral branch名の一致を対象にする． |
| push | branchesはpush先branch名を照合する．初期実装はliteral branch名の一致を対象にする．typesは使わない． |
| workflow_run | workflowsはtrigger元workflow名のOR集合、typesはrequested/in_progress/completedのOR集合．省略時はGitHubの既定activity type集合を使う．branchesはtrigger元workflow runのbranchに適用する．consumer workflowの実行開始時点をproducerの完了と同一視しない． |

GitHubはpull_request、push、workflow_runそれぞれのactivity、filter、defaultを定義している．初期subsetで厳密に評価できないpattern構文はunknown/unsupportedとし、branchが一致すると仮定しない．wildcard、否定patternの順序評価は直近拡張とし、実装時に公式pattern semanticsと照合する．

現行A1 producerにはliteral paths filterがあり、A5 producerにはbranches: [main]がある．A1～A5をfixtureとして保つ場合、初期subsetにはliteral paths/paths-ignoreも含め、event payload上のfile-changed[path]を一度だけ選び、同じpath参照を全filterで共有する．pathsを含むtrigger条件は全filterが成立したときのみ有効である．glob path、tags、pathsとbranchesの未対応組合せはunsupportedとする．

workflow_runについては以下を明示する．

- workflows、types、branches filterに一致したproducer run eventだけがconsumer runを生成する．workflowsの複数要素はORである．
- completedはproducerがterminal stateになった後に発生し、producer conclusionがfailureでもtrigger自体は成立し得る．job/stepのifがsuccessを要求するかは別に評価する．
- requested/in_progressはproducer完了前に発生し得る．consumerはproducerと並行に実行できる．download successは、そのtransitionより前に保存され、かつquery・permission条件を満たすobjectに限る．後から保存されたobjectを過去のdownloadへ遡及的に結び付けない．
- workflow_run.idはtrigger元producer run idと同じcanonical symbolとして扱う．download-artifactのrun-idに同じ式があればその値参照を保存する．
- workflow_run workflow fileがdefault branchに存在すること、chain depth上限、repository設定等は入力YAMLだけから必ずしも確定しない．repository snapshotで証明できない制約はenvironment symbol又はunknownとして保持する．二workflowのbounded prototypeを超えるchainはunsupportedとする．

### 2.3 jobs、needs、steps、if

jobはYAML job_id単位に一つ作る．needsに挙がるjobが全て成功した後にjobが開始可能になる．依存jobがfailure/skipped/cancelledの場合、公式のjob条件規則に従いdependent jobがskipされる．ifがstatus-check functionを含まない場合の暗黙success()もjob/step双方で表現する．初期subsetのstatus checkはsuccess()、failure()、cancelled()、always()とし、意味を実装・conformance caseで固定する．未対応function、object filter、format/string coercionの未実装ケースは評価不能を返し、falseへ変換しない．

job内のstepはYAML順に一つずつ進む．step開始条件はstep ifと直前までのjob statusに従う．skip、failureは実行状態として区別する．continue-on-error、timeout、parallel step、matrix、reusable workflow、containerによるrunner差異は初期subset外であり、property sliceへ影響する場合はunsupportedとする．並行jobは静的needsグラフに従い、各transitionで有効なjobの一つをschedulerが選ぶinterleavingとして表す．これはstepを追加することではなく、実行順序を表すモデル上の選択である．

初期expression ASTは、Boolean literal、string literal、括弧、!、&&、||、==、!=、status-check、次の参照を対象にする．

- github.event_name
- github.event.workflow_run.id、.name、.event、.conclusion、.head_branch
- 対象triggerのactivity、branch、pull_request base/headのうち実際にfixtureが参照するfields
- needs.<job_id>.result / needs.<job_id>.outputs.<name>
- steps.<step_id>.outputs.<name>

GitHub式の比較はcase-insensitive等の公式規則があるため、Pythonの通常比較へ置き換えない．初期subsetで正確に実装していない型変換、index、contains、startsWith、fromJSON等はunsupportedとする．初期の==/!=は有限domainが定義できるcontext値とliteral、又は同一canonical symbol間だけを対象にする．任意の二つの未確定文字列の比較は、関係をsoundに表せない限りunsupportedとする．NuSMV用string abstractionは比較に現れるliteral、unset/null、sourceごとのother classを使い、別々のunknown symbolのotherを一つの実文字列へ誤って同一化しない．${{ ... }} interpolationもliteral部とExprIRを区別し、全体を一つの不透明文字列にしない．

## 3. ArtifactとAction contract

### 3.1 Object identityとtaint

artifact nameは検索条件であり、object identityではない．IRの各upload siteとbounded run instanceから有限object tokenを割り当てる．tokenは保存成功transitionでのみ存在する．tokenには少なくともrun instance、upload site、name、path/content provenance、action contract ref、利用可能性、digest knowledgeを結び付ける．

downloadはrun-id/name/path/tokenを評価し、現在存在するcandidate object集合を構成する．name一致は候補生成に限り、成功transitionでselected object tokenを一つ記録する．same_objectはupload tokenとdownloaded tokenのidentity equalityで定義する．実際のartifact-id/digestを静的に知らない場合も、有限モデル上の同一性tokenを保ち、name一致を実体同一の証拠と取り違えない．同名候補が複数なら選択は候補集合内に制限したnondeterministic choiceとし、Action仕様から一意選択を証明できない場合はambiguous/unknownを出す．候補がない時に成功する遷移を作らない．

taintはBoolean一個へ早期縮約せず、SourceId → ValueIR → artifact object → downloaded view → step output/env/argument → sinkのprovenance edgeとして保持する．SMVでは対象fixtureに存在する有限SourceId/ObjectIdについてbitset又はfinite enumで符号化できる．A5のhead-ref、head-sha、distは同一object originを持つ別fieldとして保存し、step outputを通るvalue edgeを残す．

### 3.2 Upload/downloadの状態規則

actions/upload-artifactとactions/download-artifactはusesの実参照に対するversioned contractで扱う．初期実装は監査済みimmutable refと対応するaction contractを必須とし、単なるコメントのv4表記を実体確認の代わりにしない．未登録refはunsupportedである．

upload transition:

    pre: stepがenabled、name/pathを解決、contractが適用可能
    choose: upload outcome = success | failure（外部service/filesystemのunknownを表す）
    success: object tokenをartifact registryへ追加し、path由来のdata provenanceを保存
    failure: objectを追加しない。actionがstep failure扱いにする条件は当該ref/input contractに従う

download transition:

    pre: stepがenabled、run-id/name/repository/tokenを解決
    success許可条件: queryに一致するobjectが既に存在し、必要なcredential/permissionがある
    success: selected object tokenとdownload path viewをstateへ保存
    failure: object viewを生成しない
    同名一致のみではsuccessもsame_objectも確定しない

upload/download success/failureは有限環境選択として保持する．unknownなservice結果は同一transitionの選択結果としてstateへlatchし、その後のifやoutput参照では同一結果を使う．upload失敗後に同じstepの成功結果を別conditionが独立に選ぶことは許さない．Artifact retention、同一runへの重複upload/overwrite、複数repository、動的run-id/name、pattern/merge-multipleは初期subset外又は別contractである．影響しないと証明できない場合はunsupported/unknownとする．

### 3.3 対象Actionとwith

初期builtin contractは以下を定義する．

- actions/upload-artifact: name、path、if-no-files-found（A1/A5 fixtureで使うerrorを含む）、保存成功/失敗、artifact-id/digest outputのknownness．retention-daysは原文を保持し、時間をモデル化しないprototypeでは可用性unknownを出せるようにする．
- actions/download-artifact: name、path、run-id、github-token、repositoryの既定値と入力有無．別runのArtifact downloadではgithub-tokenとtarget repoへのactions:readが必要な条件をcontract化する．この権限はdownload capabilityであり、contents:write等のsink authorityとは別である．
- actions/checkout: audited refに対するcheckout内容とworkspace pathのsource semanticsを明示したcontractがある場合のみ、file provenanceを構成する．ref、repository、persist-credentials等に未知の式があれば影響するsource/credentialをunknownにする．

Action contractは入力、出力、data/control dependency、filesystem/environment effect、authority effect、success/failure、taint transformation、source versionを列挙する．setup-node等は存在するだけで全workflowを拒否しないが、無条件にharmlessとはしない．contractとproperty dependency sliceから対象外と証明できたActionのみproven-irrelevantとする．tainted workspace、条件、Artifact、credential、sinkに接続し得る未対応Actionはproperty-relevant-unsupportedとする．影響範囲を絞れないActionはunknown-impactであり、これらが残る解析ではsafe verdictを出さない．「全Actionに任意の副作用」を追加して見かけ上保守的にする方式も採らない．

## 4. run本文とvalue flow

任意shellの一般意味解析は行わない．最小prototypeでは次の二方式を併用する．

1. versioned finite Shell recognizer：A1～A5 fixtureに実在する、完全一致の限定command grammarだけを決定論的に解釈する．対象idiomはcheckout内literal fileからartifact stagingへの限定cp、Artifact fileのtrによる改行除去、sha256sumとliteral trusted digest比較、固定文字列predicate、case/testによる限定validation、GITHUB_OUTPUTへの特定output設定、GITHUB_STEP_SUMMARYへの研究marker出力など．各ruleはcommand、許容順序、exit/status、read/write path、data/control dependency、対応source spanを記載する．
2. 明示StepContract：解析対象外の既存stepに対して、信頼されたsidecar contractをworkflow path + job id + stable step source_id（step idが無ければYAML indexを含む）+ source hashへbindする．contractはそのstepのread/write、入力と出力、taint、success/failure、integrity、sink effectを宣言する．YAMLに存在しないstepは生成しない．contract自身とbinding元をtraceへ記録する．

run本文の一部でも未認識token、subshell、command substitution、redirection、pipeline、dynamic path、control flowがrule外なら、そのstepのproperty-relevant effectを安全と推定しない．そのstepがsource-to-object/use/verify/sink sliceに関係し得るときはunsupportedとし、単に効果がない状態遷移を作ってsafeにしない．反例探索用の明示的 over-approximationを導入する場合、そのtraceをcandidate/spurious-capableと明示し、unsupportedを隠さない．

IRは将来のstep outputs、GITHUB_OUTPUT、env、Artifact由来値、job outputs、needs outputsの伝播を表せるValueIR graphを初期から持つ．初期subsetに無いdataflowは未実装/unsupportedとして残す．Shell parserが読み取れない場合もこのgraphへ「安全」edgeを入れない．

integrity barrierは、(a)信頼originがPR/Artifactとは独立したexpected value、(b)検証対象が同じdownloaded object/value、(c)検証がuseより前、(d)不一致時にuseがskip/stop、(e)結果outputがその後のguardと同一symbol、を全て確認して初めて有効とする．sha256sumの存在やArtifact actionがdigestを出すことだけではtrusted verificationとしない．暗号学的collision resistanceは明示assumptionとして記録する．

## 5. Permission semantics

workflow-level permissionsを初期権限mapとし、job-level指定を公式のscope規則で適用する．明示permission mapの省略項目はnoneとなる（read/write-allはそれぞれ全scopeへ展開）．workflow/job双方が省略された場合はrepository/organization/enterprise defaultに依存するため、設定が与えられない限りunknownとする．fork pull_requestでwrite tokenがreadへ制限される例外設定等、repository policyに依存する有効権限もpolicy inputが無ければunknownとする．

権限はscopeごとのread/write/noneとして持ち、writeはread capabilityを含む．Artifact取得に使うactions:readと、repository更新のcontents:write、publish/deploy/OIDC等を個別に表現する．permissions declarationはoperation実行そのものではない．sink site到達、authority available、authority operation successを別state/claimにする．PAT、外部secret token、cloud credentials、environment protectionは、明示した信頼入力やAction contractがなければunknownであり、GITHUB_TOKEN設定から推測しない．

## 6. SMV状態と遷移

### 6.1 生成するstate

有限モデルはworkflow YAML nodeとbounded run instanceから構成する．各SMV symbolはIR idへ逆引き可能にする．最低限のstateは次の通りである．

- 各runのevent/activity/branch/workflow_run source relationとrun status．immutableなcontext値はFROZENVAR相当又は初期選択後固定されたstateとする．同一context参照は必ず同一symbolを使う．
- 各job status（not_started/runnable/running/success/failure/cancelled/skipped）とstep program counter/status．PCはYAMLの実step index/IDとterminal control stateだけを表し、実在しないworkflow stepを挿入しない．
- bounded Artifact slotごとのpresent、object id、name、producer run、upload site、taint/value provenance、availability、digest-knownness．
- download selected object id/status、download path view、required permission/capability．
- step/job output valueとpresence、source lineage、trust origin．値が未設定、false、空文字、unknownを混同しない．
- verification stateはobject/value idにbindし、verified / mismatch / not-run / unknownを区別する．
- artifact_dependent_use、simulated sink reached、real sink reached、authority available、actual operation resultを別々に記録する．
- scheduler selectionは有効job/runの遷移のうち一つを選ぶ．terminal stateではstutterを許す．

FROZENVARはrun中変化しない入力条件（例：event_name、trigger payload、repository policy）へ使う．Artifactの保存結果、download結果、step output等の実行状態をFROZENVARへ押し込まない．DEFINEはsame_object、enabled、effective guard、bad property等の導出式だけにし、Pythonで判定した結果の置場にしない．INIT/ASSIGN/TRANSまたは等価SMV構造で初期値と遷移を定義する．未知の一回限り結果はtransition時に選択しstateへ保存する．

### 6.2 有効transition

1. Trigger: frozen eventとtrigger predicateが一致したときだけrunを作る．不成立ならそのworkflow runは作らない．
2. Job start: needs predecessorが成功し、job if/default status guardがtrueの時にrunnableにする．skip/failure条件はjob statusへ反映する．needsが無いjob同士は並行可能で、schedulerはどのrunnable jobからも一歩選べる．
3. Step start: 同一jobの前stepが終わり、if/default status guardがtrueなら、そのYAML stepを一度実行する．falseなら当該stepをskippedにして次へ進む．
4. Action/Shell effect: builtin contract又はrecognized shell ruleだけを適用する．成功/失敗がunknownならそのtransitionで選び、Actionとrunnerの定義する失敗後制御を守る．
5. Workflow completion: 全jobがterminalになってrun conclusionを決定する．workflow_run completedはこの後だけ生成する．requested/in_progressは対応するrun phaseで生成し、consumerとのinterleavingを許す．
6. Download selection: Artifact registryのその時点のquery candidateに限ってselected IDを設定する．producer uploadがまだ実行されていない物の成功選択は禁止する．
7. Use/verify/sink: data/control dependency edgeと同じobject idを経由した遷移だけがproperty flagを設定する．内容と無関係の固定出力はartifact useとしない．

### 6.3 Nondeterminismと相関

有限選択は根拠のある外部不確実性（event payload、Action成功、upload/download service、trusted policy unknownなど）にだけ導入し、そのdomain/source/reasonをIR diagnosticとtrace manifestに保持する．同じgithub.event.workflow_run.id等を参照するifとwithは同一symbolである．同じstep outputの複数参照も同一state valueである．Artifact IDはupload成功時に割り当てたtokenであり、同じnameだけから別tokenを同一objectへ統合しない．

二つの異なるunknownを無条件に同じ値へ同一化することも、同じunknown参照を各箇所で独立に真偽選択することもしない．必要な関係（例：同一Artifactの複数field、digestとobject、verify結果と後続if）はValueIR/contractで保持し、SMV制約へ写像する．

## 7. known / unknown / unsupported

| 状態 | 意味 | verdictへの影響 |
|---|---|---|
| known | 対応規則で意味が確定、又は指定した抽象化の範囲内で関係が定義済み | 通常モデルへ変換する |
| unknown | 構文/意味の種類は認識するが、実値や外部結果が未観測（例：upload service outcome、repo default permission） | sourceごとの有限nondeterministic symbolとして保持する．相関と制約を保つ |
| unsupported | 構文又は効果の意味規則が未実装で、property sliceに関係し得る | safe-within-modelを出さない．必要に応じcandidate traceを参考出力するがunsupported付きにする |
| invalid | subset内の構造違反（壊れたneeds、型違い等） | 変換を失敗し、verdictを生成しない |

unknownはfalseでもharmlessでもない．unsupportedは未知値へ無条件に置換して本来ない副作用を足すことではない．unsupportedがproperty slice外であることをcontract/dependency graphで証明できる場合だけproven-irrelevantとして除外する．それ以外はunsupported verdictとする．safe-within-modelは、全property-relevant nodeがsupportedで、unknownが制約付きover-approximationとしてモデル化され、NuSMVでcounterexampleが無い場合に限る．反例はconditional counterexampleとし、traceに選ばれたunknown valuationを列挙する．

## 8. Counterexampleと元YAML source map

各YAML nodeにstable source_id（relative workflow path、job id、step id又はindex、YAML path）とstart/end line/columnを付す．生成されたSMV variable、guard、transition、DEFINE、propertyへsource_id集合を紐付け、sidecar manifest（例：model.map.json）をSMVと同じ生成物として保存する．

- job/step PCのtrace値は実際のYAML job/step nodeのspanへ対応する．
- if由来guardの分岐はif expression ASTのspanと参照fieldのspanを併記する．
- upload/download state updateはuses node及びname/path/run-id/token各input spanを併記する．
- dataflow/use/sink traceは元step span、適用したShell rule又はcontract id/span、taint lineageを示す．
- FROZENVAR/unknown choiceは外部入力又は未確定Action outcomeの発生源を示す．
- scheduler、terminal、Artifact registryは内部モデルstateとして明記し、架空のYAML stepのsource位置を捏造しない．

line mapが無い／staleでtraceを元入力へ復元できないモデルは受入対象外とする．

## 9. 最小Producer / Consumer例

次は意味論を示す例であり、action refのplaceholderを実運用へそのまま使うものではない．実装では監査済みimmutable refをcontract registryへ登録する．checkout contractはpull_request event下で入力fileをPR由来workspace値として結び付ける．

~~~yaml
name: Demo Producer
on:
  pull_request:
    types: [opened, synchronize, reopened]
    branches: [main]
permissions:
  contents: read
jobs:
  produce:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@AUDITED_IMMUTABLE_REF
      - id: upload
        uses: actions/upload-artifact@AUDITED_IMMUTABLE_REF
        with:
          name: request
          path: .research-artifact-input/payload.txt
          if-no-files-found: error
~~~

~~~yaml
name: Demo Consumer
on:
  workflow_run:
    workflows: ["Demo Producer"]
    types: [completed]
permissions:
  actions: read
  contents: read
jobs:
  consume:
    if: ${{ github.event.workflow_run.conclusion == 'success' &&
            github.event.workflow_run.event == 'pull_request' }}
    runs-on: ubuntu-latest
    steps:
      - id: download
        uses: actions/download-artifact@AUDITED_IMMUTABLE_REF
        with:
          name: request
          path: ${{ runner.temp }}/artifact
          github-token: ${{ secrets.GITHUB_TOKEN }}
          run-id: ${{ github.event.workflow_run.id }}
      - id: inspect
        env:
          ARTIFACT_FILE: ${{ runner.temp }}/artifact/payload.txt
        run: |
          set -eu
          request=$(tr -d '\r\n' < "$ARTIFACT_FILE")
          if [ "$request" = "publish=true" ]; then
            printf 'publish=true\n' >> "$GITHUB_OUTPUT"
          else
            printf 'publish=false\n' >> "$GITHUB_OUTPUT"
          fi
      - name: Research-only simulated sink
        if: steps.inspect.outputs.publish == 'true'
        run: printf 'dummy_publish_authority_reached=true\n' >> "$GITHUB_STEP_SUMMARY"
~~~

SMV概略（実装可能な完全SMVではなく、state/constraintの対応を示す）：

~~~smv
MODULE main
FROZENVAR
  producer_event : {pr_event, push_event};
  producer_pr_activity : {opened, synchronize, reopened};
  producer_base_branch_main : boolean;
  producer_run_id : {producer_run_1};
  consumer_run_id : {consumer_run_1};

VAR
  producer_pc : {p_start, p_checkout, p_upload, p_done};
  consumer_pc : {c_not_created, c_start, c_download, c_inspect, c_simulated_sink, c_done};
  producer_status : {queued, in_progress, completed_success, completed_failure};
  artifact_present : boolean;
  artifact_object_id : {no_object, object_from_upload_1};
  artifact_tainted : boolean;
  downloaded_object_id : {no_object, object_from_upload_1};
  download_status : {not_run, failed, succeeded};
  publish_output : {unset, publish_true, publish_false};
  artifact_used : boolean;
  trusted_integrity_blocked : boolean;
  untrusted_value_at_sink : boolean;
  simulated_sink_reached : boolean;
  effective_contents_write : boolean;

DEFINE
  trigger_matches :=
    producer_event = pr_event
    & producer_pr_activity in {opened, synchronize, reopened}
    & producer_base_branch_main;
  same_object :=
    downloaded_object_id != no_object
    & downloaded_object_id = artifact_object_id;
  bad_simulated_path :=
    artifact_tainted & same_object & artifact_used
    & untrusted_value_at_sink & !trusted_integrity_blocked
    & simulated_sink_reached;
  bad_real :=
    bad_simulated_path & effective_contents_write;

-- TRANS constraints forbid artifact_present before p_upload succeeds.
-- c_download may succeed only if the matching object is already present,
-- producer/consumer run IDs differ, and actions:read/token requirements hold.
-- publish_output is derived from the downloaded content by the recognized
-- shell rule; the later if reads this same latched output symbol.
-- untrusted_value_at_sink is computed from provenance after any trusted
-- verification transformation; it is not source_taint_reaches_object alone.
CTLSPEC AG !bad_real
CTLSPEC AG !bad_simulated_path
~~~

この例はpermissionsがcontents:readであるため、simulated pathと実authority propertyを別結果にする．payloadがpublish=trueで、upload/downloadが成功すればbad_simulated_pathの反例候補があり得るが、これを実publish到達又は実被害とは呼ばない．実被害propertyの判定には対応sink semanticsと有効authorityの双方が必要である．A1～A5の既存保存結果をこの将来モデルが再現したとは扱わない．

## 10. 公式意味論の参照

- [Workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)：event filters、jobs/needs、permissions、step構文．
- [Events that trigger workflows](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)：pull_request/push/workflow_runのtypes、workflow_run完了とconclusionの区別、default branch条件、chain制約．
- [Expressions](https://docs.github.com/en/actions/reference/workflows-and-actions/expressions)：operator、型変換、status checkのdefault．
- [Contexts](https://docs.github.com/en/actions/reference/workflows-and-actions/contexts)：github、needs、steps contextと各field．
- [Secure use](https://docs.github.com/en/actions/reference/security/secure-use)：workflow_runとuntrusted artifactの取扱い．
- [actions/upload-artifact](https://github.com/actions/upload-artifact) と [actions/download-artifact](https://github.com/actions/download-artifact)：versionごとのinput/output、別run取得、artifact id/digest．
- [actions/checkout](https://github.com/actions/checkout)：refとcheckout対象のaction contract．

GitHub DocsとAction metadataは更新されるため、実装時には対象GitHub platform、Action immutable ref、参照日、contract versionを固定する．文書間に差異があれば推測で補わず、対象platform確認まで該当構文をunsupportedとする．
