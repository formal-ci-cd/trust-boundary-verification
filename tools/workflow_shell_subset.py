# Shellを実行せず，対応する文をファイル操作・分岐・output等の操作列へ変換する．
# 行番号を保持し，後で反例を元のYAMLへ戻せるようにする．
# 未認識の行があればscript全体を拒否する．
# 読めた部分だけで安全と判断しないためである．
"""限定Shellの構文と位置情報を，操作列のIRへ変換する．

入力はYAMLのrun本文と本文の開始行，出力はShellProgramである．
各行を対応する正規表現へ照合し，ShellOp，変数間のedge，分岐の境界を保存する．
未知の非空行や閉じていない分岐はUnsupportedShellにし，scriptの一部だけを採用しない．
Shellを実行せず，fileや環境変数も変更しない．操作の意味はshell_loweringが遷移へ下げる．
構文として読めることと，後段で全効果を扱えることは別なので，後段の診断も必要である．
"""
from __future__ import annotations
from dataclasses import dataclass, field
import re
import shlex
from typing import Iterable

@dataclass(frozen=True)
class ShellSpan:
    """Shellの文に対応する元YAML内の範囲．"""
    line: int
    column: int
    end_line: int
    end_column: int

# 1つの操作と，読取り元・書込み先・YAML内の位置を保持する．
# 実行結果ではない．
@dataclass(frozen=True)
class ShellOp:
    """型を付けた1文の操作IR．"""
    kind: str
    text: str
    span: ShellSpan
    # この操作が読む変数名をtupleで保持し，後段で値と依存をたどる．
    reads: tuple[str, ...] = ()
    # この操作が設定する変数・output名を保持し，読取り元とのedgeを作る．
    writes: tuple[str, ...] = ()
    path: str | None = None
    value: str | None = None

@dataclass(frozen=True)
class ShellCondition:
    """if分岐でのliteral代入をまとめた参照情報．"""
    kind: str
    # この操作が読む変数名をtupleで保持し，後段で値と依存をたどる．
    reads: tuple[str, ...]
    literal: str | None
    then_assignments: dict[str, str]
    else_assignments: dict[str, str]
    span: ShellSpan

# script全体の操作列と，Artifact読取り・output等への索引を保持する．
@dataclass
class ShellProgram:
    """script全体の操作列と依存・read・output等の索引．"""
    operations: list[ShellOp]
    variable_edges: list[tuple[str, str, ShellSpan]] = field(default_factory=list)
    artifact_reads: list[ShellOp] = field(default_factory=list)
    output_writes: list[ShellOp] = field(default_factory=list)
    # 研究markerを含む操作を集め，通常ログと区別する．
    markers: list[ShellOp] = field(default_factory=list)
    sink_sites: list[ShellOp] = field(default_factory=list)
    control_blocks: list[tuple[str, ShellSpan]] = field(default_factory=list)
    conditions: list[ShellCondition] = field(default_factory=list)

class UnsupportedShell(ValueError):
    """対応外の文と位置をまとめて通知する例外．"""

    def __init__(self, message: str, span: ShellSpan):
        """messageをValueErrorへ渡し，位置を保持する．"""
        super().__init__(message)
        # エラーにも元YAML位置を保持し，unsupportedの行を呼出し側から示せるようにする．
        self.span = span
