"""Container-only regression checks for the stateful v2 model generator."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import workflow_shell_subset as shell  # noqa: E402
from workflow_model_v2.builder import compile_pair_v2  # noqa: E402
from workflow_model_v2.independent_bfs import explore  # noqa: E402


WORKFLOWS = ROOT / ".github" / "workflows"
PRODUCER_A1 = WORKFLOWS / "artifact-a1-pr-producer.yml"
PRODUCER_A5 = WORKFLOWS / "artifact-a5-attest-producer.yml"


class ShellSubsetTests(unittest.TestCase):
    def test_recognizes_data_read_output_and_marker_relations(self):
        program = shell.parse_shell(
            "set -eu\n"
            "request=$(tr -d '\\r\\n' < \"$ARTIFACT_FILE\")\n"
            "if [ \"$request\" = \"publish=true\" ]; then\n"
            "  authority_reached=true\n"
            "else\n"
            "  authority_reached=false\n"
            "fi\n"
            "echo \"dummy_publish_authority_reached=$authority_reached\"\n"
        )
        self.assertEqual(program.artifact_reads[0].kind, "read-trim")
        self.assertEqual(program.artifact_reads[0].writes, ("request",))
        self.assertEqual(program.sink_sites[0].kind, "echo")

    def test_rejects_unknown_shell_without_ignoring_the_rest_of_the_script(self):
        with self.assertRaises(shell.UnsupportedShell):
            shell.parse_shell('echo "before"\neval "$INPUT"\necho "after"\n')

    def test_digest_output_is_a_step_output_write(self):
        program = shell.parse_shell(
            "expected_digest='" + "a" * 64 + "'\n"
            "actual_digest=$(sha256sum \"$ARTIFACT_FILE\" | cut -d ' ' -f 1)\n"
            "if [ \"$actual_digest\" = \"$expected_digest\" ]; then\n"
            "verified=true\nelse\nverified=false\nfi\n"
            "printf 'verified=%s\\n' \"$verified\" >> \"$GITHUB_OUTPUT\"\n"
        )
        self.assertEqual(program.output_writes[0].writes, ("output:verified",))

    def test_literal_true_dummy_marker_is_a_sink_but_false_is_not(self):
        true_marker = shell.parse_shell("echo 'dummy_repository_update_reached=true'\n")
        false_marker = shell.parse_shell("echo 'dummy_repository_update_reached=false'\n")
        unrelated = shell.parse_shell("echo 'finished=true'\n")
        self.assertEqual(len(true_marker.sink_sites), 1)
        self.assertEqual(false_marker.sink_sites, [])
        self.assertEqual(unrelated.sink_sites, [])

    def test_stderr_redirection_is_accepted_as_echo_presentation_only(self):
        program = shell.parse_shell("echo 'invalid branch name' >&2\n")
        self.assertEqual(program.operations[0].kind, "echo")
        self.assertEqual(program.sink_sites, [])


class StatefulWorkflowModelTests(unittest.TestCase):
    def _pair(self, producer: Path, consumer_name: str):
        return compile_pair_v2(producer, WORKFLOWS / consumer_name)

    def test_a1_a2_a3_a4_and_a5_generate_stateful_properties(self):
        cases = [
            (PRODUCER_A1, "artifact-a1-unsafe-consumer.yml"),
            (PRODUCER_A1, "artifact-a2-safe-consumer.yml"),
            (PRODUCER_A1, "artifact-a3-download-only-consumer.yml"),
            (PRODUCER_A1, "artifact-a4-no-authority-consumer.yml"),
            (PRODUCER_A5, "artifact-a5-attest-consumer.yml"),
        ]
        for producer, consumer in cases:
            with self.subTest(consumer=consumer):
                smv, manifest = self._pair(producer, consumer)
                self.assertIn("CTLSPEC AG !bad_simulated_path", smv)
                self.assertIn("CTLSPEC AG !bad_real_authority_path", smv)
                self.assertIn("CTLSPEC AG !artifact_dependent_use", smv)
                self.assertIn("CTLSPEC AG !simulated_sink_site_reached", smv)
                self.assertIn("artifact_dependent_use", smv)
                self.assertEqual(manifest["modelVersion"], "2-stateful-prototype")
                self.assertIn("typedIR", manifest)
                self.assertNotIn(manifest["verdict"], {"safe", "unsafe"})
                self.assertIn("real-authority-sink-contract-missing",
                              {item["code"] for item in manifest["diagnostics"]})

    def test_output_reference_lowers_to_the_same_state_symbol(self):
        smv, manifest = self._pair(PRODUCER_A1, "artifact-a2-safe-consumer.yml")
        self.assertIn("out_true", smv)
        self.assertTrue(any(edge["relation"] == "if-control-reference" and
                            ":output:verified" in edge["source_id"]
                            for edge in manifest["typedIR"]["edges"]))

    def test_artifact_dependent_use_requires_successful_download_state(self):
        smv, _ = self._pair(PRODUCER_A1, "artifact-a1-unsafe-consumer.yml")
        start = smv.index("  next(artifact_dependent_use)")
        end = smv.index("  esac;", start)
        transition = smv[start:end]
        self.assertIn("_state = download_succeeded", transition)
        self.assertIn("CTLSPEC AG (artifact_dependent_use -> download_success_any)", smv)

    def test_a5_runner_temp_path_and_step_outputs_are_supported_together(self):
        smv, manifest = self._pair(PRODUCER_A5, "artifact-a5-attest-consumer.yml")
        unsupported = {item["code"] for item in manifest["diagnostics"]
                       if item["status"] == "unsupported"}
        self.assertNotIn("dynamic-artifact-input", unsupported)
        self.assertNotIn("shell-subset", unsupported)
        self.assertNotIn("unresolved-step-output", unsupported)
        self.assertIn("CTLSPEC AG (artifact_dependent_sink -> artifact_dependent_use)", smv)

    def test_unrelated_literal_marker_does_not_create_artifact_provenance(self):
        original = (WORKFLOWS / "artifact-a5-attest-consumer.yml").read_text()
        original = original.replace(
            "      - name: Simulate privileged repository update\n",
            "      - name: Simulate privileged repository update\n        if: false\n", 1)
        original += "\n      - name: Record an unrelated marker\n"
        original += "        run: echo 'dummy_repository_update_reached=true'\n"
        with tempfile.TemporaryDirectory() as temp:
            consumer = Path(temp) / "consumer.yml"
            consumer.write_text(original)
            smv, manifest = compile_pair_v2(PRODUCER_A5, consumer)
        self.assertTrue(any(d["code"] in {"uncontracted-simulated-sink",
                                          "simulated-repository-update-flow"} and
                            d["status"] == "unsupported"
                            for d in manifest["diagnostics"]))
        dependency_transition = next(line for line in smv.splitlines()
                                     if line.startswith("  next(artifact_dependent_sink)"))
        self.assertNotIn("consume_untrusted_dist_step_3", dependency_transition)

    def test_unmatched_download_candidate_keeps_a_valid_object_slot(self):
        producer = PRODUCER_A1.read_text().replace(
            "name: trust-boundary-build-output", "name: renamed-artifact", 1)
        with tempfile.TemporaryDirectory() as temp:
            producer_path = Path(temp) / "producer.yml"
            producer_path.write_text(producer)
            smv, _ = compile_pair_v2(producer_path, WORKFLOWS / "artifact-a1-unsafe-consumer.yml")
        self.assertIn("unmatched_object", smv)
        self.assertIn("download_", smv)

    def test_independent_bfs_separates_a1_use_from_a3_download_only(self):
        a1 = explore(PRODUCER_A1, WORKFLOWS / "artifact-a1-unsafe-consumer.yml")
        a3 = explore(PRODUCER_A1, WORKFLOWS / "artifact-a3-download-only-consumer.yml")
        self.assertTrue(a1["downloadReachable"])
        self.assertTrue(a1["artifactDependentUseReachable"])
        self.assertTrue(a3["downloadReachable"])
        self.assertFalse(a3["artifactDependentUseReachable"])
        self.assertTrue(a1["useRequiresPriorSuccessfulDownload"])
        self.assertTrue(a3["useRequiresPriorSuccessfulDownload"])


if __name__ == "__main__":
    unittest.main()
