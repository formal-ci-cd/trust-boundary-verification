# 明示scenarioがない場合に，宣言イベントと対応済みファイル操作から有限の入力条件を用意する．
# ここで選ぶbranch・repository・内容候補はモデルの仮定であり，実際のGitHub実行の観測値ではない．
"""明示scenarioがない場合の，有限の既定外部条件を組み立てる．

入力はworkflowのPath列，出力はrunごとのevent・scope・file在庫・内容候補等の辞書である．
宣言イベントを読み，限定Shellのcopy/grepとupload/downloadの接続から代表値を作る．
checkout後に静的な単一fileをuploadする場合は，未知の存在と有限内容候補を補い，根拠を記録する．
branchやrepository，metadataの代表値は実際の実行の観測値ではなく，モデル生成用の仮定である．
未対応Shellはこの導出では読み飛ばし，効果の診断は共通compilerが担当する．
compile_workflowsがscenario省略時に呼び，backendの種類は切り替えない．
"""
from pathlib import Path, PurePosixPath
import re
import yaml
from .workflow_loader import WorkflowLoader
from workflow_shell_subset import parse_shell, UnsupportedShell

def normalized(value):
    """既知のworkspace/temp表記をモデル用path表記へそろえる．"""
    # runner.tempとgithub.workspaceの既知表記だけをモデルの固定pathへ置換し，末尾/を除く．
    return str(value).replace('${{ runner.temp }}', '/tmp/runner').replace('${{ github.workspace }}', '/workspace').rstrip('/')

def _infer_upload_inputs(raw, cfg, workflow_path):
    """静的upload元の未供給fileを，存在が未知のcheckout候補として追加する．

    repositoryの実ファイルは開かず，YAMLに現れる単一の相対pathだけを対象にする．
    内容は空文字と代表文字列の有限候補であり，任意のbyte列を網羅する主張ではない．
    """
    assumptions = []
    diagnostics = []
    for name, job in raw['jobs'].items():
        steps = job.get('steps', [])
        # checkoutがないJobへ，ローカルにあるというだけの理由で入力fileを生やさない．
        if not any(str(step.get('uses', '')).startswith('actions/checkout@') for step in steps):
            continue
        produced = set()
        for index, step in enumerate(steps):
            script = step.get('run', '')
            try:
                program = parse_shell(script)
            except UnsupportedShell:
                program = None
            if program:
                # 前のstepで作るcopy先は，checkoutから来たfileとは扱わない．
                produced.update(op.path.split('->')[1] for op in program.operations if op.kind == 'copy')
            # 単純literal書込みも既存Shell契約と同じ引用・単一pathの形だけを認識する．
            for line in script.splitlines():
                match = re.fullmatch(r"(?:echo|printf)\s+(['\"])(.*?)\1\s*>\s*([\w./-]+)", line.strip())
                if match:
                    produced.add(normalized(match[3]))
            if not str(step.get('uses', '')).startswith('actions/upload-artifact@'):
                continue
            value = step.get('with', {}).get('path', '')
            path = normalized(value)
            evidence = dict(file=str(workflow_path), job=str(name), step=index, key='with.path')
            # 既にcopy/grepから内容が供給されるpathや，先行stepが生成するpathは従来の規則を使う．
            if path in cfg['checkoutValues'] or any(p == path or p.startswith(path + '/') for p in produced):
                continue
            # glob・式・複数path・directory指定を，存在する単一fileへ勝手に置換しない．
            if (not isinstance(value, str) or not re.fullmatch(r'[\w./-]+', path)
                    or path in {'', '.', '/'} or value.endswith('/')
                    or PurePosixPath(path).is_absolute() or '..' in PurePosixPath(path).parts):
                diagnostics.append(dict(
                    code='automatic-checkout-path',
                    message='YAML-only checkout inference requires one static relative file path: ' + str(value),
                    evidence=evidence,
                ))
                continue
            cfg['checkoutValues'][path] = ['', 'untrusted\n']
            # 実在は確認していないため，後でFROZENVARのTRUE/FALSEを両方探索する．
            cfg.setdefault('optionalCheckoutFiles', []).append(path)
            assumptions.append(dict(
                kind='checkout-file', run=cfg['id'], path=path, evidence=evidence,
                presence='unknown; both present and absent are modeled',
                pathKind='single regular file; directory layouts are outside this assumption',
                contents=['', 'untrusted\n'],
                contentScope='finite representatives only; not all possible file contents',
                provenance='checkout trust follows the selected event; pull_request is untrusted',
            ))
    cfg['optionalCheckoutFiles'] = sorted(set(cfg.get('optionalCheckoutFiles', [])))
    return assumptions, diagnostics