_VAR = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
_ASSIGN_TR = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=\$\(tr -d '([^']*)' < \"\$([A-Za-z_][A-Za-z0-9_]*)([^\"]*)\"\)$")
_ASSIGN_SHA = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=\$\(sha256sum \"\$([A-Za-z_][A-Za-z0-9_]*)\" \| cut -d ' ' -f 1\)$")
_ASSIGN_LITERAL = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)='([^']*)'$|^([A-Za-z_][A-Za-z0-9_]*)=\"([^\"]*)\"$")
_IF_EQ = re.compile(r'^if \[ "\$([A-Za-z_][A-Za-z0-9_]*)" = "([^"$\n]*)" \]; then$')
_IF_VAR = re.compile(r'^if \[ "\$([A-Za-z_][A-Za-z0-9_]*)" = "\$([A-Za-z_][A-Za-z0-9_]*)" \]; then$')
_CASE = re.compile(r'^case "\$([A-Za-z_][A-Za-z0-9_]*)" in$')
_CASE_PATTERN = re.compile(r"^(.+)\)$")
_OUTPUT = re.compile(r"^printf '([A-Za-z_][A-Za-z0-9_]*)=%s\\n' \"\$([A-Za-z_][A-Za-z0-9_]*)\" >> \"\$GITHUB_OUTPUT\"$")
_TEST_LEN = re.compile(r'^test "\$\{#([A-Za-z_][A-Za-z0-9_]*)\}" -eq ([0-9]+)$')
_TEST_FILE = re.compile(r'^test -f "\$([A-Za-z_][A-Za-z0-9_]*)([^\"]*)"$')
_ECHO = re.compile(r"^echo(?: (.*))?$")
_PRINTF = re.compile(r"^printf (.+)$")
_REDIR = re.compile(r'^\}\s*(?:>>|\| tee -a) "\$GITHUB_STEP_SUMMARY"$')

def _span(line: int, text: str, column: int=1) -> ShellSpan:
    """1文の開始行・列と長さから位置範囲を作る．"""
    return ShellSpan(line, column, line, column + len(text))

def _refs(text: str) -> tuple[str, ...]:
    """文字列から単純な$NAME参照を順序付きで収集する．"""
    # $NAMEの出現順を保って重複を除き，同じ変数への依存edgeを重複させない．
    return tuple(dict.fromkeys(_VAR.findall(text)))

def _emit(
    ops: list[ShellOp],
    kind: str,
    text: str,
    line: int,
    reads: Iterable[str]=(),
    writes: Iterable[str]=(),
    path: str | None=None,
    value: str | None=None,
    column: int=1
) -> ShellOp:
    """ShellOpを作って操作listへ追加する．"""
    # 操作種別・原文・元位置・読み書き・path・値を型付きIRへまとめ，Shellは実行しない．
    op = ShellOp(kind, text, _span(line, text, column), tuple(reads), tuple(writes), path, value)
    # 全操作の列へ追加し，元scriptと同じ順序を保持する．
    ops.append(op)
    return op

