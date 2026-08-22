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


if __name__ == "__main__":
    unittest.main()
