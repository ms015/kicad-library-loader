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

    def test_plain_kicad_zip_is_not_misclassified_as_snapeda(self):
        z = self.base / 'plain.zip'
        with zipfile.ZipFile(z, 'w') as f:
            f.writestr('part.kicad_sym', '(kicad_symbol_lib (symbol "X"))')
            f.writestr('part.kicad_mod', '(footprint "X")')
        with self.assertRaises(loader.UnsupportedArchive):
            loader.unpack(z, self.base / 'raw')

    def test_watcher_revisits_previously_unsupported_after_upgrade(self):
        downloads = Path(self.c['watch_folder']); downloads.mkdir()
        z = downloads / 'previously-ignored.zip'
        with zipfile.ZipFile(z, 'w') as f: f.writestr('test', 'data')
        stat = z.stat()
        loader.json_write(self.e.state / 'watch-history.json', {
            str(z.resolve()): dict(signature=[stat.st_size, stat.st_mtime_ns], status='ignored')})
        w = loader.Watcher(self.e)
        with patch.object(self.e, 'import_zip', return_value=dict(status='imported')) as method:
            with patch('loader.time.monotonic', return_value=100): w.scan()
            with patch('loader.time.monotonic', return_value=107): w.scan()
            method.assert_called_once()


class SampleIntegrationTests(unittest.TestCase):
    """Licensed ZIP samples stay outside Git; these tests skip on other machines."""
    def setUp(self):
        CoreTests.setUp(self)
        self.cse = Path.home() / 'Downloads' / 'LIB_AP2151WG-7.zip'
        self.ul = Path.home() / 'Downloads' / 'ul_AP2171WG-7.zip'
        archives = Path(loader.default_config()['state_folder']) / 'archives'
        if not self.cse.exists():
            self.cse = archives / '2aef67e30419d23ae489e7c402a0bb03f8135c61ae32e1bbb0a5a54b0a723113.zip'
        if not self.ul.exists():
            self.ul = archives / 'e3db3b61d0a7dc7394a858f744e4295715d084c8c635d305f4b80afbb3bd5525.zip'
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
        # A conflicting pad shape creates a separate, internally linked part set.
        conflict = self.base / 'conflict.zip'
        with zipfile.ZipFile(self.cse) as src, zipfile.ZipFile(conflict, 'w') as dst:
            for n in src.namelist():
                data = src.read(n)
                if n.endswith('.kicad_mod'):
                    data = data.replace(b'(size 0.6 1.15)', b'(size 0.7 1.15)')
                dst.writestr(n, data)
        before = {str(p): p.read_bytes() for p in self.e.root.rglob('*') if p.is_file()}
        result = self.e.import_zip(conflict)
        self.assertTrue(result['symbols'][0].startswith('AP2151WG-7__'))
        for path, data in before.items(): self.assertEqual(Path(path).read_bytes(), data)
        symfile = self.e.root / 'CSE' / 'CSE.kicad_symdir' / (result['symbols'][0] + '.kicad_sym')
        sym = loader.parse(symfile.read_text(encoding='utf-8'))
        fp = next(loader.val(p[2]) for p in loader.descendants(sym, 'property') if loader.val(p[1]) == 'Footprint')
        self.assertEqual(fp, 'CSE:' + result['footprints'][0])
        foot = loader.parse((self.e.root / 'CSE' / 'CSE.pretty' / (result['footprints'][0] + '.kicad_mod')).read_text(encoding='utf-8'))
        self.assertTrue(loader.val(loader.children(foot, 'model')[0][1]).endswith('/' + result['models'][0]))
        # Different ZIP packaging of identical variant data reuses the same names.
        repacked = self.base / 'repacked.zip'
        with zipfile.ZipFile(conflict) as src, zipfile.ZipFile(repacked, 'w') as dst:
            for name in src.namelist(): dst.writestr(name, src.read(name))
            dst.writestr('extra-note.txt', 'same CAD data')
        repeated = self.e.import_zip(repacked)
        self.assertEqual(repeated['symbols'], result['symbols'])
        self.assertEqual(len(list(symfile.parent.glob('AP2151WG-7__*.kicad_sym'))), 1)


