#!/usr/bin/env python3
# YAMLの値と行・列の位置を対応付ける．
# 参照BFSが使うworkflow表現もここで作る．
# このファイルのExprは参照BFS向けの限定式であり，本体の式変換はexpressions.pyが担当する．
"""元のYAMLの位置を保って読取り，参照BFS用の型付き表現も作る．

load_workflowはYAML fileを入力として，Workflow/Job/Step/Exprと診断を返す．
値を読むyaml.loadと位置を読むyaml.composeを併用し，Spanを1始まりの行・列へ変換する．
compilerは主に位置helperとWorkflowErrorを使い，本体の式解析にはexpressionsを使う．
このfileのExprとparse_expressionは独立BFS向けの別の限定表現である．
ここでのopaque-run等の診断を，共通backendのShell対応範囲と混同しない．
"""
from __future__ import annotations
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import yaml
from .workflow_loader import WorkflowLoader
# noqa: E402
GH_EXPR_PREFIX = chr(36) + "{{"

# 元のYAMLの範囲．
# 出力する行・列は1始まりにそろえる．
@dataclass(frozen=True)
class Span:
    """元のYAMLの位置範囲を保存するimmutableな値．"""
    file: str
    line: int
    column: int
    end_line: int
    end_column: int

    def json(self) -> dict[str, Any]:
        """位置をJSON化できる辞書へ変換する．"""
        # Python属性をsource mapのキー名へ写し，SpanをそのままJSON化できる辞書へする．
        return {
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "endLine": self.end_line,
            "endColumn": self.end_column
        }

@dataclass
class Diagnostic:
    """入力の不正や未対応を元位置と一緒に保存する値．"""
    status: str
    code: str
    message: str
    span: Span | None

    def json(self) -> dict[str, Any]:
        """診断をJSON化できる辞書へ変換する．"""
        # 診断にsourceの辞書を付け，位置がない場合はNoneとして保存する．
        return {
            "status": self.status,
            "code": self.code,
            "message": self.message,
            "source": self.span.json() if self.span else None
        }

@dataclass(frozen=True)
class Expr:
    """参照BFS用の限定式を表す構文木．"""
    kind: str
    value: Any = None
    left: Expr | None = None
    right: Expr | None = None

@dataclass
class Step:
    """参照BFSが使う，元位置付きstep表現．"""
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
    """参照BFS用のjob構造と権限宣言の有無を保持する．"""
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
    """参照BFS用のworkflow表現と入力hashを保持する．"""
    path: str
    name: str
    events: dict[str, Any]
    permissions: dict[str, str] | None
    permissions_declared: bool
    jobs: list[Job]
    sha256: str
    diagnostics: list[Diagnostic] = field(default_factory=list)

class WorkflowError(ValueError):
    """workflow構造や変換入力の不正を呼出し側へ伝える例外．"""
    pass

class ExpressionError(ValueError):
    """参照BFS用parserの対応外式を通知する例外．"""
    pass
TOKEN_RE = re.compile(
    '(?P<space>\\s+)|(?P<string>\'(?:\'\'|[^\'])*\'|"(?:\\\\.|[^"])*")|\n    (?P<op>==|!=|&&|\\|\\||!)|(?P<lp>\\()|(?P<rp>\\))|\n    (?P<ident>[A-Za-z_][A-Za-z0-9_.-]*)|(?P<other>.)',
    re.VERBOSE
)

