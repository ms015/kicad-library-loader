import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import loader


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.c = loader.default_config()
        self.c.update(library_root=str(self.base / 'Libraries'), state_folder=str(self.base / 'state'),
                      watch_folder=str(self.base / 'Downloads'), stable_seconds=6)
        self.e = loader.Engine(self.c, lambda _: None)

    def test_parser_roundtrip_escapes_unknown_fields(self):
        data = '(root (property "name" "a\\\"b\\\\c") (unknown (xyz -1.23 4 0)))'
        self.assertEqual(loader.parse(data), loader.parse(loader.dump(loader.parse(data))))
        with self.assertRaises(ValueError):
            loader.parse('(unclosed')

    def test_unsafe_names(self):
        for value in ('../escape', 'a/b', 'a:b', 'CON', 'nul.txt', 'trail.', 'a\\b'):
            with self.assertRaises(ValueError):
                loader.safe_name(value)

    def test_zip_traversal(self):
        z = self.base / 'bad.zip'
        with zipfile.ZipFile(z, 'w') as f:
            f.writestr('part.epw', '')
            f.writestr('KiCad/a.kicad_sym', '')
            f.writestr('../escape.kicad_mod', '')
        with self.assertRaises(ValueError):
            loader.unpack(z, self.base / 'raw')
        self.assertFalse((self.base / 'escape.kicad_mod').exists())

    def test_unrelated_zip_ignored(self):
        z = self.base / 'other.zip'
        with zipfile.ZipFile(z, 'w') as f:
            f.writestr('readme.txt', 'hello')
        with self.assertRaises(loader.UnsupportedArchive):
            loader.unpack(z, self.base / 'raw')

    def test_atomic_failure_rolls_back_all_files(self):
        a, b = self.e.root / 'a', self.e.root / 'b'
        loader.atomic(a, b'original')
        original_atomic = loader.atomic
        def fail(path, data):
            if path == b:
                raise OSError('injected disk failure')
            return original_atomic(path, data)
        with loader.process_lock(self.e.state):
            with patch('loader.atomic', side_effect=fail):
                with self.assertRaises(OSError):
                    self.e.commit([(a, b'changed'), (b, b'new')])
        self.assertEqual(a.read_bytes(), b'original')
        self.assertFalse(b.exists())
        self.assertFalse((self.e.state / 'transaction.json').exists())

    def test_crash_recovery(self):
        target = self.e.root / 'a'
        backup = self.e.state / 'saved'
        loader.atomic(backup, b'old')
        loader.atomic(target, b'partial')
        loader.json_write(self.e.state / 'transaction.json', dict(files=[dict(target=str(target), backup=str(backup))]))
        self.e.recover()
        self.assertEqual(target.read_bytes(), b'old')

    def test_tables_preserve_existing_entries(self):
        table = self.e.root / 'Tables' / 'sym-lib-table'
        loader.atomic(table, b'(sym_lib_table (lib (name "Existing") (type "KiCad") (uri "keep")))')
        updates = self.e.table_updates('CSE')
        content = dict(updates)[table].decode()
        self.assertIn('"Existing"', content)
        self.assertIn('"keep"', content)
        for p, data in updates:
            loader.atomic(p, data)
        self.assertEqual(self.e.table_updates('CSE'), [])

    def test_watch_stability_and_restart_dedup(self):
        downloads = Path(self.c['watch_folder']); downloads.mkdir()
        z = downloads / 'part.zip'
        with zipfile.ZipFile(z, 'w') as f:
            f.writestr('test', 'data')
        w = loader.Watcher(self.e)
        with patch.object(self.e, 'import_zip', return_value=dict(status='imported')) as method:
            with patch('loader.time.monotonic', return_value=100): w.scan()
            with patch('loader.time.monotonic', return_value=105): w.scan()
            method.assert_not_called()
            with patch('loader.time.monotonic', return_value=107): w.scan()
            method.assert_called_once()
            loader.Watcher(self.e).scan()
            method.assert_called_once()

    def test_lcsc_input_validation(self):
        for text in ('123', 'C0', 'C20 --help', '../C20'):
            with self.assertRaises(ValueError): self.e.import_lcsc(text)


class SampleIntegrationTests(unittest.TestCase):
    """Licensed ZIP samples stay outside Git; these tests skip on other machines."""
    def setUp(self):
        CoreTests.setUp(self)
        self.cse = Path.home() / 'Downloads' / 'LIB_AP2151WG-7.zip'
        self.ul = Path.home() / 'Downloads' / 'ul_AP2171WG-7.zip'
        if not self.cse.exists() or not self.ul.exists() or not self.e.cli.exists():
            self.skipTest('Local user samples and KiCad 10 required')

    def test_real_zip_import_append_duplicate_and_conflict(self):
        first = self.e.import_zip(self.cse)
        self.assertEqual(first['symbols'], ['AP2151WG-7'])
        self.assertEqual(first['models'], ['AP2151WG-7.stp'])
        ul = self.e.import_zip(self.ul)
        self.assertEqual(len(ul['footprints']), 3)
        self.assertEqual(ul['models'], [])
        before = {str(p): p.read_bytes() for p in self.e.root.rglob('*') if p.is_file()}
        self.assertEqual(self.e.import_zip(self.cse)['status'], 'duplicate')
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.e.root.rglob('*') if p.is_file()})
        # A second symbol using an identical footprint must append, retaining the first.
        other = self.base / 'another.zip'
        with zipfile.ZipFile(self.cse) as src, zipfile.ZipFile(other, 'w') as dst:
            for n in src.namelist():
                data = src.read(n)
                if n.endswith('.kicad_sym'):
                    data = data.replace(b'AP2151WG-7', b'AP2151WG-7_TEST')
                dst.writestr(n, data)
        result = self.e.import_zip(other)
        self.assertEqual(result['symbols'], ['AP2151WG-7_TEST'])
        self.assertEqual(len(list((self.e.root / 'CSE' / 'CSE.kicad_symdir').glob('*'))), 2)
        # A conflicting pad shape must reject the entire import without partial changes.
        conflict = self.base / 'conflict.zip'
        with zipfile.ZipFile(self.cse) as src, zipfile.ZipFile(conflict, 'w') as dst:
            for n in src.namelist():
                data = src.read(n)
                if n.endswith('.kicad_mod'):
                    data = data.replace(b'(size 0.6 1.15)', b'(size 0.7 1.15)')
                dst.writestr(n, data)
        before = {str(p): p.read_bytes() for p in self.e.root.rglob('*') if p.is_file()}
        with self.assertRaises(ValueError): self.e.import_zip(conflict)
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.e.root.rglob('*') if p.is_file()})


if __name__ == '__main__':
    unittest.main()
