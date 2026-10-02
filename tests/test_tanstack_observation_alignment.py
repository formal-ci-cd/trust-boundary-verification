import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import verify_tanstack_observation_alignment as alignment

MODEL = json.loads((ROOT / 'results/tanstack-cache-chain/two-consumers/analysis-native.json').read_text())
OBSERVATION = json.loads((ROOT / 'experiments/public-cases/tanstack/incident-observation.json').read_text())
JOBS = json.loads((ROOT / 'results/tanstack-cache-chain/public-run-metadata.json').read_text())


class ObservationAlignmentTests(unittest.TestCase):
    def test_saved_alignment_recomputes_from_separate_sources(self):
        expected = json.loads((ROOT / 'results/tanstack-cache-chain/two-consumers/observation-alignment.json').read_text())
        self.assertEqual(alignment.verify(MODEL, OBSERVATION, JOBS), expected)
        self.assertEqual([x['reportedSaveBeforeSetup'] for x in expected['checks']],
                         [True, True])

    def test_wrong_run_identity_is_rejected(self):
        altered = copy.deepcopy(JOBS)
        altered['runs'][1]['runId'] += 1
        with self.assertRaisesRegex(ValueError, 'IDs'):
            alignment.verify(MODEL, OBSERVATION, altered)

    def test_save_after_setup_is_rejected(self):
        altered = copy.deepcopy(OBSERVATION)
        altered['reportedCache']['savedAtUTC'] = '2026-05-11T19:22:00Z'
        with self.assertRaisesRegex(ValueError, 'before Setup'):
            alignment.verify(MODEL, altered, JOBS)

    def test_missing_successful_setup_is_rejected(self):
        altered = copy.deepcopy(JOBS)
        for step in altered['runs'][0]['job']['steps']:
            if step['name'] == 'Setup Tools':
                step['conclusion'] = 'failure'
        with self.assertRaisesRegex(ValueError, 'job steps'):
            alignment.verify(MODEL, OBSERVATION, altered)


if __name__ == '__main__':
    unittest.main()
