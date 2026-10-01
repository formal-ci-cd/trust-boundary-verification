#!/usr/bin/env python3
"""Publish compact, lossless evidence from the isolated marimo evaluations."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return {'path': str(target), 'sha256': digest(target)}


def compress(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('wb') as stream:
        with gzip.GzipFile(fileobj=stream, filename='', mode='wb', mtime=0) as archive:
            archive.write(source.read_bytes())
    with gzip.open(target, 'rb') as archive:
        if archive.read() != source.read_bytes():
            raise RuntimeError('Archive is not lossless')
    return {'path': str(target), 'sha256': digest(target),
            'originalSHA256': digest(source)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baselines', type=Path, required=True)
    p.add_argument('--temporal', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        raise FileExistsError('Output directory must be new')
    baseline = json.loads((args.baselines / 'summary.json').read_text())
    temporal = json.loads((args.temporal / 'evaluation-native.json').read_text())
    if baseline['controlDifference'] != ['.github/workflows/marimo-bot.yml']:
        raise ValueError('Control must differ only at the workflow guard')
    original = temporal['variants'][0]
    control = temporal['variants'][1]
    if (original['variant'], original['bfs']['verdict'], original['nusmv']['verdict']) != (
        'original', 'unsafe', 'unsafe'
    ):
        raise ValueError('Original not independently verified unsafe')
    if (control['variant'], control['bfs']['verdict'], control['nusmv']['verdict']) != (
        'reject-equality-control', 'safe', 'safe'
    ):
        raise ValueError('Control not independently verified safe')
    if ([(r['accepted'], r['publisherStubConsumed']) for r in original['localReplay']]
        != [(True, 'APPROVED_HARMLESS_MARKER'),
            (True, 'UNAPPROVED_HARMLESS_MARKER'), (False, None)]):
        raise ValueError('Local replay does not exhibit the expected timestamp distinction')
    if temporal['removedSupportedPaths']:
        raise ValueError('Deleted workflow still has a supported path')
    for variant in ['vulnerable', 'reject-equality-control', 'removed']:
        row = baseline['variants'][variant]
        if row['codeql']['default']['targetWorkflow'] != 0:
            raise ValueError('Expected no CodeQL warning in target workflow')
        if row['actionlint'] != 0:
            raise ValueError('actionlint produced a warning')
    if baseline['variants']['vulnerable']['codeql'] != baseline['variants']['reject-equality-control']['codeql']:
        raise ValueError('CodeQL differs across one-token control')
    if baseline['variants']['vulnerable']['zizmor'] != baseline['variants']['reject-equality-control']['zizmor']:
        raise ValueError('zizmor differs across one-token control')
    args.output.mkdir(parents=True)
    records = []
    for name in ['summary.json']:
        records.append(copy(args.baselines / name, args.output / name))
    records.append(copy(args.temporal / 'evaluation-native.json', args.output / 'temporal/evaluation.json'))
    for variant in ['original', 'reject-equality-control']:
        for ext in ['.smv', '-nusmv.txt']:
            name = variant + ext
            records.append(copy(args.temporal / name, args.output / 'temporal' / name))
    for variant in ['vulnerable', 'reject-equality-control', 'removed']:
        src = args.baselines / variant
        dest = args.output / variant
        for name in ['default.sarif', 'security-and-quality.sarif',
                     'zizmor-regular.sarif', 'zizmor-auditor.sarif',
                     'zizmor-pedantic.sarif']:
            records.append(compress(src / name, dest / (name + '.gz')))
        records.append(copy(src / 'actionlint.json', dest / 'actionlint.json'))
        if variant != 'removed':
            records.append(copy(src / 'classification.csv', dest / 'classification.csv'))
    for record in records:
        record['path'] = str(Path(record['path']).relative_to(args.output))
    (args.output / 'evidence-index.json').write_text(json.dumps({
        'description': 'Full baseline SARIF compressed losslessly; no CodeQL databases or executables bundled.',
        'files': records}, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
