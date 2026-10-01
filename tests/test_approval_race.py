import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import yaml
import jsonschema

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import approval_race_analysis as race
import yaml_to_model

CASE = ROOT / 'experiments/public-cases/marimo'


class ApprovalRaceTests(unittest.TestCase):
    def test_original_auto_path_crosses_local_reusable_workflow(self):
        candidates = race.discover(CASE / 'vulnerable')
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate['rejectionOperator'], '-gt')
        npm = next(s for s in candidate['sinks'] if s['artifact'] == 'frontend-test-branch')
        self.assertTrue(npm['configuredOIDC'])
        self.assertIsNone(npm['environment'])
        self.assertEqual(npm['evidence'][-1]['file'], '.github/workflows/publish-npm.yml')

    def test_removed_original_has_no_supported_path(self):
        self.assertEqual(race.discover(CASE / 'removed'), [])

    def test_same_second_has_reachable_unapproved_use(self):
        result = race.explore('-gt')
        self.assertEqual(result['verdict'], 'unsafe')
        self.assertEqual(result['counterexample'][0]['action'], 'attacker:update-PR')
        self.assertTrue(result['counterexample'][-1]['state'][-1])

    def test_rejecting_equality_blocks_update_after_comment(self):
        self.assertEqual(race.explore('-ge')['verdict'], 'safe')

    def test_no_oidc_blocks_this_specific_property(self):
        self.assertEqual(race.explore('-gt', enabled=False)['verdict'], 'safe')

    def test_late_push_does_not_change_already_selected_sha(self):
        state = (0, 0, -1, False, False, False, -1, False, False)
        state = dict(race.successors(state, '-gt'))['fetch-current-PR']
        state = dict(race.successors(state, '-gt'))['attacker:update-PR']
        for action in ['timestamp-guard', 'checkout-and-build', 'upload', 'download', 'privileged-publish-use']:
            state = dict(race.successors(state, '-gt'))[action]
        self.assertFalse(state[-1])

    def test_original_extended_common_models_validate(self):
        schema = json.loads((ROOT / 'model/common-model.schema.json').read_text())
        for variant in ['vulnerable', 'removed']:
            for model in yaml_to_model.load_models(CASE / variant).values():
                jsonschema.validate(model, schema)

    def mutate(self, change):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copytree(CASE / 'vulnerable', root, dirs_exist_ok=True)
            path = root / '.github/workflows/marimo-bot.yml'
            data = yaml.load(path.read_text(), Loader=yaml_to_model.WorkflowLoader)
            change(data)
            path.write_text(yaml.safe_dump(data, sort_keys=False))
            return race.discover(root)

    def test_mismatched_checkout_ref_does_not_join(self):
        result = self.mutate(lambda d: d['jobs']['create-test-release']['steps'][1]['with'].update(ref='main'))
        self.assertEqual(result, [])

    def test_missing_needs_does_not_invent_artifact_identity(self):
        def change(data):
            for key in ['publish-test-release', 'publish_wasm_test']:
                data['jobs'][key]['needs'] = 'other-job'
        self.assertEqual(self.mutate(change), [])

    def test_wrong_artifact_blocks_npm_path(self):
        result = self.mutate(lambda d: d['jobs']['publish_wasm_test']['with'].update({'package-artifact-name': 'other'}))
        self.assertEqual([s['artifact'] for s in result[0]['sinks']], ['wheel-test-release'])

    def test_reusable_caller_permissions_limit_callee(self):
        result = self.mutate(lambda d: d['jobs']['publish_wasm_test'].update(permissions={'contents': 'read'}))
        npm = next(s for s in result[0]['sinks'] if s['artifact'] == 'frontend-test-branch')
        self.assertFalse(npm['configuredOIDC'])

    def test_supported_control_is_extracted_from_yaml(self):
        def change(data):
            step = data['jobs']['create-test-release']['steps'][0]
            step['run'] = step['run'].replace(' -gt ', ' -ge ')
        result = self.mutate(change)
        self.assertEqual(result[0]['rejectionOperator'], '-ge')
        self.assertEqual(race.explore(result[0]['rejectionOperator'])['verdict'], 'safe')

    def test_guard_replay_matches_timestamp_policy(self):
        candidate = race.discover(CASE / 'vulnerable')[0]
        self.assertEqual([r['accepted'] for r in race.replay_guard(candidate)], [True, True, False])
        fixed = copy.deepcopy(candidate)
        fixed['sourceScript'] = fixed['sourceScript'].replace(' -gt ', ' -ge ')
        self.assertEqual([r['accepted'] for r in race.replay_guard(fixed)], [True, False, False])


if __name__ == '__main__':
    unittest.main()
