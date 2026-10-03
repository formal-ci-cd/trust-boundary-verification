from pathlib import Path
import sys
import shutil
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import cache_two_run_model as model
import evaluate_cache_subset as evaluation
import extract_shared_operations as shared
import yaml_to_model

CASE = ROOT / 'experiments/public-cases/tanstack'
NUSMV = Path('/Applications/NuSMV-2.7.0-macos-universal/bin/NuSMV')


class TwoRunCacheModelTests(unittest.TestCase):
    def test_restore_before_save_does_not_create_bad_state(self):
        facts = {name: True for name in model.FACTS}
        facts['integrityVerified'] = False
        state = model.initial(facts)
        for actor in ('consumer', 'consumer', 'consumer', 'producer', 'producer'):
            state = model.transition(state, actor)
        self.assertFalse(state[4])
        state = model.initial(facts)
        for actor in ('producer', 'producer', 'consumer', 'consumer', 'consumer'):
            state = model.transition(state, actor)
        self.assertTrue(state[4])

    def test_trusted_verification_or_no_shared_object_blocks_all_schedules(self):
        for blocked in ('integrityVerified', 'sameObject'):
            facts = {name: 'true' for name in model.FACTS}
            facts['integrityVerified'] = 'false'
            facts[blocked] = 'true' if blocked == 'integrityVerified' else 'false'
            self.assertEqual(model.explore(facts)['verdict'], 'safe')

    def test_event_condition_subset_keeps_unknown_unknown(self):
        self.assertEqual(evaluation.event_status("github.event_name == 'pull_request_target'", 'pull_request'), 'false')
        self.assertEqual(evaluation.event_status("unknown && github.event_name == 'pull_request_target'", 'pull_request'), 'false')
        self.assertEqual(evaluation.event_status("unknown || github.event_name == 'pull_request_target'", 'pull_request'), 'unknown')


@unittest.skipUnless(NUSMV.exists(), 'NuSMV unavailable')
class TanStackCommonCachePathTests(unittest.TestCase):
    def setUp(self):
        self.contracts = shared.load_contracts(CASE / 'composite-contracts.json')

    def analyze(self, variant, policy):
        root = CASE / variant
        models = yaml_to_model.load_models(root)
        inventory = shared.extract(models, self.contracts)
        inventory['cacheCandidates'] = shared.pair_cache(inventory['operations'])
        with tempfile.TemporaryDirectory() as directory:
            rows = evaluation.evaluate(root, models, inventory, self.contracts,
                                       NUSMV, Path(directory), policy)
        return [r for r in rows if r['producer']['file'].endswith('bundle-size.yml')
                and r['producer']['job'] == 'benchmark-pr'
                and r['consumer']['file'].endswith('release.yml')]

    def test_original_and_mitigation_differ_only_under_historical_scope_policy(self):
        before = self.analyze('pre-incident', 'historical-pre-2026-06-26')
        after = self.analyze('mitigation', 'historical-pre-2026-06-26')
        self.assertEqual(len(before), len(after), 1)
        self.assertEqual(before[0]['status'], 'unsafe-counterexample')
        self.assertEqual(after[0]['status'], 'safe-within-model')
        self.assertEqual(after[0]['sharedObject']['sameObject'], 'false')
        self.assertEqual(before[0]['unknownAssumptions']['sameObject'], True)
        self.assertEqual(before[0]['unknownAssumptions']['integrityVerified'], False)
        self.assertEqual(before[0]['order']['counterexample'][-1]['bad'], True)
        self.assertEqual(before[0]['evidence'][0]['line'], 7)
        self.assertIsInstance(before[0]['producer']['compositeLine'], int)

    def test_without_historical_policy_mitigation_is_not_proven_safe(self):
        after = self.analyze('mitigation', 'unknown')
        self.assertEqual(after[0]['sharedObject']['sameObject'], 'unknown')
        self.assertEqual(after[0]['status'], 'unsafe-counterexample')

    def test_disabled_restore_step_blocks_same_model_property(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(CASE / 'pre-incident/.github/workflows', root / '.github/workflows')
            release = root / '.github/workflows/release.yml'
            source = release.read_text()
            release.write_text(source.replace('      - name: Setup Tools\n        uses:',
                                              '      - name: Setup Tools\n        if: false\n        uses:', 1))
            models = yaml_to_model.load_models(root)
            inventory = shared.extract(models, self.contracts)
            inventory['cacheCandidates'] = shared.pair_cache(inventory['operations'])
            rows = evaluation.evaluate(root, models, inventory, self.contracts,
                                       NUSMV, root, 'historical-pre-2026-06-26')
            target = next(r for r in rows if r['producer']['file'].endswith('bundle-size.yml')
                          and r['producer']['job'] == 'benchmark-pr')
            self.assertEqual(target['facts']['consumerCallEnabled'], 'false')
            self.assertEqual(target['status'], 'safe-within-model')

    def test_read_only_permission_control_blocks_same_model_property(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(CASE / 'pre-incident/.github/workflows', root / '.github/workflows')
            release = root / '.github/workflows/release.yml'
            source = release.read_text()
            release.write_text(source.replace('  contents: write\n', '  contents: read\n', 1))
            models = yaml_to_model.load_models(root)
            inventory = shared.extract(models, self.contracts)
            inventory['cacheCandidates'] = shared.pair_cache(inventory['operations'])
            rows = evaluation.evaluate(root, models, inventory, self.contracts,
                                       NUSMV, root, 'historical-pre-2026-06-26')
            target = next(r for r in rows if r['producer']['file'].endswith('bundle-size.yml')
                          and r['producer']['job'] == 'benchmark-pr')
            self.assertEqual(target['facts']['hasAuthority'], 'false')
            self.assertEqual(target['status'], 'safe-within-model')
            self.assertEqual(target['order']['counterexample'], [])

    def test_missing_action_contract_yields_no_safe_claim(self):
        root = CASE / 'pre-incident'
        models = yaml_to_model.load_models(root)
        inventory = shared.extract(models, {})
        inventory['cacheCandidates'] = shared.pair_cache(inventory['operations'])
        with tempfile.TemporaryDirectory() as directory:
            rows = evaluation.evaluate(root, models, inventory, {},
                                       NUSMV, Path(directory), 'historical-pre-2026-06-26')
        self.assertEqual(rows, [])
        self.assertTrue(inventory['unsupportedActions'])


if __name__ == '__main__':
    unittest.main()
