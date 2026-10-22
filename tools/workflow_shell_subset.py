"""Closed, source-mapped shell grammar used by the workflow model v2.

This is intentionally not a shell interpreter.  It accepts only complete
scripts made from the statement forms listed in ``SUPPORTED``; an unrecognised
non-empty line rejects the whole script so that a partially understood script
cannot be mistaken for a harmless one.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re
import shlex
from typing import Iterable


@dataclass(frozen=True)
class ShellSpan:
    line: int
    column: int
    end_line: int
    end_column: int


@dataclass(frozen=True)
class ShellOp:
    kind: str
    text: str
    span: ShellSpan
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    path: str | None = None
    value: str | None = None


@dataclass(frozen=True)
class ShellCondition:
    kind: str
    reads: tuple[str, ...]
    literal: str | None
    then_assignments: dict[str, str]
    else_assignments: dict[str, str]
    span: ShellSpan


@dataclass
class ShellProgram:
    operations: list[ShellOp]
    variable_edges: list[tuple[str, str, ShellSpan]] = field(default_factory=list)
    artifact_reads: list[ShellOp] = field(default_factory=list)
    output_writes: list[ShellOp] = field(default_factory=list)
    markers: list[ShellOp] = field(default_factory=list)
    sink_sites: list[ShellOp] = field(default_factory=list)
    control_blocks: list[tuple[str, ShellSpan]] = field(default_factory=list)
    conditions: list[ShellCondition] = field(default_factory=list)


class UnsupportedShell(ValueError):
    def __init__(self, message: str, span: ShellSpan):
        super().__init__(message)
        self.span = span


_VAR = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
_ASSIGN_TR = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)=\$\(tr -d '([^']*)' < \"\$([A-Za-z_][A-Za-z0-9_]*)([^\"]*)\"\)$"
)
_ASSIGN_SHA = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_]*)=\$\(sha256sum \"\$([A-Za-z_][A-Za-z0-9_]*)\" \| cut -d ' ' -f 1\)$"
)
_ASSIGN_LITERAL = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)='([^']*)'$|^([A-Za-z_][A-Za-z0-9_]*)=\"([^\"]*)\"$")
_IF_EQ = re.compile(r'^if \[ "\$([A-Za-z_][A-Za-z0-9_]*)" = "([^"$\n]*)" \]; then$')
_IF_VAR = re.compile(r'^if \[ "\$([A-Za-z_][A-Za-z0-9_]*)" = "\$([A-Za-z_][A-Za-z0-9_]*)" \]; then$')
_CASE = re.compile(r'^case "\$([A-Za-z_][A-Za-z0-9_]*)" in$')
_CASE_PATTERN = re.compile(r"^(.+)\)$")
_OUTPUT = re.compile(
    r"^printf '([A-Za-z_][A-Za-z0-9_]*)=%s\\n' \"\$([A-Za-z_][A-Za-z0-9_]*)\" >> \"\$GITHUB_OUTPUT\"$"
)
_TEST_LEN = re.compile(r'^test "\$\{#([A-Za-z_][A-Za-z0-9_]*)\}" -eq ([0-9]+)$')
_TEST_FILE = re.compile(r'^test -f "\$([A-Za-z_][A-Za-z0-9_]*)([^\"]*)"$')
_ECHO = re.compile(r"^echo(?: (.*))?$")
_PRINTF = re.compile(r"^printf (.+)$")
_REDIR = re.compile(r'^\}\s*(?:>>|\| tee -a) "\$GITHUB_STEP_SUMMARY"$')


def _span(line: int, text: str, column: int = 1) -> ShellSpan:
    return ShellSpan(line, column, line, column + len(text))


def _refs(text: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(_VAR.findall(text)))


def _emit(ops: list[ShellOp], kind: str, text: str, line: int,
          reads: Iterable[str] = (), writes: Iterable[str] = (),
          path: str | None = None, value: str | None = None,
          column: int = 1) -> ShellOp:
    op = ShellOp(kind, text, _span(line, text, column), tuple(reads), tuple(writes), path, value)
    ops.append(op)
    return op


def parse_shell(source: str, yaml_run_line: int = 1) -> ShellProgram:
    """Parse a complete script from the supported straight-line subset.

    The grammar deliberately keeps conditional and case boundaries as nodes;
    semantic lowering can then make their decisions in the generated model.
    """
    ops: list[ShellOp] = []
    edges: list[tuple[str, str, ShellSpan]] = []
    reads: list[ShellOp] = []
    outputs: list[ShellOp] = []
    markers: list[ShellOp] = []
    sinks: list[ShellOp] = []
    controls: list[tuple[str, ShellSpan]] = []
    stack: list[tuple[str, str, ShellSpan]] = []

    for offset, raw in enumerate(source.splitlines()):
        column = len(raw) - len(raw.lstrip()) + 1
        text = raw.strip()
        line = yaml_run_line + offset
        if not text or text.startswith("#"):
            continue
        span = _span(line, text, column)

        def emit(kind: str, op_text: str, op_line: int, **kwargs) -> ShellOp:
            return _emit(ops, kind, op_text, op_line, column=column, **kwargs)

        if text == "set -eu":
            emit("strict-mode", text, line)
            continue
        if text.startswith("mkdir -p "):
            try:
                words = shlex.split(text)
            except ValueError as exc:
                raise UnsupportedShell(f"invalid mkdir quoting: {exc}", span) from exc
            if len(words) >= 3 and words[:2] == ["mkdir", "-p"] and all(_literal_repo_path(x) for x in words[2:]):
                emit("mkdir", text, line, path="|".join(words[2:]))
                continue
        if text.startswith("cp "):
            try:
                words = shlex.split(text)
            except ValueError as exc:
                raise UnsupportedShell(f"invalid cp quoting: {exc}", span) from exc
            if len(words) == 3 and words[0] == "cp" and all(_literal_repo_path(x) for x in words[1:]):
                emit("copy", text, line, path=words[1] + "->" + words[2])
                continue
        if text.startswith("grep -Eq "):
            try:
                words = shlex.split(text)
            except ValueError as exc:
                raise UnsupportedShell(f"invalid grep quoting: {exc}", span) from exc
            if len(words) == 4 and words[:2] == ["grep", "-Eq"] and _literal_repo_path(words[3]):
                emit("grep-assert", text, line, path=words[3], value=words[2])
                continue

        match = _ASSIGN_TR.fullmatch(text)
        if match:
            target, strip_chars, source_var, path_suffix = match.groups()
            literal_chars=strip_chars.replace(r'\r','').replace(r'\n','')
            if any(c in literal_chars for c in ('\\','-','[',']')):
                raise UnsupportedShell('tr -d ranges and unregistered escapes are outside the literal deletion subset',span)
            op = emit("read-trim", text, line, reads=(source_var,), writes=(target,),
                      path=path_suffix, value=strip_chars)
            reads.append(op)
            edges.append((source_var, target, op.span))
            continue
        match = _ASSIGN_SHA.fullmatch(text)
        if match:
            target, source_var = match.groups()
            op = emit("digest", text, line, reads=(source_var,), writes=(target,))
            reads.append(op)
            edges.append((source_var, target, op.span))
            continue
        match = _IF_EQ.fullmatch(text)
        if match:
            variable, literal = match.groups()
            op = emit("if-eq", text, line, reads=(variable,), value=literal)
            stack.append(("if", variable, op.span))
            controls.append((variable, op.span))
            continue
        match = _IF_VAR.fullmatch(text)
        if match:
            left, right = match.groups()
            op = emit("if-eq-var", text, line, reads=(left, right))
            stack.append(("if", left + "=" + right, op.span))
            controls.extend(((left, op.span), (right, op.span)))
            continue
        if text == "else":
            if not stack or stack[-1][0] != "if" or "|else" in stack[-1][1]:
                raise UnsupportedShell("unmatched else", span)
            kind, value, start = stack[-1]
            stack[-1] = (kind, value + "|else", start)
            emit("else", text, line)
            continue
        if text == "fi":
            if not stack or stack[-1][0] != "if" or "|else" not in stack[-1][1]:
                raise UnsupportedShell("unsupported or unbalanced if block", span)
            stack.pop()
            emit("fi", text, line)
            continue
        match = _CASE.fullmatch(text)
        if match:
            variable = match.group(1)
            emit("case", text, line, reads=(variable,))
            stack.append(("case", variable, span))
            controls.append((variable, span))
            continue
        match = _CASE_PATTERN.fullmatch(text)
        if match and stack and stack[-1][0] == "case":
            pattern = match.group(1)
            if not re.fullmatch(r"(?:\*\[[^\]]+\]\*|''|[A-Za-z0-9_./-]+)(?:\|(?:\*\[[^\]]+\]\*|''|[A-Za-z0-9_./-]+))*", pattern):
                raise UnsupportedShell("unsupported case pattern", span)
            emit("case-pattern", text, line, value=pattern)
            continue
        if text == ";;" and stack and stack[-1][0] == "case":
            emit("case-next", text, line)
            continue
        if text == "esac":
            if not stack or stack[-1][0] != "case":
                raise UnsupportedShell("unmatched esac", span)
            stack.pop()
            emit("esac", text, line)
            continue
        if text in {"exit 1", "exit 0"}:
            emit("exit", text, line, value=text[-1])
            continue
        match = _TEST_LEN.fullmatch(text)
        if match:
            variable, length = match.groups()
            op = emit("test-length", text, line, reads=(variable,), value=length)
            controls.append((variable, op.span))
            continue
        match = _TEST_FILE.fullmatch(text)
        if match:
            variable, suffix = match.groups()
            op = emit("test-file", text, line, reads=(variable,), value=suffix)
            controls.append((variable, op.span))
            continue
        match = _OUTPUT.fullmatch(text)
        if match:
            key, variable = match.groups()
            op = emit("github-output", text, line, reads=(variable,), writes=("output:" + key,))
            outputs.append(op)
            edges.append((variable, "output:" + key, op.span))
            continue
        if text == "{" or text == "}":
            emit("block", text, line)
            continue
        if _REDIR.fullmatch(text):
            emit("summary-redirection", text, line)
            continue
        match = _ECHO.fullmatch(text)
        if match:
            body = match.group(1) or ""
            checked = body.removesuffix(" >&2").rstrip()
            if ("`" in checked or ";" in checked or "$(" in checked or
                    not _safe_echo_body(checked)):
                raise UnsupportedShell("unsupported echo expansion or redirection", span)
            refs = _refs(body)
            op = emit("echo", text, line, reads=refs, value=body)
            marker = re.search(r"(?:dummy_publish_authority_reached|dummy_repository_update_reached)=([^'\"]+)", body)
            if marker:
                markers.append(op)
                # A data-dependent marker (for example
                # dummy_publish_authority_reached=$authority_reached) and an
                # unconditional literal-true marker are both sink sites.  A
                # literal false marker remains a marker only.
                if "$" in marker.group(1) or marker.group(1).strip() == "true":
                    sinks.append(op)
            elif "dummy_publish_authority_reached" in body or "dummy_repository_update_reached" in body:
                markers.append(op)
                if "true" in body:
                    sinks.append(op)
            continue
        match = _PRINTF.fullmatch(text)
        if match:
            body = match.group(1)
            if "$(" in body or "`" in body or ";" in body or ">" in body:
                raise UnsupportedShell("unsupported printf expansion or side effect", span)
            if not re.fullmatch(r"'[^']*'(?:\s+\"\$[A-Za-z_][A-Za-z0-9_]*\")*", body):
                raise UnsupportedShell("printf arguments are outside the literal/quoted-variable subset", span)
            refs = _refs(body)
            op = emit("printf", text, line, reads=refs, value=body)
            if "GITHUB_OUTPUT" in text:
                raise UnsupportedShell("unsupported GITHUB_OUTPUT syntax", span)
            continue
        # A variable assignment is accepted only as a literal and remains a
        # distinct typed operation; command substitutions have their own rules.
        assign = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=(true|false)", text)
        if assign:
            target, value = assign.groups()
            emit("literal-assign", text, line, writes=(target,), value=value)
            continue
        assign = _ASSIGN_LITERAL.fullmatch(text)
        if assign:
            target = assign.group(1) or assign.group(3)
            value = assign.group(2) if assign.group(1) else assign.group(4)
            if assign.group(3) and any(c in value for c in ('$','`')):
                raise UnsupportedShell('expanded double-quoted assignment is outside the literal subset',span)
            emit("literal-assign", text, line, writes=(target,), value=value)
            continue
        raise UnsupportedShell(f"unsupported shell syntax: {text}", span)

    if stack:
        kind, _, start = stack[-1]
        raise UnsupportedShell(f"unclosed {kind} block", start)
    if not ops:
        raise UnsupportedShell("empty shell body is outside the subset", _span(yaml_run_line, ""))
    return ShellProgram(ops, edges, reads, outputs, markers, sinks, controls,
                        _conditions(ops))


def _conditions(operations: list[ShellOp]) -> list[ShellCondition]:
    result: list[ShellCondition] = []
    stack: list[dict] = []
    for op in operations:
        if op.kind in {"if-eq", "if-eq-var"}:
            stack.append({"op": op, "branch": "then", "then": {}, "else": {}})
        elif op.kind == "else" and stack:
            stack[-1]["branch"] = "else"
        elif op.kind == "literal-assign" and stack:
            target = op.writes[0]
            stack[-1][stack[-1]["branch"]][target] = str(op.value)
        elif op.kind == "fi" and stack:
            frame = stack.pop()
            start = frame["op"]
            result.append(ShellCondition(start.kind, start.reads, start.value,
                dict(frame["then"]), dict(frame["else"]), start.span))
    return result


def _literal_repo_path(value: str) -> bool:
    return bool(value and not value.startswith("/") and
                not any(part in {".", ".."} for part in value.split("/")) and
                not any(part in value for part in ("${", "$(", "`", "*", "?", ":")))


def _safe_echo_body(body: str) -> bool:
    if not body:
        return True
    if body.startswith("'") and body.endswith("'"):
        return "'" not in body[1:-1] and "$" not in body[1:-1] and "`" not in body[1:-1]
    if body.startswith('"') and body.endswith('"'):
        inner = body[1:-1]
        if "$(" in inner or "`" in inner:
            return False
        # Shell variables and GitHub expression strings are data interpolation
        # only inside quotes.  Other dollar forms are deliberately rejected.
        remainder = re.sub(r"\$[A-Za-z_][A-Za-z0-9_]*", "", inner)
        remainder = re.sub(r"\$\{\{[^{}]*}}", "", remainder)
        return "$" not in remainder
    return bool(re.fullmatch(r"[A-Za-z0-9_./:= -]+", body))


def sha256_literal(value: str) -> str:
    """Return the digest relation label; callers run this only in the tool container."""
    import hashlib
    return hashlib.sha256(value.encode()).hexdigest()
