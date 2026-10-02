import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import conditional_checkout_chain as chain


ROOT = Path(__file__).resolve().parents[1] / 'experiments/public-cases/spotbugs-chain'


class ConditionalCheckoutChainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        shutil.copytree(ROOT / '.github', self.root / '.github')
        shutil.copytree(ROOT / 'external-action-cond', self.root / 'external-action-cond')
        self.workflow = self.root / '.github/workflows/sonarqube.yml'
        self.external = self.root / 'external-action-cond'

    def modify(self, old, new):
        source = self.workflow.read_text()
        self.assertIn(old, source)
        self.workflow.write_text(source.replace(old, new, 1))

    def findings(self):
        return chain.discover(self.root, self.external, 'haya14busa/action-cond', 'v1')['findings']

    def test_original_connects_selector_checkout_and_secret_sink(self):
        findings = self.findings()
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding['configuredSecrets'], ['PAT_TO_FORK', 'SONAR_TOKEN'])
        self.assertEqual([e['line'] for e in finding['evidence']], [35, 42, 59])

    def test_base_ref_control_breaks_untrusted_path(self):
        self.modify('if_true: refs/pull/${{ github.event.pull_request.number }}/merge',
                    'if_true: ${{ github.sha }}')
        self.assertEqual(self.findings(), [])

    def test_checkout_ref_must_follow_selector_output(self):
        self.modify('ref: ${{ steps.condval.outputs.value }}', 'ref: ${{ github.sha }}')
        self.assertEqual(self.findings(), [])

    def test_secret_must_be_configured_at_execution_step(self):
        self.modify('secrets.PAT_TO_FORK', 'vars.PAT_TO_FORK')
        self.modify('secrets.SONAR_TOKEN', 'vars.SONAR_TOKEN')
        self.assertEqual(self.findings(), [])

    def test_bundled_action_semantics_are_required(self):
        bundled = self.external / 'index.js'
        source = bundled.read_text()
        self.assertIn("cond === 'true' ? ifTrue : ifFalse", source)
        bundled.write_text(source.replace("cond === 'true' ? ifTrue : ifFalse",
                                          "cond === 'true' ? ifFalse : ifTrue", 1))
        self.assertEqual(chain.discover(self.root, self.external, 'haya14busa/action-cond', 'v1')['status'],
                         'unsupported-external-action')

    def test_supplied_external_version_must_match_workflow(self):
        self.assertEqual(chain.discover(self.root, self.external,
                                        'haya14busa/action-cond', 'v2')['findings'], [])

    def test_unrecognized_condition_is_not_assumed_safe_or_unsafe(self):
        self.modify("github.event_name == 'pull_request_target'",
                    "github.event_name != 'pull_request_target'")
        self.assertEqual(self.findings(), [])

    def test_real_fork_exclusion_suppresses_external_fork_path(self):
        self.modify("github.repository == 'spotbugs/sonar-findbugs'",
                    'github.event.pull_request.head.repo.full_name == github.repository')
        result = chain.discover(self.root, self.external, 'haya14busa/action-cond', 'v1')
        self.assertEqual(result['status'], 'fork-excluded')
        self.assertEqual(result['findings'], [])
        self.assertEqual(result['forkExcludedJobs'][0]['job'], 'build')

    def test_repository_identity_is_not_fork_exclusion(self):
        self.assertFalse(chain.excludes_external_fork("github.repository == 'spotbugs/sonar-findbugs'"))
        self.assertFalse(chain.excludes_external_fork(
            'github.event.pull_request.head.repo.full_name == github.repository || true'))
        self.assertTrue(chain.excludes_external_fork(
            '${{ github.repository == github.event.pull_request.head.repo.full_name }}'))

    def test_unrelated_guard_is_not_reported_as_excluded_candidate(self):
        self.modify("github.repository == 'spotbugs/sonar-findbugs'",
                    'github.event.pull_request.head.repo.full_name == github.repository')
        self.modify('if_true: refs/pull/${{ github.event.pull_request.number }}/merge',
                    'if_true: ${{ github.sha }}')
        result = chain.discover(self.root, self.external, 'haya14busa/action-cond', 'v1')
        self.assertEqual(result['status'], 'no-supported-path')
        self.assertNotIn('forkExcludedJobs', result)


if __name__ == '__main__':
    unittest.main()
