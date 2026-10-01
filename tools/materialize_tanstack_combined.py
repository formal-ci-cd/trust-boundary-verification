#!/usr/bin/env python3
"""Rebuild the TanStack CodeQL/zizmor source tree from pinned originals."""
import argparse
import json
from pathlib import Path
import shutil

import tanstack_cache_chain


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('case', type=Path)
    p.add_argument('--variant', choices=['pre-incident', 'mitigation'], required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    if args.output.exists():
        raise FileExistsError('Output directory must be new')
    manifest = json.loads((args.case / 'manifest.json').read_text())
    tanstack_cache_chain.verify_sources(args.case, manifest)
    shutil.copytree(args.case / args.variant, args.output)
    dest = args.output / '.github/actions/tanstack-config-setup'
    dest.mkdir(parents=True)
    shutil.copyfile(args.case / 'external-setup/.github/setup/action.yml',
                    dest / 'action.yml')


if __name__ == '__main__':
    main()
