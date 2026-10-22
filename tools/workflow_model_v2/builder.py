"""Stateful v2 SMV extension for the existing source-mapped workflow model.

Static extraction creates typed value/provenance relations.  Reachability of
use, integrity barriers, and sinks is left to NuSMV; this module never returns
an A1--A5-specific hazard Boolean.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import workflow_to_smv as base
from .ir import PairIR, lower_pair
from workflow_shell_subset import ShellOp, ShellProgram


def _h(value: str, n: int = 10) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:n]


def _enum(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value).lower()
    if not cleaned or cleaned[0].isdigit():
        cleaned = "x_" + cleaned
    return "v_" + cleaned


def _symbol(wf: base.Workflow, job: base.Job, step_id: str, output: str) -> str:
    return "output_" + _h(wf.path + "::" + job.job_id + "::" + step_id + "::" + output)


def _upload_destination_within(copy_path: str, upload_path: str | None) -> bool:
    if not upload_path or "->" not in copy_path:
        return False
    _, destination = copy_path.split("->", 1)
    return destination == upload_path or destination.startswith(upload_path.rstrip("/") + "/")


def _shell_span(op: ShellOp, step: base.Step) -> dict[str, Any]:
    run_span = step.run_span
    return {"file": run_span.file if run_span else step.span.file,
            "line": op.span.line, "column": op.span.column,
            "endLine": op.span.end_line, "endColumn": op.span.end_column}


class StatefulModelBuilder(base.ModelBuilder):
    """Append provenance, integrity, and sink state to the base scheduler."""

    def __init__(self, producer: base.Workflow, consumer: base.Workflow):
        self.v2 = True
        self._output_workflows = [producer, consumer]
        self.ir: PairIR = lower_pair(Path(producer.path), Path(consumer.path))
        self.step_ir = {}
        for wf_ir in (self.ir.producer, self.ir.consumer):
            for item in wf_ir.steps:
                self.step_ir[(item.workflow, item.job_id, item.step_index)] = item
        self.output_specs: dict[str, dict[str, Any]] = {}
        self.output_values: dict[str, dict[str, str]] = {}
        self.output_lookup: dict[tuple[str, str, str, str], str] = {}
        self._collect_output_specs()
        self.integrity_checks: list[dict[str, Any]] = []
        self.shell_semantics: list[dict[str, Any]] = []
        self.metadata_contract_steps: set[tuple[str, str, int]] = set()
        super().__init__(producer, consumer)
        # v1 deliberately reports all run bodies as opaque.  v2 replaces those
        # diagnostics with a whole-script parse result and adds the IR issues.
        self.diagnostics = [d for d in self.diagnostics if d.code != "opaque-run" and
                            not (d.code == "unknown-action" and "actions/checkout" in d.message)]
        self.diagnostics.append(base.Diagnostic("unsupported", "real-authority-sink-contract-missing",
            "no real authority-requiring operation contract is implemented; bad_real_authority_path is not an evaluated safety result", None))
        for item in self.ir.diagnostics:
            self.diagnostics.append(base.Diagnostic(item.status, item.code, item.message,
                base.Span(item.source.file, item.source.line, item.source.column,
                          item.source.end_line, item.source.end_column) if item.source else None))
        for wf, job, step, shell in self._property_shell_steps():
            output_names = {op.writes[0] for op in shell.output_writes}
            if {"output:target_branch", "output:target_sha"}.issubset(output_names):
                site = (wf.path, job.job_id, step.index)
                if self._metadata_contract(shell):
                    self.metadata_contract_steps.add(site)
                else:
                    self.diagnostics.append(base.Diagnostic("unsupported", "metadata-validation-contract",
                        "metadata read, character validation, SHA length check, and GITHUB_OUTPUT assignment do not match the supported semantics", step.span))
        for wf in self.workflows:
            if len(wf.jobs) != 1:
                self.diagnostics.append(base.Diagnostic("unsupported", "v2-job-scope",
                    "stateful v2 effects are currently bounded to one job per workflow; the base scheduler still retains the needs graph", wf.jobs[0].span if wf.jobs else None))
        if len(self.uploads) > 1:
            self.diagnostics.append(base.Diagnostic("unsupported", "v2-artifact-slots",
                "stateful v2 value fields currently bind to one upload object per producer pair", None))
        for wf, job, step, shell in self._property_shell_steps():
            if wf.path != self.consumer.path:
                continue
            for op in shell.artifact_reads:
                if not op.reads or op.kind not in {"read-trim", "digest"}:
                    continue
                if not self._download_for_path(wf, job, op.reads[0]):
                    self.diagnostics.append(base.Diagnostic("unsupported", "artifact-file-origin",
                        f"Shell file variable {op.reads[0]!r} is not tied to a modeled download path", step.span))
            for raw in step.env.values():
                match = re.fullmatch(r"\$\{\{\s*steps\.([\w-]+)\.outputs\.([\w-]+)\s*}}", str(raw))
                if match and (wf.path, job.job_id, match.group(1), match.group(2)) not in self.output_lookup:
                    self.diagnostics.append(base.Diagnostic("unsupported", "unresolved-step-output",
                        f"{raw!r} has no supported output writer in this job", step.span))

    def _collect_output_specs(self) -> None:
        for wf in self.workflows_for_output_scan():
            for job in wf.jobs:
                for step in job.steps:
                    item = self.step_ir.get((wf.path, job.job_id, step.index))
                    if not item or not item.shell:
                        continue
                    for op in item.shell.output_writes:
                        key = op.writes[0].split(":", 1)[1]
                        symbol = _symbol(wf, job, step.step_id, key)
                        domain = self._output_domain(key)
                        self.output_specs[symbol] = {"key": key, "values": domain,
                            "workflow": wf.path, "job": job.job_id,
                            "step": step.step_id, "index": step.index,
                            "source": _shell_span(op, step)}
                        self.output_values[symbol] = {literal: atom for literal, atom in domain.items()}
                        self.output_lookup[(wf.path, job.job_id, step.step_id, key)] = symbol

    @staticmethod
    def _output_domain(key: str) -> dict[str, str]:
        if key == "verified":
            return {"true": "out_true", "false": "out_false", "other": "out_other"}
        if key == "actual_digest":
            return {"equal": "out_digest_equal", "different": "out_digest_different", "other": "out_other"}
        if key == "target_branch":
            return {"main": "out_branch_main", "other": "out_branch_other", "invalid": "out_branch_invalid"}
        if key == "target_sha":
            return {"valid": "out_sha_valid", "other": "out_sha_other", "invalid": "out_sha_invalid"}
        return {"other": "out_other"}

    def workflows_for_output_scan(self):
        return self._output_workflows

    def _collect_expressions(self):
        super()._collect_expressions()

    def _symbol(self, ref: str, wf: base.Workflow, job: base.Job):
        match = re.fullmatch(r"steps\.([A-Za-z_][\w-]*)\.outputs\.([A-Za-z_][\w-]*)", ref)
        if match:
            symbol = self.output_lookup.get((wf.path, job.job_id, match.group(1), match.group(2)))
            if symbol:
                return symbol, "step-output:" + match.group(2), None
            key = self._generic_key(wf, job, ref)
            self.generic_refs.setdefault(key, set()).update({"true", "false", "other"})
            self.diagnostics.append(base.Diagnostic("unsupported", "unresolved-step-output",
                f"{ref!r} has no supported producer output in this job", None))
        return super()._symbol(ref, wf, job)

    def _v2_frozen_decls(self) -> list[str]:
        return [
            "  producer_payload : { payload_publish_true, payload_publish_false, payload_other };",
            "  producer_metadata_branch : { branch_main, branch_other, branch_invalid };",
            "  producer_metadata_sha : { sha_valid, sha_other_valid, sha_invalid };",
            "  consumer_workflow_trusted : boolean;",
        ]

    def _v2_variable_decls(self) -> list[str]:
        result = [
            "  artifact_read : boolean;",
            "  verification_read : boolean;",
            "  post_verification_use : boolean;",
            "  post_integrity_mismatch_use : boolean;",
            "  artifact_dependent_use : boolean;",
            "  integrity_state : { integrity_not_run, integrity_verified, integrity_mismatch, integrity_unverifiable };",
            "  integrity_blocked : boolean;",
            "  consumer_payload : { value_unset, value_publish_true, value_publish_false, value_other };",
            "  object_metadata_branch : { branch_unset, branch_main, branch_other, branch_invalid };",
            "  object_metadata_sha : { sha_unset, sha_valid, sha_other_valid, sha_invalid };",
            "  artifact_has_dist : boolean;",
            "  simulated_sink_site_reached : boolean;",
            "  artifact_dependent_sink : boolean;",
            "  simulated_sink_result_true : boolean;",
            "  real_authority_sink_site_reached : boolean;",
            "  authority_operation_succeeded : boolean;",
        ]
        for _, _, _, token in self.uploads:
            result.append(f"  {token}_payload : {{ value_unset, value_publish_true, value_publish_false, value_other }};")
        for symbol in sorted(self.output_specs):
            values = list(dict.fromkeys(["out_unset", *self.output_specs[symbol]["values"].values()]))
            result.append(f"  {symbol} : {{ " + ", ".join(values) + " };")
        return result

    def _v2_define_decls(self) -> list[str]:
        result = [
            "  bad_simulated_path := artifact_dependent_sink & simulated_sink_site_reached & simulated_sink_result_true;",
            "  bad_real_authority_path := artifact_dependent_sink & real_authority_sink_site_reached & effective_contents_write & authority_operation_succeeded;",
            "  integrity_trust_anchor_available := consumer_workflow_trusted;",
            "  download_success_any := " + (" | ".join(f"{item[3]}_state = download_succeeded" for item in self.downloads) or "FALSE") + ";",
        ]
        for wf, job, step, token, matches, _ in self.downloads:
            result.append(f"  selected_payload_{token} := case")
            for obj in matches:
                result.append(f"    {token}_object = {obj} : {obj}_payload;")
            result.append("    TRUE : value_other;")
            result.append("  esac;")
        permission_terms = []
        for job in self.consumer.jobs:
            permission_terms.append(f"perm_{base._wf_key(self.consumer)}_{base._clean(job.job_id)}_contents = perm_write")
        result.extend([
            "  effective_contents_write := " + (" | ".join(permission_terms) or "FALSE") + ";",
            "  effective_authority_available := effective_contents_write;",
        ])
        return result

    def _step_gate(self, wf: base.Workflow, job: base.Job, step: base.Step) -> str:
        return (f"scheduler = {base._sched(wf, job)} & "
                f"{base._pc(wf, job)} = {base._step_pc(wf, job, step)} & "
                f"({self.step_guards[(wf.path, job.job_id, step.index)]})")

    def _step_prerequisite_guard(self, wf: base.Workflow, job: base.Job,
                                 step: base.Step) -> str:
        item = self.step_ir.get((wf.path, job.job_id, step.index))
        if not item or not item.shell:
            return "TRUE"
        tokens = []
        for op in item.shell.operations:
            if op.kind not in {"read-trim", "digest", "test-file"}:
                continue
            for env_name in op.reads:
                download = self._download_for_path(wf, job, env_name)
                if download:
                    tokens.append(f"{download[3]}_state = download_succeeded")
                else:
                    return "FALSE"
        return " & ".join(dict.fromkeys(tokens)) or "TRUE"

    def _outcome(self, outcomes: dict, wf: base.Workflow, job: base.Job, step: base.Step) -> str:
        return outcomes[(wf.path, job.job_id, step.index)]

    def _download_for_path(self, wf: base.Workflow, job: base.Job, env_name: str) -> tuple | None:
        # Current subset binds the file/directory to the download's literal path
        # (runner.temp may prefix both sides with the same expression).
        path_expr = None
        for step in job.steps:
            candidate = step.env.get(env_name)
            if isinstance(candidate, str):
                path_expr = candidate
                break
        if path_expr is None:
            return None
        for item in self.downloads:
            dwf, djob, dstep, *_ = item
            if dwf.path != wf.path or djob.job_id != job.job_id:
                continue
            target = base._input(dstep, "path") or "${{ github.workspace }}"
            left = str(target).rstrip("/")
            right = str(path_expr)
            if right == left or right.startswith(left + "/") or ("runner.temp" in left and "runner.temp" in right and
                    left.split("runner.temp", 1)[1].rstrip("/") == right.split("runner.temp", 1)[1].split("/", 1)[0]):
                return item
        return None

    def _payload_expr_for_shell_step(self, wf: base.Workflow, job: base.Job,
                                     step: base.Step, shell: ShellProgram) -> str:
        for op in shell.artifact_reads:
            if op.kind != "read-trim" or not op.reads:
                continue
            download = self._download_for_path(wf, job, op.reads[0])
            if download:
                return f"selected_payload_{download[3]}"
        return "value_other"

    def _property_shell_steps(self):
        for wf in self.workflows:
            for job in wf.jobs:
                for step in job.steps:
                    item = self.step_ir.get((wf.path, job.job_id, step.index))
                    if item and item.shell:
                        yield wf, job, step, item.shell

    def _integrity_spec(self, wf, job, step, shell):
        digest_ops = [op for op in shell.operations if op.kind == "digest"]
        expected_ops = [op for op in shell.operations if op.kind == "literal-assign" and op.writes == ("expected_digest",)]
        valid_compare = any(condition.kind == "if-eq-var" and
            set(condition.reads) == {"actual_digest", "expected_digest"} and
            condition.then_assignments.get("verified") == "true" and
            condition.else_assignments.get("verified") == "false"
            for condition in shell.conditions)
        compare_ops = [op for op in shell.operations if op.kind == "if-eq-var" and
                       set(op.reads) == {"actual_digest", "expected_digest"}]
        output_verified = next((op for op in shell.output_writes if op.writes == ("output:verified",)), None)
        if not digest_ops or not expected_ops or not compare_ops or not output_verified:
            return None
        digest_reads_artifact = any(op.reads == ("ARTIFACT_FILE",) for op in digest_ops)
        if not digest_reads_artifact or not valid_compare:
            return {"wf": wf, "job": job, "step": step, "shell": shell,
                    "known": False, "expected": expected_ops[0].value,
                    "match_values": [], "output_verified": True}
        expected = expected_ops[0].value
        if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
            return {"wf": wf, "job": job, "step": step, "shell": shell,
                    "known": False, "expected": expected, "match_values": []}
        match_values = []
        for value, representative in (("value_publish_true", "publish=true\n"),
                                      ("value_publish_false", "publish=false\n")):
            if hashlib.sha256(representative.encode()).hexdigest() == expected:
                match_values.append(value)
        return {"wf": wf, "job": job, "step": step, "shell": shell,
                "known": bool(match_values), "expected": expected,
                "match_values": match_values, "output_verified": output_verified is not None}

    @staticmethod
    def _publish_condition(shell: ShellProgram):
        artifact_values = {op.writes[0] for op in shell.operations
                           if op.kind == "read-trim" and op.reads == ("ARTIFACT_FILE",) and op.writes}
        for condition in shell.conditions:
            if (condition.kind == "if-eq" and condition.literal == "publish=true" and
                    condition.reads and condition.reads[0] in artifact_values and
                    condition.then_assignments.get("authority_reached") == "true" and
                    condition.else_assignments.get("authority_reached") == "false"):
                marker = next((op for op in shell.sink_sites if "authority_reached" in " ".join(op.reads)), None)
                if marker:
                    return condition, marker
        return None

    def _repository_update_test_download(self, wf, job, shell, marker):
        """Resolve the modeled artifact-presence test preceding a fixed update marker."""
        marker_index = shell.operations.index(marker)
        for op in shell.operations[:marker_index]:
            if op.kind != "test-file" or not op.reads:
                continue
            download = self._download_for_path(wf, job, op.reads[0])
            if download:
                return download
        return None

    def _metadata_update_contract(self, wf, job, step, shell, marker):
        """Check the bounded data-flow slice from metadata outputs to update arguments."""
        marker_index = shell.operations.index(marker)
        earlier = shell.operations[:marker_index]
        file_download = self._repository_update_test_download(wf, job, shell, marker)
        if not file_download:
            return None
        bindings: dict[str, str] = {}
        for env_name, raw in step.env.items():
            match = re.fullmatch(r"\$\{\{\s*steps\.([\w-]+)\.outputs\.(target_branch|target_sha)\s*}}", str(raw))
            if not match:
                continue
            output_name = match.group(2)
            symbol = self.output_lookup.get((wf.path, job.job_id, match.group(1), output_name))
            if symbol:
                bindings[output_name] = (env_name, symbol)
        if not {"target_branch", "target_sha"}.issubset(bindings):
            return None
        reads = {name for op in earlier if op.kind == "echo" for name in op.reads}
        branch_env, branch_symbol = bindings["target_branch"]
        sha_env, sha_symbol = bindings["target_sha"]
        if branch_env not in reads or sha_env not in reads:
            return None
        return {"branch": branch_symbol, "sha": sha_symbol,
                "download": file_download, "branchEnv": branch_env, "shaEnv": sha_env}

    @staticmethod
    def _metadata_contract(shell: ShellProgram) -> bool:
        reads = {}
        for op in shell.artifact_reads:
            if op.kind == "read-trim" and op.writes:
                reads[op.writes[0]] = op.path or ""
        required_reads = {
            "target_branch": "/dist-meta/head-ref",
            "target_sha": "/dist-meta/head-sha",
        }
        if any(reads.get(variable) != suffix for variable, suffix in required_reads.items()):
            return False
        def rejected_case(variable: str) -> bool:
            for index, op in enumerate(shell.operations):
                if op.kind != "case" or op.reads != (variable,):
                    continue
                cursor = index + 1
                while cursor < len(shell.operations) and shell.operations[cursor].kind != "esac":
                    if shell.operations[cursor].kind == "case-pattern":
                        pattern = shell.operations[cursor].value or ""
                        if pattern.startswith("*[!") and pattern.endswith("*|''"):
                            end = cursor + 1
                            while end < len(shell.operations) and shell.operations[end].kind not in {"case-next", "esac"}:
                                end += 1
                            if any(item.kind == "exit" and item.value == "1"
                                   for item in shell.operations[cursor + 1:end]):
                                return True
                    cursor += 1
            return False

        has_branch_reject = rejected_case("target_branch")
        has_sha_reject = rejected_case("target_sha")
        has_length_check = any(op.kind == "test-length" and op.reads == ("target_sha",) and op.value == "40"
                               for op in shell.operations)
        outputs = {op.writes[0] for op in shell.output_writes}
        test_index = next((index for index, op in enumerate(shell.operations)
                           if op.kind == "test-length" and op.reads == ("target_sha",) and op.value == "40"), -1)
        output_indexes = [index for index, op in enumerate(shell.operations)
                          if op.kind == "github-output" and op.writes and
                          op.writes[0] in {"output:target_branch", "output:target_sha"}]
        strict_mode = any(op.kind == "strict-mode" for op in shell.operations)
        return (strict_mode and has_branch_reject and has_sha_reject and has_length_check and
                {"output:target_branch", "output:target_sha"}.issubset(outputs) and
                len(output_indexes) == 2 and all(index > test_index for index in output_indexes))

    def _upload_source_kind(self, wf, job, upload_step) -> str | None:
        upload_path = base._input(upload_step, "path")
        if not upload_path:
            return None
        operations = [op for source_wf, source_job, source_step, shell in self._property_shell_steps()
                      if source_wf.path == wf.path and source_job.job_id == job.job_id
                      for op in shell.operations]
        if any(op.kind == "grep-assert" and op.path == upload_path for op in operations):
            return "checked-literal-domain"
        for op in operations:
            if op.kind != "copy" or not op.path or "->" not in op.path:
                continue
            _, destination = op.path.split("->", 1)
            if destination.startswith(upload_path.rstrip("/") + "/"):
                return "copied-workspace-files"
        return None

    def _v2_assign_decls(self, outcomes: dict) -> list[str]:
        result: list[str] = [
            "  init(object_metadata_branch) := branch_unset;",
            "  init(object_metadata_sha) := sha_unset;",
            "  init(artifact_has_dist) := FALSE;",
        ]
        metadata_branch_updates = []
        metadata_sha_updates = []
        dist_updates = []

        # Object content and metadata are set only at successful upload
        # transitions; a matching name alone cannot create an object.
        for wf, job, step, token in self.uploads:
            gate = self._step_gate(wf, job, step)
            outcome = self._outcome(outcomes, wf, job, step)
            source_kind = self._upload_source_kind(wf, job, step)
            payload_source = "producer_payload" if source_kind == "checked-literal-domain" else "payload_other"
            if source_kind is None:
                self.diagnostics.append(base.Diagnostic("unsupported", "artifact-content-origin",
                    "upload path is not connected to a supported checkout/validation/copy relation", step.span))
            result.extend([f"  init({token}_payload) := value_unset;",
                           f"  next({token}_payload) := case"])
            if payload_source == "producer_payload":
                result.extend([
                    f"    {gate} & {outcome} = success : case",
                    "      producer_payload = payload_publish_true : value_publish_true;",
                    "      producer_payload = payload_publish_false : value_publish_false;",
                    "      TRUE : value_other;",
                    "    esac;",
                ])
            else:
                result.append(f"    {gate} & {outcome} = success : value_other;")
            result.extend([f"    TRUE : {token}_payload;", "  esac;"])
            copied_meta = any(
                shell_op.kind == "copy" and shell_op.path and "dist-meta" in shell_op.path and
                _upload_destination_within(shell_op.path, base._input(step, "path"))
                for source_wf, source_job, source_step, shell in self._property_shell_steps()
                if source_wf.path == wf.path and source_job.job_id == job.job_id
                for shell_op in shell.operations
            )
            is_metadata = copied_meta
            if is_metadata:
                metadata_branch_updates.append(f"{gate} & {outcome} = success : case producer_metadata_branch = branch_main : branch_main; producer_metadata_branch = branch_other : branch_other; TRUE : branch_invalid; esac;")
                metadata_sha_updates.append(f"{gate} & {outcome} = success : case producer_metadata_sha = sha_valid : sha_valid; producer_metadata_sha = sha_other_valid : sha_other_valid; TRUE : sha_invalid; esac;")
                dist_updates.append(f"{gate} & {outcome} = success : TRUE;")
        for name, clauses in (("object_metadata_branch", metadata_branch_updates),
                              ("object_metadata_sha", metadata_sha_updates),
                              ("artifact_has_dist", dist_updates)):
            result.extend([f"  next({name}) := case", *["    " + clause for clause in clauses],
                           f"    TRUE : {name};", "  esac;"])

        result.extend([
            "  init(artifact_read) := FALSE;",
            "  next(artifact_read) := case",
            *[f"    {self._step_gate(wf, job, step)} & {self._outcome(outcomes, wf, job, step)} = success & ({self._step_prerequisite_guard(wf, job, step)}) : TRUE;"
              for wf, job, step, shell in self._property_shell_steps()
              if any(op.kind in {"read-trim", "digest", "test-file"} for op in shell.operations)
              and wf.path == self.consumer.path],
            "    TRUE : artifact_read;", "  esac;",
            "  init(artifact_dependent_use) := FALSE;",
            "  next(artifact_dependent_use) := case",
        ])
        for wf, job, step, shell in self._property_shell_steps():
            if wf.path != self.consumer.path:
                continue
            env_names = {op.reads[0] for op in shell.artifact_reads
                         if op.reads and op.kind == "read-trim"}
            if any(self._download_for_path(wf, job, env) for env in env_names):
                prerequisite = self._step_prerequisite_guard(wf, job, step)
                result.append(f"    {self._step_gate(wf, job, step)} & {self._outcome(outcomes, wf, job, step)} = success & ({prerequisite}) : TRUE;")
        result.extend(["    TRUE : artifact_dependent_use;", "  esac;"])

        # A read used to verify bytes is not a later use of verified bytes.
        # All three are history latches. A blocked later step cannot erase an
        # earlier use, so integrity_blocked must not imply an empty use history.
        for state, operation, integrity_guard in (
                ("verification_read", "digest", "TRUE"),
                ("post_verification_use", "read-trim", "integrity_state = integrity_verified"),
                ("post_integrity_mismatch_use", "read-trim", "integrity_state = integrity_mismatch")):
            result.extend([f"  init({state}) := FALSE;", f"  next({state}) := case"])
            for wf, job, step, shell in self._property_shell_steps():
                if wf.path != self.consumer.path or not any(op.kind == operation for op in shell.operations):
                    continue
                prerequisite = self._step_prerequisite_guard(wf, job, step)
                result.append(f"    {self._step_gate(wf, job, step)} & {self._outcome(outcomes, wf, job, step)} = success & ({prerequisite}) & ({integrity_guard}) : TRUE;")
            result.extend([f"    TRUE : {state};", "  esac;"])

        # Read values from the same downloaded object token selected by the
        # action state.  The object token is latched by the existing scheduler.
        result.extend(["  init(consumer_payload) := value_unset;", "  next(consumer_payload) := case"])
        for wf, job, step, shell in self._property_shell_steps():
            if wf.path != self.consumer.path or not any(op.kind == "read-trim" for op in shell.operations):
                continue
            env_names = [op.reads[0] for op in shell.artifact_reads if op.kind == "read-trim" and op.reads]
            if not any(self._download_for_path(wf, job, env) for env in env_names):
                continue
            download = next((self._download_for_path(wf, job, env) for env in env_names
                             if self._download_for_path(wf, job, env)), None)
            if not download:
                continue
            token, matches = download[3], download[4]
            result.append(f"    {self._step_gate(wf, job, step)} & {self._outcome(outcomes, wf, job, step)} = success & {token}_state = download_succeeded : selected_payload_{token};")
        result.extend(["    TRUE : consumer_payload;", "  esac;"])

        self.integrity_checks = []
        result.extend(["  init(integrity_state) := integrity_not_run;", "  next(integrity_state) := case"])
        integrity_output_updates: list[str] = []
        for wf, job, step, shell in self._property_shell_steps():
            spec = self._integrity_spec(wf, job, step, shell)
            if not spec or wf.path != self.consumer.path:
                continue
            download = self._download_for_path(wf, job, "ARTIFACT_FILE")
            if not download:
                spec["known"] = False
            token, matches = (download[3], download[4]) if download else (None, [])
            same = f"same_object_{token}" if token else "FALSE"
            payload_match = " | ".join(f"selected_payload_{token} = {value}" for value in spec["match_values"]) if token and spec["match_values"] else "FALSE"
            gate = self._step_gate(wf, job, step)
            outcome = self._outcome(outcomes, wf, job, step)
            if spec["known"]:
                result.append(f"    {gate} & {outcome} = success & ({same}) & !consumer_workflow_trusted : integrity_unverifiable;")
                result.append(f"    {gate} & {outcome} = success & ({same}) & consumer_workflow_trusted & ({payload_match}) : integrity_verified;")
                result.append(f"    {gate} & {outcome} = success & ({same}) & consumer_workflow_trusted & !({payload_match}) : integrity_mismatch;")
            result.append(f"    {gate} & {outcome} = success & !({same}) : integrity_unverifiable;")
            if not spec["known"] and token:
                result.append(f"    {gate} & {outcome} = success & ({same}) : integrity_unverifiable;")
            if not spec["known"]:
                self.diagnostics.append(base.Diagnostic("unsupported", "integrity-trust-anchor",
                    "digest is parsed, but no finite trusted content relation can be established", step.span))
            self.integrity_checks.append({"workflow": wf.path, "job": job.job_id,
                "step": step.index, "expectedDigest": spec["expected"],
                "matchValues": spec["match_values"], "sameObjectToken": token,
                "source": step.span.json(), "assumption": "SHA-256 collision resistance; consumer_workflow_trusted must hold for the literal to act as a trust anchor"})
            # The output is latched from the same check result consumed by the
            # subsequent steps.<output> guard.
            verified_symbol = self.output_lookup.get((wf.path, job.job_id, step.step_id, "verified"))
            actual_symbol = self.output_lookup.get((wf.path, job.job_id, step.step_id, "actual_digest"))
            for symbol, kind in ((verified_symbol, "verified"), (actual_symbol, "actual_digest")):
                if not symbol:
                    continue
                integrity_output_updates.extend([f"  init({symbol}) := out_unset;", f"  next({symbol}) := case"])
                if spec["known"]:
                    integrity_output_updates.extend([
                    f"    {gate} & {outcome} = success & ({same}) & consumer_workflow_trusted & ({payload_match}) : " +
                    ("out_true;" if kind == "verified" else "out_digest_equal;"),
                    f"    {gate} & {outcome} = success & ({same}) & consumer_workflow_trusted & !({payload_match}) : " +
                    ("out_false;" if kind == "verified" else "out_digest_different;"),
                    f"    {gate} & {outcome} = success & ({same}) & !consumer_workflow_trusted : out_other;"])
                integrity_output_updates.extend([
                    f"    {gate} & {outcome} = success & !({same}) : out_other;",
                    f"    TRUE : {symbol};", "  esac;"])
        result.extend(["    TRUE : integrity_state;", "  esac;"])
        result.extend(integrity_output_updates)

        result.extend([
            "  init(integrity_blocked) := FALSE;",
            "  next(integrity_blocked) := case",
        ])
        for wf, job, step, shell in self._property_shell_steps():
            if wf.path != self.consumer.path or not any(op.kind == "read-trim" for op in shell.operations):
                continue
            result.append(f"    scheduler = {base._sched(wf, job)} & {base._pc(wf, job)} = {base._step_pc(wf, job, step)} & !({self.step_guards[(wf.path, job.job_id, step.index)]}) & integrity_state = integrity_mismatch : TRUE;")
        result.extend(["    TRUE : integrity_blocked;", "  esac;"])

        # A1/A2 marker authority is selected by the value read from the
        # downloaded object.  A5's fixed marker is a simulated site whose
        # control inputs are the saved outputs produced by the metadata step.
        result.extend([
            "  init(simulated_sink_site_reached) := FALSE;",
            "  init(artifact_dependent_sink) := FALSE;",
            "  init(simulated_sink_result_true) := FALSE;",
            "  init(real_authority_sink_site_reached) := FALSE;",
            "  next(real_authority_sink_site_reached) := real_authority_sink_site_reached;",
            "  init(authority_operation_succeeded) := FALSE;",
            "  next(authority_operation_succeeded) := authority_operation_succeeded;",
        ])
        for wf, job, step, shell in self._property_shell_steps():
            if wf.path != self.consumer.path:
                continue
            sink_ops = [op for op in shell.sink_sites]
            if not sink_ops:
                continue
            gate = self._step_gate(wf, job, step)
            outcome = self._outcome(outcomes, wf, job, step)
            dummy_publish = any("dummy_publish_authority_reached" in str(op.value) for op in shell.markers)
            publish_contract = self._publish_condition(shell) if dummy_publish else None
            if dummy_publish and publish_contract:
                condition, marker = publish_contract
                condvar = condition.reads[0]
                self.shell_semantics.append({"workflow": wf.path, "job": job.job_id,
                    "step": step.index, "kind": "simulated-publish-marker",
                    "predicate": f"downloaded value for {condvar} = 'publish=true'", "source": _shell_span(marker, step),
                    "dependsOn": ["downloaded artifact value", condvar]})
            elif dummy_publish:
                self.diagnostics.append(base.Diagnostic("unsupported", "simulated-publish-shell-contract",
                    "publish marker is present but its if/else assignment does not match the supported value-flow rule", step.span))
            elif any("dummy_repository_update_reached" in str(op.value) for op in shell.markers):
                marker = next(op for op in shell.sink_sites
                              if "dummy_repository_update_reached" in str(op.value))
                contract = self._metadata_update_contract(wf, job, step, shell, marker)
                if contract:
                    branch, sha = contract["branch"], contract["sha"]
                    condition = f"({branch} != out_unset & {sha} != out_unset & artifact_has_dist & same_object_{contract['download'][3]})"
                    self.shell_semantics.append({"workflow": wf.path, "job": job.job_id,
                        "step": step.index, "kind": "simulated-repository-update-marker",
                        "predicate": condition, "source": _shell_span(marker, step),
                        "workflowHash": self.ir.consumer.source_hash,
                        "dependsOn": [branch, sha, "downloaded artifact file test"]})
                else:
                    self.diagnostics.append(base.Diagnostic("unsupported", "simulated-repository-update-flow",
                        "fixed update marker is not linked to both downloaded metadata outputs and a same-object file test", step.span))

        # Values of the separate sink flags are derived from the operation
        # contract above; no Python-side path verdict is inserted.
        sim_conditions = []
        dep_conditions = []
        result_true_conditions = []
        for wf, job, step, shell in self._property_shell_steps():
            if wf.path != self.consumer.path or not shell.sink_sites:
                continue
            gate = self._step_gate(wf, job, step)
            outcome = self._outcome(outcomes, wf, job, step)
            marker = " ".join(op.value or "" for op in shell.markers)
            if "dummy_publish_authority_reached" in marker:
                if not self._publish_condition(shell):
                    continue
                cond = f"{self._payload_expr_for_shell_step(wf, job, step, shell)} = value_publish_true"
                prerequisite = self._step_prerequisite_guard(wf, job, step)
                sim_conditions.append(f"({gate} & {outcome} = success & ({prerequisite}))")
                dep_conditions.append(f"({gate} & {outcome} = success & ({prerequisite}) & ({cond}))")
                result_true_conditions.append(f"({gate} & {outcome} = success & ({prerequisite}) & ({cond}))")
            elif "dummy_repository_update_reached=true" in marker:
                marker_op = next(op for op in shell.sink_sites
                                 if "dummy_repository_update_reached=true" in str(op.value))
                download = self._repository_update_test_download(wf, job, shell, marker_op)
                if download:
                    site_cond = (f"{gate} & {outcome} = success & artifact_has_dist & "
                                 f"same_object_{download[3]}")
                else:
                    site_cond = f"{gate} & {outcome} = success"
                sim_conditions.append(f"({site_cond})")
                result_true_conditions.append(f"({site_cond})")
                contract = self._metadata_update_contract(wf, job, step, shell, marker_op)
                if contract:
                    dep_cond = (f"{site_cond} & {contract['branch']} != out_unset & "
                                f"{contract['sha']} != out_unset")
                    dep_conditions.append(f"({dep_cond})")
            else:
                # An unrelated literal marker is observable as a simulated
                # site, but its constant text alone creates no provenance edge.
                for marker_op in shell.sink_sites:
                    site_cond = f"{gate} & {outcome} = success"
                    sim_conditions.append(f"({site_cond})")
                    if re.search(r"(?:=| )true(?:'|\"|$)", marker_op.value or ""):
                        result_true_conditions.append(f"({site_cond})")
                    self.diagnostics.append(base.Diagnostic("unsupported", "uncontracted-simulated-sink",
                        "simulated sink marker has no supported artifact data-flow contract", step.span))
        for name, conditions in (("simulated_sink_site_reached", sim_conditions),
                                 ("artifact_dependent_sink", dep_conditions),
                                 ("simulated_sink_result_true", result_true_conditions)):
            expression = " | ".join(conditions) or "FALSE"
            result.append(f"  next({name}) := case ({expression}) : TRUE; TRUE : {name}; esac;")

        # Every output variable has one persistent state assignment.  A2's
        # digest outputs are assigned above from the integrity check.  A5's
        # metadata outputs are copied from the same downloaded object fields.
        output_symbols_assigned = {self.output_lookup.get((check["workflow"], check["job"],
            next((s.step_id for w, j, s, _ in self._property_shell_steps()
                  if w.path == check["workflow"] and j.job_id == check["job"] and s.index == check["step"]), ""), key))
            for check in self.integrity_checks for key in ("verified", "actual_digest")}
        for symbol, spec in self.output_specs.items():
            if symbol in output_symbols_assigned:
                continue
            wf = next(w for w in self.workflows if w.path == spec["workflow"])
            job = next(j for j in wf.jobs if j.job_id == spec["job"])
            step = next(s for s in job.steps if s.index == spec["index"])
            gate = self._step_gate(wf, job, step)
            outcome = self._outcome(outcomes, wf, job, step)
            key = spec["key"]
            if key in {"target_branch", "target_sha"}:
                if (wf.path, job.job_id, step.index) not in self.metadata_contract_steps:
                    result.extend([f"  init({symbol}) := out_unset;",
                                   f"  next({symbol}) := {symbol};"])
                    continue
                item = next((d for d in self.downloads if d[0].path == wf.path and d[1].job_id == job.job_id), None)
                same = f"same_object_{item[3]}" if item else "FALSE"
                result.extend([f"  init({symbol}) := out_unset;", f"  next({symbol}) := case"])
                if key == "target_branch":
                    result.extend([
                        f"    {gate} & {outcome} = success & ({same}) & object_metadata_branch = branch_main : out_branch_main;",
                        f"    {gate} & {outcome} = success & ({same}) & object_metadata_branch = branch_other : out_branch_other;",
                    ])
                else:
                    result.extend([
                        f"    {gate} & {outcome} = success & ({same}) & object_metadata_sha = sha_valid : out_sha_valid;",
                        f"    {gate} & {outcome} = success & ({same}) & object_metadata_sha = sha_other_valid : out_sha_other;",
                    ])
                result.extend([f"    TRUE : {symbol};", "  esac;"])
            else:
                result.extend([f"  init({symbol}) := out_unset;",
                    f"  next({symbol}) := case {gate} & {outcome} = success : out_other; TRUE : {symbol}; esac;"])
        return result

    def _v2_properties(self) -> list[str]:
        return [
            "CTLSPEC AG !bad_simulated_path",
            "CTLSPEC AG !bad_real_authority_path",
            "CTLSPEC AG (artifact_dependent_use -> download_success_any)",
            "CTLSPEC AG (artifact_dependent_sink -> artifact_dependent_use)",
            "CTLSPEC AG !post_integrity_mismatch_use",
            "CTLSPEC AG !verification_read",
            "CTLSPEC AG !post_verification_use",
            # These dual reachability checks make absence/presence of the
            # intermediate states explicit in the NuSMV output. A false
            # result provides a trace; a true result is only within this model.
            "CTLSPEC AG !artifact_dependent_use",
            "CTLSPEC AG !simulated_sink_site_reached",
            "CTLSPEC AG !artifact_dependent_sink",
            "CTLSPEC AG (integrity_state != integrity_verified)",
            "CTLSPEC AG (integrity_state != integrity_mismatch)",
            "CTLSPEC AG !integrity_blocked",
        ]

    def _v2_manifest_fields(self) -> dict[str, Any]:
        unsupported = any(d.status in {"unsupported", "invalid"} for d in self.diagnostics)
        object_values = []
        for wf, job, step, token in self.uploads:
            object_values.append({"objectId": token, "payloadState": token + "_payload",
                "producer": {"workflow": wf.path, "job": job.job_id, "step": step.index,
                             "source": step.span.json()},
                "contentOrigin": "checkout/file provenance retained in PairIR"})
        return {
            "modelVersion": "2-stateful-prototype",
            "typedIR": self.ir.json(),
            "objectsV2": object_values,
            "integrityChecks": self.integrity_checks,
            "integrityHistoryMeaning": {
                "verification_read": "digest check read, independently of a later use",
                "post_verification_use": "read-trim executed with a previously verified integrity state",
                "post_integrity_mismatch_use": "read-trim executed after mismatch; must not be reachable for the guarded subset",
                "integrity_blocked": "a mismatch guard blocked at least one later read; does not erase prior reads",
            },
            "shellSemantics": self.shell_semantics,
            "propertyNames": ["bad_simulated_path", "bad_real_authority_path",
                              "artifact_dependent_use", "integrity_blocked"],
            "verdict": "unknown/unsupported" if unsupported else "generated-pending-container-verification",
            "unsupportedSemanticsPreventingGlobalSafety": [d.json() for d in self.diagnostics if d.status == "unsupported"],
            "sinkMeaning": {
                "simulated": "research marker or simulated operation in a run step; not an actual publish/update",
                "real": "authority-requiring action/command site; no such registered real sink contract is inferred from markers",
            },
            "externalInputs": {"consumer_workflow_trusted": "FROZENVAR choice; true asserts the supplied consumer YAML is the trusted default-branch workflow definition; the default-branch identity is not inferred from YAML"},
        }


def compile_pair_v2(producer_path: Path, consumer_path: Path) -> tuple[str, dict[str, Any]]:
    producer = base.load_workflow(producer_path)
    consumer = base.load_workflow(consumer_path)
    builder = StatefulModelBuilder(producer, consumer)
    return builder.build()
