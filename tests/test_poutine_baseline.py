import collections
import gzip
import hashlib
import json
from pathlib import Path
import unittest


RESULTS = Path(__file__).resolve().parents[1] / 'results/additional-poutine-baseline'


class PoutineBaselineTests(unittest.TestCase):
    def test_full_outputs_match_index_and_do_not_claim_silent_scans(self):
        index = json.loads((RESULTS / 'evidence-index.json').read_text())
        self.assertEqual(index['version'], '1.1.6')
        cases = {case['name']: case for case in index['cases']}
        self.assertEqual(set(cases), {
            'tanstack-pre', 'tanstack-mitigation', 'tanstack-control',
            'cline-pre', 'cline-mitigation'})
        for case in cases.values():
            output = RESULTS / case['result']
            self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), case['sha256'])
            report = json.loads(gzip.decompress(output.read_bytes()))
            findings = report['findings']
            self.assertEqual(len(findings), case['findingCount'])
            self.assertEqual(dict(collections.Counter(f['rule_id'] for f in findings)),
                             case['ruleCounts'])
            self.assertTrue(findings)
            self.assertTrue(all(f['purl'].endswith('repository_url=%2Finput')
                                for f in findings))
            if case['name'].startswith('tanstack'):
                self.assertEqual(set(case['ruleCounts']), {
                    'github_action_from_unverified_creator_used', 'unpinnable_action'})
                self.assertTrue(any(f.get('meta', {}).get('job') == 'benchmark-pr'
                                    for f in findings))
                self.assertTrue(any(f.get('meta', {}).get('job') == 'release'
                                    for f in findings))

        cline = json.loads(gzip.decompress((RESULTS / cases['cline-pre']['result']).read_bytes()))
        entry = [f for f in cline['findings'] if f.get('meta', {}).get('path', '').endswith(
            'claude-issue-triage.yml')]
        self.assertEqual([f['rule_id'] for f in entry],
                         ['github_action_from_unverified_creator_used'])
        self.assertTrue(all(f.get('meta', {}).get('path', '').endswith('publish.yml')
                            for f in cline['findings'] if f['rule_id'] == 'injection'))


if __name__ == '__main__':
    unittest.main()
