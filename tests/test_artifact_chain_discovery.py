import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))


def load_module(name):
    path = TOOLS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DISCOVER = load_module("discover_artifact_chains")
EVALUATE = load_module("evaluate_artifact_chains")
CSV_PATH = ROOT / "results" / "codeql-actions-model-v2.26.3.csv"
ANNOTATIONS_PATH = ROOT / "model" / "artifact-chain-annotations.json"


class ArtifactChainDiscoveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.models = DISCOVER.load_models(CSV_PATH)
        cls.candidates = DISCOVER.discover_candidates(cls.models)
        cls.annotations = DISCOVER.load_json(ANNOTATIONS_PATH)["scenarios"]

    def test_codeql_results_join_four_cross_workflow_paths(self):
        pairs = {
            (
                candidate["producerModel"]["workflow"]["name"],
                candidate["consumerModel"]["workflow"]["name"],
            )
            for candidate in self.candidates
        }

        self.assertEqual(len(pairs), 4)
        self.assertEqual(
            {consumer for _, consumer in pairs},
            {
                "GHA-A1 Unverified artifact consumer",
                "GHA-A2 Verified artifact consumer",
                "GHA-A3 Download-only artifact consumer",
                "GHA-A4 Artifact consumer without authority",
            },
        )
        self.assertTrue(
            all(candidate["pairingStatus"] == "unique" for candidate in self.candidates)
        )

    def test_pairing_requires_name_and_workflow_run_id(self):
        changed = copy.deepcopy(self.models)
        unsafe = changed["GHA-A1 Unverified artifact consumer"]
        unsafe["sharedStateOperations"][0]["runId"] = "${{ github.run_id }}"

        candidates = DISCOVER.discover_candidates(changed)

        consumers = {
            candidate["consumerModel"]["workflow"]["name"]
            for candidate in candidates
        }
        self.assertNotIn("GHA-A1 Unverified artifact consumer", consumers)
        self.assertEqual(len(candidates), 3)

    def test_duplicate_producers_are_reported_as_ambiguous(self):
        changed = copy.deepcopy(self.models)
        duplicate = copy.deepcopy(
            changed["GHA-A Producer - untrusted PR artifact"]
        )
        duplicate["workflow"]["file"] = ".github/workflows/duplicate.yml"
        changed["Duplicate artifact producer"] = duplicate

        candidates = DISCOVER.discover_candidates(changed)

        self.assertEqual(len(candidates), 8)
        self.assertTrue(
            all(candidate["pairingStatus"] == "ambiguous" for candidate in candidates)
        )

    def test_workflow_run_target_must_match_producer_name(self):
        changed = copy.deepcopy(self.models)
        unsafe = changed["GHA-A1 Unverified artifact consumer"]
        event = next(
            event for event in unsafe["workflow"]["events"]
            if event["name"] == "workflow_run"
        )
        event["properties"]["workflows"] = ["Different producer"]

        candidates = DISCOVER.discover_candidates(changed)

        consumers = {
            candidate["consumerModel"]["workflow"]["name"]
            for candidate in candidates
        }
        self.assertNotIn("GHA-A1 Unverified artifact consumer", consumers)

    def test_every_semantic_model_element_has_one_source(self):
        for annotation in self.annotations:
            candidate = DISCOVER.find_candidate(self.candidates, annotation)
            chain = DISCOVER.build_annotated_chain(
                candidate,
                annotation,
                str(CSV_PATH),
                str(ANNOTATIONS_PATH),
            )
            DISCOVER.validate_provenance(chain)
            required = DISCOVER.required_provenance_elements(chain)
            actual = {record["element"] for record in chain["provenance"]}
            self.assertEqual(required, actual)
            self.assertEqual(
                {record["sourceKind"] for record in chain["provenance"]},
                {
                    "static-analysis",
                    "runtime-observation",
                    "manual-judgment",
                },
            )

    def test_generated_models_have_expected_structural_differences(self):
        chains = {
            path.stem: json.loads(path.read_text(encoding="utf-8"))
            for path in (ROOT / "model" / "generated-chains").glob("*.json")
        }

        self.assertEqual(chains["gha-a1-auto"]["facts"]["hasAuthority"], "true")
        self.assertEqual(
            chains["gha-a2-auto"]["facts"]["integrityCheckPresent"], "true"
        )
        self.assertEqual(
            chains["gha-a3-auto"]["facts"]["consumerUsesObject"], "false"
        )
        self.assertEqual(chains["gha-a4-auto"]["facts"]["hasAuthority"], "false")


class ArtifactChainEvaluationTest(unittest.TestCase):
    def test_nusmv_verdict_is_parsed(self):
        self.assertEqual(
            EVALUATE.parse_verdict(
                "-- specification AG !(stage = authority_reached) is false\n"
            ),
            "unsafe",
        )
        self.assertEqual(
            EVALUATE.parse_verdict(
                "-- specification AG !(stage = authority_reached) is true\n"
            ),
            "safe",
        )

    def test_accuracy_summary_counts_both_error_types(self):
        results = [
            {"expectedVerdict": "unsafe", "actualVerdict": "unsafe"},
            {"expectedVerdict": "unsafe", "actualVerdict": "safe"},
            {"expectedVerdict": "safe", "actualVerdict": "safe"},
            {"expectedVerdict": "safe", "actualVerdict": "unsafe"},
        ]

        summary = EVALUATE.summarize(results)

        self.assertEqual(summary["accuracy"], 0.5)
        self.assertEqual(
            summary["confusionMatrix"],
            {
                "truePositive": 1,
                "trueNegative": 1,
                "falsePositive": 1,
                "falseNegative": 1,
            },
        )


if __name__ == "__main__":
    unittest.main()
