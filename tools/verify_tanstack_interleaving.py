#!/usr/bin/env python3
"""Verify both generated interleaving models and preserve NuSMV outputs."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


def verify(directory, nusmv):
    report = json.loads((directory / 'analysis.json').read_text())
    for case in report['cases']:
        variant = case['variant']
        model = directory / case['model']
        completed = subprocess.run([str(nusmv), str(model)], check=True,
                                   capture_output=True, text=True, timeout=120)
        output = completed.stdout + completed.stderr
        match = re.search(r'-- specification AG !bad\s+is (true|false)', output)
        if not match:
            raise ValueError(f'Missing NuSMV property verdict: {variant}')
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
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory', type=Path)
    p.add_argument('--nusmv', type=Path, required=True)
    args = p.parse_args()
    verify(args.directory, args.nusmv)


if __name__ == '__main__':
    main()
