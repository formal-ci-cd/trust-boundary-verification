import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import tanstack_interleaving as schedule


CASE = ROOT / 'experiments/public-cases/tanstack'


class TanStackInterleavingTests(unittest.TestCase):
    def test_order_of_independent_runs_changes_reachability(self):
        saved_first = schedule.simulate(['producer'] * 4 + ['consumer'] * 2, True)
        restored_first = schedule.simulate(['consumer'] + ['producer'] * 4 + ['consumer'], True)
        self.assertTrue(saved_first['bad'])
        self.assertFalse(restored_first['bad'])
        self.assertFalse(restored_first['trace'][-1]['restoredTainted'])

    def test_mitigation_scope_blocks_all_interleavings(self):
        unsafe = schedule.explore(True)
        safe = schedule.explore(False)
        self.assertEqual(unsafe['verdict'], 'possible')
        self.assertEqual(safe['verdict'], 'no-path-in-supported-model')
        self.assertTrue(unsafe['conditionalCounterexample'][-1]['bad'])
        self.assertEqual(safe['conditionalCounterexample'], [])
        self.assertTrue(all(unsafe['witnessFacts'].values()))

    def test_models_are_generated_from_pinned_yaml_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            result = schedule.analyze(CASE, Path(directory))
            self.assertEqual([x['variant'] for x in result['cases']],
                             ['pre-incident', 'mitigation'])
            pre, post = result['cases']
            self.assertEqual(pre['producer']['event'], 'pull_request_target')
            self.assertEqual(post['producer']['event'], 'pull_request')
            self.assertTrue(pre['cacheScopeCompatible'])
            self.assertFalse(post['cacheScopeCompatible'])
            self.assertIn('CTLSPEC AG !bad', (Path(directory) / pre['model']).read_text())
            self.assertIn('bundle-size.yml', (Path(directory) / pre['model']).read_text())
            self.assertIn('release.yml', (Path(directory) / pre['model']).read_text())


if __name__ == '__main__':
    unittest.main()
