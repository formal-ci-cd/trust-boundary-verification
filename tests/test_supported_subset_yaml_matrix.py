"""YAML-level boundary matrix for the declared artifact subset.

The expected outcomes below are specified from the threat-model boundaries,
before invoking the extractor or model checker. No workflow is executed.
"""

from pathlib import Path
from itertools import product
import os
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import discover_artifact_chains as discovery
import evaluate_artifact_subset as evaluator
import cache_two_run_model
import evaluate_cache_subset
import extract_shared_operations
import yaml_to_model

NUSMV = Path(os.environ.get('NUSMV_BIN') or shutil.which('NuSMV') or
             '/Applications/NuSMV-2.7.0-macos-universal/bin/NuSMV')
PRODUCER = 'artifact-a1-pr-producer.yml'
A1 = 'artifact-a1-unsafe-consumer.yml'
A2 = 'artifact-a2-safe-consumer.yml'
A3 = 'artifact-a3-download-only-consumer.yml'
A4 = 'artifact-a4-no-authority-consumer.yml'
A5_PRODUCER = 'artifact-a5-attest-producer.yml'
A5_CONSUMER = 'artifact-a5-attest-consumer.yml'
TANSTACK = ROOT / 'experiments/public-cases/tanstack'
EVALUATION_ROWS = []


def record(group, variant, expected, observed, row):
    EVALUATION_ROWS.append({
        'group': group, 'variant': variant, 'expected': expected,
        'observed': observed, 'match': expected == observed,
        'facts': row['facts'] if row else None,
        'sourceLocationsChecked': row is not None,
    })


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise AssertionError(f'Expected exactly one YAML anchor: {old}')
    return text.replace(old, new, 1)


def replace_first(text, old, new):
    if old not in text:
        raise AssertionError(f'Missing YAML anchor: {old}')
    return text.replace(old, new, 1)


def no_upload(text):
    marker = '      - name: Upload PR-controlled artifact'
    return text[:text.index(marker)]


def no_download(text):
    return text[:text.index('    steps:')] + '    steps:\n      - run: echo no-download\n'


def producer_triggered_after_consumer(text):
    before, rest = text.split('on:\n', 1)
    _, after = rest.split('permissions:\n', 1)
    return (before + 'on:\n  workflow_run:\n'
            '    workflows: ["GHA-A1 Unverified artifact consumer"]\n'
            '    types: [completed]\n\npermissions:\n' + after)


def consumer_push_before_producer(text):
    before, rest = text.split('on:\n', 1)
    _, after = rest.split('permissions:\n', 1)
    return before + 'on: push\n\npermissions:\n' + after


def renamed_checked_out_file(text):
    return text.replace('.research-artifact-input/payload.txt',
                        'another-pr-controlled-file.txt')


def extra_dynamic_upload(text):
    marker = '      - name: Record producer identity'
    extra = ('      - name: Additional dynamic-name upload\n'
             '        uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02\n'
             '        with:\n'
             '          name: ${{ inputs.other_artifact_name }}\n'
             '          path: .research-artifact-input/payload.txt\n\n')
    return replace_once(text, marker, extra + marker)


def extra_exact_upload(text):
    return extra_dynamic_upload(text).replace(
        'name: ${{ inputs.other_artifact_name }}',
        'name: trust-boundary-build-output')


