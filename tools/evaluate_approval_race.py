#!/usr/bin/env python3
"""Evaluate original and controlled approval guards without upstream execution."""
import argparse
import copy
import hashlib
import json
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import approval_race_analysis as race
import evaluate_public_cases


def harmless_replay(candidate):
    """Run original PR-info script with gh/jq stubs, then copy harmless bytes.

    The local build and artifact/publish stages are controlled fixtures, not
    original GitHub jobs. No repository build hooks or package scripts run.
    """
    import os
    rows = []
    for label, sha, stamp in [('old-commit', 'a'*40, '2026-09-04T00:00:00Z'),
                              ('updated-same-second', 'b'*40, '2026-09-04T00:00:01Z'),
                              ('updated-next-second', 'b'*40, '2026-09-04T00:00:02Z')]:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            bin_dir = root / 'bin'
            bin_dir.mkdir()
            api = root / 'api.json'
            api.write_text(json.dumps({'head': {'sha': sha, 'repo': {'pushed_at': stamp}}}))
            gh = bin_dir / 'gh'
            gh.write_text('#!/bin/sh\ncat "$LOCAL_PR_RESPONSE"\n')
            jq = bin_dir / 'jq'
            jq.write_text('''#!/usr/bin/env python3
import json,sys
data=json.load(sys.stdin)
if sys.argv[1:] == ['-r','.head.sha']: print(data['head']['sha'])
elif sys.argv[1:] == ['-r','.head.repo.pushed_at']: print(data['head']['repo']['pushed_at'])
else: raise SystemExit(90)
''')
            gh.chmod(0o755)
            jq.chmod(0o755)
            output = root / 'output'
            env = dict(os.environ, PATH=str(bin_dir)+':'+os.environ['PATH'],
                       LOCAL_PR_RESPONSE=str(api), GH_REPO='local/demo', PR_NUMBER='1',
                       GH_TOKEN='dummy-no-credential', COMMENT_AT='2026-09-04T00:00:01Z',
                       GITHUB_OUTPUT=str(output))
            run = subprocess.run(['bash', '-e', '-o', 'pipefail', '-c', candidate['sourceScript']],
                                 env=env, cwd=root, capture_output=True, text=True)
            if run.returncode not in [0, 1]:
                raise RuntimeError(run.stderr)
            accepted = run.returncode == 0
            selected = output.read_text().strip().split('=', 1)[1] if accepted else None
            consumed = None
            if accepted:
                build = root / 'build'
                build.mkdir()
                marker = b'UNAPPROVED_HARMLESS_MARKER' if selected == 'b'*40 else b'APPROVED_HARMLESS_MARKER'
                (build / 'marker.txt').write_bytes(marker)
                # Immutable artifact copy and downstream download; no rewrite race
                # is assumed. The bad bytes originate in the selected PR revision.
                shutil.copytree(build, root / 'artifact')
                shutil.copytree(root / 'artifact', root / 'consumer')
                consumed = (root / 'consumer/marker.txt').read_bytes().decode()
            rows.append(dict(case=label, accepted=accepted, selectedSHA=selected,
                             publisherStubConsumed=consumed,
                             sourceScriptSHA256=hashlib.sha256(candidate['sourceScript'].encode()).hexdigest(),
                             originalStage='Get PR Info run block',
                             substitutedStages=['GitHub API', 'jq field reader', 'checkout/build', 'artifact transfer', 'publish']))
    return rows


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('case', type=Path)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--nusmv', type=Path)
    p.add_argument('--replay', action='store_true', help='Run only in offline Linux container')
    p.add_argument('--control-dir', type=Path, help='Save the generated one-token control source')
    args = p.parse_args()
    manifest = evaluate_public_cases.verify_manifest(args.case)
    args.output.mkdir(parents=True, exist_ok=True)
    candidates = race.discover(args.case / 'vulnerable')
    if len(candidates) != 1:
        raise ValueError('Expected one supported candidate in pinned case')
    original = candidates[0]
    # The control changes the extracted guard only; all other YAML bytes remain
    # identical. It is a research intervention, not the upstream deletion fix.
    with tempfile.TemporaryDirectory() as temp:
        control_root = Path(temp)
        shutil.copytree(args.case / 'vulnerable', control_root, dirs_exist_ok=True)
        file = control_root / original['evidence'][0]['file']
        source = file.read_text()
        needle = ' -gt $(date -d "$COMMENT_AT" +%s)'
        if source.count(needle) != 1:
            raise ValueError('Control must change exactly one comparison')
        file.write_text(source.replace(needle, ' -ge $(date -d "$COMMENT_AT" +%s)'))
        controls = race.discover(control_root)
        if len(controls) != 1:
            raise ValueError('Control path not extracted')
        if args.control_dir:
            if args.control_dir.exists():
                raise FileExistsError('Control directory must be new')
            shutil.copytree(control_root, args.control_dir)
    results = []
    for label, candidate in [('original', original), ('reject-equality-control', controls[0])]:
        enabled = any(s['configuredOIDC'] and not s['environment'] for s in candidate['sinks'])
        bfs = race.explore(candidate['rejectionOperator'], enabled)
        model = args.output / (label + '.smv')
        model.write_text(race.render(candidate['rejectionOperator'], enabled))
        result = dict(variant=label, automatic=candidate, bfs=bfs)
        if args.nusmv:
            completed = subprocess.run([str(args.nusmv), str(model)], capture_output=True, text=True, check=True)
            (args.output / (label + '-nusmv.txt')).write_text(completed.stdout)
            if re.search(r'-- specification AG !bad\s+is false', completed.stdout):
                verdict = 'unsafe'
            elif re.search(r'-- specification AG !bad\s+is true', completed.stdout):
                verdict = 'safe'
            else:
                raise RuntimeError('Missing NuSMV verdict')
            if verdict != bfs['verdict']:
                raise RuntimeError('Independent BFS and NuSMV disagree')
            result['nusmvVerdict'] = verdict
        if args.replay:
            result['localReplay'] = harmless_replay(candidate)
        results.append(result)
    summary = dict(
        case=manifest['repository'], confirmedExploitation=manifest['confirmedExploitation'],
        property='Unapproved PR revision after an authorizing comment must not supply content to a publish step configured with id-token: write and no environment.',
        original=manifest['files'][0]['commit'],
        intervention='Strict -gt changed to -ge in one guard; upstream instead removed marimo-bot.yml.',
        controlScriptSHA256=hashlib.sha256(controls[0]['sourceScript'].encode()).hexdigest(),
        removedSupportedPaths=race.discover(args.case / 'removed'),
        variants=results)
    (args.output / ('evaluation-native.json' if args.nusmv else 'evaluation-replay.json')).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
