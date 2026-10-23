# 短いArtifact例が，未対応処理の省略によらず利用の到達・不達を示すことを確認する．
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from workflow_model_v2.compiler import compile_workflows
from workflow_model_v2.verification import verify, read_verification


class ArtifactExampleTests(unittest.TestCase):
    def check_example(self, consumer, expected_use):
        # 同じProducerとscenarioを使い，Consumerのguardによる差だけを比較する．
        paths = [ROOT / 'examples/artifact-producer.yml', ROOT / 'examples' / consumer]
        scenario = json.loads((ROOT / 'examples/artifact-scenario.json').read_text())
        text, mapping = compile_workflows(paths, scenario)
        self.assertEqual(mapping['diagnostics'], [])
        with tempfile.TemporaryDirectory() as temporary:
            model = Path(temporary) / 'model.smv'
            model.write_text(text)
            self.assertEqual(verify(model), (0, 0))
            result = read_verification(model)
            self.assertTrue(result['reachability']['nonemptyReachableSet'])
            properties = dict(re.findall(r'-- specification (.*?)\s+is (true|false)', result['modelCheck']['stdout']))
            # safe版も取得には到達させ，workflow全体を実行不能にした不達を除外する．
            self.assertEqual(properties['AG !transfer'], 'false')
            self.assertEqual(properties['AG !dependent_use'], expected_use)
            self.assertEqual(properties['AG !artifact_dependent_use'], expected_use)

    def test_consumer_reaches_artifact_use(self):
        self.check_example('artifact-consumer.yml', 'false')

    def test_safe_consumer_downloads_but_blocks_pr_artifact_use(self):
        self.check_example('artifact-safe-consumer.yml', 'true')
