"""Expand only explicitly supplied, content-hash-checked composite metadata."""
from pathlib import Path
import copy
import hashlib
import yaml
import workflow_to_smv as base
from yaml_to_model import WorkflowLoader


def expand(steps, node, file, registry, diagnostic, stack=()):
    result = []
    for index, raw in enumerate(steps):
        span = base._span(node.value[index] if node else None, file).json()
        ref = raw.get('uses') if isinstance(raw, dict) else None
        if ref not in registry:
            result.append((raw, span))
            continue
        if ref in stack or len(stack) >= 8:
            raise base.WorkflowError('composite recursion/depth limit: ' + ref)
        entry = registry[ref]
        path = Path(entry['path'])
        source = path.read_bytes()
        if hashlib.sha256(source).hexdigest() != entry['sha256']:
            raise base.WorkflowError('composite metadata hash mismatch: ' + ref)
        metadata = yaml.load(source, Loader=WorkflowLoader)
        if metadata.get('runs', {}).get('using') != 'composite':
            raise base.WorkflowError('registered metadata is not a composite: ' + ref)
        if metadata.get('outputs') or metadata.get('inputs') or raw.get('with') or raw.get('continue-on-error') or raw.get('if') is not None:
            diagnostic('composite-boundary', 'composite inputs/outputs/parent guards/continue-on-error require a nested call frame', span)
        tree = yaml.compose(source, Loader=WorkflowLoader)
        snode = base._member(base._member(tree, 'runs'), 'steps')
        children = expand(metadata['runs']['steps'], snode, str(path), registry, diagnostic, stack + (ref,))
        prefix = raw.get('id', 'composite' + str(index)) + '__'
        for child, child_span in children:
            child = copy.deepcopy(child)
            if child.get('id'):
                diagnostic('composite-step-reference', 'internal step id/output scoping is not flattened silently', child_span)
            child['id'] = prefix + str(child.get('id', len(result)))
            child['env'] = {**raw.get('env', {}), **child.get('env', {})}
            result.append((child, child_span))
    return result
