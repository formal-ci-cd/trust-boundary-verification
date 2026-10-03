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
        ast = yaml.compose(source.read_text(), Loader=yaml_to_model.WorkflowLoader)
        def child(node, key):
            return next((v for k, v in node.value if k.value == key), None)
        step_nodes = child(child(ast, 'runs'), 'steps').value
        contracts[reference] = {'body': body, 'path': str(source), 'sha256': actual,
                                'stepLines': [node.start_mark.line + 1 for node in step_nodes],
                                'runtimeIdentity': 'unknown; snapshot is not proof of runtime tag resolution'}
    return contracts


def expand_reference(reference, arguments, origin, contracts, unsupported, stack=()):
    """Expand known contracts and exact supplied composite references only."""
    action = split_action(reference)
    direct = contract_operations(action, arguments, origin,
                                 'known-action-contract' if not stack else
                                 'supplied-composite-snapshot; runtime identity unknown')
    if direct:
        return direct
    contract = contracts.get(reference)
    if contract is None:
        if action != 'actions/checkout':
            unsupported.append({'location': origin, 'action': reference,
                                'reason': 'no supplied composite contract or known threat-model action contract'})
        return []
    if reference in stack or len(stack) >= 8:
        unsupported.append({'location': origin, 'action': reference,
                            'reason': 'composite recursion cycle or depth limit'})
        return []
    operations = []
    for index, inner in enumerate(contract['body']['runs']['steps']):
        inner_reference = str(inner.get('uses', ''))
        inner_loc = dict(origin,
                         compositeFile=contract['path'], compositeStep=index,
                         compositeLine=contract['stepLines'][index],
                         compositeSHA256=contract['sha256'],
                         compositeStack=list(stack + (reference,)))
        if not inner_reference:
            # Shell inside a composite is a separate semantic boundary. The
            # cache inventory records operations only; it does not infer use.
            continue
        inner_args = {k: str(v) for k, v in inner.get('with', {}).items()}
        operations.extend(expand_reference(inner_reference, inner_args, inner_loc,
                                           contracts, unsupported, stack + (reference,)))
    return operations


def extract(models, contracts):
    operations, unsupported = [], []
    for model in models.values():
        for job in model['workflow']['jobs']:
            for step in job['steps']:
                if step['type'] != 'uses':
                    continue
                reference = step.get('action', '') + '@' + step.get('version', '')
                operations.extend(expand_reference(reference, step.get('arguments', {}),
                                                   source_location(model, job, step),
                                                   contracts, unsupported))
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
