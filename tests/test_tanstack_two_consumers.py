import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import tanstack_two_consumers as multi

RESULTS = ROOT / 'results/tanstack-cache-chain/two-consumers'


class TwoConsumerTests(unittest.TestCase):
    def test_order_changes_which_release_run_can_reach_tainted_cache(self):
        schedules = {
            'before_both': ['producer'] * 4 + ['consumer1', 'consumer2'] * 2,
            'between': ['consumer1'] + ['producer'] * 4 +
                       ['consumer2', 'consumer1', 'consumer2'],
            'after_both': ['consumer1', 'consumer2'] + ['producer'] * 4 +
                          ['consumer1', 'consumer2'],
        }
        self.assertEqual([(multi.simulate(s, True)['bad1'],
                           multi.simulate(s, True)['bad2']) for s in schedules.values()],
                         [(True, True), (False, True), (False, False)])
        self.assertEqual([(multi.simulate(s, False)['bad1'],
                           multi.simulate(s, False)['bad2']) for s in schedules.values()],
                         [(False, False)] * 3)

    def test_each_consumer_has_independent_restore_outcome(self):
        schedule = ['producer'] * 4 + ['consumer1', 'consumer2'] * 2
        only_second_restores = (True, False, True, True, True, True, True)
        self.assertEqual((multi.simulate(schedule, True, only_second_restores)['bad1'],
                          multi.simulate(schedule, True, only_second_restores)['bad2']),
                         (False, True))

    def test_saved_models_and_nusmv_match_bfs(self):
        report = json.loads((RESULTS / 'analysis-native.json').read_text())
        self.assertEqual(len(report['cases']), 2)
        for case in report['cases']:
            model = RESULTS / case['model']
            self.assertEqual(model.read_text(),
                             multi.render(case['cacheScopeCompatible'], case['sourceEvidence']))
            self.assertEqual(case['bfs'], multi.explore(case['cacheScopeCompatible']))
            self.assertEqual(case['nusmv']['modelSHA256'],
                             hashlib.sha256(model.read_bytes()).hexdigest())
            output = (RESULTS / (case['variant'] + '.nusmv.txt')).read_text()
            expected = 'false' if case['bfs']['verdict'] == 'possible' else 'true'
            self.assertIn('-- specification AG !(bad1 | bad2)  is ' + expected, output)


if __name__ == '__main__':
    unittest.main()
