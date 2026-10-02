import copy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import verify_attack_pr_alignment as alignment

CASE = ROOT / 'experiments/public-cases/spotbugs-chain'
ANALYSIS = json.loads((ROOT / 'results/spotbugs-screening/conditional-chain-analysis.json').read_text())


class AttackPRAlignmentTests(unittest.TestCase):
    def test_actual_attack_changed_the_detected_local_executable(self):
        result = alignment.verify(CASE, ANALYSIS)
        self.assertEqual(result['status'], 'aligned')
        self.assertEqual(result['alignment'][0]['changedFile'], 'mvnw')
        self.assertEqual(result['alignment'][0]['sinkCommand'], './mvnw')
        self.assertTrue(result['alignment'][0]['addedUnconditionalRemoteShellBeforeMaven'])

    def case_with_metadata_change(self, change):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copy(CASE / 'manifest.json', root / 'manifest.json')
            shutil.copytree(CASE / '.github', root / '.github')
            shutil.copytree(CASE / 'external-action-cond', root / 'external-action-cond')
            shutil.copy(CASE / 'attack-pr-files.json', root / 'attack-pr-files.json')
            data = json.loads((CASE / 'attack-pr-metadata.json').read_text())
            change(data)
            (root / 'attack-pr-metadata.json').write_text(json.dumps(data))
            return alignment.verify(root, ANALYSIS)

    def test_unrelated_modified_file_does_not_align(self):
        result = self.case_with_metadata_change(
            lambda data: data['changedFiles'][0].update(filename='README.md'))
        self.assertEqual(result['status'], 'no-attack-file-alignment')

    def test_wrong_base_revision_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'base'):
            self.case_with_metadata_change(
                lambda data: data['base'].update(sha='0' * 40))

    def test_supplied_analysis_must_match_recomputed_source(self):
        changed = copy.deepcopy(ANALYSIS)
        changed['findings'] = []
        with self.assertRaisesRegex(ValueError, 'analysis'):
            alignment.verify(CASE, changed)

    def test_attack_diff_digest_is_required(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copy(CASE / 'manifest.json', root / 'manifest.json')
            shutil.copy(CASE / 'attack-pr-metadata.json', root / 'attack-pr-metadata.json')
            shutil.copy(CASE / 'attack-pr-files.json', root / 'attack-pr-files.json')
            shutil.copytree(CASE / '.github', root / '.github')
            shutil.copytree(CASE / 'external-action-cond', root / 'external-action-cond')
            p = root / 'attack-pr-files.json'
            p.write_text(p.read_text().replace('gist.githubusercontent.com', 'example.invalid'))
            with self.assertRaisesRegex(ValueError, 'digest'):
                alignment.verify(root, ANALYSIS)


if __name__ == '__main__':
    unittest.main()
