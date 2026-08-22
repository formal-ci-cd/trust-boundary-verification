import hashlib
import importlib.util
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name):
    path = ROOT / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILD = load_module("build_trust_chain")
CONVERT = load_module("chain_to_nusmv")
EXPLAIN = load_module("explain_nusmv_trace")


class TrustChainTest(unittest.TestCase):
    def build(self, scenario_id):
        config = BUILD.load_json(
            ROOT / "model" / "chain-inputs" / f"{scenario_id}.json"
        )
        producer = BUILD.load_json(ROOT / config["producerModel"])
        consumer = BUILD.load_json(ROOT / config["consumerModel"])
        return BUILD.build_chain(config, producer, consumer)

    def test_codeql_models_are_joined_by_artifact_name_and_run(self):
        chain = self.build("gha-a1")

        self.assertEqual(chain["producer"]["workflow"]["event"], "pull_request")
        self.assertEqual(chain["consumer"]["workflow"]["event"], "workflow_run")
        self.assertEqual(
            chain["sharedObject"]["name"], "trust-boundary-build-output"
        )
        self.assertEqual(
            chain["consumer"]["operation"]["runId"],
            "${{ github.event.workflow_run.id }}",
        )
        self.assertEqual(chain["facts"]["producerUntrusted"], "true")
        self.assertEqual(chain["facts"]["privilegedConsumer"], "true")

    def test_unsafe_chain_has_authority_counterexample_conditions(self):
        chain = self.build("gha-a1")
        rendered = CONVERT.render_model(chain, "gha-a1.json")

        self.assertIn("integrity_verified := FALSE;", rendered)
        self.assertIn("has_authority := TRUE;", rendered)
        self.assertIn("CTLSPEC AG stage != authority_reached", rendered)
        self.assertIn("artifact-a1-unsafe-consumer.yml", rendered)

    def test_safe_chain_blocks_unverified_authority_path(self):
        chain = self.build("gha-a2")
        rendered = CONVERT.render_model(chain, "gha-a2.json")

        self.assertIn("integrity_verified := TRUE;", rendered)
        self.assertIn(
            "stage = object_used & integrity_verified : integrity_checked;",
            rendered,
        )

    def test_nusmv_counterexample_is_mapped_to_workflow_steps(self):
        chain = self.build("gha-a1")
        nusmv_output = """
-- specification AG stage != authority_reached is false
Trace Description: CTL Counterexample
-> State: 1.1 <-
  stage = start
-> State: 1.2 <-
  stage = object_written
-> State: 1.3 <-
  stage = object_restored
-> State: 1.4 <-
  stage = object_used
-> State: 1.5 <-
  stage = authority_reached
"""

        explanation = EXPLAIN.render_explanation(chain, nusmv_output)

        self.assertIn("違反する反例あり", explanation)
        self.assertIn("artifact-a1-pr-producer.yml", explanation)
        self.assertIn("artifact-a1-unsafe-consumer.yml", explanation)
        self.assertIn("`consume-without-verification`", explanation)

    def test_mismatched_artifact_name_is_rejected(self):
        config = BUILD.load_json(
            ROOT / "model" / "chain-inputs" / "gha-a1.json"
        )
        producer = BUILD.load_json(ROOT / config["producerModel"])
        consumer = BUILD.load_json(ROOT / config["consumerModel"])
        consumer["sharedStateOperations"][0]["name"] = "different-artifact"

        with self.assertRaises(ValueError):
            BUILD.build_chain(config, producer, consumer)

    def test_safe_workflow_pins_the_trusted_payload_digest(self):
        payload = ROOT / ".research-artifact-input" / "payload.txt"
        expected_digest = hashlib.sha256(payload.read_bytes()).hexdigest()
        safe_workflow = (
            ROOT / ".github" / "workflows" / "artifact-a2-safe-consumer.yml"
        ).read_text(encoding="utf-8")

        self.assertIn(expected_digest, safe_workflow)


if __name__ == "__main__":
    unittest.main()
