import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import agent_cache_boundary as chain

CASE = ROOT / 'experiments/public-cases/cline'


class AgentCacheBoundaryTests(unittest.TestCase):
    def test_original_and_official_mitigation(self):
        chain.check_manifest(CASE)
        original = chain.scan(CASE, 'pre-incident')
        fixed = chain.scan(CASE, 'mitigation')
        self.assertEqual((original['workflowCount'], fixed['workflowCount']), (15, 12))
        self.assertEqual(len(original['producers']), 1)
        self.assertEqual(len(original['consumers']), 2)
        self.assertEqual(len(fixed['producers']), 0)
        self.assertEqual(len(fixed['consumers']), 0)
        npm = next(c for c in original['consumers'] if c['workflow'].endswith('npm-nightly.yaml'))
        self.assertEqual(npm['secretsAtPublicationStep'], ['NPM_RELEASE_TOKEN'])
        self.assertEqual(chain.explore(True)['verdict'], 'possible')
        self.assertEqual(chain.explore(False)['verdict'], 'no-path-in-supported-model')
        witness = chain.explore(True)['conditionalCounterexample']
        self.assertTrue(all(witness['assumptions'].values()))
        self.assertTrue(witness['trace'][-1]['state'][-1])

    def test_agent_shell_permission_controls_the_path(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            shutil.copytree(CASE / 'pre-incident', temp / 'pre-incident')
            workflow = temp / 'pre-incident/.github/workflows/claude-issue-triage.yml'
            workflow.write_text(workflow.read_text().replace(
                'Bash,Read,Write,Edit,Glob,Grep,WebFetch,WebSearch',
                'Read,Glob,Grep,WebFetch,WebSearch'))
            row = chain.scan(temp, 'pre-incident')
            self.assertEqual(row['producers'], [])
            self.assertEqual(row['staticVerdict'], 'no-path-in-supported-model')

    def test_cache_directory_change_does_not_remove_archive_post_step_risk(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            shutil.copytree(CASE / 'pre-incident', temp / 'pre-incident')
            for name in ('npm-nightly.yaml', 'publish-nightly.yml'):
                workflow = temp / 'pre-incident/.github/workflows' / name
                workflow.write_text(workflow.read_text()
                                    .replace('path: node_modules', 'path: docs/static-cache')
                                    .replace('path: webview-ui/node_modules',
                                             'path: docs/static-cache'))
            row = chain.scan(temp, 'pre-incident')
            self.assertEqual(len(row['producers']), 1)
            self.assertEqual(len(row['consumers']), 2)
            self.assertEqual(row['staticVerdict'], 'conditional-path')

    def test_missing_checkout_post_step_is_outside_supported_path(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            shutil.copytree(CASE / 'pre-incident', temp / 'pre-incident')
            for name in ('npm-nightly.yaml', 'publish-nightly.yml'):
                workflow = temp / 'pre-incident/.github/workflows' / name
                workflow.write_text(workflow.read_text().replace(
                    'uses: actions/checkout@v4',
                    'uses: actions/upload-artifact@v4'))
            row = chain.scan(temp, 'pre-incident')
            self.assertEqual(len(row['producers']), 1)
            self.assertEqual(row['consumers'], [])
            self.assertEqual(row['staticVerdict'], 'no-path-in-supported-model')

    def test_changed_snapshot_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory) / 'case'
            shutil.copytree(CASE, temp)
            workflow = temp / 'pre-incident/.github/workflows/npm-nightly.yaml'
            workflow.write_text(workflow.read_text() + '\n# changed\n')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                chain.check_manifest(temp)

    def test_read_only_default_branch_cache_policy_blocks_public_issue_save(self):
        original = chain.scan(CASE, 'pre-incident')
        self.assertEqual(original['staticVerdict'], 'conditional-path')
        self.assertEqual(chain.explore(False)['verdict'], 'no-path-in-supported-model')
        self.assertIn('phase = 0 : FALSE & agent_executes_issue_instruction',
                      chain.render(False))


if __name__ == '__main__':
    unittest.main()
