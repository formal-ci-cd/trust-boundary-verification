#!/usr/bin/env python3
"""Recheck saved scalable TanStack models against independent BFS and NuSMV."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time

import tanstack_n_consumers as scaled


def verify(directory, nusmv):
    report = json.loads((directory / 'analysis.json').read_text())
    count = report['consumers']
    unknown = report['frozenUnknownFacts']
    for case in report['cases']:
        variant = case['variant']
        model = directory / (variant + '.smv')
        expected = scaled.render(count, case['cacheScopeCompatible'],
                                 case['sourceEvidence'], unknown)
        if model.read_text() != expected:
            raise ValueError(f'Model differs from current renderer: {variant}')
        bfs = scaled.explore(count, case['cacheScopeCompatible'], unknown)
        for key in ('reachableStates', 'possible', 'witnessFacts'):
            if case['bfs'][key] != bfs[key]:
                raise ValueError(f'Saved BFS result is stale: {variant} {key}')
        started = time.perf_counter()
        completed = subprocess.run([str(nusmv), str(model)], check=True,
                                   capture_output=True, text=True, timeout=120)
        elapsed = time.perf_counter() - started
        output = completed.stdout + completed.stderr
        match = re.search(r'-- specification .*?\s+is (true|false)', output)
        if not match:
            raise ValueError(f'Missing NuSMV verdict: {variant}')
        possible = match[1] == 'false'
        if possible != case['bfs']['possible']:
            raise ValueError(f'NuSMV/BFS disagree: {variant}')
        case['nusmv'] = {'possible': possible,
                         'modelSHA256': hashlib.sha256(model.read_bytes()).hexdigest(),
                         'seconds': round(elapsed, 3)}
    (directory / 'analysis-native.json').write_text(
        json.dumps(report, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('--nusmv', required=True, type=Path)
    args = parser.parse_args()
    verify(args.directory, args.nusmv)


if __name__ == '__main__':
    main()
