"""General bounded run/job and shared-object v2 backend.

The scenario supplies explicit finite external contexts. YAML control/data
structure is retained; only NuSMV decides reachability. Unsupported contracts
invalidate safety interpretation, even if their diagnostic model has no trace.
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

import yaml
import workflow_to_smv as base
from yaml_to_model import WorkflowLoader
from . import expressions as ex
from . import policies as pol
from .transition import TransitionSystem, atom, symbol, conjunction as AND, disjunction as OR
from .composites import expand as expand_composites
from .action_contracts import resolve as resolve_action


CONTRACT_VERSION = 'bounded-contracts-2026-10-22.1'


@dataclass
class Step:
    id: str
    raw: dict
    source: dict
    contract: str = 'unsupported'
    ops: list = field(default_factory=list)
    versioned_contract: dict | None = None


@dataclass
class Job:
    id: str
    original: str
    run: int
    raw: dict
    matrix: dict
    source: dict
    steps: list
    needs: list = field(default_factory=list)


@dataclass
class Run:
    id: str
    path: str
    raw: dict
    context: dict
    jobs: list


def shell_contract(script):
    """Closed, line-oriented operations; no partial acceptance of a script."""
    operations = []
    for line in script.splitlines():
        text = line.strip()
        if not text or text.startswith('#') or text in {'set -e', 'set -eu', 'set -euo pipefail'}:
            continue
        output = re.fullmatch(r'''echo\s+['"]([\w-]+)=(.*?)['"]\s*>>\s*['"]?\$GITHUB_OUTPUT['"]?''', text)
        write = re.fullmatch(r'''(?:echo|printf)\s+(['"])(.*?)\1\s*>\s*([\w./-]+)''', text)
        if output:
            operations.append(('output', output.group(1), output.group(2)))
        elif write:
            operations.append(('write', write.group(3), write.group(2)))
        elif '${{' in text or any(c in text for c in (';', '|', '&', '`', '$', '>', '<')):
            raise ValueError('shell expansion/control/redirection outside closed operation contract: ' + text)
        else:
            parts = shlex.split(text)
            if parts[:2] == ['mkdir', '-p'] and len(parts) == 3:
                operations.append(('mkdir', parts[2]))
            elif parts and parts[0] == 'cp' and len(parts) == 3:
                operations.append(('copy', parts[1], parts[2]))
            elif parts and parts[0] == 'cat' and len(parts) == 2:
                operations.append(('read', parts[1]))
            elif parts[:2] == ['git', 'add'] and len(parts) == 3:
                operations.append(('stage', parts[2]))
            elif parts[:2] == ['git', 'push'] and len(parts) in {2, 3, 4}:
                operations.append(('sink', 'contents', 'staged'))
            elif parts[:2] in (['npm', 'publish'], ['pnpm', 'publish']) and len(parts) in {2, 3}:
                operations.append(('sink', 'packages', parts[2] if len(parts) == 3 else '.'))
            elif parts[:3] == ['gh', 'api', 'repos/{owner}/{repo}/deployments']:
                operations.append(('sink', 'deployments', '.'))
            elif parts == ['exit', '1']:
                operations.append(('fail',))
            elif parts and parts[0] == 'echo':
                operations.append(('log',))
            else:
                raise ValueError('unrecognized property-relevant command: ' + text)
    return operations


class ExtendedCompiler:
    def __init__(self, paths, scenario):
        self.scenario = scenario
        self.policy = scenario.get('cachePolicy')
        self.ts = TransitionSystem()
        self.runs, self.jobs, self.diagnostics = [], [], []
        self.outputs, self.files, self.objects, self.caches = {}, {}, [], []
        self.static_refs = {}
        self.action_refs = set()
        self._unknowns = {}
        self._load(paths)
        self.clock_modulus = max(2, sum(len(job.steps) for job in self.jobs) + 2)
        self.ts.inputs['actor'] = ['idle'] + [job.id for job in self.jobs]
        self.ts.inputs['operation_ok'] = ['FALSE', 'TRUE']
        self.ts.inputs['cache_io_ok'] = ['FALSE', 'TRUE']
        if isinstance(scenario.get('cacheIO'), bool):
            self.ts.constraints.append('cache_io_ok = ' + str(scenario['cacheIO']).upper())
        self.ts.inputs['cancel_request'] = ['FALSE', 'TRUE']
        for setting, variable in [('operationOutcome', 'operation_ok'), ('cancelRequests', 'cancel_request')]:
            if isinstance(scenario.get(setting), bool):
                self.ts.constraints.append(variable + ' = ' + str(scenario[setting]).upper())
        for name in ['transfer', 'dependent_use', 'sink_site', 'authority_available', 'sink_succeeded', 'bad_authority']:
            self.ts.var(name, ['FALSE', 'TRUE'], 'FALSE')
        self.ts.properties = {
            'no_transfer': 'AG !transfer', 'no_use': 'AG !dependent_use',
            'no_sink_site': 'AG !sink_site', 'no_success': 'AG !sink_succeeded',
            'no_bad_authority': 'AG !bad_authority',
            'success_requires_site': 'AG (sink_succeeded -> sink_site)',
            'bad_requires_success': 'AG (bad_authority -> sink_succeeded)',
        }
        self._declare()
        # Resolve finite output domains before any consumer expression is
        # lowered, independently of the textual order of jobs in the YAML.
        for _ in range(9):
            before = {k: tuple(v[1]) for k, v in self.outputs.items()}
            for job in self.jobs:
                for step in job.steps:
                    for op in step.ops:
                        if op[0] == 'output':
                            values = self.template(op[2], job, step)
                            self.output(job, step.id, op[1], ['', *[ex.string(v) for v, _ in values]])
            if before == {k: tuple(v[1]) for k, v in self.outputs.items()}:
                break
        else:
            self.diagnostic('output-domain-cycle', 'finite output domains did not converge within nine dependency passes')

    def diagnostic(self, code, message, source=None, status='unsupported'):
        record = dict(status=status, code=code, message=message, source=source,
                      properties=['no_transfer', 'no_use', 'no_bad_authority'])
        if record not in self.diagnostics:
            self.diagnostics.append(record)

    def unknown(self, identity, message, source=None):
        name = symbol('unknown', identity)
        if name not in self._unknowns:
            self._unknowns[name] = identity
            self.ts.frozen[name] = ['FALSE', 'TRUE']
        self.diagnostic('unsupported-semantics', message, source)
        return name

    def _load(self, paths):
        configs = self.scenario.get('runs', [])
        if len(configs) != len(paths):
            raise base.WorkflowError('scenario.runs must contain one context per supplied workflow')
        for ri, (path, config) in enumerate(zip(paths, configs)):
            source = Path(path).read_text()
            raw = yaml.load(source, Loader=WorkflowLoader)
            tree = yaml.compose(source, Loader=WorkflowLoader)
            if not isinstance(raw, dict) or not isinstance(raw.get('jobs'), dict) or not raw['jobs']:
                raise base.WorkflowError('workflow requires jobs mapping: ' + str(path))
            # Require external facts instead of deriving run identity/trust from filenames.
            required = {'event', 'activity', 'branch', 'scope', 'repository', 'defaultBranch',
                        'baseBranch', 'fork', 'defaultBranchWorkflow', 'refType', 'runnerOS', 'runnerArch'}
            if not required.issubset(config):
                raise base.WorkflowError('scenario context missing: ' + ', '.join(sorted(required - set(config))))
            run = Run(str(config.get('id', f'run{ri}')), str(path), raw, dict(config), [])
            for name in raw.keys() - {'name', 'on', 'permissions', 'env', 'jobs', 'cache-mode', 'concurrency'}:
                self.diagnostic('workflow-key', 'workflow key lacks semantics: ' + str(name))
            if any(r.id == run.id for r in self.runs):
                raise base.WorkflowError('duplicate run id')
            self.runs.append(run)
            if config['event'] == 'workflow_call':
                self.diagnostic('workflow-call-context', 'caller event payload, permissions and secrets propagation are not implemented')
            if 'concurrency' in raw:
                self.diagnostic('concurrency', 'queue replacement/cancellation across additional runs is outside this bounded contract', base._span(base._member(tree, 'concurrency'), str(path)).json())
            jnode = base._member(tree, 'jobs')
            for name, job_raw in raw['jobs'].items():
                if not isinstance(job_raw, dict):
                    raise base.WorkflowError('job must be mapping')
                if 'uses' not in job_raw and 'runs-on' not in job_raw:
                    raise base.WorkflowError('step job requires runs-on')
                span = base._span(base._member(jnode, str(name)), str(path)).json()
                for key in job_raw.keys() - {'name', 'runs-on', 'needs', 'if', 'permissions', 'steps', 'env', 'outputs', 'strategy', 'cache-mode'}:
                    self.diagnostic('job-key', 'job key lacks semantics: ' + str(key), span)
                strategy = job_raw.get('strategy', {})
                matrices = [{}]
                if strategy:
                    try:
                        matrix = strategy['matrix']
                        if set(strategy) - {'matrix', 'fail-fast', 'max-parallel'}:
                            raise ValueError('unrecognized strategy keys')
                        if not isinstance(matrix, dict) or any(k in matrix for k in ('include', 'exclude')):
                            raise ValueError('only static cartesian matrix is supported; include/exclude requires merge semantics')
                        if strategy.get('fail-fast', True) is not False:
                            raise ValueError('matrix requires fail-fast: false; sibling cancellation is not inferred')
                        if 'max-parallel' in strategy:
                            raise ValueError('max-parallel is not modeled')
                        if any(not isinstance(v, list) or not v for v in matrix.values()):
                            raise ValueError('matrix axes require nonempty literal arrays')
                        if '${{' in json.dumps(matrix):
                            raise ValueError('dynamic matrix values are outside static cartesian expansion')
                        matrices = [dict(zip(matrix, vals)) for vals in product(*matrix.values())]
                        if len(matrices) > 32:
                            raise ValueError('matrix exceeds 32 expansions')
                    except (KeyError, TypeError, ValueError) as exc:
                        self.diagnostic('matrix', str(exc), span)
                        matrices = [{}]
                if 'uses' in job_raw:
                    self.diagnostic('reusable-workflow', 'reusable calls require caller/callee permission and secret propagation; not flattened implicitly', span)
                for feature in ('container', 'services', 'environment', 'concurrency', 'continue-on-error'):
                    if feature in job_raw:
                        self.diagnostic('job-' + feature, 'job feature not represented: ' + feature, span)
                for mi, matrix in enumerate(matrices):
                    identity = symbol('j', f'{run.id}:{name}:{mi}')
                    steps = []
                    snode = base._member(base._member(jnode, str(name)), 'steps')
                    seen = set()
                    expanded = expand_composites(job_raw.get('steps', []), snode, str(path),
                                                 self.scenario.get('compositeContracts', {}), self.diagnostic)
                    for si, (step_raw, step_span) in enumerate(expanded):
                        if not isinstance(step_raw, dict) or ('run' in step_raw) == ('uses' in step_raw):
                            raise base.WorkflowError('step requires exactly one run/uses')
                        sid = str(step_raw.get('id', f'index{si}'))
                        if sid in seen:
                            raise base.WorkflowError('duplicate step id ' + sid)
                        seen.add(sid)
                        step = Step(sid, step_raw, step_span)
                        for key in step_raw.keys() - {'id', 'name', 'run', 'uses', 'with', 'if', 'env', 'continue-on-error', 'shell'}:
                            self.diagnostic('step-key', 'step key lacks semantics: ' + str(key), step_span)
                        if 'run' in step_raw:
                            try:
                                step.ops = shell_contract(str(step_raw['run']))
                                step.contract = 'shell-operations-v1'
                            except (ValueError, TypeError) as exc:
                                self.diagnostic('shell-contract', str(exc), step_span)
                        else:
                            uses = str(step_raw['uses'])
                            self.action_refs.add(uses)
                            contract = resolve_action(uses)
                            if contract:
                                step.contract = contract.effect
                                step.versioned_contract = asdict(contract)
                            else:
                                self.diagnostic('action-contract', 'unregistered versioned action: ' + uses, step_span)
                        for feature in ('working-directory', 'timeout-minutes'):
                            if feature in step_raw:
                                self.diagnostic('step-' + feature, 'step feature not represented: ' + feature, step_span)
                        if step_raw.get('shell', 'bash') != 'bash':
                            self.diagnostic('shell-language', 'only bash closed operation contracts are registered', step_span)
                        steps.append(step)
                    job = Job(identity, str(name), ri, job_raw, matrix, span, steps)
                    self.jobs.append(job)
                    run.jobs.append(job)
        for run in self.runs:
            for job in run.jobs:
                needs = job.raw.get('needs', [])
                if isinstance(needs, str):
                    needs = [needs]
                for dep in needs:
                    matches = [j.id for j in run.jobs if j.original == dep]
                    if not matches:
                        raise base.WorkflowError('missing needs job: ' + str(dep))
                    job.needs.extend(matches)
        graph = {j.id: j.needs for j in self.jobs}
        def visit(jid, active):
            if jid in active:
                raise base.WorkflowError('needs cycle')
            for dep in graph[jid]:
                visit(dep, active | {jid})
        for job in self.jobs:
            visit(job.id, set())

    def _declare(self):
        for job in self.jobs:
            pc = 'pc_' + job.id
            self.ts.var(pc, ['pending', 'success', 'failure', 'skipped', 'cancelled'] + [f's{i}' for i in range(len(job.steps) + 1)], 'pending', job.source)
            self.ts.var('failed_' + job.id, ['FALSE', 'TRUE'], 'FALSE', job.source)
            self.ts.var('cancelled_' + job.id, ['FALSE', 'TRUE'], 'FALSE', job.source)
            self.ts.var('staged_' + job.id, ['FALSE', 'TRUE'], 'FALSE', job.source)
            for i, step in enumerate(job.steps):
                outcome = self.outcome(job, step)
                self.ts.var(outcome, ['unset', 'success', 'failure', 'skipped', 'cancelled'], 'unset', step.source)
                self.ts.var(outcome + '_conclusion', ['unset', 'success', 'failure', 'skipped', 'cancelled'], 'unset', step.source)
                for op in step.ops:
                    if op[0] == 'output':
                        self.output(job, step.id, op[1], ['', op[2]])
                action = step.contract
                if action in {'actions/upload-artifact-v4', 'actions/cache/save-v4', 'actions/cache-v4'}:
                    inputs = step.raw.get('with', {})
                    kind = 'artifact' if 'upload-artifact' in action else 'cache'
                    key_field = 'name' if kind == 'artifact' else 'key'
                    raw_key = inputs.get(key_field, 'artifact' if kind == 'artifact' else '')
                    if any(token in json.dumps([raw_key, self.runs[job.run].raw.get('env', {}), job.raw.get('env', {}), step.raw.get('env', {})]) for token in ('.outputs.', 'needs.')):
                        self.diagnostic('state-dependent-object-key', 'object key/name depending on step/job outputs requires delayed candidate expansion', step.source)
                    alternatives = self.template(raw_key, job, step)
                    for name, guard in alternatives:
                        if not ex.string(name) or (kind == 'cache' and len(ex.string(name)) > 512):
                            self.diagnostic('invalid-object-name', 'object key/name is empty or exceeds cache key bound', step.source)
                        token = symbol('object', f'{job.id}:{step.id}:{kind}:{name}')
                        obj = dict(token=token, job=job, step=step, index=i, name=ex.string(name), guard=guard,
                                   kind=kind, path=self.action_path(inputs.get('path', '.'), job, step), source=step.source)
                        self.ts.var(token, ['absent', 'clean', 'tainted'], 'absent', step.source)
                        self.ts.var('age_' + token, [str(i) for i in range(self.clock_modulus)], '0', step.source) if kind == 'cache' else None
                        (self.objects if kind == 'artifact' else self.caches).append(obj)
                    if kind == 'artifact':
                        self.output(job, step.id, 'artifact-id', ['', *[o['token'] for o in self.objects if o['job'] is job and o['step'] is step]])
                        self.output(job, step.id, 'artifact-digest', ['', 'runtime-digest-unknown'])
                if action in {'actions/cache-v4', 'actions/cache/restore-v4'}:
                    self.output(job, step.id, 'cache-hit', ['', 'true', 'false'])
                    self.ts.var('cache_exact_' + self.outcome(job, step), ['FALSE', 'TRUE'], 'FALSE', step.source)
        if self.caches:
            # At most one creation per candidate slot; no loop/unbounded run generation.
            self.ts.var('save_clock', [str(i) for i in range(self.clock_modulus)], '0')

    def output(self, job, sid, name, values=None):
        lookup = (job.id, sid, name)
        if lookup not in self.outputs:
            values = values or ['']
            sym = symbol('out', ':'.join(lookup))
            self.outputs[lookup] = (sym, list(dict.fromkeys(values)))
            self.ts.var(sym, [atom(v) for v in self.outputs[lookup][1]], atom(''), job.source)
        elif values:
            sym, domain = self.outputs[lookup]
            domain[:] = list(dict.fromkeys(domain + values))
            self.ts.variables[sym] = ([atom(v) for v in domain] + ([atom('__sentinel__')] if len(domain) == 1 else []), atom(''))
        return self.outputs[lookup]

    @staticmethod
    def outcome(job, step):
        return symbol('outcome', job.id + ':' + step.id)

    def file(self, job, path):
        if not isinstance(path, str) or any(c in path for c in '${}*?!\n~'):
            self.diagnostic('file-path', 'only normalized literal workspace paths are modeled: ' + repr(path), job.source)
        path = str(path).removeprefix('./').rstrip('/') or '.'
        lookup = (job.id, path)
        if lookup not in self.files:
            sym = symbol('file', job.id + ':' + path)
            self.ts.var(sym, ['absent', 'clean', 'tainted'], 'absent', job.source)
            self.files[lookup] = sym
        return self.files[lookup]

    def action_path(self, raw, job, step):
        alternatives = self.template(raw, job, step)
        paths = list(dict.fromkeys(ex.string(value) for value, _ in alternatives))
        if len(paths) != 1:
            self.diagnostic('dynamic-path-domain', 'multiple dynamic filesystem paths need separate file slots', step.source)
            return 'unsupported-path'
        return paths[0]

    def statuses(self, job, step=None):
        deps = [d for d in job.needs]
        success = AND('!failed_' + job.id, '!cancelled_' + job.id) if step else AND(*(f'pc_{d} = success' for d in deps))
        failure = OR([f'failed_{job.id}', *[f'pc_{d} = failure' for d in deps]])
        return dict(success=success, failure=failure, cancelled='cancelled_' + job.id)

    def resolve(self, ref, job, step, resolving=None):
        run = self.runs[job.run]
        ctx = run.context
        constants = {
            'github.event_name': ctx['event'], 'github.ref_name': ctx['branch'],
            'github.ref': ctx['scope'], 'github.repository': ctx['repository'],
            'github.run_id': run.id, 'github.workflow': run.raw.get('name', run.id),
            'github.event.workflow_run.id': ctx.get('upstreamRun', ''),
            'github.event.workflow_run.name': ctx.get('upstreamName', ''),
            'runner.os': ctx.get('runnerOS', 'Linux'), 'runner.arch': ctx.get('runnerArch', 'X64'),
            'strategy.job-index': self.runs[job.run].jobs.index(job),
            'strategy.job-total': len([j for j in run.jobs if j.original == job.original]),
        }
        if ref in constants:
            return [(constants[ref], 'TRUE')]
        if ref.startswith('github.event.workflow_run.'):
            upstream = next((r for r in self.runs if r.id == str(ctx.get('upstreamRun'))), None)
            if upstream and ref == 'github.event.workflow_run.event':
                return [(upstream.context['event'], 'TRUE')]
            if upstream and ref == 'github.event.workflow_run.head_branch':
                return [(upstream.context['branch'], 'TRUE')]
            if upstream and ref == 'github.event.workflow_run.conclusion' and len(upstream.jobs) == 1:
                pc = 'pc_' + upstream.jobs[0].id
                return [(value, pc + ' = ' + value) for value in ('success', 'failure', 'cancelled', 'skipped')] + [('', '!' + '(' + pc + ' in { success, failure, cancelled, skipped })')]
        if ref.startswith('matrix.') and ref[7:] in job.matrix:
            return [(job.matrix[ref[7:]], 'TRUE')]
        if ref.startswith('steps.'):
            match = re.fullmatch(r'steps\.([\w-]+)\.(outputs\.([\w-]+)|outcome|conclusion)', ref)
            if match:
                sid, field, output = match.groups()
                if field.startswith('outputs.'):
                    if output == 'artifact-digest':
                        raise ex.UnsupportedExpression('runtime artifact digest comparisons require a correlated digest contract')
                    sym, domain = self.output(job, sid, output)
                    if not any(s.id == sid for s in job.steps):
                        self.diagnostic('missing-step-reference', ref, step.source if step else job.source)
                    return [(v, f'{sym} = {atom(v)}') for v in domain]
                writer = next((s for s in job.steps if s.id == sid), None)
                if writer:
                    sym = self.outcome(job, writer) + ('_conclusion' if field == 'conclusion' else '')
                    return [(v if v != 'unset' else '', f'{sym} = {v}') for v in ['unset', 'success', 'failure', 'skipped', 'cancelled']]
        if ref.startswith('needs.'):
            match = re.fullmatch(r'needs\.([\w-]+)\.(result|outputs\.([\w-]+))', ref)
            if match:
                name, field, output = match.groups()
                deps = [j for j in run.jobs if j.original == name and j.id in job.needs]
                if len(deps) == 1:
                    dep = deps[0]
                    if field == 'result':
                        return [(v, f'pc_{dep.id} = {v}') for v in ['success', 'failure', 'skipped', 'cancelled']]
                    raw = dep.raw.get('outputs', {}).get(output, '')
                    return self.template(raw, dep, None)
                self.diagnostic('needs-matrix-output', 'needs outputs of multiple matrix jobs require completion-order semantics', job.source)
        if ref.startswith('env.'):
            name = ref[4:]
            raw = {**run.raw.get('env', {}), **job.raw.get('env', {}), **(step.raw.get('env', {}) if step else {})}.get(name, '')
            resolving = set(resolving or ())
            if ref in resolving:
                raise ex.UnsupportedExpression('cyclic env binding ' + ref)
            return self.template(raw, job, step, resolving | {ref})
        if ref in ctx.get('domains', {}):
            values = ctx['domains'][ref]
            if not isinstance(values, list) or not values:
                raise ex.UnsupportedExpression('finite context domain must be nonempty')
            sym = symbol('ctx', run.id + ':' + ref)
            self.ts.frozen[sym] = list(dict.fromkeys(atom(v) for v in values))
            return [(v, f'{sym} = {atom(v)}') for v in values]
        if ref.startswith(('inputs.', 'vars.')):
            return [(ctx.get(ref.split('.')[0], {}).get(ref.split('.', 1)[1], ''), 'TRUE')]
        raise ex.UnsupportedExpression('no complete finite domain for ' + ref)

    def expression(self, raw, job, step=None, resolving=None):
        node = ex.parse(raw)
        return ex.lower(node, lambda r: self.resolve(r, job, step, resolving), self.statuses(job, step))

    def template(self, raw, job, step=None, resolving=None):
        if not isinstance(raw, str):
            return [(raw, 'TRUE')]
        matches = list(re.finditer(r'\$\{\{(.*?)}}', raw))
        if not matches:
            return [(raw, 'TRUE')]
        try:
            if len(matches) == 1 and matches[0].span() == (0, len(raw)):
                return self.expression(matches[0].group(1), job, step, resolving)
            result, offset = [('', 'TRUE')], 0
            for match in matches:
                values = self.expression(match.group(1), job, step, resolving)
                result = [(s + raw[offset:match.start()] + ex.string(v), AND(g, h)) for s, g in result for v, h in values]
                offset = match.end()
            return [(s + raw[offset:], g) for s, g in result]
        except (ex.UnsupportedExpression, KeyError, TypeError, ValueError) as exc:
            self.diagnostic('expression', str(exc), step.source if step else job.source)
            return [('unsupported-value', 'TRUE')]

    def condition(self, raw, job, step=None):
        if raw is None:
            return self.statuses(job, step)['success']
        try:
            node = ex.parse(raw)
            has_status = any(n.kind == 'call' and n.value in {'always', 'success', 'failure', 'cancelled'} for n in ex.walk(node))
            predicate = OR(g for v, g in self.expression(raw, job, step) if ex.truth(v))
            return predicate if has_status else AND(self.statuses(job, step)['success'], predicate)
        except (ex.UnsupportedExpression, KeyError, TypeError, ValueError) as exc:
            return self.unknown((job.id, str(raw)), str(exc), step.source if step else job.source)

    def gate(self, job, index):
        return AND(f'actor = {job.id}', f'pc_{job.id} = s{index}', '!cancel_request')

    def _enabled(self, run):
        events = run.raw.get('on', {})
        if isinstance(events, str):
            events = {events: None}
        elif isinstance(events, list):
            events = dict.fromkeys(events)
        try:
            enabled = 'TRUE' if pol.trigger(events, run.context) else 'FALSE'
        except (pol.UnsupportedPolicy, KeyError, TypeError) as exc:
            enabled = self.unknown((run.id, 'trigger'), str(exc))
        after = run.context.get('after')
        if run.context['event'] == 'workflow_run':
            after = run.context.get('upstreamRun')
            if run.context.get('activity') != 'completed':
                self.diagnostic('workflow-run-activity', 'only completed workflow_run ordering is modeled')
        if after:
            prior = next((r for r in self.runs if r.id == str(after)), None)
            if prior:
                enabled = AND(enabled, *(f'pc_{j.id} in {{ success, failure, skipped, cancelled }}' for j in prior.jobs))
            else:
                enabled = AND(enabled, self.unknown((run.id, 'after'), 'upstream run not supplied'))
        return enabled

    def _version(self, obj):
        job, step = obj['job'], obj['step']
        ctx = self.runs[job.run].context
        inputs = step.raw.get('with', {})
        if inputs.get('enableCrossOsArchive') not in (None, False, 'false'):
            self.diagnostic('cross-os-cache', 'cross-OS cache archive compatibility is outside the contract', step.source)
        # Opaque version identity asserted by the scenario; not a real service hash.
        return ctx.get('cacheVersion', {}).get(str(obj['path']), 'unasserted')

    def _cache_mode(self, job):
        run = self.runs[job.run]
        mode = job.raw.get('cache-mode', run.raw.get('cache-mode', run.context.get('cacheMode')))
        if self.policy == 'github-current-2026-10-22' and mode is None:
            mode = 'write' if run.context['event'] in {'push', 'pull_request', 'workflow_dispatch', 'schedule'} else 'read'
        return mode

    def token_permission(self, job, scope, write=False):
        run = self.runs[job.run]
        ctx = run.context
        fork_gate = 'TRUE'
        permission_context = ctx
        if ctx['event'] == 'pull_request' and ctx['fork']:
            permission_context = {**ctx, 'sendWriteTokens': True}
            if ctx.get('sendWriteTokens') is None:
                setting = symbol('fork_write_setting', ctx['repository'])
                self.ts.frozen[setting] = ['FALSE', 'TRUE']
                self.diagnostic('fork-write-policy', 'fork PR write-token setting was not supplied', job.source, 'unknown')
                fork_gate = setting if write else 'TRUE'
            elif ctx.get('sendWriteTokens') is False and write:
                fork_gate = 'FALSE'
        value = pol.permission(run.raw, job.raw, permission_context, scope)
        if value is not None:
            return AND(fork_gate, str(value == 'write' if write else value in {'read', 'write'}).upper())
        # Repository defaults are one shared enum per repository/scope, not
        # independently chosen write/read flags for each use or each job.
        name = symbol('permission_default', run.context['repository'] + ':' + scope)
        self.ts.frozen[name] = ['perm_none', 'perm_read', 'perm_write'] if scope != 'id-token' else ['perm_none', 'perm_write']
        self.diagnostic('permission-default', 'effective ' + scope + ' permission depends on shared repository policy', job.source, 'unknown')
        return AND(fork_gate, name + ' = perm_write' if write else name + ' in { perm_read, perm_write }')

    def _save(self, obj, guard):
        job = obj['job']
        run = self.runs[job.run]
        token = obj['token']
        source = self.file(job, obj['path'])
        if obj['kind'] == 'cache':
            guard = AND(guard, 'cache_io_ok')
            try:
                writable = pol.cache_writable(run.context, self.policy, self._cache_mode(job))
                guard = AND(guard, 'TRUE' if writable else 'FALSE')
            except pol.UnsupportedPolicy as exc:
                guard = AND(guard, self.unknown((job.id, 'cache-write-policy'), str(exc), obj['source']))
            others = [o for o in self.caches if o['name'] == obj['name'] and self.runs[o['job'].run].context['repository'] == run.context['repository'] and self.runs[o['job'].run].context['scope'] == run.context['scope'] and self._version(o) == self._version(obj)]
            guard = AND(guard, *(o['token'] + ' = absent' for o in others))
            # Keep the arithmetic total even in unreachable maximum-clock
            # valuations; reachable executions create each slot at most once.
            tick = '(save_clock + 1) mod ' + str(self.clock_modulus)
            self.ts.update('age_' + token, guard, tick)
            self.ts.update('save_clock', guard, tick)
        else:
            if obj['step'].raw.get('with', {}).get('overwrite') not in (None, False, 'false'):
                self.diagnostic('artifact-overwrite', 'delete/recreate identity for overwrite is not implemented', obj['source'])
            duplicates = [o for o in self.objects if o is not obj and o['job'].run == job.run and o['name'] == obj['name']]
            guard = AND(guard, *(o['token'] + ' = absent' for o in duplicates))
        guard = AND(guard, source + ' != absent')
        self.ts.update(token, guard, source)
        if obj['kind'] == 'artifact':
            sym, _ = self.output(job, obj['step'].id, 'artifact-id')
            self.ts.update(sym, guard, atom(token))
            digest, _ = self.output(job, obj['step'].id, 'artifact-digest')
            self.ts.update(digest, guard, atom('runtime-digest-unknown'))

    def _restore(self, job, step, guard):
        run, inputs = self.runs[job.run], step.raw.get('with', {})
        cache = 'cache' in step.contract
        destination_path = self.action_path(inputs.get('path', '.'), job, step)
        destination = self.file(job, destination_path)
        candidates = []
        if cache:
            restore_keys = self.template(inputs.get('restore-keys', ''), job, step)
            for key, key_guard in self.template(inputs.get('key', ''), job, step):
                for prefixes, prefix_guard in restore_keys:
                    for obj in self.caches:
                        try:
                            visible = pol.cache_visible(self.runs[obj['job'].run].context, run.context, self.policy)
                        except pol.UnsupportedPolicy as exc:
                            self.diagnostic('cache-policy', str(exc), step.source)
                            visible = False
                        try:
                            rank = pol.cache_rank(obj['name'], ex.string(key), [p.strip() for p in str(prefixes).splitlines() if p.strip()])
                        except pol.UnsupportedPolicy as exc:
                            self.diagnostic('cache-key-collation', str(exc), step.source)
                            rank = None
                        current = dict(job=job, step=step, path=destination_path)
                        version = self._version(obj)
                        if version == 'unasserted' or self._version(current) == 'unasserted':
                            self.diagnostic('cache-version', 'cache version identity needs explicit per-path opaque scenario values', step.source)
                        same_version = version == self._version(current)
                        if rank is not None and visible and same_version:
                            scope = self.runs[obj['job'].run].context['scope']
                            scope_rank = 0 if scope == run.context['scope'] else 1 if scope == run.context['baseBranch'] else 2
                            candidates.append((obj, (scope_rank, rank), AND(key_guard, prefix_guard)))
            if self.policy == 'github-current-2026-10-22' and self._cache_mode(job) in {'none', 'write-only'}:
                candidates = []
        else:
            for name, name_guard in self.template(inputs.get('name', ''), job, step):
                for obj in self.objects:
                    owner = self.runs[obj['job'].run]
                    selector = inputs.get('run-id')
                    allowed_runs = self.template(selector, job, step) if selector is not None else [(run.id, 'TRUE')]
                    for rid, rid_guard in allowed_runs:
                        if str(rid) != owner.id:
                            continue
                        if str(inputs.get('repository', run.context['repository'])) != owner.context['repository']:
                            continue
                        if owner.id != run.id and not inputs.get('github-token'):
                            self.diagnostic('artifact-token', 'cross-run download requires an explicit token capability', step.source)
                            continue
                        token_guard = 'TRUE'
                        if owner.id != run.id:
                            if inputs.get('github-token') not in {'${{ secrets.GITHUB_TOKEN }}', '${{ github.token }}'}:
                                self.diagnostic('artifact-token-contract', 'cross-run token must be an explicit GITHUB_TOKEN expression', step.source)
                            token_guard = self.token_permission(job, 'actions')
                        if name and obj['name'] != str(name):
                            continue
                        if 'pattern' in inputs and not pol.glob(str(inputs['pattern']), obj['name']):
                            continue
                        if 'artifact-ids' in inputs:
                            ids = self.template(inputs['artifact-ids'], job, step)
                            idguard = OR(g for value, g in ids if obj['token'] in str(value).split(','))
                        else:
                            idguard = 'TRUE'
                        candidates.append((obj, (0, 0), AND(name_guard, rid_guard, idguard, token_guard)))
            if not inputs.get('name') and not inputs.get('artifact-ids'):
                self.diagnostic('artifact-multiple-download', 'all/pattern retrieval layout and merge-multiple are not fully modeled', step.source)
        choices = []
        for index, (obj, rank, selector) in enumerate(candidates):
            present = AND(obj['token'] + ' != absent', selector)
            blockers = []
            if cache:
                for other, other_rank, other_selector in candidates:
                    if other['token'] == obj['token']:
                        continue
                    if other_rank < rank:
                        blockers.append('!' + AND(other['token'] + ' != absent', other_selector))
                    elif other_rank == rank:
                        blockers.append('!' + AND(other['token'] + ' != absent', other_selector, 'age_' + other['token'] + ' > age_' + obj['token']))
            selected = AND(present, *blockers)
            choice = symbol('select', self.outcome(job, step))
            self.ts.inputs.setdefault(choice, ['none'])
            if 'id_' + obj['token'] not in self.ts.inputs[choice]:
                self.ts.inputs[choice].append('id_' + obj['token'])
            effect = AND(guard, selected, choice + ' = id_' + obj['token'])
            choices.append(selected)
            self.ts.update(destination, effect, obj['token'])
            self.ts.latch('transfer', effect)
            if cache:
                hit, _ = self.output(job, step.id, 'cache-hit')
                self.ts.update(hit, effect, atom('true' if rank[1] == 0 else 'false'))
                if rank[1] == 0:
                    self.ts.latch('cache_exact_' + self.outcome(job, step), effect)
        any_match = OR(choices)
        if candidates:
            choice = symbol('select', self.outcome(job, step))
            valid = []
            for obj, rank, selector in candidates:
                blockers = []
                if cache:
                    for other, other_rank, other_selector in candidates:
                        if other['token'] == obj['token']:
                            continue
                        if other_rank < rank:
                            blockers.append('!' + AND(other['token'] + ' != absent', other_selector))
                        elif other_rank == rank:
                            blockers.append('!' + AND(other['token'] + ' != absent', other_selector,
                                                     'age_' + other['token'] + ' > age_' + obj['token']))
                valid.append(AND(choice + ' = id_' + obj['token'], obj['token'] + ' != absent', selector, *blockers))
            self.ts.constraints.append(AND(guard, any_match) + ' -> ' + OR(valid))
        if cache:
            hit, _ = self.output(job, step.id, 'cache-hit')
            self.ts.update(hit, AND(guard, '!' + any_match), atom(''))
        return any_match

    def _shell(self, job, step, guard):
        touched = set()
        # Multiple operations updating a path within one step need ordered
        # microstates; reject such scripts rather than read stale pre-state.
        writes = [op[2] if op[0] == 'copy' else op[1] for op in step.ops if op[0] in {'copy', 'write'}]
        reads = [op[1] for op in step.ops if op[0] in {'copy', 'read', 'stage'}]
        if set(writes) & set(reads) or len(set(writes)) != len(writes):
            self.diagnostic('shell-sequential-effects', 'split dependent file writes/reads into steps; microstep lowering not implemented', step.source)
        for op in step.ops:
            kind = op[0]
            if kind == 'write':
                self.ts.update(self.file(job, op[1]), guard, 'clean')
            elif kind == 'copy':
                source, dest = self.file(job, op[1]), self.file(job, op[2])
                self.ts.update(dest, guard, source)
                self.ts.latch('dependent_use', AND(guard, source + ' = tainted'))
            elif kind in {'read', 'stage'}:
                source = self.file(job, op[1])
                self.ts.latch('dependent_use', AND(guard, source + ' = tainted'))
                if kind == 'stage':
                    self.ts.latch('staged_' + job.id, AND(guard, source + ' = tainted'))
            elif kind == 'output':
                values = self.template(op[2], job, step)
                sym, _ = self.output(job, step.id, op[1], ['', *[ex.string(v) for v, _ in values]])
                for value, condition in values:
                    self.ts.update(sym, AND(guard, condition), atom(ex.string(value)))
            elif kind == 'sink':
                scope = op[1]
                taint = 'staged_' + job.id if op[2] == 'staged' else self.file(job, op[2]) + ' = tainted'
                self.ts.latch('sink_site', guard.replace('operation_ok', 'TRUE'))
                capability = self.token_permission(job, scope, write=True)
                if scope != 'packages':
                    authenticated = self.runs[job.run].context.get('tokenAuthenticated', {}).get(scope)
                    if authenticated is None:
                        auth = symbol('authentication', f'{job.run}:{scope}')
                        self.ts.frozen[auth] = ['FALSE', 'TRUE']
                        self.diagnostic('sink-authentication', 'command authentication with GITHUB_TOKEN is unknown', step.source, 'unknown')
                    else:
                        auth = str(bool(authenticated)).upper()
                    capability = AND(capability, auth)
                # Package publishing can use external credentials instead of GITHUB_TOKEN.
                if scope == 'packages':
                    cred = self.runs[job.run].context.get('externalPackageCredential')
                    if cred is None:
                        external = symbol('credential', str(job.run) + ':packages')
                        self.ts.frozen[external] = ['FALSE', 'TRUE']
                        self.diagnostic('external-credential', 'package registry credential availability is unknown', step.source, 'unknown')
                    else:
                        external = str(bool(cred)).upper()
                    capability = external
                self.ts.latch('authority_available', AND(guard.replace('operation_ok', 'TRUE'), capability))
                self.ts.latch('sink_succeeded', AND(guard, capability))
                self.ts.latch('dependent_use', AND(guard, taint))
                self.ts.latch('bad_authority', AND(guard, capability, taint))

    def build(self):
        for job in self.jobs:
            run = self.runs[job.run]
            pc = 'pc_' + job.id
            selected = 'actor = ' + job.id
            terminal = AND(*(f'pc_{d} in {{ success, failure, skipped, cancelled }}' for d in job.needs))
            start = AND(selected, pc + ' = pending', self._enabled(run), terminal)
            condition = self.condition(job.raw.get('if'), job)
            self.ts.update(pc, AND(start, condition), 's0')
            self.ts.update(pc, AND(start, '!' + '(' + condition + ')'), 'skipped')
            self.ts.latch('cancelled_' + job.id, AND(selected, 'cancel_request', '!' + '(' + pc + ' in { pending, success, failure, skipped, cancelled })'))
            self.ts.update(pc, AND(selected, '!cancel_request', pc + f' = s{len(job.steps)}', 'cancelled_' + job.id), 'cancelled')
            self.ts.update(pc, AND(selected, '!cancel_request', pc + f' = s{len(job.steps)}', '!cancelled_' + job.id, 'failed_' + job.id), 'failure')
            self.ts.update(pc, AND(selected, '!cancel_request', pc + f' = s{len(job.steps)}', '!cancelled_' + job.id, '!failed_' + job.id), 'success')
            for i, step in enumerate(job.steps):
                gate = self.gate(job, i)
                condition = self.condition(step.raw.get('if'), job, step)
                execute = AND(gate, condition)
                success = AND(execute, 'operation_ok')
                failure = AND(execute, '!operation_ok')
                action = step.contract
                if action == 'unsupported':
                    self.diagnostic('unsupported-step-effects', 'no safety interpretation for omitted step effects', step.source)
                inputs = step.raw.get('with', {})
                if action == 'actions/checkout-v4':
                    default_untrusted = run.context['event'] == 'pull_request'
                    if 'ref' in inputs or 'repository' in inputs:
                        untrusted = run.context.get('checkoutUntrusted')
                        if untrusted is None:
                            self.diagnostic('checkout-provenance', 'custom checkout ref trust requires explicit scenario fact', step.source)
                            untrusted = True
                    else:
                        untrusted = default_untrusted
                    for path in run.context.get('checkoutFiles', []):
                        self.ts.update(self.file(job, path), success, 'tainted' if untrusted else 'clean')
                    if not run.context.get('checkoutFiles'):
                        self.diagnostic('checkout-files', 'bounded checkout file inventory was not supplied', step.source)
                elif action == 'shell-operations-v1':
                    self._shell(job, step, success)
                    if any(op[0] == 'fail' for op in step.ops):
                        failure, success = execute, 'FALSE'
                elif action in {'actions/upload-artifact-v4', 'actions/cache/save-v4'}:
                    for obj in self.objects + self.caches:
                        if obj['job'] is job and obj['step'] is step:
                            self._save(obj, AND(success, obj['guard']))
                elif action in {'actions/download-artifact-v4', 'actions/cache/restore-v4', 'actions/cache-v4'}:
                    matched = self._restore(job, step, success)
                    if action == 'actions/download-artifact-v4' or inputs.get('fail-on-cache-miss') in {True, 'true'}:
                        failure = OR([failure, AND(execute, '!' + matched)])
                        success = AND(success, matched)
                # General action inputs not interpreted by a contract remain explicit blockers.
                supported_inputs = {
                    'actions/checkout-v4': {'ref', 'repository', 'persist-credentials'},
                    'actions/upload-artifact-v4': {'name', 'path'},
                    'actions/download-artifact-v4': {'name', 'path', 'run-id', 'repository', 'github-token', 'artifact-ids', 'pattern'},
                    'actions/cache-v4': {'key', 'path', 'restore-keys', 'fail-on-cache-miss'},
                    'actions/cache/save-v4': {'key', 'path'},
                    'actions/cache/restore-v4': {'key', 'path', 'restore-keys', 'fail-on-cache-miss'},
                }
                if action in supported_inputs:
                    for name in inputs.keys() - supported_inputs[action]:
                        self.diagnostic('action-input', f'{action}: input {name} lacks effect contract', step.source)
                self.ts.update(pc, gate, f's{i + 1}')
                out = self.outcome(job, step)
                self.ts.update(out, AND(gate, '!' + '(' + condition + ')'), 'skipped')
                self.ts.update(out, success, 'success')
                self.ts.update(out, failure, 'failure')
                coe = step.raw.get('continue-on-error', False)
                if not isinstance(coe, bool):
                    self.diagnostic('continue-on-error-expression', 'continue-on-error currently requires Boolean literal', step.source)
                    coe = False
                self.ts.update(out + '_conclusion', AND(gate, '!' + '(' + condition + ')'), 'skipped')
                self.ts.update(out + '_conclusion', success, 'success')
                self.ts.update(out + '_conclusion', failure, 'success' if coe else 'failure')
                if not coe:
                    self.ts.latch('failed_' + job.id, failure)
            # actions/cache saves after a successful job, using final workspace
            # state and the latched exact-hit result from its restore step.
            for obj in self.caches:
                if obj['job'] is job and obj['step'].contract == 'actions/cache-v4':
                    out = self.outcome(job, obj['step'])
                    guard = AND(selected, '!cancel_request', pc + f' = s{len(job.steps)}', '!failed_' + job.id, '!cancelled_' + job.id,
                                out + ' = success', '!cache_exact_' + out, 'operation_ok', obj['guard'])
                    self._save(obj, guard)
            if sum(obj['job'] is job and obj['step'].contract == 'actions/cache-v4' for obj in self.caches) > 1:
                self.diagnostic('multiple-cache-post-actions', 'reverse ordered post-action frames are not implemented for multiple combined cache actions', job.source)
        manifest = {
            'modelVersion': '2-extended-bounded', 'contractVersion': CONTRACT_VERSION,
            'cachePolicy': self.policy, 'scenario': self.scenario,
            'sources': [{'file': r.path, 'sha256': hashlib.sha256(Path(r.path).read_bytes()).hexdigest()} for r in self.runs],
            'jobs': [asdict(job) for job in self.jobs],
            'objects': [{k: v for k, v in o.items() if k not in {'job', 'step'}} | {'job': o['job'].id, 'step': o['step'].id} for o in self.objects + self.caches],
            'symbols': self.ts.sources, 'outputSymbols': [{'job': j, 'step': s, 'output': n, 'symbol': v[0], 'domain': v[1]} for (j, s, n), v in self.outputs.items()],
            'diagnostics': self.diagnostics, 'actionRefs': sorted(self.action_refs),
            'verdict': 'unknown/unsupported' if any(d['status'] == 'unsupported' for d in self.diagnostics) else 'pending-model-check',
            'assumptions': ['one bounded run per supplied workflow; no eviction/retention expiry',
                            'scenario contexts and opaque cache versions are assumptions, not runtime evidence',
                            'nondeterministic action/network success; shell operations are atomic per step',
                            'tag contracts are version families; tags are not resolved to immutable source commits'],
            'properties': self.ts.properties,
            'stateVariables': len(self.ts.variables),
        }
        return self.ts.render(), manifest


def compile_extended(paths, scenario):
    try:
        return ExtendedCompiler(paths, scenario).build()
    except (yaml.YAMLError, ValueError, TypeError, KeyError, RecursionError) as exc:
        raise base.WorkflowError(str(exc)) from exc
