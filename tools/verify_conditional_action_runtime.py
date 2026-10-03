#!/usr/bin/env python3
"""Replay a pinned Action bundle with harmless inputs in an offline Docker container.

This checks the supplied bundle's behavior, not the mutable tag's historical
target or a third-party CI run. No repository secret or attack payload is used.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess


DEFAULT_IMAGE = 'node@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293'


def parse_output(output):
    match = re.search(r'^value<<([^\n]+)\n([^\n]+)\n\1$', output, re.MULTILINE)
    if not match:
        raise ValueError('No well-formed GitHub Actions value output')
    return match.group(2)


def replay(bundle, image, condition):
    command = [
        'docker', 'run', '--rm', '--pull=never', '--network', 'none', '--read-only',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
        '--user', '1000:1000', '--tmpfs', '/tmp:rw,mode=1777',
        '-v', str(bundle.resolve()) + ':/action/index.js:ro',
        '-e', 'INPUT_COND=' + condition,
        '-e', 'INPUT_IF_TRUE=refs/pull/1116/merge',
        '-e', 'INPUT_IF_FALSE=refs/heads/master',
        '-e', 'GITHUB_OUTPUT=/tmp/gha-output', image,
        'sh', '-lc', 'touch /tmp/gha-output && node /action/index.js && cat /tmp/gha-output',
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=True)
    return parse_output(completed.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('--image', default=DEFAULT_IMAGE)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    observed = {condition: replay(args.bundle, args.image, condition)
                for condition in ('true', 'false')}
    expected = {'true': 'refs/pull/1116/merge', 'false': 'refs/heads/master'}
    result = {
        'status': 'matched' if observed == expected else 'mismatch',
        'bundleSha256': hashlib.sha256(args.bundle.read_bytes()).hexdigest(),
        'containerImage': args.image,
        'network': 'none',
        'secrets': 'none',
        'inputs': {'if_true': expected['true'], 'if_false': expected['false']},
        'observed': observed,
        'limitation': ('This observes the supplied bundle only. It does not establish '
                       'the mutable @v1 tag target on 2024-12-06 or any CI execution.'),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    if result['status'] != 'matched':
        raise SystemExit('Action output did not match the model assumption')


if __name__ == '__main__':
    main()
