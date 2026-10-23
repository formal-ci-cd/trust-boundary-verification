# Shellの認識，Artifactの内容・検証履歴，既定scenarioと明示条件の一致を調べる回帰テスト．

"""Container-only regression checks for the stateful v2 model generator."""
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import workflow_shell_subset as shell  # noqa: E402
from workflow_model_v2.compiler import compile_workflows
from workflow_model_v2.scenario import default_scenario  # noqa: E402
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

    def test_expanded_literal_assignment_is_not_silently_treated_as_a_literal(self):
        with self.assertRaises(shell.UnsupportedShell):
            shell.parse_shell('value="$INPUT"')

    def test_tr_deletes_internal_newlines_instead_of_only_trimming_ends(self):
        from workflow_model_v2.shell_lowering import ShellLowering
        self.assertEqual(ShellLowering.trim('a\nb\r\n', r'\r\n'), 'ab')


class StatefulWorkflowModelTests(unittest.TestCase):
    def pair(self, case):
        producer = PRODUCER_A5 if case == 'A5' else PRODUCER_A1
        consumer = WORKFLOWS / {'A1':'artifact-a1-unsafe-consumer.yml','A2':'artifact-a2-safe-consumer.yml','A3':'artifact-a3-download-only-consumer.yml','A4':'artifact-a4-no-authority-consumer.yml','A5':'artifact-a5-attest-consumer.yml'}[case]
        return [producer, consumer]

    def test_all_artifact_cases_use_the_same_backend_with_default_or_explicit_inputs(self):
        for case in ('A1','A2','A3','A4','A5'):
            paths = self.pair(case)
            implicit, manifest = compile_workflows(paths)
            explicit, other = compile_workflows(paths, default_scenario(paths))
            self.assertEqual(implicit, explicit)
            self.assertEqual(manifest['modelVersion'], '2-unified-bounded')
            self.assertEqual(other['modelVersion'], manifest['modelVersion'])
            self.assertIn('AG !bad_simulated_path', implicit)
            self.assertTrue(manifest['jobs'])

    def test_step_output_guard_uses_the_registered_writer_symbol(self):
        smv, manifest = compile_workflows(self.pair('A2'))
        output = next(o for o in manifest['outputSymbols'] if o['step']=='verify-artifact' and o['output']=='verified')
        self.assertIn('true', output['domain'])
        self.assertIn('false', output['domain'])
        self.assertGreater(smv.count(output['symbol']), 3)

    def test_integrity_history_is_per_file_and_does_not_erase_prior_use(self):
        smv, _ = compile_workflows(self.pair('A2'))
        self.assertIn('next(verification_read)',smv)
        self.assertIn('next(post_verification_use)',smv)
        self.assertIn('next(integrity_',smv)
        self.assertNotIn('AG (integrity_blocked -> !artifact_dependent_use)',smv)
        self.assertIn('AG (artifact_dependent_use -> download_success_any)',smv)

    def test_a5_metadata_validation_and_outputs_are_supported_without_old_builder(self):
        smv, manifest = compile_workflows(self.pair('A5'))
        unsupported={d['code'] for d in manifest['diagnostics'] if d['status']=='unsupported'}
        self.assertFalse(unsupported,unsupported)
        self.assertTrue(any(o['output']=='target_branch' for o in manifest['outputSymbols']))
        self.assertIn('AG (artifact_dependent_sink -> artifact_dependent_use)',smv)

    def test_missing_output_binding_does_not_invent_a_metadata_dependency(self):
        producer, consumer = self.pair('A5')
        changed=consumer.read_text().replace("${{ steps.artifact-metadata.outputs.target_branch }}",'refs/heads/main')
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'consumer.yml';path.write_text(changed)
            _, manifest=compile_workflows([producer,path])
        self.assertIn('uncontracted-simulated-sink',{d['code'] for d in manifest['diagnostics']})

    def test_digest_contract_does_not_depend_on_shell_variable_names(self):
        producer, consumer = self.pair('A2')
        changed=consumer.read_text().replace('expected_digest','trusted_hash').replace('actual_digest','computed_hash').replace('verified=true','accepted=true').replace('verified=false','accepted=false').replace('"$verified"','"$accepted"')
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'consumer.yml';path.write_text(changed)
            smv, manifest=compile_workflows([producer,path])
        self.assertNotIn('shell-contract',{d['code'] for d in manifest['diagnostics']})
        self.assertIn('integrity_verified',smv)
        self.assertIn('computed_hash',{o['output'] for o in manifest['outputSymbols']})

    def test_structured_statement_span_points_to_the_source_command(self):
        _,manifest=compile_workflows(self.pair('A2'))
        step=next(s for j in manifest['jobs'] for s in j['steps'] if s['id']=='verify-artifact')
        digest=next(op for op in step['program']['operations'] if op['kind']=='digest')
        line=Path(step['source']['file']).read_text().splitlines()[digest['span']['line']-1]
        self.assertIn('actual_digest=$(sha256sum',line)

    def test_unmatched_artifact_still_has_a_total_transition_model(self):
        producer, consumer = self.pair('A1')
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'producer.yml';path.write_text(producer.read_text().replace('name: trust-boundary-build-output','name: renamed-artifact'))
            smv, manifest=compile_workflows([path,consumer])
        self.assertIn('MODULE main',smv)
        self.assertTrue(manifest['objects'])

    def test_independent_bfs_separates_download_from_use(self):
        a1=explore(*self.pair('A1'));a3=explore(*self.pair('A3'))
        self.assertTrue(a1['downloadReachable']);self.assertTrue(a1['artifactDependentUseReachable'])
        self.assertTrue(a3['downloadReachable']);self.assertFalse(a3['artifactDependentUseReachable'])


if __name__=='__main__':
    unittest.main()
