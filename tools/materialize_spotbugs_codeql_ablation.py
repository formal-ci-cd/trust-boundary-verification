#!/usr/bin/env python3
"""Generate one/two-line controls isolating the SpotBugs CodeQL alert gate."""
import argparse
from pathlib import Path


ORIGINAL_GATE = "if: github.repository == 'spotbugs/sonar-findbugs'"
OPEN_GATE = 'if: ${{ true }}'
INDIRECT_REF = 'ref: ${{ steps.condval.outputs.value }}'
INLINE_REF = 'ref: refs/pull/${{ github.event.pull_request.number }}/merge'


def variants(source):
    if source.count(ORIGINAL_GATE) != 1 or source.count(INDIRECT_REF) != 1:
        raise ValueError('Expected exactly one original gate and one indirect ref')
    return {
        'gate-on-inline': source.replace(INDIRECT_REF, INLINE_REF),
        'gate-off-indirect': source.replace(ORIGINAL_GATE, OPEN_GATE),
        'gate-off-inline': source.replace(ORIGINAL_GATE, OPEN_GATE).replace(
            INDIRECT_REF, INLINE_REF),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('source', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        raise FileExistsError('Output directory must be new')
    for name, content in variants(args.source.read_text()).items():
        dest = args.output / name / '.github/workflows/sonarqube.yml'
        dest.parent.mkdir(parents=True)
        dest.write_text(content)


if __name__ == '__main__':
    main()
