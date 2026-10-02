#!/usr/bin/env python3
"""Join a public attack PR diff to a detected local-executable path.

The diff is untrusted input. It is inspected as text and never executed;
remote script contents and CI runtime are outside this check.
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
    files_path = case / manifest['attackPR']['filesFile']
    if hashlib.sha256(files_path.read_bytes()).hexdigest() != manifest['attackPR']['filesSha256']:
        raise ValueError('Pinned attack PR files digest mismatch')
    pr_files = json.loads(files_path.read_text())
    if len(pr_files) != 1 or len(pr['changedFiles']) != 1:
        raise ValueError('Expected exactly one attack PR changed file')
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
            diff = pr_files[0]
            if (diff['filename'] != changed['filename'] or diff['sha'] != changed['sha']
                    or diff['status'] != 'modified'):
                raise ValueError('Attack PR diff does not match file metadata')
            patch = diff.get('patch', '')
            added = [line[1:] for line in patch.splitlines()
                     if line.startswith('+') and not line.startswith('+++')]
            piped_shell = [line for line in added if re.fullmatch(
                r'curl\s+-sSfL\s+https://gist\.githubusercontent\.com/\S+\s+\|\s+bash\s*>\s*/dev/null\s+2>&1',
                line)]
            if len(piped_shell) != 1 or patch.index('+' + piped_shell[0]) > patch.index(' if [ -z "$MAVEN_SKIP_RC" ]; then'):
                raise ValueError('No matching added command before the Maven launcher body')
            joined.append(dict(prNumber=pr['number'], prHead=pr['head']['sha'],
                               changedFile=changed['filename'], changedBlob=changed['sha'],
                               workflowCommit=workflow['commit'],
                               sinkCommand=command,
                               sinkEvidence=finding['evidence'][-1],
                               prDiffSha256=manifest['attackPR']['filesSha256'],
                               addedUnconditionalRemoteShellBeforeMaven=True,
                               remoteHost='gist.githubusercontent.com'))
    return dict(status='aligned' if joined else 'no-attack-file-alignment',
                alignment=joined,
                interpretation=('The attack PR added a remote shell download piped to bash '
                                'near the start of the exact local executable on the detected '
                                'conditional path. Remote contents were not fetched. This does '
                                'not prove the external Action tag target, CI execution, secret '
                                'availability, or exfiltration.'))


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
