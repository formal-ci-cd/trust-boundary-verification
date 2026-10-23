# YAML由来の条件候補と利用者の制限を，共有単位・根拠・網羅性付きのIRへまとめる．
"""外部条件の有限候補を作り，同じWorkflowCompilerへ渡すための共通条件IR．

入力はworkflowのYAML列と任意の部分条件，出力は合法なrun contextの候補と条件の対応表である．
Pythonは各条件で成立する既存契約のguardを生成し，実行条件の選択自体はSMVのFROZENVARへ残す．
ShellやActionの新しい意味はこのmoduleへ実装しない．不完全な文字列・内容候補はcoverageへ記録する．
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import copy
import hashlib
import itertools
import json
import math
import re
from pathlib import Path

import yaml
from . import expressions as ex
from . import policies
from .scenario import default_scenario
from .transition import symbol, atom, conjunction as AND, disjunction as OR
from .workflow_loader import WorkflowLoader


@dataclass
class ExternalCondition:
    """値候補の意味と共有単位を，SMV記号から独立して保持する．"""
    name: str
    owner: str
    value_type: str
    domain: list
    origin: str
    evidence: object
    coverage: str = 'modeled-finite-domain'
    constraints: list = field(default_factory=list)
    symbol: str | None = None
    encodings: dict = field(default_factory=dict)

    def json(self):
        """manifestへ保存する辞書を返し，元のIRは変更しない．"""
        return asdict(self)


@dataclass
class ConditionSpace:
    """runごとの合法な候補列と，repository共有の制限を保持する．"""
    base: dict
    profiles: list
    conditions: list
    coverage: list
    restrictions: list
    workflows: list

    def count(self):
        """全runの候補を組み合わせる個数を，列挙前に計算する．"""
        return math.prod(len(rows) for rows in self.profiles)


# legacy contextを一式与える既存実験は，従来の値をそのまま使用する．
REQUIRED_CONTEXT = {'event', 'activity', 'branch', 'scope', 'repository', 'defaultBranch',
                    'baseBranch', 'fork', 'defaultBranchWorkflow', 'refType', 'runnerOS', 'runnerArch'}
FIELD_ALIASES = {'head-branch': 'headBranch', 'base-branch': 'baseBranch',
                 'default-branch': 'defaultBranch', 'runner-os': 'runnerOS',
                 'runner-arch': 'runnerArch', 'repository-visibility':'repositoryVisibility', 'send-write-tokens': 'sendWriteTokens', 'workflow-present': 'defaultBranchWorkflow'}
RESTRICTABLE = {'event', 'activity', 'fork', 'headBranch', 'baseBranch', 'defaultBranch',
                'repository', 'repositoryVisibility', 'runnerOS', 'runnerArch', 'sendWriteTokens', 'defaultBranchWorkflow',
                'branch', 'refType', 'scope', 'defaultPermissions', 'tokenAuthenticated',
                'externalPackageCredential', 'checkoutUntrusted', 'workflowTrusted', 'inputs', 'vars',
                'cacheVersion','cacheMode','checkoutFiles','checkoutValues','optionalCheckoutFiles',
                'repositorySnapshot','fileInventoryComplete','domains'}


def is_complete_scenario(scenario, count):
    """旧形式の完全なscenarioかを判定し，部分指定と混同しない．"""
    return (isinstance(scenario, dict) and scenario.get('mode') != 'automatic'
            and 'conditions' not in scenario and len(scenario.get('runs', [])) == count
            and all(REQUIRED_CONTEXT <= set(run) for run in scenario['runs']))


def events(raw):
    """onの文字列・配列・mappingを，順序付きのevent辞書へそろえる．"""
    value = raw.get('on', {})
    if isinstance(value, str):
        return {value: None}
    if isinstance(value, list):
        return dict.fromkeys(value)
    if not isinstance(value, dict) or not value:
        raise ValueError('automatic analysis requires at least one declared event')
    return value


def runner_labels(value, matrix=None):
    """既知のhosted runner名またはself-hosted labelからOS・architectureを得る．"""
    if isinstance(value, str) and matrix:
        match = re.fullmatch(r'\$\{\{\s*matrix\.([\w-]+)\s*}}', value)
        if match:
            value = matrix.get(match[1], value)
    labels = value if isinstance(value, list) else [value]
    os_values, arch_values = set(), set()
    for item in labels:
        if not isinstance(item, str) or '${{' in item:
            continue
        label = item.lower()
        if label.startswith('ubuntu-') or label == 'linux':
            os_values.add('Linux')
        elif label.startswith('windows-') or label == 'windows':
            os_values.add('Windows')
        elif label.startswith('macos-') or label == 'macos':
            os_values.add('macOS')
        if label in {'x64', 'arm64'}:
            arch_values.add(label.upper())
        elif label.startswith(('ubuntu-', 'windows-')):
            arch_values.add('ARM64' if 'arm' in label else 'X64')
        elif label.startswith('macos-') and ('intel' in label or label.endswith('-large') or '13' in label):
            arch_values.add('X64')
        elif label in {'macos-latest','macos-14','macos-15','macos-26'} or label.endswith('-xlarge'):
            # hosted runnerの既知labelだけを確定し，独自labelを同じarchitectureと推測しない．
            arch_values.add('ARM64')
    return (next(iter(os_values)) if len(os_values) == 1 else None,
            next(iter(arch_values)) if len(arch_values) == 1 else None)


def _target(selector, workflows, ids):
    """run IDか一意なworkflow名を解決し，曖昧な指定は拒否する．"""
    if selector in ids:
        return ids.index(selector)
    found = [i for i, raw in enumerate(workflows) if raw.get('name') == selector]
    if len(found) != 1:
        raise ValueError('run selector must be an ID or unique workflow name: ' + str(selector))
    return found[0]


def _restrictions(partial, cli, workflows, ids):
    """JSONとCLIを同じ制限へ正規化し，競合する値は黙って上書きしない．"""
    result = [{} for _ in workflows]
    recorded = []
    def put(index, key, value, source):
        key = FIELD_ALIASES.get(key, key)
        if key not in RESTRICTABLE:
            raise ValueError('unsupported external condition: ' + key)
        if key in {'fork', 'sendWriteTokens', 'defaultBranchWorkflow', 'checkoutUntrusted', 'workflowTrusted', 'externalPackageCredential'} and type(value) is not bool:
            raise ValueError(key + ' requires a JSON Boolean')
        if key in {'event','activity','headBranch','baseBranch','defaultBranch','repository','runnerOS','runnerArch','branch','scope','refType','cacheVersion','cacheMode'} and not isinstance(value,str):
            raise ValueError(key + ' requires a string')
        if key=='defaultPermissions' and (not isinstance(value,dict) or any(level not in {'none','read','write'} for level in value.values())):
            raise ValueError('defaultPermissions requires a mapping of none/read/write values')
        if key=='repositoryVisibility' and (not isinstance(value,str) or value not in {'public','private','internal'}):
            raise ValueError('repositoryVisibility must be public/private/internal')
        if key in result[index] and result[index][key] != value:
            raise ValueError(f'contradictory external condition for {ids[index]}: {key}')
        result[index][key] = value
        recorded.append(dict(owner=ids[index], name=key, value=value, source=source))
    for index, values in enumerate((partial or {}).get('runs', [])):
        if index >= len(workflows):
            raise ValueError('too many run restrictions')
        if 'id' in values:
            index = _target(values['id'],workflows,ids)
        for key, value in values.items():
            if key != 'id':
                put(index, key, value, 'scenario.runs')
    for entry in (partial or {}).get('conditions', []) + (cli or []):
        selector = entry.get('run')
        if selector is None:
            if len(workflows) != 1:
                raise ValueError('multiple workflows require --run or a JSON run selector')
            index = 0
        else:
            index = _target(selector, workflows, ids)
        for key, value in entry.items():
            if key != 'run':
                put(index, key, value, 'condition restriction')
    return result, recorded


def _branch_candidates(workflows):
    """filterと対応式のliteralを集め，無限文字列の代表domainであることも返す．"""
    names = {'main'}
    relevant = False
    glob_used = False
    for raw in workflows:
        for event, config in events(raw).items():
            if not isinstance(config, dict):
                continue
            for field in ('branches', 'branches-ignore', 'tags', 'tags-ignore'):
                for pattern in config.get(field, []):
                    relevant = True
                    pattern = pattern.lstrip('!')
                    if any(c in pattern for c in '*?['):
                        glob_used = True
                        # これはpattern一致の代表例であり，glob全体の網羅性を主張しない．
                        pattern = pattern.replace('**', 'topic').replace('*', 'topic').replace('?', 'x')
                    if pattern and not any(c in pattern for c in '[]+\\'):
                        names.add(pattern)
        for job in raw['jobs'].values():
            for value in [job.get('if', ''), *[step.get('if', '') for step in job.get('steps', [])]]:
                if not isinstance(value, str) or not any(key in value for key in ('branch', 'ref', 'head_ref', 'base_ref')):
                    continue
                relevant = True
                for literal in re.findall(r"'([^']+)'", value):
                    if literal not in {'pull_request', 'push', 'success', 'failure'}:
                        names.add(literal.removeprefix('refs/heads/').removeprefix('refs/tags/'))
    if relevant:
        other = 'other-branch'
        while other in names:
            other += '-x'
        names.add(other)
    return sorted(names), relevant, glob_used


def build_space(paths, partial=None, restrictions=None):
    """YAMLと部分条件から，event・fork・ref等が整合する有限profileを作る．"""
    workflows = [yaml.load(Path(path).read_text(), Loader=WorkflowLoader) for path in paths]
    base = default_scenario(paths)
    ids = [run['id'] for run in base['runs']]
    selected, explicit = _restrictions(partial, restrictions, workflows, ids)
    # default branchもrepository設定なので，片方のrunだけ別の既定branchを選ばないよう制限を共有する．
    repositories = [fixed.get('repository',run['repository']) for fixed,run in zip(selected,base['runs'])]
    defaults = {}
    for repo,fixed in zip(repositories,selected):
        if 'defaultBranch' in fixed:
            value=fixed['defaultBranch'].removeprefix('refs/heads/')
            if repo in defaults and defaults[repo]!=value:
                raise ValueError('contradictory repository defaultBranch: '+repo)
            defaults[repo]=value
    for repo,fixed in zip(repositories,selected):
        if repo in defaults:
            fixed['defaultBranch']=defaults[repo]
    branches, branch_relevant, glob_used = _branch_candidates(workflows)
    coverage = []
    if branch_relevant:
        coverage.append(dict(code='external-branch-domain', reason='finite branch/tag representatives; arbitrary strings are not exhaustively abstracted'))
    if glob_used:
        coverage.append(dict(code='external-filter-domain', reason='glob witnesses do not prove a complete partition of branch/path strings'))
    if any(run.get('checkoutValues') for run in base['runs']):
        coverage.append(dict(code='external-file-content-domain', reason='YAML-derived file contents are finite representatives, not all possible bytes'))
    # YAMLだけでは実repository identityを観測できないため，同一repositoryという有界な役割を記録する．
    coverage.append(dict(code='repository-identity-assumption', reason='supplied workflows share one repository role unless explicitly separated'))
    profiles = []
    conditions = []
    for index, (raw, original, fixed) in enumerate(zip(workflows, base['runs'], selected)):
        rows = []
        declared = events(raw)
        if fixed.get('event') is not None and fixed['event'] not in declared:
            raise ValueError('restricted event is not declared: ' + str(fixed['event']))
        default_branch = str(fixed.get('defaultBranch', 'main')).removeprefix('refs/heads/')
        repository = fixed.get('repository', original['repository'])
        # 同じrun内の既知runnerはJobごとに解決する．未知labelの場合のみrunのOS候補を作る．
        inferred = [runner_labels(job.get('runs-on')) for job in raw['jobs'].values()]
        known_os = {os for os, _ in inferred if os}
        os_domain = [fixed['runnerOS']] if 'runnerOS' in fixed else (sorted(known_os) if known_os else ['Linux', 'Windows', 'macOS'])
        if 'runnerOS' in fixed and len(known_os)>1:
            raise ValueError('run-level runner OS restriction is ambiguous for heterogeneous jobs')
        if 'runnerOS' in fixed and known_os and fixed['runnerOS'] not in known_os:
            raise ValueError('runner OS restriction contradicts runs-on')
        known_arch = {arch for _, arch in inferred if arch}
        if 'runnerArch' in fixed and known_arch and (len(known_arch)>1 or fixed['runnerArch'] not in known_arch):
            raise ValueError('runner architecture restriction contradicts or is ambiguous for runs-on')
        arch_domain = [fixed['runnerArch']] if 'runnerArch' in fixed else (sorted(known_arch) if known_arch else ['X64','ARM64'])
        if not known_os or not known_arch:
            coverage.append(dict(code='external-runner-domain', reason='unresolved runner labels use finite OS/architecture candidates', run=ids[index]))
        for event, config in declared.items():
            if 'event' in fixed and fixed['event'] != event:
                continue
            config = config if isinstance(config, dict) else {}
            if event in {'pull_request', 'pull_request_target'}:
                activities = config.get('types', ['opened', 'synchronize', 'reopened'])
                forks = [False, True]
            elif event == 'workflow_run':
                activities = config.get('types', ['requested', 'in_progress', 'completed'])
                forks = [False]
            else:
                activities = config.get('types', [''])
                forks = [False]
            activities = [a for a in activities if 'activity' not in fixed or a == fixed['activity']]
            forks = [f for f in forks if 'fork' not in fixed or f == fixed['fork']]
            names = config.get('workflows', [])
            names = [names] if isinstance(names, str) else names
            upstreams = [i for i, workflow in enumerate(workflows) if i != index and workflow.get('name') in names and repositories[i]==repository] if event == 'workflow_run' else [None]
            if not upstreams:
                upstreams = [None]
                coverage.append(dict(code='external-workflow-run', reason='no supplied upstream matches the workflow name', run=ids[index]))
            base_names = [str(fixed['baseBranch']).removeprefix('refs/heads/')] if 'baseBranch' in fixed else (branches if event in {'pull_request', 'pull_request_target'} and branch_relevant else [default_branch])
            heads = [fixed['headBranch']] if 'headBranch' in fixed else (branches if branch_relevant else ['topic' if event in {'pull_request', 'pull_request_target'} else default_branch])
            if event == 'workflow_run':
                heads = [default_branch]
            if event not in {'pull_request', 'pull_request_target'} and 'baseBranch' in fixed and base_names != [default_branch]:
                continue
            present = [fixed['defaultBranchWorkflow']] if 'defaultBranchWorkflow' in fixed else ([False, True] if index and event in {'workflow_run','workflow_dispatch','repository_dispatch','schedule'} else [True])
            ref_types = ['branch']
            if event == 'push':
                ref_types.append('tag')
            if 'refType' in fixed:
                ref_types = [value for value in ref_types if value == fixed['refType']]
            path_options = [original.get('changedPaths',[])]
            if any(key in config for key in ('paths','paths-ignore')):
                path_options = [[],['other-file.txt']]
                for pattern in config.get('paths',[])+config.get('paths-ignore',[]):
                    pattern = pattern.lstrip('!')
                    witness = pattern.replace('**','folder').replace('*','file').replace('?','x')
                    if not any(char in witness for char in '[]+\\'):
                        path_options.append([witness])
                coverage.append(dict(code='external-changed-path-domain',reason='finite changed-file witnesses, not a complete filesystem/change-set abstraction',run=ids[index]))
            for activity, fork, head, base_name, os_name, arch_name, exists, upstream, ref_type, changed in itertools.product(activities, forks, heads, base_names, os_domain, arch_domain, present, upstreams, ref_types,path_options):
                run = copy.deepcopy(original)
                is_pr = event in {'pull_request', 'pull_request_target'}
                scope = ('refs/pull/1/merge' if event == 'pull_request' else 'refs/heads/' + base_name if event == 'pull_request_target' else ('refs/tags/' if ref_type == 'tag' else 'refs/heads/') + head)
                run.update(event=event, activity=activity, fork=fork, repository=repository,
                           branch=base_name if is_pr else head, baseBranch='refs/heads/'+base_name,
                           headBranch=head, baseBranchName=base_name, defaultBranch='refs/heads/'+default_branch,
                           scope=scope, refType=ref_type, refName='1/merge' if event=='pull_request' else base_name if event=='pull_request_target' else head,
                           headRepository=repository+'-fork' if fork else repository,
                           defaultBranchWorkflow=exists, runnerOS=os_name,
                           runnerArch=arch_name, sendWriteTokens=None,
                           externalProfile=True,changedPaths=changed,diffComplete=True)
                if event=='workflow_run' and upstream is None:
                    run.pop('upstreamRun',None)
                    run['upstreamName']=''
                if upstream is not None:
                    run.update(upstreamRun=ids[upstream],upstreamName=workflows[upstream]['name'])
                    run.pop('automaticUpstreamIssue',None)
                # repository設定や認証等の制限は候補選択でなく，そのまま既存契約の共有条件へ渡す．
                for key in ('repositoryVisibility','sendWriteTokens','defaultPermissions','tokenAuthenticated','externalPackageCredential','checkoutUntrusted','workflowTrusted','inputs','vars','cacheVersion','cacheMode','checkoutFiles','checkoutValues','optionalCheckoutFiles','repositorySnapshot','fileInventoryComplete','domains'):
                    if key in fixed:
                        run[key] = copy.deepcopy(fixed[key])
                if 'branch' in fixed and run['branch'] != fixed['branch']:
                    continue
                if 'scope' in fixed and scope != fixed['scope']:
                    continue
                if index == 0:
                    try:
                        if not policies.trigger(declared,run):
                            continue
                    except (policies.UnsupportedPolicy, KeyError) as exc:
                        coverage.append(dict(code='external-root-trigger',reason=str(exc),run=ids[index]))
                    run['assumeStarted'] = True
                rows.append(run)
        # 空集合をモデル化すると不達が空虚に成立するため，入力矛盾として停止する．
        if not rows:
            raise ValueError('contradictory external conditions: no legal context for ' + ids[index])
        unique = {json.dumps(row,sort_keys=True):row for row in rows}
        rows = list(unique.values())
        profiles.append(rows)
        token = symbol('external_run', ids[index])
        fields = ('event','activity','fork','headBranch','baseBranchName','defaultBranch','repository','scope','refType','headRepository','defaultBranchWorkflow','runnerOS','runnerArch','upstreamRun','changedPaths')
        for name in fields:
            values = list({json.dumps(row.get(name),sort_keys=True):row.get(name) for row in rows}.values())
            encodings = {'profile_'+str(index)+'_'+str(i):row.get(name) for i,row in enumerate(rows)}
            origin = 'explicit' if name in fixed or name=='baseBranchName' and 'baseBranch' in fixed else 'yaml-domain' if name in {'event','activity'} else 'finite-abstraction' if name in {'headBranch','baseBranchName','defaultBranch','repository','headRepository','scope'} else 'external-domain'
            conditions.append(ExternalCondition(name,ids[index],'boolean' if all(type(v) is bool for v in values) else 'array' if any(isinstance(v,list) for v in values) else 'string',values,origin=origin,evidence=dict(file=str(paths[index])),
                                               constraints=['values in one run profile are correlated'],symbol=token,encodings=encodings))
    # 同一repositoryの明示設定を全runへそろえ，異なる明示値は拒否する．
    shared = {}
    for rows in profiles:
        row = rows[0]
        repo = row['repository']
        for key in ('defaultPermissions','sendWriteTokens','repositoryVisibility'):
            value = row.get(key)
            if value is None:
                continue
            if isinstance(value,dict):
                for scope,level in value.items():
                    identity=(repo,key,scope)
                    if identity in shared and shared[identity]!=level:
                        raise ValueError('contradictory repository setting: '+str(identity))
                    shared[identity]=level
            else:
                identity=(repo,key,'')
                if identity in shared and shared[identity]!=value:
                    raise ValueError('contradictory repository setting: '+str(identity))
                shared[identity]=value
    for rows in profiles:
        for row in rows:
            for (repo,key,scope),value in shared.items():
                if row['repository']==repo:
                    if scope:row.setdefault(key,{})[scope]=value
                    else:row[key]=value
    allowed_top={'runs','conditions','mode','bounds','cachePolicy','cacheIO','operationOutcome','cancelRequests','stepOutcomes','compositeContracts'}
    for key in (partial or {}):
        if key not in allowed_top:
            raise ValueError('unsupported partial scenario field: '+key)
    for key,value in (partial or {}).items():
        if key not in {'runs','conditions','mode'}:
            base[key]=copy.deepcopy(value)
    descriptions = [dict(raw, file=str(path)) for raw,path in zip(workflows,paths)]
    return ConditionSpace(base,profiles,conditions,coverage,explicit,descriptions)


def _frozen_conditions(space, system):
    """既存backendが宣言した権限・認証等も，同じ意味付き条件表へ登録する．"""
    known = {item.symbol for item in space.conditions}
    result = []
    for index, rows in enumerate(space.profiles):
        context = rows[0]
        run, repo = context['id'], context['repository']
        candidates = [
            (symbol('fork_write_setting',repo),'sendWriteTokens',repo,'boolean'),
            (symbol('workflow_trust',run),'workflowTrusted',run,'boolean'),
            (symbol('credential',str(index)+':packages'),'externalPackageCredential',run,'boolean'),
        ]
        for scope in ('contents','actions','packages','deployments','id-token'):
            candidates.extend([
                (symbol('permission_default',repo+':'+scope),'defaultPermissions.'+scope,repo,'permission'),
                (symbol('authentication',str(index)+':'+scope),'tokenAuthenticated.'+scope,run,'boolean'),
            ])
        for path in context.get('optionalCheckoutFiles',[]):
            candidates.append((symbol('input_present',run+':'+path),'filePresent.'+path,run,'boolean'))
        for token,name,owner,kind in candidates:
            if token not in system.frozen or token in known:
                continue
            values = system.frozen[token]
            decoded = {value:(value=='TRUE' if kind=='boolean' else value.removeprefix('perm_')) for value in values}
            result.append(ExternalCondition(name,owner,kind,list(decoded.values()),'external-domain',
                                           'existing WorkflowCompiler contract',symbol=token,encodings=decoded))
            known.add(token)
    return result


def _explicit_settings(space):
    """固定された権限・認証等もIRへ残し，FROZENVARがない値を反例の観測値と区別する．"""
    result = {}
    for rows in space.profiles:
        context = rows[0]
        for key in ('sendWriteTokens','defaultPermissions','tokenAuthenticated','externalPackageCredential','checkoutUntrusted','workflowTrusted'):
            value = context.get(key)
            if value is None:
                continue
            owner = context['repository'] if key in {'sendWriteTokens','defaultPermissions'} else context['id']
            # scope別mappingは，contents/actions等を別々の意味付き条件として記録する．
            entries = value.items() if isinstance(value,dict) else [('',value)]
            for scope,item in entries:
                name = key+'.'+scope if scope else key
                result[(owner,name)] = ExternalCondition(name,owner,'boolean' if type(item) is bool else 'string',
                                                         [item],'explicit','supplied scenario/CLI restriction',coverage='explicit-fixed')
    return list(result.values())


def compile_space(space, compiler_type):
    """同じcompilerの条件別遷移IRを，排他的guard付きの単一状態系へ接続する．

    条件ごとのSMVを別々に実行する方式ではなく，1つのfile/object/PC状態系を共有する．
    Pythonで判定済みの規則も条件付きに戻すことで，triggerだけが固定のまま残ることを防ぐ．
    """
    from .transition import TransitionSystem
    limit = space.base.get('bounds',{}).get('conditionConfigurations',256)
    if type(limit) is not int or limit <= 0:
        raise ValueError('conditionConfigurations must be a positive integer')
    if space.count() > limit:
        # 一部だけを生成すると，落とした条件の危険経路を見落とすため，切捨てず明示的に停止する．
        raise ValueError(f'external-condition product {space.count()} exceeds conditionConfigurations={limit}; restrict conditions or increase the explicit bound')
    combinations = list(itertools.product(*[list(enumerate(rows)) for rows in space.profiles]))
    systems = []
    manifests = []
    predicates = []
    for combination in combinations:
        scenario = copy.deepcopy(space.base)
        scenario['runs'] = [copy.deepcopy(row) for _,row in combination]
        # 自動で選べる上流が複数あっても，今回のprofileが一意に接続した関係は未対応扱いにしない．
        scenario['automaticDiagnostics'] = [issue for issue in scenario.get('automaticDiagnostics',[])
                                            if issue['code']!='automatic-workflow-run']
        backend = compiler_type([Path(raw['file']) for raw in space.workflows],scenario)
        _,manifest = backend.build()
        systems.append(backend.ts)
        manifests.append(manifest)
        predicates.append(AND(*(symbol('external_run',row['id'])+' = profile_'+str(index)+'_'+str(number)
                                for index,(number,row) in enumerate(combination))))
    merged = TransitionSystem()
    # 型domainは和集合を取り，同じ意味のstate slotを条件ごとに複製しない．
    for system in systems:
        for token,domain in system.frozen.items():
            merged.frozen[token] = list(dict.fromkeys(merged.frozen.get(token,[])+domain))
        for token,domain in system.inputs.items():
            merged.inputs[token] = list(dict.fromkeys(merged.inputs.get(token,[])+domain))
        for name,(domain,initial) in system.variables.items():
            if name not in merged.variables:
                merged.var(name,domain,initial,system.sources.get(name))
            else:
                old,old_initial = merged.variables[name]
                if old_initial != initial:
                    raise ValueError('conditional initial values require an explicit initial-state contract: '+name)
                merged.variables[name] = (list(dict.fromkeys(old+domain)),initial)
        merged.sources.update(system.sources)
    # profileを選ぶFROZENVARがevent/activity/fork/refの関係を一緒に保持する．
    for index,rows in enumerate(space.profiles):
        merged.frozen[symbol('external_run',rows[0]['id'])] = ['profile_'+str(index)+'_'+str(i) for i in range(len(rows))]
    for name in merged.variables:
        sequences = {}
        for index,system in enumerate(systems):
            rules = tuple(system.rules.get(name,[]))
            sequences.setdefault(rules,[]).append(index)
        for rules,indices in sequences.items():
            # 規則列全体が同じ場合だけまとめ，caseの優先順を条件間で入れ替えない．
            domain_guard = 'TRUE' if len(indices)==len(systems) else OR(predicates[i] for i in indices)
            for guard,value in rules:
                merged.update(name,guard if domain_guard=='TRUE' else AND(domain_guard,guard),value)
    definitions = set().union(*(system.defines for system in systems))
    for name in sorted(definitions):
        values = {}
        for index,system in enumerate(systems):
            if name not in system.defines:
                raise ValueError('conditional DEFINE has no contract in some contexts: '+name)
            values.setdefault(system.defines[name],[]).append(index)
        if len(values)==1:
            merged.defines[name] = next(iter(values))
        else:
            merged.defines[name] = 'case '+' '.join(OR(predicates[i] for i in indices)+' : '+value+';' for value,indices in values.items())+' esac'
    constraints = set().union(*(set(system.constraints) for system in systems))
    for constraint in sorted(constraints):
        indices = [i for i,system in enumerate(systems) if constraint in system.constraints]
        merged.constraints.append(constraint if len(indices)==len(systems) else OR(predicates[i] for i in indices)+' -> ('+constraint+')')
    # 元のpropertyをそのまま使い，別の安全性命題へ置き換えない．
    for system in systems:
        for name,expression in system.properties.items():
            if name in merged.properties and merged.properties[name]!=expression:
                raise ValueError('property meaning differs between external contexts: '+name)
            merged.properties[name]=expression
    manifest = copy.deepcopy(manifests[0])
    # 条件によって追加されたobjectやsource記号も，最初のprofileだけの対応表へ落とさない．
    objects = {}
    for index,item in enumerate(manifests):
        for obj in item['objects']:
            record=objects.setdefault(obj['token'],dict(obj,guardAlternatives=[]))
            alternative=dict(condition=predicates[index],guard=obj['guard'])
            if alternative not in record['guardAlternatives']:
                record['guardAlternatives'].append(alternative)
    manifest['objects']=list(objects.values())
    manifest['symbols']=merged.sources
    for key in ('outputSymbols','dataflowLabels','runtimeEnvironment','hashInputs'):
        manifest[key]=list({json.dumps(record,sort_keys=True):record for item in manifests for record in item.get(key,[])}.values())
    diagnostics = []
    for item in manifests:
        for diagnostic in item['diagnostics']:
            if diagnostic not in diagnostics:
                diagnostics.append(diagnostic)
    # 無限文字列等の不完全な候補は，危険不達を全体の保証へ昇格させない．
    for issue in space.coverage:
        if issue['code']!='repository-identity-assumption':
            diagnostics.append(dict(status='unsupported',code=issue['code'],message=issue['reason'],source=None,
                                    properties=list(merged.properties)))
    policy_records = []
    repositories = sorted({rows[0]['repository'] for rows in space.profiles})
    for repo in repositories:
        scopes = ('contents','packages','actions','deployments','id-token')
        profiles = {'restricted':{scope:'read' if scope in {'contents','packages'} else 'none' for scope in scopes},
                    'permissive':{scope:'none' if scope=='id-token' else 'write' for scope in scopes}}
        fixed = {}
        for rows in space.profiles:
            if rows[0]['repository']==repo:
                fixed.update(rows[0].get('defaultPermissions',{}))
        unknown = {scope:symbol('permission_default',repo+':'+scope) for scope in scopes
                   if symbol('permission_default',repo+':'+scope) in merged.frozen}
        if not unknown and not fixed:
            continue
        allowed = [name for name,values in profiles.items() if all(values.get(scope)==level for scope,level in fixed.items())]
        if not allowed:
            raise ValueError('repository defaultPermissions do not match a supported standard profile; use a complete scenario for nonstandard policy')
        policy = symbol('repository_policy',repo)
        merged.frozen[policy]=allowed
        for scope,token in unknown.items():
            merged.initial_constraints.append(OR(AND(policy+' = '+name,token+' = perm_'+profiles[name][scope]) for name in allowed))
        policy_records.append(ExternalCondition('tokenPolicy',repo,'enum',allowed,'external-domain',
                                                'standard restricted/permissive defaults; additional organization restrictions are not represented',
                                                coverage='standard-profiles-only',symbol=policy,encodings={value:value for value in allowed}))
    for repo in repositories:
        contexts=[rows[0] for rows in space.profiles if rows[0]['repository']==repo]
        setting=symbol('fork_write_setting',repo)
        explicit_visibility=next((row['repositoryVisibility'] for row in contexts if 'repositoryVisibility' in row),None)
        if explicit_visibility=='public' and any(row.get('sendWriteTokens') is True for row in contexts):
            raise ValueError('public repository cannot enable private-fork write-token policy')
        if setting not in merged.frozen and explicit_visibility is None and not any(row.get('sendWriteTokens') is True for row in contexts) and symbol('repository_visibility',repo) not in merged.frozen:
            continue
        visibility=symbol('repository_visibility',repo)
        domain=[explicit_visibility] if explicit_visibility else ['public','private','internal']
        merged.frozen[visibility]=domain
        if setting in merged.frozen:
            merged.initial_constraints.append('!'+setting+' | '+visibility+' != public')
        elif any(row.get('sendWriteTokens') is True for row in contexts):
            merged.initial_constraints.append(visibility+' != public')
        policy_records.append(ExternalCondition('repositoryVisibility',repo,'enum',domain,
                                                'explicit' if explicit_visibility else 'external-domain',
                                                'private/internal fork policy; public forks cannot receive write tokens',
                                                symbol=visibility,encodings={value:value for value in domain}))
    condition_records = space.conditions+policy_records+_explicit_settings(space)+_frozen_conditions(space,merged)
    manifest.update(
        diagnostics=diagnostics,
        verdict='unknown/unsupported' if any(item['status']=='unsupported' for item in diagnostics) else 'pending-model-check',
        inputMode='automatic' if not space.restrictions else 'constrained-automatic',
        scenario=dict(mode='automatic',conditions=space.restrictions,
                      runs=[dict(id=rows[0]['id'],source='externalConditions') for rows in space.profiles]),
        externalConditions=dict(version=1,conditions=[item.json() for item in condition_records],
                                restrictions=space.restrictions,configurationCount=len(combinations),
                                constraints=['one legal event/activity/fork/ref/runner profile per run',
                                             'repository policy values are shared across runs',
                                             'first supplied workflow is already started under a matching declared trigger'],
                                coverage=dict(complete=not space.coverage,issues=space.coverage,
                                              fixedRepresentatives=['default branch main unless explicitly constrained','repository/head identity roles','branch/tag strings without relevant filters use a representative'],
                                              unmodeled=['branch protection and organization ceilings','secret availability and OIDC exchange','full Cache version derivation and service policy discovery','arbitrary file contents and external service behavior']),
                                runs=[dict(id=rows[0]['id'],workflow=space.workflows[i].get('name'),
                                           profiles=[dict(encoding='profile_'+str(i)+'_'+str(j),values={c.name:row.get(c.name) for c in space.conditions if c.owner==rows[0]['id']}) for j,row in enumerate(rows)]) for i,rows in enumerate(space.profiles)]),
        stateVariables=len(merged.variables),
    )
    text = merged.render()
    manifest['modelSha256']=hashlib.sha256(text.encode()).hexdigest()
    return text,manifest

def constrain_complete(paths, scenario, restrictions=None):
    """完全な旧scenarioへ同値な制限だけを受け付け，共有repository設定を照合する．"""
    result=copy.deepcopy(scenario)
    workflows=[yaml.load(Path(path).read_text(),Loader=WorkflowLoader) for path in paths]
    ids=[str(run.get('id','run'+str(i))) for i,run in enumerate(result['runs'])]
    selected,_=_restrictions({},restrictions,workflows,ids)
    for context,values in zip(result['runs'],selected):
        for key,value in values.items():
            old=context.get(key)
            if key in {'baseBranch','defaultBranch'}:
                old=old.removeprefix('refs/heads/') if isinstance(old,str) else old
                value=value.removeprefix('refs/heads/') if isinstance(value,str) else value
            if old is not None and old!=value:
                raise ValueError('CLI restriction contradicts the fixed legacy scenario: '+key)
            if context.get(key) is None:
                context[key]=value
    shared={}
    for context in result['runs']:
        for key in ('defaultPermissions','sendWriteTokens','repositoryVisibility'):
            value=context.get(key)
            entries=value.items() if isinstance(value,dict) else [('',value)]
            for scope,setting in entries:
                if setting is None:
                    continue
                identity=(context['repository'],key,scope)
                if identity in shared and shared[identity]!=setting:
                    raise ValueError('contradictory repository setting: '+str(identity))
                shared[identity]=setting
    for context in result['runs']:
        for (repo,key,scope),setting in shared.items():
            if context['repository']==repo:
                if scope:context.setdefault(key,{})[scope]=setting
                else:context[key]=setting
    return result


def fixed_condition_manifest(paths, scenario, system):
    """固定モードも同じ条件IRで説明し，未指定の既存FROZENVARを意味付きで残す．"""
    conditions=[]
    for index,context in enumerate(scenario['runs']):
        owner=str(context.get('id','run'+str(index)))
        for key in ('event','activity','fork','branch','scope','repository','defaultBranch','baseBranch',
                    'headBranch','defaultBranchWorkflow','runnerOS','runnerArch'):
            if key in context:
                value=context[key]
                conditions.append(ExternalCondition(key,owner,'boolean' if type(value) is bool else 'string',
                                                    [value],'explicit',str(paths[index]),coverage='explicit-fixed'))
    profiles=[[dict(context,id=str(context.get('id','run'+str(index))))] for index,context in enumerate(scenario['runs'])]
    space=ConditionSpace(scenario,profiles,conditions,[],[],[])
    conditions+=_explicit_settings(space)+_frozen_conditions(space,system)
    return dict(version=1,mode='fixed-legacy',configurationCount=1,conditions=[item.json() for item in conditions],
                restrictions=[],constraints=['explicit legacy contexts are fixed; existing backend semantics preserved'],
                coverage=dict(complete=False,issues=[dict(code='legacy-finite-scenario',reason='explicit contexts and content domains are assumptions, not a complete GitHub environment')]))
