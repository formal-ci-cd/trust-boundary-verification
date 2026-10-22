#!/usr/bin/env python3
"""Compile a bounded producer/consumer GitHub Actions subset into SMV.

Shell bodies remain opaque. This prototype preserves their step order and
possible failure, but does not infer their dataflow or security effects.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workflow_model_v2.workflow_loader import WorkflowLoader  # noqa: E402

GH_EXPR_PREFIX = chr(36) + "{{"
SUPPORTED_ACTIONS = {
    ("actions/upload-artifact", "ea165f8d65b6e75b540449e92b4886f43607fa02"),
    ("actions/download-artifact", "d3f86a106a0bac45b974a628896c90dbdf5c8093"),
}


@dataclass(frozen=True)
class Span:
    file: str
    line: int
    column: int
    end_line: int
    end_column: int

    def json(self) -> dict[str, Any]:
        return {"file": self.file, "line": self.line, "column": self.column,
                "endLine": self.end_line, "endColumn": self.end_column}


@dataclass
class Diagnostic:
    status: str
    code: str
    message: str
    span: Span | None

    def json(self) -> dict[str, Any]:
        return {"status": self.status, "code": self.code, "message": self.message,
                "source": self.span.json() if self.span else None}


@dataclass(frozen=True)
class Expr:
    kind: str
    value: Any = None
    left: Expr | None = None
    right: Expr | None = None


@dataclass
class Step:
    index: int
    step_id: str
    uses: str | None
    run: str | None
    inputs: dict[str, Any]
    condition_text: str | None
    condition: Expr | None
    span: Span
    run_span: Span | None = None
    env: dict[str, Any] = field(default_factory=dict)
    name: str = ""


@dataclass
class Job:
    job_id: str
    needs: list[str]
    condition_text: str | None
    condition: Expr | None
    permissions: dict[str, str] | None
    permissions_declared: bool
    steps: list[Step]
    span: Span


@dataclass
class Workflow:
    path: str
    name: str
    events: dict[str, Any]
    permissions: dict[str, str] | None
    permissions_declared: bool
    jobs: list[Job]
    sha256: str
    diagnostics: list[Diagnostic] = field(default_factory=list)


class WorkflowError(ValueError):
    pass


class ExpressionError(ValueError):
    pass


TOKEN_RE = re.compile(
    r"""(?P<space>\s+)|(?P<string>'(?:''|[^'])*'|"(?:\\.|[^"])*")|
    (?P<op>==|!=|&&|\|\||!)|(?P<lp>\()|(?P<rp>\))|
    (?P<ident>[A-Za-z_][A-Za-z0-9_.-]*)|(?P<other>.)""",
    re.VERBOSE,
)


def parse_expression(source: str | None) -> Expr | None:
    """Parse literals, references, status checks, Boolean operators and comparisons."""
    if source is None or not str(source).strip():
        return None
    text = str(source).strip()
    if text.startswith(GH_EXPR_PREFIX) and text.endswith("}}"):
        text = text[3:-2].strip()
    tokens: list[tuple[str, str]] = []
    for match in TOKEN_RE.finditer(text):
        kind = match.lastgroup or ""
        value = match.group()
        if kind == "space":
            continue
        if kind == "other":
            raise ExpressionError(f"unsupported token {value!r}")
        tokens.append((kind, value))
    tokens.append(("end", ""))
    pos = 0

    def peek():
        return tokens[pos]

    def take(expected=None):
        nonlocal pos
        result = tokens[pos]
        if expected and result[0] != expected:
            raise ExpressionError(f"expected {expected}, got {result[1]!r}")
        pos += 1
        return result

    def primary():
        kind, value = peek()
        if kind == "op" and value == "!":
            take()
            return Expr("not", left=primary())
        if kind == "lp":
            take()
            result = parse_bp(0)
            take("rp")
            return result
        if kind == "string":
            take()
            if value.startswith("'"):
                return Expr("string", value[1:-1].replace("''", "'"))
            try:
                return Expr("string", json.loads(value))
            except json.JSONDecodeError as exc:
                raise ExpressionError("unsupported quoted string escape") from exc
        if kind != "ident":
            raise ExpressionError(f"expected expression, got {value!r}")
        take()
        lower = value.lower()
        if lower in {"true", "false"}:
            return Expr("bool", lower == "true")
        if peek()[0] == "lp":
            take("lp")
            take("rp")
            if "." in value:
                raise ExpressionError("qualified expression functions are unsupported")
            return Expr("call", lower)
        return Expr("ref", value)

    precedence = {"||": 1, "&&": 2, "==": 3, "!=": 3}

    def parse_bp(minimum):
        left = primary()
        while peek()[0] == "op" and peek()[1] in precedence:
            op = peek()[1]
            level = precedence[op]
            if level < minimum:
                break
            take()
            left = Expr(op, left=left, right=parse_bp(level + 1))
        return left

    result = parse_bp(0)
    if peek()[0] != "end":
        raise ExpressionError(f"unexpected token {peek()[1]!r}")
    return result


def _span(node: yaml.Node | None, file: str) -> Span:
    if node is None:
        return Span(file, 1, 1, 1, 1)
    return Span(file, node.start_mark.line + 1, node.start_mark.column + 1,
                node.end_mark.line + 1, node.end_mark.column + 1)


def _member(node: yaml.Node | None, key: str) -> yaml.Node | None:
    if not isinstance(node, yaml.MappingNode):
        return None
    return next((value for key_node, value in node.value if key_node.value == key), None)


def _permission_map(value: Any, span: Span, diagnostics: list[Diagnostic]) -> dict[str, str] | None:
    if value is None:
        return None
    if isinstance(value, str) and value in {"read-all", "write-all"}:
        return {"*": value[:-4]}
    if not isinstance(value, dict):
        diagnostics.append(Diagnostic("invalid", "permissions-type", "permissions must be a mapping, read-all, or write-all", span))
        return {}
    result = {}
    for scope, access in value.items():
        if not isinstance(scope, str) or access not in {"read", "write", "none"}:
            diagnostics.append(Diagnostic("invalid", "permission-value", f"unsupported permission {scope!r}: {access!r}", span))
        else:
            result[scope] = access
    return result


def load_workflow(path: Path, root: Path | None = None) -> Workflow:
    source = path.read_text(encoding="utf-8")
    try:
        data = yaml.load(source, Loader=WorkflowLoader)
        tree = yaml.compose(source, Loader=WorkflowLoader)
    except Exception as exc:
        raise WorkflowError(f"{path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
        raise WorkflowError(f"{path}: expected a workflow mapping with jobs")
    filename = path.relative_to(root).as_posix() if root else path.as_posix()
    diagnostics: list[Diagnostic] = []
    events = data.get("on")
    if isinstance(events, str):
        events = {events: None}
    elif isinstance(events, list):
        events = {event: None for event in events}
    if not isinstance(events, dict):
        diagnostics.append(Diagnostic("invalid", "trigger-type", "on must be a mapping, event name, or event list", _span(_member(tree, "on"), filename)))
        events = {}
    permissions = _permission_map(data.get("permissions"), _span(_member(tree, "permissions"), filename), diagnostics)
    jobs_node = _member(tree, "jobs")
    jobs: list[Job] = []
    for job_id, raw_job in data["jobs"].items():
        job_node = _member(jobs_node, str(job_id))
        if not isinstance(raw_job, dict):
            diagnostics.append(Diagnostic("invalid", "job-type", f"job {job_id!r} must be a mapping", _span(job_node, filename)))
            continue
        needs = raw_job.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        if not isinstance(needs, list) or not all(isinstance(x, str) for x in needs):
            diagnostics.append(Diagnostic("invalid", "needs-type", f"job {job_id!r} needs must be a string or list", _span(_member(job_node, "needs"), filename)))
            needs = []
        for unsupported in ("uses", "strategy", "container", "services", "environment"):
            if unsupported in raw_job:
                diagnostics.append(Diagnostic("unsupported", "job-" + unsupported, f"job-level {unsupported} is outside this prototype", _span(_member(job_node, unsupported), filename)))
        raw_if = raw_job.get("if")
        try:
            expr = parse_expression(str(raw_if)) if raw_if is not None else None
        except ExpressionError as exc:
            expr = Expr("unsupported", str(raw_if))
            diagnostics.append(Diagnostic("unsupported", "job-if-expression", str(exc), _span(_member(job_node, "if"), filename)))
        job_permissions = _permission_map(raw_job.get("permissions"), _span(_member(job_node, "permissions"), filename), diagnostics)
        raw_steps = raw_job.get("steps", [])
        steps_node = _member(job_node, "steps")
        if not isinstance(raw_steps, list):
            diagnostics.append(Diagnostic("invalid", "steps-type", f"job {job_id!r} steps must be a list", _span(steps_node, filename)))
            raw_steps = []
        steps: list[Step] = []
        for index, raw_step in enumerate(raw_steps):
            step_node = steps_node.value[index] if isinstance(steps_node, yaml.SequenceNode) and index < len(steps_node.value) else None
            span = _span(step_node, filename)
            if not isinstance(raw_step, dict):
                diagnostics.append(Diagnostic("invalid", "step-type", "step must be a mapping", span))
                continue
            has_uses, has_run = "uses" in raw_step, "run" in raw_step
            if has_uses == has_run:
                diagnostics.append(Diagnostic("invalid", "step-operation", "each step must contain exactly one of uses or run", span))
            uses = raw_step.get("uses") if has_uses else None
            run = raw_step.get("run") if has_run else None
            if uses is not None and not isinstance(uses, str):
                diagnostics.append(Diagnostic("invalid", "uses-type", "uses must be a string", _span(_member(step_node, "uses"), filename)))
                uses = None
            inputs = raw_step.get("with", {})
            if not isinstance(inputs, dict):
                diagnostics.append(Diagnostic("invalid", "with-type", "with must be a mapping", _span(_member(step_node, "with"), filename)))
                inputs = {}
            raw_step_if = raw_step.get("if")
            try:
                step_expr = parse_expression(str(raw_step_if)) if raw_step_if is not None else None
            except ExpressionError as exc:
                step_expr = Expr("unsupported", str(raw_step_if))
                diagnostics.append(Diagnostic("unsupported", "step-if-expression", str(exc), _span(_member(step_node, "if"), filename)))
            if has_run:
                diagnostics.append(Diagnostic("unsupported", "opaque-run", "run body is retained but its dataflow/effects are not analyzed", _span(_member(step_node, "run"), filename)))
            for feature in ("continue-on-error", "timeout-minutes", "shell", "working-directory"):
                if feature in raw_step:
                    diagnostics.append(Diagnostic("unsupported", "step-" + feature, f"step-level {feature} is outside this prototype", _span(_member(step_node, feature), filename)))
            steps.append(Step(index, str(raw_step.get("id", "")), uses if isinstance(uses, str) else None,
                              str(run) if run is not None else None, dict(inputs),
                              str(raw_step_if) if raw_step_if is not None else None, step_expr, span,
                              _span(_member(step_node, "run"), filename) if has_run else None,
                              dict(raw_step.get("env", {})) if isinstance(raw_step.get("env", {}), dict) else {},
                              str(raw_step.get("name", ""))))
        jobs.append(Job(str(job_id), list(needs), str(raw_if) if raw_if is not None else None, expr,
                        job_permissions, "permissions" in raw_job, steps, _span(job_node, filename)))
    job_ids = {job.job_id for job in jobs}
    for job in jobs:
        for dependency in job.needs:
            if dependency not in job_ids:
                diagnostics.append(Diagnostic("invalid", "needs-missing", f"unknown needs job {dependency!r}", job.span))
    _check_cycles(jobs, diagnostics)
    for event, cfg in events.items():
        if event not in {"pull_request", "push", "workflow_run"}:
            diagnostics.append(Diagnostic("unsupported", "trigger-event", f"event {event!r} is outside the prototype subset", _span(_member(_member(tree, "on"), str(event)), filename)))
        if cfg is not None and not isinstance(cfg, dict):
            diagnostics.append(Diagnostic("invalid", "trigger-config", f"trigger {event!r} must be a mapping", _span(_member(_member(tree, "on"), str(event)), filename)))
    return Workflow(filename, str(data.get("name", filename)), events, permissions,
                    "permissions" in data, jobs, hashlib.sha256(source.encode()).hexdigest(), diagnostics)


def _check_cycles(jobs: list[Job], diagnostics: list[Diagnostic]) -> None:
    edges = {j.job_id: j.needs for j in jobs}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(job_id: str):
        if job_id in visiting:
            job = next(j for j in jobs if j.job_id == job_id)
            diagnostics.append(Diagnostic("invalid", "needs-cycle", f"needs cycle includes {job_id!r}", job.span))
            return
        if job_id in visited:
            return
        visiting.add(job_id)
        for dep in edges.get(job_id, []):
            if dep in edges:
                visit(dep)
        visiting.remove(job_id)
        visited.add(job_id)

    for job_id in edges:
        visit(job_id)


def _walk(expr: Expr | None):
    if expr is None:
        return
    yield expr
    if expr.left:
        yield from _walk(expr.left)
    if expr.right:
        yield from _walk(expr.right)


def _clean(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_]", "_", value).lower()
    return "x_" + result if not result or result[0].isdigit() else result


def _hash(value: str, count: int = 10) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:count]


def _atom(symbol: str, value: str) -> str:
    return "v_" + _hash(symbol + "\0" + value)


def _wf_key(wf: Workflow) -> str:
    return _clean(Path(wf.path).stem)


def _pc(wf: Workflow, job: Job) -> str:
    return f"pc_{_wf_key(wf)}_{_clean(job.job_id)}"


def _sched(wf: Workflow, job: Job) -> str:
    return "sched_" + _pc(wf, job)


def _step_pc(wf: Workflow, job: Job, step: Step) -> str:
    return f"{_pc(wf, job)}_step_{step.index}"


def _site(wf: Workflow, job: Job, step: Step) -> str:
    return f"{wf.path}::{job.job_id}::{step.step_id or step.index}"


def _split_action(ref: str | None) -> tuple[str, str | None]:
    if not ref or "@" not in ref:
        return "unknown", None
    return tuple(ref.rsplit("@", 1))


def _input(step: Step, key: str) -> str | None:
    value = step.inputs.get(key)
    return value if isinstance(value, str) else None


def _supported_runner_temp_path(value: str | None) -> bool:
    """Accept runner.temp plus a literal relative suffix as a symbolic path."""
    if not value:
        return False
    match = re.fullmatch(r"\$\{\{\s*runner\.temp\s*}}((?:/[A-Za-z0-9._/-]+)?)", value)
    if not match:
        return False
    suffix = match.group(1)
    return all(part not in {".", ".."} for part in suffix.split("/") if part)


class ModelBuilder:
    def __init__(self, producer: Workflow, consumer: Workflow):
        self.producer, self.consumer = producer, consumer
        self.workflows = [producer, consumer]
        self.diagnostics = producer.diagnostics + consumer.diagnostics
        self.uploads: list[tuple[Workflow, Job, Step, str]] = []
        self.downloads: list[tuple[Workflow, Job, Step, str, list[str], bool]] = []
        self.unknown_conditions: dict[str, str] = {}
        self.generic_refs: dict[tuple[str, str, str], set[str]] = {}
        self.symbols: dict[str, dict[str, Any]] = {}
        self.permission_symbols: dict[tuple[str, str, str], str] = {}
        self.permission_invariants: list[str] = []
        self.expr_cache: dict[tuple[str, str, str, int | None], str] = {}
        self._validate()
        self._collect_expressions()
        self._collect_actions()
        self.producer_trigger = self._producer_trigger()
        self.consumer_trigger = self._consumer_trigger()
        for wf in self.workflows:
            for job in wf.jobs:
                self.job_guards[(wf.path, job.job_id)] = self._job_guard(wf, job)
                for step in job.steps:
                    self.step_guards[(wf.path, job.job_id, step.index)] = self._step_guard(wf, job, step)

    job_guards: dict[tuple[str, str], str]
    step_guards: dict[tuple[str, str, int], str]

    def _validate(self):
        self.job_guards, self.step_guards = {}, {}
        if not self.producer.jobs or not self.consumer.jobs:
            self.diagnostics.append(Diagnostic("invalid", "empty-jobs", "both workflows need at least one job", None))
        if "workflow_run" not in self.consumer.events:
            self.diagnostics.append(Diagnostic("invalid", "consumer-trigger", "consumer must declare workflow_run", None))

    def _collect_expressions(self):
        for wf in self.workflows:
            for job in wf.jobs:
                for expr in [job.condition] + [s.condition for s in job.steps]:
                    for node in _walk(expr):
                        if node.kind == "ref":
                            if getattr(self, "v2", False) and node.value.startswith("steps.") and ".outputs." in node.value:
                                continue
                            self.generic_refs.setdefault(self._generic_key(wf, job, node.value), set())
                        elif node.kind in {"==", "!="}:
                            l, r = node.left, node.right
                            if l and l.kind == "ref" and r and r.kind == "string":
                                self.generic_refs.setdefault(self._generic_key(wf, job, l.value), set()).add(r.value)
                            if r and r.kind == "ref" and l and l.kind == "string":
                                self.generic_refs.setdefault(self._generic_key(wf, job, r.value), set()).add(l.value)

    def _generic_key(self, wf: Workflow, job: Job, ref: str) -> tuple[str, str, str]:
        scope = job.job_id if ref.startswith(("steps.", "needs.")) else ""
        return wf.path, scope, ref

    def _collect_actions(self):
        for wf in self.workflows:
            for job in wf.jobs:
                for step in job.steps:
                    if not step.uses:
                        continue
                    action, ref = _split_action(step.uses)
                    if action not in {"actions/upload-artifact", "actions/download-artifact"}:
                        self.diagnostics.append(Diagnostic("unsupported", "unknown-action", f"{step.uses!r} has no modeled effect contract", step.span))
                        continue
                    if (action, ref) not in SUPPORTED_ACTIONS:
                        self.diagnostics.append(Diagnostic("unsupported", "unregistered-action-ref", f"{step.uses!r} is not in the pinned prototype contract registry", step.span))
                        continue
                    self.diagnostics.append(Diagnostic("unknown", "action-outcome", "the action's success/failure outcome is selected once at this step and latched in model state", step.span))
                    needed = ("name", "path") if action.endswith("upload-artifact") else ("name", "path", "run-id")
                    for key in needed:
                        value = _input(step, key)
                        supported_run_id = action.endswith("download-artifact") and key == "run-id" and value == GH_EXPR_PREFIX + " github.event.workflow_run.id }}"
                        supported_temp_path = (action.endswith("download-artifact") and key == "path" and
                                               _supported_runner_temp_path(value))
                        if not supported_run_id and not supported_temp_path and (value is None or (chr(36) + "{") in value):
                            self.diagnostics.append(Diagnostic("unsupported", "dynamic-artifact-input", f"input {key!r} must be a literal in this prototype", step.span))
                    if action.endswith("upload-artifact"):
                        token = "object_" + _hash(_site(wf, job, step))
                        self.uploads.append((wf, job, step, token))
                    else:
                        token = "download_" + _hash(_site(wf, job, step))
                        self.downloads.append((wf, job, step, token, [], _input(step, "run-id") == GH_EXPR_PREFIX + " github.event.workflow_run.id }}"))
        resolved = []
        for wf, job, step, token, _, valid_id in self.downloads:
            matches = [obj for uwf, _, us, obj in self.uploads
                       if uwf.path == self.producer.path and _input(step, "name") == _input(us, "name") and valid_id]
            if len(matches) > 1:
                self.diagnostics.append(Diagnostic("unsupported", "ambiguous-artifact-name", "more than one producer upload has this name; object selection is not modeled", step.span))
            if not matches:
                self.diagnostics.append(Diagnostic("unknown", "no-upload-candidate", "no producer upload with this name/run identity was found", step.span))
            if wf.path != self.consumer.path:
                self.diagnostics.append(Diagnostic("unsupported", "download-location", "only consumer-workflow downloads are modeled", step.span))
            resolved.append((wf, job, step, token, matches, valid_id))
        self.downloads = resolved

    def _step_prerequisite_guard(self, wf: Workflow, job: Job, step: Step) -> str:
        """Additional modeled preconditions for a step's effects, if any."""
        return "TRUE"

    def _step_effect_guard(self, wf: Workflow, job: Job, step: Step, downloaded: tuple | None) -> str:
        terms = []
        if downloaded:
            terms.append(f"can_{downloaded[3]}")
        prerequisite = self._step_prerequisite_guard(wf, job, step)
        if prerequisite != "TRUE":
            terms.append(f"({prerequisite})")
        return " & ".join(terms) if terms else "TRUE"

    def _symbol(self, ref: str, wf: Workflow, job: Job) -> tuple[str, str, dict[str, str]]:
        if ref == "github.event_name":
            return ("ev_workflow_run", "constant", {}) if wf.path == self.consumer.path else ("origin_event", "origin-event", {})
        if ref == "github.event.workflow_run.event":
            return "origin_event", "origin-event", {}
        if ref == "github.event.workflow_run.conclusion":
            return "producer_phase", "conclusion", {}
        if ref == "github.event.workflow_run.id":
            return "producer_run_id", "constant", {}
        if ref == "github.event.workflow_run.name":
            return "producer_workflow_name", "constant", {}
        if ref == "github.event.workflow_run.head_branch":
            return "origin_head_branch", "head-branch", {}
        if ref == "github.event.pull_request.base.ref":
            return "origin_base_branch", "base-branch", {}
        if ref == "github.ref_name":
            return "origin_push_branch", "push-branch", {}
        if ref.startswith("needs.") and ref.endswith(".result"):
            dep = ref.split(".")[1]
            return f"result_{_wf_key(wf)}_{_clean(dep)}", "job-result", {}
        scope = job.job_id if ref.startswith(("steps.", "needs.")) else ""
        symbol = "ctx_" + _hash(wf.path + "::" + scope + "::" + ref)
        key = (wf.path, scope, ref)
        return symbol, "generic:" + ref, {v: _atom(symbol, v) for v in sorted(self.generic_refs.get(key, set()))}

    def _unknown_bool(self, text: str, span: Span) -> str:
        key = span.file + "::" + text.strip()
        name = self.unknown_conditions.setdefault(key, "unknown_if_" + _hash(key))
        self.diagnostics.append(Diagnostic("unknown", "condition-unknown", "condition uses one correlated nondeterministic Boolean choice", span))
        self.symbols.setdefault(name, {"kind": "unknown-condition", "sources": [span.json()]})
        return name

    def _compile_expr(self, expr: Expr | None, wf: Workflow, job: Job, step: Step | None, implicit: str, span: Span) -> str:
        if expr is None:
            return implicit

        def go(node: Expr) -> tuple[str, str, str | None]:
            if node.kind == "bool":
                return ("TRUE" if node.value else "FALSE", "bool", None)
            if node.kind == "string":
                return str(node.value), "literal", None
            if node.kind == "ref":
                ref = node.value
                if ref.startswith("steps.") and ".outputs." in ref:
                    if not getattr(self, "v2", False):
                        self.diagnostics.append(Diagnostic("unsupported", "step-output", f"{ref!r} is an unknown value because run effects are opaque", span))
                if ref.startswith("needs.") and ".outputs." in ref:
                    self.diagnostics.append(Diagnostic("unsupported", "job-output", f"{ref!r} depends on job outputs not modeled by this prototype", span))
                sym, typ, _ = self._symbol(ref, wf, job)
                if typ.startswith("generic:"):
                    if not self.generic_refs.get(self._generic_key(wf, job, ref)):
                        self.generic_refs[self._generic_key(wf, job, ref)] = set()
                    return sym, typ, ref
                return sym, typ, ref
            if node.kind == "call":
                if node.value == "always":
                    return "TRUE", "bool", None
                if node.value == "success":
                    return implicit, "bool", None
                if node.value == "failure":
                    if step is not None:
                        return f"failed_{_pc(wf, job)}", "bool", None
                    return self._needs_failed(wf, job), "bool", None
                if node.value == "cancelled":
                    self.diagnostics.append(Diagnostic("unsupported", "cancelled-status", "cancellation is not modeled", span))
                    return self._unknown_bool("cancelled(" + wf.path + job.job_id + ")", span), "bool", None
                self.diagnostics.append(Diagnostic("unsupported", "expression-function", f"{node.value}() is unsupported", span))
                return self._unknown_bool(node.value, span), "bool", None
            if node.kind == "not":
                value, typ, ref = go(node.left)
                if typ != "bool":
                    self.diagnostics.append(Diagnostic("unsupported", "expression-type", "negation requires Boolean input", span))
                    return self._unknown_bool(repr(expr), span), "bool", None
                return f"!({value})", "bool", None
            if node.kind in {"&&", "||"}:
                left, lt, _ = go(node.left)
                right, rt, _ = go(node.right)
                if lt != "bool" or rt != "bool":
                    self.diagnostics.append(Diagnostic("unsupported", "expression-type", f"{node.kind} requires Boolean operands", span))
                    return self._unknown_bool(repr(expr), span), "bool", None
                return f"({left} {'&' if node.kind == '&&' else '|'} {right})", "bool", None
            if node.kind in {"==", "!="}:
                left, lt, lr = go(node.left)
                right, rt, rr = go(node.right)
                if lt == "literal" and rt != "literal":
                    left, right, lt, rt, lr, rr = right, left, rt, lt, rr, lr
                invert = node.kind == "!="
                if rt == "literal":
                    literal = right
                    if lt == "origin-event":
                        if literal not in {"pull_request", "push", "other"}:
                            return ("FALSE" if not invert else "TRUE"), "bool", None
                        atom = _atom("origin_event", literal)
                    elif lt == "conclusion":
                        if literal not in {"success", "failure", "other"}:
                            return ("FALSE" if not invert else "TRUE"), "bool", None
                        atom = {
                            "success": "conclusion_success",
                            "failure": "conclusion_failure",
                            "other": "conclusion_other",
                        }[literal]
                        pred = f"(producer_conclusion = {atom})"
                        return (f"!{pred}" if invert else pred), "bool", None
                    elif lt == "base-branch":
                        atom = _atom("origin_base_branch", literal)
                    elif lt == "push-branch":
                        atom = _atom("origin_push_branch", literal)
                    elif lt == "head-branch":
                        atom = _atom("origin_head_branch", literal)
                    elif lt == "constant":
                        expected = {"ev_workflow_run": "workflow_run", "producer_run_id": "producer_run_id",
                                    "producer_workflow_name": self.producer.name}.get(left, left)
                        equal = literal == expected
                        return (("FALSE" if invert else "TRUE") if equal else ("TRUE" if invert else "FALSE")), "bool", None
                    elif lt.startswith("generic:"):
                        ref = lt[len("generic:"):]
                        symbol, _, mapping = self._symbol(ref, wf, job)
                        atom = mapping.get(literal)
                        if atom is None:
                            return ("TRUE" if invert else "FALSE"), "bool", None
                        left = symbol
                    elif lt == "job-result":
                        if lr and lr.startswith("needs."):
                            dep_id = lr.split(".")[1]
                        elif rr and rr.startswith("needs."):
                            dep_id = rr.split(".")[1]
                        else:
                            dep_id = ""
                        dep = next((j for j in wf.jobs if j.job_id == dep_id), None)
                        target = {"success": "done", "failure": "failed", "skipped": "skipped"}.get(literal)
                        if dep is None or target is None:
                            return self._unknown_bool(repr(expr), span), "bool", None
                        pc = _pc(wf, dep)
                        pred = f"({pc} = {pc}_{target})"
                        return (f"!{pred}" if invert else pred), "bool", None
                    elif lt.startswith("step-output:"):
                        output_values = getattr(self, "output_values", {}).get(left)
                        if output_values is None:
                            self.diagnostics.append(Diagnostic("unsupported", "step-output-domain", f"No state domain is registered for {lr!r}", span))
                            return self._unknown_bool(repr(expr), span), "bool", None
                        value = output_values.get(literal, output_values.get("other"))
                        if value is None:
                            return ("TRUE" if invert else "FALSE"), "bool", None
                        pred = f"({left} = {value})"
                        return (f"!{pred}" if invert else pred), "bool", None
                    else:
                        self.diagnostics.append(Diagnostic("unsupported", "expression-comparison", "comparison cannot be represented by the supported finite value domain", span))
                        return self._unknown_bool(repr(expr), span), "bool", None
                    pred = f"({left} {'!=' if invert else '='} {atom})"
                    return pred, "bool", None
                if lt == rt and left == right:
                    return ("FALSE" if invert else "TRUE"), "bool", None
                self.diagnostics.append(Diagnostic("unsupported", "cross-symbol-comparison", "comparison of two distinct unknown values is unsupported", span))
                return self._unknown_bool(repr(expr), span), "bool", None
            self.diagnostics.append(Diagnostic("unsupported", "expression-syntax", "expression syntax is outside the supported subset", span))
            return self._unknown_bool(repr(expr), span), "bool", None

        result, typ, _ = go(expr)
        if typ != "bool":
            self.diagnostics.append(Diagnostic("unsupported", "condition-type", "if must lower to Boolean", span))
            return self._unknown_bool(repr(expr), span)
        status_calls = {x.value for x in _walk(expr) if x.kind == "call"}
        has_status = bool(status_calls & {"success", "failure", "cancelled", "always"})
        return result if has_status else f"({implicit} & ({result}))"

    def _needs_failed(self, wf: Workflow, job: Job) -> str:
        deps = [next((j for j in wf.jobs if j.job_id == dep), None) for dep in job.needs]
        pcs = [f"{_pc(wf, dep)} = {_pc(wf, dep)}_failed" for dep in deps if dep]
        return "(" + " | ".join(pcs) + ")" if pcs else "FALSE"

    def _job_guard(self, wf: Workflow, job: Job) -> str:
        if job.condition is None:
            return self._needs_success(wf, job)
        return self._compile_expr(job.condition, wf, job, None, self._needs_success(wf, job), job.span)

    def _step_guard(self, wf: Workflow, job: Job, step: Step) -> str:
        if step.condition is None:
            return f"!failed_{_pc(wf, job)}"
        return self._compile_expr(step.condition, wf, job, step, f"!failed_{_pc(wf, job)}", step.span)

    def _needs_success(self, wf: Workflow, job: Job) -> str:
        values = []
        for depid in job.needs:
            dep = next((j for j in wf.jobs if j.job_id == depid), None)
            if dep:
                pc = _pc(wf, dep)
                values.append(f"{pc} = {pc}_done")
        return " & ".join(values) if values else "TRUE"

    def _producer_trigger(self) -> str:
        clauses = []
        for event, cfg in self.producer.events.items():
            if event not in {"pull_request", "push"}:
                continue
            parts = [f"origin_event = {_atom('origin_event', event)}"]
            if event == "pull_request":
                types = cfg.get("types", ["opened", "synchronize", "reopened"]) if isinstance(cfg, dict) else ["opened", "synchronize", "reopened"]
                if isinstance(types, str):
                    types = [types]
                if not types:
                    parts.append("FALSE")
                else:
                    parts.append("(" + " | ".join(f"origin_pr_activity = {_atom('origin_pr_activity', str(t))}" for t in types) + ")")
            if isinstance(cfg, dict):
                branches = cfg.get("branches", [])
                if isinstance(branches, str):
                    branches = [branches]
                if branches:
                    var = "origin_base_branch" if event == "pull_request" else "origin_push_branch"
                    valid = [b for b in branches if isinstance(b, str) and not any(c in b for c in "*?![]")]
                    if valid:
                        parts.append("(" + " | ".join(f"{var} = {_atom(var, b)}" for b in valid) + ")")
                    if len(valid) != len(branches):
                        self.diagnostics.append(Diagnostic("unsupported", "branch-pattern", "glob/negative branch filters are unsupported", None))
                        choice = "unknown_branch_" + _hash(self.producer.path + event)
                        self.unknown_conditions["branch:" + choice] = choice
                        parts.append(choice)
                if "paths" in cfg or "paths-ignore" in cfg:
                    choice = "unknown_path_" + _hash(self.producer.path + event)
                    self.unknown_conditions["trigger:" + choice] = choice
                    self.diagnostics.append(Diagnostic("unknown", "path-filter", "path filters are abstracted as an unknown trigger input", None))
                    parts.append(choice)
            clauses.append("(" + " & ".join(parts) + ")")
        return "(" + " | ".join(clauses) + ")" if clauses else "FALSE"

    def _consumer_trigger(self) -> str:
        cfg = self.consumer.events.get("workflow_run")
        if not isinstance(cfg, dict):
            return "FALSE"
        names = cfg.get("workflows", [])
        if isinstance(names, str):
            names = [names]
        if "workflows" not in cfg:
            names = [self.producer.name]
        name_match = self.producer.name in names
        if not name_match:
            self.diagnostics.append(Diagnostic("unknown", "workflow-name-mismatch", "workflow_run workflows filter does not match the supplied producer name", None))
        types = cfg.get("types", ["completed"])
        if isinstance(types, str):
            types = [types]
        allowed = [v for v in types if v in {"requested", "in_progress", "completed"}]
        if len(allowed) != len(types):
            self.diagnostics.append(Diagnostic("unsupported", "workflow-run-type", "unrecognized workflow_run activity type is outside the prototype", None))
        if not allowed:
            self.diagnostics.append(Diagnostic("unsupported", "workflow-run-types", "workflow_run event types are unsupported or empty", None))
            return "FALSE"
        activity_atoms = {"requested": "activity_requested", "in_progress": "activity_in_progress", "completed": "activity_completed"}
        activity_expr = "(" + " | ".join("consumer_activity = " + activity_atoms[t] for t in allowed) + ")"
        branch_expr = "TRUE"
        branches = cfg.get("branches", [])
        if isinstance(branches, str):
            branches = [branches]
        if branches:
            valid = [b for b in branches if isinstance(b, str) and not any(c in b for c in "*?![]")]
            if valid:
                branch_expr = "(" + " | ".join(f"origin_head_branch = {_atom('origin_head_branch', b)}" for b in valid) + ")"
            if len(valid) != len(branches):
                choice = "unknown_branch_filter"
                self.unknown_conditions["branch-filter:" + self.consumer.path] = choice
                self.diagnostics.append(Diagnostic("unsupported", "workflow-run-branch-pattern", "glob/negative workflow_run branches are unsupported", None))
                branch_expr = "(" + branch_expr + " | " + choice + ")"
        event_ready = (
            "((consumer_activity = activity_requested & producer_trigger) | "
            "(consumer_activity = activity_in_progress & producer_trigger & producer_has_started) | "
            "(consumer_activity = activity_completed & producer_trigger & producer_terminal))"
        )
        return f"({str(name_match).upper()} & {activity_expr} & {branch_expr} & {event_ready})"

    def _effective_permission(self, wf: Workflow, job: Job, scope: str) -> str:
        if job.permissions_declared:
            values = job.permissions or {}
        elif wf.permissions_declared:
            values = wf.permissions or {}
        else:
            name = f"permission_{_wf_key(wf)}_{_clean(job.job_id)}_{scope}"
            if name not in self.symbols:
                self.symbols[name] = {"kind": "unknown-permission", "sources": [job.span.json()]}
                self.diagnostics.append(Diagnostic("unknown", "repository-permission-default", f"effective {scope} permission depends on repository policy not supplied to this bounded model", job.span))
            return name
        permission = values.get("*", values.get(scope, "none"))
        return {"none": "perm_none", "read": "perm_read", "write": "perm_write"}[permission]

    def _branch_domain(self, event_key: str) -> set[str]:
        values = set()
        for wf in self.workflows:
            cfg = wf.events.get(event_key)
            if isinstance(cfg, dict):
                raw = cfg.get("branches", [])
                if isinstance(raw, str):
                    raw = [raw]
                if isinstance(raw, list):
                    values.update(v for v in raw if isinstance(v, str) and not any(c in v for c in "*?![]"))
        return values

    def _generic_decls(self) -> list[str]:
        result = []
        for (path, scope, ref), literals in sorted(self.generic_refs.items()):
            if ref in {"github.event_name", "github.event.workflow_run.event", "github.event.workflow_run.conclusion",
                       "github.event.workflow_run.id", "github.event.workflow_run.name",
                       "github.event.workflow_run.head_branch", "github.event.pull_request.base.ref",
                       "github.ref_name"} or (ref.startswith("needs.") and ref.endswith(".result")):
                continue
            wf = next(w for w in self.workflows if w.path == path)
            sym = "ctx_" + _hash(wf.path + "::" + scope + "::" + ref)
            domain = [_atom(sym, v) for v in sorted(literals)] + [_atom(sym, "other")]
            result.append(f"  {sym} : {{ " + ", ".join(domain) + " };")
            self.symbols.setdefault(sym, {"kind": "unknown-context", "sources": []})
        return result

    def build(self) -> tuple[str, dict[str, Any]]:
        if any(d.status == "invalid" for d in self.diagnostics):
            raise WorkflowError("invalid workflow structure; no SMV generated")
        all_events = {e for e in self.producer.events if e in {"pull_request", "push"}}
        if not all_events:
            all_events = {"other"}
        base = self._branch_domain("pull_request")
        push = self._branch_domain("push")
        head = set()
        cfg = self.consumer.events.get("workflow_run")
        if isinstance(cfg, dict):
            raw = cfg.get("branches", [])
            if isinstance(raw, str):
                raw = [raw]
            if isinstance(raw, list):
                head = {v for v in raw if isinstance(v, str) and not any(c in v for c in "*?![]")}
        activity_values = {"opened", "synchronize", "reopened"}
        for wf in self.workflows:
            cfg2 = wf.events.get("pull_request")
            if isinstance(cfg2, dict):
                types = cfg2.get("types", [])
                if isinstance(types, str):
                    types = [types]
                if isinstance(types, list):
                    activity_values.update(str(t) for t in types)
        frozen = [
            "  origin_event : { " + ", ".join([_atom("origin_event", e) for e in sorted(all_events)] + [_atom("origin_event", "other")]) + " };",
            "  origin_pr_activity : { " + ", ".join([_atom("origin_pr_activity", e) for e in sorted(activity_values)] + [_atom("origin_pr_activity", "other")]) + " };",
            "  origin_base_branch : { " + ", ".join([_atom("origin_base_branch", e) for e in sorted(base)] + [_atom("origin_base_branch", "other")]) + " };",
            "  origin_push_branch : { " + ", ".join([_atom("origin_push_branch", e) for e in sorted(push)] + [_atom("origin_push_branch", "other")]) + " };",
            "  origin_head_branch : { " + ", ".join([_atom("origin_head_branch", e) for e in sorted(head)] + [_atom("origin_head_branch", "other")]) + " };",
            "  consumer_activity : { activity_requested, activity_in_progress, activity_completed };",
        ]
        for wf in self.workflows:
            for job in wf.jobs:
                for scope in ("actions", "contents"):
                    value = self._effective_permission(wf, job, scope)
                    symbol = f"perm_{_wf_key(wf)}_{_clean(job.job_id)}_{scope}"
                    self.permission_symbols[(wf.path, job.job_id, scope)] = symbol
                    frozen.append(f"  {symbol} : {{ perm_none, perm_read, perm_write }};")
                    self.symbols[symbol] = {
                        "kind": "effective-token-permission",
                        "sources": [job.span.json()],
                    }
                    if not value.startswith("permission_"):
                        self.permission_invariants.append(f"{symbol} = {value}")
        frozen.extend(self._generic_decls())
        if getattr(self, "v2", False):
            frozen.extend(self._v2_frozen_decls())
        for value in self.unknown_conditions.values():
            if value.startswith("unknown_path_") or value.startswith("unknown_branch_"):
                frozen.append(f"  {value} : boolean;")
            elif value.startswith("unknown_if_"):
                frozen.append(f"  {value} : boolean;")
        ivars = ["  scheduler : { " + ", ".join(_sched(w, j) for w in self.workflows for j in w.jobs) + " };"]
        outcomes = {}
        for wf in self.workflows:
            for job in wf.jobs:
                for step in job.steps:
                    outcomes[(wf.path, job.job_id, step.index)] = "outcome_" + _hash(_site(wf, job, step))
                    ivars.append(f"  {outcomes[(wf.path, job.job_id, step.index)]} : {{ success, failure }};")

        variables = [
            "  producer_phase : { run_not_created, run_requested, run_in_progress, run_completed_success, run_completed_failure, run_completed_unknown };",
            "  producer_has_started : boolean;",
            "  producer_conclusion : { conclusion_success, conclusion_failure, conclusion_other };",
        ]
        for wf in self.workflows:
            for job in wf.jobs:
                pc = _pc(wf, job)
                states = [pc + "_dormant", pc + "_ready"] + [_step_pc(wf, job, s) for s in job.steps] + [pc + "_done", pc + "_failed", pc + "_skipped"]
                variables.append(f"  {pc} : {{ " + ", ".join(states) + " };")
                variables.append(f"  failed_{pc} : boolean;")
        for _, _, _, token in self.uploads:
            variables.append(f"  {token}_state : {{ not_uploaded, upload_failed, {token} }};")
        for _, _, _, token, matches, _ in self.downloads:
            variables.append(f"  {token}_state : {{ not_attempted, download_failed, download_succeeded }};")
            # Keep the slot's type non-singleton when there is no matching
            # upload. The sentinel is unreachable, but prevents NuSMV from
            # folding an assigned VAR into a constant and rejecting ASSIGN.
            object_values = ["no_object"] + matches
            if not matches:
                object_values.append("unmatched_object")
            variables.append(f"  {token}_object : {{ " + ", ".join(object_values) + " };")
        if getattr(self, "v2", False):
            variables.extend(self._v2_variable_decls())

        defines = [
            f"  producer_trigger := {self.producer_trigger};",
            "  producer_terminal := producer_phase in { run_completed_success, run_completed_failure, run_completed_unknown };",
            f"  consumer_trigger := {self.consumer_trigger};",
        ]
        producer_pcs = [_pc(self.producer, j) for j in self.producer.jobs]
        all_terminal = " & ".join(f"{p} in {{ {p}_done, {p}_failed, {p}_skipped }}" for p in producer_pcs) or "FALSE"
        any_failed = " | ".join(f"{p} = {p}_failed" for p in producer_pcs) or "FALSE"
        any_success = " | ".join(f"{p} = {p}_done" for p in producer_pcs) or "FALSE"
        any_started = " | ".join(f"{p} != {p}_dormant" for p in producer_pcs) or "FALSE"
        for wf in self.workflows:
            for job in wf.jobs:
                pc = _pc(wf, job)
                if job.needs:
                    failed_expr = self._needs_failed(wf, job)
                    defines.append(f"  needs_terminal_{pc} := {self._needs_terminal(wf, job)};")
                    defines.append(f"  needs_failed_{pc} := {failed_expr};")
                for depid in job.needs:
                    dep = next((j for j in wf.jobs if j.job_id == depid), None)
                    if dep:
                        dpc = _pc(wf, dep)
                        result = f"result_{_wf_key(wf)}_{_clean(depid)}"
                        defines.append(f"  {result}_success := {dpc} = {dpc}_done;")
                        defines.append(f"  {result}_failure := {dpc} = {dpc}_failed;")
                        defines.append(f"  {result}_skipped := {dpc} = {dpc}_skipped;")
        for _, job, step, token, matches, valid_id in self.downloads:
            wf = self.consumer
            perm = self.permission_symbols[(wf.path, job.job_id, "actions")]
            token_input = _input(step, "github-token")
            token_ok = token_input in {GH_EXPR_PREFIX + " secrets.GITHUB_TOKEN }}", GH_EXPR_PREFIX + " github.token }}"}
            if not token_ok:
                self.diagnostics.append(Diagnostic("unsupported", "download-token", "download requires an explicit GITHUB_TOKEN expression in this prototype", step.span))
            candidates = " | ".join(f"{obj}_state = {obj}" for obj in matches) or "FALSE"
            defines.append(f"  can_{token} := (({candidates}) & ({perm} in {{ perm_read, perm_write }}) & {str(token_ok and valid_id).upper()});")
            defines.append(f"  same_object_{token} := ({token}_state = download_succeeded & {token}_object != no_object);")
        if getattr(self, "v2", False):
            defines.extend(self._v2_define_decls())

        assigns = [
            "  init(producer_phase) := run_not_created;",
            "  next(producer_phase) := case",
            "    !producer_trigger : run_not_created;",
            f"    ({all_terminal}) & ({any_failed}) : run_completed_failure;",
            f"    ({all_terminal}) & !({any_success}) : run_completed_unknown;",
            f"    ({all_terminal}) : run_completed_success;",
            "    producer_has_started : run_in_progress;",
            "    TRUE : run_requested;",
            "  esac;",
            "  init(producer_has_started) := FALSE;",
            f"  next(producer_has_started) := producer_has_started | (producer_trigger & ({any_started}));",
            "  init(producer_conclusion) := conclusion_other;",
            "  next(producer_conclusion) := case",
            "    producer_terminal : producer_conclusion;",
            f"    ({all_terminal}) & ({any_failed}) : conclusion_failure;",
            f"    ({all_terminal}) & ({any_success}) : conclusion_success;",
            f"    ({all_terminal}) : {{ conclusion_success, conclusion_other }};",
            "    TRUE : conclusion_other;",
            "  esac;",
        ]

        for wf in self.workflows:
            trigger = "producer_trigger" if wf.path == self.producer.path else "consumer_trigger"
            for job in wf.jobs:
                pc = _pc(wf, job)
                clauses = []
                if job.needs:
                    needs_ready = self._needs_terminal(wf, job)
                else:
                    needs_ready = "TRUE"
                guard = self.job_guards[(wf.path, job.job_id)]
                clauses.append(f"    scheduler = {_sched(wf, job)} & {pc} = {pc}_dormant & {trigger} & ({needs_ready}) & !({guard}) : {pc}_skipped;")
                clauses.append(f"    scheduler = {_sched(wf, job)} & {pc} = {pc}_dormant & {trigger} & ({needs_ready}) & ({guard}) : {pc}_ready;")
                clauses.append(f"    scheduler = {_sched(wf, job)} & {pc} = {pc}_ready : {_step_pc(wf, job, job.steps[0]) if job.steps else pc + '_done'};")
                for i, step in enumerate(job.steps):
                    current = _step_pc(wf, job, step)
                    final = i == len(job.steps) - 1
                    next_pc = _step_pc(wf, job, job.steps[i + 1]) if not final else None
                    next_success = f"({pc}_failed if failed_{pc} else {pc}_done)" if False else None
                    # The final PC choice is expanded into guarded cases to avoid inventing a YAML step.
                    guard = self.step_guards[(wf.path, job.job_id, step.index)]
                    outcome = outcomes[(wf.path, job.job_id, step.index)]
                    action, ref = _split_action(step.uses) if step.uses else ("", None)
                    uploaded = next((t for w, j, s, t in self.uploads if w.path == wf.path and j.job_id == job.job_id and s.index == step.index), None)
                    downloaded = next((d for d in self.downloads if d[0].path == wf.path and d[1].job_id == job.job_id and d[2].index == step.index), None)
                    effect_guard = self._step_effect_guard(wf, job, step, downloaded)
                    if final:
                        clauses.append(f"    scheduler = {_sched(wf, job)} & {pc} = {current} & !({guard}) & failed_{pc} : {pc}_failed;")
                        clauses.append(f"    scheduler = {_sched(wf, job)} & {pc} = {current} & !({guard}) & !failed_{pc} : {pc}_done;")
                        clauses.append(f"    scheduler = {_sched(wf, job)} & {pc} = {current} & ({guard}) & ({effect_guard}) & {outcome} = success & failed_{pc} : {pc}_failed;")
                        clauses.append(f"    scheduler = {_sched(wf, job)} & {pc} = {current} & ({guard}) & ({effect_guard}) & {outcome} = success & !failed_{pc} : {pc}_done;")
                        clauses.append(f"    scheduler = {_sched(wf, job)} & {pc} = {current} & ({guard}) & ((!( {effect_guard} )) | {outcome} = failure) : {pc}_failed;")
                    else:
                        clauses.append(f"    scheduler = {_sched(wf, job)} & {pc} = {current} & !({guard}) : {next_pc};")
                        clauses.append(f"    scheduler = {_sched(wf, job)} & {pc} = {current} & ({guard}) & ({effect_guard}) & {outcome} = success : {next_pc};")
                        clauses.append(f"    scheduler = {_sched(wf, job)} & {pc} = {current} & ({guard}) & ((!( {effect_guard} )) | {outcome} = failure) : {next_pc};")
                assigns.extend([
                    f"  init({pc}) := {pc}_dormant;",
                    f"  next({pc}) := case", *clauses, f"    TRUE : {pc};", "  esac;",
                    f"  init(failed_{pc}) := FALSE;",
                    f"  next(failed_{pc}) := case",
                    *[
                        f"    scheduler = {_sched(wf, job)} & {pc} = {_step_pc(wf, job, step)} & ({self.step_guards[(wf.path, job.job_id, step.index)]}) & ({failure_guard}) : TRUE;"
                        for step in job.steps
                        for down in [next((d for d in self.downloads if d[0].path == wf.path and d[1].job_id == job.job_id and d[2].index == step.index), None)]
                        for failure_guard in [
                            self._step_failure_guard(wf, job, step, outcomes[(wf.path, job.job_id, step.index)], down)
                        ]
                    ],
                    f"    TRUE : failed_{pc};",
                    "  esac;",
                ])

        for wf, job, step, token in self.uploads:
            pc, step_pc = _pc(wf, job), _step_pc(wf, job, step)
            outcome = outcomes[(wf.path, job.job_id, step.index)]
            guard = self.step_guards[(wf.path, job.job_id, step.index)]
            assigns.extend([
                f"  init({token}_state) := not_uploaded;",
                f"  next({token}_state) := case",
                f"    scheduler = {_sched(wf, job)} & {pc} = {step_pc} & ({guard}) & {outcome} = success : {token};",
                f"    scheduler = {_sched(wf, job)} & {pc} = {step_pc} & ({guard}) & {outcome} = failure : upload_failed;",
                f"    TRUE : {token}_state;",
                "  esac;",
            ])
            self.symbols[token + "_state"] = {"kind": "artifact-object-slot", "sources": [step.span.json()]}
            self.symbols[token] = {"kind": "artifact-object-id", "sources": [step.span.json()]}
        for wf, job, step, token, matches, _ in self.downloads:
            pc, step_pc = _pc(wf, job), _step_pc(wf, job, step)
            outcome = outcomes[(wf.path, job.job_id, step.index)]
            guard = self.step_guards[(wf.path, job.job_id, step.index)]
            can = f"can_{token}"
            selected = matches[0] if len(matches) == 1 else "no_object"
            assigns.extend([
                f"  init({token}_state) := not_attempted;",
                f"  next({token}_state) := case",
                f"    scheduler = {_sched(wf, job)} & {pc} = {step_pc} & ({guard}) & ({can}) & {outcome} = success : download_succeeded;",
                f"    scheduler = {_sched(wf, job)} & {pc} = {step_pc} & ({guard}) : download_failed;",
                f"    TRUE : {token}_state;",
                "  esac;",
                f"  init({token}_object) := no_object;",
                f"  next({token}_object) := case",
                f"    scheduler = {_sched(wf, job)} & {pc} = {step_pc} & ({guard}) & ({can}) & {outcome} = success : {selected};",
                f"    TRUE : {token}_object;",
                "  esac;",
            ])
            self.symbols[token + "_state"] = {"kind": "download-result", "sources": [step.span.json()]}
            self.symbols[token + "_object"] = {"kind": "downloaded-object-id", "sources": [step.span.json()]}

        if getattr(self, "v2", False):
            assigns.extend(self._v2_assign_decls(outcomes))

        for wf in self.workflows:
            for job in wf.jobs:
                self.symbols[_pc(wf, job)] = {"kind": "job-program-counter", "sources": [job.span.json()]}
                self.symbols[f"failed_{_pc(wf, job)}"] = {"kind": "job-failure-latch", "sources": [job.span.json()]}
                for step in job.steps:
                    self.symbols[_step_pc(wf, job, step)] = {"kind": "step-program-counter", "sources": [step.span.json()]}

        lines = ["MODULE main", "FROZENVAR", *frozen, "IVAR", *ivars, "VAR", *variables, "DEFINE", *defines]
        if self.permission_invariants:
            constraints = " & ".join(f"({item})" for item in self.permission_invariants)
            lines.extend(["INVAR", f"{constraints};"])
        lines.extend(["ASSIGN", *assigns])
        for _, _, _, token, _, _ in self.downloads:
            lines.append(f"CTLSPEC AG ({token}_state = download_succeeded -> {token}_object != no_object)")
            # NuSMV checks CTL specifications from every initial state.  The
            # negated reachability invariant is therefore the useful query:
            # it is refuted exactly when a successful download is reachable
            # from at least one allowed initial scenario, with a counterexample.
            lines.append(f"CTLSPEC AG ({token}_state != download_succeeded)")
        if getattr(self, "v2", False):
            lines.extend(self._v2_properties())
            lines.append("-- v2 stateful value-flow model; real authority and simulated sinks are separate.")
        else:
            lines.append("-- Structural artifact-transfer properties only; no sink/shell safety verdict is emitted.")
        smv = "\n".join(lines) + "\n"
        manifest = {
            "format": "workflow-smv-source-map-v1",
            "sources": [{"path": w.path, "sha256": w.sha256} for w in self.workflows],
            "boundedRuns": {"producerRunId": "producer_run_id", "consumerRunId": "consumer_run_id", "distinct": True},
            "symbols": self.symbols,
            "objects": [{"token": t, "workflow": w.path, "job": j.job_id, "step": s.index,
                         "name": _input(s, "name"), "source": s.span.json()} for w, j, s, t in self.uploads],
            "downloads": [{"token": t, "candidateObjects": ids, "runIdReferencesProducer": valid,
                           "source": s.span.json()} for _, _, s, t, ids, valid in self.downloads],
            "properties": [
                {
                    "formula": f"AG ({t}_state != download_succeeded)",
                    "meaning": "false means NuSMV found a reachable successful transfer in at least one allowed initial scenario",
                    "source": s.span.json(),
                }
                for _, _, s, t, _, _ in self.downloads
            ],
            "diagnostics": [d.json() for d in self.diagnostics],
            "verdict": "not-produced: shell and sink effects are opaque",
        }
        if getattr(self, "v2", False):
            manifest.update(self._v2_manifest_fields())
        return smv, manifest

    def _step_failure_guard(self, wf: Workflow, job: Job, step: Step,
                            outcome: str, downloaded: tuple | None) -> str:
        terms = [f"{outcome} = failure"]
        if downloaded:
            terms.append(f"!can_{downloaded[3]}")
        prerequisite = self._step_prerequisite_guard(wf, job, step)
        if prerequisite != "TRUE":
            terms.append(f"!({prerequisite})")
        return "(" + " | ".join(terms) + ")"

    def _needs_terminal(self, wf: Workflow, job: Job) -> str:
        parts = []
        for depid in job.needs:
            dep = next((j for j in wf.jobs if j.job_id == depid), None)
            if dep:
                pc = _pc(wf, dep)
                parts.append(f"{pc} in {{ {pc}_done, {pc}_failed, {pc}_skipped }}")
        return " & ".join(parts) if parts else "TRUE"


def compile_pair(producer_path: Path, consumer_path: Path) -> tuple[str, dict[str, Any]]:
    return ModelBuilder(load_workflow(producer_path), load_workflow(consumer_path)).build()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("producer", type=Path)
    parser.add_argument("consumer", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--map", dest="map_path", type=Path)
    args = parser.parse_args()
    try:
        smv, manifest = compile_pair(args.producer, args.consumer)
    except WorkflowError as exc:
        parser.error(str(exc))
    mapping = args.map_path or Path(str(args.output) + ".map.json")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    mapping.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(smv, encoding="utf-8")
    mapping.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
