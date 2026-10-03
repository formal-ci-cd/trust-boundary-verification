#!/usr/bin/env python3
"""Check generated cache-chain models with NuSMV and independent BFS results."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory', type=Path)
    p.add_argument('--nusmv', type=Path, required=True)
    args = p.parse_args()
    report = json.loads((args.directory / 'analysis.json').read_text())
    for row in report['cases']:
        name = row['repositoryVariant'] + '__' + row['actionVariant']
        model = args.directory / (name + '.smv')
        run = subprocess.run([str(args.nusmv), str(model)], capture_output=True, text=True, check=True)
        (args.directory / (name + '.nusmv.txt')).write_text(
            '\n'.join(line.rstrip() for line in run.stdout.splitlines()) + '\n')
        match = re.search(r'-- specification AG !bad\s+is (true|false)', run.stdout)
        if not match:
            raise RuntimeError('Missing NuSMV verdict for ' + name)
        verdict = 'possible' if match[1] == 'false' else 'no-path-in-supported-model'
        if verdict != row['bfs']['verdict']:
            raise RuntimeError('NuSMV/BFS disagreement for ' + name)
        row['nusmv'] = {'verdict': verdict,
                        'modelSHA256': hashlib.sha256(model.read_bytes()).hexdigest()}
    (args.directory / 'analysis-native.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print('NuSMV/BFS agree for all four source combinations')


if __name__ == '__main__':
    main()