# 参照BFS向けの限定式parser．
# 変換器本体の式parserとは対応範囲が異なる．
def parse_expression(source: str | None) -> Expr | None:
    """参照用のliteral・参照・status・論理比較式を解析する．"""
    # ifが未指定または空なら，参照BFS用の式を作らずNoneを返す．
    if source is None or not str(source).strip():
        return None
    text = str(source).strip()
    if text.startswith(GH_EXPR_PREFIX) and text.endswith("}}"):
        text = text[3:-2].strip()
    tokens: list[tuple[str, str]] = []
    for match in TOKEN_RE.finditer(text):
        # 名前付き正規表現で一致した種類を取り出し，空白・演算子・文字列等を分ける．
        kind = match.lastgroup or ""
        # tokenが元の式でどの文字列だったかを保持する．
        value = match.group()
        if kind == "space":
            continue
        # 未知の1文字も黙って飛ばさず，限定parserの対応外として例外にする．
        if kind == "other":
            raise ExpressionError(f'unsupported token {value!r}')
        # token種別と元の文字列を組にし，後でliteralと参照を区別できるようにする．
        tokens.append((kind, value))
    tokens.append(("end", ""))
    pos = 0

    def peek():
        """次のtokenを進めずに見る．"""
        return tokens[pos]

    def take(expected=None):
        """tokenを取り，必要なら種別を検査する．"""
        # 外側のparserが持つ位置を共有し，内部関数の呼出しごとに0へ戻らないようにする．
        nonlocal pos
        result = tokens[pos]
        if expected and result[0] != expected:
            raise ExpressionError(f'expected {expected}, got {result[1]!r}')
        pos += 1
        return result

    def primary():
        """参照parserの単項・literal・括弧・関数を読む．"""
        # まだ位置を進めずに種別と文字列を読み，次の構文分岐を選ぶ．
        kind, value = peek()
        # !の後の基本式を再帰的に読み，参照用ASTのnotへ対応付ける．
        if kind == "op" and value == "!":
            take()
            return Expr("not", left=primary())
        # 括弧内を優先順位の低い位置から読み，閉じ括弧までを1つの式にする．
        if kind == "lp":
            take()
            result = parse_bp(0)
            take("rp")
            return result
        # 引用符付きliteralを参照名とは分け，文字列nodeとして保存する．
        if kind == "string":
            take()
            if value.startswith("'"):
                return Expr("string", value[1:-1].replace("''", "'"))
            try:
                return Expr("string", json.loads(value))
            except json.JSONDecodeError as exc:
                raise ExpressionError("unsupported quoted string escape") from exc
        if kind != "ident":
            raise ExpressionError(f'expected expression, got {value!r}')
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
        """演算子の優先順位を使って参照用の二項式をまとめる．"""
        left = primary()
        while peek()[0] == "op" and peek()[1] in precedence:
            op = peek()[1]
            level = precedence[op]
            if level < minimum:
                break
            take()
            # 右辺を高い優先順位で読み，既存の左辺と合わせて二項演算nodeを作る．
            left = Expr(op, left=left, right=parse_bp(level + 1))
        return left
    result = parse_bp(0)
    if peek()[0] != "end":
        raise ExpressionError(f'unexpected token {peek()[1]!r}')
    return result

def _span(node: yaml.Node | None, file: str) -> Span:
    """YAML nodeの開始・終了markを1始まりのSpanへ変換する．"""
    # 対応nodeがない場合は，位置不明の代替Spanとして先頭行・先頭列を返す．
    if node is None:
        return Span(file, 1, 1, 1, 1)
    # PyYAMLの0始まりの行・列へ1を足し，エディタで読む1始まりの範囲へ変換する．
    return Span(
        file,
        node.start_mark.line + 1,
        node.start_mark.column + 1,
        node.end_mark.line + 1,
        node.end_mark.column + 1
    )

def _member(node: yaml.Node | None, key: str) -> yaml.Node | None:
    """YAMLのmapping nodeから，指定keyの値nodeを探す．"""
    # mapping以外のnodeにキー検索は適用できないため，Noneを返す．
    if not isinstance(node, yaml.MappingNode):
        return None
    # キーnodeの文字列が一致する値nodeを最初の1個だけ取り出し，見つからなければNoneとする．
    return next((value for key_node, value in node.value if key_node.value == key), None)

def _permission_map(value: Any, span: Span, diagnostics: list[Diagnostic]) -> dict[str, str] | None:
    """参照表現のpermissionsを辞書へそろえる．"""
    if value is None:
        return None
    if isinstance(value, str) and value in {"read-all", "write-all"}:
        # read-all/write-allの-allを外し，全scopeに適用する参照IRの*キーへ格納する．
        return {"*": value[:-4]}
    if not isinstance(value, dict):
        diagnostics.append(Diagnostic(
            "invalid",
            "permissions-type",
            "permissions must be a mapping, read-all, or write-all",
            span
        ))
        return {}
    result = {}
    # permissionsの各scopeとlevelを走査し，対応する文字列かを検査する．
    for scope, access in value.items():
        if not isinstance(scope, str) or access not in {"read", "write", "none"}:
            diagnostics.append(Diagnostic(
                "invalid",
                "permission-value",
                f'unsupported permission {scope!r}: {access!r}',
                span
            ))
        else:
            result[scope] = access
    return result

