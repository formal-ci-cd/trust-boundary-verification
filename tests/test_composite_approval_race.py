import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import composite_approval_race as race

CASE = ROOT / 'experiments/public-cases/jupyter-notebook-screening'


class CompositeApprovalRaceTests(unittest.TestCase):
    def test_actual_upstream_before_and_fixed_pair(self):
        before = race.discover(CASE / 'upstream-before')
        fixed = race.discover(CASE / 'upstream-fixed')
        self.assertEqual(len(before), 1)
        self.assertEqual(len(fixed), 1)
        self.assertEqual(before[0]['rejectionOperator'], '-gt')
        self.assertEqual(fixed[0]['rejectionOperator'], '-ge')
        self.assertEqual(race.explore('-gt')['verdict'], 'unsafe')
        self.assertEqual(race.explore('-ge')['verdict'], 'safe')
        self.assertEqual([r['accepted'] for r in race.replay_guard(before[0])],
                         [True, True, False])
        self.assertEqual([r['accepted'] for r in race.replay_guard(fixed[0])],
                         [True, False, False])

    def test_head_validation_rejects_update_after_fetch(self):
        state = (0, 0, -1, False, False, False, -1, False, False)
        state = dict(race.successors(state, '-gt'))['fetch-current-PR']
        state = dict(race.successors(state, '-gt'))['attacker:update-PR']
        for action in ('timestamp-guard', 'checkout-PR', 'validate-HEAD-SHA'):
            state = dict(race.successors(state, '-gt'))[action]
        self.assertEqual(state[0], 5)
        self.assertFalse(state[-1])

    def test_manifest_does_not_silently_accept_changed_action(self):
        with tempfile.TemporaryDirectory() as temp:
            case = Path(temp)
            shutil.copytree(CASE / 'upstream-before', case / 'upstream-before')
            shutil.copy(CASE / 'manifest.json', case / 'manifest.json')
            path = case / 'upstream-before/.github/actions/update-snapshots-checkout/action.yml'
            path.write_text(path.read_text().replace(' -gt ', ' -ge '))
            self.assertEqual(race.discover(case / 'upstream-before'), [])

    def test_missing_local_action_breaks_supported_path(self):
        with tempfile.TemporaryDirectory() as temp:
            case = Path(temp)
            shutil.copytree(CASE / 'upstream-before', case / 'upstream-before')
            manifest = json.loads((CASE / 'manifest.json').read_text())
            path = case / 'upstream-before/.github/workflows/playwright-update.yml'
            path.write_text(path.read_text().replace('./.github/actions/build-dist',
                                                     'jupyterlab/maintainer-tools/.github/actions/build-dist'))
            import hashlib
            manifest['upstreamPairedComparison']['upstream-before']['workflowSHA256'] = hashlib.sha256(path.read_bytes()).hexdigest()
            (case / 'manifest.json').write_text(json.dumps(manifest))
            self.assertEqual(race.discover(case / 'upstream-before'), [])


if __name__ == '__main__':
    unittest.main()
