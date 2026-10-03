import gzip
import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
HISTORICAL = ROOT / 'results/tanstack-cache-chain/historical-codeql-2026-05'
CURRENT_CONTROL = ROOT / 'results/tanstack-cache-chain/without-workflow-dispatch-control'


class TanStackHistoricalCodeQLTests(unittest.TestCase):
    def test_incident_time_codeql_flags_real_pr_event_and_mitigation_removes_it(self):
        index = json.loads((HISTORICAL / 'evidence-index.json').read_text())
        self.assertEqual(index['cliVersion'], '2.25.4')
        self.assertEqual(index['queryPack'], 'codeql/actions-queries@0.6.27')
        expected = {'pre-incident': 'pull_request_target',
                    'without-dispatch-control': 'pull_request_target',
                    'mitigation': None}
        for case in index['cases']:
            source = HISTORICAL / case['file']
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), case['sha256'])
            results = json.loads(gzip.decompress(source.read_bytes()))['runs'][0]['results']
            self.assertEqual(case['extractedActionsFiles'], 8)
            self.assertEqual(len(results), case['findingCount'])
            event = expected[case['case']]
            if event is None:
                self.assertEqual(results, [])
            else:
                self.assertEqual(len(results), 1)
                self.assertEqual(results[0]['ruleId'], 'actions/cache-poisoning/poisonable-step')
                self.assertIn(event, results[0]['message']['text'])

        current = json.loads(gzip.decompress(
            (CURRENT_CONTROL / 'codeql-default.sarif.gz').read_bytes()))
        self.assertEqual(current['runs'][0]['results'], [])
        for case in index['mixedVersionControl']['cases']:
            source = HISTORICAL / case['file']
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), case['sha256'])
            results = json.loads(gzip.decompress(source.read_bytes()))['runs'][0]['results']
            self.assertEqual(len(results), case['findingCount'])
            if case['case'] == 'pre':
                self.assertIn('workflow_dispatch', results[0]['message']['text'])


if __name__ == '__main__':
    unittest.main()
