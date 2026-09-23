/**
 * @name Export GitHub Actions workflow structure
 * @description GitHub Actionsの内部表現から，形式モデルの入力候補となる要素を列挙する．
 * @kind table
 * @id formal-ci-cd/actions-model/workflow-structure
 */

import actions

/** 対象nodeが属するワークフロー名を取得する．CSV上で行をワークフローごとに分けるために使用する． */
private string getWorkflowName(AstNode node) {
  result = node.getEnclosingWorkflow().getName()
}

/**
 * 対象nodeがjob自身ならそのIDを返し，job内のnodeなら親jobのIDを返す．
 * ワークフロー全体の要素など，jobに属さない場合は欠損ではなく"-"を出力する．
 */
private string getJobId(AstNode node) {
  node instanceof Job and
  result = node.(Job).getId()
  or
  not node instanceof Job and
  result = node.getEnclosingJob().getId()
  or
  not node instanceof Job and
  not exists(node.getEnclosingJob()) and
  result = "-"
}

/**
 * 対象nodeが属するstepの順番を0始まりで取得する．
 * Actionの引数や式も，親stepと同じ番号へまとめるために使用する．
 */
private string getStepIndex(AstNode node) {
  exists(Step step, StepsContainer container, int index |
    (node = step or not node instanceof Step and step = node.getEnclosingStep()) and
    step.getContainer() = container and
    container.getStep(index) = step and
    result = index.toString()
  )
  or
  not exists(node.getEnclosingStep()) and
  not node instanceof Step and
  result = "-"
}

/** YAMLでstepのidが省略されている場合も，CSVの列数を一定にするため"-"を返す． */
private string getStepId(Step step) {
  result = step.getId()
  or
  not exists(step.getId()) and
  result = "-"
}

/** CodeQLの判定を使い，外部から起動可能なeventかを文字列へ変換する． */
private string getEventTrust(Event event) {
  event.isExternallyTriggerable() and
  result = "externally-triggerable"
  or
  not event.isExternallyTriggerable() and
  result = "not-externally-triggerable"
}

/** CodeQLの判定を使い，default branch側の権限で動くeventかを文字列へ変換する． */
private string getEventPrivilege(Event event) {
  event.isPrivileged() and
  result = "privileged"
  or
  not event.isPrivileged() and
  result = "not-privileged"
}

/**
 * 形式モデルへ必要なAction引数だけを抽出対象とする．
 * 全てのwith引数を出すのではなく，checkout，cache及びartifactの対応付けに使う値へ絞る．
 */
private string getModelArgumentName() {
  result = [
      "ref", "repository", "path", "key", "restore-keys", "persist-credentials",
      "allow-unsafe-pr-checkout", "lookup-only", "name", "run-id", "github-token",
      "artifact-ids", "pattern", "merge-multiple", "if-no-files-found", "retention-days"
    ]
}

/**
 * 1個のCodeQL nodeを，CSVへ出す種類，名前及び詳細へ変換する．
 * このpredicateは危険性を判定せず，後続のモデル生成に必要な静的構造だけを列挙する．
 */
private predicate describeNode(AstNode node, string kind, string name, string detail) {
  // ワークフロー自身を1行として出し，名称を後続処理の識別子にする．
  exists(Workflow workflow |
    node = workflow and
    kind = "workflow" and
    name = workflow.getName() and
    detail = "-"
  )
  or
  // 起動契機と，CodeQLが判断した外部起動可能性及び権限区分を出す．
  exists(Event event |
    node = event and
    kind = "event" and
    name = event.getName() and
    detail = getEventTrust(event) + ";" + getEventPrivilege(event)
  )
  or
  // workflow_runの接続先など，eventに付随する値を別の行として出す．
  exists(Event event, string propertyName |
    node = event and
    propertyName = ["workflows", "types", "branches"] and
    kind = "event-property" and
    name = event.getName() + "." + propertyName and
    detail = event.getAPropertyValue(propertyName)
  )
  or
  // jobのIDと実行環境を出す．1個のjobに複数runner labelがある場合は複数行になり得る．
  exists(LocalJob job, string runnerLabel |
    node = job and
    kind = "job" and
    name = job.getId() and
    runnerLabel = job.getARunsOnLabel() and
    detail = "runs-on=" + runnerLabel
  )
  or
  // uses stepはAction名とversionへ分けられる形で出す．
  exists(UsesStep step |
    node = step and
    kind = "uses-step" and
    name = getStepId(step) and
    detail = step.getCallee() + "@" + step.getVersion()
  )
  or
  // 選択したwith引数を，所属するjob及びstep番号と一緒に出す．
  exists(UsesStep step, string argumentName |
    node = step and
    argumentName = getModelArgumentName() and
    exists(step.getArgument(argumentName)) and
    kind = "uses-argument" and
    name = argumentName and
    detail = step.getArgument(argumentName)
  )
  or
  // run stepはシェルコマンドを出すが，コマンドの研究上の意味まではここで判定しない．
  exists(Run step, string command |
    node = step and
    kind = "run-step" and
    name = getStepId(step) and
    command = step.getScript().getACommand() and
    detail = command
  )
  or
  // usesでもrunでもないstepが存在する場合も，位置を失わないように残す．
  exists(Step step |
    node = step and
    not step instanceof UsesStep and
    not step instanceof Run and
    kind = "step" and
    name = getStepId(step) and
    detail = "-"
  )
  or
  // ワークフロー又はjobへ設定されたpermissionを1権限ずつ出す．
  exists(Permissions permissions, string permission |
    node = permissions and
    kind = "permission" and
    name = "permission" and
    permission = permissions.getAPermission() and
    detail = permission
  )
  or
  // 式は元の表記とCodeQLが正規化した表記を両方残す．
  exists(Expression expression |
    node = expression and
    kind = "expression" and
    name = expression.getRawExpression() and
    detail = expression.getNormalizedExpression()
  )
}

// describeNodeで選んだ全要素へ，元ファイル，行番号，job及びstepの位置を付けて表として出力する．
from
  AstNode node, string filePath, int line, string workflowName, string jobId, string stepIndex,
  string kind, string name, string detail
where
  describeNode(node, kind, name, detail) and
  filePath = node.getLocation().getFile().getRelativePath() and
  line = node.getLocation().getStartLine() and
  workflowName = getWorkflowName(node) and
  jobId = getJobId(node) and
  stepIndex = getStepIndex(node)
select filePath, line, workflowName, jobId, stepIndex, kind, name, detail
order by filePath, line, kind, name
