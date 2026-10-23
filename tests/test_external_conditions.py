# 条件IRの候補・相関・明示制限を，SMV接続前に確認する．
import sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
from workflow_model_v2.external_conditions import build_space,is_complete_scenario

WORKFLOW='''name: Demo
on: [push, pull_request]
jobs:
  job:
    runs-on: windows-latest
    steps:
      - run: echo ok
'''
class ExternalConditionIRTests(unittest.TestCase):
    def paths(self,tmp):
        path=Path(tmp)/'workflow.yml';path.write_text(WORKFLOW);return [path]
    def test_events_and_forks_are_legal_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            space=build_space(self.paths(tmp));rows=space.profiles[0]
            self.assertEqual({row['event'] for row in rows},{'push','pull_request'})
            self.assertEqual({row['fork'] for row in rows if row['event']=='pull_request'},{True,False})
            self.assertTrue(all(not row['fork'] for row in rows if row['event']=='push'))
            self.assertTrue(all(row['runnerOS']=='Windows' for row in rows))
    def test_explicit_restrictions_intersect_and_conflicts_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths=self.paths(tmp);space=build_space(paths,restrictions=[{'event':'pull_request','fork':True}])
            self.assertTrue(all(row['event']=='pull_request' and row['fork'] for row in space.profiles[0]))
            with self.assertRaises(ValueError):build_space(paths,restrictions=[{'event':'push','fork':True}])
            with self.assertRaises(ValueError):build_space(paths,{'conditions':[{'event':'push'}]},[{'event':'pull_request'}])
    def test_run_selector_is_required_and_repository_policy_is_shared(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths=self.paths(tmp);other=Path(tmp)/'other.yml';other.write_text(WORKFLOW.replace('name: Demo','name: Other'));paths.append(other)
            with self.assertRaises(ValueError):build_space(paths,restrictions=[{'fork':True}])
            space=build_space(paths,{'conditions':[{'run':'Demo','defaultPermissions':{'contents':'read'}}]})
            self.assertTrue(all(row['defaultPermissions']['contents']=='read' for rows in space.profiles for row in rows))
            with self.assertRaises(ValueError):build_space(paths,{'conditions':[{'run':'run0','defaultPermissions':{'contents':'read'}},{'run':'run1','defaultPermissions':{'contents':'write'}}]})
    def test_ir_keeps_domain_evidence_owner_and_encodings(self):
        with tempfile.TemporaryDirectory() as tmp:
            condition=next(c for c in build_space(self.paths(tmp)).conditions if c.name=='fork')
            self.assertEqual(condition.value_type,'boolean');self.assertEqual(condition.owner,'run0')
            self.assertIsNotNone(condition.symbol);self.assertEqual(set(condition.encodings.values()),{True,False})

    def test_repository_default_branch_is_shared_and_conflicts_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths=self.paths(tmp);other=Path(tmp)/'second.yml';other.write_text(WORKFLOW.replace('name: Demo','name: Second'));paths.append(other)
            space=build_space(paths,{'conditions':[{'run':'run0','defaultBranch':'release'}]})
            self.assertTrue(all(row['defaultBranch']=='refs/heads/release' for rows in space.profiles for row in rows))
            with self.assertRaisesRegex(ValueError,'contradictory repository defaultBranch'):
                build_space(paths,{'conditions':[{'run':'run0','defaultBranch':'release'},{'run':'run1','defaultBranch':'main'}]})

    def test_unfiltered_push_includes_branch_and_tag_refs(self):
        with tempfile.TemporaryDirectory() as tmp:
            space=build_space(self.paths(tmp))
            self.assertEqual({row['refType'] for row in space.profiles[0] if row['event']=='push'},{'branch','tag'})
            self.assertTrue(all(row['refType']=='branch' for row in space.profiles[0] if row['event']=='pull_request'))
