import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import workflow_to_smv as compiler


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
    steps:
      - run: echo opaque
  upload:
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
        self.assertEqual(upload.span.line, 14)
        self.assertEqual(upload.inputs["name"], "payload")

    def test_expression_precedence_and_shared_context_lowering(self):
        expr = compiler.parse_expression(
            "github.event.workflow_run.event == 'pull_request' || "
            "github.event.workflow_run.conclusion == 'success' && true"
        )
        self.assertEqual(expr.kind, "||")
        self.assertEqual(expr.right.kind, "&&")

        smv, mapping = compiler.compile_pair(self.producer_path, self.consumer_path)
        event_atom = compiler._atom("origin_event", "pull_request")
        self.assertIn(f"origin_event = {event_atom}", smv)
        self.assertIn(f"origin_event != {event_atom}", smv)
        self.assertIn("producer_conclusion = conclusion_success", smv)
        self.assertIn("producer_terminal", smv)
        self.assertTrue(mapping["downloads"][0]["runIdReferencesProducer"])

    def test_models_ordered_jobs_artifact_identity_and_source_mapping(self):
        smv, mapping = compiler.compile_pair(self.producer_path, self.consumer_path)
        object_id = mapping["objects"][0]["token"]
        download = mapping["downloads"][0]
        self.assertEqual(download["candidateObjects"], [object_id])
        self.assertEqual(download["source"]["line"], 13)
        self.assertIn("pc_producer_prepare in { pc_producer_prepare_done, pc_producer_prepare_failed, pc_producer_prepare_skipped }", smv)
        self.assertIn(f"{object_id}_state = {object_id}", smv)
        self.assertIn(f": {object_id};", smv)
        self.assertIn(f"CTLSPEC AG ({download['token']}_state = download_succeeded ->", smv)
        self.assertIn(f"CTLSPEC AG ({download['token']}_state != download_succeeded)", smv)
        self.assertIn("NuSMV found a reachable successful transfer", mapping["properties"][0]["meaning"])
        self.assertTrue(mapping["boundedRuns"]["distinct"])
        self.assertEqual(mapping["verdict"], "not-produced: shell and sink effects are opaque")

    def test_opaque_run_is_not_treated_as_safe_or_given_a_sink(self):
        smv, mapping = compiler.compile_pair(self.producer_path, self.consumer_path)
        diagnostics = mapping["diagnostics"]
        self.assertTrue(any(d["code"] == "opaque-run" for d in diagnostics))
        self.assertNotIn("bad_real", smv)
        self.assertNotIn("CTLSPEC AG !", smv)
        self.assertNotIn("authority_reached", smv)

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
            "  a:\n    needs: b\n    steps: []\n"
            "  b:\n    needs: a\n    steps: []\n",
            encoding="utf-8",
        )
        with self.assertRaises(compiler.WorkflowError):
            compiler.compile_pair(self.producer_path, self.consumer_path)


if __name__ == "__main__":
    unittest.main()
