#!/usr/bin/env python3
"""Keep compact, lossless evidence for the two TanStack source variants."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import shutil


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sarif_rows(path):
    run = json.loads(path.read_text())['runs'][0]
    return run.get('results', []), run['tool']['driver']


def location(result):
    physical = result.get('locations', [{}])[0].get('physicalLocation', {})
    return physical.get('artifactLocation', {}).get('uri', '')


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return {'path': target.as_posix(), 'sha256': sha256(target)}


def compress(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('wb') as stream:
        with gzip.GzipFile(fileobj=stream, mode='wb', filename='', mtime=0) as archive:
            archive.write(source.read_bytes())
    with gzip.open(target, 'rb') as stream:
        if stream.read() != source.read_bytes():
            raise RuntimeError('Compressed SARIF did not round-trip')
    return {'path': target.as_posix(), 'sha256': sha256(target),
            'originalSHA256': sha256(source)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--analysis', type=Path, required=True)
    p.add_argument('--pre-baselines', type=Path, required=True)
    p.add_argument('--fixed-baselines', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        raise FileExistsError('Output directory must be new')
    analysis = json.loads((args.analysis / 'analysis-native.json').read_text())
    expected = [('pre-incident', 'possible'),
                ('mitigation', 'no-path-in-supported-model')]
    if [(c['variant'], c['bfs']['verdict']) for c in analysis['cases']] != expected:
        raise ValueError('Unexpected model result')
    if any(c['bfs']['verdict'] != c['nusmv']['verdict'] for c in analysis['cases']):
        raise ValueError('NuSMV and BFS differ')
    args.output.mkdir(parents=True)
    records = []
    summary = {}
    for case, baseline_dir in [('pre-incident', args.pre_baselines),
                               ('mitigation', args.fixed_baselines)]:
        summary[case] = {}
        for tool, name in [('codeql-default', 'default.sarif'),
                           ('codeql-security-and-quality', 'security-and-quality.sarif'),
                           ('zizmor-regular', 'zizmor-regular.sarif')]:
            source = baseline_dir / name
            rows, driver = sarif_rows(source)
            dest = args.output / case / (tool + '.sarif.gz')
            records.append(compress(source, dest))
            bundle = [r for r in rows if location(r).endswith('/bundle-size.yml')]
            summary[case][tool] = {
                'version': driver.get('semanticVersion', driver.get('version')),
                'totalAlerts': len(rows),
                'rules': dict(sorted(Counter(r['ruleId'] for r in rows).items())),
                'bundleSizeAlerts': [
                    {'rule': r['ruleId'],
                     'line': r.get('locations', [{}])[0].get('physicalLocation', {})
                              .get('region', {}).get('startLine'),
                     'message': r.get('message', {}).get('text', '')}
                    for r in bundle],
            }
        for extension in ('.smv', '.nusmv.txt'):
            source = args.analysis / (case + extension)
            records.append(copy(source, args.output / (case + extension)))
    records.append(copy(args.analysis / 'analysis-native.json', args.output / 'analysis.json'))
    for record in records:
        record['path'] = str(Path(record['path']).relative_to(args.output))
    (args.output / 'evidence-index.json').write_text(json.dumps({
        'description': ('Full CodeQL and zizmor SARIF are compressed losslessly. '
                        'No executable, database, cache content or attacker payload is included.'),
        'codeqlCLI': '2.27.1',
        'codeqlQueryPack': 'codeql/actions-queries@0.6.36',
        'zizmor': '1.30.1 --offline --no-config --no-ignores --persona=regular',
        'baselineSummary': summary,
        'files': records}, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
