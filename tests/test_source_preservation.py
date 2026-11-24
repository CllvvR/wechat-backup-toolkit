"""Opt-in preservation with synthetic input only; no real account or backup key."""
import base64
import hashlib
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from wechat_backup_converter.archive import discover_backup_files
from wechat_backup_converter.cli import build_parser
from wechat_backup_converter import export
from wechat_backup_converter.index import BackupIndex, BackupMetadata, TextSegment


class SourcePreservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.key = b'S' * 16
        # Message with an unrecognized field 50 and one unresolved media reference.
        self.raw = b'\x08\x01\x50\x01\x5a\x03\x0a\x01m\x62\x02\x08\x03\x92\x03\x02\x00\xff'
        segment = b'\x08\x01\x12' + bytes([len(self.raw)]) + self.raw
        pad = 16 - len(segment) % 16
        enc = Cipher(algorithms.AES(self.key), modes.ECB()).encryptor()
        cipher = enc.update(segment + bytes([pad]) * pad) + enc.finalize()
        (self.root / 'Backup.db').write_bytes(b'synthetic encrypted index')
        (self.root / 'BAK_0_TEXT').write_bytes(cipher)
        (self.root / 'BAK_0_MEDIA').write_bytes(b'opaque media')
        self.index = BackupIndex(BackupMetadata('test', None, 1, 2, None, None, None, None, 0, 0),
            [], {}, [TextSegment(1, 1, 1, 2, 0, len(cipher), 'test', 0, 0, '', 's', '', '')], {}, [], {})

    def convert(self, name='out.db', preserve=True, **kwargs):
        paths = discover_backup_files(self.root)
        config = export.ExportConfig(self.root / name, 'none', None, 1, False, True, preserve)
        with patch.object(export, 'load_index', return_value=self.index), redirect_stdout(StringIO()):
            return export.convert_backup(config, paths, self.root / 'unused', self.key, '', **kwargs)

    def test_opt_in_raw_bytes_digests_and_missing_reference(self):
        with closing(sqlite3.connect(self.convert())) as db:
            meta = dict(db.execute('SELECT key,value FROM export_meta'))
            self.assertEqual(base64.b64decode(db.execute('SELECT raw_proto_base64 FROM messages').fetchone()[0]), self.raw)
            digest = hashlib.sha256((self.root / 'BAK_0_MEDIA').read_bytes()).hexdigest()
            self.assertEqual(meta['source_media_sha256'], digest)
            self.assertEqual(json.loads(meta['source_media_sha256_json']), {'BAK_0_MEDIA': digest})
            self.assertEqual(meta['raw_wire_preserved'], 'true')
            self.assertEqual(meta['backup_media_references_complete'], 'false')
            self.assertEqual(db.execute('SELECT identifier FROM unresolved_message_media').fetchall(), [('m',)])
        with closing(sqlite3.connect(self.convert('old.db', False))) as db:
            self.assertNotIn('raw_proto_base64', {r[1] for r in db.execute('PRAGMA table_info(messages)')})
            self.assertIsNone(db.execute("SELECT value FROM export_meta WHERE key='source_media_sha256'").fetchone())

    def test_multi_media_and_source_changes(self):
        (self.root / 'BAK_1_MEDIA').write_bytes(b'other')
        with closing(sqlite3.connect(self.convert())) as db:
            meta = dict(db.execute('SELECT key,value FROM export_meta'))
            self.assertNotIn('source_media_sha256', meta)
            self.assertEqual(set(json.loads(meta['source_media_sha256_json'])), {'BAK_0_MEDIA', 'BAK_1_MEDIA'})
        stamps = export._source_stamps(discover_backup_files(self.root))
        (self.root / 'BAK_1_MEDIA').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'source changed before export'):
            self.convert('changed.db', expected_source_stamps=stamps)
        self.assertFalse((self.root / 'changed.db').exists())
        original = export.finalize_output
        def mutate(db):
            original(db)
            (self.root / 'BAK_1_MEDIA').write_bytes(b'again')
        with patch.object(export, 'finalize_output', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, 'source changed during export'):
                self.convert('during.db')
        self.assertFalse((self.root / 'during.db').exists())

    def test_cli_opt_in(self):
        parser = build_parser()
        self.assertFalse(parser.parse_args(['convert','--input','test']).preserve_source)
        self.assertTrue(parser.parse_args(['convert','--input','test','--preserve-source']).preserve_source)
        self.assertFalse(hasattr(parser.parse_args(['verify','--input','test']), 'preserve_source'))
