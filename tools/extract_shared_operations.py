#!/usr/bin/env python3
"""Inventory cache/artifact contracts from workflow YAML and supplied composite snapshots."""
import argparse
import hashlib
import json
from pathlib import Path

import yaml
import yaml_to_model

CACHE_ACTIONS = {'actions/cache': ('cache.read', 'cache.write'),
                 'actions/cache/save': ('cache.write',),
                 'actions/cache/restore': ('cache.read',)}
ARTIFACT_ACTIONS = {'actions/upload-artifact': ('artifact.upload',),
                    'actions/download-artifact': ('artifact.download',)}


def split_action(reference):
    return reference.rsplit('@', 1)[0] if '@' in reference else reference


def source_location(model, job, step):
    return {'workflow': model['workflow']['file'], 'job': job['id'],
            'step': step['index'], 'line': step['line']}


def contract_operations(action, arguments, origin, resolution):
    kinds = CACHE_ACTIONS.get(action, ()) + ARTIFACT_ACTIONS.get(action, ())
    return [dict(kind=kind, action=action, arguments={
                key: arguments.get(key) for key in ('key', 'restore-keys', 'path', 'name', 'run-id')},
                location=origin, resolution=resolution,
                runtimeSuccess='unknown') for kind in kinds]


def load_contracts(path):
    if path is None:
        return {}
    mapping = json.loads(path.read_text())
    contracts = {}
    for reference, spec in mapping.items():
        source = Path(spec['path'])
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
        if actual != spec['sha256']:
            raise ValueError(f'Composite snapshot checksum mismatch: {source}')
        body = yaml.load(source.read_text(), Loader=yaml_to_model.WorkflowLoader)
        if body.get('runs', {}).get('using') != 'composite':
            raise ValueError(f'Not a composite Action: {source}')
        contracts[reference] = {'body': body, 'path': str(source), 'sha256': actual,
                                'runtimeIdentity': 'unknown; snapshot is not proof of runtime tag resolution'}
    return contracts


def extract(models, contracts):
    operations, unsupported = [], []
    for model in models.values():
        for job in model['workflow']['jobs']:
            for step in job['steps']:
                if step['type'] != 'uses':
                    continue
                reference = step.get('actionReference', '') or step.get('command', '')
                # The common model normalizes the action name separately.
                action = step.get('action', '')
                loc = source_location(model, job, step)
                args = step.get('arguments', {})
                direct = contract_operations(action, args, loc, 'known-action-contract')
                if direct:
                    operations.extend(direct)
                    continue
                raw_uses = step.get('uses', '') or step.get('actionRaw', '')
                # Resolve by exact workflow reference, never by an action name alone.
                if not raw_uses:
                    raw_uses = action + '@' + step.get('version', '')
                contract = contracts.get(raw_uses)
                if not contract:
                    if action not in {'actions/checkout'}:
                        unsupported.append({'location': loc, 'action': raw_uses,
                                            'reason': 'no supplied composite contract; unrelated actions may be outside the threat model'})
                    continue
                for index, inner in enumerate(contract['body']['runs']['steps']):
                    inner_action = split_action(str(inner.get('uses', '')))
                    inner_loc = dict(loc, compositeFile=contract['path'], compositeStep=index,
                                     compositeSHA256=contract['sha256'])
                    inner_args = {k: str(v) for k,v in inner.get('with', {}).items()}
                    operations.extend(contract_operations(inner_action, inner_args, inner_loc,
                                                          'supplied-composite-snapshot; runtime identity ' + contract['runtimeIdentity']))
    return {'operations': operations, 'unsupportedActions': unsupported}


def pair_cache(operations):
    writes = [o for o in operations if o['kind'] == 'cache.write']
    reads = [o for o in operations if o['kind'] == 'cache.read']
    pairs = []
    for write in writes:
        for read in reads:
            wk, rk = write['arguments'].get('key'), read['arguments'].get('key')
            if not wk or not rk:
                compatibility = 'unknown'
            elif wk == rk:
                compatibility = 'expression-equal; effective key unknown'
            elif '${{' not in wk and '${{' not in rk and not read['arguments'].get('restore-keys'):
                compatibility = 'literal-key-different'
            else:
                compatibility = 'unknown; restore-key or expression may resolve to same entry'
            pairs.append({'producer': write['location'], 'consumer': read['location'],
                          'writeKeyExpression': wk, 'readKeyExpression': rk,
                          'restoreKeysExpression': read['arguments'].get('restore-keys'),
                          'keyCompatibility': compatibility,
                          'sameObject': 'unknown', 'saveRestoreOrder': 'unknown',
                          'cacheScope': 'unknown', 'cacheVersion': 'unknown',
                          'candidateEntry': 'unknown', 'distinctRun': 'unknown',
                          'sameCallSite': all(write['location'][key] == read['location'][key]
                                              for key in ('workflow', 'job', 'step')),
                          'reason': 'matching key expression across workflow files is only a candidate'})
    return pairs


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('root', type=Path)
    p.add_argument('--composites', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    models = yaml_to_model.load_models(args.root)
    contracts = load_contracts(args.composites)
    result = extract(models, contracts)
    result['cacheCandidates'] = pair_cache(result['operations'])
    result['workflowCount'] = len(models)
    result['emptyCandidateMeaning'] = 'unknown/unsupported; not safe'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(f"{len(result['operations'])} operations, {len(result['cacheCandidates'])} cache candidate pairs")


if __name__ == '__main__':
    main()
