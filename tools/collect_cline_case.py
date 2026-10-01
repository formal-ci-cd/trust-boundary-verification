#!/usr/bin/env python3
"""Package complete Cline baseline outputs and model results without executables."""
import argparse
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import shutil


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def store(source, destination, compressed=False):
    destination.parent.mkdir(parents=True, exist_ok=True)
    if compressed:
        with destination.open('wb') as stream:
            with gzip.GzipFile(fileobj=stream, mode='wb', filename='', mtime=0) as archive:
                archive.write(source.read_bytes())
        with gzip.open(destination, 'rb') as stream:
            if stream.read() != source.read_bytes():
                raise ValueError('Gzip round trip failed')
    else:
        shutil.copyfile(source, destination)
    return {'path': destination.name, 'sha256': digest(destination),
            'sourceSHA256': digest(source)}


def sarif_summary(path):
    run = json.loads(path.read_text())['runs'][0]
    rows = run.get('results', [])
    locations = []
    for row in rows:
        place = row.get('locations', [{}])[0].get('physicalLocation', {})
        locations.append({'rule': row['ruleId'],
                          'file': place.get('artifactLocation', {}).get('uri'),
                          'line': place.get('region', {}).get('startLine')})
    return {'count': len(rows), 'rules': dict(sorted(Counter(r['ruleId'] for r in rows).items())),
            'locations': locations}


def zizmor_summary(path):
    rows = json.loads(path.read_text())
    locations = []
    for row in rows:
        place = row['locations'][0]
        locations.append({'rule': row['ident'],
                          'file': place['symbolic']['key']['Local']['verbatim_path']
                                        .replace('/source/', ''),
                          'line': place['concrete']['location']['start_point']['row'] + 1})
    return {'count': len(rows), 'rules': dict(sorted(Counter(r['ident'] for r in rows).items())),
            'locations': locations}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--opengrep-rule', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Output must be a new directory')
    analysis = json.loads((args.input / 'model/analysis.json').read_text())
    expected = [('pre-incident', 'possible'), ('mitigation', 'no-path-in-supported-model')]
    if [(c['variant'], c['bfs']['verdict']) for c in analysis['cases']] != expected:
        raise ValueError('Unexpected model verdict')
    for variant, verdict in expected:
        output = (args.input / 'model' / f'{variant}.nusmv.txt').read_text()
        expected_line = ('-- specification AG !bad  is false' if verdict == 'possible'
                         else '-- specification AG !bad  is true')
        if expected_line not in output:
            raise ValueError(f'NuSMV and BFS differ: {variant}')
    records, summary = [], {}
    args.output.mkdir(parents=True)
    for phase, variant in [('pre', 'pre-incident'), ('post', 'mitigation')]:
        summary[variant] = {}
        for label, name in [('codeqlDefault', f'codeql-full-{phase}-actions-code-scanning.qls.sarif'),
                            ('codeqlSecurityAndQuality',
                             f'codeql-full-{phase}-actions-security-and-quality.qls.sarif')]:
            path = args.input / name
            summary[variant][label] = sarif_summary(path)
            record = store(path, args.output / (name + '.gz'), True)
            record['path'] = name + '.gz'
            records.append(record)
        zizmor = args.input / f'zizmor-full-{phase}.json'
        summary[variant]['zizmorRegular'] = zizmor_summary(zizmor)
        record = store(zizmor, args.output / (zizmor.name + '.gz'), True)
        record['path'] = zizmor.name + '.gz'
        records.append(record)
        actionlint = args.input / f'actionlint-{phase}.jsonl'
        alerts = json.loads(actionlint.read_text())
        summary[variant]['actionlint'] = {
            'count': len(alerts), 'locations': [
                {'file': row['filepath'], 'line': row['line'], 'kind': row['kind']}
                for row in alerts]}
        record = store(actionlint, args.output / (actionlint.name + '.gz'), True)
        record['path'] = actionlint.name + '.gz'
        records.append(record)
        opengrep = args.input / f'opengrep-promptpwnd-{phase}.json'
        scan = json.loads(opengrep.read_text())
        summary[variant]['opengrepPromptPwnd'] = {
            'count': len(scan['results']),
            'locations': [{'rule': row['check_id'], 'file': row['path'],
                           'line': row['start']['line']} for row in scan['results']]}
        record = store(opengrep, args.output / (opengrep.name + '.gz'), True)
        record['path'] = opengrep.name + '.gz'
        records.append(record)
        for extension in ('.smv', '.nusmv.txt'):
            name = variant + extension
            record = store(args.input / 'model' / name, args.output / name)
            records.append(record)
    records.append(store(args.input / 'model/analysis.json', args.output / 'analysis.json'))
    records.append(store(args.opengrep_rule,
                         args.output / 'promptpwnd-rule-2025-12-03.yaml'))
    (args.output / 'evidence-index.json').write_text(json.dumps({
        'description': ('Full baseline outputs compressed losslessly. No CodeQL database, '
                        'executables, runtime cache contents, or attacker payloads included.'),
        'codeqlCLI': '2.27.1',
        'codeqlQueryPack': 'codeql/actions-queries@0.6.36',
        'zizmor': '1.30.1 --offline --no-config --no-ignores --persona=regular',
        'actionlint': '1.7.12',
        'opengrep': '1.30.0; AikidoSec rule at 12b001b4b1d65532b1a988b2f57a44468ad50445',
        'summary': summary,
        'files': records,
    }, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
