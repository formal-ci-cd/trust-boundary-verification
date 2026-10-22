"""Versioned event/cache policies. No reachability or final verdict here."""
from __future__ import annotations

import re


class UnsupportedPolicy(ValueError):
    pass


EVENTS = {'pull_request', 'pull_request_target', 'push', 'workflow_run',
          'workflow_dispatch', 'repository_dispatch', 'workflow_call', 'schedule'}
POLICIES = {'github-historical-2025', 'github-current-2026-10-22'}


def glob(pattern, text):
    """GitHub's *, **, ? subset, including zero directories for **/."""
    if not isinstance(pattern, str) or any(c in pattern for c in '[]+\\'):
        raise UnsupportedPolicy('glob supports literal, *, ** and ? only: ' + repr(pattern))
    pieces, i = [], 0
    while i < len(pattern):
        if pattern[i:i + 3] == '**/':
            pieces.append('(?:.*/)?')
            i += 3
        elif pattern[i:i + 2] == '**':
            pieces.append('.*')
            i += 2
        elif pattern[i] == '*':
            pieces.append('[^/]*')
            i += 1
        elif pattern[i] == '?':
            pieces.append('[^/]')
            i += 1
        else:
            pieces.append(re.escape(pattern[i]))
            i += 1
    return re.fullmatch(''.join(pieces), text) is not None


def ordered_patterns(patterns, text):
    if isinstance(patterns, str) or not isinstance(patterns, list):
        raise UnsupportedPolicy('filter patterns must be a list')
    if not any(isinstance(p, str) and not p.startswith('!') for p in patterns):
        raise UnsupportedPolicy('negative-only or empty include filter')
    matched = False
    for pattern in patterns:
        negative = pattern.startswith('!')
        if glob(pattern[1:] if negative else pattern, text):
            matched = not negative
    return matched


def filter_field(config, field, value):
    positive, negative = field, field + '-ignore'
    if positive in config and negative in config:
        raise UnsupportedPolicy(f'{positive} and {negative} cannot coexist')
    if positive in config:
        return ordered_patterns(config[positive], value)
    if negative in config:
        patterns = config[negative]
        if not isinstance(patterns, list) or any(p.startswith('!') for p in patterns):
            raise UnsupportedPolicy('ignore filter requires non-negated pattern list')
        return not any(glob(p, value) for p in patterns)
    return True


def trigger(events, context):
    event = context['event']
    if event not in EVENTS:
        raise UnsupportedPolicy('unmodeled trigger event ' + event)
    if event not in events:
        return False
    config = events[event] or {}
    if event == 'schedule':
        if not isinstance(config, list) or not all(isinstance(c, dict) and 'cron' in c for c in config):
            raise UnsupportedPolicy('invalid schedule')
        # The scenario asserts a scheduled delivery, not cron/time simulation.
        return context['defaultBranchWorkflow']
    if not isinstance(config, dict):
        raise UnsupportedPolicy('event configuration must be mapping')
    valid_filters = {
        'pull_request': {'types', 'branches', 'branches-ignore', 'paths', 'paths-ignore'},
        'pull_request_target': {'types', 'branches', 'branches-ignore', 'paths', 'paths-ignore'},
        'push': {'branches', 'branches-ignore', 'tags', 'tags-ignore', 'paths', 'paths-ignore'},
        'workflow_run': {'workflows', 'types', 'branches', 'branches-ignore'},
        'workflow_dispatch': {'inputs'}, 'repository_dispatch': {'types'},
        'workflow_call': {'inputs', 'outputs', 'secrets'},
    }[event]
    if set(config) - valid_filters:
        raise UnsupportedPolicy('unsupported event keys: ' + ', '.join(sorted(set(config) - valid_filters)))
    defaults = {'pull_request': ['opened', 'synchronize', 'reopened'],
                'pull_request_target': ['opened', 'synchronize', 'reopened'],
                'workflow_run': ['requested', 'in_progress', 'completed']}
    types = config.get('types', defaults.get(event))
    if types is not None and not isinstance(types, list):
        raise UnsupportedPolicy('event types must be a list')
    if types is not None and context['activity'] not in types:
        return False
    if event in {'pull_request', 'pull_request_target', 'push', 'workflow_run'}:
        is_tag = event == 'push' and context['refType'] == 'tag'
        field = 'tags' if is_tag else 'branches'
        opposite = 'branches' if is_tag else 'tags'
        if event == 'push' and any(k in config for k in (opposite, opposite + '-ignore')) and not any(k in config for k in (field, field + '-ignore')):
            return False
        if not filter_field(config, field, context['branch']):
            return False
    if event in {'push', 'pull_request', 'pull_request_target'} and not (event == 'push' and context['refType'] == 'tag'):
        if 'paths' in config and 'paths-ignore' in config:
            raise UnsupportedPolicy('paths and paths-ignore cannot coexist')
        if any(k in config for k in ('paths', 'paths-ignore')):
            if context.get('diffComplete') is not True:
                raise UnsupportedPolicy('path filters need a complete bounded diff; GitHub diff limits/timeouts are not guessed')
            files = context['changedPaths']
            if 'paths' in config and not any(ordered_patterns(config['paths'], f) for f in files):
                return False
            if 'paths-ignore' in config and not any(filter_field(config, 'paths', f) for f in files):
                return False
    if event in {'workflow_dispatch', 'repository_dispatch', 'workflow_run'} and not context['defaultBranchWorkflow']:
        return False
    if event == 'workflow_run' and context['upstreamName'] not in config.get('workflows', []):
        return False
    return True


