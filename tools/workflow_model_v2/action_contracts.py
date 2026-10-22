"""Versioned identities for the bounded effects used by the backend."""
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ActionContract:
    identity: str
    action: str
    reference: str
    major: int
    runtime: str
    effect: str
    metadata: str


REFERENCES = {
    'actions/checkout': {'v4': 4, '11d5960a326750d5838078e36cf38b85af677262': 4},
    'actions/upload-artifact': {'v4': 4, 'ea165f8d65b6e75b540449e92b4886f43607fa02': 4},
    'actions/download-artifact': {'v4': 4, 'd3f86a106a0bac45b974a628896c90dbdf5c8093': 4},
    'actions/cache': {'v4': 4, 'v5.0.4': 5},
    'actions/cache/save': {'v4': 4}, 'actions/cache/restore': {'v4': 4},
}


def resolve(uses):
    action, _, reference = uses.rpartition('@')
    major = REFERENCES.get(action, {}).get(reference)
    if major is None:
        return None
    repo = '/'.join(action.split('/')[:2])
    suffix = '/'.join(action.split('/')[2:])
    metadata = f'https://raw.githubusercontent.com/{repo}/{reference}/' + (suffix + '/' if suffix else '') + 'action.yml'
    return ActionContract(f'{action}@{reference}:bounded-effects-v1', action, reference, major,
                          'node24' if major == 5 else 'node20',
                          # Shared dispatch name denotes only the common subset;
                          # identity, runtime, ref and metadata remain separate.
                          action + '-v4', metadata)
