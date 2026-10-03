#!/usr/bin/env python3
"""Measure whether one saved SARIF result links all three SpotBugs path stages.

This is an output-level metric for pinned inputs, not a statement that other
tools cannot identify the problem or that separate warnings lack value.
"""
import argparse
import gzip
import hashlib
import json
from pathlib import Path


STAGES = {'selector': (35, 41), 'checkout': (42, 45), 'secretSink': (59, 74)}
WORKFLOW = 'sonarqube.yml'


def locations(result):
    found = set()

    def visit(value):
        if isinstance(value, dict):
            physical = value.get('physicalLocation')
            if isinstance(physical, dict):
                uri = physical.get('artifactLocation', {}).get('uri', '')
                region = physical.get('region', {})
                if Path(uri).name == WORKFLOW and region.get('startLine'):
                    # Some relatedLocations cover an entire YAML step and end
                    # at the next step's start. Use the anchor only.
                    found.add(region['startLine'])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(result)
    return found


def summarize(path):
    raw = path.read_bytes()
    data = json.loads(gzip.decompress(raw))
    rows = []
    for run in data['runs']:
        for result in run.get('results', []):
            lines = locations(result)
            stages = [name for name, (lo, hi) in STAGES.items()
                      if any(lo <= line <= hi for line in lines)]
            rows.append({'rule': result['ruleId'], 'stages': stages})
    return {'compressedSHA256': hashlib.sha256(raw).hexdigest(),
            'findingCount': len(rows),
            'sameFindingLinksAllStages': sum(len(row['stages']) == len(STAGES) for row in rows),
            'stageFindingCounts': {stage: sum(stage in row['stages'] for row in rows)
                                   for stage in STAGES},
            'findingsAtPathStages': [row for row in rows if row['stages']]}


def compare(root):
    index = json.loads((root / 'results/spotbugs-screening/evidence-index.json').read_text())
    manifest = json.loads((root / 'experiments/public-cases/spotbugs-chain/manifest.json').read_text())
    source_sha = manifest['files'][0]['sha256']
    if source_sha != index['sourceSHA256']:
        raise ValueError('Proposed analyzer and baseline workflow digests differ')
    proposed = json.loads((root / 'results/spotbugs-screening/conditional-chain-analysis.json').read_text())
    control = json.loads((root / 'results/spotbugs-screening/safe-ref-control-analysis.json').read_text())
    if len(proposed['findings']) != 1 or control['findings']:
        raise ValueError('Proposed original/control outputs no longer match comparison')
    evidence = proposed['findings'][0]['evidence']
    if [e['role'] for e in evidence] != ['conditional-PR-merge-ref',
                                         'checkout-conditional-output',
                                         'local-executable-with-secrets']:
        raise ValueError('Proposed path lacks expected stages')
    outputs = {}
    for entry in index['files']:
        path = root / 'results/spotbugs-screening' / entry['file']
        stats = summarize(path)
        if stats['compressedSHA256'] != entry['compressedSHA256']:
            raise ValueError(f'SARIF checksum mismatch: {path}')
        outputs[entry['file']] = stats
    return {
        'property': proposed['findings'][0]['property'],
        'scope': 'Saved retrospective SARIF results for fixed SpotBugs workflow; listed combined CodeQL input also contains fixed external Action files',
        'interpretation': 'Zero full-path results means no single emitted SARIF result links selector, checkout and secret-bearing local execution. Separate entry warnings exist; custom rules or human correlation could find the route.',
        'stageLines': STAGES,
        'proposed': {'originalPathCount': len(proposed['findings']),
                     'safeRefControlPathCount': len(control['findings']),
                     'evidence': evidence},
        'baselines': outputs,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    args.output.write_text(json.dumps(compare(args.root), ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
