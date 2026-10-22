#!/usr/bin/env python3
"""Record the pinned CodeQL repository-check rule relevant to SpotBugs."""
import argparse
import hashlib
import json
from pathlib import Path
import re


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def line_of(content, needle):
    for number, line in enumerate(content.splitlines(), start=1):
        if needle in line:
            return number
    raise ValueError(f'Missing source marker: {needle}')


def inspect(pack_root, output):
    pack = pack_root / 'actions-queries/0.6.36/qlpack.yml'
    controls = pack_root / 'actions-all/0.6.2/codeql/actions/security/ControlChecks.qll'
    query = pack_root / 'actions-queries/0.6.36/Security/CWE-829/UntrustedCheckoutCritical.ql'
    for path in (pack, controls, query):
        if not path.is_file():
            raise FileNotFoundError(path)
    pack_text = pack.read_text()
    controls_text = controls.read_text()
    query_text = query.read_text()
    if not re.search(r'(?m)^version: 0\.6\.36$', pack_text):
        raise ValueError('Unexpected Actions query-pack version')
    if not re.search(r'(?m)^\s+codeql/actions-all: 0\.6\.2$', pack_text):
        raise ValueError('Unexpected Actions library-pack version')
    marker = 'class PullRequestTargetRepositoryIfCheck extends RepositoryCheck instanceof If {'
    next_marker = 'class WorkflowRunRepositoryIfCheck extends RepositoryCheck instanceof If {'
    start = controls_text.index(marker)
    end = controls_text.index(next_marker, start)
    body = controls_text[start:end]
    required = [r'"\\bgithub\\.repository\\b"',
                'event = "pull_request_target" and category = any_category()',
                '.regexpFind([']
    if not all(item in body for item in required):
        raise ValueError('Repository-check behavior changed')
    # A match on github.repository alone can satisfy regexpFind; it does not
    # require comparison with the PR head repository.
    if 'not exists(ControlCheck check | check.protects(checkout, event, "untrusted-checkout"))' not in query_text:
        raise ValueError('Critical query no longer suppresses protected checkout')
    if 'not exists(ControlCheck check | check.protects(poisonable, event, "untrusted-checkout"))' not in query_text:
        raise ValueError('Critical query no longer suppresses protected sink')
    result = {
        'schemaVersion': 'spotbugs-codeql-control-check-source-0.1',
        'pack': {'version': '0.6.36', 'actionsAllDependency': '0.6.2',
                 'fileSHA256': sha256(pack)},
        'controlChecks': {'fileSHA256': sha256(controls),
                          'classLine': line_of(controls_text, marker),
                          'githubRepositoryPatternLine': line_of(controls_text,
                                                                  required[0]),
                          'eventApplicabilityLine': line_of(controls_text,
                                                              required[1])},
        'criticalQuery': {'fileSHA256': sha256(query),
                          'checkoutSuppressionLine': line_of(query_text,
                                                               'not exists(ControlCheck check | check.protects(checkout, event, "untrusted-checkout"))'),
                          'sinkSuppressionLine': line_of(query_text,
                                                           'not exists(ControlCheck check | check.protects(poisonable, event, "untrusted-checkout"))')},
        'interpretation': ('The repository-check class uses regexpFind on a list containing '
                           'github.repository alone and applies to pull_request_target. '
                           'The critical query excludes paths protected by a ControlCheck. '
                           'The saved ablation shows this check suppresses the SpotBugs warning '
                           'for the pinned version; this is not a claim about all CodeQL versions.')
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + '\n')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('pack_root', type=Path)
    p.add_argument('--output', required=True, type=Path)
    args = p.parse_args()
    inspect(args.pack_root, args.output)


if __name__ == '__main__':
    main()
