#!/usr/bin/env python3
"""Rebuild the TanStack CodeQL/zizmor source tree from pinned originals."""
import argparse
import json
from pathlib import Path
import shutil

import tanstack_cache_chain


def remove_unrelated_dispatch(path):
    source = path.read_text()
    marker = '  workflow_dispatch:\n'
    if source.count(marker) != 1:
        raise ValueError('Expected one workflow_dispatch event in the pinned bundle workflow')
    path.write_text(source.replace(marker, ''))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('case', type=Path)
    p.add_argument('--variant', choices=['pre-incident', 'mitigation'], required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--without-dispatch-control', action='store_true',
                   help='Remove only the unrelated workflow_dispatch event from the pre-incident bundle workflow')
    args = p.parse_args()
    if args.without_dispatch_control and args.variant != 'pre-incident':
        p.error('--without-dispatch-control applies only to pre-incident')
    if args.output.exists():
        raise FileExistsError('Output directory must be new')
    manifest = json.loads((args.case / 'manifest.json').read_text())
    tanstack_cache_chain.verify_sources(args.case, manifest)
    shutil.copytree(args.case / args.variant, args.output)
    if args.without_dispatch_control:
        remove_unrelated_dispatch(args.output / '.github/workflows/bundle-size.yml')
    dest = args.output / '.github/actions/tanstack-config-setup'
    dest.mkdir(parents=True)
    shutil.copyfile(args.case / 'external-setup/.github/setup/action.yml',
                    dest / 'action.yml')


if __name__ == '__main__':
    main()
