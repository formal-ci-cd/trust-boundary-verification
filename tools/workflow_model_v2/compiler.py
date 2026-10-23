# 共通の変換器本体．
# Runはworkflowの実行1回，Jobはmatrix展開後のjob，Stepはその中の処理を表す．
# 入力の値候補とYAMLの制御構造から，guard（更新条件）付きの状態遷移を組み立てる．
# Pythonでは危険地点への到達性を決めず，生成したSMVをNuSMVで検証する．
# 表現できない効果はdiagnosticへ記録し，モデル上の不達を安全性の保証へ読み替えない．
"""GitHub Actionsの構文・有限の実行条件を，共通の状態遷移IRへ変換する．

入力はYAMLのPath列とscenario，出力はSMV文字列とmanifestの組である．
YAMLをRun/Job/Stepへ整理し，output/envの値候補を収束させて保存objectを宣言する．
buildでjobのPC，stepの成否，file/objectの状態，権限・利用履歴の更新条件を生成する．
TransitionSystemがSMVへ出力し，到達性の最終検査はNuSMVへ残す．
DataflowとShellLoweringを継承し，値の伝播と構造化Shellの意味付けを共通状態へ接続する．
fileのabsent/clean/taintedは抽象状態であり，実ファイルの安全性の判定結果ではない．
未知の権限は共有FROZENVAR，実行ごとの選択はIVAR，変化する履歴やPCはVARで表す．
未対応効果はdiagnosticsへ残す．仮定・適用範囲はdocs/semantics.mdも参照する．
"""
from __future__ import annotations
from dataclasses import asdict, dataclass, field
from itertools import product
from pathlib import Path
import copy
import hashlib
import json
import re
import shlex
import time
import yaml
from . import frontend as base
from .workflow_loader import WorkflowLoader
from . import expressions as ex
from . import policies as pol
from .transition import TransitionSystem, atom, symbol, conjunction as AND, disjunction as OR
from .composites import expand as expand_composites
from .action_contracts import resolve as resolve_action
from .shell_lowering import ShellLowering
from .dataflow import Dataflow
from workflow_shell_subset import parse_shell, UnsupportedShell
# manifestへ記録する契約の版であり，Actionの実コードを取得して確認したことを表す値ではない．
CONTRACT_VERSION = 'bounded-contracts-2026-10-23.1'

# dataclassはfieldを宣言してデータの入れ物を作る．
# 型ヒントは読み手への情報で，自動の入力検査ではない．
@dataclass
class Step:
    """1つのYAML stepと，対応契約・操作列を保持するIR．"""
    # 型ヒントのstrは文字列を想定するという情報であり，dataclassが値の妥当性を自動検査するわけではない．
    id: str
    # YAMLのKey/Valueを保持し，後段でif・with・envなど必要なキーを読む．
    raw: dict
    # 元YAMLのファイル・行・列を保持し，SMV記号や診断から元の記述へ戻れるようにする．
    source: dict
    contract: str = 'unsupported'
    # default_factory=listはStepごとに新しいリストを作り，別Stepと操作列が共有されるのを防ぐ．
    ops: list = field(default_factory=list)
    versioned_contract: dict | None = None
    program: object = None

@dataclass
class Job:
    """matrixを展開した後の1つのjobを表すIR．"""
    # 型ヒントのstrは文字列を想定するという情報であり，dataclassが値の妥当性を自動検査するわけではない．
    id: str
    original: str
    run: int
    # YAMLのKey/Valueを保持し，後段でif・with・envなど必要なキーを読む．
    raw: dict
    matrix: dict
    # 元YAMLのファイル・行・列を保持し，SMV記号や診断から元の記述へ戻れるようにする．
    source: dict
    steps: list
    # matrix展開後の依存Jobの識別子を格納し，Jobごとに独立したリストを使う．
    needs: list = field(default_factory=list)

@dataclass
class Run:
    """供給されたworkflowに対応する，有界な実行1回分を表すIR．"""
    # 型ヒントのstrは文字列を想定するという情報であり，dataclassが値の妥当性を自動検査するわけではない．
    id: str
    path: str
    # YAMLのKey/Valueを保持し，後段でif・with・envなど必要なキーを読む．
    raw: dict
    context: dict
    jobs: list

# 単純なShellを閉じた操作列へ変換する．
# 任意のコマンドや展開を推測で許可しない．
def shell_contract(script):
    """対応する単純Shellを，型名付きtupleの操作列へ変換する．"""
    # ここで作るtupleは命令を実行するための引数ではなく，後段が読む操作IRである．
    operations = []
    # run本文を1行ずつ読み，認識した行だけを実行せず操作IRへ変換する．
    for line in script.splitlines():
        # 行頭・行末の空白を除き，字下げに影響されず閉じたShell契約へ照合する．
        text = line.strip()
        # 空行・コメント・登録済みのset指定は操作として追加せず，次の行を調べる．
        if not text or text.startswith('#') or text in {'set -e', 'set -eu', 'set -euo pipefail'}:
            continue
        # 環境ファイルの単純な1行書込み．
        # double quote内の変数展開は共通データフローへ渡す．
        # echoによるKEY=valueの追記を行全体で照合し，変数名・値・引用符・書込先を名前付きgroupへ分ける．
        file_command = re.fullmatch(
            r"""echo\s+(?P<quote>['"])(?P<key>[A-Za-z_][A-Za-z0-9_-]*)=(?P<value>.*?)(?P=quote)\s*>>\s*(?P<destquote>"?)\$(?P<dest>GITHUB_ENV|GITHUB_OUTPUT)(?P=destquote)""",
            text
        )
        # 環境ファイルへの追記に完全一致した場合だけ，捕捉した各groupを読み取る．
        if file_command:
            # 正規表現が取り出した4つの値を受け取り，引用方法とGITHUB_ENV/GITHUB_OUTPUTの違いを後段へ残す．
            quote, key, payload, dest = (
                file_command['quote'],
                file_command['key'],
                file_command['value'],
                file_command['dest']
            )
            # GitHub式の埋込み部分を一旦除き，その外側に契約外のShell展開がないか調べる準備をする．
            literal = re.sub(r'\$\{\{.*?}}', '', payload)
            # バッククォート・バックスラッシュ・引用符の混在やコマンド置換があれば，この単純契約では解釈しない．
            if any((c in literal for c in ('`', '\\', quote))) or '$(' in literal:
                raise ValueError('unsupported environment file command expansion/quoting')
            # GITHUB_ENVならjob共有env，GITHUB_OUTPUTならstep outputへの書込操作を作り，二重引用符の場合だけ変数展開を有効にする．
            operations.append((
                'runtime-env' if dest == 'GITHUB_ENV' else 'runtime-output',
                key,
                payload,
                quote == '"'
            ))
            continue
        # 環境ファイルらしい追記が正しい契約に一致しなかった場合は，引用や展開を推測して受け入れず例外にする．
        if re.search(r""">+\s*["']?\$(?:GITHUB_ENV|GITHUB_OUTPUT)""", text):
            raise ValueError('environment file destination must be expanded, with balanced quoting')
        # GITHUB_OUTPUTへの単純な出力名・値の記述を照合し，後続のoutput操作へ渡す．
        output = re.fullmatch(r'''echo\s+['"]([\w-]+)=(.*?)['"]\s*>>\s*['"]?\$GITHUB_OUTPUT['"]?''', text)
        # literalのecho/printfと単一パスへの上書きを照合し，ファイル内容の有限候補を得る．
        write = re.fullmatch(r'''(?:echo|printf)\s+(['"])(.*?)\1\s*>\s*([\w./-]+)''', text)
        # outputへの書込みに一致した場合は，出力名と未評価の値を操作列へ追加する．
        if output:
            operations.append(('output', output.group(1), output.group(2)))
        # literalのファイル書込みに一致した場合だけ，内容と改行の有無を保存する．
        elif write:
            # 書込内容にShell展開があれば，literalとして安全に扱わず構造化Shellの契約へ任せる．
            if '$' in write.group(2) or '`' in write.group(2):
                raise ValueError('file-write interpolation requires a structured value contract')
            # printfの書式指定やエスケープはこの契約では評価しないため，該当時は例外にする．
            if text.startswith('printf ') and ('%' in write.group(2) or '\\' in write.group(2)):
                raise ValueError('printf format expansion outside literal file-write contract')
            # echoには末尾改行を加え，printfには加えず，後段の内容hash計算へ渡す文字列を保持する．
            operations.append((
                'write',
                write.group(3),
                write.group(2) + ('\n' if text.startswith('echo ') else '')
            ))
        # 残りの行に展開・制御・リダイレクトの記号がある場合，単語分割だけで意味を推測しない．
        elif '${{' in text or any((c in text for c in (';', '|', '&', '`', '$', '>', '<'))):
            raise ValueError('shell expansion/control/redirection outside closed operation contract: ' + text)
        else:
            # 引用を考慮して単語へ分割するだけで，commandを起動することはない．
            parts = shlex.split(text)
            # mkdir -pとパス1つの形だけを，ディレクトリ作成の操作IRとして受け付ける．
            if parts[:2] == ['mkdir', '-p'] and len(parts) == 3:
                operations.append(('mkdir', parts[2]))
            # cpのコピー元とコピー先を保持し，後段でファイル状態と由来をコピーする．
            elif parts and parts[0] == 'cp' and (len(parts) == 3):
                operations.append(('copy', parts[1], parts[2]))
            # catの対象パスを読み取り操作として保存し，後段でtaintedな値の利用を記録する．
            elif parts and parts[0] == 'cat' and (len(parts) == 2):
                operations.append(('read', parts[1]))
            # git addの対象をstage操作へ変換し，taintedな内容が登録された履歴を別のVARへ記録する．
            elif parts[:2] == ['git', 'add'] and len(parts) == 3:
                operations.append(('stage', parts[2]))
            # 限定形のgit pushをcontents権限のsinkへ対応付け，stagedの汚染履歴を参照させる．
            elif parts[:2] == ['git', 'push'] and len(parts) in {2, 3, 4}:
                operations.append(('sink', 'contents', 'staged'))
            # npm/pnpm publishをpackagesのsinkへ対応付け，指定パスまたは既定の.を依存対象にする．
            elif parts[:2] in (['npm', 'publish'], ['pnpm', 'publish']) and len(parts) in {2, 3}:
                operations.append(('sink', 'packages', parts[2] if len(parts) == 3 else '.'))
            # 登録済みのdeployment API呼出しだけをdeployments権限のsinkとして表す．
            elif parts[:3] == ['gh', 'api', 'repos/{owner}/{repo}/deployments']:
                operations.append(('sink', 'deployments', '.'))
            # 明示的な異常終了をfail操作として残し，buildでstep失敗へ結び付ける．
            elif parts == ['exit', '1']:
                operations.append(('fail',))
            # 他のechoはログ操作として登録し，ここではファイル更新や権限効果を付与しない．
            elif parts and parts[0] == 'echo':
                operations.append(('log',))
            else:
                raise ValueError('unrecognized property-relevant command: ' + text)
    # 認識した順序の操作列を返し，実際のShellコマンドは起動しない．
    return operations

