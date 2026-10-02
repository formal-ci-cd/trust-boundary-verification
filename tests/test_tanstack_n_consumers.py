import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import tanstack_n_consumers as scaled
import tanstack_two_consumers as original


class ScaledConsumerTests(unittest.TestCase):
    def test_two_run_generalization_matches_original_exhaustive_search(self):
        for compatible in (True, False):
            self.assertEqual(scaled.explore(2, compatible)['reachableStates'],
                             original.explore(compatible)['reachableStates'])
            self.assertEqual(scaled.explore(2, compatible)['possible'],
                             original.explore(compatible)['verdict'] == 'possible')

    def test_each_consumer_can_be_affected_independently_by_schedule(self):
        count = 3
        facts = (True,) * len(scaled.facts_for(count))
        state = scaled.initial(count, facts)
        # First consumer restores before the poison save; the other two after.
        for actor in (1, 0, 0, 0, 0, 1, 2, 2, 3, 3):
            state = scaled.transition(state, actor, count, True)
        self.assertEqual(state[2 * count + 2:3 * count + 2],
                         (False, True, True))

    def test_mitigation_has_no_path_with_three_unknown_fact_consumers(self):
        self.assertEqual(scaled.explore(3, False)['reachableStates'], 138240)
        self.assertFalse(scaled.explore(3, False)['possible'])
        self.assertTrue(scaled.explore(3, True)['possible'])


if __name__ == '__main__':
    unittest.main()
