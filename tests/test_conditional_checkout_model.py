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
import conditional_checkout_chain as chain
import verify_conditional_action_runtime as runtime

CASE = ROOT / 'experiments/public-cases/spotbugs-chain'
EXTERNAL = CASE / 'external-action-cond'
UPSTREAM = ROOT / 'experiments/public-cases/spotbugs-upstream-change'


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

    def test_true_fork_guard_is_reported_separately(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copytree(CASE / '.github', root / '.github')
            path = root / '.github/workflows/sonarqube.yml'
            path.write_text(path.read_text().replace(
                "github.repository == 'spotbugs/sonar-findbugs'",
                'github.event.pull_request.head.repo.full_name == github.repository'))
            self.assertIsNone(model.extract(root, EXTERNAL, 'haya14busa/action-cond', 'v1'))
            self.assertEqual(model.fork_excluded_jobs(root, EXTERNAL, 'haya14busa/action-cond', 'v1'),
                             [{'file': '.github/workflows/sonarqube.yml', 'job': 'build'}])

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

    def test_saved_other_scanners_control_comparison(self):
        results = ROOT / 'results/spotbugs-screening'
        comparison = json.loads((results / 'additional-control-scanners-comparison.json').read_text())
        original = 'experiments/public-cases/spotbugs-chain/.github/workflows/sonarqube.yml'
        control = 'experiments/public-cases/spotbugs-chain/safe-ref-control/.github/workflows/sonarqube.yml'
        for tool, details in comparison['tools'].items():
            normalized = []
            for variant, path in [('original', original), ('control', control)]:
                record = details['variants'][variant]
                archive = results / record['path']
                self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),
                                 record['sha256'])
                with gzip.open(archive, 'rt') as f:
                    hits = json.load(f)['runs'][0]['results']
                self.assertEqual(len(hits), record['count'], tool)
                self.assertEqual(sorted(item['ruleId'] for item in hits),
                                 record['rules'], tool)
                normalized.append(json.dumps(hits, sort_keys=True).replace(path, 'WORKFLOW'))
            self.assertEqual(normalized[0], normalized[1], tool)

    def test_actual_upstream_change_is_not_misreported_as_proven_safe(self):
        manifest = json.loads((UPSTREAM / 'manifest.json').read_text())
        for record in manifest['files']:
            path = UPSTREAM / record['localPath']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                             record['sha256'])
        before = UPSTREAM / 'before'
        after = UPSTREAM / 'after'
        self.assertIsNone(model.extract(before, EXTERNAL, 'haya14busa/action-cond', 'v1'))
        self.assertEqual(model.unsupported_environment_jobs(before),
                         [{'file': '.github/workflows/sonarqube.yml', 'job': 'build'}])
        self.assertEqual(model.unsupported_environment_jobs(after), [])
        self.assertEqual(chain.discover(before, EXTERNAL, 'haya14busa/action-cond', 'v1')['status'],
                         'unsupported-environment-gate')
        self.assertEqual(chain.discover(after, EXTERNAL, 'haya14busa/action-cond', 'v1')['status'],
                         'no-supported-path')
        saved = ROOT / 'results/spotbugs-upstream-change'
        for variant, status in [('before', 'unsupported-environment-gate'),
                                ('after', 'no-supported-path')]:
            self.assertEqual(json.loads((saved / (variant + '-static.json')).read_text())['status'],
                             status)
            self.assertEqual(json.loads((saved / (variant + '-model/analysis.json')).read_text())['status'],
                             status)

    def test_saved_external_action_runtime_matches_pinned_bundle(self):
        result = json.loads((ROOT / 'results/spotbugs-screening/external-action-runtime.json').read_text())
        bundle = EXTERNAL / 'index.js'
        self.assertEqual(result['bundleSha256'], hashlib.sha256(bundle.read_bytes()).hexdigest())
        self.assertEqual(result['status'], 'matched')
        self.assertEqual(result['observed'],
                         {'true': 'refs/pull/1116/merge', 'false': 'refs/heads/master'})
        self.assertEqual(runtime.parse_output(
            'value<<marker\nrefs/pull/1116/merge\nmarker\n'), 'refs/pull/1116/merge')


if __name__ == '__main__':
    unittest.main()