# 対応する文だけでscript全体を読めるか確認する．
# 未対応文は行位置付きで通知する．
def parse_shell(source: str, yaml_run_line: int=1) -> ShellProgram:
    """対応する文だけでscript全体を操作IRへ変換する．"""
    # 型ヒント付きlistを用途ごとに作る．
    # opsは全操作，ほかは同じ操作への用途別索引である．
    ops: list[ShellOp] = []
    # 変数間の依存を，読取元・書込先・位置の組で保持するリストを用意する．
    edges: list[tuple[str, str, ShellSpan]] = []
    # Artifact内容を読む操作への索引を用意し，全操作列の順序とは別に参照できるようにする．
    reads: list[ShellOp] = []
    # output書込み操作への索引を用意し，後のmetadata依存確認に使う．
    outputs: list[ShellOp] = []
    # 研究markerを含む操作を集め，通常ログと区別する．
    markers: list[ShellOp] = []
    # 模擬sink地点に該当する操作を記録する．
    sinks: list[ShellOp] = []
    # 分岐を決める変数と位置を記録し，control dependencyの追跡へ渡す．
    controls: list[tuple[str, ShellSpan]] = []
    # if/caseの対応関係を積み，elseや終端が不正な位置にないか検査する．
    stack: list[tuple[str, str, ShellSpan]] = []
    # 空行もoffsetには含む．
    # 本文開始行を加算し，元YAMLでの位置を保つ．
    for offset, raw in enumerate(source.splitlines()):
        # 行全体の長さから左空白を除いた長さを引き，元行の開始列を1始まりで求める．
        column = len(raw) - len(raw.lstrip()) + 1
        # 字下げを除いた本文を認識対象にし，列情報は別変数へ保持する．
        text = raw.strip()
        # 本文内の0始まりoffsetをYAML上の開始行へ加え，空行分も含めた行位置を復元する．
        line = yaml_run_line + offset
        if not text or text.startswith("#"):
            continue
        # この行の認識失敗でも位置を出せるよう，操作生成前に範囲を作る．
        span = _span(line, text, column)

        def emit(kind: str, op_text: str, op_line: int, **kwargs) -> ShellOp:
            """現在行のcolumnを付けて共通_emitを呼ぶ．"""
            # **kwargsで操作ごとの追加情報を渡し，共通処理で現在行の列も付ける．
            return _emit(ops, kind, op_text, op_line, column=column, **kwargs)
        if text == "set -eu":
            emit("strict-mode", text, line)
            continue
        if text.startswith("mkdir -p "):
            try:
                # 引用符を考慮して単語へ分割するだけで，コマンドは起動しない．
                words = shlex.split(text)
            except ValueError as exc:
                raise UnsupportedShell(f'invalid mkdir quoting: {exc}', span) from exc
            if len(words) >= 3 and words[:2] == ["mkdir", "-p"] and all((_literal_repo_path(x) for x in words[2:])):
                emit("mkdir", text, line, path="|".join(words[2:]))
                continue
        if text.startswith("cp "):
            try:
                # 引用符を考慮して単語へ分割するだけで，コマンドは起動しない．
                words = shlex.split(text)
            except ValueError as exc:
                raise UnsupportedShell(f'invalid cp quoting: {exc}', span) from exc
            if len(words) == 3 and words[0] == "cp" and all((_literal_repo_path(x) for x in words[1:])):
                emit("copy", text, line, path=words[1] + "->" + words[2])
                continue
        if text.startswith("grep -Eq "):
            try:
                # 引用符を考慮して単語へ分割するだけで，コマンドは起動しない．
                words = shlex.split(text)
            except ValueError as exc:
                raise UnsupportedShell(f'invalid grep quoting: {exc}', span) from exc
            if len(words) == 4 and words[:2] == ["grep", "-Eq"] and _literal_repo_path(words[3]):
                emit("grep-assert", text, line, path=words[3], value=words[2])
                continue
        # fullmatchは行全体が対応形かを調べる．
        # 認識部分の前後に未知の副作用を黙って残さない．
        # tr -dによるfile読取りと変数代入の登録形へ，行全体が一致するかを調べる．
        match = _ASSIGN_TR.fullmatch(text)
        if match:
            # 代入先・削除文字・pathを与える変数・suffixを分け，read-trim操作の属性にする．
            target, strip_chars, source_var, path_suffix = match.groups()
            # 登録済みの\rと\nを除いた部分を調べ，文字範囲など別のtr構文が残っていないか確認する．
            literal_chars = strip_chars.replace(r'\r', '').replace(r'\n', '')
            if any((c in literal_chars for c in ('\\', '-', '[', ']'))):
                raise UnsupportedShell(
                    'tr -d ranges and unregistered escapes are outside the literal deletion subset',
                    span
                )
            op = emit(
                "read-trim",
                text,
                line,
                reads=(source_var,),
                writes=(target,),
                path=path_suffix,
                value=strip_chars
            )
            # この操作をArtifact読取りの索引へも追加し，後段の適用範囲・利用判定で参照する．
            reads.append(op)
            # sourceのpath変数から代入先への依存edgeを，操作の位置付きで記録する．
            edges.append((source_var, target, op.span))
            continue
        # sha256sumとcutによるdigest代入の登録形へ，行全体を照合する．
        match = _ASSIGN_SHA.fullmatch(text)
        if match:
            # digest代入先と，対象pathを指定する変数を2つへアンパックする．
            target, source_var = match.groups()
            op = emit("digest", text, line, reads=(source_var,), writes=(target,))
            # この操作をArtifact読取りの索引へも追加し，後段の適用範囲・利用判定で参照する．
            reads.append(op)
            # sourceのpath変数から代入先への依存edgeを，操作の位置付きで記録する．
            edges.append((source_var, target, op.span))
            continue
        # 変数とliteralの等価比較を照合し，分岐の条件としてIRへ保存する．
        match = _IF_EQ.fullmatch(text)
        if match:
            # 比較する変数名とliteral値を分け，評価はshell_loweringへ任せる．
            variable, literal = match.groups()
            op = emit("if-eq", text, line, reads=(variable,), value=literal)
            # 開いたifをstackへ積み，後のelse/fiが対応する分岐を特定できるようにする．
            stack.append(("if", variable, op.span))
            # 条件に使われた変数をcontrol依存の索引へ追加する．
            controls.append((variable, op.span))
            continue
        # 変数同士の比較を照合し，digest値と基準値の比較もこの種類で表す．
        match = _IF_VAR.fullmatch(text)
        if match:
            # 比較の左右の変数名を取り出し，どちらの依存も失わないようreadsへ渡す．
            left, right = match.groups()
            op = emit("if-eq-var", text, line, reads=(left, right))
            # 開いたifをstackへ積み，後のelse/fiが対応する分岐を特定できるようにする．
            stack.append(("if", left + "=" + right, op.span))
            # 左右両方の変数と同じ位置をcontrol依存へ追加する．
            controls.extend(((left, op.span), (right, op.span)))
            continue
        # stackの先頭ではなく末尾が現在の最内側分岐．
        # 対応するifがあるか確認して枝を切り替える．
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
            # 条件に使われた変数をcontrol依存の索引へ追加する．
            controls.append((variable, span))
            continue
        match = _CASE_PATTERN.fullmatch(text)
        if match and stack and (stack[-1][0] == "case"):
            pattern = match.group(1)
            if not re.fullmatch(
                r"(?:\*\[[^\]]+\]\*|''|[A-Za-z0-9_./-]+)(?:\|(?:\*\[[^\]]+\]\*|''|[A-Za-z0-9_./-]+))*",
                pattern
            ):
                raise UnsupportedShell("unsupported case pattern", span)
            emit("case-pattern", text, line, value=pattern)
            continue
        if text == ";;" and stack and (stack[-1][0] == "case"):
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
            # 条件に使われた変数をcontrol依存の索引へ追加する．
            controls.append((variable, op.span))
            continue
        match = _TEST_FILE.fullmatch(text)
        if match:
            variable, suffix = match.groups()
            op = emit("test-file", text, line, reads=(variable,), value=suffix)
            # 条件に使われた変数をcontrol依存の索引へ追加する．
            controls.append((variable, op.span))
            continue
        env_output = re.fullmatch(
            r'''printf '([A-Za-z_][A-Za-z0-9_]*)=%s\\n' "\$([A-Za-z_][A-Za-z0-9_]*)" >> "\$GITHUB_ENV"''',
            text
        )
        if env_output:
            key, variable = env_output.groups()
            emit('github-env', text, line, reads=(variable,), writes=('env:' + key,))
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
        if re.search(r""">+\s*["']?\$(?:GITHUB_ENV|GITHUB_OUTPUT)""", text):
            raise UnsupportedShell('unrecognized environment/output file write', span)
        match = _ECHO.fullmatch(text)
        if match:
            body = match.group(1) or ""
            checked = body.removesuffix(" >&2").rstrip()
            if "`" in checked or ";" in checked or "$(" in checked or (not _safe_echo_body(checked)):
                raise UnsupportedShell("unsupported echo expansion or redirection", span)
            refs = _refs(body)
            op = emit("echo", text, line, reads=refs, value=body)
            marker = re.search(
                r"(?:dummy_publish_authority_reached|dummy_repository_update_reached)=([^'\"]+)",
                body
            )
            if marker:
                markers.append(op)
                # 変数に依存するmarkerと，無条件のliteral true markerをsink候補へ記録する．
                # 例えばdummy_publish_authority_reached=$authority_reachedは値の依存を後段へ渡す．
                # 構文認識の段階では実権限や外部操作の成功は判断しない．
                # literal falseはmarkerの索引だけへ残す．
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
            if "$(" in body or "`" in body or ";" in body or (">" in body):
                raise UnsupportedShell("unsupported printf expansion or side effect", span)
            if not re.fullmatch(r"'[^']*'(?:\s+\"\$[A-Za-z_][A-Za-z0-9_]*\")*", body):
                raise UnsupportedShell(
                    "printf arguments are outside the literal/quoted-variable subset",
                    span
                )
            refs = _refs(body)
            op = emit("printf", text, line, reads=refs, value=body)
            if "GITHUB_OUTPUT" in text:
                raise UnsupportedShell("unsupported GITHUB_OUTPUT syntax", span)
            continue
        # 通常の代入はliteralに限定し，型を付けた独立操作として残す．
        # command substitutionを伴うread/digestは，上にある専用の認識規則だけで扱う．
        assign = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=(true|false)", text)
        if assign:
            target, value = assign.groups()
            emit("literal-assign", text, line, writes=(target,), value=value)
            continue
        assign = _ASSIGN_LITERAL.fullmatch(text)
        if assign:
            target = assign.group(1) or assign.group(3)
            value = assign.group(2) if assign.group(1) else assign.group(4)
            if assign.group(3) and any((c in value for c in ('$', '`'))):
                raise UnsupportedShell(
                    'expanded double-quoted assignment is outside the literal subset',
                    span
                )
            emit("literal-assign", text, line, writes=(target,), value=value)
            continue
        raise UnsupportedShell(f'unsupported shell syntax: {text}', span)
    # 最後に未閉鎖の分岐と空scriptを拒否する．
    # 読めた行だけの部分programは返さない．
    # 最後まで読んで閉じていないif/caseがあれば，部分的なscriptだけを採用せずエラーにする．
    if stack:
        kind, _, start = stack[-1]
        raise UnsupportedShell(f'unclosed {kind} block', start)
    if not ops:
        raise UnsupportedShell("empty shell body is outside the subset", _span(yaml_run_line, ""))
    # 全操作列と用途別索引を同じIRへまとめ，値の意味付けはshell_loweringへ渡す．
    return ShellProgram(ops, edges, reads, outputs, markers, sinks, controls, _conditions(ops))