def cache_visible(writer, reader, policy):
    if policy not in POLICIES:
        raise UnsupportedPolicy('cache policy must be explicitly selected')
    if writer['repository'] != reader['repository']:
        return False
    if writer['scope'] == reader['scope']:
        return True
    visible = {reader['defaultBranch']}
    if reader['event'] in {'pull_request', 'pull_request_target'}:
        visible.add(reader['baseBranch'])
    return writer['scope'] in visible


def cache_writable(context, policy, mode=None):
    if policy not in POLICIES:
        raise UnsupportedPolicy('cache policy must be explicitly selected')
    if policy == 'github-historical-2025':
        if mode is not None:
            raise UnsupportedPolicy('cache-mode is not part of historical policy')
        return True
    # Current cache-mode is policy-dependent: require the effective mode from
    # the scenario rather than guessing repository/org defaults.
    if mode not in {'read', 'write', 'write-only', 'none'}:
        raise UnsupportedPolicy('current policy requires explicit effective cache-mode')
    return mode in {'write', 'write-only'}


def cache_rank(saved, requested, restore_keys):
    """Ordered key search; scope precedence is applied by the caller."""
    if any(not value.isascii() for value in [saved, requested, *restore_keys]):
        raise UnsupportedPolicy('locale-sensitive non-ASCII cache keys require a service matching contract')
    if (saved.lower() == requested.lower() and saved != requested) or any(
            saved.lower().startswith(prefix.lower()) and not saved.startswith(prefix)
            for prefix in [requested, *restore_keys]):
        raise UnsupportedPolicy('case-only cache matching is unspecified here; action exact-hit uses locale comparison')
    if saved == requested:
        return 0
    if saved.startswith(requested):
        return 1
    for index, prefix in enumerate(restore_keys):
        if saved.startswith(prefix):
            return 2 + index
    return None


def permission(raw_workflow, raw_job, context, scope):
    """None means policy-dependent. Fork downgrade is an explicit assumption."""
    declaration = raw_job.get('permissions', raw_workflow.get('permissions'))
    if declaration is None:
        value = context.get('defaultPermissions', {}).get(scope)
    elif declaration in ('read-all', 'write-all'):
        value = declaration.split('-')[0]
        if scope == 'id-token' and value == 'read':
            value = 'none'
    elif isinstance(declaration, dict):
        value = declaration.get(scope, 'none')
    else:
        raise UnsupportedPolicy('invalid permissions declaration')
    if value not in {None, 'none', 'read', 'write'} or (scope == 'id-token' and value == 'read'):
        raise UnsupportedPolicy('invalid permission level')
    if context['event'] == 'pull_request' and context['fork'] and value == 'write':
        if context.get('sendWriteTokens') is False:
            value = 'read' if scope != 'id-token' else 'none'
        elif context.get('sendWriteTokens') is not True:
            value = None
    return value
