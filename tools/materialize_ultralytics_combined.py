#!/usr/bin/env python3
"""Create an analysis-only source tree from two pinned Ultralytics repositories."""
import argparse
from pathlib import Path
import shutil
import ultralytics_cache_chain
import json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('case', type=Path)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        raise FileExistsError('Output directory must be new')
    manifest = json.loads((args.case / 'manifest.json').read_text())
    ultralytics_cache_chain.checked(manifest, args.case)
    shutil.copytree(args.case / 'pre-incident', args.output)
    action = args.output / '.github/actions/ultralytics-external'
    action.mkdir(parents=True)
    for name in ['action.yml', 'LICENSE']:
        shutil.copyfile(args.case / 'external-action-2024-12-04' / name, action / name)
    print('Copied 9 original workflows and the pinned remote Action into one analysis tree')


if __name__ == '__main__':
    main()
