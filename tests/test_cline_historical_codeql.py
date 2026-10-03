import gzip
import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
HISTORICAL = ROOT / 'results/cline-agent-cache-boundary/historical-codeql-2026-02'


class ClineHistoricalCodeQLTests(unittest.TestCase):
    def test_incident_time_codeql_results_match_index(self):
        index = json.loads((HISTORICAL / 'evidence-index.json').read_text())
        self.assertEqual(index['cliVersion'], '2.24.1')
        self.assertEqual(index['queryPack'], 'codeql/actions-queries@0.6.19')
        expected = {
            ('pre', 'default'): (15, 1),
            ('post', 'default'): (12, 1),
            ('pre', 'security-and-quality'): (15, 8),
            ('post', 'security-and-quality'): (12, 6),
        }
        self.assertEqual(len(index['cases']), len(expected))
        for case in index['cases']:
            key = case['case'], case['suite']
            self.assertIn(key, expected)
            compressed = (HISTORICAL / case['file']).read_bytes()
            self.assertEqual(hashlib.sha256(compressed).hexdigest(), case['sha256'])
            raw = gzip.decompress(compressed)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), case['sourceSHA256'])
            results = json.loads(raw)['runs'][0]['results']
            self.assertEqual((case['extractedActionsFiles'], len(results)), expected[key])
            self.assertEqual(case['findingCount'], len(results))
            self.assertFalse(case['issueToAiCacheReleaseFinding'])
            self.assertTrue(all(result['ruleId'] in {
                'actions/missing-workflow-permissions', 'actions/unpinned-tag'}
                for result in results))


if __name__ == '__main__':
    unittest.main()
