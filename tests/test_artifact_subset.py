import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import discover_artifact_chains as discovery
import evaluate_artifact_subset as subset
import yaml_to_model

NUSMV = Path('/Applications/NuSMV-2.7.0-macos-universal/bin/NuSMV')


@unittest.skipUnless(NUSMV.exists(), 'NuSMV unavailable')
class ArtifactSubsetTests(unittest.TestCase):
    def run_repo(self, root):
        models = yaml_to_model.load_models(root)
        with tempfile.TemporaryDirectory() as folder:
            rows = [subset.evaluate(c, NUSMV, Path(folder), root)
                    for c in discovery.discover_candidates(models)]
        return {r['consumer']['workflow']['file'].split('/')[-1]: r for r in rows}

    def test_pinned_artifact_cases_from_yaml_without_annotations(self):
        rows = self.run_repo(ROOT)
        expected = {'artifact-a1-unsafe-consumer.yml': 'unsafe-counterexample',
                    'artifact-a2-safe-consumer.yml': 'safe-within-model',
                    'artifact-a3-download-only-consumer.yml': 'safe-within-model',
                    'artifact-a4-no-authority-consumer.yml': 'safe-within-model',
                    'artifact-a5-attest-consumer.yml': 'unsafe-counterexample'}
        self.assertEqual({k: r['status'] for k, r in rows.items()}, expected)
        trace = rows['artifact-a1-unsafe-consumer.yml']
        self.assertEqual(trace['counterexampleStages'][-1], 'authority_reached')
        self.assertIsInstance(trace['traceMap']['authority_reached']['line'], int)
        self.assertIn('sameObject', trace['unknownAssumptions'])

    def test_unsupported_action_does_not_become_safe(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(ROOT / '.github/workflows', root / '.github/workflows')
            path = root / '.github/workflows/artifact-a3-download-only-consumer.yml'
            text = path.read_text()
            text += '\n      - uses: unknown/action@v1\n'
            path.write_text(text)
            rows = self.run_repo(root)
            self.assertEqual(rows[path.name]['status'], 'unknown/unsupported')


    def test_extra_a5_shell_command_is_unsupported(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(ROOT / '.github/workflows', root / '.github/workflows')
            path = root / '.github/workflows/artifact-a5-attest-consumer.yml'
            text = path.read_text().replace('          target_branch=$(tr',
                                            '          exit 0\n          target_branch=$(tr')
            path.write_text(text)
            rows = self.run_repo(root)
            self.assertEqual(rows[path.name]['status'], 'unknown/unsupported')

    def test_unmatched_a5_metadata_flow_is_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(ROOT / '.github/workflows', root / '.github/workflows')
            path = root / '.github/workflows/artifact-a5-attest-consumer.yml'
            text = path.read_text().replace('steps.artifact-metadata.outputs.target_branch',
                                            'steps.other.outputs.target_branch')
            path.write_text(text)
            rows = self.run_repo(root)
            self.assertEqual(rows[path.name]['status'], 'unknown/unsupported')

    def test_extra_unguarded_artifact_use_cancels_safe_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(ROOT / '.github/workflows', root / '.github/workflows')
            path = root / '.github/workflows/artifact-a2-safe-consumer.yml'
            text = path.read_text()
            text += '''
      - name: Extra unguarded use
        env:
          ARTIFACT_FILE: ${{ runner.temp }}/trust-boundary-artifact/payload.txt
        run: cat "$ARTIFACT_FILE"
'''
            path.write_text(text)
            rows = self.run_repo(root)
            self.assertNotEqual(rows[path.name]['status'], 'safe-within-model')

    def test_unmatched_verification_is_not_safe(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(ROOT / '.github/workflows', root / '.github/workflows')
            path = root / '.github/workflows/artifact-a2-safe-consumer.yml'
            text = path.read_text().replace("expected_digest='c94960cc", "expected_digest=$(echo c94960cc")
            path.write_text(text)
            rows = self.run_repo(root)
            self.assertNotEqual(rows[path.name]['status'], 'safe-within-model')

    def test_a1_upload_outside_checkout_is_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(ROOT / '.github/workflows', root / '.github/workflows')
            path = root / '.github/workflows/artifact-a1-pr-producer.yml'
            path.write_text(path.read_text().replace(
                'path: .research-artifact-input/payload.txt',
                'path: /tmp/untracked-payload.txt'))
            rows = self.run_repo(root)
            self.assertEqual(rows['artifact-a1-unsafe-consumer.yml']['status'],
                             'unknown/unsupported')

    def test_a5_unrecognized_producer_copy_is_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(ROOT / '.github/workflows', root / '.github/workflows')
            path = root / '.github/workflows/artifact-a5-attest-producer.yml'
            path.write_text(path.read_text().replace(
                'cp .research-artifact-a5-input/head-ref.txt',
                'install .research-artifact-a5-input/head-ref.txt'))
            rows = self.run_repo(root)
            self.assertEqual(rows['artifact-a5-attest-consumer.yml']['status'],
                             'unknown/unsupported')

    def test_conditional_upload_is_not_unconditionally_trusted(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            shutil.copytree(ROOT / '.github/workflows', root / '.github/workflows')
            path = root / '.github/workflows/artifact-a1-pr-producer.yml'
            path.write_text(path.read_text().replace(
                '        id: upload-artifact',
                "        if: github.event.pull_request.head.repo.fork == false\n        id: upload-artifact"))
            rows = self.run_repo(root)
            self.assertEqual(rows['artifact-a1-unsafe-consumer.yml']['status'],
                             'unknown/unsupported')


if __name__ == '__main__':
    unittest.main()
