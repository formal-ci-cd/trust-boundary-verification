# 条件ごとの既存契約が，実際に単一SMVの到達性へ反映されることを検査する．
import json,re,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from workflow_model_v2.compiler import compile_workflows
from workflow_model_v2.verification import verify,read_verification
from workflow_model_v2.transition import symbol

WORKFLOW='''name: Conditions
on: [push, pull_request]
permissions: {contents: write}
jobs:
  job:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: {persist-credentials: false}
      - run: cp source.txt data
      - run: git add data
      - run: git push
'''
class ExternalConditionBackendTests(unittest.TestCase):
    def compile(self,tmp,raw=WORKFLOW,partial=None,restrictions=None):
        path=Path(tmp)/'workflow.yml';path.write_text(raw)
        return compile_workflows([path],partial,restrictions=restrictions)
    def properties(self,tmp,text):
        p=Path(tmp)/('model-'+str(len(list(Path(tmp).glob('model-*.smv'))))+'.smv');p.write_text(text);self.assertEqual(verify(p,timeout=30),(0,0));r=read_verification(p)
        return {k.strip():v for k,v in re.findall(r'^-- specification (.*?) is (true|false)',r['modelCheck']['stdout'],re.M)}
    def test_event_fork_and_token_policy_change_reachability(self):
        for event,fork,expected in [('push',False,'false'),('pull_request',False,'false'),('pull_request',True,'true')]:
            with self.subTest(event=event,fork=fork),tempfile.TemporaryDirectory() as tmp:
                # push由来のcheckoutはcleanなので，badは不達となる．
                text,m=self.compile(tmp,restrictions=[{'event':event,'fork':fork,'sendWriteTokens':False,'tokenAuthenticated':{'contents':True}}])
                props=self.properties(tmp,text)
                self.assertEqual(props['AG !bad_authority'],'true' if event=='push' else expected)
    def test_both_events_and_forks_reach_distinct_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            text,m=self.compile(tmp);conditions=m['externalConditions']['conditions'];extra=[]
            for name,value in [('event','push'),('event','pull_request'),('fork',True),('fork',False)]:
                condition=next(c for c in conditions if c['owner']=='run0' and c['name']==name)
                predicate=' | '.join(condition['symbol']+' = '+encoded for encoded,decoded in condition['encodings'].items() if decoded==value)
                extra.append('CTLSPEC AG !('+predicate+');')
            props=self.properties(tmp,text+'\n'+'\n'.join(extra))
            checked=[v for k,v in props.items() if 'external_run_' in k]
            self.assertEqual(checked,['false']*4)
    def test_root_branch_filter_has_matching_started_context(self):
        raw='''name: Release
on:
  push:
    branches: [release]
jobs:
  job:
    runs-on: ubuntu-latest
    steps:
      - run: echo ok
'''
        with tempfile.TemporaryDirectory() as tmp:
            text,m=self.compile(tmp,raw);condition=next(c for c in m['externalConditions']['conditions'] if c['name']=='headBranch')
            self.assertEqual(condition['domain'],['release'])
            job=m['jobs'][0]['id'];props=self.properties(tmp,text+'\nCTLSPEC AG !(pc_'+job+' = s0);\n')
            self.assertEqual(props['AG !(pc_'+job+' = s0)'],'false')
            with self.assertRaises(ValueError):self.compile(tmp,raw,restrictions=[{'headBranch':'main'}])
    def test_windows_label_and_runner_context_agree(self):
        raw="""name: Windows
on: push
jobs:
  job:
    runs-on: windows-latest
    steps:
      - if: runner.os == 'Windows'
        run: echo ok
"""
        with tempfile.TemporaryDirectory() as tmp:
            text,m=self.compile(tmp,raw);job=m['jobs'][0];out=symbol('outcome',job['id']+':index0')
            props=self.properties(tmp,text+'\nCTLSPEC AG !('+out+' = success);\n')
            self.assertEqual(props['AG !('+out+' = success)'],'false')
    def test_later_workflow_waits_for_upstream_completion(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);p=directory/'p.yml';c=directory/'c.yml'
            p.write_text('name: Producer\non: push\njobs:\n  first:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n')
            c.write_text('name: Consumer\non:\n  workflow_run:\n    workflows: [Producer]\n    types: [completed]\njobs:\n  later:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo ok\n')
            text,m=compile_workflows([p,c]);a,b=[j['id'] for j in m['jobs']]
            query=f'AG (pc_{b} = s0 -> pc_{a} in {{success,failure,skipped,cancelled}})'
            props=self.properties(tmp,text+'\nCTLSPEC '+query+';\n')
            self.assertTrue(any(v=='true' for k,v in props.items() if k.startswith('AG (pc_'+b)))
            blocked,_=compile_workflows([p,c],restrictions=[{'run':'run1','defaultBranchWorkflow':False}]);props=self.properties(directory,blocked+'\nCTLSPEC AG !(pc_'+b+' = s0);\n')
            self.assertEqual(props['AG !(pc_'+b+' = s0)'],'true')

    def test_activity_and_visibility_contexts_use_the_selected_conditions(self):
        raw="""name: Activity
on:
  pull_request:
    types: [opened, reopened]
jobs:
  job:
    runs-on: ubuntu-latest
    steps:
      - if: github.event.action == 'reopened' && github.event.repository.private
        run: echo ok
"""
        for activity,visibility,expected in [('reopened','private','false'),('opened','private','true'),('reopened','public','true')]:
            with self.subTest(activity=activity,visibility=visibility),tempfile.TemporaryDirectory() as tmp:
                text,mapping=self.compile(tmp,raw,restrictions=[{'activity':activity,'repositoryVisibility':visibility}])
                outcome=symbol('outcome',mapping['jobs'][0]['id']+':index0')
                props=self.properties(tmp,text+'\nCTLSPEC AG !('+outcome+' = success);\n')
                self.assertEqual(props['AG !('+outcome+' = success)'],expected)
