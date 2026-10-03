#!/usr/bin/env python3
"""Evaluate one-fact safe controls for the pinned A1/A5 artifact models."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile

import chain_to_nusmv
import static_chain_baseline

ROOT = Path(__file__).resolve().parents[1]
CASES = {
    'A1': ROOT / 'model/chains/gha-a1.json',
    'A5': ROOT / 'model/case-studies/gha-a5-actions-attest.json',
}
CONTROLS = {
    'source-excluded': {'producerUntrusted': 'false'},
    'write-denied': {'writeAuthorized': 'false', 'writeSucceeded': 'false'},
    'save-failed': {'writeSucceeded': 'false', 'readSucceeded': 'false'},
    'different-object': {'sameObject': 'false'},
    'restore-failed': {'readSucceeded': 'false'},
    'not-used': {'consumerUsesObject': 'false'},
    'verified-before-use': {'integrityCheckPresent': 'true', 'integrityCheckPassed': 'true'},
    'no-authority': {'hasAuthority': 'false'},
}


def verify(chain, nusmv):
    bfs = static_chain_baseline.check(chain)['verdict']
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / 'chain.smv'
        path.write_text(chain_to_nusmv.render_model(chain, chain['scenario']['id']))
        run = subprocess.run([str(nusmv), str(path)], capture_output=True, text=True, check=True)
    marker = 'specification AG !(stage = authority_reached & object_tainted)  is '
    verdicts = [line.split(marker, 1)[1].strip() for line in run.stdout.splitlines() if marker in line]
    if len(verdicts) != 1 or verdicts[0] not in ('true', 'false'):
        raise RuntimeError('NuSMV property result not found')
    formal = 'safe' if verdicts[0] == 'true' else 'unsafe'
    if bfs != formal:
        raise RuntimeError(f'NuSMV/BFS disagreement: {bfs}, {formal}')
    return formal


def evaluate(nusmv):
    rows = []
    for case, source in CASES.items():
        original = json.loads(source.read_text())
        baseline = verify(original, nusmv)
        if baseline != 'unsafe':
            raise RuntimeError(f'Expected unsafe baseline: {case}')
        rows.append({'case': case, 'control': 'original', 'changedFacts': {}, 'verdict': baseline})
        for name, changes in CONTROLS.items():
            control = deepcopy(original)
            control['facts'].update(changes)
            if any(original['facts'][key] == value for key, value in changes.items()):
                raise RuntimeError(f'Control does not change its fact: {case}/{name}')
            verdict = verify(control, nusmv)
            if verdict != 'safe':
                raise RuntimeError(f'Control did not block path: {case}/{name}')
            rows.append({'case': case, 'control': name, 'changedFacts': changes, 'verdict': verdict})
    return {'scope': 'One boundary condition per control in pinned finite artifact models; runtime control not demonstrated', 'rows': rows}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--nusmv', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = evaluate(args.nusmv)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(f"Verified {len(result['rows'])} NuSMV/BFS case-control combinations")


if __name__ == '__main__':
    main()