# axis, variant, producer edit, consumer edit, expected result, decisive fact
CASES = [
    ('source', 'untrusted', None, None, 'unsafe-counterexample', ('producerUntrusted', 'true')),
    ('source', 'trusted-push', ("on:\n  pull_request:", "on:\n  push:"), None,
     'safe-within-model', ('producerUntrusted', 'false')),
    ('source', 'other-literal-checkout-file', renamed_checked_out_file,
     lambda text: text.replace('payload.txt', 'another-pr-controlled-file.txt'),
     'unsafe-counterexample', ('producerUntrusted', 'true')),
    ('write-intent', 'absent', no_upload, None, 'unknown/unsupported', None),
    ('write-call', 'enabled', ('        id: upload-artifact', '        if: true\n        id: upload-artifact'),
     None, 'unsafe-counterexample', ('writeIntent', 'true')),
    ('write-call', 'disabled', ('        id: upload-artifact', '        if: false\n        id: upload-artifact'),
     None, 'safe-within-model', ('writeIntent', 'false')),
    ('write-call', 'unparsed', ('        id: upload-artifact',
                                '        if: github.event.pull_request.draft == false\n        id: upload-artifact'),
     None, 'unknown/unsupported', ('writeIntent', 'unknown')),
    ('write-capability', 'runtime-unknown', None, None,
     'unsafe-counterexample', ('writeAuthorized', 'unknown')),
    ('same-object', 'same-name-candidate', None, None, 'unsafe-counterexample', ('sameObject', 'unknown')),
    ('same-object', 'different-name', None,
     ('name: trust-boundary-build-output', 'name: definitely-different-artifact'),
     'safe-within-model', ('sameObject', 'false')),
    ('same-object', 'dynamic-name', None,
     ('name: trust-boundary-build-output', 'name: ${{ inputs.artifact_name }}'),
     'unknown/unsupported', ('sameObject', 'unknown')),
    ('same-object', 'exact-plus-dynamic-upload', extra_dynamic_upload, None,
     'unsafe-counterexample', ('sameObject', 'unknown')),
    ('order', 'workflow-run-after-producer', None, None,
     'unsafe-counterexample', ('sameObject', 'unknown')),
    ('order', 'unrelated-push', None, ('  workflow_run:', '  push:'),
     'unknown/unsupported', None),
    ('order', 'producer-after-consumer', producer_triggered_after_consumer,
     consumer_push_before_producer, 'unknown/unsupported', None),
    ('read', 'present', None, None, 'unsafe-counterexample', ('readSucceeded', 'unknown')),
    ('read', 'absent', None, no_download, 'unknown/unsupported', None),
    ('read', 'disabled', None,
     ('        id: download-artifact', '        if: false\n        id: download-artifact'),
     'safe-within-model', ('readSucceeded', 'false')),
    ('use', 'present', None, None, 'unsafe-counterexample', ('consumerUsesObject', 'true')),
    ('use', 'absent', None, None, 'safe-within-model', ('consumerUsesObject', 'false')),
    ('use', 'disabled', None,
     ('      - name: Use artifact for a dummy publish decision',
      '      - name: Use artifact for a dummy publish decision\n        if: false'),
     'safe-within-model', ('consumerUsesObject', 'false')),
    ('verification', 'absent', None,
     ("digest=$(sha256sum \"$ARTIFACT_FILE\" | cut -d ' ' -f 1)", 'digest=unverified'),
     'unsafe-counterexample', ('integrityCheckPresent', 'false')),
    ('verification', 'hash-only', None, None,
     'unsafe-counterexample', ('integrityCheckPresent', 'unknown')),
    ('verification', 'trusted-digest-guard', None, None, 'safe-within-model', ('integrityCheckPresent', 'true')),
    ('verification', 'unparsed-digest', None,
     ("expected_digest='c94960cc", "expected_digest=$(echo c94960cc"),
     'unknown/unsupported', ('integrityCheckPresent', 'unknown')),
    ('authority', 'dummy-present', None, None, 'unsafe-counterexample', ('hasAuthority', 'true')),
    ('authority', 'absent', None, None, 'safe-within-model', ('hasAuthority', 'false')),
    ('sink-dependency', 'independent-marker', None,
     ("echo 'authority_available=false'", "echo 'authority_available=true'"),
     'safe-within-model', ('hasAuthority', 'false')),
    ('syntax', 'supported', None, None, 'unsafe-counterexample', ('consumerUsesObject', 'true')),
    ('syntax', 'unsupported-action', None,
     ('      - name: Use artifact for a dummy publish decision',
      '      - uses: unknown/action@v1\n      - name: Use artifact for a dummy publish decision'),
     'unknown/unsupported', None),
    ('syntax', 'early-exit-shell', None,
     ('          request=$(tr', '          exit 0\n          request=$(tr'),
     'unknown/unsupported', None),
    ('syntax', 'extra-if-shell', None,
     ('          request=$(tr', '          if false; then echo skipped; fi\n          request=$(tr'),
     'unknown/unsupported', None),
]


