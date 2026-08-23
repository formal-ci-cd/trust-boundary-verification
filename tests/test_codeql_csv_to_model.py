import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "tools" / "codeql_csv_to_model.py"
SPEC = importlib.util.spec_from_file_location("codeql_csv_to_model", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CSV_PATH = ROOT / "results" / "codeql-actions-model-v2.26.3.csv"


class CodeQLCsvToModelTest(unittest.TestCase):
    def build(self, workflow_name):
        rows = MODULE.load_rows(CSV_PATH, workflow_name)
        return MODULE.build_model(rows, CSV_PATH)

    def test_gha_c1_keeps_checkout_and_cache_arguments(self):
        model = self.build("GHA-C1 Cache write from untrusted PR in default context")

        event = model["workflow"]["events"][0]
        self.assertEqual(event["name"], "pull_request_target")
        self.assertTrue(event["externallyTriggerable"])
        self.assertTrue(event["privileged"])

        steps = model["workflow"]["jobs"][0]["steps"]
        self.assertEqual(
            steps[0]["arguments"]["ref"], "${{ github.event.pull_request.head.sha }}"
        )
        self.assertEqual(steps[0]["arguments"]["persist-credentials"], "false")

        operation = model["sharedStateOperations"][0]
        self.assertEqual(operation["kind"], "cacheWriteIntent")
        self.assertEqual(operation["key"], "trust-boundary-c1-default-context-v1")
        self.assertEqual(operation["path"], ".research-cache-input")
        self.assertEqual(model["verificationFacts"]["sameObject"], "unknown")

    def test_gha_c2_keeps_pr_scoped_key(self):
        model = self.build("GHA-C2 PR-isolated cache")

        event = model["workflow"]["events"][0]
        self.assertEqual(event["name"], "pull_request")
        self.assertFalse(event["privileged"])

        operation = model["sharedStateOperations"][0]
        self.assertEqual(
            operation["key"],
            "trust-boundary-c2-pr-${{ github.event.pull_request.number }}-v1",
        )

    def test_gha_c2_consumer_becomes_cache_read_intent(self):
        model = self.build("GHA-C2 Default-branch cache consumer")

        operation = model["sharedStateOperations"][0]
        self.assertEqual(operation["kind"], "cacheReadIntent")
        self.assertEqual(operation["key"], "trust-boundary-c2-pr-${{ inputs.pr_number }}-v1")
        step = model["workflow"]["jobs"][0]["steps"][0]
        self.assertEqual(step["arguments"]["lookup-only"], "true")

    def test_gha_c1_observation_references_existing_operation(self):
        model = self.build("GHA-C1 Cache write from untrusted PR in default context")
        observation_path = ROOT / "model" / "observations" / "gha-c1-run-30803626815.json"
        with observation_path.open(encoding="utf-8") as source:
            observation = json.load(source)

        MODULE.add_observation(model, observation)

        runtime = model["runtimeObservations"][0]
        self.assertEqual(runtime["platformPolicy"]["cacheWriteDecision"], "denied")
        self.assertEqual(runtime["operationResults"][0]["outcome"], "failed")

    def test_unknown_operation_in_observation_is_rejected(self):
        model = self.build("GHA-C1 Cache write from untrusted PR in default context")
        observation = {
            "operationResults": [{"operationId": "cache-write:unknown:0"}]
        }

        with self.assertRaises(ValueError):
            MODULE.add_observation(model, observation)

    def test_artifact_actions_become_shared_state_operations(self):
        rows = [
            {
                "filePath": ".github/workflows/artifact.yml",
                "line": "1",
                "workflowName": "Artifact workflow",
                "jobId": "-",
                "stepIndex": "-",
                "kind": "workflow",
                "name": "Artifact workflow",
                "detail": "-",
            },
            {
                "filePath": ".github/workflows/artifact.yml",
                "line": "5",
                "workflowName": "Artifact workflow",
                "jobId": "artifact-job",
                "stepIndex": "-",
                "kind": "job",
                "name": "artifact-job",
                "detail": "runs-on=ubuntu-latest",
            },
            {
                "filePath": ".github/workflows/artifact.yml",
                "line": "8",
                "workflowName": "Artifact workflow",
                "jobId": "artifact-job",
                "stepIndex": "0",
                "kind": "uses-step",
                "name": "-",
                "detail": "actions/upload-artifact@commit",
            },
            {
                "filePath": ".github/workflows/artifact.yml",
                "line": "9",
                "workflowName": "Artifact workflow",
                "jobId": "artifact-job",
                "stepIndex": "0",
                "kind": "uses-argument",
                "name": "name",
                "detail": "build-output",
            },
            {
                "filePath": ".github/workflows/artifact.yml",
                "line": "10",
                "workflowName": "Artifact workflow",
                "jobId": "artifact-job",
                "stepIndex": "0",
                "kind": "uses-argument",
                "name": "path",
                "detail": "build/output.txt",
            },
            {
                "filePath": ".github/workflows/artifact.yml",
                "line": "12",
                "workflowName": "Artifact workflow",
                "jobId": "artifact-job",
                "stepIndex": "1",
                "kind": "uses-step",
                "name": "-",
                "detail": "actions/download-artifact@commit",
            },
            {
                "filePath": ".github/workflows/artifact.yml",
                "line": "2",
                "workflowName": "Artifact workflow",
                "jobId": "-",
                "stepIndex": "-",
                "kind": "event",
                "name": "workflow_run",
                "detail": "externally-triggerable;privileged",
            },
            {
                "filePath": ".github/workflows/artifact.yml",
                "line": "2",
                "workflowName": "Artifact workflow",
                "jobId": "-",
                "stepIndex": "-",
                "kind": "event-property",
                "name": "workflow_run.workflows",
                "detail": "Producer workflow",
            },
            {
                "filePath": ".github/workflows/artifact.yml",
                "line": "13",
                "workflowName": "Artifact workflow",
                "jobId": "artifact-job",
                "stepIndex": "1",
                "kind": "uses-argument",
                "name": "name",
                "detail": "build-output",
            },
            {
                "filePath": ".github/workflows/artifact.yml",
                "line": "14",
                "workflowName": "Artifact workflow",
                "jobId": "artifact-job",
                "stepIndex": "1",
                "kind": "uses-argument",
                "name": "run-id",
                "detail": "${{ github.event.workflow_run.id }}",
            },
        ]

        model = MODULE.build_model(rows, Path("artifact.csv"))

        write_operation, read_operation = model["sharedStateOperations"]
        self.assertEqual(write_operation["kind"], "artifactWriteIntent")
        self.assertEqual(write_operation["name"], "build-output")
        self.assertEqual(read_operation["kind"], "artifactReadIntent")
        self.assertEqual(
            read_operation["runId"], "${{ github.event.workflow_run.id }}"
        )
        self.assertEqual(
            model["workflow"]["events"][0]["properties"]["workflows"],
            ["Producer workflow"],
        )


if __name__ == "__main__":
    unittest.main()