# 宣言されたイベントと対応済み操作から，生成に必要な有限の代表条件を組み立てる．
def default_scenario(paths):
    """YAMLと閉じた操作から，有限の既定contextを組み立てる．"""
    # すべての入力YAMLを専用loaderで読み，宣言イベントと閉じた操作を調べる．
    workflows = [yaml.load(Path(path).read_text(), Loader=WorkflowLoader) for path in paths]
    # 各入力にjobs辞書があることを確認し，不正な入力から既定条件を作らない．
    if not all((isinstance(raw, dict) and isinstance(raw.get('jobs'), dict) for raw in workflows)):
        raise ValueError('each workflow requires a jobs mapping')
    # workflowごとに生成する有限contextを，入力と同じ順序でためる．
    configs = []
    for index, raw in enumerate(workflows):
        events = raw.get('on', {})
        if isinstance(events, str):
            events = {events: None}
        if isinstance(events, list):
            events = dict.fromkeys(events)
        # 最初の宣言eventを代表として選ぶ既定動作．
        # すべてのeventを列挙した評価ではない．
        event = next(iter(events), 'workflow_dispatch')
        # 選んだ代表イベントのfilter設定を取り出し，null指定は空辞書へそろえる．
        event_config = events.get(event) or {}
        # mainや合成repository等の値は外部観測ではない．
        # 明示scenarioが必要な実事例と区別する．
        cfg = dict(
            id=f'run{index}',
            event=event,
            activity='completed' if event == 'workflow_run' else 'opened' if event in {'pull_request', 'pull_request_target'} else '',
            branch='main',
            scope='refs/pull/1/merge' if event == 'pull_request' else 'refs/heads/main',
            repository='bounded/repository',
            defaultBranch='refs/heads/main',
            baseBranch='refs/heads/main',
            fork=event == 'pull_request',
            sendWriteTokens=False,
            defaultBranchWorkflow=True,
            refType='branch',
            runnerOS='Linux',
            runnerArch='X64',
            changedPaths=[],
            diffComplete=True,
            checkoutFiles=[],
            checkoutValues={}
        )
        # workflow_run.workflowsの名前を照合し，入力の並び順から上流を決めない．
        if event == 'workflow_run':
            names = event_config.get('workflows', []) if isinstance(event_config, dict) else []
            names = [names] if isinstance(names, str) else names
            candidates = [i for i, workflow in enumerate(workflows)
                          if i != index and workflow.get('name') in names]
            if len(candidates) == 1:
                upstream = candidates[0]
                cfg.update(upstreamRun=f'run{upstream}', upstreamName=workflows[upstream]['name'])
            else:
                # 候補なし・複数候補は推測で接続せず，後で未対応診断に残す．
                cfg['automaticUpstreamIssue'] = dict(names=names, candidates=candidates)
        for pattern in event_config.get('paths', []) if isinstance(event_config, dict) else []:
            # 肯定literal pathだけを代表変更一覧へ入れ，globの一致fileを推測して生成しない．
            if not pattern.startswith('!') and (not any((c in pattern for c in '*?['))):
                cfg['changedPaths'].append(pattern)
        for job in raw.get('jobs', {}).values():
            for step in job.get('steps', []):
                if 'run' not in step:
                    continue
                try:
                    # 対応済みShellだけをIRへ読み，checkout在庫を導くcopy/grepの情報を得る．
                    program = parse_shell(step['run'])
                # 既定値の導出では未対応scriptを読み飛ばし，効果の診断は本体compilerへ委ねる．
                except UnsupportedShell:
                    continue
                for op in program.operations:
                    if op.kind == 'copy':
                        path = op.path.split('->')[0]
                        # 明示済みの候補を上書きせず，コピー元等へ代表の未信頼内容を補う．
                        cfg['checkoutValues'].setdefault(path, ['untrusted\n'])
                    elif op.kind == 'grep-assert':
                        match = re.fullmatch(r'\^([^()|]+)\(([^()]+)\)\$', op.value or '')
                        if match:
                            cfg['checkoutValues'][op.path] = [match[1] + v + '\n' for v in match[2].split('|')]
                        else:
                            # 明示済みの候補を上書きせず，コピー元等へ代表の未信頼内容を補う．
                            cfg['checkoutValues'].setdefault(op.path, ['untrusted\n'])
        configs.append(cfg)
    # consumerのmetadata検査を，uploadへcopyされる同じmemberの候補内容と対応付ける．
    for ri, raw in enumerate(workflows):
        for job in raw.get('jobs', {}).values():
            steps = job.get('steps', [])
            # このJobのdownload-artifact stepを集め，consumerのmetadata readとのpath対応を探す．
            downloads = [s for s in steps if str(s.get('uses', '')).startswith('actions/download-artifact@')]
            for step in steps:
                if 'run' not in step:
                    continue
                try:
                    # 対応済みShellだけをIRへ読み，checkout在庫を導くcopy/grepの情報を得る．
                    program = parse_shell(step['run'])
                # 既定値の導出では未対応scriptを読み飛ばし，効果の診断は本体compilerへ委ねる．
                except UnsupportedShell:
                    continue
                # workflow→job→stepの順でenvを展開し，同名変数は後ろの指定を優先する．
                env = {**raw.get('env', {}), **job.get('env', {}), **step.get('env', {})}
                for op in program.artifact_reads:
                    if op.kind != 'read-trim':
                        continue
                    # readの代入先をcaseで検査している操作だけを選び，metadata候補を導く対象にする．
                    cases = [c for c in program.operations if c.kind == 'case' and c.reads == op.writes]
                    if not cases:
                        continue
                    # 同じ変数への長さ検査があればその数を取り出し，なければNoneとする．
                    length = next(
                        (int(c.value) for c in program.operations if c.kind == 'test-length' and c.reads == op.writes),
                        None
                    )
                    # 文字種/長さを試す有限の代表値を作る．
                    # 任意metadata内容を網羅しているわけではない．
                    values = ['a' * length + '\n', 'b' * length + '\n', 'invalid\n'] if length else ['main\n', 'topic\n', 'invalid space\n']
                    # envで与えたrootとreadのsuffixを連結し，consumerが読むfileのpathを作る．
                    full = normalized(env.get(op.reads[0], '')) + (op.path or '')
                    for download in downloads:
                        inp = download.get('with', {})
                        dest = normalized(inp.get('path', '.'))
                        if not full.startswith(dest + '/'):
                            continue
                        # download rootより下の相対部分を取り，producerのupload memberへ対応付ける．
                        suffix = full[len(dest):]
                        for wi, writer in enumerate(workflows):
                            for wjob in writer.get('jobs', {}).values():
                                # download名と一致するupload候補を，保存元workflowのJobから集める．
                                uploads = [s for s in wjob.get('steps', []) if str(s.get( 'uses', '' )).startswith('actions/upload-artifact@') and s.get('with', {}).get('name') == inp.get('name')]
                                for source_step in wjob.get('steps', []):
                                    try:
                                        source = parse_shell(source_step.get('run', ''))
                                    # 既定値の導出では未対応scriptを読み飛ばし，効果の診断は本体compilerへ委ねる．
                                    except UnsupportedShell:
                                        continue
                                    for copy in source.operations:
                                        if copy.kind == 'copy':
                                            # producer内のcopy元とcopy先を分け，uploadされるmemberの元内容を特定する．
                                            src, target = copy.path.split('->')
                                            if any((target == normalized(u['with']['path']) + suffix for u in uploads)):
                                                # consumerで試すmetadata候補を，同じmemberを生成するproducerのコピー元内容へ設定する．
                                                configs[wi]['checkoutValues'][src] = values
    assumptions = []
    diagnostics = []
    for raw, cfg, path in zip(workflows, configs, paths):
        inferred, issues = _infer_upload_inputs(raw, cfg, path)
        assumptions.extend(inferred)
        diagnostics.extend(issues)
        if 'automaticUpstreamIssue' in cfg:
            diagnostics.append(dict(
                code='automatic-workflow-run',
                message='workflow_run requires exactly one supplied workflow matching its name filter',
                evidence=dict(file=str(path), **cfg['automaticUpstreamIssue']),
            ))
        # 内容候補が得られたpath名を安定した順に並べ，有限checkout在庫として使う．
        cfg['checkoutFiles'] = sorted(cfg['checkoutValues'])
    return dict(
        runs=configs,
        automaticAssumptions=assumptions,
        automaticDiagnostics=diagnostics,
        defaultContext='synthetic finite contexts inferred from declared events and closed file operations; not observed GitHub runs'
    )
