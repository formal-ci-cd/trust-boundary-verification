# 出力の追加archiveと復元を確認する．照合失敗や既存ファイルがある場合も元データを保持する．

import hashlib
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from workflow_model_v2.evidence import pack, read_prototype, restore


class EvidenceTests(unittest.TestCase):
    def test_lossless_pack_preserves_every_detail_and_readable_summary(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'verification-summary.json').write_text('{"total": 1, "validated": 1, "syntaxControls": 0, "failures": []}')
            (root / 'case').mkdir()
            data = b'-- specification AG !bad is false\n\x00payload'
            (root / 'case/trace.txt').write_bytes(data)
            result = pack(root)
            self.assertTrue((root / 'verification-summary.json').exists())
            self.assertEqual((root / "case/trace.txt").read_bytes(), data)
            with tarfile.open(root / 'details.tar.gz') as archive:
                restored = archive.extractfile('case/trace.txt').read()
            self.assertEqual(restored, data)
            self.assertEqual(result['members']['case/trace.txt'], hashlib.sha256(data).hexdigest())

    def test_verification_failure_does_not_remove_originals(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'verification-summary.json').write_text('{"total": 1, "validated": 1, "syntaxControls": 0, "failures": []}')
            (root / 'valuable.txt').write_text('must survive')
            with patch('workflow_model_v2.evidence.archive_verified', side_effect=ValueError('hash mismatch')):
                with self.assertRaises(ValueError):
                    pack(root)
            self.assertEqual((root / 'valuable.txt').read_text(), 'must survive')

    def test_existing_archive_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'verification-summary.json').write_text('{"total": 1, "validated": 1, "syntaxControls": 0, "failures": []}')
            (root / 'details.tar.gz').write_bytes(b'prior evidence')
            with self.assertRaises(FileExistsError):
                pack(root)
            self.assertEqual((root / 'details.tar.gz').read_bytes(), b'prior evidence')

    def test_reference_reader_uses_archived_original(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'cases').mkdir()
            (root / 'cases/A1.stdout.txt').write_text('saved result')
            self.assertEqual(read_prototype(root, 'cases/A1.stdout.txt'), 'saved result')
            (root / 'archive').mkdir()
            with tarfile.open(root / 'archive/prototype-2026-10-22.tar.gz', 'w:gz') as archive:
                archive.add(root / 'cases/A1.stdout.txt', arcname='cases/A1.stdout.txt')
            (root / 'cases/A1.stdout.txt').unlink()
            self.assertEqual(read_prototype(root, 'cases/A1.stdout.txt'), 'saved result')

    def test_restore_verifies_and_preserves_existing_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            source, target = Path(temp) / 'source', Path(temp) / 'restored'
            source.mkdir()
            (source / 'verification-summary.json').write_text('{"total": 1, "validated": 1, "syntaxControls": 0, "failures": []}')
            (source / 'model.smv').write_bytes(b'MODULE main\n')
            pack(source)
            result = restore(source / 'details.tar.gz', target, source / 'pack-manifest.json')
            self.assertEqual(result['restored'], 1)
            self.assertEqual((target / 'model.smv').read_bytes(), b'MODULE main\n')
            self.assertEqual(restore(source / 'details.tar.gz', target, source / 'pack-manifest.json')['restored'], 0)
            (target / 'model.smv').write_text('newer output')
            with self.assertRaises(FileExistsError):
                restore(source / 'details.tar.gz', target, source / 'pack-manifest.json')
            self.assertEqual((target / 'model.smv').read_text(), 'newer output')

    def test_corrupt_archive_is_rejected_before_restoration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'verification-summary.json').write_text('{"total": 1, "validated": 1, "syntaxControls": 0, "failures": []}')
            (root / 'model.smv').write_text('evidence')
            pack(root)
            (root / 'details.tar.gz').write_bytes(b'corrupt')
            target = root / 'restored'
            with self.assertRaises(ValueError):
                restore(root / 'details.tar.gz', target, root / 'pack-manifest.json')
            self.assertFalse(target.exists())
