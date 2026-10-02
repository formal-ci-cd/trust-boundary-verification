#!/usr/bin/env python3
"""Join public PR file metadata to a detected local-executable path.

This checks the attacker's changed filename, not payload behavior or CI runtime.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import conditional_checkout_chain


def verify(case, analysis):
    manifest = json.loads((case / 'manifest.json').read_text())
    pr = json.loads((case / manifest['attackPR']['metadataFile']).read_text())
    for source in manifest['files']:
        digest = hashlib.sha256((case / source['localPath']).read_bytes()).hexdigest()
        if digest != source['sha256']:
            raise ValueError('Pinned source digest mismatch: ' + source['localPath'])
    workflow = next(f for f in manifest['files']
                    if f['repository'] == 'spotbugs/sonar-findbugs'
                    and f['path'] == '.github/workflows/sonarqube.yml')
    actual_analysis = conditional_checkout_chain.discover(
        case, case / 'external-action-cond', 'haya14busa/action-cond',
        manifest['externalActionTag'])
    if analysis != actual_analysis:
        raise ValueError('Supplied analysis does not match pinned source')
    if pr['base']['sha'] != workflow['commit']:
        raise ValueError('Attack PR base is not the pinned workflow revision')
    if not re.fullmatch(r'[0-9a-f]{40}', pr['head']['sha']):
        raise ValueError('Invalid PR head commit')
    if pr['number'] != manifest['attackPR']['number'] or pr['state'] != 'closed':
        raise ValueError('Unexpected attack PR identity')
    joined = []
    for finding in analysis.get('findings', []):
        if finding.get('status') != 'potential-risk':
            continue
        command = finding.get('localCommand', '')
        if not re.fullmatch(r'\./[\w./-]+', command):
            continue
        changed = next((f for f in pr['changedFiles']
                        if f['filename'] == command[2:]
                        and f['status'] == 'modified'
                        and re.fullmatch(r'[0-9a-f]{40}', f['sha'])), None)
        if changed:
            joined.append(dict(prNumber=pr['number'], prHead=pr['head']['sha'],
                               changedFile=changed['filename'], changedBlob=changed['sha'],
                               workflowCommit=workflow['commit'],
                               sinkCommand=command,
                               sinkEvidence=finding['evidence'][-1]))
    return dict(status='aligned' if joined else 'no-attack-file-alignment',
                alignment=joined,
                interpretation=('The attack PR changed the exact local executable '
                                'on the detected conditional path. This does not '
                                'prove the external Action tag target, CI execution, '
                                'secret availability, or exfiltration.'))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('case', type=Path)
    p.add_argument('analysis', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = verify(args.case, json.loads(args.analysis.read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
