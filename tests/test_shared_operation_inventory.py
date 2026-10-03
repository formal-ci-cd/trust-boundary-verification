import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import extract_shared_operations as shared
import yaml_to_model

CASE = ROOT / 'experiments/public-cases/tanstack'


class SharedOperationInventoryTests(unittest.TestCase):
    def test_supplied_composite_snapshot_yields_cross_workflow_cache_candidate(self):
        contracts = shared.load_contracts(CASE / 'composite-contracts.json')
        models = yaml_to_model.load_models(CASE / 'pre-incident')
        inventory = shared.extract(models, contracts)
        pairs = shared.pair_cache(inventory['operations'])
        matched = [p for p in pairs if p['producer']['workflow'].endswith('bundle-size.yml')
                   and p['producer']['job'] == 'benchmark-pr'
                   and p['consumer']['workflow'].endswith('release.yml')]
        self.assertEqual(len(matched), 1)
        pair = matched[0]
        self.assertEqual(pair['sameObject'], 'unknown')
        self.assertEqual(pair['saveRestoreOrder'], 'unknown')
        self.assertIn('expression-equal', pair['keyCompatibility'])
        self.assertTrue(any(o['resolution'].endswith('runtime identity unknown; snapshot is not proof of runtime tag resolution')
                            for o in inventory['operations']))

    def test_missing_composite_stays_unsupported(self):
        models = yaml_to_model.load_models(CASE / 'pre-incident')
        inventory = shared.extract(models, {})
        self.assertEqual(inventory['operations'], [])
        self.assertTrue(any('TanStack/config/.github/setup@main' == o['action']
                            for o in inventory['unsupportedActions']))

    def test_checksum_failure_rejects_wrong_composite(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'map.json'
            path.write_text(json.dumps({'x@v1': {'path': str(CASE / 'external-setup/.github/setup/action.yml'),
                                                 'sha256': '0' * 64}}))
            with self.assertRaises(ValueError):
                shared.load_contracts(path)


if __name__ == '__main__':
    unittest.main()
