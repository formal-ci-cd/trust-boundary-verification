# 明示scenarioがない場合に，宣言イベントと対応済みファイル操作から有限の入力条件を用意する．
# ここで選ぶbranch・repository・内容候補はモデルの仮定であり，実際のGitHub実行の観測値ではない．
"""明示scenarioがない場合の，有限の既定外部条件を組み立てる．

入力はworkflowのPath列，出力はrunごとのevent・scope・file在庫・内容候補等の辞書である．
宣言イベントを読み，限定Shellのcopy/grepとupload/downloadの接続から代表値を作る．
branchやrepository，metadataの代表値は実際の実行の観測値ではなく，モデル生成用の仮定である．
未対応Shellはこの導出では読み飛ばし，効果の診断は共通compilerが担当する．
compile_workflowsがscenario省略時に呼び，backendの種類は切り替えない．
"""
from pathlib import Path
import re
import yaml
from .workflow_loader import WorkflowLoader
from workflow_shell_subset import parse_shell, UnsupportedShell

def normalized(value):
    """既知のworkspace/temp表記をモデル用path表記へそろえる．"""
    # runner.tempとgithub.workspaceの既知表記だけをモデルの固定pathへ置換し，末尾/を除く．
    return str(value).replace('${{ runner.temp }}', '/tmp/runner').replace('${{ github.workspace }}', '/workspace').rstrip('/')

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
        # 先行入力があるworkflow_runには，直前のworkflowを代表上流として結ぶ．
        if event == 'workflow_run' and index:
            # 上流run idとworkflow名をcontextへ補い，schedulerと名前filterが参照できるようにする．
            cfg.update(
                upstreamRun=f'run{index - 1}',
                upstreamName=workflows[index - 1].get('name', f'run{index - 1}')
            )
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
    for cfg in configs:
        # 内容候補が得られたpath名を安定した順に並べ，有限checkout在庫として使う．
        cfg['checkoutFiles'] = sorted(cfg['checkoutValues'])
    return dict(
        runs=configs,
        defaultContext='synthetic finite contexts inferred from declared events and closed file operations; not observed GitHub runs'
    )
