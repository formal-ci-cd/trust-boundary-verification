import copy
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import automatic_artifact_analysis as auto
import codeql_csv_to_model as table
import discover_artifact_chains as discovery
import temporal_verification as temporal
import static_chain_baseline
import yaml_to_model as yaml_frontend
import jsonschema
import compare_frontends
import evaluate_public_cases
import yaml

CASE = ROOT / "experiments/public-cases/actions-attest"


class FrontendTests(unittest.TestCase):
    def test_full_block_and_on_key(self):
        model = yaml_frontend.load_models(CASE / "vulnerable")[
            ".github/workflows/commit-dist.yml"
        ]
        self.assertEqual(model["workflow"]["events"][0]["name"], "workflow_run")
        job = model["workflow"]["jobs"][0]
        script = next(s for s in job["steps"] if s.get("id") == "check")["command"]
        self.assertIn("exit 1", script)
        self.assertIn('echo "head-ref=$head_ref"', script)
        self.assertTrue(
            next(s for s in job["steps"] if s.get("id") == "check")["commandOrderKnown"]
        )

    def test_duplicate_yaml_keys_rejected(self):
        with self.assertRaises(ValueError):
            yaml.load(
                "on: push\non: pull_request\n", Loader=yaml_frontend.WorkflowLoader
            )

    def test_unknown_tags_are_not_executed(self):
        with self.assertRaises(yaml.constructor.ConstructorError):
            yaml.load(
                '!!python/object/apply:os.system ["echo forbidden"]',
                Loader=yaml_frontend.WorkflowLoader,
            )

    def test_all_models_and_generated_chains_validate(self):
        common = json.loads((ROOT / "model/common-model.schema.json").read_text())
        chain = json.loads((ROOT / "model/trust-chain.schema.json").read_text())
        for variant in ["vulnerable", "fixed"]:
            models = yaml_frontend.load_models(CASE / variant)
            for model in models.values():
                jsonschema.validate(model, common)
            for candidate in discovery.discover_candidates(models):
                result = auto.analyze(candidate)
                jsonschema.validate(result["chain"], chain)
                discovery.validate_provenance(result["chain"])
                self.assertFalse(
                    any(
                        p["sourceKind"] == "manual-judgment"
                        for p in result["chain"]["provenance"]
                    )
                )

    def test_csv_commands_not_overwritten_or_assumed_ordered(self):
        rows = table.load_rows(
            ROOT / "results/codeql-actions-model-v2.26.3.csv",
            "GHA-A1 Unverified artifact consumer",
        )
        original = [r for r in rows if r["kind"] == "run-step"][0]
        extra = dict(original, detail="echo second-command")
        model = table.build_model(rows + [extra], Path("fixture.csv"))
        step = next(
            s
            for j in model["workflow"]["jobs"]
            for s in j["steps"]
            if s["type"] == "run" and s["index"] == int(original["stepIndex"])
        )
        self.assertIn(original["detail"], step["command"])
        self.assertIn("echo second-command", step["command"])
        self.assertFalse(step["commandOrderKnown"])

    def test_run_script_takes_precedence_independent_of_row_order(self):
        rows = table.load_rows(
            ROOT / "results/codeql-actions-model-v2.26.3.csv",
            "GHA-A1 Unverified artifact consumer",
        )
        run = next(r for r in rows if r["kind"] == "run-step")
        row = dict(run, kind="run-script", detail="if test -f a; then\n  cat a\nfi\n")
        for values in [[row] + rows, rows + [row]]:
            model = table.build_model(values, Path("fixture.csv"))
            step = next(
                s
                for j in model["workflow"]["jobs"]
                for s in j["steps"]
                if s["type"] == "run" and s["index"] == int(run["stepIndex"])
            )
            self.assertEqual(step["command"], row["detail"])
            self.assertTrue(step["commandOrderKnown"])

    def test_saved_frontend_outputs_match_entire_scripts(self):
        for variant in ["vulnerable", "fixed"]:
            self.assertTrue(
                compare_frontends.compare(
                    CASE / variant, ROOT / f"results/attest-{variant}-structure.csv"
                )["equivalent"]
            )

    def test_original_source_checksums(self):
        for name in ["actions-attest", "ultralytics"]:
            evaluate_public_cases.verify_manifest(
                ROOT / "experiments/public-cases" / name
            )

    def test_duplicate_workflow_names_preserve_files(self):
        rows = table.load_rows(
            ROOT / "results/codeql-actions-model-v2.26.3.csv",
            "GHA-C1 Cache write from untrusted PR in default context",
        )
        copied = [dict(r, filePath=".github/workflows/other.yml") for r in rows]
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "duplicate.csv"
            with path.open("w") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows + copied)
            self.assertEqual(len(discovery.load_models(path)), 2)
            with self.assertRaises(ValueError):
                table.load_rows(path, rows[0]["workflowName"])


