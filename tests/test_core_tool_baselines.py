import gzip
import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'results/core-artifact-tool-baselines'


class CoreToolBaselineTests(unittest.TestCase):
    def test_archived_inputs_and_classification_are_reproducible(self):
        report = json.loads((EVIDENCE / 'property-level-comparison.json').read_text())
        for name, digest in report['inputSHA256'].items():
            data = (ROOT / '.github/workflows' / name).read_bytes()
            self.assertEqual(hashlib.sha256(data).hexdigest(), digest)
        for tool, record in report['outputs'].items():
            compressed = (EVIDENCE / record['output']).read_bytes()
            self.assertEqual(hashlib.sha256(compressed).hexdigest(), record['compressedSHA256'])
            raw = gzip.decompress(compressed)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), record['rawSHA256'])
            payload = json.loads(raw)
            count = len(payload['findings']) if tool == 'poutine' else sum(
                len(run.get('results', [])) for run in payload['runs'])
            self.assertEqual(count, record['totalFindings'])
        for case in report['cases'].values():
            for suite in ('codeql-default', 'codeql-broad'):
                self.assertEqual(case[suite]['propertyLevelClass'], '対象findingなし')
                self.assertEqual(case[suite]['sameFindingLinksProducerSharedObjectConsumerAuthority'], 0)


if __name__ == '__main__':
    unittest.main()
