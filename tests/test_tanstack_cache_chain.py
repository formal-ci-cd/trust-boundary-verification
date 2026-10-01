import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import tanstack_cache_chain as chain

CASE = ROOT / 'experiments/public-cases/tanstack'


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

    def test_real_event_change_separates_fork_cache(self):
        fixed = chain.analyze(CASE, 'mitigation', self.manifest)
        self.assertEqual(fixed['producer']['event'], 'pull_request')
        self.assertFalse(fixed['cache']['sameDefaultBranchScopePossibleForFork'])
        self.assertEqual(chain.explore(False)['verdict'], 'no-path-in-supported-model')
        self.assertFalse(chain.explore(False)['conditionalCounterexample'])
        trace = chain.explore(True)['conditionalCounterexample']
        self.assertTrue(trace[-1]['state'][-1])
        self.assertTrue(all(trace[0]['state'][i] for i in range(1, 5)))

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
