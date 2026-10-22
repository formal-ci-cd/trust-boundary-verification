"""Finite default contexts; supplying a scenario changes inputs, not the compiler."""
from pathlib import Path
import re
import yaml
from .workflow_loader import WorkflowLoader
from workflow_shell_subset import parse_shell, UnsupportedShell


def normalized(value):
    return str(value).replace('${{ runner.temp }}', '/tmp/runner').replace('${{ github.workspace }}', '/workspace').rstrip('/')


def default_scenario(paths):
    workflows = [yaml.load(Path(path).read_text(), Loader=WorkflowLoader) for path in paths]
    if not all(isinstance(raw,dict) and isinstance(raw.get('jobs'),dict) for raw in workflows):
        raise ValueError('each workflow requires a jobs mapping')
    configs = []
    for index, raw in enumerate(workflows):
        events = raw.get('on', {})
        if isinstance(events, str):
            events = {events: None}
        if isinstance(events, list):
            events = dict.fromkeys(events)
        event = next(iter(events), 'workflow_dispatch')
        event_config = events.get(event) or {}
        cfg = dict(id=f'run{index}', event=event, activity='completed' if event=='workflow_run' else 'opened' if event in {'pull_request','pull_request_target'} else '',
                   branch='main', scope='refs/pull/1/merge' if event=='pull_request' else 'refs/heads/main',
                   repository='bounded/repository', defaultBranch='refs/heads/main', baseBranch='refs/heads/main',
                   fork=event=='pull_request', sendWriteTokens=False, defaultBranchWorkflow=True,
                   refType='branch', runnerOS='Linux', runnerArch='X64', changedPaths=[], diffComplete=True,
                   checkoutFiles=[], checkoutValues={})
        if event=='workflow_run' and index:
            cfg.update(upstreamRun=f'run{index-1}', upstreamName=workflows[index-1].get('name',f'run{index-1}'))
        for pattern in event_config.get('paths', []) if isinstance(event_config, dict) else []:
            if not pattern.startswith('!') and not any(c in pattern for c in '*?['):
                cfg['changedPaths'].append(pattern)
        for job in raw.get('jobs',{}).values():
            for step in job.get('steps',[]):
                if 'run' not in step:
                    continue
                try:
                    program = parse_shell(step['run'])
                except UnsupportedShell:
                    continue
                for op in program.operations:
                    if op.kind=='copy':
                        path=op.path.split('->')[0]
                        cfg['checkoutValues'].setdefault(path,['untrusted\n'])
                    elif op.kind=='grep-assert':
                        match=re.fullmatch(r'\^([^()|]+)\(([^()]+)\)\$',op.value or '')
                        if match:
                            cfg['checkoutValues'][op.path]=[match[1]+v+'\n' for v in match[2].split('|')]
                        else:
                            cfg['checkoutValues'].setdefault(op.path,['untrusted\n'])
        configs.append(cfg)
    # Correlate validated metadata with the actual copied members of each upload.
    for ri,raw in enumerate(workflows):
        for job in raw.get('jobs',{}).values():
            steps=job.get('steps',[])
            downloads=[s for s in steps if str(s.get('uses','')).startswith('actions/download-artifact@')]
            for step in steps:
                if 'run' not in step:
                    continue
                try:
                    program=parse_shell(step['run'])
                except UnsupportedShell:
                    continue
                env={**raw.get('env',{}),**job.get('env',{}),**step.get('env',{})}
                for op in program.artifact_reads:
                    if op.kind!='read-trim':
                        continue
                    cases=[c for c in program.operations if c.kind=='case' and c.reads==op.writes]
                    if not cases:
                        continue
                    length=next((int(c.value) for c in program.operations if c.kind=='test-length' and c.reads==op.writes),None)
                    values=['a'*length+'\n','b'*length+'\n','invalid\n'] if length else ['main\n','topic\n','invalid space\n']
                    full=normalized(env.get(op.reads[0],''))+(op.path or '')
                    for download in downloads:
                        inp=download.get('with',{});dest=normalized(inp.get('path','.'))
                        if not full.startswith(dest+'/'):
                            continue
                        suffix=full[len(dest):]
                        for wi,writer in enumerate(workflows):
                            for wjob in writer.get('jobs',{}).values():
                                uploads=[s for s in wjob.get('steps',[]) if str(s.get('uses','')).startswith('actions/upload-artifact@') and s.get('with',{}).get('name')==inp.get('name')]
                                for source_step in wjob.get('steps',[]):
                                    try:
                                        source=parse_shell(source_step.get('run',''))
                                    except UnsupportedShell:
                                        continue
                                    for copy in source.operations:
                                        if copy.kind=='copy':
                                            src,target=copy.path.split('->')
                                            if any(target==normalized(u['with']['path'])+suffix for u in uploads):
                                                configs[wi]['checkoutValues'][src]=values
    for cfg in configs:
        cfg['checkoutFiles']=sorted(cfg['checkoutValues'])
    return dict(runs=configs, defaultContext='synthetic finite contexts inferred from declared events and closed file operations; not observed GitHub runs')
