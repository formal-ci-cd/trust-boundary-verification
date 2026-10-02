import hashlib
import gzip
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import conditional_checkout_model as model

CASE = ROOT / 'experiments/public-cases/spotbugs-chain'
EXTERNAL = CASE / 'external-action-cond'


class ConditionalCheckoutModelTests(unittest.TestCase):
    def test_original_and_one_edit_control(self):
        manifest = json.loads((CASE / 'manifest.json').read_text())
        original = (CASE / '.github/workflows/sonarqube.yml').read_text()
        control_path = CASE / manifest['researchControl']['path']
        control = control_path.read_text()
        self.assertEqual(hashlib.sha256(control_path.read_bytes()).hexdigest(),
                         manifest['researchControl']['sha256'])
        self.assertEqual(control, original.replace(
            'if_true: refs/pull/${{ github.event.pull_request.number }}/merge',
            'if_true: ${{ github.sha }}'))
        vulnerable = model.extract(CASE, EXTERNAL, 'haya14busa/action-cond', 'v1')
        fixed = model.extract(CASE / 'safe-ref-control', EXTERNAL,
                              'haya14busa/action-cond', 'v1')
        self.assertTrue(vulnerable['prRefSelected'])
        self.assertFalse(fixed['prRefSelected'])
        self.assertEqual(model.explore(True)['verdict'], 'unsafe')
        self.assertEqual(model.explore(False)['verdict'], 'safe')
        self.assertEqual(vulnerable['configuredSecrets'], ['PAT_TO_FORK', 'SONAR_TOKEN'])

    def test_different_checkout_output_is_not_modeled(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copytree(CASE / '.github', root / '.github')
            path = root / '.github/workflows/sonarqube.yml'
            path.write_text(path.read_text().replace(
                'ref: ${{ steps.condval.outputs.value }}', 'ref: ${{ github.sha }}'))
            self.assertIsNone(model.extract(root, EXTERNAL, 'haya14busa/action-cond', 'v1'))

    def test_unrecognized_external_action_is_not_treated_as_safe(self):
        self.assertIsNone(model.extract(CASE, EXTERNAL, 'haya14busa/action-cond', 'v2'))

    def test_unsuccessful_checkout_cannot_reach_local_pr_code(self):
        state = (0, True, False, False, True, False, True, True, False)
        for action in ('select-ref', 'checkout-ref', 'run-local-executable'):
            state = dict(model.successors(state, True))[action]
        self.assertFalse(state[-1])

    def test_saved_codeql_control_comparison(self):
        results = ROOT / 'results/spotbugs-screening'
        comparison = json.loads((results / 'codeql-safe-ref-control-comparison.json').read_text())
        self.assertEqual(comparison['controlSha256'], hashlib.sha256(
            (CASE / 'safe-ref-control/.github/workflows/sonarqube.yml').read_bytes()
        ).hexdigest())
        for suite, details in comparison['variants'].items():
            for variant, filename in [('original', details['originalSarif']),
                                      ('safeRefControl', details['controlSarif'])]:
                with gzip.open(results / filename, 'rt') as f:
                    saved = json.load(f)['runs'][0]['results']
                self.assertEqual(len(saved), details[variant]['count'], suite)
            self.assertEqual(details['original']['alerts'],
                             details['safeRefControl']['alerts'], suite)


if __name__ == '__main__':
    unittest.main()
