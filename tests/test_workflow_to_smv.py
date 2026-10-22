import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from workflow_model_v2 import frontend as compiler
from workflow_model_v2.compiler import compile_workflows


UPLOAD_REF = "ea165f8d65b6e75b540449e92b4886f43607fa02"
DOWNLOAD_REF = "d3f86a106a0bac45b974a628896c90dbdf5c8093"


def producer_yaml():
    return f"""name: Demo Producer
on:
  pull_request:
    types: [opened, synchronize, reopened]
permissions:
  contents: read
jobs:
  prepare:
    runs-on: ubuntu-latest
    steps:
      - run: echo opaque
  upload:
    runs-on: ubuntu-latest
    needs: prepare
    steps:
      - id: upload
        uses: actions/upload-artifact@{UPLOAD_REF}
        with:
          name: payload
          path: payload.txt
"""


def consumer_yaml():
    run_id = chr(36) + "{{ github.event.workflow_run.id }}"
    token = chr(36) + "{{ secrets.GITHUB_TOKEN }}"
    return f"""name: Demo Consumer
on:
  workflow_run:
    workflows: [Demo Producer]
    types: [completed]
permissions:
  actions: read
  contents: read
jobs:
  consume:
    runs-on: ubuntu-latest
    if: github.event.workflow_run.conclusion == 'success' && github.event.workflow_run.event == 'pull_request'
    steps:
      - id: download
        uses: actions/download-artifact@{DOWNLOAD_REF}
        with:
          name: payload
          path: out
          run-id: {run_id}
          github-token: {token}
      - name: Same context, opposite predicate
        if: github.event.workflow_run.event != 'pull_request'
        run: echo opaque
"""


class WorkflowToSmvTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.producer_path = self.root / "producer.yml"
        self.consumer_path = self.root / "consumer.yml"
        self.producer_path.write_text(producer_yaml(), encoding="utf-8")
        self.consumer_path.write_text(consumer_yaml(), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_yaml_on_key_and_source_spans_are_preserved(self):
        workflow = compiler.load_workflow(self.producer_path)
        self.assertIn("pull_request", workflow.events)
        upload = workflow.jobs[1].steps[0]
        self.assertEqual(upload.span.file, str(self.producer_path))
        self.assertEqual(upload.span.line, 16)
        self.assertEqual(upload.inputs["name"], "payload")

    def test_expression_precedence_and_shared_context_lowering(self):
        expr = compiler.parse_expression(
            "github.event.workflow_run.event == 'pull_request' || "
            "github.event.workflow_run.conclusion == 'success' && true"
        )
        self.assertEqual(expr.kind, "||")
        self.assertEqual(expr.right.kind, "&&")

        smv, mapping = compile_workflows([self.producer_path, self.consumer_path])
        self.assertEqual(mapping['modelVersion'],'2-unified-bounded')
        self.assertIn('transfer',smv)

    def test_models_ordered_jobs_artifact_identity_and_source_mapping(self):
        smv, mapping = compile_workflows([self.producer_path,self.consumer_path])
        prepare=next(j for j in mapping['jobs'] if j['original']=='prepare')
        upload=next(j for j in mapping['jobs'] if j['original']=='upload')
        self.assertEqual(upload['needs'],[prepare['id']])
        self.assertIn('pc_'+prepare['id'],smv)
        self.assertEqual(mapping['objects'][0]['source']['file'],str(self.producer_path))
        self.assertEqual(mapping['objects'][0]['source']['line'],16)

    def test_unsupported_shell_does_not_yield_a_safety_verdict(self):
        self.consumer_path.write_text(consumer_yaml().replace('run: echo opaque','run: eval input'))
        _,mapping=compile_workflows([self.producer_path,self.consumer_path])
        self.assertEqual(mapping['verdict'],'unknown/unsupported')
        self.assertIn('shell-contract',{d['code'] for d in mapping['diagnostics']})

    def test_duplicate_yaml_keys_are_rejected(self):
        self.producer_path.write_text(
            "name: dup\non:\n  pull_request: null\n  pull_request: null\njobs: {}\n",
            encoding="utf-8",
        )
        with self.assertRaises(compiler.WorkflowError):
            compiler.load_workflow(self.producer_path)

    def test_needs_cycle_is_invalid_and_does_not_emit_smv(self):
        self.producer_path.write_text(
            "name: cycle\non: push\njobs:\n"
            "  a:\n    runs-on: ubuntu-latest\n    needs: b\n    steps: []\n"
            "  b:\n    runs-on: ubuntu-latest\n    needs: a\n    steps: []\n",
            encoding="utf-8",
        )
        with self.assertRaises(compiler.WorkflowError):
            compile_workflows([self.producer_path, self.consumer_path])


if __name__ == "__main__":
    unittest.main()
