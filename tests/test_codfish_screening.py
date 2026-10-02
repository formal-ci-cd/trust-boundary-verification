import gzip
import hashlib
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / 'experiments/public-cases/codfish-prt-scan'
RESULTS = ROOT / 'results/codfish-prt-scan-screening'


class CodfishScreeningTests(unittest.TestCase):
    def test_pinned_files_and_single_line_control(self):
        manifest = json.loads((CASE / 'manifest.json').read_text())
        for record in manifest['files']:
            source = CASE / record['path']
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),
                             record['sha256'])
        original = (CASE / 'original/.github/workflows/pr.yml').read_text()
        control = (CASE / 'fork-excluded-control/.github/workflows/pr.yml').read_text()
        self.assertEqual(control, original.replace(
            '  build:\n',
            '  build:\n    if: github.event.pull_request.head.repo.full_name == github.repository\n'))

    def test_historical_codeql_warns_on_original_build_only(self):
        index = json.loads((RESULTS / 'evidence-index.json').read_text())
        for label, record in index['cases'].items():
            path = RESULTS / record['sarif']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                             record['sha256'])
            with gzip.open(path, 'rt') as file:
                results = json.load(file)['runs'][0]['results']
            self.assertEqual(len(results), record['totalAlerts'], label)
            target = [r for r in results if r.get('locations', [{}])[0]
                      .get('physicalLocation', {}).get('artifactLocation', {})
                      .get('uri', '').endswith('/pr.yml')]
            self.assertEqual(len(target), len(record['targetWorkflowAlerts']), label)
        self.assertEqual(len(index['cases']['historical-original']['targetWorkflowAlerts']), 2)
        self.assertEqual(index['cases']['historical-fork-control']['targetWorkflowAlerts'], [])


if __name__ == '__main__':
    unittest.main()
