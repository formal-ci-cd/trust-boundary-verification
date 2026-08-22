/**
 * @name Export GitHub Actions workflow structure
 * @description GitHub Actionsの内部表現から，形式モデルの入力候補となる要素を列挙する．
 * @kind table
 * @id formal-ci-cd/actions-model/workflow-structure
 */

import actions

private string getWorkflowName(AstNode node) {
  result = node.getEnclosingWorkflow().getName()
}

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

private string getStepId(Step step) {
  result = step.getId()
  or
  not exists(step.getId()) and
  result = "-"
}

private string getEventTrust(Event event) {
  event.isExternallyTriggerable() and
  result = "externally-triggerable"
  or
  not event.isExternallyTriggerable() and
  result = "not-externally-triggerable"
}

private string getEventPrivilege(Event event) {
  event.isPrivileged() and
  result = "privileged"
  or
  not event.isPrivileged() and
  result = "not-privileged"
}

private string getModelArgumentName() {
  result = [
      "ref", "repository", "path", "key", "restore-keys", "persist-credentials",
      "allow-unsafe-pr-checkout", "lookup-only", "name", "run-id", "github-token",
      "artifact-ids", "pattern", "merge-multiple", "if-no-files-found", "retention-days"
    ]
}

private predicate describeNode(AstNode node, string kind, string name, string detail) {
  exists(Workflow workflow |
    node = workflow and
    kind = "workflow" and
    name = workflow.getName() and
    detail = "-"
  )
  or
  exists(Event event |
    node = event and
    kind = "event" and
    name = event.getName() and
    detail = getEventTrust(event) + ";" + getEventPrivilege(event)
  )
  or
  exists(LocalJob job, string runnerLabel |
    node = job and
    kind = "job" and
    name = job.getId() and
    runnerLabel = job.getARunsOnLabel() and
    detail = "runs-on=" + runnerLabel
  )
  or
  exists(UsesStep step |
    node = step and
    kind = "uses-step" and
    name = getStepId(step) and
    detail = step.getCallee() + "@" + step.getVersion()
  )
  or
  exists(UsesStep step, string argumentName |
    node = step and
    argumentName = getModelArgumentName() and
    exists(step.getArgument(argumentName)) and
    kind = "uses-argument" and
    name = argumentName and
    detail = step.getArgument(argumentName)
  )
  or
  exists(Run step, string command |
    node = step and
    kind = "run-step" and
    name = getStepId(step) and
    command = step.getScript().getACommand() and
    detail = command
  )
  or
  exists(Step step |
    node = step and
    not step instanceof UsesStep and
    not step instanceof Run and
    kind = "step" and
    name = getStepId(step) and
    detail = "-"
  )
  or
  exists(Permissions permissions, string permission |
    node = permissions and
    kind = "permission" and
    name = "permission" and
    permission = permissions.getAPermission() and
    detail = permission
  )
  or
  exists(Expression expression |
    node = expression and
    kind = "expression" and
    name = expression.getRawExpression() and
    detail = expression.getNormalizedExpression()
  )
}

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
