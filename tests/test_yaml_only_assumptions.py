# YAMLだけの入力で用いる有限仮定，存在分岐，接続と未対応の保持を実NuSMVで検査する．
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from workflow_model_v2.compiler import compile_workflows
from workflow_model_v2.scenario import default_scenario
from workflow_model_v2.transition import symbol
from workflow_model_v2.verification import verify, read_verification


class YamlOnlyAssumptionTests(unittest.TestCase):
    def paths(self, safe=False):
        return [ROOT / 'examples/artifact-producer.yml', ROOT / 'examples' / (
            'artifact-safe-consumer.yml' if safe else 'artifact-consumer.yml')]

    def inspect_model(self, directory, text, mapping, extra=''):
        model = directory / 'model.smv'
        model.write_text(text + extra)
        if not extra:
            Path(str(model) + '.map.json').write_text(json.dumps(mapping))
        self.assertEqual(verify(model), (0, 0))
        result = read_verification(model)
        self.assertTrue(result['reachability']['nonemptyReachableSet'])
        properties = {k.strip(): v for k, v in re.findall(
            r'^-- specification (.*?) is (true|false)', result['modelCheck']['stdout'], re.M)}
        return properties, result, model

    def test_examples_need_only_yaml_and_preserve_presence_alternatives(self):
        for safe in (False, True):
            with self.subTest(safe=safe), tempfile.TemporaryDirectory() as tmp:
                text, mapping = compile_workflows(self.paths(safe))
                self.assertEqual({item['code'] for item in mapping['diagnostics']},{'external-file-content-domain'})
                self.assertEqual(mapping['inputMode'], 'automatic')
                self.assertEqual(mapping['modelSha256'], hashlib.sha256(text.encode()).hexdigest())
                assumption = mapping['automaticAssumptions'][0]
                self.assertEqual(assumption['path'], 'examples/payload.txt')
                self.assertEqual(assumption['contents'], ['', 'untrusted\n'])
                # 存在しない場合にも到達状態を持ち，存在しなければ転送できないことを検査する．
                present = symbol('input_present', 'run0:examples/payload.txt')
                # CTLはすべての初期状態で評価されるため，FROZENVARの片側へEFで移れるとは検査しない．
                # AG presentとAG !presentが両方falseなら，固定値の両側が初期状態に含まれている．
                extra = (f'CTLSPEC AG {present};\n'
                         f'CTLSPEC AG !{present};\n'
                         f'CTLSPEC AG !({present} & transfer);\n'
                         f'CTLSPEC AG (!{present} -> !transfer);\n')
                properties, _, _ = self.inspect_model(Path(tmp), text, mapping, extra)
                self.assertEqual(properties['AG !transfer'], 'false')
                self.assertEqual(properties['AG !dependent_use'], 'true' if safe else 'false')
                self.assertEqual(properties['AG !artifact_dependent_use'], 'true' if safe else 'false')
                # NuSMVは性質の表示順を並べ替えるので，順番でなく式そのものを照合する．
                self.assertEqual(properties[f'AG {present}'], 'false')
                self.assertEqual(properties[f'AG !{present}'], 'false')
                self.assertEqual(properties[f'AG !({present} & transfer)'], 'false')
                self.assertEqual(properties[f'AG (!{present} -> !transfer)'], 'true')

    def test_assumptions_are_in_the_single_log_without_sidecar_proliferation(self):
        with tempfile.TemporaryDirectory() as tmp:
            text, mapping = compile_workflows(self.paths())
            _, result, model = self.inspect_model(Path(tmp), text, mapping)
            context = result['analysisContext']
            self.assertEqual(context['status'], 'matched')
            self.assertEqual(context['inputMode'], 'automatic')
            self.assertEqual(context['automaticAssumptions'], mapping['automaticAssumptions'])
            self.assertEqual(context['runContexts'][0]['source'], 'externalConditions')
            self.assertEqual(next(c for c in context['externalConditions']['conditions'] if c['owner']=='run0' and c['name']=='event')['domain'], ['pull_request'])
            self.assertIn('解析条件・仮定・未対応事項', Path(str(model) + '.log').read_text())
            self.assertEqual({p.name for p in Path(tmp).iterdir()}, {'model.smv', 'model.smv.map.json', 'model.smv.log'})

    def test_direct_cat_reads_the_downloaded_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            consumer = directory / 'consumer.yml'
            raw = self.paths()[1].read_text()
            raw = raw[:raw.index('      - run:')] + '      - run: cat payload/payload.txt\n'
            consumer.write_text(raw)
            text, mapping = compile_workflows([self.paths()[0], consumer])
            self.assertEqual({item['code'] for item in mapping['diagnostics']},{'external-file-content-domain'})
            properties, _, _ = self.inspect_model(directory, text, mapping)
            self.assertEqual(properties['AG !transfer'], 'false')
            self.assertEqual(properties['AG !dependent_use'], 'false')

    def test_consumer_first_is_connected_by_workflow_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            text, mapping = compile_workflows(list(reversed(self.paths())))
            self.assertEqual({item['code'] for item in mapping['diagnostics']},{'external-file-content-domain'})
            self.assertEqual(next(c for c in mapping['externalConditions']['conditions'] if c['owner']=='run0' and c['name']=='upstreamRun')['domain'],['run1'])
            properties, _, _ = self.inspect_model(Path(tmp), text, mapping)
            self.assertEqual(properties['AG !dependent_use'], 'false')

    def test_glob_directory_and_dynamic_uploads_remain_unsupported(self):
        for path in ['examples/*.txt', 'examples/', '${{ inputs.path }}', '../payload.txt']:
            with self.subTest(path=path), tempfile.TemporaryDirectory() as tmp:
                producer = Path(tmp) / 'producer.yml'
                producer.write_text(self.paths()[0].read_text().replace('path: examples/payload.txt', 'path: ' + path))
                _, mapping = compile_workflows([producer, self.paths()[1]])
                self.assertEqual(mapping['verdict'], 'unknown/unsupported')
                self.assertIn('automatic-checkout-path', {item['code'] for item in mapping['diagnostics']})

    def test_unsupported_shell_is_not_hidden_by_file_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            consumer = Path(tmp) / 'consumer.yml'
            raw = self.paths()[1].read_text()
            raw = raw[:raw.index('      - run:')] + '      - run: eval "$INPUT"\n'
            consumer.write_text(raw)
            _, mapping = compile_workflows([self.paths()[0], consumer])
            self.assertEqual(mapping['verdict'], 'unknown/unsupported')
            self.assertIn('shell-contract', {item['code'] for item in mapping['diagnostics']})

    def test_missing_or_ambiguous_upstream_is_not_silently_selected(self):
        with tempfile.TemporaryDirectory() as tmp:
            consumer = Path(tmp) / 'consumer.yml'
            consumer.write_text(self.paths()[1].read_text().replace('workflows: [Artifact Producer]', 'workflows: [Missing Producer]'))
            _, mapping = compile_workflows([self.paths()[0], consumer])
            self.assertIn('external-workflow-run', {item['code'] for item in mapping['diagnostics']})
            duplicate = Path(tmp) / 'duplicate.yml'
            duplicate.write_text(self.paths()[0].read_text())
            _, mapping = compile_workflows([self.paths()[0], duplicate, self.paths()[1]])
            self.assertEqual(set(next(c for c in mapping['externalConditions']['conditions'] if c['owner']=='run2' and c['name']=='upstreamRun')['domain']),{'run0','run1'})

    def test_explicit_scenario_keeps_fixed_inventory(self):
        scenario = json.loads((ROOT / 'examples/artifact-scenario.json').read_text())
        text, mapping = compile_workflows(self.paths(), scenario)
        self.assertEqual(mapping['inputMode'], 'explicit-scenario')
        self.assertEqual(mapping['automaticAssumptions'], [])
        self.assertNotIn('input_present_', text)
        self.assertEqual(mapping['scenario'], scenario)

    def test_explicit_contents_override_inferred_optional_inventory(self):
        paths = self.paths()
        scenario = default_scenario(paths)
        scenario['runs'][0]['checkoutValues']['examples/payload.txt'] = ['payload\n']
        text, mapping = compile_workflows(paths, scenario)
        self.assertNotIn('input_present_', text)
        self.assertEqual(mapping['automaticAssumptions'], [])
        self.assertEqual(mapping['scenario']['automaticAssumptionOverrides'][0]['path'], 'examples/payload.txt')
        # 呼出し元の辞書は変更せず，compiler内部の複製にだけ優先関係を適用する．
        self.assertEqual(scenario['runs'][0]['optionalCheckoutFiles'], ['examples/payload.txt'])

    def test_snapshot_replaces_automatic_contents_and_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'examples').mkdir()
            (directory / 'examples/payload.txt').write_text('snapshot bytes\n')
            scenario = default_scenario(self.paths())
            scenario['runs'][0]['repositorySnapshot'] = str(directory)
            text, mapping = compile_workflows(self.paths(), scenario)
            self.assertEqual(mapping['scenario']['runs'][0]['checkoutValues']['examples/payload.txt'], ['snapshot bytes\n'])
            self.assertNotIn('input_present_', text)
            self.assertEqual(mapping['automaticAssumptions'], [])

    def test_mismatched_source_map_is_not_used_as_analysis_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            text, mapping = compile_workflows(self.paths())
            mapping['modelSha256'] = 'incorrect'
            _, result, _ = self.inspect_model(directory, text, mapping)
            self.assertEqual(result['analysisContext']['status'], 'mismatch')
            self.assertNotIn('automaticAssumptions', result['analysisContext'])

    def test_cli_without_scenario_generates_expected_reachability(self):
        with tempfile.TemporaryDirectory() as tmp:
            for safe in (False, True):
                model = Path(tmp) / ('safe.smv' if safe else 'unsafe.smv')
                command = [sys.executable, str(ROOT / 'tools/workflow_to_smv_v2.py'),
                           *map(str, self.paths(safe)), '--output', str(model)]
                generated = subprocess.run(command, capture_output=True, text=True, cwd=ROOT)
                self.assertEqual(generated.returncode, 0, generated.stderr)
                self.assertIn('外部条件の有限候補を自動構成', generated.stdout)
                verified = subprocess.run([sys.executable, str(ROOT / 'tools/workflow_model_v2/verification.py'),
                                           str(model)], capture_output=True, text=True, cwd=ROOT)
                self.assertEqual(verified.returncode, 0, verified.stderr)
                result = read_verification(model)
                self.assertEqual(result['analysisContext']['status'], 'matched')
                self.assertRegex(result['modelCheck']['stdout'], r'AG !dependent_use\s+is ' + ('true' if safe else 'false'))
