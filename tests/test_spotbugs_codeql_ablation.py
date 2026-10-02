import gzip
import hashlib
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from materialize_spotbugs_codeql_ablation import (  # noqa: E402
    BYPASSABLE_GUARD, FORK_GUARD, INDIRECT_REF, INLINE_REF, OPEN_GATE, ORIGINAL_GATE, variants,
)


class SpotBugsCodeQLAblationTest(unittest.TestCase):
    def test_only_declared_lines_change(self):
        original = (Path(__file__).resolve().parents[1] /
                    'experiments/public-cases/spotbugs-chain/.github/workflows/sonarqube.yml').read_text()
        controls = variants(original)
        self.assertEqual(controls['gate-on-inline'], original.replace(INDIRECT_REF, INLINE_REF))
        self.assertEqual(controls['gate-off-indirect'], original.replace(ORIGINAL_GATE, OPEN_GATE))
        self.assertEqual(controls['gate-off-inline'], original.replace(ORIGINAL_GATE, OPEN_GATE)
                         .replace(INDIRECT_REF, INLINE_REF))
        self.assertEqual(controls['gate-correct-fork'], original.replace(ORIGINAL_GATE, FORK_GUARD))
        self.assertEqual(controls['gate-bypassable-or'],
                         original.replace(ORIGINAL_GATE, BYPASSABLE_GUARD))

    def test_refuses_changed_source(self):
        with self.assertRaises(ValueError):
            variants('on: pull_request_target\n')

    def test_saved_alerts_and_true_fork_control(self):
        root = Path(__file__).resolve().parents[1]
        index = json.loads((root / 'results/spotbugs-screening/codeql-ablation/evidence-index.json').read_text())
        expected = {
            'gate-on-indirect': [], 'gate-on-inline': [],
            'gate-off-indirect': ['actions/untrusted-checkout/critical'],
            'gate-off-inline': ['actions/untrusted-checkout/critical'],
            'gate-correct-fork': [],
            'gate-bypassable-or': [],
        }
        for name, rules in expected.items():
            record = index['variants'][name]
            source = root / record['source']
            archive = root / record['sarif']
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), record['sourceSha256'])
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(), record['sarifGzipSha256'])
            findings = json.loads(gzip.decompress(archive.read_bytes()))['runs'][0]['results']
            self.assertEqual([finding['ruleId'] for finding in findings], rules)
        control = root / 'results/spotbugs-screening/codeql-ablation'
        self.assertEqual(json.loads((control / 'gate-correct-fork-analysis.json').read_text())['status'],
                         'fork-excluded')
        self.assertEqual(json.loads((control / 'gate-correct-fork-model/analysis.json').read_text())['status'],
                         'fork-excluded')
        bypassed = json.loads((control / 'gate-bypassable-or-analysis.json').read_text())
        self.assertEqual(bypassed['status'], 'analyzed')
        self.assertEqual(len(bypassed['findings']), 1)

    def test_other_scanners_do_not_distinguish_true_fork_control(self):
        root = Path(__file__).resolve().parents[1]
        index = json.loads((root / 'results/spotbugs-screening/fork-guard-baselines/evidence-index.json').read_text())
        expected_counts = {'zizmor': 8, 'sisakulint': 15}
        for tool, count in expected_counts.items():
            outputs = []
            for variant in ('original', 'correctForkGuard'):
                record = index['variants'][variant]
                self.assertEqual(hashlib.sha256((root / record['source']).read_bytes()).hexdigest(),
                                 record['sourceSha256'])
                baseline = record['tools'][tool]
                archive = root / baseline['sarif']
                self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),
                                 baseline['sarifGzipSha256'])
                outputs.append(json.loads(gzip.decompress(archive.read_bytes()))['runs'][0]['results'])
                self.assertEqual(len(outputs[-1]), count)
            self.assertEqual(outputs[0], outputs[1])
            self.assertTrue(index['sameResults'][tool])
        outputs = []
        for variant in ('original', 'correctForkGuard'):
            baseline = index['variants'][variant]['tools']['poutine']
            archive = root / baseline['output']
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),
                             baseline['outputGzipSha256'])
            findings = json.loads(gzip.decompress(archive.read_bytes()))['findings']
            self.assertEqual(len(findings), 2)
            outputs.append(findings)
        self.assertEqual(outputs[0], outputs[1])
        self.assertIn('untrusted_checkout_exec', [hit['rule_id'] for hit in outputs[0]])
        self.assertTrue(index['sameResults']['poutine'])


if __name__ == '__main__':
    unittest.main()
