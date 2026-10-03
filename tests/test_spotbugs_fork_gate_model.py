import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import spotbugs_fork_gate_model as gate

CASE = ROOT / 'experiments/public-cases/spotbugs-chain'
SAVED = ROOT / 'results/spotbugs-screening/fork-gate-model'


class SpotbugsGateModelTests(unittest.TestCase):
    def test_original_and_single_edit_control(self):
        original = CASE / '.github/workflows/sonarqube.yml'
        control = CASE / 'codeql-ablation/gate-correct-fork/.github/workflows/sonarqube.yml'
        self.assertEqual(control.read_text(), original.read_text().replace(
            gate.BASE_GATE, gate.FORK_GATE))
        self.assertTrue(gate.explore(gate.BASE_GATE)['possibleExternalForkPath'])
        self.assertFalse(gate.explore(gate.FORK_GATE)['possibleExternalForkPath'])

    def test_saved_nusmv_matches_source_and_bfs(self):
        report = json.loads((SAVED / 'analysis.json').read_text())
        self.assertEqual(len(report['cases']), 2)
        for case in report['cases']:
            model = SAVED / case['model']
            self.assertEqual(model.read_text(), gate.render(case['gate']))
            self.assertEqual(hashlib.sha256(model.read_bytes()).hexdigest(),
                             case['modelSHA256'])
            workflow = ROOT / case['workflow']
            self.assertEqual(hashlib.sha256(workflow.read_bytes()).hexdigest(),
                             case['workflowSHA256'])
            self.assertEqual(gate.explore(case['gate']), case['bfs'])
            expected = 'false' if case['bfs']['possibleExternalForkPath'] else 'true'
            output = (SAVED / (case['label'] + '.nusmv.txt')).read_text()
            self.assertIn('-- specification AG !(external_fork & bad)  is ' + expected,
                          output)


if __name__ == '__main__':
    unittest.main()