# 値と構文nodeの両方を読み，workflow/job/stepへ元の位置を付ける．
def load_workflow(path: Path, root: Path | None=None) -> Workflow:
    """YAMLの値・位置・限定条件を読み，参照用Workflowを作る．"""
    # UTF-8として元YAMLを読み，値・位置・入力hashを同じ原文から求める．
    source = path.read_text(encoding="utf-8")
    try:
        # 値はPythonのdict/listへ，treeは行・列markを持つYAML nodeへ読む．
        # 同じ原文から両方を対応付ける．
        data = yaml.load(source, Loader=WorkflowLoader)
        tree = yaml.compose(source, Loader=WorkflowLoader)
    except Exception as exc:
        raise WorkflowError(f'{path}: {exc}') from exc
    if not isinstance(data, dict) or not isinstance(data.get("jobs"), dict):
        raise WorkflowError(f'{path}: expected a workflow mapping with jobs')
    # root指定時だけ相対名にし，source情報で使う表記を選ぶ．
    filename = path.relative_to(root).as_posix() if root else path.as_posix()
    diagnostics: list[Diagnostic] = []
    # 専用loaderが文字列として保持したonキーから，起動イベントの指定を取り出す．
    events = data.get("on")
    # on:push，on:[push,...]，onのmappingを，後段が同じ辞書形式で扱えるようにする．
    if isinstance(events, str):
        events = {events: None}
    elif isinstance(events, list):
        events = {event: None for event in events}
    if not isinstance(events, dict):
        diagnostics.append(Diagnostic(
            "invalid",
            "trigger-type",
            "on must be a mapping, event name, or event list",
            _span(_member(tree, "on"), filename)
        ))
        events = {}
    # workflow-level permissionsを参照IRの辞書へそろえ，不正指定には位置付き診断を残す．
    permissions = _permission_map(
        data.get("permissions"),
        _span(_member(tree, "permissions"), filename),
        diagnostics
    )
    jobs_node = _member(tree, "jobs")
    jobs: list[Job] = []
    # jobsのKeyをJob名，Valueを設定として読み，型付きJobへ組み立てる．
    for job_id, raw_job in data["jobs"].items():
        # 値とは別に同名Jobの位置nodeを取得し，診断とIRへ元位置を付ける．
        job_node = _member(jobs_node, str(job_id))
        if not isinstance(raw_job, dict):
            diagnostics.append(Diagnostic(
                "invalid",
                "job-type",
                f'job {job_id!r} must be a mapping',
                _span(job_node, filename)
            ))
            continue
        # needs省略時は空リストにし，単一文字列は次の分岐でリストへそろえる．
        needs = raw_job.get("needs", [])
        if isinstance(needs, str):
            needs = [needs]
        if not isinstance(needs, list) or not all((isinstance(x, str) for x in needs)):
            diagnostics.append(Diagnostic(
                "invalid",
                "needs-type",
                f'job {job_id!r} needs must be a string or list',
                _span(_member(job_node, "needs"), filename)
            ))
            needs = []
        for unsupported in ("uses", "strategy", "container", "services", "environment"):
            if unsupported in raw_job:
                diagnostics.append(Diagnostic(
                    "unsupported",
                    "job-" + unsupported,
                    f'job-level {unsupported} is outside this prototype',
                    _span(_member(job_node, unsupported), filename)
                ))
        # job-level ifの元文字列を保持し，解析済み式と両方をIRへ入れる．
        raw_if = raw_job.get("if")
        try:
            # if指定がある時だけ参照用ASTを作り，省略を文字列'None'として評価しない．
            expr = parse_expression(str(raw_if)) if raw_if is not None else None
        except ExpressionError as exc:
            # 読めないifはunsupported nodeとして残し，条件をTRUEへ置き換えたことにはしない．
            expr = Expr("unsupported", str(raw_if))
            diagnostics.append(Diagnostic(
                "unsupported",
                "job-if-expression",
                str(exc),
                _span(_member(job_node, "if"), filename)
            ))
        # job-levelの権限指定を位置付きで読み，workflowの指定と分けて保存する．
        job_permissions = _permission_map(
            raw_job.get("permissions"),
            _span(_member(job_node, "permissions"), filename),
            diagnostics
        )
        # 実行順を持つstepsリストを取り出し，省略時は空リストにする．
        raw_steps = raw_job.get("steps", [])
        steps_node = _member(job_node, "steps")
        if not isinstance(raw_steps, list):
            diagnostics.append(Diagnostic(
                "invalid",
                "steps-type",
                f'job {job_id!r} steps must be a list',
                _span(steps_node, filename)
            ))
            raw_steps = []
        steps: list[Step] = []
        # stepの値を0始まりの添字と組にし，同じ順番のYAML nodeへ対応付ける．
        for index, raw_step in enumerate(raw_steps):
            # stepsがsequence nodeで範囲内なら同じ添字を使い，位置がなければNoneを使う．
            step_node = steps_node.value[index] if isinstance(steps_node, yaml.SequenceNode) and index < len(steps_node.value) else None
            span = _span(step_node, filename)
            if not isinstance(raw_step, dict):
                diagnostics.append(Diagnostic("invalid", "step-type", "step must be a mapping", span))
                continue
            # どちらも真，またはどちらも偽ならstepの処理が一意に決まらないので診断する．
            has_uses, has_run = ("uses" in raw_step, "run" in raw_step)
            if has_uses == has_run:
                diagnostics.append(Diagnostic(
                    "invalid",
                    "step-operation",
                    "each step must contain exactly one of uses or run",
                    span
                ))
            uses = raw_step.get("uses") if has_uses else None
            run = raw_step.get("run") if has_run else None
            if uses is not None and (not isinstance(uses, str)):
                diagnostics.append(Diagnostic(
                    "invalid",
                    "uses-type",
                    "uses must be a string",
                    _span(_member(step_node, "uses"), filename)
                ))
                uses = None
            # Action入力のwithを辞書として読み，IRのinputsへ格納する．
            inputs = raw_step.get("with", {})
            if not isinstance(inputs, dict):
                diagnostics.append(Diagnostic(
                    "invalid",
                    "with-type",
                    "with must be a mapping",
                    _span(_member(step_node, "with"), filename)
                ))
                inputs = {}
            # step-level ifの原文を取得し，job-level ifと混ぜず個別に解析する．
            raw_step_if = raw_step.get("if")
            try:
                # step-level ifを参照用の限定ASTへ変換し，省略時はNoneを保持する．
                step_expr = parse_expression(str(raw_step_if)) if raw_step_if is not None else None
            except ExpressionError as exc:
                step_expr = Expr("unsupported", str(raw_step_if))
                diagnostics.append(Diagnostic(
                    "unsupported",
                    "step-if-expression",
                    str(exc),
                    _span(_member(step_node, "if"), filename)
                ))
            # この参照表現だけではrunの効果を解析しない．
            # 共通compilerのShell契約とは役割・対応範囲が異なる．
            if has_run:
                diagnostics.append(Diagnostic(
                    "unsupported",
                    "opaque-run",
                    "run body is retained but its dataflow/effects are not analyzed",
                    _span(_member(step_node, "run"), filename)
                ))
            for feature in ("continue-on-error", "timeout-minutes", "shell", "working-directory"):
                if feature in raw_step:
                    diagnostics.append(Diagnostic(
                        "unsupported",
                        "step-" + feature,
                        f'step-level {feature} is outside this prototype',
                        _span(_member(step_node, feature), filename)
                    ))
            # step順・id・uses/run・with・if・env・位置をまとめ，値と原文の両方を参照IRへ残す．
            steps.append(Step(
                index,
                str(raw_step.get("id", "")),
                uses if isinstance(uses, str) else None,
                str(run) if run is not None else None,
                dict(inputs),
                str(raw_step_if) if raw_step_if is not None else None,
                step_expr,
                span,
                _span(_member(step_node, "run"), filename) if has_run else None,
                dict(raw_step.get("env", {})) if isinstance(raw_step.get("env", {}), dict) else {},
                str(raw_step.get("name", ""))
            ))
        # Job設定・needs・権限・Step列・sourceを型付きJobへまとめる．
        jobs.append(Job(
            str(job_id),
            list(needs),
            str(raw_if) if raw_if is not None else None,
            expr,
            job_permissions,
            "permissions" in raw_job,
            steps,
            _span(job_node, filename)
        ))
    # Job名の集合を作り，needsが実在する名前かを高速に検査する．
    job_ids = {job.job_id for job in jobs}
    for job in jobs:
        for dependency in job.needs:
            # 不明なneeds名は無視せず，元Jobの位置付きで入力不正として診断する．
            if dependency not in job_ids:
                diagnostics.append(Diagnostic(
                    "invalid",
                    "needs-missing",
                    f'unknown needs job {dependency!r}',
                    job.span
                ))
    # すべてのJobが読めた後，needsの循環を検査する．
    _check_cycles(jobs, diagnostics)
    for event, cfg in events.items():
        if event not in {"pull_request", "push", "workflow_run"}:
            diagnostics.append(Diagnostic(
                "unsupported",
                "trigger-event",
                f'event {event!r} is outside the prototype subset',
                _span(_member(_member(tree, "on"), str(event)), filename)
            ))
        if cfg is not None and (not isinstance(cfg, dict)):
            diagnostics.append(Diagnostic(
                "invalid",
                "trigger-config",
                f'trigger {event!r} must be a mapping',
                _span(_member(_member(tree, "on"), str(event)), filename)
            ))
    # イベント・権限・Job列・元YAMLのSHA-256・診断を参照用Workflowとして返す．
    return Workflow(
        filename,
        str(data.get("name", filename)),
        events,
        permissions,
        "permissions" in data,
        jobs,
        hashlib.sha256(source.encode()).hexdigest(),
        diagnostics
    )

