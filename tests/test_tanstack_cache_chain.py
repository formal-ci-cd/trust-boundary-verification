import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import tanstack_cache_chain as chain
import materialize_tanstack_combined as materialize

CASE = ROOT / 'experiments/public-cases/tanstack'
CONTROL = ROOT / 'results/tanstack-cache-chain/without-workflow-dispatch-control'


class TanStackCacheChainTests(unittest.TestCase):
    def setUp(self):
        self.manifest = json.loads((CASE / 'manifest.json').read_text())

    def test_pinned_sources_and_cross_repository_path(self):
        chain.verify_sources(CASE, self.manifest)
        result = chain.analyze(CASE, 'pre-incident', self.manifest)
        self.assertEqual(result['producer']['event'], 'pull_request_target')
        self.assertEqual(result['consumer']['permission'], 'id-token: write')
        self.assertTrue(result['cache']['sameDefaultBranchScopePossibleForFork'])
        self.assertEqual(result['staticVerdict'], 'potential-cross-workflow-cache-path')
        self.assertEqual([e['role'] for e in result['evidence']][-3:],
                         ['oidc-authority', 'consumer-external-setup',
                          'privileged-use-after-restore'])
        self.assertEqual(chain.explore(True)['verdict'], 'possible')

    def test_job_condition_does_not_exclude_possible_disjunctions(self):
        self.assertTrue(chain.job_accepts_event(
            {'if': "github.event_name == 'push' || github.event_name == 'pull_request_target'"},
            'pull_request_target'))
        self.assertFalse(chain.job_accepts_event(
            {'if': "github.event_name != 'pull_request_target' && github.ref == 'refs/heads/main'"},
            'pull_request_target'))
        self.assertTrue(chain.job_accepts_event(
            {'if': "github.event_name == 'push' || inputs.force"},
            'pull_request_target'))

    def test_real_event_change_separates_fork_cache(self):
        fixed = chain.analyze(CASE, 'mitigation', self.manifest)
        self.assertEqual(fixed['producer']['event'], 'pull_request')
        self.assertFalse(fixed['cache']['sameDefaultBranchScopePossibleForFork'])
        self.assertEqual(chain.explore(False)['verdict'], 'no-path-in-supported-model')
        self.assertFalse(chain.explore(False)['conditionalCounterexample'])
        trace = chain.explore(True)['conditionalCounterexample']
        self.assertTrue(trace[-1]['state'][-1])
        self.assertTrue(all(trace[0]['state'][i] for i in range(1, 5)))

    def test_entry_is_found_without_an_incident_workflow_name(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / 'case'
            shutil.copytree(CASE, fixture)
            workflows = fixture / 'pre-incident/.github/workflows'
            (workflows / 'bundle-size.yml').rename(workflows / 'renamed-entry.yml')
            result = chain.analyze(fixture, 'pre-incident', self.manifest)
            self.assertTrue(result['producer']['workflow'].endswith('renamed-entry.yml'))
            self.assertTrue(result['consumer']['workflow'].endswith('release.yml'))
            self.assertEqual(result['staticVerdict'], 'potential-cross-workflow-cache-path')

    def test_unrelated_dispatch_control_keeps_the_exploited_pr_path(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / 'case'
            shutil.copytree(CASE, fixture)
            workflow = fixture / 'pre-incident/.github/workflows/bundle-size.yml'
            before = workflow.read_text()
            materialize.remove_unrelated_dispatch(workflow)
            self.assertEqual(workflow.read_text(), before.replace('  workflow_dispatch:\n', ''))
            result = chain.analyze(fixture, 'pre-incident', self.manifest)
            self.assertEqual(result['producer']['event'], 'pull_request_target')
            self.assertEqual(result['staticVerdict'], 'potential-cross-workflow-cache-path')
            self.assertEqual(chain.explore(True)['verdict'], 'possible')

    def test_path_is_removed_by_independent_structural_controls(self):
        mutations = {
            'job-does-not-run-on-pr': (
                'pre-incident/.github/workflows/bundle-size.yml',
                "if: github.event_name == 'pull_request_target'",
                "if: github.event_name == 'workflow_dispatch'"),
            'checkout-does-not-read-pr': (
                'pre-incident/.github/workflows/bundle-size.yml',
                'ref: refs/pull/${{ github.event.pull_request.number }}/merge',
                'ref: main'),
            'release-has-no-oidc': (
                'pre-incident/.github/workflows/release.yml',
                '  id-token: write\n', ''),
            'external-setup-has-no-cache': (
                'external-setup/.github/setup/action.yml',
                'uses: actions/cache@', 'uses: actions/setup-node@'),
        }
        for name, (relative, old, new) in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                fixture = Path(directory) / 'case'
                shutil.copytree(CASE, fixture)
                path = fixture / relative
                source = path.read_text()
                self.assertIn(old, source)
                path.write_text(source.replace(old, new, 1))
                result = chain.analyze(fixture, 'pre-incident', self.manifest)
                self.assertEqual(result['staticVerdict'], 'no-path-in-supported-model')
                self.assertFalse(result['cache']['sameDefaultBranchScopePossibleForFork'])
                self.assertEqual(chain.explore(False)['verdict'], 'no-path-in-supported-model')

    def test_dispatch_control_baselines_are_pinned_and_path_specific(self):
        comparison = json.loads((CONTROL / 'comparison.json').read_text())
        source = CASE / 'pre-incident/.github/workflows/bundle-size.yml'
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),
                         comparison['originalSHA256'])
        self.assertEqual(hashlib.sha256(source.read_text().replace(
            '  workflow_dispatch:\n', '').encode()).hexdigest(),
            comparison['controlSHA256'])
        for record in comparison['files']:
            path = CONTROL / record['path']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record['sha256'])
        original_sarif = ROOT / 'results/tanstack-cache-chain/pre-incident/codeql-default.sarif.gz'
        original = json.loads(gzip.decompress(original_sarif.read_bytes()))
        self.assertEqual(len(original['runs'][0]['results']), 1)
        self.assertIn('workflow_dispatch', original['runs'][0]['results'][0]['message']['text'])
        default = json.loads(gzip.decompress((CONTROL / 'codeql-default.sarif.gz').read_bytes()))
        self.assertEqual(default['runs'][0]['results'], [])
        quality = json.loads(gzip.decompress(
            (CONTROL / 'codeql-security-and-quality.sarif.gz').read_bytes()))
        self.assertFalse(any('cache-poisoning' in result['ruleId']
                             for result in quality['runs'][0]['results']))
        zizmor = json.loads(gzip.decompress((CONTROL / 'zizmor-regular.sarif.gz').read_bytes()))
        self.assertTrue(any(result['ruleId'] == 'zizmor/dangerous-triggers'
                            for result in zizmor['runs'][0]['results']))
        analysis = json.loads((CONTROL / 'analysis.json').read_text())
        self.assertEqual(analysis['cases'][0]['producer']['event'], 'pull_request_target')
        self.assertEqual(analysis['cases'][0]['bfs']['verdict'], 'possible')

    def test_tampered_source_is_rejected_before_analysis(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / 'case'
            shutil.copytree(CASE, fixture)
            bundle = fixture / 'pre-incident/.github/workflows/bundle-size.yml'
            bundle.write_text(bundle.read_text().replace('pull_request_target:', 'pull_request:'))
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                chain.verify_sources(fixture, self.manifest)


if __name__ == '__main__':
    unittest.main()
