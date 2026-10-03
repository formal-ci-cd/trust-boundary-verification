#!/usr/bin/env python3
"""Verify the two-consumer TanStack models with NuSMV and independent BFS."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

import tanstack_two_consumers as multi


SPECIFICATION = re.compile(r'-- specification AG !\(bad1 \| bad2\)\s+is (true|false)')


def verify(directory, nusmv):
    report = json.loads((directory / 'analysis.json').read_text())
    for case in report['cases']:
        variant = case['variant']
        model = directory / case['model']
        if model.read_text() != multi.render(case['cacheScopeCompatible'], case['sourceEvidence']):
            raise ValueError(f'Model no longer matches extracted evidence: {variant}')
        if case['bfs'] != multi.explore(case['cacheScopeCompatible']):
            raise ValueError(f'Saved BFS result is stale: {variant}')
        completed = subprocess.run([str(nusmv), str(model)], check=True,
                                   capture_output=True, text=True, timeout=120)
        output = completed.stdout + completed.stderr
        match = SPECIFICATION.search(output)
        if not match:
            raise ValueError(f'Missing NuSMV verdict: {variant}')
        verdict = 'possible' if match[1] == 'false' else 'no-path-in-supported-model'
        if verdict != case['bfs']['verdict']:
            raise ValueError(f'NuSMV/BFS disagree: {variant}')
        (directory / (variant + '.nusmv.txt')).write_text(
            '\n'.join(line.rstrip() for line in output.splitlines()) + '\n')
        case['nusmv'] = {'verdict': verdict,
                         'modelSHA256': hashlib.sha256(model.read_bytes()).hexdigest()}
    (directory / 'analysis-native.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--nusmv', required=True, type=Path)
    args = parser.parse_args()
    verify(args.directory, args.nusmv)


if __name__ == '__main__':
    main()
