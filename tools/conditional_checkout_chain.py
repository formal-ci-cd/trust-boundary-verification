#!/usr/bin/env python3
"""Detect a supported conditional PR-ref -> checkout -> local executable chain.

The external action's bundled JavaScript is an explicit input. A finding is
conditional on that source matching the action version resolved at runtime;
mutable tags cannot establish historical runtime identity by themselves.
This is static reachability, not proof that a shell command or exfiltration ran.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re

import yaml
import yaml_to_model


COND = re.compile(r"\$\{\{\s*github\.event_name\s*==\s*['\"]pull_request_target['\"]\s*\}\}")
PR_MERGE = re.compile(r"refs/pull/\$\{\{\s*github\.event\.pull_request\.number\s*\}\}/merge")
OUTPUT = re.compile(r"\$\{\{\s*steps\.([\w-]+)\.outputs\.value\s*\}\}")
LOCAL_EXEC = re.compile(r"(?m)^\s*\./[\w./-]+(?:\s|$)")
SECRET = re.compile(r"\$\{\{\s*secrets\.([\w-]+)\s*\}\}")


def action_semantics(action_yml, source):
    data = yaml.load(action_yml.read_text(), Loader=yaml_to_model.WorkflowLoader)
    if not isinstance(data, dict) or data.get('runs', {}).get('using') not in ['node16', 'node20', 'node24']:
        return False
    if data['runs'].get('main') != 'dist/index.js':
        return False
    if not {'cond', 'if_true', 'if_false'} <= set(data.get('inputs', {})):
        return False
    if 'value' not in data.get('outputs', {}):
        return False
    code = source.read_text()
    required = [
        r"const\s+cond\s*=\s*core\.getInput\(['\"]cond['\"]",
        r"const\s+ifTrue\s*=\s*core\.getInput\(['\"]if_true['\"]",
        r"const\s+ifFalse\s*=\s*core\.getInput\(['\"]if_false['\"]",
        r"core\.setOutput\(['\"]value['\"],\s*cond\s*===\s*['\"]true['\"]\s*\?\s*ifTrue\s*:\s*ifFalse\s*\)",
    ]
    return all(re.search(pattern, code) for pattern in required)


def evidence(file, job, step, role):
    return dict(file=file, job=job['id'], line=step['line'], step=step['index'], role=role)


def excludes_external_fork(condition):
    """Recognize only a direct same-repository PR-head equality guard.

    github.repository is the base repository under pull_request_target, so a
    comparison with a fixed repository name is not a fork exclusion.
    """
    condition = condition.strip()
    if condition.startswith('${{') and condition.endswith('}}'):
        condition = condition[3:-2].strip()
    return bool(re.fullmatch(
        r'github\.event\.pull_request\.head\.repo\.full_name\s*==\s*github\.repository'
        r'|github\.repository\s*==\s*github\.event\.pull_request\.head\.repo\.full_name',
        condition,
    ))


def discover(workflow_root, external_root, external_name, external_version):
    action_yml = external_root / 'action.yml'
    source = external_root / 'index.js'
    if not action_semantics(action_yml, source):
        return dict(status='unsupported-external-action', findings=[])
    findings = []
    unsupported_jobs = []
    fork_excluded_jobs = []
    for file, model in yaml_to_model.load_models(workflow_root).items():
        if not any(event['name'] == 'pull_request_target' for event in model['workflow']['events']):
            continue
        for job in model['workflow']['jobs']:
            if job.get('environment'):
                unsupported_jobs.append(dict(file=file, job=job['id'],
                                             reason='job environment selection or approval is not modeled'))
                continue
            external_fork_excluded = excludes_external_fork(job.get('condition', ''))
            steps = job['steps']
            for selector in steps:
                if (selector.get('action') != external_name
                        or selector.get('version') != external_version
                        or not selector.get('id') or selector.get('condition')):
                    continue
                args = selector.get('arguments', {})
                if not COND.fullmatch(args.get('cond', '')) or not PR_MERGE.fullmatch(args.get('if_true', '')):
                    continue
                expected = selector['id']
                for checkout in steps:
                    if checkout['index'] <= selector['index'] or checkout.get('action') != 'actions/checkout' or checkout.get('condition'):
                        continue
                    ref = checkout.get('arguments', {}).get('ref', '')
                    match = OUTPUT.fullmatch(ref)
                    if not match or match[1] != expected:
                        continue
                    for sink in steps:
                        if sink['index'] <= checkout['index'] or sink.get('type') != 'run' or sink.get('condition'):
                            continue
                        # A later checkout can replace the PR tree before execution.
                        if any(s.get('action') == 'actions/checkout' and checkout['index'] < s['index'] < sink['index'] for s in steps):
                            continue
                        command = sink.get('command', '')
                        if not LOCAL_EXEC.search(command) or sink.get('workingDirectory') not in ['.', './']:
                            continue
                        secrets = sorted({name for value in sink.get('env', {}).values()
                                          for name in SECRET.findall(value)})
                        if not secrets:
                            continue
                        if external_fork_excluded:
                            record = dict(file=file, job=job['id'],
                                          reason='PR head repository must equal the base repository')
                            if record not in fork_excluded_jobs:
                                fork_excluded_jobs.append(record)
                            continue
                        findings.append(dict(
                            property='PR-controlled checkout bytes cannot reach a local executable with configured secrets under pull_request_target',
                            status='potential-risk',
                            event='pull_request_target',
                            refSource=args['if_true'],
                            checkoutRef=ref,
                            localCommand=LOCAL_EXEC.search(command)[0].strip(),
                            configuredSecrets=secrets,
                            evidence=[evidence(file, job, selector, 'conditional-PR-merge-ref'),
                                      evidence(file, job, checkout, 'checkout-conditional-output'),
                                      evidence(file, job, sink, 'local-executable-with-secrets')],
                            assumptions=[
                                'The mutable external action tag resolved to the supplied action implementation at the run time.',
                                'The pull_request_target event reaches this job; any job-level repository condition is satisfied.',
                                'The pull request merge ref contains attacker-controlled bytes and checkout succeeds.',
                                'The local executable runs successfully and receives the configured secret environment.',
                                'No claim is made about exfiltration, actual secret value availability, or later lateral movement.'
                            ]))
    result = dict(status='analyzed' if findings else
                  'unsupported-environment-gate' if unsupported_jobs else
                  'fork-excluded' if fork_excluded_jobs else 'no-supported-path',
                  findings=findings, externalAction=external_name + '@' + external_version,
                  externalActionSourceSHA256=hashlib.sha256(source.read_bytes()).hexdigest())
    if unsupported_jobs:
        result['unsupportedJobs'] = unsupported_jobs
    if fork_excluded_jobs:
        result['forkExcludedJobs'] = fork_excluded_jobs
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('workflow_root', type=Path)
    p.add_argument('external_root', type=Path)
    p.add_argument('--external-name', required=True,
                   help='Repository path used by workflow uses:, without its @version')
    p.add_argument('--external-version', required=True,
                   help='Version string used by workflow uses:; runtime resolution remains an assumption')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = discover(args.workflow_root, args.external_root,
                      args.external_name, args.external_version)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
