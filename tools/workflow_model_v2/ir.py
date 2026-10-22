"""Typed source-mapped IR for the bounded workflow-to-SMV prototype."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
import re
import sys
from typing import Any

_TOOLS = str(Path(__file__).resolve().parents[1])
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
from workflow_shell_subset import ShellProgram, UnsupportedShell, parse_shell  # noqa: E402
import workflow_to_smv as base  # noqa: E402


@dataclass(frozen=True)
class SourceSpan:
    file: str
    line: int
    column: int
    end_line: int
    end_column: int

    @classmethod
    def from_base(cls, span: base.Span | None) -> "SourceSpan | None":
        if span is None:
            return None
        return cls(span.file, span.line, span.column, span.end_line, span.end_column)


@dataclass
class Diagnostic:
    status: str
    code: str
    message: str
    source: SourceSpan | None


@dataclass
class ValueIR:
    value_id: str
    kind: str
    domain: list[str]
    source_provenance: list[str]
    artifact_object_id: str | None
    trust_state: str
    references: list[str]
    source: SourceSpan | None


@dataclass
class ProvenanceEdge:
    source_id: str
    target_id: str
    relation: str
    source: SourceSpan | None
    control: bool = False


@dataclass
class StepIR:
    workflow: str
    job_id: str
    step_index: int
    step_id: str
    action: str | None
    action_ref: str | None
    inputs: dict[str, Any]
    env: dict[str, Any]
    condition_ast: base.Expr | None
    shell: ShellProgram | None
    contract: str | None
    source: SourceSpan
    status: str


@dataclass
class ArtifactObjectIR:
    object_id: str
    name: str | None
    path: str | None
    upload_step: str
    content_values: list[str]
    producer_run: str
    source: SourceSpan


@dataclass
class WorkflowIR:
    path: str
    name: str
    source_hash: str
    events: dict[str, Any]
    permissions: dict[str, str] | None
    permissions_declared: bool
    jobs: list[base.Job]
    steps: list[StepIR]
    source: SourceSpan


@dataclass
class PairIR:
    producer: WorkflowIR
    consumer: WorkflowIR
    objects: list[ArtifactObjectIR]
    values: list[ValueIR]
    edges: list[ProvenanceEdge]
    diagnostics: list[Diagnostic]
    assumptions: list[str] = field(default_factory=list)

    def json(self) -> dict[str, Any]:
        def to_json(value):
            if hasattr(value, "__dataclass_fields__"):
                return {key: to_json(item) for key, item in asdict(value).items()}
            if isinstance(value, dict):
                return {str(key): to_json(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [to_json(item) for item in value]
            return value

        return to_json(self)


ACTION_CONTRACTS = {
    "actions/checkout": {"11d5960a326750d5838078e36cf38b85af677262": "checkout-pr-v4"},
    "actions/upload-artifact": {"ea165f8d65b6e75b540449e92b4886f43607fa02": "upload-artifact-v4"},
    "actions/download-artifact": {"d3f86a106a0bac45b974a628896c90dbdf5c8093": "download-artifact-v4"},
}

_STEP_OUTPUT = re.compile(r"^\$\{\{\s*steps\.([A-Za-z_][\w-]*)\.outputs\.([A-Za-z_][\w-]*)\s*}}$")
_PATH_SUFFIX = re.compile(r"^\$([A-Za-z_][A-Za-z0-9_]*)(/.*)?$")


def _source(span: base.Span | None) -> SourceSpan | None:
    return SourceSpan.from_base(span)


def _site(wf: base.Workflow, job: base.Job, step: base.Step) -> str:
    return f"{wf.path}::{job.job_id}::{step.step_id or 'index-' + str(step.index)}"


def _action(uses: str | None) -> tuple[str | None, str | None, str | None]:
    if not uses:
        return None, None, None
    if "@" not in uses:
        return uses, None, None
    name, ref = uses.rsplit("@", 1)
    contract = ACTION_CONTRACTS.get(name, {}).get(ref)
    return name, ref, contract


def _steps(wf: base.Workflow, diagnostics: list[Diagnostic]) -> list[StepIR]:
    result = []
    for job in wf.jobs:
        for step in job.steps:
            action, ref, contract = _action(step.uses)
            shell = None
            status = "known"
            if contract == "checkout-pr-v4":
                if any(key in step.inputs for key in ("ref", "repository")):
                    status = "unsupported"
                    diagnostics.append(Diagnostic("unsupported", "checkout-ref",
                        "checkout provenance is modeled only for the default event ref in the supplied repository", _source(step.span)))
                persist = step.inputs.get("persist-credentials")
                if persist not in {None, False, "false"}:
                    status = "unsupported"
                    diagnostics.append(Diagnostic("unsupported", "checkout-credentials",
                        "checkout credential persistence is outside the source-provenance contract", _source(step.span)))
            if step.run is not None:
                try:
                    run_line = (step.run_span.line + 1) if step.run_span else step.span.line
                    shell = parse_shell(step.run, run_line)
                except UnsupportedShell as exc:
                    status = "unsupported"
                    source = SourceSpan(wf.path, exc.span.line, exc.span.column,
                                        exc.span.end_line, exc.span.end_column)
                    diagnostics.append(Diagnostic("unsupported", "shell-subset", str(exc), source))
            elif action is not None and contract is None:
                status = "unsupported"
                diagnostics.append(Diagnostic("unsupported", "unregistered-action-ref",
                    f"No effect contract is registered for {step.uses!r}", _source(step.span)))
            result.append(StepIR(wf.path, job.job_id, step.index, step.step_id,
                action, ref, step.inputs, step.env, step.condition, shell, contract,
                _source(step.span), status))
    return result


def lower_pair(producer_path: Path, consumer_path: Path) -> PairIR:
    producer = base.load_workflow(producer_path)
    consumer = base.load_workflow(consumer_path)
    diagnostics: list[Diagnostic] = []
    # Loader diagnostics are retained, but opaque-run is replaced by the closed
    # whole-script Shell parser result rather than silently accepted.
    for wf in (producer, consumer):
        for item in wf.diagnostics:
            if item.code != "opaque-run":
                diagnostics.append(Diagnostic(item.status, item.code, item.message, _source(item.span)))
    psteps = _steps(producer, diagnostics)
    csteps = _steps(consumer, diagnostics)
    producer_ir = WorkflowIR(producer.path, producer.name, producer.sha256, producer.events,
        producer.permissions, producer.permissions_declared, producer.jobs, psteps,
        _source(producer.jobs[0].span if producer.jobs else None))
    consumer_ir = WorkflowIR(consumer.path, consumer.name, consumer.sha256, consumer.events,
        consumer.permissions, consumer.permissions_declared, consumer.jobs, csteps,
        _source(consumer.jobs[0].span if consumer.jobs else None))

    objects: list[ArtifactObjectIR] = []
    values: list[ValueIR] = []
    edges: list[ProvenanceEdge] = []
    source_by_path: dict[str, str] = {}
    file_values: dict[tuple[str, str, str], str] = {}

    for wf, ir in ((producer, producer_ir), (consumer, consumer_ir)):
        ir_by_site = {(item.job_id, item.step_index): item for item in ir.steps}
        for job in wf.jobs:
            for step in job.steps:
                item = ir_by_site[(job.job_id, step.index)]
                site = _site(wf, job, step)
                if item.contract == "checkout-pr-v4":
                    source_id = "source:" + site + ":checkout-workspace"
                    source_by_path[wf.path + "::workspace"] = source_id
                    values.append(ValueIR(source_id, "checkout-workspace", ["untrusted-pr", "trusted-ref", "other"],
                        ["github.event.pull_request.head"], None, "untrusted-or-unknown", [], _source(step.span)))
                if item.shell:
                    for op in item.shell.operations:
                        if op.kind == "copy" and op.path:
                            src, dst = op.path.split("->", 1)
                            origin = source_by_path.get(wf.path + "::workspace", "source:" + site + ":filesystem-unknown")
                            value_id = "value:" + site + ":file:" + dst
                            file_values[(wf.path, job.job_id, dst)] = value_id
                            values.append(ValueIR(value_id, "file", ["unknown-content"],
                                [origin], None, "inherits-source", [src], _source(step.run_span)))
                            edges.append(ProvenanceEdge(origin, value_id,
                                "copy", _source(step.run_span)))
                        if op.kind == "read-trim":
                            source_var = op.reads[0]
                            env_binding = next((value for key, value in step.env.items() if key == source_var), None)
                            path_match = _PATH_SUFFIX.fullmatch(str(env_binding or ""))
                            object_id = None
                            if path_match:
                                artifact_path = (path_match.group(1), path_match.group(2) or "")
                                object_id = next((obj.object_id for obj in objects if obj.path and obj.path.endswith(artifact_path[1])), None)
                            value_id = "value:" + site + ":var:" + (op.writes[0] if op.writes else "read")
                            values.append(ValueIR(value_id, "shell-string", ["unknown-content"],
                                [object_id] if object_id else ["downloaded-file:" + site], object_id,
                                "tainted-or-unknown", [source_var], _source(step.run_span)))
                            edges.append(ProvenanceEdge(object_id or "downloaded-file:" + site,
                                value_id, "read-trim", _source(step.run_span)))
                        for from_id, to_id, shell_span in item.shell.variable_edges:
                            if from_id.startswith("output:") or to_id.startswith("output:"):
                                edges.append(ProvenanceEdge("value:" + site + ":var:" + from_id,
                                    "value:" + site + ":" + to_id, "shell-output", _source(step.run_span)))
                if item.contract == "upload-artifact-v4":
                    objid = "artifact:" + site
                    name = step.inputs.get("name")
                    path = step.inputs.get("path")
                    upload_path = str(path) if isinstance(path, str) else ""
                    contents = [value_id for (path_wf, path_job, file_path), value_id in file_values.items()
                                if path_wf == wf.path and path_job == job.job_id and
                                (file_path == upload_path or file_path.startswith(upload_path.rstrip("/") + "/"))]
                    if not contents and upload_path and source_by_path.get(wf.path + "::workspace"):
                        value_id = "value:" + site + ":checkout-file:" + upload_path
                        contents = [value_id]
                        values.append(ValueIR(value_id, "checkout-file", ["unknown-content"],
                            [source_by_path[wf.path + "::workspace"]], None, "untrusted-or-unknown", [], _source(step.span)))
                        edges.append(ProvenanceEdge(source_by_path[wf.path + "::workspace"], value_id,
                            "checkout-read", _source(step.span)))
                    if not contents:
                        contents = ["workspace-path:" + str(path)]
                    objects.append(ArtifactObjectIR(objid, str(name) if isinstance(name, str) else None,
                        str(path) if isinstance(path, str) else None, site, contents,
                        "producer-run:" + wf.sha256[:12], _source(step.span)))
                    for content in contents:
                        edges.append(ProvenanceEdge(content, objid, "upload", _source(step.span)))

    # Record downloads and direct output references as value-flow edges.  These
    # are relations, not hazard verdicts, and do not imply use or sink reach.
    for item in consumer_ir.steps:
        for job in consumer.jobs:
            if job.job_id != item.job_id:
                continue
            step = next((candidate for candidate in job.steps if candidate.index == item.step_index), None)
            if not step or not step.condition:
                continue
            for node in base._walk(step.condition):
                if node.kind != "ref" or not isinstance(node.value, str):
                    continue
                match = re.fullmatch(r"steps\.([A-Za-z_][\w-]*)\.outputs\.([A-Za-z_][\w-]*)", node.value)
                if not match:
                    continue
                source_step = next((p for p in consumer_ir.steps if p.job_id == item.job_id and
                                    p.step_id == match.group(1)), None)
                source_id = ("value:" + source_step.workflow + "::" + source_step.job_id + "::" +
                    str(source_step.step_index) + ":output:" + match.group(2)) if source_step else "unresolved-step-output:" + node.value
                target_id = "guard:" + item.workflow + "::" + item.job_id + "::" + str(item.step_index)
                edges.append(ProvenanceEdge(source_id, target_id, "if-control-reference", item.source, control=True))
        if item.contract == "download-artifact-v4":
            name = item.inputs.get("name")
            run_id = item.inputs.get("run-id")
            run_id_matches = run_id == "${{ github.event.workflow_run.id }}"
            candidates = [obj for obj in objects if obj.name == name and run_id_matches]
            for obj in candidates:
                download_id = "download:" + item.workflow + "::" + item.job_id + "::" + str(item.step_index)
                edges.append(ProvenanceEdge(obj.object_id, download_id, "candidate-by-name-and-run", item.source))
                values.append(ValueIR(download_id, "downloaded-artifact-view", ["absent", "same-object", "unknown-object"],
                    [obj.object_id], obj.object_id, "inherits-object", [], item.source))
        for env_name, raw in item.env.items():
            match = _STEP_OUTPUT.fullmatch(str(raw))
            if match:
                producer_step_id, output_name = match.groups()
                producer_step = next((p for p in consumer_ir.steps if p.step_id == producer_step_id), None)
                source_id = ("value:" + producer_step.workflow + "::" + producer_step.job_id + "::" +
                    str(producer_step.step_index) + ":output:" + output_name) if producer_step else "unresolved-step-output:" + str(raw)
                target_id = "value:" + item.workflow + "::" + item.job_id + "::" + str(item.step_index) + ":env:" + env_name
                edges.append(ProvenanceEdge(source_id, target_id, "steps-output-reference", item.source))
                values.append(ValueIR(target_id, "environment-value", ["unset", "known-domain", "unknown"],
                    [source_id], None, "inherits-source", [str(raw)], item.source))

    assumptions = [
        "A run is bounded to the supplied producer/consumer pair and one producer-run instance.",
        "Artifact service outcomes and unavailable repository policy are modeled as state choices, not inferred by Python.",
        "A recognized Shell script is accepted only if every non-empty statement matches the closed grammar.",
    ]
    return PairIR(producer_ir, consumer_ir, objects, values, edges, diagnostics, assumptions)
