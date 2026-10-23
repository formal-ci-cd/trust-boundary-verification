# CLIと部分scenarioが，同じ候補制限へ変換されることを検査する．
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from workflow_model_v2.compiler import compile_workflows
from workflow_model_v2.external_conditions import runner_labels
from workflow_model_v2.scenario import default_scenario

WORKFLOW = '''name: CLI root
on: [push, pull_request]
permissions: {contents: write}
jobs:
  job:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
'''


class ExternalConditionCLITests(unittest.TestCase):
    def paths(self, directory):
        producer = directory / 'producer.yml'
        consumer = directory / 'consumer.yml'
        producer.write_text(WORKFLOW)
        consumer.write_text('''name: CLI downstream
on:
  workflow_run:
    workflows: [CLI root]
    types: [completed]
jobs:
  job:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
''')
        return [producer, consumer]

    def cli(self, paths, output, *arguments):
        return subprocess.run([sys.executable, str(ROOT / 'tools/workflow_to_smv_v2.py'),
                               *map(str, paths), '--output', str(output), *arguments],
                              text=True, capture_output=True)

    def test_yaml_only_and_cli_fork_event_restrictions(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            paths = self.paths(directory)
            output = directory / 'restricted.smv'
            result = self.cli(paths, output, '--event', 'pull_request', '--fork', 'true')
            self.assertEqual(result.returncode, 0, result.stderr)
            mapping = json.loads(Path(str(output) + '.map.json').read_text())
            conditions = mapping['externalConditions']['conditions']
            self.assertEqual(next(c['domain'] for c in conditions if c['owner']=='run0' and c['name']=='fork'), [True])
            self.assertEqual(next(c['domain'] for c in conditions if c['owner']=='run0' and c['name']=='event'), ['pull_request'])
            self.assertIn('external_run_', output.read_text())
            # 最初のYAMLだけでも，任意のConsumerを補わず変換できる．
            self.assertEqual(self.cli(paths[:1], directory / 'single.smv').returncode, 0)

    def test_named_target_and_partial_json_intersect(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            paths = self.paths(directory)
            scenario = directory / 'partial.json'
            scenario.write_text(json.dumps({'conditions':[{'run':'run0','fork':True}]}))
            output = directory / 'named.smv'
            result = self.cli(paths, output, '--scenario', str(scenario), '--run', 'CLI downstream', '--workflow-present', 'false')
            self.assertEqual(result.returncode, 0, result.stderr)
            conditions = json.loads(Path(str(output)+'.map.json').read_text())['externalConditions']['conditions']
            self.assertEqual(next(c['domain'] for c in conditions if c['owner']=='run1' and c['name']=='defaultBranchWorkflow'), [False])
            conflict = directory / 'conflict.smv'
            result = self.cli(paths, conflict, '--scenario', str(scenario), '--fork', 'false')
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(conflict.exists())
            self.assertIn('contradictory', result.stderr)

    def test_complete_scenario_and_cli_must_agree(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            paths = self.paths(directory)
            scenario = default_scenario(paths)
            # YAMLの明示permissionsとrepository既定値の違いは矛盾ではない．
            for run in scenario['runs']:
                run['defaultPermissions'] = {'contents':'read'}
            original, _ = compile_workflows(paths, scenario)
            same, _ = compile_workflows(paths, scenario, restrictions=[{'run':'run0','event':scenario['runs'][0]['event']}])
            self.assertEqual(original, same)
            with self.assertRaisesRegex(ValueError, 'contradicts'):
                compile_workflows(paths, scenario, restrictions=[{'run':'run0','event':'undeclared'}])

    def test_repository_policy_shared_and_bound_not_truncated(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.paths(Path(tmp))
            paths[0].write_text(WORKFLOW.replace('permissions: {contents: write}\n','').replace('run: echo ok','run: git push'))
            text, mapping = compile_workflows(paths)
            policies = [c for c in mapping['externalConditions']['conditions'] if c['name']=='tokenPolicy']
            self.assertEqual(len(policies), 1)
            self.assertIn('INIT ', text)
            self.assertEqual(set(policies[0]['domain']), {'restricted','permissive'})
            with self.assertRaisesRegex(ValueError, 'exceeds'):
                compile_workflows(paths, {'bounds':{'conditionConfigurations':1}})

    def test_architecture_and_invalid_values(self):
        self.assertEqual(runner_labels('macos-latest'), ('macOS','ARM64'))
        self.assertEqual(runner_labels('macos-15-intel'), ('macOS','X64'))
        self.assertEqual(runner_labels('ubuntu-24.04-arm'), ('Linux','ARM64'))
        with tempfile.TemporaryDirectory() as tmp:
            paths = self.paths(Path(tmp))
            with self.assertRaisesRegex(ValueError, 'requires a string'):
                compile_workflows(paths, {'conditions':[{'run':'run0','event':42}]})
            with self.assertRaisesRegex(ValueError, 'architecture restriction'):
                compile_workflows(paths, {'conditions':[{'run':'run0','runnerArch':'ARM64'}]})

    def test_public_visibility_excludes_write_token_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths=self.paths(Path(tmp))
            paths[0].write_text(WORKFLOW.replace('run: echo ok','run: git push'))
            text,mapping=compile_workflows(paths,{'conditions':[{'run':'run0','repositoryVisibility':'public'}]})
            visibility=next(c for c in mapping['externalConditions']['conditions'] if c['name']=='repositoryVisibility')
            self.assertEqual(visibility['domain'],['public'])
            self.assertIn(' != public',text)
            with self.assertRaisesRegex(ValueError,'public repository'):
                compile_workflows(paths,{'conditions':[{'run':'run0','repositoryVisibility':'public','sendWriteTokens':True}]})
