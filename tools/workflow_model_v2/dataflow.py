# step間の環境変数とoutputを，共通の状態変数へ変換する補助処理．
# 実際の環境変数やcredentialにはアクセスしない．
# 値・由来・taintを別々に扱う．
"""値の伝播と依存ラベルを，共通の状態遷移IRへ接続するmixin．

WorkflowCompilerが持つjob/step/contextを入力として使い，有限の値とSMV条件の組を返す．
GITHUB_ENV，step/job outputの共有slotを管理し，値・由来・taint・データ依存・制御依存を分ける．
環境ファイルへの書込みはVARの更新で表し，同一step内の参照は更新前の値を使う．
snapshotを明示された場合は通常fileの内容を読み，hashFilesの有限候補計算へ供給する．
完全な在庫や内容がないhashは代表記号と診断にし，実hashだと主張しない．
このclass単独でSMVを出力せず，compilerとshell_loweringから利用される．
"""
import re
from . import expressions as ex
from .transition import atom, symbol, conjunction as AND, disjunction as OR

class Dataflow:
    # 環境ファイルへの書込み候補を先に宣言する．
    # 未設定と空文字の設定は別の状態．
    """実行時env・outputと，その依存ラベルを扱うmixin．"""

    def _prepare_dataflow(self):
        """環境ファイルへ書かれる名前を集め，共有slotとラベルの管理表を作る．"""
        # GITHUB_ENVで設定する値・設定済みフラグ・有限domainを，(Job, 変数名)ごとに保持する表を作る．
        self.runtime_env = {}
        # Artifact由来かを表すラベルの共有表を作り，値がcleanかどうかとは分けて管理する．
        self.value_origins = {}
        # 信頼できないデータの影響を受ける条件を，originとは別のBooleanとして管理する．
        self.value_taints = {}
        # 値の計算元であるdata依存と，分岐を選んだcontrol依存を別々の記号で管理する．
        self.value_dependencies = {}
        # matrix展開後の各Jobを走査し，現在のJobに属する設定・step・状態だけを処理する．
        for job in self.jobs:
            # このJobのstepを元の順序で走査し，各stepの契約・値・sourceを参照する．
            for step in job.steps:
                # 単純操作列からruntime-envだけを選ぶ内包表記．
                # 構造化Shellのgithub-envも同じ名前一覧へ追加する．
                names = [op[1] for op in step.ops if op[0] == 'runtime-env']
                # 構造化ShellのIRがあるstepでは，単純操作列だけでなくそのgithub-env操作も調べる．
                if step.program:
                    # writes[0]のenv:NAME等を最初の:で分け，後半のNAMEだけを宣言対象へ加える．
                    names += [op.writes[0].split(':', 1)[1] for op in step.program.operations if op.kind == 'github-env']
                # このstepが書き込むすべてのenv名について，共有slotを準備する．
                for name in names:
                    # 同じJob・同じ名前への書込みは共通の値VARと設定済みフラグを使う．
                    self.environment_slot(job, name)

    def environment_slot(self, job, name, values=()):
        """値と設定済みフラグを持つjob単位のenv slotを取得・拡張する．"""
        # 辞書のkeyに(job,変数名)のtupleを使い，同名envが別jobへ漏れないようにする．
        key = (job.id, name)
        # GitHub/Runnerの予約変数やNODE_OPTIONSへの書込みは診断し，ここではslot宣言自体は続ける．
        if name.startswith(('GITHUB_', 'RUNNER_')) or name == 'NODE_OPTIONS':
            self.diagnostic(
                'reserved-environment',
                'GITHUB_ENV cannot set reserved variable ' + name,
                job.source
            )
        # 未登録のenvだけ新しく宣言し，後続stepでも同じ状態変数を参照する．
        if key not in self.runtime_env:
            # Jobとenv名からSMV識別子を作り，異なるJobの同名envを分離する．
            value = symbol('runtime_env', ':'.join(key))
            # 値が空文字なのか，まだ設定されていないのかを区別する別のBoolean名を作る．
            assigned = value + '_set'
            # 初期値と，明示的に空文字を書いた状態を表せるよう空文字を最初の候補にする．
            domain = ['']
            # 識別子・設定済みフラグ・可変domainを組で保存し，後で候補だけを拡張できるようにする．
            self.runtime_env[key] = (value, assigned, domain)
            # env値のVARを空文字で初期化し，後のdomain伝播で候補を増やす．
            self.ts.var(value, [atom('')], atom(''), job.source)
            # 設定済みフラグはFALSEから始め，GITHUB_ENVへの書込み時にTRUEへ更新する．
            self.ts.var(assigned, ['FALSE', 'TRUE'], 'FALSE', job.source)
        # 共有している3要素をアンパックし，値の候補と設定済み条件を別々に使えるようにする．
        value, assigned, domain = self.runtime_env[key]
        # dict.fromkeysで順序を保ち重複を除く．
        # *は既存domainと変換後候補をlist内へ展開する．
        domain[:] = list(dict.fromkeys([*domain, *map(ex.string, values)]))
        # 拡張したdomainをSMVの列挙名へ写し，候補1個の場合は定数化防止の補助値も加える．
        self.ts.variables[value] = (
            [atom(v) for v in domain] + ([atom('__unused__')] if len(domain) == 1 else []),
            atom('')
        )
        return (value, assigned, domain)

    # 同じstep output/envの由来とtaintを共有する．
    # cleanなArtifact由来もtaintedな入力も区別できる．
    def value_label(self, kind, job, identity, taint=False, component=None):
        """output/envの由来・taint・依存関係のVARを共通化する．"""
        # data/controlの指定があれば依存関係用の表を使い，なければ下のorigin/taint用の表を使う．
        if component:
            # ラベルの成分・output/env種別・Job・対象名を合わせ，同じラベルだけを共有する．
            key = (component, kind, job.id, identity)
            # 初回参照時だけ依存ラベルを初期値FALSEで宣言する．
            if key not in self.value_dependencies:
                token = symbol(component, ':'.join(key))
                self.ts.var(token, ['FALSE', 'TRUE'], 'FALSE', job.source)
                # 生成したVAR名を保存し，書込み側と読取り側が同じ依存条件を参照するようにする．
                self.value_dependencies[key] = token
            return self.value_dependencies[key]
        # taint=Trueなら汚染ラベル，そうでなければArtifact由来ラベルの辞書を選ぶ．
        table = self.value_taints if taint else self.value_origins
        # ラベルの共有単位をoutput/env種別・Job・対象名の組で表す．
        key = (kind, job.id, identity)
        # 初めて使うラベルだけを宣言し，既存ラベルの履歴を初期化し直さない．
        if key not in table:
            # originとtaintでprefixを分け，同じ対象でも別のBoolean VARを使う．
            name = symbol('taint' if taint else 'origin', ':'.join(key))
            # 依存が発生する前はFALSEとし，後の書込み・伝播で条件付き更新を登録する．
            self.ts.var(name, ['FALSE', 'TRUE'], 'FALSE', job.source)
            # ラベルのSMV記号を選択した辞書へ登録し，以後の参照で再利用する．
            table[key] = name
        return table[key]

    def environment_values(self, job, step, name, resolving):
        """step env，実行時env，job/workflow envの順に候補を解決する．"""
        # Jobが保持するrun番号を添字にして，所属workflowと外部contextを取得する．
        run = self.runs[job.run]
        # step位置でのenv設定を読み，job-level参照などstepがない位置では空辞書にする．
        step_env = step.raw.get('env', {}) if step else {}
        # step envに明示されていれば，GITHUB_ENVやjob/workflow envより優先してその式を評価する．
        if name in step_env:
            return self.template(step_env[name], job, step, resolving)
        # **の辞書展開は後ろの同名keyで上書きする．
        # step envは上の分岐で最優先に処理済み．
        base = {**run.raw.get('env', {}), **job.raw.get('env', {})}.get(name, '')
        # 実行時envが未設定の場合に使う，job/workflow envの値候補と条件を求める．
        fallback = self.template(base, job, step, resolving)
        # そのenvへの実行時書込みがモデルにないなら，設定済みフラグによる分岐は不要となる．
        if (job.id, name) not in self.runtime_env:
            return fallback
        # 共有している3要素をアンパックし，値の候補と設定済み条件を別々に使えるようにする．
        value, assigned, domain = self.runtime_env[job.id, name]
        # assignedがTRUEならruntime値，FALSEならjob/workflow由来のfallback値を選ぶ条件を付ける．
        return [( v, AND(assigned, f'{value} = {atom(v)}') ) for v in domain] + [(v, AND('!' + assigned, g)) for v, g in fallback]

    def reference_label(self, ref, job, step, taint=False, seen=(), component=None):
        """steps/needs/envの参照から，指定した依存ラベルの条件を得る．"""
        # 同じ参照へ再帰して戻った場合はFALSEを返して探索を止め，ここだけで安全判定はしない．
        if ref in seen:
            return 'FALSE'
        # tupleの展開で訪問経路を新しく作る．
        # 同じ参照へ戻る再帰を抑えるために使う．
        seen = (*seen, ref)
        # steps.id.outputs.nameの参照形を調べ，同じstep outputの依存ラベルへ対応付ける．
        match = re.fullmatch(r'steps\.([\w-]+)\.outputs\.([\w-]+)', ref)
        if match:
            # steps.checkout.outputs.sha等からstep idとoutput名を取り出し，同じラベルslotへ結ぶ．
            sid, name = match.groups()
            return self.value_label('output', job, sid + ':' + name, taint, component)
        # needs.job.outputs.nameの参照形を調べ，job outputの元式を再帰的にたどる．
        match = re.fullmatch(r'needs\.([\w-]+)\.outputs\.([\w-]+)', ref)
        if match:
            # needsで結ばれた同名Jobだけを選び，参照できないJobのoutput由来を混ぜない．
            deps = [j for j in self.runs[job.run].jobs if j.original == match[1] and j.id in job.needs]
            # 一意に決まる依存先だけをたどり，matrix集約の未実装部分をここで推測しない．
            if len(deps) == 1:
                dep = deps[0]
                # job outputsの式を依存先Jobの文脈でたどり，元のstep output由来へ戻す．
                return self.template_label(
                    dep.raw.get('outputs', {}).get(match[2], ''),
                    dep,
                    None,
                    taint,
                    seen,
                    component
                )
        # env参照では値と同じ優先順を使い，step指定・runtime設定・job/workflow指定の由来を分ける．
        if ref.startswith('env.'):
            name = ref[4:]
            # step envの式を取り出し，その値を選ぶ最優先の由来を調べる．
            env = step.raw.get('env', {}) if step else {}
            # step envにある参照は，実行時envやjob/workflow側へ進まずその式のラベルを返す．
            if name in env:
                return self.template_label(env[name], job, step, taint, seen, component)
            # job envがworkflow envを上書きする辞書を作り，runtime未設定時の由来を調べる．
            base = {**self.runs[job.run].raw.get('env', {}), **job.raw.get('env', {})}.get(name, '')
            # fallbackの値そのものではなく，その式が持つ指定成分のラベル条件を求める．
            fallback = self.template_label(base, job, step, taint, seen, component)
            # GITHUB_ENVへ書かれる名前なら，設定済みフラグによってruntime側とfallback側を分ける．
            if (job.id, name) in self.runtime_env:
                # 使わない値の識別子とdomainを_で受け，envが設定済みかのフラグだけを取り出す．
                _, assigned, _ = self.runtime_env[job.id, name]
                # runtime設定済みならその由来，未設定ならfallback由来が成立する条件をORにする．
                return OR([
                    AND(assigned, self.value_label('env', job, name, taint, component)),
                    AND('!' + assigned, fallback)
                ])
            return fallback
        return 'FALSE'

    def template_label(self, text, job, step, taint=False, seen=(), component=None):
        """埋込み式が参照する由来・taint・依存条件を集約する．"""
        # 1つのtemplate中で参照した由来・taint・依存条件を集める．
        labels = []
        # 文字列に埋め込まれた各GitHub式を，出現順に解析する．
        for match in re.finditer(r'\$\{\{(.*?)}}', str(text)):
            try:
                # ${{と}}を除いた式本文をASTへ変換し，その内部のrefやhashFilesを調べる．
                node = ex.parse(match[1])
                # set内包表記で同じ参照を重複除去し，sortedで安定した順に条件へまとめる．
                refs = {n.value for n in ex.walk(node) if n.kind == 'ref'}
                # ASTの各nodeを走査し，literal patternだけのhashFiles呼出しがあるか調べる．
                for call in ex.walk(node):
                    if call.kind == 'call' and call.value == 'hashfiles' and all((a.kind == 'literal' and isinstance(a.value, str) for a in call.args)):
                        from .policies import ordered_patterns
                        # 同じJobの既知fileからhashFilesのpatternに合うものだけを取り出す．
                        paths = [p for jid, p in self.files if jid == job.id and ordered_patterns([a.value for a in call.args], p)]
                        # hashの値は対象fileのtaintまたは由来に依存するが，この処理ではcontrol依存を付けない．
                        labels.extend((self.file(job, p) + ' = tainted' if taint else 'FALSE' if component == 'control' else self.origins.get((job.id, p), 'FALSE') for p in paths))
                # 重複除去した参照名を安定した順にたどり，参照先のラベル条件を集める．
                labels.extend((self.reference_label(r, job, step, taint, seen, component) for r in sorted(refs)))
            # 値の変換側が未対応を診断するため，ラベル収集中の例外だけをここで抑える．
            except ex.UnsupportedExpression:
                pass
        # 値の変換側が未対応診断を記録する．
        # ここで安全判定を行わない．
        return OR(labels)

    # double quote内の単純な$NAME/${NAME}だけを扱う．
    # command substitution等は受け付けない．
    def shell_value(self, raw, expand, job, step):
        """引用内の単純変数展開を，有限値の候補として組み立てる．"""
        # GitHub式を先に評価し，Shellの$NAME展開に渡す文字列候補とguardを得る．
        values = self.template(raw, job, step)
        # 値に対応するorigin・taint・data・controlの4成分を，順番を固定して求める．
        labels = [
            self.template_label(raw, job, step),
            self.template_label(raw, job, step, True),
            self.template_label(raw, job, step, component='data'),
            self.template_label(raw, job, step, component='control')
        ]
        # 単引用符などShell変数を展開しない書込みでは，GitHub式だけを評価した候補を返す．
        if not expand:
            return (values, *labels)
        # 各GitHub式候補に対するShell変数展開の結果をためる．
        result = []
        # GitHub式の展開結果ごとに，文字列と成立条件を保って$NAMEの置換を進める．
        for text, guard in values:
            text = ex.string(text)
            # GitHub式の置換値をShellとして再解釈する経路は，注入効果を省略せず未対応とする．
            if '${{' in raw and any((c in text for c in '$`"\\\n\r')):
                self.diagnostic(
                    'shell-template-injection',
                    'expanded expression contains shell metacharacters',
                    step.source
                )
            # $NAMEと${NAME}だけの出現位置を列挙し，それ以外のparameter expansionは下で診断する．
            matches = list(re.finditer(r'\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))', text))
            # 対応した単純参照を除いても$が残る場合は，未解釈のShell展開があることを診断する．
            if '$' in re.sub(r'\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)', '', text):
                self.diagnostic('shell-value-expansion', 'unsupported shell parameter expansion', step.source)
            # 現在までの文字列候補を空から始め，候補のguardを引き継いで置換範囲を管理する．
            rows, offset = ([('', guard)], 0)
            # 変数参照を左から置換し，直前までの文字列候補とenv候補の直積を作る．
            for match in matches:
                # ${NAME}と$NAMEのどちらで一致したかに応じて，空でないgroupから名前を選ぶ．
                name = match[1] or match[2]
                # Shell変数名を共通のenv参照へ変換し，step/env/GITHUB_ENVの優先順を共用する．
                alternatives = self.resolve('env.' + name, job, step)
                # prefix候補と環境変数候補を組み合わせる．
                # 途中でdomainが大きくなりすぎれば停止する．
                rows = [(p + text[offset:match.start()] + ex.string(v), AND(g, h)) for p, g in rows for v, h in alternatives]
                # 変数展開の直積が上限を超えた場合は例外にし，一部候補だけを採用しない．
                if len(rows) > self.scenario.get('bounds', {}).get('expressionProducts', 8192):
                    raise ex.UnsupportedExpression('shell value product exceeds configured bound')
                # 展開したenvのArtifact由来を，元のtemplateの由来条件へORで加える．
                labels[0] = OR([labels[0], self.reference_label('env.' + name, job, step)])
                # 同じenvのtaintも独立して伝え，cleanなArtifact由来と汚染を混同しない．
                labels[1] = OR([labels[1], self.reference_label('env.' + name, job, step, True)])
                # 4成分のうち位置2はdata，位置3はcontrolとして，対応するenvラベルを伝える．
                for index, component in [(2, 'data'), (3, 'control')]:
                    # 成分ごとに元のラベルと展開したenvの依存条件をORにして保持する．
                    labels[index] = OR([
                        labels[index],
                        self.reference_label('env.' + name, job, step, component=component)
                    ])
                offset = match.end()
            # 最後の変数以降に残ったliteralも加え，すべての値候補を結果へ追加する．
            result.extend(((p + text[offset:], g) for p, g in rows))
        return (result, *labels)

    def propagate_domains(self):
        """output/envの書込み候補を読み，有限domainを拡張する．"""
        # matrix展開後の各Jobを走査し，現在のJobに属する設定・step・状態だけを処理する．
        for job in self.jobs:
            # このJobのstepを元の順序で走査し，各stepの契約・値・sourceを参照する．
            for step in job.steps:
                # 単純Shell契約で認識した操作tupleを，元のscriptと同じ順序で取り出す．
                for op in step.ops:
                    # 候補伝播の対象をoutput/envへの書込みに絞り，file操作やsinkはここで処理しない．
                    if op[0] not in {'output', 'runtime-output', 'runtime-env'}:
                        continue
                    # 単純outputはGitHub式だけを，環境ファイルは引用に応じたShell変数展開も評価する．
                    values = self.template(op[2], job, step) if op[0] == 'output' else self.shell_value(op[2], op[3], job, step)[0]
                    if op[0] == 'runtime-env':
                        # GITHUB_ENVへ書き得る値を共有envのdomainへ追加し，状態遷移はまだ生成しない．
                        self.environment_slot(job, op[1], [v for v, g in values])
                    else:
                        # GITHUB_OUTPUTへ書き得る値を空文字とともに追加し，後続参照で同じdomainを使う．
                        self.output(job, step.id, op[1], ['', *[ex.string(v) for v, g in values]])

    def emit_file_command(
        self,
        kind,
        name,
        values,
        origin,
        taint,
        job,
        step,
        guard,
        data=None,
        control='FALSE'
    ):
        """環境ファイル/outputの値とラベルの更新規則を登録する．"""
        # GITHUB_ENVはjobの共有slot，GITHUB_OUTPUTはstepのoutput slotへ書く規則に分ける．
        if kind == 'runtime-env':
            # envの値VAR・設定フラグ・domainを取得し，書込み先を確定する．
            token, assigned, domain = self.environment_slot(job, name, [v for v, g in values])
            # このstepが実行された場合は設定済みにし，以後のstepでruntime envを選べるようにする．
            self.ts.update(assigned, guard, 'TRUE')
            # ラベルの保存先もJob共有envの名前に対応させる．
            label_kind, identity = ('env', name)
        else:
            token, _ = self.output(job, step.id, name, ['', *[ex.string(v) for v, g in values]])
            # outputのラベルはstep idとoutput名の組にし，別stepの同名outputと分離する．
            label_kind, identity = ('output', step.id + ':' + name)
        # 環境ファイルの改行・複数行構文は別契約が必要．
        # 1行の有限値だけで成功扱いしない．
        if any(('\n' in ex.string(v) or '\r' in ex.string(v) for v, g in values)):
            self.diagnostic(
                'environment-file-multiline',
                'multiline environment file values require delimiter parsing',
                step.source
            )
        # 書込み値の各候補について，step実行guardと候補自身の条件を合わせた更新を作る．
        for value, condition in values:
            # 規則の先頭へ置くため，同じstep内の後の環境ファイル書込みが優先される．
            # 通常のupdateの末尾追加とは異なる．
            self.ts.rules[token].insert(0, (AND(guard, condition), atom(ex.string(value))))
        # stepのifで選ばれた事実はcontrol依存へ加える．
        # 由来・taint・data/controlは別のVARへ保存する．
        control = OR([control, self.template_label(step.raw.get('if', ''), job, step)])
        # stepのifを左右した汚染もtaintへ加え，値そのものがliteralでも制御による影響を残す．
        taint = OR([taint, self.template_label(step.raw.get('if', ''), job, step, True)])
        # 値の由来と分岐の由来を合わせ，この書込みがArtifact由来に依存する条件を求める．
        source_origin = OR([origin, control])
        # dataとcontrolを別ラベルへ書き込み，data未指定時には元のoriginを代用する．
        for component, label in [('data', origin if data is None else data), ('control', control)]:
            # data/controlのラベル更新も先頭へ入れ，同じstep内では最後の書込みを優先する．
            self.ts.rules[self.value_label(label_kind, job, identity, component=component)].insert(0, (guard, label))
        # originとtaintの更新先を切り替え，同じ書込みguardの下でそれぞれの値を設定する．
        for is_taint, label in [(False, source_origin), (True, taint)]:
            # origin/taintも値と同じ書込み順で更新し，値だけ新しくラベルだけ古いという不整合を避ける．
            self.ts.rules[self.value_label(label_kind, job, identity, is_taint)].insert(0, (guard, label))

    # snapshotを明示された場合だけ内容を読む．
    # 現在のcheckoutが実行時と同一という外部仮定を残す．
    def prepare_snapshots(self):
        """明示snapshotから通常fileの内容とhashを読み，contextへ記録する．"""
        from pathlib import Path
        import hashlib
        # hashに用いたsnapshotやpatternの根拠を，manifestへ記録するためのリストを作る．
        self.hash_facts = []
        # scenarioの各runに明示snapshotがあるかを調べ，あるrunだけを内容収集の対象にする．
        for context in self.scenario.get('runs', []):
            # 各runに明示されたsnapshotパスだけを読み，通常のホスト作業ディレクトリを暗黙に使わない．
            snapshot = context.get('repositorySnapshot')
            # snapshot未指定のrunではファイルシステムの収集を行わず，次のrunへ進む．
            if not snapshot:
                continue
            # 明示snapshotのルートを正規化し，相対pathの基準にする．
            root = Path(snapshot).resolve()
            # 入力がディレクトリでなければ，ファイル在庫を作らず例外にする．
            if not root.is_dir():
                raise ValueError('repositorySnapshot must be a readable directory')
            # 明示済みのcheckoutValuesを複製し，snapshotから得た値で既存指定を上書きしないようにする．
            contents = dict(context.get('checkoutValues', {}))
            records = {}
            # 明示snapshotだけを読む．
            # 順序をそろえ，.git・symlink・非通常fileを下の分岐で除外する．
            for path in sorted(root.rglob('*')):
                # Git内部の履歴・設定を入力ファイル在庫へ含めず，次のpathへ進む．
                if '.git' in path.relative_to(root).parts:
                    continue
                # symlink先の内容を通常fileと同じ契約で読むことはせず，診断して除外する．
                if path.is_symlink():
                    self.diagnostic(
                        'snapshot-symlink',
                        'snapshot symlinks require a workspace containment contract'
                    )
                    continue
                # ディレクトリ等を内容候補へ入れず，通常fileだけを読み取る．
                if not path.is_file():
                    continue
                # snapshot rootを除いた/区切りの相対名にし，workspaceのpathと照合できる形へする．
                name = path.relative_to(root).as_posix()
                # hash計算と文字列候補の作成に，同じファイルのbyte列を使う．
                raw = path.read_bytes()
                try:
                    # 内容domainがUTF-8文字列を前提とするため，byte列を明示的にUTF-8で復号する．
                    value = raw.decode('utf-8')
                # binary等の非UTF-8は別契約が必要なので，診断して内容候補から除く．
                except UnicodeDecodeError:
                    self.diagnostic(
                        'snapshot-binary',
                        'binary snapshot content is outside the text content domain: ' + name
                    )
                    continue
                # 明示checkoutValuesがあれば優先する．
                # snapshotが過去runと同じだという仮定はhash_factsに残す．
                contents.setdefault(name, [value])
                # 読み取ったbyte列のhashを記録し，snapshot内容を後から確認できるようにする．
                records[name] = hashlib.sha256(raw).hexdigest()
            # 収集した内容を複製scenarioのcontextへ保存し，呼出し元の元辞書は変更しない．
            context['checkoutValues'] = contents
            # 明示在庫と読めた内容の名前を集合和でまとめ，重複を除いて安定した順に並べる．
            context['checkoutFiles'] = sorted(set(context.get('checkoutFiles', [])) | set(contents))
            # この明示snapshotを完全な実行時在庫と見なす仮定をcontextへ設定する．
            context['fileInventoryComplete'] = True
            self.hash_facts.append(dict(
                kind='repository-snapshot',
                root=str(root),
                files=records,
                assumption='supplied snapshot is the checked-out runtime inventory; UTF-8 regular files only'
            ))

    # hashFilesは式の参照nodeへ置換する．
    # 同じ呼出しは同じfile状態・同じ未知記号を参照する．
    def lower_hash_calls(self, node, job, step):
        """hashFilesの呼出しを，専用の共有参照Nodeへ置き換えた木を作る．"""
        import json
        # ASTがhashFiles関数呼出しである場合だけ，専用の参照nodeへ変換する．
        if node.kind == 'call' and node.value == 'hashfiles':
            # 引数なし・動的引数・非文字列patternでは有限の対象を確定できないため，未対応式にする．
            if not node.args or any((a.kind != 'literal' or not isinstance(a.value, str) for a in node.args)):
                raise ex.UnsupportedExpression('hashFiles requires literal workspace patterns')
            # literal nodeからpattern文字列だけを抜き出し，呼出しの同一性に使う．
            patterns = [a.value for a in node.args]
            # pattern列を安定したJSON文字列にし，同じ呼出しを同じ共有参照として解決する．
            identity = json.dumps(patterns, separators=(',', ':'))
            # 呼出しを専用refへ置くことで，同じhashFiles参照を式の共有参照としてlowerへ渡す．
            return ex.Node('ref', '__hashFiles.' + identity)
        # hashFiles以外のnodeはkind/valueを保ち，子nodeだけを再帰的に変換する．
        return ex.Node(node.kind, node.value, tuple((self.lower_hash_calls(a, job, step) for a in node.args)))

    def hash_values(self, identity, job, step):
        """現時点のfile状態に対応するhashFilesの有限候補を列挙する．"""
        import hashlib
        import itertools
        import json
        from . import policies
        # 専用refに埋め込んだJSONをpatternのリストへ戻す．
        patterns = json.loads(identity)
        context = self.runs[job.run].context
        # patternと順序の契約はLinuxを前提とするため，他OSなら未対応診断を残す．
        if context.get('runnerOS') != 'Linux':
            self.diagnostic(
                'hashfiles-platform',
                'hashFiles glob and ordering contract currently requires Linux',
                step.source if step else job.source
            )
        # 絶対path・親ディレクトリ・バックスラッシュを拒否し，workspace内の相対pathだけを扱う．
        if any((p.startswith('/') or '..' in p.split('/') or '\\' in p for p in patterns)):
            raise ex.UnsupportedExpression('hashFiles patterns must be normalized relative workspace paths')
        # globを使うのに在庫が完全と明示されていなければ，全対象を知ることができないため未知hashへ戻す．
        if any((any((c in p for c in '*?')) for p in patterns)) and (not context.get('fileInventoryComplete')):
            return self.unknown_hash(
                identity,
                job,
                step,
                'glob hash requires a complete runtime file inventory'
            )
        # job内の既知file在庫からpatternに合うpathだけを集める．
        # globで在庫が不完全なら上で未知扱いにする．
        paths = [path for jid, path in self.files if jid == job.id and policies.ordered_patterns(patterns, path)]
        # 肯定のliteral pathはglob在庫とは別にslotを用意し，未存在の候補も状態として表す．
        literal_paths = [p for p in patterns if not p.startswith('!') and (not any((c in p for c in '*?')))]
        # globを含まない肯定pathを走査し，不在状態も含めた内容slotを用意する．
        for path in literal_paths:
            self.file(job, path)
            self.content(job, path)
            if path not in paths and policies.ordered_patterns(patterns, path):
                paths.append(path)
        # 複数fileの連結順を相対path順へ固定し，この順序が仮定であることを下で診断する．
        paths.sort()
        # 実runnerの列挙順を実証したわけではないため，複数fileの順序仮定を記録する．
        if len(paths) > 1:
            self.diagnostic(
                'hashfiles-order',
                'multiple-file hash uses sorted workspace paths; runner enumeration order is an explicit assumption',
                step.source if step else job.source,
                'unknown'
            )
        # 対象file数が上限を超えると全候補を扱えないため，共有された未知hashへ戻す．
        if len(paths) > self.scenario.get('bounds', {}).get('hashFiles', 8):
            return self.unknown_hash(identity, job, step, 'hashFiles file-count bound exceeded')
        # Cache復元のbyte内容はモデル化していないため，その内容とhashとの関係が不完全なことを診断する．
        if any((s.contract in {'actions/cache-v4', 'actions/cache/restore-v4'} for s in job.steps)):
            self.diagnostic(
                'hashfiles-cache-bytes',
                'restored Cache bytes are not represented; digest relation is incomplete',
                job.source
            )
        rows = []
        # 対象fileごとに不在・存在時の内容の候補列を作り，直積の各次元にする．
        for path in paths:
            # hash対象fileの存在・taint VARを取得し，absentかどうかを候補条件へ使う．
            state = self.file(job, path)
            # hash計算には内容VARだけを使い，Artifact由来の戻り値は_で受け取って使わない．
            content, _ = self.content(job, path)
            # 不在をNone，存在時を各内容候補として表す．
            # 値候補に加え，file/contentの成立条件も持たせる．
            options = [(None, state + ' = absent')]
            # 存在時の各内容候補を，存在guardとcontent一致guardの両方に結び付ける．
            options.extend(((value, AND(state + ' != absent', f'{content} = {atom(value)}')) for value in self.values))
            # fileごとの不在・内容候補の列を追加し，後で各fileの直積を列挙する．
            rows.append(options)
        # 直積サイズの積は1から始め，fileが0個なら空積の候補1個を扱えるようにする．
        count = 1
        for row in rows:
            # 各fileの候補数を掛け，列挙前に組合せ総数を見積もる．
            count *= len(row)
        # 内容候補の直積が上限を超えた場合は，一部のhashだけを計算せず未知値へ戻す．
        if count > self.scenario.get('bounds', {}).get('expressionProducts', 8192):
            return self.unknown_hash(identity, job, step, 'hashFiles finite content product exceeds bound')
        # すべてのfile状態の組合せに対するhashとguardをためる．
        results = []
        # 各fileの不在/内容候補を組み合わせる．
        # 存在fileが0個なら空文字，それ以外は二段のSHA-256である．
        for selected in itertools.product(*rows):
            # 今回の組合せから不在を表すNoneを除き，存在するfileの内容だけを取り出す．
            present = [v for v, g in selected if v is not None]
            # 各fileのSHA-256のbyte列を連結して再度SHA-256を取り，存在fileがなければ空文字を返す．
            digest = hashlib.sha256(b''.join((hashlib.sha256(v.encode()).digest() for v in present))).hexdigest() if present else ''
            # 計算したhashと，今回選んだfile/content候補がすべて成立するSMV条件を対応付ける．
            results.append((digest, AND(*(g for v, g in selected))))
        # 計算したhashの元pattern・pathと，ソート順という仮定をmanifest向けに残す．
        record = dict(
            kind='hashFiles',
            job=job.id,
            step=step.id if step else None,
            patterns=patterns,
            paths=paths,
            interpretation='SHA256 of per-file SHA256 bytes in sorted path order; state-dependent finite content'
        )
        # 固定点反復などで同じhash根拠が再登場しても，manifestの記録を重複させない．
        if record not in self.hash_facts:
            self.hash_facts.append(record)
        return results

    def unknown_hash(self, identity, job, step, reason):
        """不明hashを共有代表値へ置き換え，未対応診断を残す．"""
        # これは有限の等価関係用代表値．
        # 実hashだとは主張せず，必ずunsupportedで安全判定を止める．
        key = job.id + ':' + (step.id if step else '') + ':' + identity
        token = symbol('unknown_hash', key)
        # 未知hashは等価・非等価の代表値であり，実際のSHA-256値を表す候補ではない．
        values = ['', 'symbolic-hash-a', 'symbolic-hash-b']
        # 同じ未知hashの参照には同じ固定値を使い，参照のたびに別の値を選ばせない．
        self.ts.frozen[token] = [atom(v) for v in values]
        self.diagnostic('hashfiles-domain', reason, step.source if step else job.source)
        return [(v, f'{token} = {atom(v)}') for v in values]
