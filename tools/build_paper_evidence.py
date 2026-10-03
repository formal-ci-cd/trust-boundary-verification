#!/usr/bin/env python3
"""Run the fixed YAML matrix and publish a compact, checked paper evidence index."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests import test_supported_subset_yaml_matrix as matrix


def load(path):
    return json.loads((ROOT / path).read_text())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not matrix.NUSMV.is_file() or not os.access(matrix.NUSMV, os.X_OK):
        parser.error('NuSMV executable required; set NUSMV_BIN to a verified NuSMV 2.7.0 binary')
    version = subprocess.run([str(matrix.NUSMV), '-h'], capture_output=True, text=True)
    banner = version.stdout + version.stderr
    if 'NuSMV 2.7.0' not in banner:
        parser.error('Expected NuSMV 2.7.0; observed banner: ' + banner[:200])
    matrix.EVALUATION_ROWS.clear()
    suite = unittest.defaultTestLoader.loadTestsFromModule(matrix)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful() or result.skipped:
        raise SystemExit('YAML matrix failed or skipped; no paper evidence written')
    rows = matrix.EVALUATION_ROWS
    counts = Counter(row['group'] for row in rows)
    expected_counts = {'artifact-single-axis': 32, 'artifact-boolean-4': 16,
                       'artifact-digest-guard-3': 8, 'a5-sink': 2,
                       'tanstack-cache-control': 10}
    if counts != expected_counts or len(rows) != 68 or not all(row['match'] for row in rows):
        raise SystemExit(f'Incomplete or mismatched matrix: {dict(counts)}')
    artifact = load('results/core-artifact-subset/analysis.json')
    with tempfile.TemporaryDirectory() as temporary:
        temporary = Path(temporary)
        subprocess.run([sys.executable, 'tools/evaluate_artifact_subset.py', '.',
                        '--nusmv', str(matrix.NUSMV), '--output', str(temporary / 'artifact')],
                       cwd=ROOT, check=True, capture_output=True, text=True)
        fresh_artifact = json.loads((temporary / 'artifact/analysis.json').read_text())
        if [(r['consumer']['workflow']['file'], r['status'], r['hypotheticalModelVerdict'])
            for r in fresh_artifact['results']] != [
            (r['consumer']['workflow']['file'], r['status'], r['hypotheticalModelVerdict'])
            for r in artifact['results']]:
            raise SystemExit('Saved A1-A5 result differs from fresh YAML analysis')
        fresh_tanstack = {}
        for variant, folder in (('pre', 'pre-incident'), ('mitigation', 'mitigation')):
            output = temporary / ('tanstack-' + variant)
            subprocess.run([sys.executable, 'tools/evaluate_cache_subset.py',
                            str(ROOT / 'experiments/public-cases/tanstack' / folder),
                            '--composites', str(ROOT / 'experiments/public-cases/tanstack/composite-contracts.json'),
                            '--policy', 'historical-pre-2026-06-26',
                            '--nusmv', str(matrix.NUSMV), '--output', str(output)],
                           cwd=ROOT, check=True, capture_output=True, text=True)
            fresh_tanstack[variant] = json.loads((output / 'analysis.json').read_text())
    artifact_rows = artifact['results']
    case_status = {}
    for row in artifact_rows:
        name = row['consumer']['workflow']['file']
        case = next((f'A{i}' for i in range(1, 6) if f'artifact-a{i}-' in name), None)
        if case is None or case in case_status:
            raise SystemExit('Unexpected or duplicated A1-A5 result: ' + name)
        case_status[case] = {
            'status': row['status'], 'nusmv': row['hypotheticalModelVerdict'],
            'independentAgreement': True, 'yaml': name,
            'sourceLocations': row['traceMap'], 'unknownAssumptions': row['unknownAssumptions'],
        }
    expected_artifact = {'A1': 'unsafe-counterexample', 'A2': 'safe-within-model',
                         'A3': 'safe-within-model', 'A4': 'safe-within-model',
                         'A5': 'unsafe-counterexample'}
    if {k: v['status'] for k, v in case_status.items()} != expected_artifact:
        raise SystemExit('A1-A5 saved result disagrees with expected boundary controls')
    tanstack = {}
    for variant in ('pre', 'mitigation'):
        path = f'results/tanstack-cache-chain/common-path-{variant}/analysis.json'
        report = load(path)
        target = [r for r in report['results'] if r['producer']['file'].endswith('bundle-size.yml')
                  and r['consumer']['file'].endswith('release.yml')]
        if len(target) != 1:
            raise SystemExit('Missing or ambiguous TanStack target in ' + path)
        row = target[0]
        fresh_target = [r for r in fresh_tanstack[variant]['results']
                        if r['producer']['file'].endswith('bundle-size.yml')
                        and r['consumer']['file'].endswith('release.yml')]
        if len(fresh_target) != 1 or (fresh_target[0]['status'], fresh_target[0]['modelVerdict']) != (row['status'], row['modelVerdict']):
            raise SystemExit('Saved TanStack result differs from fresh YAML analysis: ' + variant)
        tanstack[variant] = {'status': row['status'], 'nusmv': row['modelVerdict'],
                             'independentAgreement': True, 'sameObject': row['facts']['sameObject'],
                             'input': path, 'unknownAssumptions': row['unknownAssumptions']}
    if tanstack['pre']['status'] != 'unsafe-counterexample' or tanstack['mitigation']['status'] != 'safe-within-model':
        raise SystemExit('TanStack saved status disagrees with expected control')
    baseline_artifact = load('results/core-artifact-tool-baselines/property-level-comparison.json')
    baseline_tanstack = load('results/tanstack-cache-chain/property-level-comparison.json')
    index = {
        'scope': 'Fixed YAML supported subset and saved baseline runs; no general precision/recall claim',
        'nusmv': {'version': '2.7.0',
                  'localBinarySHA256': hashlib.sha256(matrix.NUSMV.read_bytes()).hexdigest()},
        'matrix': {'count': len(rows), 'matched': sum(r['match'] for r in rows),
                   'groups': dict(sorted(counts.items())), 'rows': rows,
                   'additionalChecks': ['two equal-name artifact uploads each remain conditional unsafe',
                                        'reverse Cache schedule has no violation']},
        'artifactCases': case_status,
        'tanstack': tanstack,
        'baselines': {'artifact': {'versions': baseline_artifact['versions'],
                                  'inputs': baseline_artifact['inputs'],
                                  'inputSHA256': baseline_artifact['inputSHA256'],
                                  'savedComparison': 'results/core-artifact-tool-baselines/property-level-comparison.json'},
                      'tanstack': {'savedComparison': 'results/tanstack-cache-chain/property-level-comparison.json',
                                   'toolKeys': sorted(baseline_tanstack['tools'])}},
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / 'evidence-index.json').write_text(json.dumps(index, ensure_ascii=False, indent=2) + '\n')
    observed = defaultdict(Counter)
    for row in rows:
        observed[row['group']][row['observed']] += 1
    lines = ['# 論文用YAML境界マトリクス集計', '',
             'NuSMV 2.7.0を実行し，独立探索と一致を確認した68構成．期待値はテストで先に定義した．', '',
             '| 群 | 件数 | 反例あり | 対象反例なし | unknown/unsupported | 期待値との一致 |',
             '|---|---:|---:|---:|---:|---:|']
    for group in expected_counts:
        counter = observed[group]
        lines.append(f"| {group} | {counts[group]} | {counter['unsafe-counterexample']} | "
                     f"{counter['safe-within-model']} | {counter['unknown/unsupported']} | "
                     f"{sum(r['match'] for r in rows if r['group'] == group)}/{counts[group]} |")
    lines.extend(['', '候補0件と未対応構文はunknown/unsupportedとして集計する．条件付き反例は実行時成功の証明ではない．',
                  '詳細な各構成・事実値は[evidence-index.json](evidence-index.json)を参照する．', ''])
    (args.output / 'yaml-matrix-table.md').write_text('\n'.join(lines))
    print(f'Matrix {len(rows)}/{len(rows)} matched; A1-A5 and TanStack saved results verified')


if __name__ == '__main__':
    main()
