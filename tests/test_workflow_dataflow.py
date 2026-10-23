# 新しいデータフローの境界条件を検査する．到達性の照合は全評価側でNuSMVを使う．
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from workflow_model_v2.compiler import compile_workflows, shell_contract
from workflow_model_v2.scenario import default_scenario
from workflow_model_v2 import policies
from workflow_model_v2.frontend import WorkflowError
from workflow_shell_subset import parse_shell, UnsupportedShell
import yaml


class DataflowTests(unittest.TestCase):
    def test_environment_file_is_not_an_output(self):
        ops=shell_contract('echo "MODE=$VALUE" >> "$GITHUB_ENV"')
        self.assertEqual(ops,[('runtime-env','MODE','$VALUE',True)])
        ops=shell_contract("echo 'MODE=$VALUE' >> \"$GITHUB_ENV\"")
        self.assertFalse(ops[0][3])
        with self.assertRaises(ValueError):
            shell_contract('echo "MODE=$(id)" >> "$GITHUB_ENV"')

    def test_unrecognized_redirection_is_not_a_log(self):
        for script in ['echo "MODE=x" >> \'$GITHUB_ENV\'', 'echo "a" > "$DEST"']:
            with self.subTest(script=script), self.assertRaises((ValueError,UnsupportedShell)):
                shell_contract(script)
        with self.assertRaises(UnsupportedShell):parse_shell('echo "a" > "$DEST"')
        with self.assertRaises(ValueError):shell_contract('echo "$VALUE" > data')

    def test_structured_env_preserves_variable_dependency(self):
        program=parse_shell("printf 'MODE=%s\\n' \"$value\" >> \"$GITHUB_ENV\"")
        op=program.operations[0]
        self.assertEqual((op.kind,op.reads,op.writes),('github-env',('value',),('env:MODE',)))
        with self.assertRaises(UnsupportedShell):
            parse_shell('echo "MODE<<END" >> "$GITHUB_ENV"')

    def test_matrix_include_does_not_merge_into_added_rows(self):
        result=policies.expand_matrix(dict(fruit=['apple','pear'],animal=['cat','dog'],include=[
            {'color':'green'},{'color':'pink','animal':'cat'},{'fruit':'apple','shape':'circle'},
            {'fruit':'banana'},{'fruit':'banana','animal':'cat'}]))
        self.assertEqual(len(result),6)
        self.assertEqual(result[0],dict(fruit='apple',animal='cat',color='pink',shape='circle'))
        self.assertEqual(result[-2:],[{'fruit':'banana'},{'fruit':'banana','animal':'cat'}])

    def test_matrix_excludes_and_bounds(self):
        self.assertEqual(policies.expand_matrix({'os':['linux','mac'],'exclude':[{'os':'mac'}]}),[{'os':'linux'}])
        self.assertEqual(policies.expand_matrix({'include':[{'os':'linux'},{'os':'mac'}]}),[{'os':'linux'},{'os':'mac'}])
        with self.assertRaises(ValueError):
            policies.expand_matrix({'os':['linux','mac']},1)
        with self.assertRaises(ValueError):
            policies.expand_matrix({'os':'${{ inputs.os }}'})

    def test_fixtures_preserve_diagnostics_and_shared_variables(self):
        specs=json.loads((ROOT/'tests/fixtures/dataflow-cases.json').read_text())['cases']
        with tempfile.TemporaryDirectory() as tmp:
            for spec in specs:
                with self.subTest(case=spec['id']):
                    directory=Path(tmp)/spec['id'];directory.mkdir()
                    paths=[]
                    for i,raw in enumerate(spec['workflows']):
                        path=directory/f'{i}.yml';path.write_text(yaml.safe_dump(raw,sort_keys=False));paths.append(path)
                    scenario=default_scenario(paths);extra=spec.get('scenario',{})
                    scenario.update({k:v for k,v in extra.items() if k!='runs'})
                    for ctx,update in zip(scenario['runs'],extra.get('runs',[])):ctx.update(update)
                    if spec.get('snapshotFiles'):
                        snapshot=directory/'snapshot';snapshot.mkdir()
                        for name,value in spec['snapshotFiles'].items():(snapshot/name).write_text(value)
                        scenario['runs'][0]['repositorySnapshot']=str(snapshot)
                    text,mapping=compile_workflows(paths,scenario)
                    self.assertEqual(mapping['modelVersion'],'2-unified-bounded')
                    self.assertTrue(set(spec.get('expectedDiagnostics',[])).issubset({d['code'] for d in mapping['diagnostics']}))
                    if spec.get('expectedDiagnostics'):
                        self.assertEqual(mapping['verdict'],'unknown/unsupported')
                    else:
                        self.assertFalse([d for d in mapping['diagnostics'] if d['status']=='unsupported'],mapping['diagnostics'])
                    identities=[(e['job'],e['name']) for e in mapping['runtimeEnvironment']]
                    self.assertEqual(len(identities),len(set(identities)))

    def test_run_bound_rejects_instead_of_silently_truncating(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'workflow.yml';path.write_text('on: push\njobs:\n  work:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n')
            scenario=default_scenario([path]);scenario['bounds']={'runs':0}
            with self.assertRaises(WorkflowError):
                compile_workflows([path],scenario)

class VerificationTimeoutTests(unittest.TestCase):
    def test_timeout_records_incomplete_without_overwriting(self):
        from unittest.mock import patch
        import subprocess
        from workflow_model_v2.verification import verify
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'model.smv';path.write_text('MODULE main')
            with patch('workflow_model_v2.verification.subprocess.run',side_effect=subprocess.TimeoutExpired('NuSMV',1)):
                self.assertEqual(verify(path,timeout=1),(124,None))
            self.assertIn('no reachability verdict',Path(str(path)+'.log').read_text())
            with self.assertRaises(FileExistsError):verify(path,timeout=1)

    def test_timeout_requires_positive_limit(self):
        from workflow_model_v2.verification import verify
        with self.assertRaises(ValueError):verify('unused.smv',timeout=0)


if __name__=='__main__': unittest.main()