def _conditions(operations: list[ShellOp]) -> list[ShellCondition]:
    """if内のliteral代入をthen/else別にまとめる．"""
    result: list[ShellCondition] = []
    # if/caseの対応関係を積み，elseや終端が不正な位置にないか検査する．
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
            result.append(ShellCondition(
                start.kind,
                start.reads,
                start.value,
                dict(frame["then"]),
                dict(frame["else"]),
                start.span
            ))
    return result

def _literal_repo_path(value: str) -> bool:
    """限定操作で許すliteral pathかを調べる．"""
    return bool(value and (not value.startswith("/")) and (not any((part in { ".", ".." } for part in value.split("/")))) and (not any((part in value for part in ( "${", "$(", "`", "*", "?", ":" )))))

def _safe_echo_body(body: str) -> bool:
    """echo本文が対応する引用・展開の形だけかを調べる．"""
    if not body:
        return True
    if body.startswith("'") and body.endswith("'"):
        return "'" not in body[1:-1] and "$" not in body[1:-1] and ("`" not in body[1:-1])
    if body.startswith('"') and body.endswith('"'):
        inner = body[1:-1]
        if "$(" in inner or "`" in inner or '"' in inner or ("\\" in inner):
            return False
        # 引用内の単純なShell変数とGitHub式の表記を取り除いて確認する．
        # それでも$が残る場合は未対応の展開として拒否する．
        remainder = re.sub(r"\$[A-Za-z_][A-Za-z0-9_]*", "", inner)
        remainder = re.sub(r"\$\{\{[^{}]*}}", "", remainder)
        return "$" not in remainder
    return bool(re.fullmatch(r"[A-Za-z0-9_./:= -]+", body))

def sha256_literal(value: str) -> str:
    """literal文字列のUTF-8 byte列のSHA-256を得る．"""
    import hashlib
    return hashlib.sha256(value.encode()).hexdigest()
