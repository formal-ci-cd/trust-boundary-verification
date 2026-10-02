#!/usr/bin/env python3
"""Join saved TanStack model schedules to independent public incident records.

The postmortem and job metadata are read only after model generation. A
chronological match is not proof of cache bytes, external Action SHA, or OIDC
execution; those claims retain their separate provenance.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path


def instant(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def verify(model, observation, jobs):
    cases = {case['variant']: case for case in model['cases']}
    if set(cases) != {'pre-incident', 'mitigation'}:
        raise ValueError('Missing modeled variant')
    reported = observation['reportedCache']
    identifiers = reported['restoredByReleaseRuns']
    if len(identifiers) != 2 or len(set(identifiers)) != 2:
        raise ValueError('Expected two distinct reported release runs')
    actual = {f"{run['runId']}/attempt-{run['attempt']}": run for run in jobs['runs']}
    if set(identifiers) != set(actual):
        raise ValueError('Public job IDs do not match postmortem record')
    saved = instant(reported['savedAtUTC'])
    checks = []
    for identifier in identifiers:
        run = actual[identifier]
        steps = {step['name']: step for step in run['job']['steps']}
        required = {'Setup Tools': 'success', 'Run Tests': 'failure',
                    'Publish Packages': 'skipped'}
        if any(steps.get(name, {}).get('conclusion') != conclusion
               for name, conclusion in required.items()):
            raise ValueError(f'Unexpected public job steps: {identifier}')
        setup = steps['Setup Tools']
        if not saved < instant(setup['started_at']):
            raise ValueError(f'Cache-save time is not before Setup Tools: {identifier}')
        checks.append({'run': identifier, 'reportedSaveBeforeSetup': True,
                       'setupStartedUTC': setup['started_at'],
                       'setupConclusion': setup['conclusion'],
                       'testsConclusion': steps['Run Tests']['conclusion'],
                       'declaredPublishConclusion': steps['Publish Packages']['conclusion'],
                       'githubRunURL': run['githubRunURL']})
    before = cases['pre-incident']['fixedFactSchedules']['saveBeforeBothRestores']
    after = cases['mitigation']['fixedFactSchedules']['saveBeforeBothRestores']
    if (before['bad1'], before['bad2']) != (True, True):
        raise ValueError('Pre-incident model does not reach both consumers')
    if (after['bad1'], after['bad2']) != (False, False):
        raise ValueError('Mitigation model still reaches a consumer')
    return {
        'status': 'chronology-consistent-partial',
        'reportedCacheSaveUTC': reported['savedAtUTC'],
        'reportedCacheKey': reported['key'],
        'sourceType': 'Maintainer postmortem plus public GitHub job-step metadata',
        'checks': checks,
        'modelResult': {'preIncident': [before['bad1'], before['bad2']],
                        'mitigation': [after['bad1'], after['bad2']]},
        'limits': [
            'The cache-save time and restored cache identity are from the maintainer postmortem, not independently recovered runner logs.',
            'Successful Setup Tools steps do not independently reveal the cache key, entry bytes, or external Action runtime SHA.',
            'Skipped declared Publish Packages steps do not disprove the postmortem\'s separate direct OIDC publication mechanism.',
            'The model keeps key equality, restore success, and malicious code execution as independent unknown facts.',
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('model', type=Path)
    parser.add_argument('observation', type=Path)
    parser.add_argument('jobs', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    result = verify(json.loads(args.model.read_text()),
                    json.loads(args.observation.read_text()),
                    json.loads(args.jobs.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