# 読取り・宣言・遷移生成を共通の状態系で行う．
# 構造化Shellの処理はmixinから使う．
class WorkflowCompiler(ShellLowering, Dataflow):
    # YAMLを読んで状態変数を用意し，consumerが参照するoutputの有限domainを先に確定する．
    """共通schedulerとobject状態を組み立てる変換器．"""

    def __init__(self, paths, scenario):
        """入力を読み，scheduler・file・output・保存候補の宣言を準備する．"""
        # 入れ子の辞書/listも複製し，snapshot導出等で呼出し側のscenarioを変更しない．
        self.scenario = copy.deepcopy(scenario)
        # 以降は複製済みscenarioを使い，snapshot等の導出値も呼出し側の辞書へ書き戻さない．
        scenario = self.scenario
        # Cacheの履歴版・現行版を区別するpolicy識別子を取り出す．
        self.policy = scenario.get('cachePolicy')
        # 以後のAction・Shell・schedulerが共用する状態遷移IRを作る．
        self.ts = TransitionSystem()
        # 入力run・展開済みjob・省略した意味規則の診断を，それぞれ別のリストで管理する．
        self.runs, self.jobs, self.diagnostics = ([], [], [])
        # 値の共有表とArtifact/Cache候補を初期化し，値の参照と保存物の同一性を分ける．
        self.outputs, self.files, self.objects, self.caches = ({}, {}, [], [])
        # 有限context参照の管理用辞書を空で用意する．
        self.static_refs = {}
        # 同じuses参照を重複させずmanifestへ記録するため，setを用意する．
        self.action_refs = set()
        # 同一identityの未知条件を共有する表を用意し，参照ごとに別の真偽を選ばせない．
        self._unknowns = {}
        # YAMLの位置情報付き構文木をファイル名ごとに再利用する表を作る．
        self.source_trees = {}
        # snapshotを指定した場合，自動の代表内容を実ファイルより優先する明示値とは扱わない．
        for assumption in scenario.get('automaticAssumptions', []):
            context = next((run for run in scenario.get('runs', []) if run.get('id') == assumption['run']), {})
            path = assumption['path']
            if context.get('repositorySnapshot') and context.get('checkoutValues', {}).get(path) == assumption['contents']:
                context['checkoutValues'].pop(path)
                context['checkoutFiles'] = [p for p in context.get('checkoutFiles', []) if p != path]
        # 明示されたsnapshotだけを読んで，有限のファイル在庫と内容候補をscenarioへ補う．
        self.prepare_snapshots()
        # default_scenarioを土台に研究用の在庫・内容を明示した場合は，その指定を自動候補より優先する．
        # snapshotの完全在庫や差し替えた内容へ，古い「存在が未知」の条件を残さない．
        active_assumptions = []
        for assumption in scenario.get('automaticAssumptions', []):
            context = next((run for run in scenario.get('runs', []) if run.get('id') == assumption['run']), {})
            path = assumption['path']
            overridden = (context.get('fileInventoryComplete')
                          or path not in context.get('checkoutFiles', [])
                          or path not in context.get('checkoutValues', {})
                          or context['checkoutValues'][path] != assumption['contents'])
            if overridden:
                context['optionalCheckoutFiles'] = [p for p in context.get('optionalCheckoutFiles', []) if p != path]
                scenario.setdefault('automaticAssumptionOverrides', []).append(dict(
                    run=assumption['run'], path=path,
                    reason='explicit contents or complete snapshot replace the inferred file candidate',
                ))
            else:
                active_assumptions.append(assumption)
        if 'automaticAssumptions' in scenario:
            scenario['automaticAssumptions'] = active_assumptions
        # 入力run数が設定上限を超えた場合は，切り捨てたモデルを作らず変換を停止する．
        if len(paths) > scenario.get('bounds', {}).get('runs', 32):
            raise base.WorkflowError('run bound exceeded; model was not generated')
        # YAMLをRun/Job/Stepへ読み替え，matrix展開とneeds参照の検査を済ませる．
        self._load(paths)
        # 自動導出の失敗を，IRが保持する実際のYAML位置へ対応付けて診断する．
        for issue in scenario.get('automaticDiagnostics', []):
            evidence = issue.get('evidence', {})
            source = next((step.source for job in self.jobs
                           if self.runs[job.run].path == evidence.get('file')
                           and job.original == evidence.get('job')
                           for index, step in enumerate(job.steps)
                           if index == evidence.get('step')), None)
            self.diagnostic(issue['code'], issue['message'], source)
        # matrix展開後のJob数も上限を確認し，巨大化を黙って省略しない．
        if len(self.jobs) > scenario.get('bounds', {}).get('jobs', 128):
            raise base.WorkflowError('job bound exceeded; model was not generated')
        # 総step数に余裕を加えた有限clock幅を決め，Cacheの保存順をageとして記録できるようにする．
        self.clock_modulus = max(2, sum((len(job.steps) for job in self.jobs)) + 2)
        # actorが各遷移で進めるjobを選ぶ．
        # operation_ok等は観測結果ではなく，モデル内の成否入力．
        # IVARのactorが選んだjobだけを進める．
        # idleも許し，複数jobの実行順を単一に固定しない．
        self.ts.inputs['actor'] = ['idle'] + [job.id for job in self.jobs]
        # stepの成功・失敗を選ぶIVARを宣言し，実行結果を観測した値とは区別する．
        self.ts.inputs['operation_ok'] = ['FALSE', 'TRUE']
        # Cacheの通信成否をstep自体の成否から独立したIVARとして宣言する．
        self.ts.inputs['cache_io_ok'] = ['FALSE', 'TRUE']
        # Cache I/Oをscenarioで真偽に固定した場合だけ，その値を全遷移へ課す．
        if isinstance(scenario.get('cacheIO'), bool):
            # TRANSへ出す等式を登録し，Cache I/Oの非決定的な選択を指定値へ制限する．
            self.ts.constraints.append('cache_io_ok = ' + str(scenario['cacheIO']).upper())
        # jobの取消要求を各遷移で選ぶIVARとして宣言する．
        self.ts.inputs['cancel_request'] = ['FALSE', 'TRUE']
        # scenarioの成否・取消指定を，対応するSMV入力名との組で調べる．
        for setting, variable in [('operationOutcome', 'operation_ok'), ('cancelRequests', 'cancel_request')]:
            # 指定がBooleanなら固定し，未指定ならIVARの両方の可能性を残す．
            if isinstance(scenario.get(setting), bool):
                # PythonのTrue/FalseをSMVのTRUE/FALSEへ変換し，TRANS制約へ追加する．
                self.ts.constraints.append(variable + ' = ' + str(scenario[setting]).upper())
        # これらは一度起きた事象を保持するBooleanのVAR．
        # 到達・権限・成功を別の履歴にする．
        for name in [
            'transfer',
            'dependent_use',
            'sink_site',
            'authority_available',
            'sink_succeeded',
            'bad_authority'
        ]:
            # 各履歴を初期値FALSEのVARにし，事象が起きた時だけlatchでTRUEへ更新する．
            self.ts.var(name, ['FALSE', 'TRUE'], 'FALSE')
        # AG !xは「すべての到達状態でxが起きないか」を問うCTL．
        # falseならモデル内の反例がある．
        self.ts.properties = {
            'no_transfer': 'AG !transfer',
            'no_use': 'AG !dependent_use',
            'no_sink_site': 'AG !sink_site',
            'no_success': 'AG !sink_succeeded',
            'no_bad_authority': 'AG !bad_authority',
            'success_requires_site': 'AG (sink_succeeded -> sink_site)',
            'bad_requires_success': 'AG (bad_authority -> sink_succeeded)'
        }
        # GITHUB_ENV/outputの共有slotと依存ラベルの表を，状態宣言より先に準備する．
        self._prepare_dataflow()
        # 構造化Shellが使う内容・由来・完全性確認の状態を共通IRへ用意する．
        self._prepare_shell_state()
        # jobのPCと失敗・取消・step結果のVARを，遷移を登録する前に宣言する．
        self._declare()
        # outputとGITHUB_ENVのdomainを先に収束させてから，動的keyのobject候補を宣言する．
        for _ in range(self.scenario.get('bounds', {}).get('domainPasses', 16)):
            # 候補集合の変化を比較する準備用の固定点反復．
            # NuSMVの状態探索をPythonで代行する処理ではない．
            before = repr((self.outputs, self.runtime_env))
            # 既知のoutput/env候補を次の参照先へ伝え，動的keyやpathを列挙できる範囲を広げる．
            self.propagate_domains()
            # 候補表が変わらなければ固定点に達したため，残りの反復を打ち切る．
            if before == repr((self.outputs, self.runtime_env)):
                break
        else:
            self.diagnostic(
                'output-domain-cycle',
                'finite dataflow domains did not converge within the configured bound'
            )
        # 確定したkey/name/path候補から，Artifact/Cacheごとの状態変数を宣言する．
        self._declare_objects()
        # 保存物候補の総数も上限を確認し，候補の切捨てで到達性を過小評価することを防ぐ．
        if len(self.objects) + len(self.caches) > scenario.get('bounds', {}).get('objects', 256):
            raise base.WorkflowError('object bound exceeded; model was not generated')

    # 省略した意味規則と影響するpropertyを記録し，後の安全性の解釈に残す．
    def diagnostic(self, code, message, source=None, status='unsupported'):
        """モデルの意味解釈に必要な診断を重複なく記録する．"""
        # 診断の種類・説明・sourceと，省略した意味規則の影響を受けるproperty名を1件の辞書へまとめる．
        record = dict(
            status=status,
            code=code,
            message=message,
            source=source,
            properties=['no_transfer', 'no_use', 'no_bad_authority']
        )
        # 同じ診断が固定点反復などで繰り返された場合は，重複して追加しない．
        if record not in self.diagnostics:
            # まだ記録していない診断を追加し，後でmanifestの対応状況として返す．
            self.diagnostics.append(record)

    # 同じidentityの未知条件には，同じ固定Booleanを再利用する．
    # 未対応診断も必ず残す．
    def unknown(self, identity, message, source=None):
        """identityごとに共有する未知Booleanを宣言する．"""
        # 未対応条件のidentityから安定した記号を作り，同じ未知条件を全参照で共有する．
        name = symbol('unknown', identity)
        # 初めて使う未知条件の場合だけ，FROZENVARの宣言を追加する．
        if name not in self._unknowns:
            # この未知記号に対応するidentityを登録し，次の参照で同じ固定値を使えるようにする．
            self._unknowns[name] = identity
            # 実行開始時に真偽を選び，その後は変化しない未知条件として扱う．
            self.ts.frozen[name] = ['FALSE', 'TRUE']
        # 未知の真偽を導入した理由を必ず診断し，このモデルの不達を対応済みの安全保証にしない．
        self.diagnostic('unsupported-semantics', message, source)
        return name

    # YAMLのrun本文の開始行を求め，Shell内の位置を元のファイルへ対応付ける．
    def parse_structured_shell(self, script, source):
        """run本文の正しい開始行を求めて構造化Shellを解析する．"""
        # 該当YAMLの位置情報付き構文木が，既に読まれているか確認する．
        tree = self.source_trees.get(source['file'])
        # 未読の場合だけ構文木を読み直し，後のShell位置計算でも再利用する．
        if tree is None:
            # 同じYAMLをnodeの木としても読み，値だけでは失われる行・列を保持する．
            tree = yaml.compose(Path(source['file']).read_text(), Loader=WorkflowLoader)
            # 読み直した位置構文木を保存し，以後のrun開始行計算で再解析しないようにする．
            self.source_trees[source['file']] = tree
        # run本文の実際の開始行を求め，Shellの各操作へ元YAML上の行番号を付ける．
        return parse_shell(script, base.run_content_line(tree, source))

    # workflowをRun/Job/Stepへ整理する．
    # matrixを展開し，needsの参照先と循環を確認する．
    def _load(self, paths):
        """YAMLをIRへ格納し，契約解決とneedsの整合性検査を行う．"""
        # workflowの各パスと1対1に対応する外部実行条件を取り出す．
        configs = self.scenario.get('runs', [])
        # 条件の数とYAMLの数が違うとrunの対応を決められないため，ここでエラーにする．
        if len(configs) != len(paths):
            raise base.WorkflowError('scenario.runs must contain one context per supplied workflow')
        # enumerateでrunの添字を付け，zipでPathとcontextを対応付ける．
        # 括弧はtupleのアンパックである．
        for ri, (path, config) in enumerate(zip(paths, configs)):
            # workflowファイルを文字列として読み，同じ内容を値の解析と位置の解析へ渡す．
            source = Path(path).read_text()
            # YAMLのKey/ValueをPythonの辞書・リストへ変換し，専用loaderでonの暗黙Boolean化と重複キーを防ぐ．
            raw = yaml.load(source, Loader=WorkflowLoader)
            # 同じYAMLをnodeの木としても読み，値だけでは失われる行・列を保持する．
            tree = yaml.compose(source, Loader=WorkflowLoader)
            # 値の解析と同じファイルの構文木を登録し，Shellのsource map生成から参照できるようにする．
            self.source_trees[str(path)] = tree
            # workflow全体とjobsが辞書で，jobsが空でないことを確認してから下位のキーを読む．
            if not isinstance(raw, dict) or not isinstance(raw.get('jobs'), dict) or (not raw['jobs']):
                raise base.WorkflowError('workflow requires jobs mapping: ' + str(path))
            # file名から実行idや信頼性を決めず，必要な外部条件をcontextから受け取る．
            # モデルのイベント・信頼境界・Cache scopeを決める必須contextキーの集合を用意する．
            required = {
                'event',
                'activity',
                'branch',
                'scope',
                'repository',
                'defaultBranch',
                'baseBranch',
                'fork',
                'defaultBranchWorkflow',
                'refType',
                'runnerOS',
                'runnerArch'
            }
            # 必須キーが欠けていれば，集合差で不足名を求めてエラーにし，環境を推測して補わない．
            if not required.issubset(config):
                raise base.WorkflowError('scenario context missing: ' + ', '.join(sorted(required - set(config))))
            # 未加工YAMLとscenarioのcontextを同じRunへ保持し，ファイル名から信頼性を決めない．
            run = Run(str(config.get('id', f'run{ri}')), str(path), raw, dict(config), [])
            # dictのkey集合から対応済みkeyを引き，意味規則のない構文を診断する．
            for name in raw.keys() - {
                'name',
                'on',
                'permissions',
                'env',
                'jobs',
                'cache-mode',
                'concurrency'
            }:
                self.diagnostic('workflow-key', 'workflow key lacks semantics: ' + str(name))
            # 既存runとidが重複するとworkflow_run参照を区別できないため，追加前にエラーにする．
            if any((r.id == run.id for r in self.runs)):
                raise base.WorkflowError('duplicate run id')
            # 読み込んだRunを入力順で保存し，Jobからrun番号で参照できるようにする．
            self.runs.append(run)
            # reusable workflowの呼出し文脈の伝播は実装していないため，診断を残す．
            if config['event'] == 'workflow_call':
                self.diagnostic(
                    'workflow-call-context',
                    'caller event payload, permissions and secrets propagation are not implemented'
                )
            # run間のqueue置換・取消をこのモデルで再現しないため，該当YAMLの位置付きで診断する．
            if 'concurrency' in raw:
                self.diagnostic(
                    'concurrency',
                    'queue replacement/cancellation across additional runs is outside this bounded contract',
                    base._span(base._member(tree, 'concurrency'), str(path)).json()
                )
            # jobsの位置情報nodeを取り出し，各job・stepのsourceを求める起点にする．
            jnode = base._member(tree, 'jobs')
            # jobs辞書をjob名と設定値の組で走査し，それぞれを展開済みJobへ変換する．
            for name, job_raw in raw['jobs'].items():
                # job設定が辞書でなければ，下位キーを読まず入力形式エラーにする．
                if not isinstance(job_raw, dict):
                    raise base.WorkflowError('job must be mapping')
                # 通常のstep jobにはruns-onを要求し，job-level usesの呼出しは別の未対応診断へ回す．
                if 'uses' not in job_raw and 'runs-on' not in job_raw:
                    raise base.WorkflowError('step job requires runs-on')
                # YAML構文木からこのjobのファイル名・行・列を取り出す．
                span = base._span(base._member(jnode, str(name)), str(path)).json()
                for key in job_raw.keys() - {
                    'name',
                    'runs-on',
                    'needs',
                    'if',
                    'permissions',
                    'steps',
                    'env',
                    'outputs',
                    'strategy',
                    'cache-mode'
                }:
                    self.diagnostic('job-key', 'job key lacks semantics: ' + str(key), span)
                # strategyキーがなければ空辞書を使い，matrixなしのjobとして扱う．
                strategy = job_raw.get('strategy', {})
                # matrixなし，または診断後の代替表現では，空の割当て1個を使ってJobを1つ作る．
                matrices = [{}]
                # strategyが指定されている場合だけ，静的matrixとして扱えるか検査する．
                if strategy:
                    try:
                        # strategy.matrixの軸とinclude/excludeの指定を読み取る．
                        matrix = strategy['matrix']
                        # 対応していないstrategyキーがあれば，省略せずmatrix未対応として記録する．
                        if set(strategy) - {'matrix', 'fail-fast', 'max-parallel'}:
                            raise ValueError('unrecognized strategy keys')
                        # 動的式など辞書でないmatrixは，静的な直積展開として扱わない．
                        if not isinstance(matrix, dict):
                            raise ValueError('matrix must be a static mapping')
                        # 兄弟jobの取消を生成していないため，fail-fastが明示的にfalseのmatrixだけを受け付ける．
                        if strategy.get('fail-fast', True) is not False:
                            raise ValueError('matrix requires fail-fast: false; sibling cancellation is not inferred')
                        # 同時実行数の上限をschedulerへ反映していないため，指定時は未対応にする．
                        if 'max-parallel' in strategy:
                            raise ValueError('max-parallel is not modeled')
                        # 静的な組ごとに別Jobを作る．
                        # 未対応strategyは下のexceptで診断し，安全判定を止める．
                        matrices = pol.expand_matrix(matrix, self.scenario.get('bounds', {}).get('matrix', 32))
                    # 読めない設定を診断へ残してモデル生成を続ける場合も，対応済みとして安全解釈しない．
                    except (KeyError, TypeError, ValueError) as exc:
                        self.diagnostic('matrix', str(exc), span)
                        # matrixなし，または診断後の代替表現では，空の割当て1個を使ってJobを1つ作る．
                        matrices = [{}]
                if 'uses' in job_raw:
                    self.diagnostic(
                        'reusable-workflow',
                        'reusable calls require caller/callee permission and secret propagation; not flattened implicitly',
                        span
                    )
                for feature in ('container', 'services', 'environment', 'concurrency', 'continue-on-error'):
                    if feature in job_raw:
                        self.diagnostic('job-' + feature, 'job feature not represented: ' + feature, span)
                # matrixの割当てごとに番号を付け，同じ元jobから独立したJobを作る．
                for mi, matrix in enumerate(matrices):
                    # run id・元job名・matrix番号の組から，SMVで衝突しないJob識別子を作る．
                    identity = symbol('j', f'{run.id}:{name}:{mi}')
                    steps = []
                    # このjobのstepsの位置情報を取り出し，Composite展開にも渡す．
                    snode = base._member(base._member(jnode, str(name)), 'steps')
                    seen = set()
                    # 登録済みCompositeの内部stepを展開し，各stepとその元ファイル位置の組を得る．
                    expanded = expand_composites(
                        job_raw.get('steps', []),
                        snode,
                        str(path),
                        self.scenario.get('compositeContracts', {}),
                        self.diagnostic
                    )
                    # 展開後のstepを順に読み，設定値とsource位置をアンパックする．
                    for si, (step_raw, step_span) in enumerate(expanded):
                        # runとusesのどちらか一方だけを持つstepを要求し，両方・両方なしは拒否する．
                        if not isinstance(step_raw, dict) or ('run' in step_raw) == ('uses' in step_raw):
                            raise base.WorkflowError('step requires exactly one run/uses')
                        # 明示idがなければ順番からindex0等を作り，step参照用の名前にする．
                        sid = str(step_raw.get('id', f'index{si}'))
                        # 同じjob内でstep idが重複していれば，output参照が曖昧になるため拒否する．
                        if sid in seen:
                            raise base.WorkflowError('duplicate step id ' + sid)
                        # 受け付けたidを集合へ追加し，後続stepの重複検査で使う．
                        seen.add(sid)
                        # 未加工stepとsource位置を保持するIRを作り，続けて適用できる契約を設定する．
                        step = Step(sid, step_raw, step_span)
                        for key in step_raw.keys() - {
                            'id',
                            'name',
                            'run',
                            'uses',
                            'with',
                            'if',
                            'env',
                            'continue-on-error',
                            'shell'
                        }:
                            self.diagnostic('step-key', 'step key lacks semantics: ' + str(key), step_span)
                        # runなら単純操作→構造化Shellの順に試し，usesなら版付きAction契約を解決する．
                        if 'run' in step_raw:
                            try:
                                # 単純Shell契約で操作列を作り，成功した場合は後段の基本効果生成へ渡す．
                                step.ops = shell_contract(str(step_raw['run']))
                                step.contract = 'shell-operations-v1'
                                if 'dummy_publish_authority_reached' in str(step_raw['run']) or 'dummy_repository_update_reached' in str(step_raw['run']):
                                    # 複雑なShellや研究markerを，変数・分岐・位置を保持する構造化IRへ変換する．
                                    step.program = self.parse_structured_shell(str(step_raw['run']), step_span)
                                    step.contract = 'shell-structured-v1'
                            except (ValueError, TypeError):
                                try:
                                    # 複雑なShellや研究markerを，変数・分岐・位置を保持する構造化IRへ変換する．
                                    step.program = self.parse_structured_shell(str(step_raw['run']), step_span)
                                    step.contract = 'shell-structured-v1'
                                # 構造化Shellでも読めない場合は，効果を推測せず未対応診断を残す．
                                except UnsupportedShell as exc:
                                    self.diagnostic('shell-contract', str(exc), step_span)
                        else:
                            # usesのAction名とrefを文字列として取り出し，版付き契約表へ照合する．
                            uses = str(step_raw['uses'])
                            # 使用されたAction参照を重複除去して記録し，manifestから適用範囲を確認できるようにする．
                            self.action_refs.add(uses)
                            # 登録されたAction名・refの完全一致で契約を探し，未登録ならNoneを受け取る．
                            contract = resolve_action(uses)
                            # 登録済みの契約があれば限定効果と版情報を保存し，なければ下のelseで未対応にする．
                            if contract:
                                # 実際の遷移生成で使う限定効果名をStepへ設定する．
                                step.contract = contract.effect
                                # dataclassの契約情報を辞書へ変換し，後でJSONに保存できる形へする．
                                step.versioned_contract = asdict(contract)
                            else:
                                self.diagnostic(
                                    'action-contract',
                                    'unregistered versioned action: ' + uses,
                                    step_span
                                )
                        for feature in ('working-directory', 'timeout-minutes'):
                            if feature in step_raw:
                                self.diagnostic(
                                    'step-' + feature,
                                    'step feature not represented: ' + feature,
                                    step_span
                                )
                        if step_raw.get('shell', 'bash') != 'bash':
                            self.diagnostic(
                                'shell-language',
                                'only bash closed operation contracts are registered',
                                step_span
                            )
                        # 設定した契約も含め，元の実行順でStepをJobの候補列へ追加する．
                        steps.append(step)
                    # 元job名・run番号・matrix割当て・Step列を，schedulerが使うJobへまとめる．
                    job = Job(identity, str(name), ri, job_raw, matrix, span, steps)
                    # 全runを横断するJob一覧へ追加し，actorの候補や状態宣言で使う．
                    self.jobs.append(job)
                    # 所属Runにも同じJobを登録し，workflow_runの完了判定でまとめて参照する．
                    run.jobs.append(job)
        # 入力の各Runを走査し，同じRunに属するJobや実行contextをまとめて参照する．
        for run in self.runs:
            # 所属RunのJobだけでneedsを解決し，別workflowの同名Jobへ誤接続しないようにする．
            for job in run.jobs:
                # needsの指定を読み，省略時は依存先なしの空リストを使う．
                needs = job.raw.get('needs', [])
                # needs: buildのような単一文字列を，複数指定と同じリスト形式へそろえる．
                if isinstance(needs, str):
                    # 依存名1個をリストへ包み，後続のループで文字単位に走査しないようにする．
                    needs = [needs]
                for dep in needs:
                    # 同じ元job名のmatrix展開先をすべてneedsへ結ぶ．
                    # 待機条件はbuildでPCの終端集合へ変換する．
                    matches = [j.id for j in run.jobs if j.original == dep]
                    # 指定した元job名が見つからなければ，依存先を無視せず入力エラーにする．
                    if not matches:
                        raise base.WorkflowError('missing needs job: ' + str(dep))
                    # 同じ元job名のmatrix展開先をすべて依存先へ追加し，1つだけを待つことを避ける．
                    job.needs.extend(matches)
        # 各Jobから依存先への辺を辞書にし，循環検査で再帰的にたどれる形にする．
        graph = {j.id: j.needs for j in self.jobs}

        def visit(jid, active):
            """needs graphの現在の経路に同じjobが現れるか調べる．"""
            # 現在の再帰経路へ戻った場合はneedsの循環なので，終端待機できない入力として拒否する．
            if jid in active:
                raise base.WorkflowError('needs cycle')
            # 現在Jobの依存辺をすべてたどり，経路集合によって循環を検査する．
            for dep in graph[jid]:
                # 訪問済み経路へ現在のJobを加えて依存先をたどり，別の探索経路の集合は変更しない．
                visit(dep, active | {jid})
        # matrix展開後の各Jobを走査し，現在のJobに属する設定・step・状態だけを処理する．
        for job in self.jobs:
            # 各Jobを起点として循環を調べ，空集合から始めて経路を混同しないようにする．
            visit(job.id, set())

    # jobの実行位置・成否・outputと，保存候補ごとのArtifact/Cache slotを先に宣言する．
    def _declare(self):
        """jobのPC，失敗・取消・stage履歴，step outcome/conclusionを宣言する．"""
        # matrix展開後の各Jobを走査し，現在のJobに属する設定・step・状態だけを処理する．
        for job in self.jobs:
            # PCは「次にどのstepへ進むか」を表すVAR．
            # pending→s0…→成功/失敗/skip/取消の値を持つ．
            pc = 'pc_' + job.id
            self.ts.var(
                pc,
                ['pending', 'success', 'failure', 'skipped', 'cancelled'] + [f's{i}' for i in range(len(job.steps) + 1)],
                'pending',
                job.source
            )
            # 失敗履歴を初期値FALSEのVARへ宣言し，後続の暗黙success()の判定に使う．
            self.ts.var('failed_' + job.id, ['FALSE', 'TRUE'], 'FALSE', job.source)
            # 取消履歴を別のVARへ宣言し，失敗と取消を区別して状態を進める．
            self.ts.var('cancelled_' + job.id, ['FALSE', 'TRUE'], 'FALSE', job.source)
            # taintedな内容がgit addされた履歴を保持し，後のgit pushの依存条件へ使う．
            self.ts.var('staged_' + job.id, ['FALSE', 'TRUE'], 'FALSE', job.source)
            # 0始まりの番号とstepを組にし，YAMLのstep順をPCのs0，s1等へ対応付ける．
            for i, step in enumerate(job.steps):
                # 元の処理結果outcomeと，continue-on-error適用後のconclusionを別VARにする．
                outcome = self.outcome(job, step)
                # stepの元の結果をunsetから始め，成功・失敗・skip等を後段で更新する．
                self.ts.var(
                    outcome,
                    ['unset', 'success', 'failure', 'skipped', 'cancelled'],
                    'unset',
                    step.source
                )
                # continue-on-error適用後の結果を別に保持し，元outcomeを失わないようにする．
                self.ts.var(
                    outcome + '_conclusion',
                    ['unset', 'success', 'failure', 'skipped', 'cancelled'],
                    'unset',
                    step.source
                )
                # 単純Shell契約で認識した操作tupleを，元のscriptと同じ順序で取り出す．
                for op in step.ops:
                    if op[0] == 'output':
                        self.output(job, step.id, op[1], ['', op[2]])

    # 動的な値の候補は事前に列挙し，実行時に同じ共有変数の条件で選ぶ．
    def _declare_objects(self):
        """動的name/keyとpathの有限候補から，保存objectのslotを宣言する．"""
        # matrix展開後の各Jobを走査し，現在のJobに属する設定・step・状態だけを処理する．
        for job in self.jobs:
            # 0始まりの番号とstepを組にし，YAMLのstep順をPCのs0，s1等へ対応付ける．
            for i, step in enumerate(job.steps):
                # uses/runから確定した限定効果名を読み，対応するActionまたはShellの遷移生成へ分岐する．
                action = step.contract
                if action in {'actions/upload-artifact-v4', 'actions/cache/save-v4', 'actions/cache-v4'}:
                    # Actionのwithキーを辞書として取り出し，省略時は空辞書で既定入力を使う．
                    inputs = step.raw.get('with', {})
                    # upload-artifactならArtifact，cache系ならCacheとして保存物の種類を分ける．
                    kind = 'artifact' if 'upload-artifact' in action else 'cache'
                    # ArtifactのnameとCacheのkeyを，共通の保存候補生成へ対応付ける．
                    key_field = 'name' if kind == 'artifact' else 'key'
                    # 名前・keyの元の値を読み，Artifactの省略名にはartifact，Cacheの省略keyには空文字を使う．
                    raw_key = inputs.get(key_field, 'artifact' if kind == 'artifact' else '')
                    # 値とpathの組を列挙し，同じ組の成立条件をまとめる．
                    # 実行時の相関はguardに残る．
                    # name/keyの埋込み式を有限値とguardへ展開し，実行時の参照との相関を保つ．
                    alternatives = self.template(raw_key, job, step)
                    # 保存・復元先pathの有限候補と，それぞれを選ぶ条件を求める．
                    paths = self.action_paths(inputs.get('path', '.'), job, step)
                    grouped = {}
                    # productはkey候補とpath候補の直積．
                    # 両方のguardを残すので，同じenv/outputの相関を捨てない．
                    for (name, key_guard), (path, path_guard) in product(alternatives, paths):
                        name = ex.string(name)
                        # 空の名前・keyと512文字を超えるCache keyを，保存候補の宣言から除く．
                        if not name or (kind == 'cache' and len(name) > 512):
                            continue
                        # setdefaultで同じ(name,path)のlistを一度だけ作り，成立条件をそこへまとめる．
                        grouped.setdefault((name, path), []).append(AND(key_guard, path_guard))
                    # 同じ名前・pathへまとまった条件を走査し，候補ごとに状態変数を1個だけ作る．
                    for (name, path), guards in grouped.items():
                        # job・step・種類・名前・pathを同一性に含め，別の保存候補と同じ状態を共有しないようにする．
                        token = symbol('object', f'{job.id}:{step.id}:{kind}:{name}:{path}')
                        # 候補の識別子と，その候補が有効になるguard・保存元・sourceを1つの辞書へまとめる．
                        obj = dict(
                            token=token,
                            job=job,
                            step=step,
                            index=i,
                            name=name,
                            guard=OR(guards),
                            kind=kind,
                            path=path,
                            source=step.source
                        )
                        # 保存前はabsent，保存後はclean/taintedになる抽象objectのVARを宣言する．
                        self.ts.var(token, ['absent', 'clean', 'tainted'], 'absent', step.source)
                        # Cacheだけに保存順の有限値を宣言し，同じ探索順位の候補から新しいものを選ぶために使う．
                        self.ts.var(
                            'age_' + token,
                            [str(i) for i in range(self.clock_modulus)],
                            '0',
                            step.source
                        ) if kind == 'cache' else None
                        # 条件式でArtifact用・Cache用のリストを選び，宣言した候補をその一覧へ追加する．
                        (self.objects if kind == 'artifact' else self.caches).append(obj)
                        if action == 'actions/cache-v4':
                            # combined Cacheがrestore時点で選んだkey/path条件を保持し，post-saveで別候補へ変わらないようにする．
                            self.ts.var('captured_' + token, ['FALSE', 'TRUE'], 'FALSE', step.source)
                    if kind == 'artifact':
                        # 保存候補の抽象tokenをartifact-idの候補へ登録し，実サービスの数値IDとは区別する．
                        self.output(
                            job,
                            step.id,
                            'artifact-id',
                            ['', *[o['token'] for o in self.objects if o['job'] is job and o['step'] is step]]
                        )
                        # runtime digestはここで計算せず，不明を表す記号を候補へ登録する．
                        self.output(job, step.id, 'artifact-digest', ['', 'runtime-digest-unknown'])
                if action in {'actions/cache-v4', 'actions/cache/restore-v4'}:
                    # cache-hitの未取得の空文字・exact hitのtrue・prefix hitのfalseを別の値として宣言する．
                    self.output(job, step.id, 'cache-hit', ['', 'true', 'false'])
                    # combined Cacheのpost-saveを抑制するため，exact hitが起きた履歴を別のVARへ保持する．
                    self.ts.var(
                        'cache_exact_' + self.outcome(job, step),
                        ['FALSE', 'TRUE'],
                        'FALSE',
                        step.source
                    )
        # Cache候補がある場合だけ保存順clockを宣言し，Artifactだけの入力では不要なVARを増やさない．
        if self.caches:
            # 候補slotは高々1回作成する有界モデル．
            # 保存順をageへ写すclockを用意する．
            self.ts.var('save_clock', [str(i) for i in range(self.clock_modulus)], '0')

    # 同じjob・step・output名には，同じ状態変数と値候補を使う．
    def output(self, job, sid, name, values=None):
        """job・step・output名に対応する共有の状態変数と候補値を得る．"""
        # job・step・output名を辞書のキーにし，同じoutput参照に同じVARを返す．
        lookup = (job.id, sid, name)
        # 初めて現れたoutputだけを新規宣言し，既存参照の状態を作り直さない．
        if lookup not in self.outputs:
            # 候補が未指定・空なら，未設定outputを表す空文字を最低1つ用意する．
            values = values or ['']
            # outputの参照キーから，SMVの列挙状態変数に使える安定した名前を作る．
            sym = symbol('out', ':'.join(lookup))
            # 候補の重複をdict.fromkeysで除き，識別子と可変domainの組を共有表へ格納する．
            self.outputs[lookup] = (sym, list(dict.fromkeys(values)))
            # 共有outputを有限enumのVARとして宣言し，未設定を表す空文字で初期化する．
            self.ts.var(sym, [atom(v) for v in self.outputs[lookup][1]], atom(''), job.source)
        # 既存outputに追加候補が渡された場合だけ，元のdomainを拡張する．
        elif values:
            sym, domain = self.outputs[lookup]
            # slice代入はlist自体を置換せず内容を更新する．
            # 他の登録表が同じdomainを参照していても値を共有できる．
            domain[:] = list(dict.fromkeys(domain + values))
            # 共有domainに合わせてVAR宣言も更新し，候補1個の場合は定数化を避ける補助値を加える．
            self.ts.variables[sym] = (
                [atom(v) for v in domain] + ([atom('__sentinel__')] if len(domain) == 1 else []),
                atom('')
            )
        # 識別子とdomainを返し，参照側が「値がvならVAR=atom(v)」という条件を作れるようにする．
        return self.outputs[lookup]

    @staticmethod
    def outcome(job, step):
        """stepの元の成否を記録するSMV変数名を作る．"""
        return symbol('outcome', job.id + ':' + step.id)

    # job内の同じpathを，absent/clean/taintedの状態を持つ同じfile slotへ対応付ける．
    def file(self, job, path):
        """jobとpathに対応するfile状態slotを取得・宣言する．"""
        # literalでないpathは診断を残すが，この関数では続けて正規化とslot取得を行う．
        if not isinstance(path, str) or any((c in path for c in '${}*?!\n~')):
            self.diagnostic(
                'file-path',
                'only normalized literal workspace paths are modeled: ' + repr(path),
                job.source
            )
        # ./と末尾/を除き，同じworkspaceパスを別のfile slotとして登録しないようにする．
        path = str(path).removeprefix('./').rstrip('/') or '.'
        # workspaceはJob間で共有せず，同じパス名でもJobが違えば別の状態として管理する．
        lookup = (job.id, path)
        # 初めて使うjob・pathの組だけをabsent初期値で宣言する．
        if lookup not in self.files:
            # Jobと正規化pathからfile記号を作り，同じworkspace位置に同じVARを使う．
            sym = symbol('file', job.id + ':' + path)
            # fileはabsentから始め，checkout/write/restoreに応じてcleanまたはtaintedへ変わる．
            self.ts.var(sym, ['absent', 'clean', 'tainted'], 'absent', job.source)
            # 宣言したfileの記号を登録し，以後のread/write/restoreで共通のVARを参照する．
            self.files[lookup] = sym
        return self.files[lookup]

    # 単一pathの有限候補．
    # 複数pathのbundleは同じ意味にはできないので，明示診断を残す．
    def action_paths(self, raw, job, step):
        """Actionのpath式を，条件付きの単一path候補へ展開する．"""
        # path内のGitHub式を有限候補へ展開し，それぞれの成立条件も保持する．
        values = self.template(raw, job, step)
        # 対応できる単一pathの候補とguardをためるリストを作る．
        paths = []
        # 各path候補を文字列化・正規化し，複数pathやglob等を除外する．
        for value, guard in values:
            # path候補が空文字なら，有効な保存・復元先として追加しない．
            if not ex.string(value):
                continue
            # 型を文字列へそろえて./と末尾/を除き，空になればworkspaceの.へ正規化する．
            path = ex.string(value).removeprefix('./').rstrip('/') or '.'
            # 改行区切りの複数path・glob・tildeを単一pathとして解釈せず，診断して候補から除く．
            if '\n' in path or '\r' in path or any((c in path for c in '*?[]~')):
                self.diagnostic(
                    'action-path',
                    'multi-path/glob/tilde layouts require a bundle contract',
                    step.source
                )
                continue
            # 正規化後のpathと，その値が成立する元のguardを一緒に保存する．
            paths.append((path, guard))
        return paths

    def action_path(self, raw, job, step):
        """pathがただ1種類に決まる場面で，そのpathを取得する．"""
        # 保存・復元先pathの有限候補と，それぞれを選ぶ条件を求める．
        paths = self.action_paths(raw, job, step)
        # 異なるpath候補が複数，または候補がない場合は，単一静的pathとして扱えないことを診断する．
        if len({v for v, g in paths}) != 1:
            self.diagnostic('action-path', 'a single static path was required at this site', step.source)
            return 'unsupported-path'
        return paths[0][0]

    # success/failure/cancelled関数が参照する，jobの現在の失敗・取消条件を返す．
    def statuses(self, job, step=None):
        """status関数のSMV条件をjob/stepの段階に合わせて返す．"""
        # matrix展開後の依存先識別子を取り出し，status関数の条件生成に使う．
        deps = [d for d in job.needs]
        # step位置では当該jobが失敗・取消していないこと，job開始時では全needsが成功したことを要求する．
        success = AND('!failed_' + job.id, '!cancelled_' + job.id) if step else AND(*(f'pc_{d} = success' for d in deps))
        # 自jobの失敗履歴とneeds先のfailureをORにし，どれかが失敗した条件を作る．
        failure = OR([f'failed_{job.id}', *[f'pc_{d} = failure' for d in deps]])
        return dict(success=success, failure=failure, cancelled='cancelled_' + job.id)

    # github・steps・needs・env等の参照を「値候補，成立するSMV条件」へ変換する．
    # envはworkflow→job→stepの順で上書きする．
    # 未知contextの相関は共有変数で保持する．
    def resolve(self, ref, job, step, resolving=None):
        """context参照を共有状態と成立条件に結び付ける．"""
        # Jobが保持するrun番号を添字にして，所属workflowと外部contextを取得する．
        run = self.runs[job.run]
        # YAMLだけでは決まらないイベント・repository等を，対応するRunのscenarioから参照する．
        ctx = run.context
        # github/runner/strategyの固定contextを値へ対応付け，後続の解決分岐でSMV条件TRUEの候補として返す．
        constants = {
            'github.event_name': ctx['event'],
            'github.ref_name': ctx.get('refName',ctx['branch']),
            'github.ref': ctx['scope'],
            'github.repository': ctx['repository'],
            'github.run_id': run.id,
            'github.workflow': run.raw.get('name', run.id),
            'github.event.workflow_run.id': ctx.get('upstreamRun', ''),
            'github.event.workflow_run.name': ctx.get('upstreamName', ''),
            'runner.temp': '/tmp/runner',
            'github.workspace': '/workspace',
            'runner.os': ctx.get('runnerOS', 'Linux'),
            'runner.arch': ctx.get('runnerArch', 'X64'),
            'strategy.job-index': [j for j in run.jobs if j.original == job.original].index(job),
            'strategy.job-total': len([j for j in run.jobs if j.original == job.original])
        }
        if ctx.get('externalProfile'):
            # PRのhead/baseと実行refを分け，event固有の権限・taintとは同じprofileで相関させる．
            constants.update({
                'github.event.action': ctx['activity'],
                'github.ref_type': ctx['refType'],
                'github.event.pull_request.head.ref': ctx.get('headBranch','') if ctx['event'] in {'pull_request','pull_request_target'} else '',
                'github.event.pull_request.base.ref': ctx.get('baseBranchName','') if ctx['event'] in {'pull_request','pull_request_target'} else '',
                'github.head_ref': ctx.get('headBranch','') if ctx['event'] in {'pull_request','pull_request_target'} else '',
                'github.base_ref': ctx.get('baseBranchName','') if ctx['event'] in {'pull_request','pull_request_target'} else '',
                'github.event.pull_request.head.repo.fork': ctx['fork'],
                'github.event.pull_request.head.repo.full_name': ctx.get('headRepository',ctx['repository']),
                'github.event.pull_request.base.repo.full_name': ctx['repository'],
                'github.event.repository.default_branch': ctx['defaultBranch'].removeprefix('refs/heads/'),
            })
            if ref in {'github.event.repository.private','github.event.repository.visibility'}:
                # repositoryの公開範囲も，forkへのwrite token条件と同じ固定記号を参照する．
                token=symbol('repository_visibility',ctx['repository'])
                values=[ctx['repositoryVisibility']] if 'repositoryVisibility' in ctx else ['public','private','internal']
                self.ts.frozen[token]=values
                return [(value!='public' if ref.endswith('.private') else value,token+' = '+value) for value in values]
            from .external_conditions import runner_labels
            os_name,arch_name = runner_labels(job.raw.get('runs-on'),job.matrix)
            constants['runner.os'] = os_name or ctx['runnerOS']
            constants['runner.arch'] = arch_name or ctx['runnerArch']
        # 通常のcontext参照と区別したhashFiles専用参照なら，現在のfile状態からhash候補を求める．
        if ref.startswith('__hashFiles.'):
            return self.hash_values(ref[len('__hashFiles.'):], job, step)
        # 固定contextなら候補は1個であり，成立条件TRUEとして返す．
        if ref in constants:
            return [(constants[ref], 'TRUE')]
        # 上流runのcontextとPCを参照する．
        # 完了前のconclusionは空文字とし，完了後を集約する．
        if ref.startswith('github.event.workflow_run.'):
            # run idが一致する上流Runをgeneratorから最初の1個だけ取り出し，見つからなければNoneとする．
            upstream = next((r for r in self.runs if r.id == str(ctx.get('upstreamRun'))), None)
            if upstream and ref == 'github.event.workflow_run.event':
                return [(upstream.context['event'], 'TRUE')]
            if upstream and ref == 'github.event.workflow_run.head_branch':
                return [(upstream.context.get('headBranch',upstream.context['branch']), 'TRUE')]
            if upstream and ref == 'github.event.workflow_run.conclusion':
                # 上流run内の全Jobが成功・失敗・取消・skipのいずれかへ達した条件を作る．
                terminal = AND(*(f'pc_{j.id} in {{ success, failure, cancelled, skipped }}' for j in upstream.jobs))
                # 上流Jobのどれかがfailureになった条件を，workflow_runの集約結果の最優先に使う．
                failed = OR((f'pc_{j.id} = failure' for j in upstream.jobs))
                # 上流Jobの取消を検出し，failureがない時のconclusionをcancelledへする条件に使う．
                cancelled = OR((f'pc_{j.id} = cancelled' for j in upstream.jobs))
                # 上流Jobがすべてskippedかを求め，failure・cancelled以外の終端結果を区別する．
                skipped = AND(*(f'pc_{j.id} = skipped' for j in upstream.jobs))
                return [
                    ('failure', AND(terminal, failed)),
                    ('cancelled', AND(terminal, '!' + failed, cancelled)),
                    ('skipped', AND(terminal, '!' + failed, '!' + cancelled, skipped)),
                    ('success', AND(terminal, '!' + failed, '!' + cancelled, '!' + skipped)),
                    ('', '!' + terminal)
                ]
        # matrixの軸名が割当てにあれば，この展開Jobに固定した値を返す．
        if ref.startswith('matrix.') and ref[7:] in job.matrix:
            return [(job.matrix[ref[7:]], 'TRUE')]
        # steps.id.outputs.nameは共有VARへ，outcome/conclusionはそのstepの成否VARへ結び付ける．
        if ref.startswith('steps.'):
            # steps.id.outputs.nameとsteps.id.outcome/conclusionの登録形を，参照文字列全体に照合する．
            match = re.fullmatch(r'steps\.([\w-]+)\.(outputs\.([\w-]+)|outcome|conclusion)', ref)
            if match:
                # steps.build.outputs.sha等をstep名・参照種別・output名に分解する．
                sid, field, output = match.groups()
                if field.startswith('outputs.'):
                    # 実行時Artifact digestと内容の相関は未実装なので，通常の文字列比較として許可しない．
                    if output == 'artifact-digest':
                        raise ex.UnsupportedExpression('runtime artifact digest comparisons require a correlated digest contract')
                    sym, domain = self.output(job, sid, output)
                    # 存在しないstepへの参照は空の候補だけで安全と解釈せず，診断も残す．
                    if not any((s.id == sid for s in job.steps)):
                        self.diagnostic('missing-step-reference', ref, step.source if step else job.source)
                    return [(v, f'{sym} = {atom(v)}') for v in domain]
                # 同じjobから結果を参照するstepを探し，outcome/conclusionの記号を得る準備をする．
                writer = next((s for s in job.steps if s.id == sid), None)
                if writer:
                    sym = self.outcome(job, writer) + ('_conclusion' if field == 'conclusion' else '')
                    return [(v if v != 'unset' else '', f'{sym} = {v}') for v in ['unset', 'success', 'failure', 'skipped', 'cancelled']]
        # needsに含まれる単一jobのoutputだけを解決する．
        # matrixの完了順集約は診断して省略する．
        if ref.startswith('needs.'):
            # needs.job.resultまたはneeds.job.outputs.nameを照合し，参照先Jobと属性を分ける．
            match = re.fullmatch(r'needs\.([\w-]+)\.(result|outputs\.([\w-]+))', ref)
            if match:
                # needs.build.outputs.sha等を依存job名・参照種別・output名へ分ける．
                name, field, output = match.groups()
                # 元のjob名とneedsの両方に一致する展開先を選び，無関係なJobのoutputを参照しない．
                deps = [j for j in run.jobs if j.original == name and j.id in job.needs]
                # 依存先が1個なら解決し，matrixで複数なら完了順によるoutput集約が未対応として診断する．
                if len(deps) == 1:
                    dep = deps[0]
                    if field == 'result':
                        return [(v, f'pc_{dep.id} = {v}') for v in ['success', 'failure', 'skipped', 'cancelled']]
                    raw = dep.raw.get('outputs', {}).get(output, '')
                    values = self.template(raw, dep, None)
                    # needs outputは依存Jobの完了後だけ有効とし，完了前・skip時は空文字の候補を返す．
                    done = f'pc_{dep.id} in {{ success, failure, cancelled }}'
                    return [(v, AND(done, g)) for v, g in values] + [('', '!(' + done + ')')]
                self.diagnostic(
                    'needs-matrix-output',
                    'needs outputs of multiple matrix jobs require completion-order semantics',
                    job.source
                )
        # env.NAMEは固定contextと分け，優先順位と再帰参照を管理する解決処理へ渡す．
        if ref.startswith('env.'):
            # env.の4文字を除き，環境変数の名前だけを優先順位付き解決へ渡す．
            name = ref[4:]
            # envの再帰参照を検出する集合を作り，呼出し元の集合は直接書き換えない．
            resolving = set(resolving or ())
            # 同じenv参照へ戻ったら循環なので，無限再帰せず未対応式の例外にする．
            if ref in resolving:
                raise ex.UnsupportedExpression('cyclic env binding ' + ref)
            return self.environment_values(job, step, name, resolving | {ref})
        # 明示された有限domainには共有FROZENVARを使う．
        # 同じ参照は実行途中で別の候補へ勝手に変わらない．
        if ref in ctx.get('domains', {}):
            # 明示的に与えられた有限候補列を使い，勝手に別の候補を補わない．
            values = ctx['domains'][ref]
            # 空でないlistを有限domainとして要求し，不正な指定は未対応式にする．
            if not isinstance(values, list) or not values:
                raise ex.UnsupportedExpression('finite context domain must be nonempty')
            sym = symbol('ctx', run.id + ':' + ref)
            # 同じrun・参照に同じ候補を固定するFROZENVARを宣言し，式内やstep間で値が矛盾しないようにする．
            self.ts.frozen[sym] = list(dict.fromkeys((atom(v) for v in values)))
            return [(v, f'{sym} = {atom(v)}') for v in values]
        # dispatchのchoice/booleanは有限に列挙できる．
        # 自由文字列や未知varsは代表値だけなのでunsupportedを残す．
        if ref.startswith(('inputs.', 'vars.')):
            # inputs.nameまたはvars.nameを，最初の.だけで種別と名前へ分ける．
            kind, name = ref.split('.', 1)
            # scenarioに値が明示されていれば，YAMLの入力宣言からの推論より優先して返す．
            if name in ctx.get(kind, {}):
                return [(ctx[kind][name], 'TRUE')]
            # YAMLのonを読み，単一文字列・リスト・辞書を後段が扱えるイベント辞書へそろえる．
            events = run.raw.get('on', {})
            # onが辞書ならworkflow_dispatch設定を読み，それ以外では入力宣言なしの空辞書にする．
            config = events.get('workflow_dispatch') or {} if isinstance(events, dict) else {}
            # inputsならdispatchの入力宣言を読み，varsにはその宣言がないためNoneを使う．
            declaration = config.get('inputs', {}).get(name) if kind == 'inputs' else None
            # 宣言されていないinputsの参照には空文字を返す．
            if kind == 'inputs' and declaration is None:
                return [('', 'TRUE')]
            # 宣言がnullの場合も空辞書へそろえ，以後のtype/default参照でgetを共用できるようにする．
            declaration = declaration or {}
            if kind == 'inputs' and declaration.get('type') == 'boolean':
                # boolean入力は有限なので，偽と真の2候補を正確に列挙する．
                values = [False, True]
            elif kind == 'inputs' and declaration.get('type') == 'choice' and declaration.get('options'):
                # choice入力はoptionsの順を保って重複を除き，その候補を共有する．
                values = list(dict.fromkeys(declaration['options']))
            else:
                # 自由文字列やvarsは空文字・既定値・代表値で近似し，完全なdomainではない診断を残す．
                values = list(dict.fromkeys(['', declaration.get('default', ''), 'symbolic-other']))
                self.diagnostic(
                    'context-domain',
                    'unbounded ' + ref + ' needs an explicit finite scenario domain',
                    step.source if step else job.source
                )
            # inputsはrun単位，varsはrepository単位で同じ未知値を共有する識別元を選ぶ．
            owner = run.id if kind == 'inputs' else ctx['repository']
            # 選んだ共有単位と参照名から，固定context値のSMV識別子を作る．
            token = symbol('ctx', owner + ':' + ref)
            # 有限候補をFROZENVARへ登録し，1つの実行中にinputs/varsの値が勝手に変わらないようにする．
            self.ts.frozen[token] = [atom(v) for v in values]
            return [(v, f'{token} = {atom(v)}') for v in values]
        raise ex.UnsupportedExpression('no complete finite domain for ' + ref)

    # 式の構文木を，contextの共有参照を保った有限候補へ変換する．
    def expression(self, raw, job, step=None, resolving=None):
        """構文木を作り，hashFilesを参照へ置換して有限値へ下げる．"""
        # 式をASTへ解析し，hashFiles呼出しを共有contextとして解決できる専用refへ置き換える．
        node = self.lower_hash_calls(ex.parse(raw), job, step)
        # lambdaで参照の解決処理を渡し，有限候補の直積を上限内で評価して値とSMV条件を得る．
        return ex.lower(
            node,
            lambda r: self.resolve(r, job, step, resolving),
            self.statuses(job, step),
            limit=self.scenario.get('bounds', {}).get('expressionProducts', 8192)
        )

    # literalと${{ ... }}が混在する文字列を組み立てる．
    # 候補に付く条件も引き継ぐ．
    def template(self, raw, job, step=None, resolving=None):
        """literalと式が混ざった値を条件付きで組み立てる．"""
        # 数値・Boolean等は文字列へ強制変換せず，その型のまま条件TRUEの候補にする．
        if not isinstance(raw, str):
            return [(raw, 'TRUE')]
        # ${{ ... }}の出現位置を列挙し，literal部分と式部分を分けて組み立てる．
        matches = list(re.finditer(r'\$\{\{(.*?)}}', raw))
        # 指定した元job名が見つからなければ，依存先を無視せず入力エラーにする．
        if not matches:
            return [(raw, 'TRUE')]
        try:
            # 全体が式1個なら，周囲の文字列連結をせず式が返す型を保持する．
            if len(matches) == 1 and matches[0].span() == (0, len(raw)):
                return self.expression(matches[0].group(1), job, step, resolving)
            # 連結済みの候補を空文字から始め，次に読むliteral部分の開始位置を0にする．
            result, offset = ([('', 'TRUE')], 0)
            # 各埋込み式を左から評価し，既存文字列候補へliteral部分と式の値を順に連結する．
            for match in matches:
                # 埋込み式1個の値候補と，その候補が成立する条件を求める．
                values = self.expression(match.group(1), job, step, resolving)
                # 内包表記で既存候補と次の式候補を結合する．
                # 値だけでなく，その値が成立する条件もANDで引き継ぐ．
                result = [(s + raw[offset:match.start()] + ex.string(v), AND(g, h)) for s, g in result for v, h in values]
                # 次の式との間にあるliteralを二重に足さないよう，読取り位置を現在の式の末尾へ進める．
                offset = match.end()
            return [(s + raw[offset:], g) for s, g in result]
        except (ex.UnsupportedExpression, KeyError, TypeError, ValueError) as exc:
            self.diagnostic('expression', str(exc), step.source if step else job.source)
            return [('unsupported-value', 'TRUE')]

    # status関数がないifには，暗黙のsuccess条件を加える．
    # 未対応式は診断付き未知条件にする．
    def condition(self, raw, job, step=None):
        """job/stepのifを実行条件のSMV式へ変換する．"""
        # ifを省略した場合はsuccess()相当を使い，失敗・取消後に通常stepが無条件で動かないようにする．
        if raw is None:
            return self.statuses(job, step)['success']
        try:
            # ifをASTへ解析し，status関数が明示されているかを木の中で確認する．
            node = ex.parse(raw)
            # ASTを走査してstatus関数の明示を調べる．
            # anyは少なくとも1つあればTrueになる．
            has_status = any((n.kind == 'call' and n.value in {'always', 'success', 'failure', 'cancelled'} for n in ex.walk(node)))
            # 式の値がtruthyになる候補だけを選び，その成立条件をORでまとめる．
            predicate = OR((g for v, g in self.expression(raw, job, step) if ex.truth(v)))
            # always()/failure()等が明示されていればその意味を優先し，なければ暗黙のsuccess()もANDする．
            return predicate if has_status else AND(self.statuses(job, step)['success'], predicate)
        except (ex.UnsupportedExpression, KeyError, TypeError, ValueError) as exc:
            return self.unknown((job.id, str(raw)), str(exc), step.source if step else job.source)

    # schedulerがこのjobを選び，PCが対象stepにある場合だけ処理を進める．
    def gate(self, job, index):
        """対象stepを進めるschedulerの基本条件を作る．"""
        # actorがこのJobを選び，PCがこのstep位置で，取消要求がない時だけ実行可能にする．
        return AND(f'actor = {job.id}', f'pc_{job.id} = s{index}', '!cancel_request')

    # イベントfilterと，workflow_runが待つ上流runの完了条件を組み合わせる．
    def _enabled(self, run):
        """event filterと上流run完了から，runを開始できる条件を作る．"""
        # 最初のworkflowのrunは開始済みとし，Job自身のif/needsは別のguardで検査する．
        if run.context.get('assumeStarted'):
            return 'TRUE'
        # YAMLのonを読み，単一文字列・リスト・辞書を後段が扱えるイベント辞書へそろえる．
        events = run.raw.get('on', {})
        if isinstance(events, str):
            # on: pushを{'push': None}相当へ包み，filterなしのイベント指定を表す．
            events = {events: None}
        elif isinstance(events, list):
            # on: [push, pull_request]を各イベントがキーの辞書へ変換する．
            events = dict.fromkeys(events)
        try:
            context = dict(run.context)
            if context.get('externalProfile') and context['event']=='workflow_run':
                # 後続のbranches filterは，Consumer自身でなく選ばれた上流のhead branchへ適用する．
                upstream = next((r for r in self.runs if r.id==str(context.get('upstreamRun'))),None)
                if upstream:
                    context['branch']=upstream.context.get('headBranch',upstream.context['branch'])
            # rootは整合するtriggerで開始済み．後続の起動可否は従来の規則を条件ごとに評価する．
            enabled = 'TRUE' if context.get('assumeStarted') else 'TRUE' if pol.trigger(events, context) else 'FALSE'
        except (pol.UnsupportedPolicy, KeyError, TypeError) as exc:
            enabled = self.unknown((run.id, 'trigger'), str(exc))
        # scenarioで明示された実行順制約の上流run idを読む．
        after = run.context.get('after')
        # workflow_runでは通常のafter指定に代えてイベントの上流runを待ち，completed以外の意味は診断する．
        if run.context['event'] == 'workflow_run':
            # workflow_runの場合は完了を待つ上流run idをイベント文脈から読む．
            after = run.context.get('upstreamRun')
            # requested/in_progressの順序意味は生成していないため，completed以外は診断する．
            if run.context.get('activity') != 'completed':
                self.diagnostic('workflow-run-activity', 'only completed workflow_run ordering is modeled')
        # 待機するrunの指定がある場合だけ，そのRunの全Job完了条件を開始条件へ追加する．
        if after:
            # 供給されたRun一覧から待機対象を探し，未供給なら未知条件付きの診断へ回す．
            prior = next((r for r in self.runs if r.id == str(after)), None)
            if prior:
                # 元のイベントfilterに加え，上流runの全Jobが終端状態へ達することを要求する．
                enabled = AND(
                    enabled,
                    *(f'pc_{j.id} in {{ success, failure, skipped, cancelled }}' for j in prior.jobs)
                )
            else:
                enabled = AND(enabled, self.unknown((run.id, 'after'), 'upstream run not supplied'))
        return enabled

    # Cache versionはscenarioの同一性仮定を使う．
    # 実サービスのversionを捏造しない．
    def _version(self, obj):
        """scenarioで主張されたCache versionの同一性tokenを読む．"""
        # 保存候補が所属するJobとStepを取り出し，version解決に必要なwithとcontextを読む．
        job, step = (obj['job'], obj['step'])
        # 保存元Runの外部条件を使い，同じpathに明示されたopaque versionを参照する．
        ctx = self.runs[job.run].context
        # Actionのwithキーを辞書として取り出し，省略時は空辞書で既定入力を使う．
        inputs = step.raw.get('with', {})
        # cross-OS archiveの互換性は生成していないため，指定されていれば未対応を記録する．
        if inputs.get('enableCrossOsArchive') not in (None, False, 'false'):
            self.diagnostic(
                'cross-os-cache',
                'cross-OS cache archive compatibility is outside the contract',
                step.source
            )
        # scenarioが主張するversion同一性tokenであり，実サービスのhash値ではない．
        return ctx.get('cacheVersion', {}).get(str(obj['path']), 'unasserted')

    # job/workflowの指定または外部条件から，実効的なCache modeを選ぶ．
    def _cache_mode(self, job):
        """job/workflow/scenarioの順で実効Cache modeを得る．"""
        # Jobが保持するrun番号を添字にして，所属workflowと外部contextを取得する．
        run = self.runs[job.run]
        # cache-modeはjob指定，workflow指定，scenarioの順で優先し，外部条件と構文を区別して読む．
        mode = job.raw.get('cache-mode', run.raw.get('cache-mode', run.context.get('cacheMode')))
        # 現行policyでmodeが未指定の場合だけ，イベント種別から既定のread/writeを選ぶ．
        if self.policy == 'github-current-2026-10-22' and mode is None:
            mode = 'write' if run.context['event'] in {'push', 'pull_request', 'workflow_dispatch', 'schedule'} else 'read'
        return mode

    # 明示権限・fork制限・repository既定値を合わせ，必要scopeを持つ条件を返す．
    def token_permission(self, job, scope, write=False):
        """必要scopeの権限が使える条件を作る．"""
        # Jobが保持するrun番号を添字にして，所属workflowと外部contextを取得する．
        run = self.runs[job.run]
        # YAMLだけでは決まらないイベント・repository等を，対応するRunのscenarioから参照する．
        ctx = run.context
        # fork固有のwrite制限をまだ課していない条件から始め，該当PRの場合だけ追加する．
        fork_gate = 'TRUE'
        # 通常は元contextを権限計算に渡し，fork PRの場合だけ別のcontextを作る．
        permission_context = ctx
        # pull_requestかつfork由来の場合に限り，write-tokenのrepository設定を追加条件へ分離する．
        if ctx['event'] == 'pull_request' and ctx['fork']:
            # 基本権限の計算とforkのwrite許可条件を分離するため，基本計算用contextの設定を補う．
            permission_context = {**ctx, 'sendWriteTokens': True}
            # forkへのwrite-token設定が不明なら，同じrepositoryで共有する固定の未知条件を用意する．
            if ctx.get('sendWriteTokens') is None:
                # repository名から未知設定の識別子を作り，Jobごとに別の設定を選ばせない．
                setting = symbol('fork_write_setting', ctx['repository'])
                self.ts.frozen[setting] = ['FALSE', 'TRUE']
                self.diagnostic(
                    'fork-write-policy',
                    'fork PR write-token setting was not supplied',
                    job.source,
                    'unknown'
                )
                # write要求には未知設定を要求し，read要求にはforkのwrite設定による追加制限を課さない．
                fork_gate = setting if write else 'TRUE'
            # forkへのwrite-tokenが明示的に無効なら，write権限の条件をFALSEにする．
            elif ctx.get('sendWriteTokens') is False and write:
                fork_gate = 'FALSE'
        # jobのpermissions優先・未指定scope・既定権限をpolicy関数で解決する．
        value = pol.permission(run.raw, job.raw, permission_context, scope)
        # 実効権限が確定した場合はread/write要求と照合し，fork条件も合わせて返す．
        if value is not None:
            return AND(fork_gate, str(value == 'write' if write else value in {'read', 'write'}).upper())
        # repositoryとscopeが同じなら，既定権限は同じ列挙変数を共有する．
        # 参照場所やjobごとに別々のwrite/readを選ばせないためである．
        # 不明な既定権限はrepository・scopeごとに同じ記号を使い，全Jobで相関を保つ．
        name = symbol('permission_default', run.context['repository'] + ':' + scope)
        # 未知の既定権限を固定enumとして表し，id-tokenはreadを持たない候補集合にする．
        self.ts.frozen[name] = ['perm_none', 'perm_read', 'perm_write'] if scope != 'id-token' else ['perm_none', 'perm_write']
        self.diagnostic(
            'permission-default',
            'effective ' + scope + ' permission depends on shared repository policy',
            job.source,
            'unknown'
        )
        return AND(fork_gate, name + ' = perm_write' if write else name + ' in { perm_read, perm_write }')

    # 保存時点のfile状態をobjectへ写す．
    # CacheのI/O成否はstep自体の成否と区別する．
    def _save(self, obj, guard):
        """file状態をArtifact/Cache objectへ保存する遷移を追加する．"""
        # 保存候補のJobを取り出し，そのworkspaceと権限・Cache modeを参照する．
        job = obj['job']
        # Jobが保持するrun番号を添字にして，所属workflowと外部contextを取得する．
        run = self.runs[job.run]
        # 保存対象の抽象object識別子を取得し，workspaceのfile記号と区別する．
        token = obj['token']
        # 保存元pathの存在・taintを表すVARを得て，保存時点の状態をobjectへ写す準備をする．
        source = self.file(job, obj['path'])
        # Cacheはstep成否とは別のI/O入力と書込みpolicyを要求する．
        # 保存失敗をstep全体の失敗と同一視しない．
        if obj['kind'] == 'cache':
            # Cache保存にはstep成功だけでなくCache I/O成功も要求し，通信失敗時の保存を防ぐ．
            guard = AND(guard, 'cache_io_ok')
            try:
                # Cache policyと実効modeから，このイベント・scopeで保存可能かを求める．
                writable = pol.cache_writable(run.context, self.policy, self._cache_mode(job))
                # 保存不可ならguardをFALSEにし，保存可能な場合は元の実行条件を保持する．
                guard = AND(guard, 'TRUE' if writable else 'FALSE')
            except pol.UnsupportedPolicy as exc:
                guard = AND(guard, self.unknown((job.id, 'cache-write-policy'), str(exc), obj['source']))
            # 同じrepository/scope/key/versionのslotが存在すれば，新しい同一Cacheを作らない条件へ加える．
            others = [o for o in self.caches if o['name'] == obj['name'] and self.runs[o['job'].run].context['repository'] == run.context['repository'] and (self.runs[o['job'].run].context['scope'] == run.context['scope']) and (self._version(o) == self._version(obj))]
            # 同一repository・scope・key・versionの既存slotがすべてabsentの場合だけ，新しいCacheを保存できるようにする．
            guard = AND(guard, *(o['token'] + ' = absent' for o in others))
            # 到達しないclock最大値でも式が定義されるようmodを使う．
            # 到達実行では有界な保存回数を前提とし，ageを実時刻とは扱わない．
            tick = '(save_clock + 1) mod ' + str(self.clock_modulus)
            # この候補が保存された遷移だけageを更新し，取得時の新旧比較に使う．
            self.ts.update('age_' + token, guard, tick)
            # 保存が起きた時だけ共通clockも進め，別候補間で保存順を比較できるようにする．
            self.ts.update('save_clock', guard, tick)
        else:
            if obj['step'].raw.get('with', {}).get('overwrite') not in (None, False, 'false'):
                self.diagnostic(
                    'artifact-overwrite',
                    'delete/recreate identity for overwrite is not implemented',
                    obj['source']
                )
            # 同じrun内で同名の別Artifact候補を集め，既存物との衝突条件を作る．
            duplicates = [o for o in self.objects if o is not obj and o['job'].run == job.run and (o['name'] == obj['name'])]
            # 同じrunの同名Artifactがまだ存在しないことを要求し，別objectへの黙った上書きを避ける．
            guard = AND(guard, *(o['token'] + ' = absent' for o in duplicates))
        # Artifactはroot配下のmemberを集約する．
        # 少なくとも対象がある条件でだけobjectへ写す．
        source, exists = self._bundle_source(obj, source)
        # 保存元が存在する条件を追加し，absentなworkspaceから保存物を生成しないようにする．
        guard = AND(guard, exists)
        # 保存時点のclean/tainted状態をobjectへ写し，以降のworkspace変更から切り離して保持する．
        self.ts.update(token, guard, source)
        # Artifact memberの内容と存在も保存時点の値へ更新し，後の復元先へ渡せるようにする．
        self._save_contents(obj, guard)
        # Artifactの場合だけartifact-id/digestのoutputを更新し，Cacheへ同じoutputを作らない．
        if obj['kind'] == 'artifact':
            sym, _ = self.output(job, obj['step'].id, 'artifact-id')
            # 保存が成立した場合だけ，artifact-id outputをこの保存候補の抽象tokenへ更新する．
            self.ts.update(sym, guard, atom(token))
            digest, _ = self.output(job, obj['step'].id, 'artifact-digest')
            # digest outputは未解決記号へ更新し，実ファイルのSHA-256を計算したと誤認しないようにする．
            self.ts.update(digest, guard, atom('runtime-digest-unknown'))

    # run/repositoryやCache scope・key・versionを照合し，候補の優先順位と選択を遷移へ表す．
    def _restore(self, job, step, guard):
        """復元先の有限path候補をたどり，いずれかが一致する条件を得る．"""
        # 保存・復元先pathの有限候補と，それぞれを選ぶ条件を求める．
        paths = self.action_paths(step.raw.get('with', {}).get('path', '.'), job, step)
        # このgeneratorの評価中に_restore_singleが遷移を登録する．
        # ORは列を走査して条件文字列を作る．
        return OR((AND(
            path_guard,
            self._restore_single(job, step, AND(guard, path_guard), path)
        ) for path, path_guard in paths))

    def _restore_single(self, job, step, guard, destination_path):
        """単一復元先について候補選択と状態更新を生成する．"""
        # 復元する側のRunとwith設定を取り出し，保存側のcontextと照合する準備をする．
        run, inputs = (self.runs[job.run], step.raw.get('with', {}))
        # 契約名からCacheかArtifactかを分け，探索規則と認証条件を切り替える．
        cache = 'cache' in step.contract
        # 復元先workspaceの状態変数を取得し，選ばれたobjectの状態を書き込む先にする．
        destination = self.file(job, destination_path)
        # 可視性・名前・version等を満たす保存候補と探索順位をためるリストを作る．
        candidates = []
        # Cache候補は可視scope・versionを確認し，(scope順位,key順位)を持たせる．
        # 保存ageは後で比較する．
        if cache:
            # restore-keysの埋込み式を評価し，改行区切りのprefix候補を元の成立条件とともに保持する．
            restore_keys = self.template(inputs.get('restore-keys', ''), job, step)
            # 主keyの各値候補と成立条件を走査し，restore-keys側の候補とも組み合わせる．
            for key, key_guard in self.template(inputs.get('key', ''), job, step):
                # restore-keysの有限候補ごとに，主keyとのguardを保持して保存物の照合へ進む．
                for prefixes, prefix_guard in restore_keys:
                    # 宣言済みCache候補をすべて調べ，scope・key順位・versionが合うものだけを集める．
                    for obj in self.caches:
                        try:
                            # 保存元と復元先のrepository・ref scopeをpolicyに従って照合する．
                            visible = pol.cache_visible(self.runs[obj['job'].run].context, run.context, self.policy)
                        except pol.UnsupportedPolicy as exc:
                            self.diagnostic('cache-policy', str(exc), step.source)
                            visible = False
                        try:
                            # exact key・key prefix・restore-keys順の探索順位を求め，非対応の照合は例外へ回す．
                            rank = pol.cache_rank(
                                obj['name'],
                                ex.string(key),
                                [p.strip() for p in str(prefixes).splitlines() if p.strip()]
                            )
                        except pol.UnsupportedPolicy as exc:
                            self.diagnostic('cache-key-collation', str(exc), step.source)
                            rank = None
                        # 復元先のpathを持つ仮のobject情報を作り，保存側と同じversion解決関数へ渡す．
                        current = dict(job=job, step=step, path=destination_path)
                        # 保存側のopaqueなCache version仮定を取り出し，復元側の仮定と照合する．
                        version = self._version(obj)
                        if version == 'unasserted' or self._version(current) == 'unasserted':
                            self.diagnostic(
                                'cache-version',
                                'cache version identity needs explicit per-path opaque scenario values',
                                step.source
                            )
                        # keyが同じでもversionが違うCacheは候補へ入れないため，一致条件を別に計算する．
                        same_version = version == self._version(current)
                        # keyが一致し，scopeが可視で，versionも同じ候補だけをCacheの取得候補へ加える．
                        if rank is not None and visible and same_version:
                            scope = self.runs[obj['job'].run].context['scope']
                            # 現在scopeを0，base branchを1，その他の可視scopeを2とし，key順位より先にscopeを比較する．
                            scope_rank = 0 if scope == run.context['scope'] else 1 if scope == run.context['baseBranch'] else 2
                            # 保存物と(scope順位, key順位)，動的key/prefixの成立guardを組にして保持する．
                            candidates.append((obj, (scope_rank, rank), AND(key_guard, prefix_guard)))
            # 現行policyでmodeが未指定の場合だけ，イベント種別から既定のread/writeを選ぶ．
            if self.policy == 'github-current-2026-10-22' and self._cache_mode(job) in {'none', 'write-only'}:
                # 可視性・名前・version等を満たす保存候補と探索順位をためるリストを作る．
                candidates = []
        else:
            # Artifact名の値候補ごとに，保存物のnameとrun/repositoryの同一性を照合する．
            for name, name_guard in self.template(inputs.get('name', ''), job, step):
                # 全Artifact候補を走査し，別名・別run・別repositoryの候補を順に除く．
                for obj in self.objects:
                    # Artifactを保存したRunを取り出し，名前だけでなくrun・repositoryの同一性も確認する．
                    owner = self.runs[obj['job'].run]
                    # downloadのrun-id指定を読み，省略時は現在runを取得先とする．
                    selector = inputs.get('run-id')
                    # 指定run-idの有限候補を評価し，省略時は自runのidだけを条件TRUEで用意する．
                    allowed_runs = self.template(selector, job, step) if selector is not None else [(run.id, 'TRUE')]
                    # 取得先run idの各候補を調べ，同じ成立guardを名前・tokenの条件と組み合わせる．
                    for rid, rid_guard in allowed_runs:
                        # run idが保存元と違うArtifactは，同名でも取得候補へ入れない．
                        if str(rid) != owner.id:
                            continue
                        # repository指定または現在repositoryが保存元と違う場合は，候補から除く．
                        if str(inputs.get( 'repository', run.context['repository'] )) != owner.context['repository']:
                            continue
                        # cross-run取得は明示tokenとactions scopeを契約として要求する．
                        # 実tokenを読み出したりAPIを呼んだりしない．
                        if owner.id != run.id and (not inputs.get('github-token')):
                            self.diagnostic(
                                'artifact-token',
                                'cross-run download requires an explicit token capability',
                                step.source
                            )
                            continue
                        # 同一run取得には追加token条件を課さず，cross-runの場合だけ必要な条件へ置き換える．
                        token_guard = 'TRUE'
                        if owner.id != run.id:
                            if inputs.get('github-token') not in {'${{ secrets.GITHUB_TOKEN }}', '${{ github.token }}'}:
                                self.diagnostic(
                                    'artifact-token-contract',
                                    'cross-run token must be an explicit GITHUB_TOKEN expression',
                                    step.source
                                )
                            # 別runのArtifact取得に必要なactionsのread/write権限を，復元側Jobの実効permissionsから求める．
                            token_guard = self.token_permission(job, 'actions')
                        # name指定がある場合は保存名と完全一致を要求し，空指定は後の複数取得診断へ残す．
                        if name and obj['name'] != str(name):
                            continue
                        # pattern指定がある場合は，限定glob規則でも保存名を照合する．
                        if 'pattern' in inputs and (not pol.glob(str(inputs['pattern']), obj['name'])):
                            continue
                        if 'artifact-ids' in inputs:
                            # artifact-idsの動的な候補列を解決し，保存物tokenと対応する条件を調べる．
                            ids = self.template(inputs['artifact-ids'], job, step)
                            # カンマ区切りの取得id列にこのobject tokenが含まれる候補だけを選ぶ．
                            idguard = OR((g for value, g in ids if obj['token'] in str(value).split(',')))
                        else:
                            idguard = 'TRUE'
                        # Artifact候補はCache順位を付けず，名前・run・id・token権限の全guardを合わせて登録する．
                        candidates.append((obj, (0, 0), AND(name_guard, rid_guard, idguard, token_guard)))
            if not inputs.get('name') and (not inputs.get('artifact-ids')):
                self.diagnostic(
                    'artifact-multiple-download',
                    'all/pattern retrieval layout and merge-multiple are not fully modeled',
                    step.source
                )
        # 各候補が選択可能な条件をため，最後にORへまとめてhit/missを判定する．
        choices = []
        # 候補が存在し，より高順位/新しい候補がない場合だけ，そのobjectを選べる．
        # 取得候補を走査し，現在存在して優先規則を満たすものの復元効果を作る．
        for index, (obj, rank, selector) in enumerate(candidates):
            # 保存物がabsentでなく，今回の選択条件にも一致することを要求する．
            present = AND(obj['token'] + ' != absent', selector)
            # この候補より高順位または同順位で新しい候補を，選択を妨げる条件として集める．
            blockers = []
            # Cacheでは優先順位と新しさの条件を加え，Artifactの候補選択にはこの順位規則を適用しない．
            if cache:
                # 他候補の順位と存在を比較し，この候補を選べる条件へ反映する．
                for other, other_rank, other_selector in candidates:
                    # 同じ保存候補そのものは比較相手から外し，自分自身で選択を妨げないようにする．
                    if other['token'] == obj['token']:
                        continue
                    # tupleは先頭から比較されるため，scope順位，次にkey順位の小さい候補を優先する．
                    if other_rank < rank:
                        blockers.append('!' + AND(other['token'] + ' != absent', other_selector))
                    # 同順位ならageが大きい保存物を優先し，新しい候補が存在する時は古い方を選ばせない．
                    elif other_rank == rank:
                        blockers.append('!' + AND(
                            other['token'] + ' != absent',
                            other_selector,
                            'age_' + other['token'] + ' > age_' + obj['token']
                        ))
            # 自身の存在・selectorと，優先候補が存在しない条件をすべてANDにする．
            selected = AND(present, *blockers)
            # このstepの復元候補を選ぶIVAR名を作り，path候補が違っても同じstepの選択を共有する．
            choice = symbol('select', self.outcome(job, step))
            # 取得候補を選ぶIVARを宣言する．
            # object名は抽象tokenであり，実サービスの数値IDではない．
            self.ts.inputs.setdefault(choice, ['none'])
            # IVARのdomainへ同じobjectを二重登録しないよう，既存候補を確認する．
            if 'id_' + obj['token'] not in self.ts.inputs[choice]:
                # この保存物を選ぶ列挙値をIVARのdomainへ追加する．
                self.ts.inputs[choice].append('id_' + obj['token'])
            # step実行条件・優先候補条件・IVARの選択が同時に成立した時だけ，復元効果を発生させる．
            effect = AND(guard, selected, choice + ' = id_' + obj['token'])
            # この候補が取得可能な条件を集め，後で全候補のORからhit/missを決める．
            choices.append(selected)
            # 選択された保存物のabsent/clean/taintedのうち，存在する状態を復元先fileへコピーする．
            self.ts.update(destination, effect, obj['token'])
            # 同じ選択条件でArtifact memberの内容・由来も復元し，抽象taint状態だけのコピーと分ける．
            self._restore_contents(job, obj, destination_path, effect)
            # 保存物からworkspaceへの転送が起きた履歴をTRUEにし，後のstepで消えないようにする．
            self.ts.latch('transfer', effect)
            if cache:
                hit, _ = self.output(job, step.id, 'cache-hit')
                # exact keyなら文字列true，prefix経由なら文字列falseをcache-hitへ記録する．
                self.ts.update(hit, effect, atom('true' if rank[1] == 0 else 'false'))
                # scope順位とは別にkey順位0をexact hitとし，combined Cacheのpost-save抑制履歴を残す．
                if rank[1] == 0:
                    # 一度でもexact hitしたこのstepの履歴を保持し，同じkeyのpost-saveを抑える．
                    self.ts.latch('cache_exact_' + self.outcome(job, step), effect)
        # 少なくとも1つの取得可能候補がある条件を作り，missと有効なIVAR選択の判定に共用する．
        any_match = OR(choices)
        # 候補がある場合だけIVARの有効性制約を作り，何も取得できない時に無理な選択を要求しない．
        if candidates:
            # このstepの復元候補を選ぶIVAR名を作り，path候補が違っても同じstepの選択を共有する．
            choice = symbol('select', self.outcome(job, step))
            # 取得時に許される「選択値と存在条件」の組を集めるリストを作る．
            valid = []
            # 全候補について有効なIVAR選択条件を再構成し，TRANSへ渡すOR条件を作る．
            for obj, rank, selector in candidates:
                # この候補より高順位または同順位で新しい候補を，選択を妨げる条件として集める．
                blockers = []
                if cache:
                    # 他候補の順位と存在を比較し，この候補を選べる条件へ反映する．
                    for other, other_rank, other_selector in candidates:
                        # 同じ保存候補そのものは比較相手から外し，自分自身で選択を妨げないようにする．
                        if other['token'] == obj['token']:
                            continue
                        # tupleは先頭から比較されるため，scope順位，次にkey順位の小さい候補を優先する．
                        if other_rank < rank:
                            blockers.append('!' + AND(other['token'] + ' != absent', other_selector))
                        # 同順位ならageが大きい保存物を優先し，新しい候補が存在する時は古い方を選ばせない．
                        elif other_rank == rank:
                            blockers.append('!' + AND(
                                other['token'] + ' != absent',
                                other_selector,
                                'age_' + other['token'] + ' > age_' + obj['token']
                            ))
                # IVARがこのobjectを指し，存在・selector・優先順位も満たす有効選択条件を登録する．
                valid.append(AND(
                    choice + ' = id_' + obj['token'],
                    obj['token'] + ' != absent',
                    selector,
                    *blockers
                ))
            # TRANSへ出す含意．
            # 取得できる候補がある時，IVARが無効な候補やnoneを選ぶ遷移を禁止する．
            self.ts.constraints.append(AND(guard, any_match) + ' -> ' + OR(valid))
        # Cacheだけは取得できなかった経路でcache-hitを空文字へ更新する．
        if cache:
            hit, _ = self.output(job, step.id, 'cache-hit')
            # Cache missではcache-hitを空文字にし，prefix hitのfalseと区別する．
            self.ts.update(hit, AND(guard, '!' + any_match), atom(''))
        return any_match

    # 単純操作のfile更新と利用・権限sinkの履歴を，共通の遷移へ追加する．
    def _shell(self, job, step, guard):
        """単純Shell操作をfile更新，値伝播，利用・権限履歴へ下げる．"""
        # このShell処理に局所的な集合を用意するが，現在の実装では判定に利用していない．
        touched = set()
        # 同じstep内の依存するfile更新には，操作間の中間状態が必要である．
        # ここでは診断を残す．
        # 以下の規則は生成するが，unsupportedがあるモデルを安全とはしない．
        writes = [op[2] if op[0] == 'copy' else op[1] for op in step.ops if op[0] in {'copy', 'write'}]
        # copy/read/stageが参照するpathを集め，先行書込みの結果が必要な同一step内依存を調べる．
        reads = [op[1] for op in step.ops if op[0] in {'copy', 'read', 'stage'}]
        # 読書きpathの重なり，または同じpathへの複数書込みがあれば，中間状態が未実装であることを診断する．
        if set(writes) & set(reads) or len(set(writes)) != len(writes):
            self.diagnostic(
                'shell-sequential-effects',
                'split dependent file writes/reads into steps; microstep lowering not implemented',
                step.source
            )
        # 単純Shell契約で認識した操作tupleを，元のscriptと同じ順序で取り出す．
        for op in step.ops:
            # 操作tupleの先頭を命令種別として読み，残りの要素をその命令の引数として扱う．
            kind = op[0]
            # literalの新規書込みならcleanへ更新し，構造化内容を扱う場合は以前の由来・完全性確認もリセットする．
            if kind == 'write':
                # literal書込みが成功する経路でfileをcleanへし，既存のtaint状態を置き換える．
                self.ts.update(self.file(job, op[1]), guard, 'clean')
                if self.has_structured:
                    content, origin = self.content(job, op[1])
                    # 新しいliteral内容を内容VARへ書き込み，後続stepのread/hashが同じ値を見るようにする．
                    self.ts.update(content, guard, atom(op[2]))
                    # literalによる置換は以前のArtifact内容を引き継がないため，Artifact由来をFALSEへ戻す．
                    self.ts.update(origin, guard, 'FALSE')
                    # 内容を置き換えた後は以前のdigest検査を流用せず，このfileを未検証へ戻す．
                    self.ts.update(self.file_integrity(job, op[1]), guard, 'integrity_not_run')
            # コピー元の状態をコピー先へ伝え，構造化内容を扱う場合は内容・由来・完全性確認も引き継ぐ．
            elif kind == 'copy':
                # コピー元・先のfile記号を両方取得し，同じguardで次状態を登録する．
                source, dest = (self.file(job, op[1]), self.file(job, op[2]))
                # コピーが成立した時，元のclean/tainted/absent状態を先へ伝える．
                self.ts.update(dest, guard, source)
                if self.has_structured:
                    content, origin = self.content(job, op[1])
                    # コピー先の内容とArtifact由来のVARを，2要素の戻り値から取得する．
                    dc, do = self.content(job, op[2])
                    # 元fileの内容をコピー先へ伝え，存在・taintの更新と対応を保つ．
                    self.ts.update(dc, guard, content)
                    # 元fileのArtifact由来もコピー先へ伝え，cleanなArtifactの依存も失わないようにする．
                    self.ts.update(do, guard, origin)
                    # コピー元のfile固有の完全性状態をコピー先へ伝え，無条件にverifiedへ変更しない．
                    self.ts.update(self.file_integrity(job, op[2]), guard, self.file_integrity(job, op[1]))
                # コピー元がtaintedだった利用履歴を記録し，コピー先が後で上書きされても履歴は残す．
                self.ts.latch('dependent_use', AND(guard, source + ' = tainted'))
            # read/stageは対象のtainted状態を利用履歴へ残し，stageなら後のsink用の履歴も更新する．
            elif kind in {'read', 'stage'}:
                # read/stageの対象fileを取得し，その時点のtaintで利用条件を作る．
                source = self.file(job, op[1])
                # taintedなfileをこのstepで読んだ・stageした履歴をTRUEへする．
                self.ts.latch('dependent_use', AND(guard, source + ' = tainted'))
                # stage操作の場合だけ，後続git pushが参照する汚染stage履歴も更新する．
                if kind == 'stage':
                    # このJobでtainted内容をstageしたことを保持し，別Jobのstage履歴とは混ぜない．
                    self.ts.latch('staged_' + job.id, AND(guard, source + ' = tainted'))
            # 環境ファイルへの書込みは値と4種類のラベルを求め，共通の更新規則生成へ渡す．
            elif kind in {'output', 'runtime-output', 'runtime-env'}:
                if kind == 'output':
                    # 単純outputの値をGitHub式として評価し，候補と成立条件を得る．
                    values = self.template(op[2], job, step)
                    # 同じoutput式からArtifact由来の条件をたどり，値そのものとは分けて保存する．
                    origin = self.template_label(op[2], job, step)
                    # output式の参照元にtaintがある条件を，originと独立に求める．
                    taint = self.template_label(op[2], job, step, True)
                    # output式のdata依存を求め，値の計算元による影響を保持する．
                    data = self.template_label(op[2], job, step, component='data')
                    # output式のcontrol依存を求め，参照先の分岐に由来する影響も保持する．
                    control = self.template_label(op[2], job, step, component='control')
                else:
                    # 環境ファイル形式では引用方法に応じたShell変数展開も行い，値と4種類のラベルを一緒に受け取る．
                    values, origin, taint, data, control = self.shell_value(op[2], op[3], job, step)
                # output/envの値と由来・taint・data/control依存を，同じstep実行guardで更新する．
                self.emit_file_command(kind, op[1], values, origin, taint, job, step, guard, data, control)
            # 限定git/npm/gh契約の権限地点．
            # 到達と権限可用性と認証，処理成功とtaint依存を段階的に合わせる．
            elif kind == 'sink':
                # sink契約が要求するcontents/packages/deploymentsのscopeを取り出す．
                scope = op[1]
                # git pushならstage済みtaint，それ以外のsinkなら対象fileのtaintを危険依存の条件にする．
                taint = 'staged_' + job.id if op[2] == 'staged' else self.file(job, op[2]) + ' = tainted'
                # 成功入力を除いたguardで地点への到達を記録し，実行成功と地点到達を分ける．
                self.ts.latch('sink_site', guard.replace('operation_ok', 'TRUE'))
                # sinkが要求するwrite権限の条件を，実効permissionsとfork設定から求める．
                capability = self.token_permission(job, scope, write=True)
                if scope != 'packages':
                    # contents/deploymentsのコマンド認証が供給されているかをscenarioから読む．
                    authenticated = self.runs[job.run].context.get('tokenAuthenticated', {}).get(scope)
                    # 認証の可否が不明なら共有FROZENVARへ残し，permissionsだけでコマンド成功を断定しない．
                    if authenticated is None:
                        # 同じrun・scopeの認証未知値を共用するため，その組から安定した識別子を作る．
                        auth = symbol('authentication', f'{job.run}:{scope}')
                        # 認証可否をFROZENVARにし，実行中にコマンドの認証仮定が都合よく変わらないようにする．
                        self.ts.frozen[auth] = ['FALSE', 'TRUE']
                        self.diagnostic(
                            'sink-authentication',
                            'command authentication with GITHUB_TOKEN is unknown',
                            step.source,
                            'unknown'
                        )
                    else:
                        # 認証条件が明示された場合は，Pythonの真偽をSMV定数TRUE/FALSEへ変換する．
                        auth = str(bool(authenticated)).upper()
                    # tokenのwrite権限とコマンドがそのtokenで認証される条件を両方要求する．
                    capability = AND(capability, auth)
                # package公開の認証は，GITHUB_TOKENとは別の外部credential可用性で表す．
                if scope == 'packages':
                    # packages公開に使う外部credentialの可用性を読み，GITHUB_TOKENのscopeとは分けて扱う．
                    cred = self.runs[job.run].context.get('externalPackageCredential')
                    # 外部credentialが不明なら固定の未知値を宣言し，明示時はその値を使う．
                    if cred is None:
                        # 同じrunのpackage credential可用性を共有する記号を作る．
                        external = symbol('credential', str(job.run) + ':packages')
                        # 外部credentialの有無も実行中一定のFROZENVARとし，token scopeとは別に扱う．
                        self.ts.frozen[external] = ['FALSE', 'TRUE']
                        self.diagnostic(
                            'external-credential',
                            'package registry credential availability is unknown',
                            step.source,
                            'unknown'
                        )
                    else:
                        # 外部credentialが明示された場合は，その真偽だけをSMV定数へ変換する．
                        external = str(bool(cred)).upper()
                    # packagesの認証条件には，外部registry credentialの可用性を使う．
                    capability = external
                # 成否入力とは独立に，sink地点で必要な権限・認証が利用可能だった履歴を記録する．
                self.ts.latch('authority_available', AND(guard.replace('operation_ok', 'TRUE'), capability))
                # 地点で処理が実行され，成否入力と権限・認証も成立した場合だけ成功履歴を残す．
                self.ts.latch('sink_succeeded', AND(guard, capability))
                # sink実行時にtainted内容へ依存した履歴を，sink権限の可否とは別に記録する．
                self.ts.latch('dependent_use', AND(guard, taint))
                # sink成功とtaintedなデータへの依存が同時に成立した場合だけ，危険な権限利用履歴を立てる．
                self.ts.latch('bad_authority', AND(guard, capability, taint))

    # 各jobの開始・step実行・skip・失敗・完了を生成し，最後にSMVとmanifestを返す．
    # actorによるjob間の実行順序はモデルに残す．
    # Python側で単一順序に固定しない．
    def build(self):
        """各job/stepの遷移を生成し，SMVと対応表を返す．"""
        # consumerが先に書かれていても参照できるよう，member変数を全jobの遷移より先に宣言する．
        if self.has_structured:
            # すべてのArtifact候補についてmember構成を先に確定し，consumerの生成順に依存させない．
            for obj in self.objects:
                # upload root配下のfileを集め，この保存候補のbundle一覧へ登録する．
                self._bundle_source(obj, self.file(obj['job'], obj['path']))
                # guard=NoneでmemberのVAR宣言だけを済ませ，実際の保存規則は後の_saveで追加する．
                self._save_contents(obj, None)
        # ここからjob単位のschedulerを組み立てる．
        # PCの更新条件が偽ならrenderの自己保持規則で待機する．
        # matrix展開後の各Jobを走査し，現在のJobに属する設定・step・状態だけを処理する．
        for job in self.jobs:
            # Jobが保持するrun番号を添字にして，所属workflowと外部contextを取得する．
            run = self.runs[job.run]
            # 現在Jobの実行位置を表すPCの記号を取り出し，開始・step・終端の更新へ共用する．
            pc = 'pc_' + job.id
            # このJobが現在の遷移でschedulerに選ばれた条件を文字列として作る．
            selected = 'actor = ' + job.id
            # needsの全jobが終端値になるまでpendingから進めない．
            # その後の成功条件はjobのifが判断する．
            terminal = AND(*(f'pc_{d} in {{ success, failure, skipped, cancelled }}' for d in job.needs))
            # actor選択・pending・イベント条件・needs完了を合わせて，Job開始を許す条件を作る．
            start = AND(selected, pc + ' = pending', self._enabled(run), terminal)
            # job-level ifを評価し，開始できる時にs0へ進むかskippedになるかを決める．
            condition = self.condition(job.raw.get('if'), job)
            # pendingから最初のstep位置s0へ進む遷移を，job-level ifが真の側に登録する．
            self.ts.update(pc, AND(start, condition), 's0')
            # job-level ifが偽ならJob全体をskippedへ進め，step本体を実行しないようにする．
            self.ts.update(pc, AND(start, '!' + '(' + condition + ')'), 'skipped')
            # 実行中のJobが選ばれ取消要求もある場合に，取消履歴を立てる．
            self.ts.latch(
                'cancelled_' + job.id,
                AND(
                    selected,
                    'cancel_request',
                    '!' + '(' + pc + ' in { pending, success, failure, skipped, cancelled })'
                )
            )
            # 全stepを処理した末尾位置で，取消・失敗・成功の各履歴に対応するJob終端値へ進める．
            self.ts.update(
                pc,
                AND(selected, '!cancel_request', pc + f' = s{len(job.steps)}', 'cancelled_' + job.id),
                'cancelled'
            )
            # 全stepを処理した末尾位置で，取消・失敗・成功の各履歴に対応するJob終端値へ進める．
            self.ts.update(
                pc,
                AND(
                    selected,
                    '!cancel_request',
                    pc + f' = s{len(job.steps)}',
                    '!cancelled_' + job.id,
                    'failed_' + job.id
                ),
                'failure'
            )
            # 全stepを処理した末尾位置で，取消・失敗・成功の各履歴に対応するJob終端値へ進める．
            self.ts.update(
                pc,
                AND(
                    selected,
                    '!cancel_request',
                    pc + f' = s{len(job.steps)}',
                    '!cancelled_' + job.id,
                    '!failed_' + job.id
                ),
                'success'
            )
            # 0始まりの番号とstepを組にし，YAMLのstep順をPCのs0，s1等へ対応付ける．
            for i, step in enumerate(job.steps):
                # stepの番号iに対して，actor・PC・取消要求を組み合わせた実行位置条件を得る．
                gate = self.gate(job, i)
                # step-level ifを評価し，省略時のsuccess()も含めた実行条件を得る．
                condition = self.condition(step.raw.get('if'), job, step)
                # gateは位置とactor，conditionはif．
                # さらに成否入力を加えて成功側と失敗側へ分ける．
                # 位置に到達しただけでは実行せず，stepのifも真の場合に実行する．
                execute = AND(gate, condition)
                # このrun・元job・stepの成否がscenarioで固定されているかを調べる．
                outcome_guard = self.scenario.get('stepOutcomes', {}).get(f'{run.id}:{job.original}:{step.id}')
                # 固定成否がなければIVARを使い，指定があればそのBooleanをSMV定数へ変換する．
                operation = 'operation_ok' if outcome_guard is None else str(bool(outcome_guard)).upper()
                # 実行条件と成否入力が真の側を成功効果のguardとする．
                success = AND(execute, operation)
                # 同じ実行条件の下で成否入力が偽の側を失敗guardとし，skipとは区別する．
                failure = AND(execute, '!(' + operation + ')')
                # uses/runから確定した限定効果名を読み，対応するActionまたはShellの遷移生成へ分岐する．
                action = step.contract
                if action in {
                    'actions/cache-v4',
                    'actions/cache/save-v4',
                    'actions/cache/restore-v4',
                    'actions/upload-artifact-v4'
                }:
                    # upload-artifactではname，Cacheではkeyを必須値の妥当性確認に使う．
                    field = 'name' if action == 'actions/upload-artifact-v4' else 'key'
                    # 値が空でなくCache keyなら長さも上限内の候補だけを残し，そのguardをORにする．
                    valid = OR((g for v, g in self.template(
                        step.raw.get('with', {}).get(field, 'artifact' if field == 'name' else ''),
                        job,
                        step
                    ) if ex.string(v) and (field == 'name' or len(ex.string(v)) <= 512)))
                    # 有効な名前/keyに加え，有効なpath候補も存在することを要求する．
                    valid = AND(
                        valid,
                        OR((g for path, g in self.action_paths(step.raw.get('with', {}).get('path', '.'), job, step)))
                    )
                    # 元は成功する入力でも名前/key/pathが不正なら，Actionの失敗側へ移す．
                    failure = OR([failure, AND(success, '!(' + valid + ')')])
                    # Action効果は名前/key/pathの妥当性も満たす成功側だけへ登録する．
                    success = AND(success, valid)
                if action == 'unsupported':
                    self.diagnostic(
                        'unsupported-step-effects',
                        'no safety interpretation for omitted step effects',
                        step.source
                    )
                # Actionのwithキーを辞書として取り出し，省略時は空辞書で既定入力を使う．
                inputs = step.raw.get('with', {})
                # checkout成功時に供給在庫を作り，PR由来等の信頼条件をclean/taintedへ写す．
                # 内容は別VARで保持する．
                if action == 'actions/checkout-v4':
                    # 通常checkoutの既定taintはpull_request由来かで決め，カスタムrefは別のscenario仮定を求める．
                    default_untrusted = run.context['event'] == 'pull_request'
                    if 'ref' in inputs or 'repository' in inputs:
                        # 任意ref/repositoryの信頼性をイベント名だけで判断せず，明示のcheckoutUntrustedを読む．
                        untrusted = run.context.get('checkoutUntrusted')
                        if untrusted is None:
                            self.diagnostic(
                                'checkout-provenance',
                                'custom checkout ref trust requires explicit scenario fact',
                                step.source
                            )
                            # カスタムcheckoutの信頼性が不明な場合は診断を残し，この抽象状態ではtainted側へ置く．
                            untrusted = True
                    else:
                        untrusted = default_untrusted
                    for path in run.context.get('checkoutFiles', []):
                        # checkout成功時だけ，在庫にある各ファイルを信頼条件に対応するclean/taintedへ更新する．
                        checkout_guard = success
                        if path in run.context.get('optionalCheckoutFiles', []) and not run.context.get('fileInventoryComplete'):
                            # YAMLだけでは実在を確定できないfileは，存在する場合としない場合の両方を残す．
                            # FROZENVARをrun・pathで共有し，同じrunの別Jobでも実在の選択を変えない．
                            present = symbol('input_present', run.id + ':' + path)
                            self.ts.frozen[present] = ['FALSE', 'TRUE']
                            checkout_guard = AND(success, present)
                        self.ts.update(self.file(job, path), checkout_guard, 'tainted' if untrusted else 'clean')
                        # 内容も同じ存在条件で写し，存在しないfileに内容だけを供給しない．
                        self._checkout_contents(job, path, checkout_guard)
                    if not run.context.get('checkoutFiles'):
                        self.diagnostic(
                            'checkout-files',
                            'bounded checkout file inventory was not supplied',
                            step.source
                        )
                elif action == 'shell-structured-v1':
                    # 構造化Shellを遷移へ下げ，正常終了する条件と明示的に失敗する条件を受け取る．
                    normal, rejected = self._structured_shell(job, step, success)
                    # Action入力の失敗とShell内部の拒否条件をORにし，どちらでもstepを失敗にする．
                    failure = OR([failure, AND(success, rejected)])
                    # 構造化Shellが正常に最後まで進む条件を，元の成功guardに加える．
                    success = AND(success, normal)
                    # ifで実行されなかった場合に完全性不一致による遮断履歴があるかを，同じstep位置で調べる．
                    self._blocked_history(job, step, gate, condition)
                elif action == 'shell-operations-v1':
                    # 単純Shellのfile・output・sink効果を，step成功側のguardで共通IRへ登録する．
                    self._shell(job, step, success)
                    # 操作列にexit 1が含まれる場合は，このstepの実行を失敗側へ振り分ける．
                    if any((op[0] == 'fail' for op in step.ops)):
                        # exit 1を含むstepは実行条件の下で失敗させ，成功効果のguardをFALSEへ置く．
                        failure, success = (execute, 'FALSE')
                elif action in {'actions/upload-artifact-v4', 'actions/cache/save-v4'}:
                    # 宣言済み保存候補を走査し，このJob・stepが所有する候補だけを保存対象にする．
                    for obj in self.objects + self.caches:
                        # Pythonのisで同一Job/Step objectを確認し，別の保存stepの候補を更新しないようにする．
                        if obj['job'] is job and obj['step'] is step:
                            # step成功に候補固有のname/key/path guardも加え，今回有効な保存物だけを作る．
                            self._save(obj, AND(success, obj['guard']))
                elif action in {
                    'actions/download-artifact-v4',
                    'actions/cache/restore-v4',
                    'actions/cache-v4'
                }:
                    if action == 'actions/cache-v4':
                        for obj in self.caches:
                            if obj['job'] is job and obj['step'] is step:
                                # combined Cacheのrestore時点で有効だった候補guardを保存し，post-saveでkeyを再評価しないようにする．
                                self.ts.update('captured_' + obj['token'], success, obj['guard'])
                    # Artifact/Cacheの候補照合と復元効果を生成し，何かを取得できた条件を受け取る．
                    matched = self._restore(job, step, success)
                    # Artifact downloadまたはfail-on-cache-miss指定では，取得候補なしをstep失敗へ反映する．
                    if action == 'actions/download-artifact-v4' or inputs.get('fail-on-cache-miss') in {True, 'true'}:
                        # 実行したdownloadがmissした条件を失敗へ追加し，ifによるskipと取得失敗を区別する．
                        failure = OR([failure, AND(execute, '!' + matched)])
                        # missを失敗にする契約では，hitした実行だけを成功側へ残す．
                        success = AND(success, matched)
                # 契約にないwith入力は，解釈を省略したことを示すdiagnosticとして残す．
                # 契約ごとに意味を与えたwithキーを列挙し，未登録の入力を黙って無視しないようにする．
                supported_inputs = {
                    'actions/checkout-v4': {'ref', 'repository', 'persist-credentials'},
                    'actions/upload-artifact-v4': {'name', 'path', 'retention-days', 'if-no-files-found'},
                    'actions/download-artifact-v4': {
                        'name',
                        'path',
                        'run-id',
                        'repository',
                        'github-token',
                        'artifact-ids',
                        'pattern'
                    },
                    'actions/cache-v4': {'key', 'path', 'restore-keys', 'fail-on-cache-miss'},
                    'actions/cache/save-v4': {'key', 'path'},
                    'actions/cache/restore-v4': {'key', 'path', 'restore-keys', 'fail-on-cache-miss'}
                }
                if action in supported_inputs:
                    # withの実キーから対応済みキーを引き，差分だけを未対応診断へ追加する．
                    for name in inputs.keys() - supported_inputs[action]:
                        self.diagnostic(
                            'action-input',
                            f'{action}: input {name} lacks effect contract',
                            step.source
                        )
                # if不成立や失敗も含め，このstepを処理した次の位置へ進む．
                # 成否はPCと別に記録して後続ifへ伝える．
                self.ts.update(pc, gate, f's{i + 1}')
                # このstepのoutcome記号を得て，PCとは別にskip・成功・失敗を更新する．
                out = self.outcome(job, step)
                # ifが偽だったstepのoutcomeをskippedにし，未実行と成功・失敗を区別する．
                self.ts.update(out, AND(gate, '!' + '(' + condition + ')'), 'skipped')
                # 成功したstepの元outcomeをsuccessへ更新する．
                self.ts.update(out, success, 'success')
                # 失敗したstepの元outcomeをfailureへ更新し，continue-on-errorでもこの値は変えない．
                self.ts.update(out, failure, 'failure')
                # continue-on-errorは元outcomeを変えずconclusionだけ成功へ変える．
                # 動的式は診断し，literalだけ扱う．
                # continue-on-errorの指定を読み，省略時はfalseとして失敗履歴を残す．
                coe = step.raw.get('continue-on-error', False)
                # 式によるcontinue-on-errorは未対応なので，診断してこの処理ではfalseとして扱う．
                if not isinstance(coe, bool):
                    self.diagnostic(
                        'continue-on-error-expression',
                        'continue-on-error currently requires Boolean literal',
                        step.source
                    )
                    coe = False
                # skipのconclusionもskippedとし，continue-on-errorで実行済みに変えない．
                self.ts.update(out + '_conclusion', AND(gate, '!' + '(' + condition + ')'), 'skipped')
                # 正常に成功したstepのconclusionをsuccessへ設定する．
                self.ts.update(out + '_conclusion', success, 'success')
                # 失敗時はcontinue-on-errorがtrueならconclusionだけsuccess，そうでなければfailureにする．
                self.ts.update(out + '_conclusion', failure, 'success' if coe else 'failure')
                # continue-on-errorで吸収しない失敗だけをJob全体のfailed履歴へ伝える．
                if not coe:
                    # continue-on-errorで吸収しないstep失敗をJobのfailed履歴へ伝え，後続success()を偽にする．
                    self.ts.latch('failed_' + job.id, failure)
            # combined Cacheのpost-saveは，jobが失敗・取消されず末尾へ進む場合に生成する．
            # 最終workspace状態，restore時のexact-hit履歴，捕捉済みkey/path条件を使う．
            for obj in self.caches:
                # このJobのcombined Cacheだけを選び，独立save契約や他JobのCacheへpost-saveを付けない．
                if obj['job'] is job and obj['step'].contract == 'actions/cache-v4':
                    out = self.outcome(job, obj['step'])
                    # Job末尾・失敗取消なし・restore成功・exact miss・捕捉済み候補がそろった条件をpost-saveのguardにする．
                    guard = AND(
                        selected,
                        '!cancel_request',
                        pc + f' = s{len(job.steps)}',
                        '!failed_' + job.id,
                        '!cancelled_' + job.id,
                        out + ' = success',
                        '!cache_exact_' + out,
                        'operation_ok',
                        'captured_' + obj['token']
                    )
                    # 最終workspace状態をこのguardで保存し，restore時点の内容を誤って再保存しない．
                    self._save(obj, guard)
            if len({obj['step'].id for obj in self.caches if obj['job'] is job and obj['step'].contract == 'actions/cache-v4'}) > 1:
                self.diagnostic(
                    'multiple-cache-post-actions',
                    'reverse ordered post-action frames are not implemented for multiple combined cache actions',
                    job.source
                )
        # manifestはSMVの記号と元YAML，仮定，診断，outputの値候補を対応付ける．
        # asdictでdataclassを入れ子の辞書へ変換する．
        # この時点の判定は意味規則の対応状況．
        # CTLの検証結果は別工程で得る．
        # 生成した記号・元YAML・状態数・診断・仮定をまとめ，SMVと一緒に返す対応表を作る．
        manifest = {
            'modelVersion': '2-unified-bounded',
            'contractVersion': CONTRACT_VERSION,
            'cachePolicy': self.policy,
            'scenario': self.scenario,
            'automaticAssumptions': self.scenario.get('automaticAssumptions', []),
            'analysisScope': 'reachability under the recorded finite assumptions; not observed execution',
            'sources': [{
                'file': r.path,
                'sha256': hashlib.sha256(Path(r.path).read_bytes()).hexdigest()
            } for r in self.runs],
            'jobs': [asdict(job) for job in self.jobs],
            'objects': [{k: v for k, v in o.items() if k not in { 'job', 'step' }} | {'job': o['job'].id, 'step': o['step'].id} for o in self.objects + self.caches],
            'hashInputs': self.hash_facts,
            'runtimeEnvironment': [{'job': j, 'name': n, 'symbol': v, 'assigned': a, 'domain': d} for (j, n), (v, a, d) in self.runtime_env.items()],
            'dataflowLabels': [{
                'kind': k,
                'job': j,
                'identity': n,
                'origin': v,
                'taint': self.value_taints.get((k, j, n)),
                'dataDependency': self.value_dependencies.get(('data', k, j, n), 'FALSE'),
                'controlDependency': self.value_dependencies.get(('control', k, j, n), 'FALSE')
            } for (k, j, n), v in self.value_origins.items()],
            'symbols': self.ts.sources,
            'outputSymbols': [{
                'job': j,
                'step': s,
                'output': n,
                'symbol': v[0],
                'domain': v[1]
            } for (j, s, n), v in self.outputs.items()],
            'diagnostics': self.diagnostics,
            'actionRefs': sorted(self.action_refs),
            'verdict': 'unknown/unsupported' if any((d['status'] == 'unsupported' for d in self.diagnostics)) else 'pending-model-check',
            'assumptions': [
                'one bounded run per supplied workflow; no eviction/retention expiry',
                'scenario contexts and opaque cache versions are assumptions, not runtime evidence',
                'nondeterministic action/network success; shell operations are atomic per step',
                'tag contracts are version families; tags are not resolved to immutable source commits'
            ] + ([
                'YAML-only defaults select the first declared event, main branch, Linux/X64 and a synthetic shared repository; these are assumptions, not observations',
                'automatically inferred checkout paths are single-file candidates with both presence choices and finite representative contents; non-reachability applies only to this finite content domain'
            ] if self.scenario.get('defaultContext') else []),
            'properties': self.ts.properties,
            'stateVariables': len(self.ts.variables)
        }
        # 共通IRをSMVへ出力し，元YAMLへの対応表を別の戻り値として返す．
        text = self.ts.render()
        # ログが別モデルの対応表を誤って条件表示に使わないよう，SMV本文のhashも保存する．
        manifest['modelSha256'] = hashlib.sha256(text.encode('utf-8')).hexdigest()
        return (text, manifest)

