import gzip
import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'experiments/public-cases/elementary-screening'
RESULTS = ROOT / 'results/elementary-screening'


class ElementaryScreeningTests(unittest.TestCase):
    def test_pinned_exploited_workflow_is_flagged_by_both_baselines(self):
        manifest = json.loads((CASE / 'manifest.json').read_text())
        for record in manifest['files']:
            path = CASE / record['snapshot']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record['sha256'])

        index = json.loads((RESULTS / 'evidence-index.json').read_text())
        for record in index['compressedFiles']:
            path = RESULTS / record['path']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), record['sha256'])

        sarif = json.loads(gzip.decompress((RESULTS / 'default.sarif.gz').read_bytes()))
        alerts = sarif['runs'][0]['results']
        self.assertTrue(any(alert['ruleId'] == 'actions/code-injection/critical'
                            and alert['locations'][0]['physicalLocation']['region']['startLine'] == 17
                            for alert in alerts))
        zizmor = json.loads(gzip.decompress((RESULTS / 'zizmor.json.gz').read_bytes()))
        self.assertTrue(any(alert['ident'] == 'template-injection'
                            and any(location['symbolic']['kind'] == 'Primary'
                                    and location['concrete']['location']['start_point']['row'] == 16
                                    for location in alert['locations'])
                            for alert in zizmor))
