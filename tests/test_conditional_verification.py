# 条件付き反例の復号と，反対値の再検証に基づく必要条件の報告を確認する．
import json,re,sys,tempfile,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
from workflow_model_v2.compiler import compile_workflows
from workflow_model_v2.verification import verify,read_verification
from workflow_model_v2.conditional_verification import decode_witness, explain

WORKFLOW=(ROOT/'tests/fixtures/external-conditions/conditional-sink.yml').read_text()
class ConditionalVerificationTests(unittest.TestCase):
    def build(self,directory,settings):
        p=directory/'workflow.yml';p.write_text(WORKFLOW)
        text,mapping=compile_workflows([p],{'conditions':[dict(run='run0',**settings)]})
        model=directory/'model.smv';model.write_text(text);Path(str(model)+'.map.json').write_text(json.dumps(mapping))
        return model,mapping
    def test_boolean_opposite_check_establishes_nonfork_necessity(self):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);model,mapping=self.build(d,{'sendWriteTokens':False,'tokenAuthenticated':{'contents':True}})
            self.assertEqual(verify(model,timeout=20),(0,0));data=read_verification(model);report=data['conditionalReport']
            self.assertEqual(report['reachability'],'reachable');fork=next(c for c in report['booleanChecks'] if c['name']=='fork')
            self.assertEqual(fork['necessity'],'necessary-within-modeled-domain');self.assertFalse(fork['requiredValue'])
            self.assertTrue(all(v['status']=='complete' for v in fork['values']))
            witness=next(c for c in report['witnessConditions'] if c['name']=='fork');self.assertFalse(witness['value'])
            self.assertTrue(report['unsupported'])
            self.assertTrue(any(c['name']=='sendWriteTokens' and c['value'] is False for c in report['fixedConditions']))
            self.assertTrue(report['specifiedRestrictions'])
            self.assertEqual({p.name for p in d.iterdir()},{'workflow.yml','model.smv','model.smv.map.json','model.smv.log'})
    def test_fork_in_a_witness_is_not_claimed_necessary_when_both_values_reach(self):
        with tempfile.TemporaryDirectory() as tmp:
            model,_=self.build(Path(tmp),{'tokenAuthenticated':{'contents':True}})
            verify(model,timeout=20);report=read_verification(model)['conditionalReport'];fork=next(c for c in report['booleanChecks'] if c['name']=='fork')
            self.assertEqual(fork['necessity'],'not-necessary');self.assertTrue(all(v['reachable'] for v in fork['values']))
            self.assertFalse(any(c['name']=='fork' for c in report['necessaryConditionsWithinModel']))
    def test_witness_decoder_does_not_invent_absent_values(self):
        condition={'owner':'run0','name':'fork','symbol':'run_choice','origin':'external-domain','encodings':{'case1':True}}
        self.assertEqual(decode_witness('-- specification AG !bad is false\n x = TRUE\n','AG !bad',[condition]),[])
    def test_disabled_boolean_checks_do_not_report_necessity(self):
        with tempfile.TemporaryDirectory() as tmp:
            model,_=self.build(Path(tmp),{'tokenAuthenticated':{'contents':True}})
            verify(model,timeout=20,check_conditions=False);report=read_verification(model)['conditionalReport']
            self.assertEqual(report['booleanCheckStatus'],'not-requested');self.assertEqual(report['necessaryConditionsWithinModel'],[])

    def test_timeout_opposite_checks_never_establish_necessity(self):
        with tempfile.TemporaryDirectory() as tmp:
            model,mapping=self.build(Path(tmp),{'tokenAuthenticated':{'contents':True}})
            Path(str(model)+'.log').write_bytes(b'')
            def timed_out(*args):
                return dict(exitcode=124,timedOut=True,stdout=dict(offset=0,length=0))
            report=explain(model,dict(externalConditions=mapping['externalConditions'],properties=mapping['properties'],diagnostics=mapping['diagnostics']),
                           '-- specification AG !bad_authority is false\n',None,timed_out,None,'NuSMV',1)
            self.assertEqual(report['booleanCheckStatus'],'partial')
            self.assertEqual(report['necessaryConditionsWithinModel'],[])
            self.assertTrue(all(c['necessity']=='not-established' for c in report['booleanChecks']))