@unittest.skipUnless(NUSMV.exists(), 'NuSMV unavailable')
class SupportedSubsetYamlMatrix(unittest.TestCase):
    def analyze(self, consumer_name, producer_edit=None, consumer_edit=None,
                producer_name=PRODUCER):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflows = root / '.github/workflows'
            workflows.mkdir(parents=True)
            for name, edit in ((producer_name, producer_edit), (consumer_name, consumer_edit)):
                text = (ROOT / '.github/workflows' / name).read_text()
                if callable(edit):
                    text = edit(text)
                elif edit:
                    text = replace_once(text, *edit)
                (workflows / name).write_text(text)
            models = yaml_to_model.load_models(root)
            candidates = discovery.discover_candidates(models)
            rows = [evaluator.evaluate(candidate, NUSMV, root, root)
                    for candidate in candidates]
            for row in rows:
                for loc in row['traceMap'].values():
                    source = root / loc['workflowFile']
                    self.assertTrue(source.exists())
                    self.assertLessEqual(loc['line'], len(source.read_text().splitlines()))
            if not rows:
                return 'unknown/unsupported', None, len(candidates)
            self.assertEqual(len(rows), 1)
            return rows[0]['status'], rows[0], len(candidates)

    def test_boundary_matrix_from_yaml_through_nusmv_and_independent_search(self):
        for axis, variant, producer_edit, consumer_edit, expected, fact in CASES:
            with self.subTest(axis=axis, variant=variant):
                consumer = (A2 if variant in {'trusted-digest-guard', 'unparsed-digest'} else
                            A3 if variant == 'absent' and axis == 'use' else
                            A4 if variant in {'absent', 'independent-marker'} and axis in {'authority', 'sink-dependency'} else
                            A1)
                status, row, count = self.analyze(consumer, producer_edit, consumer_edit)
                record('artifact-single-axis', axis + '/' + variant, expected, status, row)
                self.assertEqual(status, expected)
                if row is None:
                    self.assertEqual(count, 0)
                    continue
                if fact:
                    self.assertEqual(row['facts'][fact[0]], fact[1])
                self.assertEqual(row['hypotheticalModelVerdict'],
                                 'unsafe' if expected == 'unsafe-counterexample' else
                                 'safe' if expected == 'safe-within-model' else
                                 row['hypotheticalModelVerdict'])
                if expected == 'unsafe-counterexample':
                    self.assertTrue(row['counterexampleStages'])
                    self.assertIn('sameObject', row['unknownAssumptions'])
                if expected == 'safe-within-model':
                    self.assertEqual(row['counterexampleStages'], [])
                for loc in row['traceMap'].values():
                    self.assertIsInstance(loc['line'], int)

    def test_a5_metadata_dependency_and_disabled_dummy_sink(self):
        status, row, _ = self.analyze(A5_CONSUMER, producer_name=A5_PRODUCER)
        record('a5-sink', 'original', 'unsafe-counterexample', status, row)
        self.assertEqual(status, 'unsafe-counterexample')
        self.assertEqual(row['facts']['consumerUsesObject'], 'true')
        self.assertEqual(row['facts']['hasAuthority'], 'true')
        status, row, _ = self.analyze(
            A5_CONSUMER, producer_name=A5_PRODUCER,
            consumer_edit=("echo 'dummy_repository_update_reached=true'",
                           "echo 'dummy_repository_update_reached=false'"))
        record('a5-sink', 'disabled', 'safe-within-model', status, row)
        self.assertEqual(status, 'safe-within-model')
        self.assertEqual(row['facts']['consumerUsesObject'], 'true')
        self.assertEqual(row['facts']['hasAuthority'], 'false')
        self.assertEqual(row['counterexampleStages'], [])

    def test_all_supported_source_write_read_use_boolean_combinations(self):
        unsafe_count = 0
        for untrusted, write, read, use in product((False, True), repeat=4):
            with self.subTest(untrusted=untrusted, write=write, read=read, use=use):
                def producer_edit(text):
                    if not untrusted:
                        text = replace_once(text, 'on:\n  pull_request:', 'on:\n  push:')
                    if not write:
                        text = replace_once(text, '        id: upload-artifact',
                                            '        if: false\n        id: upload-artifact')
                    return text

                def consumer_edit(text):
                    if not read:
                        text = replace_once(text, '        id: download-artifact',
                                            '        if: false\n        id: download-artifact')
                    if not use:
                        text = replace_once(
                            text, '      - name: Use artifact for a dummy publish decision',
                            '      - name: Use artifact for a dummy publish decision\n        if: false')
                    return text

                status, row, _ = self.analyze(A1, producer_edit, consumer_edit)
                expected = ('unsafe-counterexample' if all((untrusted, write, read, use))
                            else 'safe-within-model')
                record('artifact-boolean-4',
                       f'source={int(untrusted)},write={int(write)},read={int(read)},use={int(use)}',
                       expected, status, row)
                self.assertEqual(status, expected)
                if status == 'unsafe-counterexample':
                    unsafe_count += 1
                    self.assertTrue(row['counterexampleStages'])
                else:
                    self.assertEqual(row['counterexampleStages'], [])
        self.assertEqual(unsafe_count, 1)

    def test_trusted_digest_guard_across_source_write_read_combinations(self):
        for untrusted, write, read in product((False, True), repeat=3):
            with self.subTest(untrusted=untrusted, write=write, read=read):
                def producer_edit(text):
                    if not untrusted:
                        text = replace_once(text, 'on:\n  pull_request:', 'on:\n  push:')
                    if not write:
                        text = replace_once(text, '        id: upload-artifact',
                                            '        if: false\n        id: upload-artifact')
                    return text

                def consumer_edit(text):
                    if not read:
                        text = replace_once(
                            text, '      - name: Download artifact from the producer run',
                            '      - name: Download artifact from the producer run\n        if: false')
                    return text

                status, row, _ = self.analyze(A2, producer_edit, consumer_edit)
                record('artifact-digest-guard-3',
                       f'source={int(untrusted)},write={int(write)},read={int(read)}',
                       'safe-within-model', status, row)
                self.assertEqual(status, 'safe-within-model')
                self.assertEqual(row['facts']['integrityCheckPresent'], 'true')
                self.assertEqual(row['counterexampleStages'], [])

    def test_two_exact_uploads_do_not_hide_a_supported_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workflows = root / '.github/workflows'
            workflows.mkdir(parents=True)
            (workflows / PRODUCER).write_text(extra_exact_upload(
                (ROOT / '.github/workflows' / PRODUCER).read_text()))
            shutil.copyfile(ROOT / '.github/workflows' / A1, workflows / A1)
            candidates = discovery.discover_candidates(yaml_to_model.load_models(root))
            self.assertEqual(len(candidates), 2)
            self.assertTrue(all(c['pairingStatus'] == 'ambiguous' for c in candidates))
            rows = [evaluator.evaluate(c, NUSMV, root, root) for c in candidates]
            self.assertTrue(all(r['status'] == 'unsafe-counterexample' for r in rows))
            self.assertTrue(all(r['facts']['sameObject'] == 'unknown' for r in rows))