class SnapEDAIntegrationTests(unittest.TestCase):
    def setUp(self):
        CoreTests.setUp(self)
        self.sample = Path.home() / 'Downloads' / 'AP21510FM-7.zip'
        if not self.sample.exists():
            self.sample = Path(loader.default_config()['state_folder']) / 'archives' / '2e89ef9a96f314ac1f154df125e4bbdc5edbd5da4e22ac7b501b8d00f33bacd6.zip'
        if not self.sample.exists() or not self.e.cli.exists():
            self.skipTest('Local SnapEDA sample and KiCad 10 required')

    def test_import_geometry_metadata_link_and_duplicate(self):
        result = self.e.import_zip(self.sample)
        self.assertEqual(result['service'], 'SnapMagic')
        self.assertEqual(result['symbols'], ['AP21510FM-7'])
        root = self.e.root / 'SnapMagic'
        sym = loader.parse((root / 'SnapMagic.kicad_symdir' / 'AP21510FM-7.kicad_sym').read_text(encoding='utf-8'))
        props = {loader.val(p[1]): loader.val(p[2]) for p in loader.descendants(sym, 'property')}
        self.assertEqual(props['MPN'], 'AP21510FM-7')
        self.assertEqual(props['Manufacturer'], 'Diodes Inc.')
        self.assertEqual(props['SNAPEDA_PACKAGE_ID'], '57660')
        self.assertEqual(props['Footprint'], 'SnapMagic:SON50P181X201X60-7N')
        fp = loader.parse((root / 'SnapMagic.pretty' / 'SON50P181X201X60-7N.kicad_mod').read_text(encoding='utf-8'))
        with zipfile.ZipFile(self.sample) as z:
            original = loader.parse(z.read('SON50P181X201X60-7N.kicad_mod').decode())
            self.assertEqual((root / 'SnapMagic.3dshapes' / 'AP21510FM-7.step').read_bytes(), z.read('AP21510FM-7.step'))
        def pads(tree):
            return {loader.val(p[1]): [[float(v) for v in loader.children(p, field)[0][1:]]
                    for field in ('at', 'size')] for p in loader.children(tree, 'pad')}
        self.assertEqual(pads(original), pads(fp))
        self.assertEqual(len(loader.children(fp, 'fp_poly')), len(loader.children(original, 'fp_poly')))
        model = loader.children(fp, 'model')[0]
        self.assertEqual(loader.val(model[1]), '${KICAD_SYNC_ROOT}/Libraries/SnapMagic/SnapMagic.3dshapes/AP21510FM-7.step')
        self.assertTrue(result['warnings'])
        self.assertEqual(self.e.import_zip(self.sample)['status'], 'duplicate')

    def test_unrelated_footprint_does_not_guess_model_pairing(self):
        modified = self.base / 'multiple.zip'
        with zipfile.ZipFile(self.sample) as src, zipfile.ZipFile(modified, 'w') as dst:
            for name in src.namelist(): dst.writestr(name, src.read(name))
            dst.writestr('Extra.kicad_mod', src.read('SON50P181X201X60-7N.kicad_mod'))
        result = self.e.import_zip(modified)
        self.assertEqual(len(result['footprints']), 2)
        for p in (self.e.root / 'SnapMagic' / 'SnapMagic.pretty').glob('*.kicad_mod'):
            models = loader.children(loader.parse(p.read_text(encoding='utf-8')), 'model')
            self.assertEqual(len(models), 0 if p.stem == 'Extra' else 1)
        self.assertEqual(len(result['warnings']), 2)

    def test_collection_zip_rejected_even_when_renamed(self):
        sample = Path.home() / 'Downloads' / 'SnapEDA-Library.zip'
        if not sample.exists(): self.skipTest('Local collection sample required')
        with self.assertRaises(loader.UnsupportedArchive): self.e.import_zip(sample)
        renamed = self.base / 'renamed.zip'
        with zipfile.ZipFile(sample) as src, zipfile.ZipFile(renamed, 'w') as dst:
            for name in src.namelist():
                dst.writestr('Anything.kicad_sym' if name.endswith('.kicad_sym') else name, src.read(name))
        with self.assertRaises(loader.UnsupportedArchive): self.e.import_zip(renamed)
        self.assertFalse(self.e.root.exists())

    def test_changed_pin_visibility_gets_separate_symbol(self):
        self.e.import_zip(self.sample)
        changed = self.base / 'changed.zip'
        with zipfile.ZipFile(self.sample) as src, zipfile.ZipFile(changed, 'w') as dst:
            for name in src.namelist():
                data = src.read(name)
                if name.endswith('.kicad_sym'):
                    tree = loader.parse(data.decode())
                    pin = next(p for p in loader.descendants(tree, 'pin') if loader.val(loader.children(p, 'number')[0][1]) == '6')
                    pin.remove('hide')
                    data = loader.dump(tree).encode()
                dst.writestr(name, data)
        result = self.e.import_zip(changed)
        self.assertTrue(result['symbols'][0].startswith('AP21510FM-7__'))
        directory = self.e.root / 'SnapMagic' / 'SnapMagic.kicad_symdir'
        for name, expected in [('AP21510FM-7', True), (result['symbols'][0], False)]:
            tree = loader.parse((directory / (name + '.kicad_sym')).read_text(encoding='utf-8'))
            pin = next(p for p in loader.descendants(tree, 'pin') if loader.val(loader.children(p, 'number')[0][1]) == '6')
            hidden = 'hide' in pin or ['hide', 'yes'] in pin
            self.assertEqual(hidden, expected)


if __name__ == '__main__':
    unittest.main()
