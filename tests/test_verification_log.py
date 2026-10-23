# 統合ログの内容保持，失敗時の記録，旧形式の読取りと集計の互換性を確認する．
import json
from pathlib import Path
import sys
import subprocess
from unittest.mock import patch
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from workflow_model_v2.verification import verify, read_verification
import evaluate_workflow_v2_extension as evaluation


class VerificationLogTests(unittest.TestCase):
    def model(self, directory, value='FALSE', name='model.smv'):
        path = directory / name
        path.write_text(f'MODULE main\nVAR x : boolean;\nASSIGN init(x) := {value}; next(x) := x;\nCTLSPEC AG !x;\n')
        return path

    def test_real_nusmv_writes_one_log_and_preserves_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            model = self.model(directory)
            self.assertEqual(verify(model), (0, 0))
            self.assertEqual({p.name for p in directory.iterdir()}, {'model.smv', 'model.smv.log'})
            data = read_verification(model)
            self.assertIn('is true', data['modelCheck']['stdout'])
            self.assertIn('NuSMV 2.6.0', data['modelCheck']['stdout'])
            self.assertTrue(data['reachability']['nonemptyReachableSet'])
            self.assertIn('reachable states:', data['reachability']['stdout'])
            self.assertGreaterEqual(data['modelCheck']['seconds'], 0)
            log = Path(str(model) + '.log').read_text()
            for text in ('-ctt', 'read_model -i', '[標準出力]', '[標準エラー・NuSMV診断]', '合計実行時間（秒）'):
                self.assertIn(text, log)
            with self.assertRaises(FileExistsError):
                verify(model)

    def test_unicode_and_spaces_in_model_path(self):
        with tempfile.TemporaryDirectory(prefix='日本語 ') as temporary:
            model = self.model(Path(temporary), name='検証 結果.smv')
            self.assertEqual(verify(model), (0, 0))
            data = read_verification(model)
            self.assertTrue(data['reachability']['nonemptyReachableSet'])
            self.assertIn(str(model), data['reachability']['inputCommands'])
            self.assertNotIn('cannot open input file', data['reachability']['stderr'])

    def test_invalid_model_keeps_diagnostic_and_skips_stats(self):
        with tempfile.TemporaryDirectory() as temporary:
            model = Path(temporary) / 'invalid.smv'
            model.write_text('THIS_IS_NOT_AN_SMV_MODEL\n')
            code, stats = verify(model)
            self.assertNotEqual(code, 0)
            self.assertIsNone(stats)
            data = read_verification(model)
            self.assertTrue(data['modelCheck']['stderr'])
            self.assertIsNone(data['reachability'])
            self.assertEqual(len(list(Path(temporary).iterdir())), 2)

    def fake(self, directory, body):
        path = directory / 'fake-nusmv'
        path.write_text(body)
        real_run = subprocess.run
        # /tmpの実行制限を緩めず，テスト用scriptだけを既存Pythonで起動する．
        def run(command, **kwargs):
            return real_run([sys.executable, str(path), *command[1:]], **kwargs)
        replacement = patch('workflow_model_v2.verification.subprocess.run', side_effect=run)
        replacement.start()
        self.addCleanup(replacement.stop)
        return str(path)

    def test_timeout_keeps_partial_stdout_and_stderr(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            model = self.model(directory)
            executable = self.fake(directory, 'import sys,time\nprint("途中の出力",flush=True)\nprint("途中の診断",file=sys.stderr,flush=True)\ntime.sleep(3)\n')
            self.assertEqual(verify(model, executable=executable, timeout=0.2), (124, None))
            data = read_verification(model)
            self.assertIn('途中の出力', data['modelCheck']['stdout'])
            self.assertIn('途中の診断', data['modelCheck']['stderr'])
            self.assertTrue(data['modelCheck']['timedOut'])
            self.assertIn('no reachability verdict', data['modelCheck']['stderr'])

    def test_zero_exit_without_reachable_states_is_not_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            model = self.model(directory)
            executable = self.fake(directory, 'import sys\nprint("-- specification AG !x is true" if "-ctt" in sys.argv else "no state count")\n')
            self.assertEqual(verify(model, executable=executable), (0, 1))
            data = read_verification(model)
            self.assertEqual(data['reachability']['processExitcode'], 0)
            self.assertEqual(data['reachability']['exitcode'], 1)
            self.assertIn('no positive reachable-state count', data['reachability']['diagnostic'])

    def test_stats_timeout_is_saved_separately(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            model = self.model(directory)
            executable = self.fake(directory, 'import sys,time\nif "-source" in sys.argv:\n print("enumerating",flush=True)\n time.sleep(3)\nelse:\n print("-- specification AG !x is true")\n')
            self.assertEqual(verify(model, executable=executable, timeout=0.2), (0, 124))
            data = read_verification(model)
            self.assertFalse(data['modelCheck']['timedOut'])
            self.assertTrue(data['reachability']['timedOut'])
            self.assertIn('enumerating', data['reachability']['stdout'])

    def test_missing_executable_is_recorded(self):
        with tempfile.TemporaryDirectory() as temporary:
            model = self.model(Path(temporary))
            self.assertEqual(verify(model, executable=str(Path(temporary) / 'not-found')), (127, None))
            self.assertIn('Cannot start NuSMV', read_verification(model)['modelCheck']['stderr'])

    def test_old_results_are_not_overwritten_and_can_be_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            model = self.model(Path(temporary))
            Path(str(model) + '.stdout').write_text('old result')
            Path(str(model) + '.exitcode').write_text('124\n')
            Path(str(model) + '.timing').write_text('real 1m2.5s\n')
            with self.assertRaises(FileExistsError):
                verify(model)
            data = read_verification(model)
            self.assertEqual(data['modelCheck']['stdout'], 'old result')
            self.assertEqual(data['modelCheck']['seconds'], 62.5)
            self.assertTrue(data['modelCheck']['timedOut'])
            self.assertFalse(Path(str(model) + '.log').exists())

    def test_partial_log_is_incomplete_even_with_old_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            model = self.model(Path(temporary))
            Path(str(model) + '.log').write_text('partial run\n')
            Path(str(model) + '.exitcode').write_text('0\n')
            data = read_verification(model)
            self.assertEqual(data['format'], 'incomplete')
            self.assertIsNone(data['modelCheck']['exitcode'])

    def test_summary_reads_both_formats_without_trace_sidecars(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            new, old = root / 'new', root / 'old'
            new.mkdir(); old.mkdir()
            model = self.model(new, value='TRUE')
            self.assertEqual(verify(model), (0, 0))
            result = read_verification(model)
            for directory in (new, old):
                (directory / 'model.map.json').write_text(json.dumps(dict(
                    modelVersion='2-unified-bounded', jobs=[], symbols={}, diagnostics=[],
                    verdict='pending-model-check', assumptions=[],
                )))
                (directory / 'inventory.json').write_text(json.dumps([dict(
                    id='log-format-test', model='model.smv', expectedProperties={'AG !x': 'false'},
                )]))
            legacy = self.model(old, value='TRUE')
            for prefix, phase in [('', result['modelCheck']), ('.stats', result['reachability'])]:
                for channel in ('stdout', 'stderr'):
                    Path(str(legacy) + prefix + '.' + channel).write_text(phase[channel])
                Path(str(legacy) + prefix + '.exitcode').write_text(str(phase['exitcode']))
            for directory in (new, old):
                self.assertEqual(evaluation.summarize(directory), 0)
                self.assertFalse(list(directory.glob('*.cex.*')))
                record = json.loads((directory / 'verification-summary.json').read_text())['models'][0]
                self.assertEqual(record['traceCount'], 1)
                self.assertEqual(record['counterexamples'][0]['property'], 'AG !x')
                self.assertTrue(record['stateEnumerationSucceeded'])
            a = json.loads((new / 'verification-summary.json').read_text())['models'][0]
            b = json.loads((old / 'verification-summary.json').read_text())['models'][0]
            for key in ('properties', 'counterexamples', 'traceCount', 'interpretation', 'reachableStates'):
                self.assertEqual(a[key], b[key])


if __name__ == '__main__':
    unittest.main()
