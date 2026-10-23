# 構造化Shellの操作列を，共通のTransitionSystemへ変換する．
# 独立したSMV生成器は持たない．
# 内容の有限候補，taint，Artifact由来の印，ファイルごとの検証状態を別々に管理する．
# 検証前のreadや不一致後の利用は履歴として残す．
# 後の遮断で過去の利用を取り消さない．
"""構造化Shellの操作列を，内容・由来・完全性確認の遷移へ下げる．

入力はShellProgram付きのstep，job，共通の実行条件であり，実Shellは実行しない．
内容の有限候補を宣言し，local変数と分岐条件を追ってTransitionSystemへ更新規則を追加する．
同じfileのSHA-256比較と検証基準の信頼条件を区別し，別fileへ検証状態を流用しない．
read・検証read・検証後利用・不一致後利用を履歴として残す．後の遮断で過去の利用は消さない．
模擬sinkのmarkerは研究用の地点であり，実権限や外部publish成功とは別である．
WorkflowCompilerに継承され，Dataflowのoutput/envラベルと同じ状態系を使う．
"""
import hashlib
import re
from pathlib import PurePosixPath
from . import expressions as ex
from .transition import atom, symbol, conjunction as AND, disjunction as OR

class ShellLowering:
    # 内容domainと利用履歴を宣言する．
    # 内容候補は明示入力であり，任意のbyte列の網羅ではない．
    """構造化Shellと有限file内容の意味を共通IRへ下げるmixin．"""

    def _prepare_shell_state(self):
        """内容候補・完全性状態・利用履歴を宣言する．"""
        # 構造化ShellまたはhashFilesを使う入力があるか調べ，内容の詳細状態が必要かを決める．
        self.has_structured = any((s.program for j in self.jobs for s in j.steps)) or any(('hashfiles(' in str(r.raw).lower() for r in self.runs))
        # 自動導出したupload fileは，単純なcatを使う場合もmember名を保持してdownload先へ復元する．
        self.has_structured = self.has_structured or bool(self.scenario.get('automaticAssumptions'))
        # Jobとpathから内容VARを引く表を作り，absent/clean/taintedの状態とは分ける．
        self.contents = {}
        # 同じfileのArtifact由来フラグを保持し，汚染の有無とは別の軸で追跡する．
        self.origins = {}
        # upload対象rootに含まれるmember一覧を，保存物tokenごとに記録する．
        self.bundles = {}
        # output由来用の管理辞書を初期化するが，共有ラベルの取得はvalue_labelへ委ねる．
        self.output_origins = {}
        # 完全性確認をJob・fileごとに保持し，別fileの検証結果を流用しない．
        self.integrity_files = {}
        # 内容domainを空文字と代表値otherから始め，後で明示内容とliteral書込みを加える．
        self.values = ['', 'other']
        # 入力の各Runを走査し，同じRunに属するJobや実行contextをまとめて参照する．
        for run in self.runs:
            # 各runに明示されたcheckout内容の候補列を取り出す．
            for values in run.context.get('checkoutValues', {}).values():
                # 読み取った候補列を共通の有限内容domainへ加える．
                self.values.extend(values)
        # matrix展開後の各Jobを走査し，現在のJobに属する設定・step・状態だけを処理する．
        for job in self.jobs:
            # このJobのstepを元の順序で走査し，各stepの契約・値・sourceを参照する．
            for step in job.steps:
                # literal書込みの内容も候補へ追加し，書込み後のhashやreadを表せるようにする．
                self.values.extend((op[2] for op in step.ops if op[0] == 'write'))
        # 内容の最初の出現順を保って重複を除き，状態候補を必要以上に増やさない．
        self.values = list(dict.fromkeys(self.values))
        # 詳細内容を使わない入力では，この先の内容・完全性・構造化Shell専用の処理を省く．
        if not self.has_structured:
            return
        for name in [
            'artifact_read',
            'verification_read',
            'post_verification_use',
            'post_integrity_mismatch_use',
            'artifact_dependent_use',
            'integrity_blocked',
            'simulated_sink_site_reached',
            'artifact_dependent_sink',
            'simulated_sink_result_true'
        ]:
            self.ts.var(name, ['FALSE', 'TRUE'], 'FALSE')
        # 全体の完全性確認履歴を未実行から始め，verified/mismatch/unverifiableを区別する．
        self.ts.var(
            'integrity_state',
            ['integrity_not_run', 'integrity_verified', 'integrity_mismatch', 'integrity_unverifiable'],
            'integrity_not_run'
        )
        # 研究用の模擬危険経路と，実権限契約のbad_authorityを別名のDEFINEへ置く．
        # 両者を混同しない．
        self.ts.defines.update(
            bad_simulated_path='artifact_dependent_sink & simulated_sink_site_reached & simulated_sink_result_true',
            bad_real_authority_path='bad_authority',
            download_success_any='transfer'
        )
        for expression in [
            'AG !bad_simulated_path',
            'AG !bad_real_authority_path',
            'AG (artifact_dependent_use -> download_success_any)',
            'AG (artifact_dependent_sink -> artifact_dependent_use)',
            'AG !post_integrity_mismatch_use',
            'AG !verification_read',
            'AG !post_verification_use',
            'AG !artifact_dependent_use',
            'AG !simulated_sink_site_reached',
            'AG !artifact_dependent_sink',
            'AG (integrity_state != integrity_verified)',
            'AG (integrity_state != integrity_mismatch)',
            'AG !integrity_blocked'
        ]:
            # CTL式から安定したproperty名を作り，NuSMVで検査する性質として登録する．
            self.ts.properties[symbol('property', expression)] = expression
        # matrix展開後の各Jobを走査し，現在のJobに属する設定・step・状態だけを処理する．
        for job in self.jobs:
            for path in self.runs[job.run].context.get('checkoutFiles', []):
                self.content(job, path)
            # このJobのstepを元の順序で走査し，各stepの契約・値・sourceを参照する．
            for step in job.steps:
                for operation in step.ops:
                    if operation[0] == 'write':
                        self.content(job, operation[1])
                    elif operation[0] == 'copy':
                        self.content(job, operation[1])
                        self.content(job, operation[2])
                if not step.program:
                    continue
                # Shellのlocal変数が持ち得る候補を先に集め，後段のoutput/envのdomainを宣言する．
                # このstep内のShell変数ごとに候補値を集め，GITHUB_ENV/OUTPUTの宣言へ使う．
                domains = {}
                # 構造化Shellの操作IRを元の順でたどり，参照するpath・値・分岐・書込みを処理する．
                for op in step.program.operations:
                    if op.kind == 'copy':
                        for path in op.path.split('->'):
                            self.content(job, path)
                    elif op.kind == 'grep-assert':
                        self.content(job, op.path)
                    elif op.kind in {'read-trim', 'digest', 'test-file'}:
                        self.content(
                            job,
                            self.shell_path(
                                job,
                                step,
                                op.reads[0],
                                op.path or op.value if op.kind == 'test-file' else op.path or ''
                            )
                        )
                    elif op.kind == 'literal-assign':
                        # 同じlocal変数に代入されるliteralを，既存候補を残しながら追加する．
                        domains.setdefault(op.writes[0], []).append(str(op.value))
                    if op.kind == 'read-trim':
                        # trで指定文字を除いた各候補を，代入先Shell変数のdomainとして保持する．
                        domains[op.writes[0]] = list(dict.fromkeys((self.trim( v, op.value ) for v in self.values)))
                    elif op.kind == 'digest':
                        # 各内容候補のSHA-256と代表値otherを，digestを受け取るShell変数のdomainへ入れる．
                        domains[op.writes[0]] = [hashlib.sha256(v.encode()).hexdigest() for v in self.values] + ['other']
                    elif op.kind in {'github-output', 'github-env'}:
                        # IRのoutput:NAME/env:NAMEからNAMEだけを取り出す．
                        key = op.writes[0].split(':')[1]
                        # local変数の候補を展開し，未設定の空文字と未確定のotherも出力候補へ含める．
                        values = ['', *domains.get(op.reads[0], ['other']), 'other']
                        if op.kind == 'github-env':
                            self.environment_slot(job, key, values)
                        else:
                            self.output(job, step.id, key, values)
                            self.output_origin(job, step.id, key)
        self.ts.defines['bad_real_authority_path'] = 'bad_authority'

    # fileの内容とArtifact由来の印を別変数にする．
    # clean/taintedだけから由来を決めない．
    def content(self, job, path):
        """file内容とArtifact由来の印を別のVARとして共有する．"""
        path = str(path).removeprefix('./').rstrip('/') or '.'
        # 同じpathでもJobが違えば別workspaceなので，2つを合わせて状態の共有キーにする．
        key = (job.id, path)
        # 内容VARに加え，そのfileの存在・taintを表す基本VARも準備する．
        self.file(job, path)
        # 初回参照の場合だけ内容と由来のVARを宣言し，以後は同じものを使う．
        if key not in self.contents:
            name = symbol('content', job.id + ':' + path)
            # 有限内容候補をSMVの列挙値へ変換し，空文字を初期値として内容VARを宣言する．
            self.ts.var(name, [atom(v) for v in self.values], atom(''), job.source)
            # 内容の識別子をJob・pathで登録し，後のread/copyで参照する．
            self.contents[key] = name
            # 内容そのものと独立したArtifact由来フラグの名前を作る．
            origin = symbol('from_artifact', job.id + ':' + path)
            self.ts.var(origin, ['FALSE', 'TRUE'], 'FALSE', job.source)
            # 由来フラグを同じJob・pathへ対応付け，checkout時とrestore時で異なる値を設定できるようにする．
            self.origins[key] = origin
        return (self.contents[key], self.origins[key])

    # step outputの値とは別に，Artifact由来の依存がある条件を保持する．
    def output_origin(self, job, sid, name):
        """step outputの由来ラベルを共通管理表から得る．"""
        return self.value_label('output', job, sid + ':' + name)

    # Shellの環境変数をpathへ解決する．
    # 複数候補や未確定pathは診断を残す．
    def shell_path(self, job, step, variable, suffix=''):
        """Shellで使う環境変数とsuffixを単一pathへ解決する．"""
        values = self.resolve('env.' + variable, job, step)
        # 環境変数の各候補へsuffixを連結し，得られた異なるpathだけを取り出す．
        paths = list(dict.fromkeys((ex.string(v) + str(suffix or '') for v, g in values)))
        # pathが一意でない・空の場合は診断し，複数の実ファイルを1つと見なさない．
        if len(paths) != 1 or not paths[0]:
            self.diagnostic(
                'shell-path-domain',
                'file environment binding must have one static path',
                step.source
            )
            return 'unsupported-path'
        return paths[0].removeprefix('./').rstrip('/') or '.'

    # 同じrun・pathの入力内容を固定変数として共有し，checkout成功時にjobへ写す．
    def _checkout_contents(self, job, path, guard):
        """run単位で共有する入力内容を，checkout時のfileへ写す規則を作る．"""
        # 詳細内容を使わない入力では，この先の内容・完全性・構造化Shell専用の処理を省く．
        if not self.has_structured:
            return
        # checkout時の内容候補をscenarioから読み，なければ未確定の代表値で診断する．
        values = self.runs[job.run].context.get('checkoutValues', {}).get(path)
        # 明示内容がないfileについて，実checkoutの内容を推測して補わない．
        if values is None:
            values = ['other']
            self.diagnostic(
                'checkout-content-domain',
                'finite file contents were not supplied for ' + path,
                job.source
            )
        # 入力内容は同じrun・pathで共有する記号にし，別Jobのcheckoutでも元入力の相関を保つ．
        token = symbol('input_content', self.runs[job.run].id + ':' + path)
        # 候補内容をFROZENVARへ登録し，1回のrunの元入力内容が途中で変わらないようにする．
        self.ts.frozen[token] = list(dict.fromkeys((atom(v) for v in values)))
        # 同じfileの有限内容とArtifact由来フラグを取得し，値と依存を対応付けたまま扱う．
        content, origin = self.content(job, path)
        # checkout成功時に，run単位で共有した固定入力内容をこのJobのfileへ写す．
        self.ts.update(content, guard, token)
        # checkoutのfileをArtifact復元由来と誤認しないよう，由来フラグをFALSEにする．
        self.ts.update(origin, guard, 'FALSE')

    # upload対象の各fileを集め，内容と存在を保存する対象を決める．
    def _bundle_source(self, obj, source):
        """upload rootに属するmemberを集め，存在・taintの集約式を作る．"""
        # 詳細内容を使わない入力では，この先の内容・完全性・構造化Shell専用の処理を省く．
        if not self.has_structured or obj['kind'] != 'artifact':
            return (source, source + ' != absent')
        # upload対象rootの末尾/を除き，配下memberのpath比較の基準にする．
        root = obj['path'].rstrip('/')
        # upload元Jobの内容slotから，root自身またはroot配下のfileだけを選ぶ．
        paths = [path for jid, path in self.contents if jid == obj['job'].id and (path == root or path.startswith(root + '/'))]
        # 保存候補に属するmember一覧を固定し，後の保存・復元規則で同じ集合を使う．
        self.bundles[obj['token']] = paths
        # 各memberの存在・taint状態を参照するVAR名を集める．
        states = [self.file(obj['job'], path) for path in paths]
        # memberが少なくとも1つ存在する条件を作り，空のbundleを存在物として保存しない．
        exists = OR((s + ' != absent' for s in states))
        # memberのどれかがtaintedなら，bundle全体の抽象状態もtaintedとする条件を作る．
        tainted = OR((s + ' = tainted' for s in states))
        return (f'case {tainted} : tainted; {exists} : clean; TRUE : absent; esac', exists)

    # Artifact内の相対pathごとに，保存時の内容と存在の有無を保持する．
    def _save_contents(self, obj, guard):
        """Artifactのmemberごとに内容と存在の保存規則を登録する．"""
        # 詳細内容を使わない入力では，この先の内容・完全性・構造化Shell専用の処理を省く．
        if not self.has_structured or obj['kind'] != 'artifact':
            return
        job = obj['job']
        # upload対象rootの末尾/を除き，配下memberのpath比較の基準にする．
        root = obj['path'].rstrip('/')
        # 事前に確定したbundleの各memberを走査し，保存時の内容と存在のslotを作る．
        for path in self.bundles.get(obj['token'], []):
            # root配下ではrootを除いた相対名，単一fileのuploadではbasenameをmember名にする．
            relative = path[len(root):].lstrip('/') if path != root else PurePosixPath(path).name
            # 保存物の同一性とmember相対名を組にし，別Artifactの同名fileを混ぜない．
            key = (obj['token'], relative)
            # workspaceが後で変わっても保存時の内容を保持できる，object側のVAR名を作る．
            value = symbol('object_content', ':'.join(key))
            # memberが保存時に存在したかを，内容の空文字とは別のBooleanで管理する．
            present = symbol('object_member', ':'.join(key))
            # 初回だけ保存member表を作り，後続のArtifactでも共用する．
            if not hasattr(self, 'object_members'):
                self.object_members = {}
            # 同じobject・memberのVARを二重宣言せず，初回だけ作る．
            if key not in self.object_members:
                # object・相対名をキーにし，内容VAR・存在VAR・元pathを復元用に共有する．
                self.object_members[key] = (value, present, path)
                # 保存物の内容はworkspaceとは別のVARとし，保存後のworkspace変更から切り離す．
                self.ts.var(value, [atom(v) for v in self.values], atom(''), obj['source'])
                # 内容が空文字の場合も存在と区別できるよう，member存在のBooleanを別に宣言する．
                self.ts.var(present, ['FALSE', 'TRUE'], 'FALSE', obj['source'])
            # guard=Noneの事前呼出しでは宣言だけ行う．
            # 実際の保存条件がある呼出しで内容と存在の更新を追加する．
            if guard is not None:
                # 保存が成立した遷移でworkspaceの内容をobject側へ写し，以後のworkspace更新とは切り離す．
                self.ts.update(value, guard, self.content(job, path)[0])
                # 同じ保存時点の存在条件を記録し，存在しなかったmemberを復元しないようにする．
                self.ts.update(present, guard, self.file(job, path) + ' != absent')

    # 選択したArtifactの同じmemberを復元する．
    # 復元先の過去の検証状態はリセットする．
    def _restore_contents(self, job, obj, destination, guard):
        """選択されたArtifactのmemberを復元先へ対応付ける．"""
        # 詳細内容を使わない入力では，この先の内容・完全性・構造化Shell専用の処理を省く．
        if not self.has_structured:
            return
        # Cacheは詳細byte内容の復元契約がないため，必要時に診断しArtifact用member復元へは進まない．
        if obj['kind'] != 'artifact':
            if any((s.program and s.program.artifact_reads for s in job.steps)):
                self.diagnostic(
                    'cache-content-domain',
                    'structured Cache byte/digest contracts are not supplied; taint-only cache restoration remains modeled',
                    job.source
                )
            return
        # 二重のアンパックで，辞書keyのobject/memberと，値の内容/存在/元pathを取り出す．
        for (token, relative), (value, present, path) in getattr(self, 'object_members', {}).items():
            # 今回選ばれたArtifact以外のmemberは，この復元処理の対象から除く．
            if token != obj['token']:
                continue
            # ダウンロード先rootへ保存時の相対名を連結し，memberの復元先pathを作る．
            targets = [destination.rstrip('/') + '/' + relative]
            # upload対象が単一fileそのものの場合は，直接のdestinationへの対応も追加する．
            if path == obj['path']:
                targets.append(destination)
            # このmemberに対応する復元先ごとに，存在・内容・由来・検証状態の更新を登録する．
            for target in targets:
                content, origin = self.content(job, target)
                # downloadの成立に加えて，そのmemberが保存時に存在していたことも要求する．
                effect = AND(guard, present)
                # 選んだArtifactの抽象taint状態を復元先fileへ写し，内容の復元と同じguardを使う．
                self.ts.update(self.file(job, target), effect, obj['token'])
                # 復元時には現在の保存元workspaceではなく，objectに保存済みの内容をコピーする．
                self.ts.update(content, effect, value)
                # 復元したfileはcleanであってもArtifact由来として記録する．
                self.ts.update(origin, effect, 'TRUE')
                # 復元後は未検証へ戻す．
                # 同じpathに以前あったfileの検証結果を，新しく取得した内容へ流用しない．
                self.ts.update(self.file_integrity(job, target), effect, 'integrity_not_run')

    # 検証状態はjob・pathごとに持つ．
    # 別fileを検証した結果を流用しない．
    def file_integrity(self, job, path):
        """job・pathごとの完全性状態VARを取得する．"""
        # 同じpathでもJobが違えば別workspaceなので，2つを合わせて状態の共有キーにする．
        key = (job.id, path)
        # このJob・pathの初回参照だけ，未検証を初期値にした完全性VARを作る．
        if key not in self.integrity_files:
            name = symbol('integrity', job.id + ':' + path)
            self.ts.var(
                name,
                ['integrity_not_run', 'integrity_verified', 'integrity_mismatch', 'integrity_unverifiable'],
                'integrity_not_run',
                job.source
            )
            self.integrity_files[key] = name
        return self.integrity_files[key]

    # ifで利用が遮断されたときも，digest不一致による遮断の履歴を記録する．
    def _blocked_history(self, job, step, gate, condition):
        """不一致状態のfile利用がstepのifで遮断された履歴を追加する．"""
        # 構造化Shellの操作IRを元の順でたどり，参照するpath・値・分岐・書込みを処理する．
        for op in step.program.operations:
            if op.kind == 'read-trim':
                path = self.shell_path(job, step, op.reads[0], op.path or '')
                integrity = self.file_integrity(job, path)
                # step位置に到達したがifが偽で，対象fileにmismatch履歴がある場合に遮断を記録する．
                self.ts.latch(
                    'integrity_blocked',
                    AND(gate, '!(' + condition + ')', integrity + ' = integrity_mismatch')
                )

    # tr -dに相当する文字削除．
    # 文字列の両端だけでなく，内部の対象文字も削除する．
    @staticmethod
    def trim(value, characters):
        """限定tr -dに対応する指定文字の削除を行う．"""
        # tr -dの対象である\rと\nの記法を実際の改行文字へ直し，削除する文字集合にする．
        chars = str(characters or '').replace(r'\r', '\r').replace(r'\n', '\n')
        # translateの削除表を使い，両端だけでなく文字列の途中にある対象文字もすべて取り除く．
        return str(value).translate(str.maketrans('', '', chars))

    # local変数と分岐の順序を追い，成立条件付きの値・由来・file更新を生成する．
    # 同一step内で表現できない連続更新は診断する．
    # 実際のShellは実行しない．
    def _structured_shell(self, job, step, guard):
        """操作順と分岐を保持して，値・依存・検証・利用の規則を作る．"""
        # SMVの更新は同時に反映されるため，同一step内の検証結果はlocal式にも保持して後続readへ渡す．
        local_integrity = {}

        def integrity(path):
            """同じstep内の新しい検証条件を優先して取得する．"""
            # 同じstep内で直前に計算した検証結果があれば使い，なければ状態VARを参照する．
            return local_integrity.get(path, self.file_integrity(job, path))
        # 同一step内で既にコピー先になったpathを記録し，中間file状態が必要な依存を検出する．
        written = set()
        for operation in step.program.operations:
            if operation.kind == 'copy':
                # IRが保持するコピー元->コピー先を2つのpathへ分ける．
                src, dst = operation.path.split('->')
                # 以前のコピー結果へ依存する場合は，原子的stepだけでは順序を表せないことを診断する．
                if src in written or dst in written:
                    self.diagnostic(
                        'shell-sequential-effects',
                        'dependent file updates within one structured step require filesystem microstates',
                        step.source
                    )
                # このコピーの書込先を覚え，後続コピーとの依存検査に使う．
                written.add(dst)
        # 複数操作のArtifact readにset -euがなければ，失敗後の継続意味が未対応であることを診断する．
        if not any((o.kind == 'strict-mode' for o in step.program.operations)) and len(step.program.operations) > 1 and step.program.artifact_reads:
            self.diagnostic(
                'shell-failure-mode',
                'multi-command artifact reads require set -eu for failure continuation semantics',
                step.source
            )
        # これらはPython側で条件付き候補を追う辞書．
        # 実Shell環境ではない．
        # stackは入れ子分岐，activeは現在枝の条件．
        # Shellのlocal変数ごとに「値候補・成立条件」の列を保持する．
        local = {}
        # 各local変数がArtifact由来かの条件を，値とは別の辞書で保持する．
        provenance = {}
        # local変数の汚染条件を保持し，Artifact由来と同じ意味にしない．
        local_taint = {}
        # 値の計算元によるdata依存の条件を保持する．
        local_data = {}
        # if/caseの選択によるcontrol依存の条件を保持する．
        local_control = {}
        # 入れ子if/caseの親条件と依存を積み，else/fi/esacで戻れるようにする．
        stack = []
        # script入口では全経路を有効にし，分岐・検査・exitで現在位置への到達条件を絞る．
        active = 'TRUE'
        # ファイル不在・検査不一致・exit 1等，失敗して終了する経路条件をためる．
        failures = []
        # 模擬sinkに必要なstep outputを参照したか，(step id, output名)で重複なく記録する．
        metadata_refs = set()
        # Artifact由来fileの存在を確認した履歴の条件を，未確認のFALSEから始める．
        tested = 'FALSE'
        # digest計算先のlocal変数と有限のhash候補を対応付け，後の比較で識別する．
        digest_values = {}
        # digestを計算した元fileのArtifact由来条件を保存する．
        digest_origins = {}
        # digestの変数名から検査対象fileへ戻れるようにし，別fileへ検証結果を付けない．
        digest_paths = {}
        # 完全性確認の基準となるworkflow自体が信頼できるかを，明示contextから読む．
        trusted = self.runs[job.run].context.get('workflowTrusted')
        # digestが一致しても，検証基準となるworkflow自体が未信頼なら検証済みとしない．
        trust = symbol('workflow_trust', self.runs[job.run].id)
        # workflowの信頼性が不明なら，実行中一定の未知Booleanとして両方の可能性を残す．
        if trusted is None:
            # workflow信頼性が不明ならFROZENVARで固定の真偽を残し，参照ごとに違う信頼性を選ばせない．
            self.ts.frozen[trust] = ['FALSE', 'TRUE']
        else:
            # 明示されたworkflow信頼性は，SMVのTRUE/FALSEへ変換して以後の検証条件へ使う．
            trust = str(bool(trusted)).upper()

        def env(name):
            """local変数を優先し，なければenvの値と由来を得る．"""
            # Shell内で代入済みの変数を最優先にし，未代入ならenv参照へ進む．
            if name in local:
                return (local[name], provenance.get(name, 'FALSE'))
            # localで未代入なら共通env resolverを使い，GITHUB_ENVも含めた有限候補を取得する．
            values = self.resolve('env.' + name, job, step)
            # envの値候補と独立にArtifact由来条件を取得し，後の分岐やoutputへ伝える．
            origin = self.reference_label('env.' + name, job, step)
            raw = step.raw.get('env', {}).get(name, '')
            # step envがstep outputを直接参照する形かを調べ，必要metadataへの依存確認に使う．
            ref = re.fullmatch(r'\$\{\{\s*steps\.([\w-]+)\.outputs\.([\w-]+)\s*}}', str(raw))
            if ref:
                # envがstep outputへ直接結ばれている事実を記録し，後の模擬sink契約の確認へ使う．
                metadata_refs.add((ref[1], ref[2]))
            return (values, origin)

        def taint(name):
            """localまたはenv由来のtaint条件を得る．"""
            return local_taint.get(name, self.reference_label('env.' + name, job, step, True))

        def dependency(name, component):
            """指定するdata/control依存のlocal条件を得る．"""
            return (local_data if component == 'data' else local_control).get(
                name,
                self.reference_label('env.' + name, job, step, component=component)
            )

        def assign(name, values, origin, untrusted='FALSE', data=None, control='FALSE'):
            """active分岐の値を設定し，非active側の旧値を残す．"""
            # dataとcontrolを順に処理し，controlには現在の分岐stackの由来も合わせる．
            for component, table, value in [
                ('data', local_data, origin if data is None else data),
                ('control', local_control, OR([control, *[frame.get('origin', 'FALSE') for frame in stack]]))
            ]:
                # 代入しない分岐でも以前の依存ラベルを残せるよう，更新前の成分を読む．
                old = dependency(name, component)
                # active側では新しい依存，非active側では旧依存を選ぶ条件式にする．
                table[name] = OR([AND(active, value), AND('!(' + active + ')', old)])
            # taintも更新前の条件を先に取り出し，実行されない分岐の履歴を消さないようにする．
            previous_taint = taint(name)
            # activeな代入でのみ新しいtaintを使い，それ以外は以前のtaint条件を保持する．
            local_taint[name] = OR([AND(active, untrusted), AND('!(' + active + ')', previous_taint)])
            # local変数の旧候補を読み，初回代入なら空文字の候補を用意する．
            previous = local.get(name, [('', 'TRUE')])
            # 由来の旧条件も独立して取り出し，非実行側にそのまま残す．
            old_origin = provenance.get(name, 'FALSE')
            # active側は新しい値，非active側は旧値を残す．
            # ifの片側で代入しない場合も値を消さない．
            local[name] = [( v, AND(active, g) ) for v, g in values] + [(v, AND('!(' + active + ')', g)) for v, g in previous]
            # 代入が起きた側の由来と，代入されない側の旧由来をORでまとめる．
            provenance[name] = OR([AND(active, origin), AND('!(' + active + ')', old_origin)])
        # 構造化Shellの操作IRを元の順でたどり，参照するpath・値・分岐・書込みを処理する．
        for op in step.program.operations:
            # step全体の実行/成功条件と，script内で現在位置へ来る条件を合わせる．
            effect = AND(guard, active)
            # 構造化IRの操作種別を読み，分岐・read・digest・output等の効果生成を選ぶ．
            kind = op.kind
            # parserが保持した構造上の印は，ここでは独立した状態更新を追加せず次へ進む．
            if kind in {'strict-mode', 'block', 'summary-redirection', 'case-next'}:
                continue
            if kind == 'literal-assign':
                # literalの代入でも，Artifact由来の分岐に依存する場合は制御依存を残す．
                origin = OR((frame.get('origin', 'FALSE') for frame in stack))
                assign(
                    op.writes[0],
                    [(str(op.value), 'TRUE')],
                    origin,
                    OR((frame.get('taint', 'FALSE') for frame in stack)),
                    data='FALSE',
                    control=origin
                )
            elif kind in {'if-eq', 'if-eq-var'}:
                # 比較左辺の値候補と由来を取り出し，分岐条件とcontrol依存に使う．
                left, origin = env(op.reads[0])
                # 変数同士の比較なら右辺も解決し，literal比較なら値1個・由来FALSEの候補を作る．
                right, ro = env(op.reads[1]) if kind == 'if-eq-var' else ([(op.value, 'TRUE')], 'FALSE')
                # 左右の値が同じ組だけを選び，その両方のguardが成立する条件をORにする．
                predicate = OR((AND(a, b) for x, a in left for y, b in right if x == y))
                # 親のactiveと比較条件をstackへ保存する．
                # elseでは比較の否定，fiでは親の条件へ戻す．
                stack.append(dict(
                    kind='if',
                    parent=active,
                    predicate=predicate,
                    origin=OR([origin, ro]),
                    taint=OR((taint(n) for n in op.reads))
                ))
                # 比較が真の経路だけをif本体の到達条件に残す．
                active = AND(active, predicate)
                # 比較にdigest計算済み変数が含まれる場合だけ，完全性状態の更新も生成する．
                if kind == 'if-eq-var' and any((v in digest_values for v in op.reads)):
                    # 比較に使う変数のうちdigest計算済みのものを選び，検証対象fileを特定する．
                    variable = next((v for v in op.reads if v in digest_values))
                    # 比較したdigestの元fileのArtifact由来を使い，別の変数の由来へ取り違えないようにする．
                    origin = digest_origins[variable]
                    # 全体の履歴と対象file固有の検証状態へ，同じ比較結果をそれぞれ登録する．
                    for state_name in ('integrity_state', self.file_integrity(job, digest_paths[variable])):
                        # Artifact由来でも検証基準workflowが未信頼なら，一致の真偽にかかわらずunverifiableにする．
                        self.ts.update(
                            state_name,
                            AND(effect, origin, '!(' + trust + ')'),
                            'integrity_unverifiable'
                        )
                        # 信頼されたworkflowの下でdigest比較が一致した場合だけ，対象fileをverifiedへ更新する．
                        self.ts.update(
                            state_name,
                            AND(effect, origin, trust, predicate),
                            'integrity_verified'
                        )
                        # 信頼されたworkflowの下でdigest比較が不一致なら，対象fileをmismatchへ更新する．
                        self.ts.update(
                            state_name,
                            AND(effect, origin, trust, '!(' + predicate + ')'),
                            'integrity_mismatch'
                        )
                    path = digest_paths[variable]
                    # 同一step中の次状態更新をすぐ読むことはできないため，検証結果を条件式としてlocalにも重ねる．
                    local_integrity[path] = f"case {AND( effect, origin, '!(' + trust + ')' )} : integrity_unverifiable; {AND(effect, origin, trust, predicate)} : integrity_verified; {AND(effect, origin, trust, '!(' + predicate + ')')} : integrity_mismatch; TRUE : {integrity(path)}; esac"
            elif kind == 'else':
                # 入れ子分岐の最上段を取り出し，現在のif/caseに対応する親条件を参照する．
                frame = stack[-1]
                # else側では親の到達条件を保ち，比較が偽になる側だけを有効にする．
                active = AND(frame['parent'], '!(' + frame['predicate'] + ')')
            elif kind == 'fi':
                # fiで現在のifをstackから外し，分岐前の親の到達条件へ戻す．
                active = stack.pop()['parent']
            elif kind == 'case':
                # caseの親条件・対象変数・由来・taintをstackへ積み，拒否枝とesacの復帰条件に使う．
                stack.append(dict(
                    kind='case',
                    parent=active,
                    variable=op.reads[0],
                    origin=env(op.reads[0])[1],
                    taint=taint(op.reads[0])
                ))
            elif kind == 'case-pattern':
                # 入れ子分岐の最上段を取り出し，現在のif/caseに対応する親条件を参照する．
                frame = stack[-1]
                values, _ = env(frame['variable'])
                # case-patternの文字列を読み，省略された場合は空文字として扱う．
                pattern = op.value or ''
                # 登録済みの不正文字検出patternから，許可する文字クラスの中身だけを取り出す．
                charmatch = re.search(r'\*\[!([^]]+)\]\*', pattern)
                # 契約外のcase patternは診断し，この分岐を推測で実行可能にしない．
                if not charmatch:
                    self.diagnostic(
                        'shell-case-pattern',
                        'only rejecting empty/invalid-character metadata patterns are modeled',
                        step.source
                    )
                    active = 'FALSE'
                else:
                    # 値が空か，許可文字以外を含む候補のguardを集めて，拒否分岐の成立条件にする．
                    invalid = OR((g for v, g in values if not v or re.search('[^' + charmatch[1] + ']', str(v))))
                    # 親経路のうち，不正なmetadata値を持つ経路だけをcaseの拒否枝へ進める．
                    active = AND(frame['parent'], invalid)
            elif kind == 'esac':
                frame = stack.pop()
                # esac後では親経路へ戻し，すでに失敗した経路は継続させない．
                active = AND(frame['parent'], '!(' + OR(failures) + ')')
            elif kind == 'exit':
                if op.value == '1':
                    # exit 1に到達した現在の分岐条件を，step失敗経路として記録する．
                    failures.append(active)
                # exit後の現在枝は無効にし，後続操作に状態更新を追加しないようにする．
                active = 'FALSE'
            # 存在条件を確認してから読む．
            # read・digestの検査用read・不一致後の利用は別の履歴へ記録する．
            elif kind in {'read-trim', 'digest', 'test-file'}:
                path = self.shell_path(
                    job,
                    step,
                    op.reads[0],
                    op.value if kind == 'test-file' else op.path or ''
                )
                # 同じfileの有限内容とArtifact由来フラグを取得し，値と依存を対応付けたまま扱う．
                content, origin = self.content(job, path)
                state = self.file(job, path)
                # 内容が空文字かではなく，fileの存在状態がabsentでないことを読取り条件にする．
                exists = state + ' != absent'
                # 不在fileを読む経路を失敗条件へ追加し，後の操作へ進めないようにする．
                failures.append(AND(active, '!(' + exists + ')'))
                # fileが存在する経路だけを残し，後続のread/copy効果へ引き継ぐ．
                active = AND(active, exists)
                # file存在で絞ったactiveを使って実行guardを作り直し，不在経路でreadの履歴を立てない．
                effect = AND(guard, active)
                # 存在するArtifact由来fileをこの操作で参照した履歴を記録する．
                self.ts.latch('artifact_read', AND(effect, origin))
                if kind == 'test-file':
                    # test -fでArtifact由来の存在確認をした条件を，模擬sinkの前提として残す．
                    tested = OR([tested, origin])
                    continue
                if kind == 'read-trim':
                    # 各内容候補にtrの文字削除を適用し，元のcontent VARがその候補を選ぶ条件と対応付ける．
                    values = [(self.trim(v, op.value), f'{content} = {atom(v)}') for v in self.values]
                    # Artifact由来内容を通常の値として利用した履歴を残し，検証用digest readとは区別する．
                    self.ts.latch('artifact_dependent_use', AND(effect, origin))
                    # taintedなfileを利用した履歴も別に残し，Artifact由来が必ずtaintedだとは見なさない．
                    self.ts.latch('dependent_use', AND(effect, state + ' = tainted'))
                    # 同じfileがverifiedになった後の利用を記録し，検証前の利用履歴とは分ける．
                    self.ts.latch(
                        'post_verification_use',
                        AND(effect, origin, '(' + integrity(path) + ') = integrity_verified')
                    )
                    # 同じfileにmismatchの結果があるのに利用へ進んだ経路を記録する．
                    self.ts.latch(
                        'post_integrity_mismatch_use',
                        AND(effect, origin, '(' + integrity(path) + ') = integrity_mismatch')
                    )
                else:
                    # 有限内容候補ごとに実際のSHA-256文字列を計算し，元内容の条件と結び付ける．
                    values = [(
                        hashlib.sha256(v.encode()).hexdigest(),
                        f'{content} = {atom(v)}'
                    ) for v in self.values]
                    # 有限候補からdigestを計算し，同じfile内容に対応する条件と結び付ける．
                    digest_values[op.writes[0]] = values
                    # digestの代入先へ元fileの由来条件を保存し，比較しただけで新しい由来を作らない．
                    digest_origins[op.writes[0]] = origin
                    # digest計算先と元pathを対応付け，後の比較結果をこのfileへ返せるようにする．
                    digest_paths[op.writes[0]] = path
                    # digest計算用にArtifact内容を読んだ事実を，通常利用とは別の履歴にする．
                    self.ts.latch('verification_read', AND(effect, origin))
                # 読み取った値候補・Artifact由来・taintを同じlocal変数へ代入し，後の検査やoutput参照へ渡す．
                assign(op.writes[0], values, origin, state + ' = tainted')
            elif kind == 'test-length':
                values, origin = env(op.reads[0])
                # 指定した文字列長に一致する値候補だけを選び，長さ検査の成功条件にする．
                valid = OR((g for v, g in values if len(str(v)) == int(op.value)))
                # 検査が不成立の経路を失敗として記録し，成功側だけが継続できるようにする．
                failures.append(AND(active, '!(' + valid + ')'))
                # 直前の検査を通った経路だけを後続操作の到達条件へ残す．
                active = AND(active, valid)
            elif kind == 'grep-assert':
                content, _ = self.content(job, op.path)
                # 改行を除いた内容がgrepの正規表現へ一致する候補を，検査の成功条件にする．
                valid = OR((f'{content} = {atom(v)}' for v in self.values if re.search(op.value, v.strip('\r\n'))))
                # 検査が不成立の経路を失敗として記録し，成功側だけが継続できるようにする．
                failures.append(AND(active, '!(' + valid + ')'))
                # 直前の検査を通った経路だけを後続操作の到達条件へ残す．
                active = AND(active, valid)
            elif kind == 'mkdir':
                # mkdirのIRが保持する複数pathを区切り文字|で分け，各pathの状態を更新する．
                for path in op.path.split('|'):
                    # mkdirが成立した経路では，対象pathの抽象状態をcleanにする．
                    self.ts.update(self.file(job, path), effect, 'clean')
            elif kind == 'copy':
                # コピーの元と先をIRの->表現から分け，同じ操作の状態更新へ使う．
                source, target = op.path.split('->')
                state = self.file(job, source)
                content, origin = self.content(job, source)
                # コピー先の内容と由来のVARを取得し，元fileと同じ条件で更新する準備をする．
                dest, do = self.content(job, target)
                # 内容が空文字かではなく，fileの存在状態がabsentでないことを読取り条件にする．
                exists = state + ' != absent'
                # 不在fileを読む経路を失敗条件へ追加し，後の操作へ進めないようにする．
                failures.append(AND(active, '!(' + exists + ')'))
                # fileが存在する経路だけを残し，後続のread/copy効果へ引き継ぐ．
                active = AND(active, exists)
                # コピー元が存在して処理が継続する経路だけで，先fileの存在・taintを元状態へ合わせる．
                self.ts.update(self.file(job, target), AND(guard, active), state)
                # コピーが成立した経路で，元fileの内容VARを先fileへ写す．
                self.ts.update(dest, AND(guard, active), content)
                # 内容と同じ条件で由来フラグもコピーし，値だけを伝えて依存を失わないようにする．
                self.ts.update(do, AND(guard, active), origin)
                # 完全性確認の状態も元fileから先fileへ伝え，コピーした内容との対応を保つ．
                self.ts.update(
                    self.file_integrity(job, target),
                    AND(guard, active),
                    self.file_integrity(job, source)
                )
            # 内容と同じ条件でoutput/envへ値を渡す．
            # data/control依存を分け，digest判定にはworkflowTrustedの条件も使う．
            elif kind in {'github-output', 'github-env'}:
                if kind == 'github-env':
                    values, origin = env(op.reads[0])
                    # GITHUB_ENVへ値・由来・taint・data/control依存を渡し，後続stepから使う共有envを更新する．
                    self.emit_file_command(
                        'runtime-env',
                        op.writes[0].split(':')[1],
                        values,
                        origin,
                        taint(op.reads[0]),
                        job,
                        step,
                        effect,
                        dependency(op.reads[0], 'data'),
                        dependency(op.reads[0], 'control')
                    )
                    continue
                values, origin = env(op.reads[0])
                # IRのoutput:NAME/env:NAMEからNAMEだけを取り出す．
                key = op.writes[0].split(':')[1]
                token, _ = self.output(job, step.id, key, ['', *[ex.string(v) for v, g in values], 'other'])
                # このstepがdigestを計算して変数同士で比較しているかを調べ，outputの信頼条件へ使う．
                digest_related = bool(digest_values) and any((c.kind == 'if-eq-var' for c in step.program.operations))
                # outputの有限候補ごとに，その候補の成立条件を保持した次状態規則を作る．
                for value, condition in values:
                    # 値候補の条件を保ってoutputへ書き，digest判定関連ならworkflowの信頼も要求する．
                    self.ts.update(
                        token,
                        AND(effect, condition, trust if digest_related else 'TRUE'),
                        atom(ex.string(value))
                    )
                # digestを用いた判定関連outputでは，未信頼workflow側の代表値への更新も追加する．
                if digest_related:
                    # digest判定関連でworkflow未信頼ならotherを書き，検証成功の確定値として扱わない．
                    self.ts.update(token, AND(effect, '!(' + trust + ')'), atom('other'))
                # 変数のcontrol依存，入れ子分岐の由来，step-level ifの由来を合わせる．
                control = OR([
                    dependency(op.reads[0], 'control'),
                    *[f.get('origin', 'FALSE') for f in stack],
                    self.template_label(step.raw.get('if', ''), job, step)
                ])
                # outputに値の由来と制御条件の由来を合わせて伝える．
                self.ts.update(self.output_origin(job, step.id, key), effect, OR([origin, control]))
                # outputのdataラベルへ，書込元local変数のdata依存を伝える．
                self.ts.update(
                    self.value_label('output', job, step.id + ':' + key, component='data'),
                    effect,
                    dependency(op.reads[0], 'data')
                )
                # outputのcontrolラベルへ，この書込みの分岐条件に由来する依存を伝える．
                self.ts.update(
                    self.value_label('output', job, step.id + ':' + key, component='control'),
                    effect,
                    control
                )
                # outputのtaintラベルへ，書込元local変数の汚染条件を伝える．
                self.ts.update(
                    self.value_label('output', job, step.id + ':' + key, True),
                    effect,
                    taint(op.reads[0])
                )
            elif kind in {'echo', 'printf'}:
                text = op.value or ''
                # 以下は研究用markerの契約．
                # 実際のtoken権限やpublish成功を表す処理ではない．
                marker = re.search(
                    r'(dummy_publish_authority_reached|dummy_repository_update_reached)=([^\x27\"]+)',
                    text
                )
                # 研究用markerがない通常ログではsink履歴を立てず，必要なenv参照の追跡だけ行う．
                if not marker:
                    for name in op.reads:
                        raw = step.raw.get('env', {}).get(name, '')
                        if '.outputs.' in str(raw) and 'artifact-digest' not in str(raw):
                            env(name)
                    continue
                # markerの右辺を取り出し，変数参照・literal true・literal falseを区別する．
                raw = marker[2].strip()
                # markerの右辺が変数なら，値が文字列trueとなる条件とArtifact由来を追跡する．
                if raw.startswith('$'):
                    values, origin = env(raw[1:])
                    # marker変数の候補から文字列trueだけを選び，模擬結果が真になる条件を作る．
                    true = OR((g for v, g in values if str(v) == 'true'))
                    dep = origin
                else:
                    true = 'TRUE' if raw == 'true' else 'FALSE'
                    dep = 'FALSE'
                    if marker[1] == 'dummy_repository_update_reached' and raw == 'true':
                        # 固定true markerも，file存在検査と必要metadata output全体への依存がある場合だけ契約化する．
                        # 不足は診断する．
                        required = {(s.id, o.writes[0].split(':')[1]) for s in job.steps if s is not step and s.program for o in s.program.output_writes if any((v.kind == 'read-trim' and v.writes == o.reads for v in s.program.operations))}
                        # 必須metadata outputが空でなく全件参照されている場合だけ，固定true markerの依存契約を適用する．
                        if required and required.issubset(metadata_refs):
                            # file存在確認とすべてのmetadata outputの由来がそろうことを，模擬sinkの依存条件にする．
                            dep = AND(tested, *(self.output_origin(job, s, k) for s, k in sorted(required)))
                        else:
                            self.diagnostic(
                                'uncontracted-simulated-sink',
                                'marker lacks a complete artifact metadata output flow and file-presence test',
                                step.source
                            )
                # literal falseの記録は，模擬権限地点への到達としては数えない．
                if raw == 'false':
                    continue
                # 研究markerの地点へ来た履歴を記録し，実token権限の可用性とは別の変数にする．
                self.ts.latch('simulated_sink_site_reached', effect)
                # markerの値がtrueになる経路だけで模擬結果の履歴を立てる．
                self.ts.latch('simulated_sink_result_true', AND(effect, true))
                # 模擬結果がtrueで，その判定がArtifact由来に依存する経路を記録する．
                self.ts.latch('artifact_dependent_sink', AND(effect, true, dep))
        # 最後まで継続して失敗しなかった条件と，途中で失敗した条件を2要素で返す．
        return (AND(active, '!(' + OR(failures) + ')'), OR(failures))