# needsを深さ優先でたどり，循環があれば診断する．
def _check_cycles(jobs: list[Job], diagnostics: list[Diagnostic]) -> None:
    """needsの有向graphを深さ優先でたどり循環を診断する．"""
    # Job名から依存先名のリストへの有向辺を作る．
    edges = {j.job_id: j.needs for j in jobs}
    # visitingは今の再帰経路，visitedは走査済み．
    # 同じjobを別経路で見たことと循環を区別する．
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(job_id: str):
        """訪問中と訪問済みを区別してneedsを再帰走査する．"""
        # 現在の再帰経路に戻った時だけ循環とし，既に探索しただけのJobとは区別する．
        if job_id in visiting:
            job = next((j for j in jobs if j.job_id == job_id))
            diagnostics.append(Diagnostic(
                "invalid",
                "needs-cycle",
                f'needs cycle includes {job_id!r}',
                job.span
            ))
            return
        # 探索済みで循環を生じていないJobは，再帰走査を繰り返さない．
        if job_id in visited:
            return
        # 現在の再帰経路へJobを追加してから，そのneeds先を調べる．
        visiting.add(job_id)
        for dep in edges.get(job_id, []):
            if dep in edges:
                visit(dep)
        # 子の探索が終わったため，現在の経路からこのJobを外す．
        visiting.remove(job_id)
        # 探索済み集合へ登録し，別経路から来ても同じ下位探索を繰り返さないようにする．
        visited.add(job_id)
    for job_id in edges:
        visit(job_id)