@unittest.skipUnless(NUSMV.exists(), 'NuSMV unavailable')
class CacheYamlBoundaryMatrix(unittest.TestCase):
    def analyze(self, variant='pre-incident', producer_edit=None,
                consumer_edit=None, policy='historical-pre-2026-06-26',
                contracts_enabled=True):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(TANSTACK / variant / '.github/workflows', root / '.github/workflows')
            for name, edit in (('bundle-size.yml', producer_edit), ('release.yml', consumer_edit)):
                if not edit:
                    continue
                path = root / '.github/workflows' / name
                path.write_text(edit(path.read_text()) if callable(edit)
                                else replace_once(path.read_text(), *edit))
            models = yaml_to_model.load_models(root)
            contracts = (extract_shared_operations.load_contracts(TANSTACK / 'composite-contracts.json')
                         if contracts_enabled else {})
            inventory = extract_shared_operations.extract(models, contracts)
            inventory['cacheCandidates'] = extract_shared_operations.pair_cache(inventory['operations'])
            rows = evaluate_cache_subset.evaluate(root, models, inventory, contracts,
                                                  NUSMV, root, policy)
            for row in rows:
                for loc in row['evidence']:
                    source = Path(loc['file'])
                    if not source.is_absolute():
                        source = (root / source) if (root / source).exists() else (ROOT / source)
                    self.assertTrue(source.exists())
                    self.assertLessEqual(loc['line'], len(source.read_text().splitlines()))
            target = [r for r in rows if r['producer']['file'].endswith('bundle-size.yml')
                      and r['producer']['job'] == 'benchmark-pr'
                      and r['consumer']['file'].endswith('release.yml')]
            if not target:
                return 'unknown/unsupported', None, inventory
            self.assertEqual(len(target), 1)
            return target[0]['status'], target[0], inventory

    def test_tanstack_yaml_boundary_matrix(self):
        cases = [
            ('original', {}, 'unsafe-counterexample', ('sameObject', 'unknown')),
            ('producer-disabled', {'producer_edit': lambda text: replace_first(
              text, '      - name: Setup Tools\n        uses:',
              '      - name: Setup Tools\n        if: false\n        uses:')},
             'safe-within-model', ('producerCallEnabled', 'false')),
            ('producer-condition-unknown', {'producer_edit': lambda text: replace_first(
              text, '      - name: Setup Tools\n        uses:',
              '      - name: Setup Tools\n        if: github.event.pull_request.draft == false\n        uses:')},
             'unsafe-counterexample', ('producerCallEnabled', 'unknown')),
            ('read-disabled', {'consumer_edit':
              ('      - name: Setup Tools\n        uses:',
               '      - name: Setup Tools\n        if: false\n        uses:')},
             'safe-within-model', ('consumerCallEnabled', 'false')),
            ('sink-disabled', {'consumer_edit':
              ('      - name: Commit and Push Version Changes\n        id: commit',
               '      - name: Commit and Push Version Changes\n        if: false\n        id: commit')},
             'safe-within-model', ('sinkReachable', 'false')),
            ('authority-absent', {'consumer_edit':
              ('  contents: write\n', '  contents: read\n')},
             'safe-within-model', ('hasAuthority', 'false')),
            ('fork-excluded', {'producer_edit': lambda text: replace_first(
              text, "if: github.event_name == 'pull_request_target'",
              "if: github.event_name == 'pull_request_target' && github.event.pull_request.head.repo.fork == false")},
             'unknown/unsupported', None),
            ('mitigation', {'variant': 'mitigation'},
             'safe-within-model', ('sameObject', 'false')),
            ('mitigation-policy-unknown', {'variant': 'mitigation', 'policy': 'unknown'},
             'unsafe-counterexample', ('sameObject', 'unknown')),
            ('composite-unsupported', {'contracts_enabled': False},
             'unknown/unsupported', None),
        ]
        for name, options, expected, fact in cases:
            with self.subTest(name=name):
                status, row, inventory = self.analyze(**options)
                record('tanstack-cache-control', name, expected, status, row)
                self.assertEqual(status, expected)
                if row is None:
                    self.assertTrue(inventory['unsupportedActions'] or name == 'fork-excluded')
                    continue
                self.assertEqual(row['facts'][fact[0]], fact[1])
                self.assertEqual(row['modelVerdict'],
                                 'unsafe' if expected == 'unsafe-counterexample' else 'safe')
                if expected == 'unsafe-counterexample':
                    self.assertTrue(row['order']['counterexample'])
                else:
                    self.assertEqual(row['order']['counterexample'], [])
                for loc in row['evidence']:
                    self.assertIsInstance(loc['line'], int)

    def test_reverse_schedule_cannot_violate_extracted_cache_path(self):
        status, row, _ = self.analyze()
        self.assertEqual(status, 'unsafe-counterexample')
        setting = {name: (value == 'true') for name, value in row['facts'].items()
                   if value != 'unknown'}
        setting.update(row['unknownAssumptions'])
        state = cache_two_run_model.initial(setting)
        for actor in ('consumer', 'consumer', 'consumer', 'producer', 'producer'):
            state = cache_two_run_model.transition(state, actor)
        self.assertFalse(state[4])


if __name__ == '__main__':
    unittest.main()
