import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import ultralytics_cache_chain as cache_chain

CASE = ROOT / 'experiments/public-cases/ultralytics'


class UltralyticsCacheChainTests(unittest.TestCase):
    def test_pinned_originals_and_documented_path(self):
        manifest = json.loads((CASE / 'manifest.json').read_text())
        cache_chain.checked(manifest, CASE)
        case = cache_chain.analyze(CASE, 'pre-incident', 'external-action-2024-12-04')
        self.assertEqual(case['status'], 'potential-chain')
        self.assertTrue(case['entryCapable'])
        self.assertTrue(case['publisherCachePath'])
        self.assertIn("github.actor == 'glenn-jocher'", case['configuredPublisher']['guard'])
        self.assertEqual([e['role'] for e in case['staticEvidence']][-4:],
                         ['implicit-pip-cache-restore', 'dependency-install', 'build', 'publish'])
        self.assertEqual(cache_chain.explore(True, True)['verdict'], 'possible')

    def test_each_real_change_interrupts_the_supported_path(self):
        fixed_action = cache_chain.analyze(CASE, 'pre-incident', 'external-action-fixed-2024-12-05')
        isolated_publish = cache_chain.analyze(CASE, 'mitigation', 'external-action-2024-12-04')
        self.assertFalse(fixed_action['entryCapable'])
        self.assertFalse(isolated_publish['publisherCachePath'])
        self.assertEqual(cache_chain.explore(False, True)['verdict'], 'no-path-in-supported-model')
        self.assertEqual(cache_chain.explore(True, False)['verdict'], 'no-path-in-supported-model')

    def test_unknown_runtime_facts_are_not_promoted_to_observation(self):
        case = cache_chain.analyze(CASE, 'pre-incident', 'external-action-2024-12-04')
        for fact in ['sameCacheObject', 'cacheWriteSucceeded', 'publisherGuardPassed']:
            self.assertTrue(case['unknownExternalFacts'][fact].startswith('unknown:'))
        trace = cache_chain.explore(True, True)['conditionalCounterexample']
        self.assertTrue(trace[-1]['state'][-1])
        self.assertTrue(all(trace[0]['state'][i] for i in [1, 2, 3]))


if __name__ == '__main__':
    unittest.main()