def _walk(expr: Expr | None):
    """参照用Exprを親から左右の子へ列挙するgenerator．"""
    if expr is None:
        return
    # 参照ASTの現在nodeを1個返し，続けて左右の子nodeへ再帰する．
    yield expr
    if expr.left:
        yield from _walk(expr.left)
    if expr.right:
        yield from _walk(expr.right)

# stepの位置からrunのscalar nodeを探し，Shell本文の実際の開始行を返す．
def run_content_line(tree, source):
    """source位置に一致するstepを構文木から探し，run本文の開始行を返す．"""
    visited = set()

    def visit(node):
        """構文nodeを再帰走査し，sourceに一致するrunの開始行を探す．"""
        # YAML alias等で同じnodeへ戻る場合は再探索せず，無限再帰を防ぐ．
        if id(node) in visited:
            return None
        # nodeの内容ではなくPython上の同一objectを記録し，共有nodeの再訪を検出する．
        visited.add(id(node))
        if isinstance(node, yaml.MappingNode):
            # mapping nodeがrun本文を持つstepかを調べ，sourceの開始位置と一致するか確認する．
            run = _member(node, 'run')
            if run is not None and node.start_mark.line + 1 == source['line'] and (node.start_mark.column + 1 == source['column']):
                # PyYAML markは0始まり．
                # block scalarは次行から本文が始まるため，inlineと加算を変える．
                return run.start_mark.line + (2 if run.style in {'|', '>'} else 1)
            for key, value in node.value:
                # 子nodeへ潜って同じsource位置を探し，見つかった開始行だけを上位へ返す．
                found = visit(value)
                if found is not None:
                    return found
        elif isinstance(node, yaml.SequenceNode):
            for value in node.value:
                # 子nodeへ潜って同じsource位置を探し，見つかった開始行だけを上位へ返す．
                found = visit(value)
                if found is not None:
                    return found
        return None
    # YAML全体の構文木から，指定stepのrun本文の開始行を探す．
    line = visit(tree)
    # sourceに対応するrunが見つからなければ，推測の行番号を返さずエラーにする．
    if line is None:
        raise WorkflowError('cannot locate run source span: ' + str(source))
    return line
