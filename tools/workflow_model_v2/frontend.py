#!/usr/bin/env python3
"""Source-preserving YAML frontend used by the compiler and reference BFS."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .workflow_loader import WorkflowLoader  # noqa: E402

GH_EXPR_PREFIX = chr(36) + "{{"



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



def run_content_line(tree, source):
    """Locate the actual scalar content line, including composite metadata."""
    visited=set()
    def visit(node):
        if id(node) in visited:
            return None
        visited.add(id(node))
        if isinstance(node,yaml.MappingNode):
            run=_member(node,'run')
            if (run is not None and node.start_mark.line+1==source['line']
                    and node.start_mark.column+1==source['column']):
                return run.start_mark.line+(2 if run.style in {'|','>'} else 1)
            for key,value in node.value:
                found=visit(value)
                if found is not None:
                    return found
        elif isinstance(node,yaml.SequenceNode):
            for value in node.value:
                found=visit(value)
                if found is not None:
                    return found
        return None
    line=visit(tree)
    if line is None:
        raise WorkflowError('cannot locate run source span: '+str(source))
    return line