# CLIと全評価が使う共通入口．
# scenarioの有無は入力条件だけを変え，同じbackendへ渡す．
def compile_workflows(paths, scenario=None, *, restrictions=None):
    """CLI・評価で共用する変換の入口．"""
    try:
        from .scenario import default_scenario
        # 変換時間の計測を開始し，モデルの意味に関わる状態変数には含めない．
        started = time.perf_counter()
        # scenario省略時だけ既定条件を生成し，同じ変換器でIR構築とSMV生成を行う．
        from .external_conditions import is_complete_scenario,build_space,compile_space,constrain_complete,fixed_condition_manifest
        if is_complete_scenario(scenario,len(paths)):
            fixed = constrain_complete(paths,scenario,restrictions)
            backend=WorkflowCompiler(paths,fixed)
            text,manifest = backend.build()
            manifest['externalConditions']=fixed_condition_manifest(paths,backend.scenario,backend.ts)
            manifest['inputMode']='explicit-scenario'
        else:
            space = build_space(paths,scenario,restrictions)
            text,manifest = compile_space(space,WorkflowCompiler)
        # 実行ごとに変わる所要時間を対応表へ追加し，SMV本文とは切り離して記録する．
        manifest['generationSeconds'] = time.perf_counter() - started
        return (text, manifest)
    except (yaml.YAMLError, ValueError, TypeError, KeyError, RecursionError) as exc:
        # YAMLや有限式の入力エラーを共通のWorkflowErrorへ包み，元例外も原因として保持する．
        raise base.WorkflowError(str(exc)) from exc
