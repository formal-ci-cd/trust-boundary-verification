#!/usr/bin/env python3
"""Run an installed NuSMV on generated approval models; stdlib dependencies only."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import subprocess


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory', type=Path)
    p.add_argument('--nusmv', type=Path, required=True)
    args = p.parse_args()
    evaluation = json.loads((args.directory / 'evaluation-replay.json').read_text())
    for variant in evaluation['variants']:
        name = variant['variant']
        model = args.directory / (name + '.smv')
        run = subprocess.run([str(args.nusmv), str(model)], capture_output=True, text=True, check=True)
        (args.directory / (name + '-nusmv.txt')).write_text(run.stdout)
        if re.search(r'-- specification AG !bad\s+is false', run.stdout):
            verdict = 'unsafe'
        elif re.search(r'-- specification AG !bad\s+is true', run.stdout):
            verdict = 'safe'
        else:
            raise RuntimeError('Missing NuSMV verdict')
        if verdict != variant['bfs']['verdict']:
            raise RuntimeError('NuSMV and independent BFS disagree')
        variant['nusmv'] = dict(verdict=verdict, modelSHA256=hashlib.sha256(model.read_bytes()).hexdigest(),
                               version=next(line for line in run.stdout.splitlines() if 'NuSMV ' in line))
    (args.directory / 'evaluation-native.json').write_text(json.dumps(evaluation, ensure_ascii=False, indent=2) + '\n')
    print('NuSMV/BFS agree for original and reject-equality control')


if __name__ == '__main__':
    main()
