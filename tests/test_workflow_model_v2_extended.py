# 式・イベント・権限・Cache・複数job等の有限モデルを，正常系と遮断系で確認する．

"""Run only inside the isolated analysis container."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from workflow_model_v2 import expressions as ex, policies as pol
from workflow_model_v2.compiler import compile_workflows, shell_contract


def context(**changes):
    result = dict(event='push', activity='', branch='main', scope='refs/heads/main',
                  repository='test/repo', defaultBranch='refs/heads/main', baseBranch='refs/heads/main',
                  fork=False, sendWriteTokens=False, defaultBranchWorkflow=True, refType='branch', runnerOS='Linux', runnerArch='X64',
                  cacheVersion={'data': 'version1'}, checkoutFiles=['data'],
                  changedPaths=['src/code.py'], diffComplete=True, tokenAuthenticated={'contents': True})
    result.update(changes)
    return result


def cache_pair():
    producer = '''name: Save
on: pull_request_target
permissions: {contents: read}
jobs:
  write:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with: {ref: pr-head, persist-credentials: false}
      - uses: actions/cache/save@v4
        with: {key: linux-pkg-new, path: data}
'''
    consumer = '''name: Restore
on: push
permissions: {contents: write}
jobs:
  read:
    runs-on: ubuntu-latest
    steps:
      - id: cache
        uses: actions/cache/restore@v4
        with:
          key: linux-pkg-current
          restore-keys: linux-pkg-
          path: data
      - run: cat data
      - run: git add data
      - run: git push
'''
    scenario = dict(cachePolicy='github-historical-2025', runs=[
        context(id='writer', event='pull_request_target', activity='opened', checkoutUntrusted=True),
        context(id='reader', after='writer')])
    return producer, consumer, scenario


class ExpressionTests(unittest.TestCase):
    def check(self, source, expected, refs=None):
        self.assertEqual(ex.evaluate(ex.parse(source), refs or {}, dict(success=True, failure=False, cancelled=False)), expected)

    def test_boolean_precedence(self):
        self.check("false || true && !(false)", True)

    def test_github_coercion(self):
        for expression in ["null == 0", "'' == false", "'2' == 2", "'MAIN' == 'main'", "'5' > 2"]:
            self.check(expression, True)
        self.check("'abc' >= 0", False)

    def test_operand_preserving_boolean(self):
        self.check("'' || 'fallback'", 'fallback')
        self.check("'yes' && 7", 7)

    def test_functions(self):
        self.check("contains(fromJSON('[\"push\",\"pull_request\"]'), 'PUSH')", True)
        self.check("startsWith('Release/Main','release') && endsWith('Main.YML','.yml')", True)
        self.check("format('{0}-{1}', 'x', 7)", 'x-7')
        self.check("fromJSON('42') > 40", True)
        self.check("toJSON(null)", 'null')

    def test_repeated_reference_is_correlated(self):
        node = ex.parse("inputs.x == 'a' && inputs.x != 'a'")
        lowered = ex.lower(node, lambda _: [('a', 'x = a'), ('b', 'x = b')], {})
        self.assertEqual([v for v, _ in lowered], [False])

    def test_rejects_unsupported(self):
        for source in ['hashFiles(\'a\')', 'fromJSON(\'bad\')', 'contains(\'x\')']:
            with self.assertRaises(ex.UnsupportedExpression):
                ex.evaluate(ex.parse(source), {})
        with self.assertRaises(ex.UnsupportedExpression):
            ex.parse('"bad"')


class PolicyTests(unittest.TestCase):
    def test_glob_order_and_directories(self):
        self.assertTrue(pol.ordered_patterns(['**', '!docs/**', 'docs/keep/**'], 'docs/keep/x'))
        self.assertFalse(pol.ordered_patterns(['**', '!docs/**'], 'docs/x'))
        self.assertTrue(pol.glob('**/package.json', 'package.json'))
        self.assertFalse(pol.glob('a/*', 'a/b/c'))

    def test_filters(self):
        events = {'pull_request': {'branches': ['main'], 'paths': ['src/**', '!src/test/**']}}
        self.assertTrue(pol.trigger(events, context(event='pull_request', activity='opened')))
        self.assertFalse(pol.trigger(events, context(event='pull_request', activity='closed')))
        self.assertFalse(pol.trigger(events, context(event='pull_request', activity='opened', changedPaths=['src/test/a'])))
        with self.assertRaises(pol.UnsupportedPolicy):
            pol.trigger(events, context(event='pull_request', activity='opened', diffComplete=False))

    def test_all_priority_events(self):
        configs = {
            'pull_request': ({}, {'activity': 'opened'}),
            'pull_request_target': ({}, {'activity': 'opened'}),
            'push': ({}, {}), 'workflow_dispatch': ({'inputs': {}}, {}),
            'repository_dispatch': ({'types': ['build']}, {'activity': 'build'}),
            'workflow_call': ({'inputs': {}}, {}),
            'schedule': ([{'cron': '0 0 * * *'}], {}),
            'workflow_run': ({'workflows': ['Build'], 'types': ['completed']}, {'activity': 'completed', 'upstreamName': 'Build'}),
        }
        for event, (config, changes) in configs.items():
            with self.subTest(event=event):
                self.assertTrue(pol.trigger({event: config}, context(event=event, **changes)))

    def test_push_tag_branch_filter_exclusion(self):
        self.assertFalse(pol.trigger({'push': {'branches': ['main']}}, context(refType='tag')))
        self.assertTrue(pol.trigger({'push': {'tags': ['v*'], 'paths': ['src/**']}}, context(refType='tag', branch='v1', changedPaths=[])))

    def test_scope_and_policy(self):
        main = context()
        pr = context(event='pull_request', scope='refs/pull/1/merge', baseBranch='refs/heads/main')
        self.assertTrue(pol.cache_visible(main, pr, 'github-historical-2025'))
        self.assertFalse(pol.cache_visible(pr, main, 'github-historical-2025'))
        self.assertFalse(pol.cache_visible(main, context(repository='other/repo'), 'github-historical-2025'))
        self.assertTrue(pol.cache_writable(pr, 'github-historical-2025'))
        self.assertFalse(pol.cache_writable(main, 'github-current-2026-10-22', 'read'))
        self.assertTrue(pol.cache_writable(main, 'github-current-2026-10-22', 'write'))

    def test_cache_key_priority(self):
        self.assertEqual(pol.cache_rank('pkg-a', 'pkg-a', ['pkg-']), 0)
        self.assertEqual(pol.cache_rank('pkg-abc', 'pkg-a', ['pkg-']), 1)
        self.assertEqual(pol.cache_rank('pkg-z', 'not', ['other', 'pkg-']), 3)
        self.assertIsNone(pol.cache_rank('x', 'y', ['z']))

    def test_cache_case_boundary_is_explicitly_unsupported(self):
        with self.assertRaises(pol.UnsupportedPolicy):
            pol.cache_rank('Linux-deps', 'linux-deps', [])
        self.assertEqual(pol.cache_rank('Linux-deps', 'Linux-deps', []), 0)

    def test_permissions_and_fork_downgrade(self):
        self.assertEqual(pol.permission({'permissions': {'contents': 'write'}}, {}, context(event='pull_request', fork=True), 'contents'), 'read')
        self.assertEqual(pol.permission({'permissions': {'contents': 'write'}}, {'permissions': {}}, context(), 'contents'), 'none')
        self.assertIsNone(pol.permission({}, {}, context(), 'contents'))


class ExtendedTests(unittest.TestCase):
    def compile(self, p=None, c=None, scenario=None):
        bp, bc, bs = cache_pair()
        with tempfile.TemporaryDirectory() as temp:
            a, b = Path(temp) / 'a.yml', Path(temp) / 'b.yml'
            a.write_text(p or bp)
            b.write_text(c or bc)
            return compile_workflows([a, b], scenario or bs)

    def test_cache_and_sink_are_transitions(self):
        smv, manifest = self.compile()
        self.assertIn('next(bad_authority)', smv)
        self.assertIn('save_clock + 1', smv)
        self.assertIn('CTLSPEC NAME no_bad_authority := AG !bad_authority', smv)
        self.assertEqual(manifest['verdict'], 'pending-model-check')
        self.assertTrue(all(v['source']['line'] > 0 for v in manifest['objects']))

    def test_unknown_shell_never_yields_safety(self):
        p, c, s = cache_pair()
        smv, manifest = self.compile(c=c.replace('cat data', 'eval arbitrary'))
        self.assertEqual(manifest['verdict'], 'unknown/unsupported')
        self.assertIn('shell-contract', {d['code'] for d in manifest['diagnostics']})

    def test_matrix_needs_and_continue_on_error(self):
        p, c, s = cache_pair()
        p = p.replace('    steps:', '    strategy:\n      fail-fast: false\n      matrix: {os: [linux, windows]}\n    steps:')
        _, manifest = self.compile(p=p)
        self.assertEqual(len(manifest['jobs']), 3)
        self.assertEqual([j['matrix'] for j in manifest['jobs'][:2]], [{'os': 'linux'}, {'os': 'windows'}])

    def test_missing_version_stays_unsupported(self):
        p, c, s = cache_pair()
        s['runs'][1]['cacheVersion'] = {}
        _, manifest = self.compile(scenario=s)
        self.assertEqual(manifest['verdict'], 'unknown/unsupported')

    def test_whole_shell_rejection(self):
        with self.assertRaises(ValueError):
            shell_contract('echo ok\neval input\necho later')

    def test_dependency_cycle_rejected(self):
        from workflow_model_v2.frontend import WorkflowError
        p, c, s = cache_pair()
        p = p.replace('  write:\n', '  write:\n    needs: write\n')
        with self.assertRaises(WorkflowError):
            self.compile(p=p)

    def test_cancel_status_and_cleanup_have_state(self):
        p, c, s = cache_pair()
        c = c.replace('      - run: git add data', '      - if: cancelled() || always()\n        run: git add data')
        smv, mapping = self.compile(c=c)
        self.assertIn('next(cancelled_', smv)
        self.assertNotIn('step-if-expression', {d['code'] for d in mapping['diagnostics']})

    def test_unknown_workflow_keys_block_safety(self):
        p, c, s = cache_pair()
        _, mapping = self.compile(p='defaults: {run: {working-directory: elsewhere}}\n' + p)
        self.assertEqual(mapping['verdict'], 'unknown/unsupported')

    def test_digest_reads_have_distinct_history(self):
        from workflow_model_v2.compiler import compile_workflows
        workflows = ROOT / '.github/workflows'
        smv, mapping = compile_workflows([workflows / 'artifact-a1-pr-producer.yml', workflows / 'artifact-a2-safe-consumer.yml'])
        self.assertIn('next(verification_read)', smv)
        self.assertIn('next(post_verification_use)', smv)
        self.assertNotIn('AG (integrity_blocked -> !artifact_dependent_use)', smv)
        self.assertIn('AG !post_integrity_mismatch_use', smv)

    def test_composite_metadata_is_hash_checked(self):
        import hashlib
        from workflow_model_v2.frontend import WorkflowError
        p, c, scenario = cache_pair()
        with tempfile.TemporaryDirectory() as temp:
            action = Path(temp) / 'action.yml'
            action.write_text('name: Simple\nruns:\n  using: composite\n  steps:\n    - shell: bash\n      run: cat data\n')
            ref = 'example/tools@pinned'
            scenario['compositeContracts'] = {ref: dict(path=str(action), sha256=hashlib.sha256(action.read_bytes()).hexdigest())}
            _, mapping = self.compile(c=c.replace('run: cat data', 'uses: ' + ref), scenario=scenario)
            self.assertEqual(mapping['verdict'], 'pending-model-check')
            self.assertTrue(any(s['source']['file'] == str(action) for j in mapping['jobs'] for s in j['steps']))
            action.write_text(action.read_text() + '# changed\n')
            with self.assertRaises(WorkflowError):
                self.compile(c=c.replace('run: cat data', 'uses: ' + ref), scenario=scenario)

    def test_trace_mapper_retains_previous_state_values(self):
        from workflow_model_v2.trace_mapper import map_extended_trace
        manifest = {'jobs': [{'id': 'j', 'steps': [{'id': 'read', 'source': {'file': 'x', 'line': 7}}]}]}
        trace = '-> State: 1.1 <-\n  pc_j = s0\n-> Input: 1.2 <-\n  actor = j\n  operation_ok = TRUE\n-> State: 1.2 <-\n  transfer = TRUE\n-> Input: 1.3 <-\n  operation_ok = FALSE\n'
        result = map_extended_trace(trace, manifest)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1]['source']['line'], 7)

    def test_repository_defaults_share_one_permission_symbol(self):
        import re
        p, c, s = cache_pair()
        c = c.replace('permissions: {contents: write}\n', '')
        c += '  second:\n    runs-on: ubuntu-latest\n    steps:\n      - run: git push\n'
        smv, mapping = self.compile(c=c)
        symbols = re.findall(r'^  (permission_default_\w+) :', smv, re.M)
        self.assertEqual(len(symbols), 1)
        self.assertTrue(any(d['status'] == 'unknown' and d['code'] == 'permission-default' for d in mapping['diagnostics']))

    def test_action_version_contracts_keep_distinct_identity(self):
        from workflow_model_v2.action_contracts import resolve
        old, new = resolve('actions/cache@v4'), resolve('actions/cache@v5.0.4')
        self.assertNotEqual(old.identity, new.identity)
        self.assertNotEqual(old.runtime, new.runtime)
        self.assertEqual(old.effect, new.effect)
        self.assertIsNone(resolve('actions/cache@unknown'))


if __name__ == '__main__':
    unittest.main()
