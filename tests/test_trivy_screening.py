import gzip
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'experiments/public-cases/trivy-screening'
RESULTS = ROOT / 'results/trivy-screening'


class TrivyScreeningTests(unittest.TestCase):
    def test_incident_era_source_and_codeql_alert_are_pinned(self):
        manifest = json.loads((CASE / 'manifest.json').read_text())
        self.assertEqual(manifest['workflowLastChangedCommit'],
                         'ccf5a5ad09e482bb1b3f2ef5a0334182ec300ac2')
        for source in manifest['files']:
            digest = hashlib.sha256((CASE / source['path']).read_bytes()).hexdigest()
            self.assertEqual(digest, source['sha256'])
        index = json.loads((RESULTS / 'evidence-index.json').read_text())
        self.assertEqual(len(index['comparisons']), 2)
        for entry in index['comparisons']:
            path = RESULTS / entry['sarif']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                             entry['compressedSHA256'])
            with gzip.open(path, 'rt') as f:
                results = json.load(f)['runs'][0]['results']
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]['ruleId'], 'actions/untrusted-checkout/critical')
            self.assertEqual(entry['findings'][0]['rule'], results[0]['ruleId'])


if __name__ == '__main__':
    unittest.main()