class AutomaticRulesTests(unittest.TestCase):
    def result(self, variant):
        return auto.analyze(
            discovery.discover_candidates(yaml_frontend.load_models(CASE / variant))[0]
        )

    def test_vulnerable_metadata_reaches_git_ref(self):
        result = self.result("vulnerable")
        self.assertEqual(result["status"], "potential-risk")
        self.assertEqual(result["flows"][0]["output"], "steps.check.outputs.head-ref")
        self.assertEqual(result["chain"]["facts"]["sameObject"], "unknown")
        self.assertFalse(result["runtimeVerified"])

    def test_unknown_consumer_action_does_not_keep_potential_risk_class(self):
        candidate = copy.deepcopy(discovery.discover_candidates(
            yaml_frontend.load_models(CASE / 'vulnerable'))[0])
        job = candidate['consumerModel']['workflow']['jobs'][0]
        job['steps'].append({'type': 'uses', 'action': 'unknown/action',
                             'index': max(s['index'] for s in job['steps']) + 1,
                             'arguments': {}, 'env': {}})
        result = auto.analyze(candidate)
        self.assertEqual(result['status'], 'unknown')
        self.assertTrue(any('unsupported consumer Action' in reason
                            for reason in result['unresolved']))

    def test_fixed_origin_policy_blocks_external_fork(self):
        result = self.result("fixed")
        self.assertEqual(result["status"], "source-policy-blocked")
        self.assertEqual(result["chain"]["facts"]["producerUntrusted"], "false")

    def test_or_nested_and_and_quoted_conditions_do_not_prove_exclusion(self):
        guard = (
            "github.event.workflow_run.head_repository.full_name == github.repository"
        )
        for condition in [
            guard + " || true",
            "contains('x', 'x && " + guard + " && y')",
            "!(" + guard + ")",
            "'x && " + guard + " && y'",
        ]:
            self.assertFalse(auto.fork_exclusion(condition))
        self.assertTrue(
            auto.fork_exclusion(
                "success() && " + guard + " && startsWith(github.ref, 'ref/')"
            )
        )

    def test_job_read_overrides_workflow_write(self):
        model = yaml_frontend.load_models(CASE / "vulnerable")[
            ".github/workflows/commit-dist.yml"
        ]
        job = model["workflow"]["jobs"][0]
        for p in model["workflow"]["permissions"]:
            p["access"] = "write" if p["jobId"] is None else "read"
        self.assertEqual(auto.configured_authority(model, job), "false")
        model["workflow"]["permissions"] = [
            p for p in model["workflow"]["permissions"] if p["jobId"] is None
        ]
        self.assertEqual(
            auto.configured_authority(model, job), "false"
        )  # explicit job permissions: {}
        job["permissionsDeclared"] = False
        self.assertEqual(auto.configured_authority(model, job), "true")

    def test_id_token_write_does_not_grant_repository_write(self):
        model = yaml_frontend.load_models(CASE / "vulnerable")[
            ".github/workflows/commit-dist.yml"
        ]
        job = model["workflow"]["jobs"][0]
        model["workflow"]["permissions"] = [
            {"jobId": job["id"], "scope": "id-token", "access": "write"}
        ]
        self.assertEqual(auto.configured_authority(model, job), "false")
        job["steps"][0]["arguments"]["github-token"] = "${{ secrets.OTHER_TOKEN }}"
        self.assertEqual(auto.configured_authority(model, job), "unknown")

    def test_unknown_semantics_stay_unknown(self):
        candidates = discovery.discover_candidates(
            yaml_frontend.load_models(CASE / "vulnerable")
        )
        candidate = copy.deepcopy(candidates[0])
        for job in candidate["consumerModel"]["workflow"]["jobs"]:
            for step in job["steps"]:
                if step["type"] == "run":
                    step["command"] = "custom-tool --consume input"
        result = auto.analyze(candidate)
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["chain"]["facts"]["consumerUsesObject"], "unknown")


class TemporalTests(unittest.TestCase):
    def test_mutable_has_order_sensitive_counterexample(self):
        result = temporal.explore(1, "mutable")
        self.assertEqual(
            [s["action"] for s in result["counterexample"]],
            [
                "consumer[0]:restore",
                "consumer[0]:verify",
                "writer[0]:replace",
                "consumer[0]:use",
            ],
        )
        self.assertTrue(
            temporal.safe_replay(result["counterexample"], "mutable")[
                "dummyAuthorityReached"
            ]
        )

    def test_snapshot_and_revalidation_block_bad_use(self):
        for mode in ["snapshot", "revalidate"]:
            result = temporal.explore(2, mode)
            self.assertEqual(result["verdict"], "safe")

    def test_static_baseline_matches_historical_fixed_fact_models(self):
        for name, expected in [
            ("gha-a1-auto", "unsafe"),
            ("gha-a2-auto", "safe"),
            ("gha-a3-auto", "safe"),
            ("gha-a4-auto", "safe"),
        ]:
            chain = json.loads(
                (ROOT / f"model/generated-chains/{name}.json").read_text()
            )
            self.assertEqual(static_chain_baseline.check(chain)["verdict"], expected)

    def test_saved_nusmv_and_bfs_agree(self):
        saved = json.loads(
            (ROOT / "results/temporal-2026-10-02/comparison.json").read_text()
        )
        for result in saved["results"]:
            fresh = temporal.explore(result["objects"], result["mode"])
            self.assertEqual(fresh["verdict"], result["nusmv"]["verdict"])
            self.assertEqual(fresh["reachableStates"], result["bfs"]["reachableStates"])


if __name__ == "__main__":
    unittest.main()
