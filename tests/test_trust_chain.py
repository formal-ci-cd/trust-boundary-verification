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

    def test_generated_chain_facts_match_the_schema(self):
        schema = BUILD.load_json(ROOT / "model" / "trust-chain.schema.json")
        required_facts = set(
            schema["properties"]["facts"]["required"]
        )

        for scenario_id in ("gha-a1", "gha-a2"):
            chain = self.build(scenario_id)
            self.assertEqual(chain["schemaVersion"], "0.2.0")
            self.assertEqual(set(chain["facts"]), required_facts)

    def test_observed_producer_run_matches_the_chain_and_payload(self):
        observation = BUILD.load_json(
            ROOT
            / "model"
            / "observations"
            / "gha-a-producer-run-32575878081.json"
        )
        operation = observation["operationResults"][0]
        payload = ROOT / ".research-artifact-input" / "payload.txt"
        actual_digest = hashlib.sha256(payload.read_bytes()).hexdigest()

        self.assertEqual(
            operation["operationId"], "artifact-write:produce-artifact:2"
        )
        self.assertEqual(operation["writeAuthorized"], "true")
        self.assertEqual(operation["writeSucceeded"], "true")
        self.assertEqual(operation["artifact"]["payloadSha256"], actual_digest)

        for scenario_id in ("gha-a1", "gha-a2"):
            chain = self.build(scenario_id)
            self.assertEqual(chain["facts"]["writeAuthorized"], "true")
            self.assertEqual(chain["facts"]["writeSucceeded"], "true")

    def test_runtime_consumers_used_the_same_artifact(self):
        observation = BUILD.load_json(
            ROOT / "model" / "observations" / "gha-a-runtime-pr7.json"
        )
        producer = observation["producer"]
        consumers = {
            consumer["scenarioId"]: consumer
            for consumer in observation["consumers"]
        }

        self.assertEqual(set(consumers), {"GHA-A1", "GHA-A2"})
        for consumer in consumers.values():
            self.assertEqual(
                consumer["triggerProducerRunId"], producer["runId"]
            )
            self.assertEqual(
                consumer["downloadedArtifactId"], producer["artifact"]["id"]
            )
            self.assertEqual(
                consumer["downloadedArchiveDigest"],
                producer["artifact"]["archiveDigest"],
            )
            self.assertEqual(
                consumer["payloadSha256"],
                producer["artifact"]["payloadSha256"],
            )
            self.assertTrue(consumer["downloadSucceeded"])

        self.assertTrue(consumers["GHA-A1"]["dummyPublishAuthorityReached"])
        self.assertEqual(consumers["GHA-A1"]["useStepConclusion"], "success")
        self.assertIsNone(consumers["GHA-A1"]["integrityCheckPassed"])
        self.assertFalse(consumers["GHA-A2"]["dummyPublishAuthorityReached"])
        self.assertEqual(consumers["GHA-A2"]["useStepConclusion"], "skipped")
        self.assertEqual(consumers["GHA-A2"]["reason"], "digest_mismatch")

        for scenario_id in ("gha-a1", "gha-a2"):
            chain = self.build(scenario_id)
            self.assertEqual(chain["scenario"]["evidenceStatus"], "observed")
            self.assertEqual(chain["facts"]["readSucceeded"], "true")

    def test_unsafe_chain_has_authority_counterexample_conditions(self):
        chain = self.build("gha-a1")
        rendered = CONVERT.render_model(chain, "gha-a1.json")

        self.assertIn("integrity_check_present := FALSE;", rendered)
        self.assertIn("integrity_check_passed := FALSE;", rendered)
        self.assertIn("has_authority := TRUE;", rendered)
        self.assertIn(
            "CTLSPEC AG !(stage = authority_reached & object_tainted)",
            rendered,
        )
        self.assertIn("artifact-a1-unsafe-consumer.yml", rendered)

    def test_safe_chain_blocks_unverified_authority_path(self):
        chain = self.build("gha-a2")
        rendered = CONVERT.render_model(chain, "gha-a2.json")

        self.assertIn("integrity_check_present := TRUE;", rendered)
        self.assertIn("integrity_check_passed := FALSE;", rendered)
        self.assertIn(
            "stage = object_restored & integrity_check_present & integrity_check_passed : integrity_checked;",
            rendered,
        )
        self.assertIn(
            "stage = integrity_checked & consumer_uses_object : object_used;",
            rendered,
        )

    def test_successful_integrity_check_removes_untrusted_taint(self):
        chain = self.build("gha-a2")
        rendered = CONVERT.render_model(chain, "gha-a2.json")

        self.assertIn("init(object_tainted) := producer_untrusted;", rendered)
        self.assertIn(
            "stage = object_restored & integrity_check_present & integrity_check_passed : FALSE;",
            rendered,
        )
        self.assertIn(
            "INVAR integrity_check_passed -> integrity_check_present", rendered
        )

    def test_nusmv_counterexample_is_mapped_to_workflow_steps(self):
        chain = self.build("gha-a1")
        nusmv_output = """
-- specification AG !(stage = authority_reached & object_tainted) is false
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
