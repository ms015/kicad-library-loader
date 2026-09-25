import json
from pathlib import Path
import shutil
import tempfile
import unittest

import loader
from migrate_unified import migrate


class MigrationTests(unittest.TestCase):
    def test_existing_libraries_and_provenance_survive_migration(self):
        live = Path(loader.default_config()['library_root'])
        if not all((live / s).exists() for s in loader.SERVICES):
            self.skipTest('Local installed libraries required')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'Libraries'
            for name in (*loader.SERVICES, 'Tables'):
                shutil.copytree(live / name, root / name)
            before = {p: p.read_bytes() for s in loader.SERVICES for p in (root / s).rglob('*') if p.is_file()}
            settings = dict(loader.default_config(), library_root=str(root), state_folder=str(Path(tmp) / 'state'))
            # Recreate pre-migration tables even after the live library was migrated.
            engine = loader.Engine(settings, lambda _: None)
            for filename in ('sym-lib-table', 'fp-lib-table'):
                path = root / 'Tables' / filename
                tree = loader.parse(path.read_text(encoding='utf-8'))
                for lib in list(loader.children(tree, 'lib')):
                    if loader.val(loader.children(lib, 'name')[0][1]) == 'Parts':
                        tree.remove(lib)
                path.write_text(loader.dump(tree), encoding='utf-8')
            for service in loader.SERVICES:
                for path, data in engine.table_updates(service):
                    path.write_bytes(data)
            report = migrate(settings)
            for path, data in before.items():
                self.assertEqual(path.read_bytes(), data)
            for service, maps in report.items():
                for name in maps['symbols'].values():
                    self.assertTrue((root / 'Parts/Parts.kicad_symdir' / (name + '.kicad_sym')).exists())
                for path in (root / service / 'imports').glob('*.json'):
                    record = json.loads((root / 'Parts/imports' / service / path.name).read_text(encoding='utf-8'))
                    self.assertEqual(record['service'], service)
                    self.assertEqual(record['library'], 'Parts')
            for filename in ('sym-lib-table', 'fp-lib-table'):
                table = loader.parse((root / 'Tables' / filename).read_text(encoding='utf-8'))
                names = {loader.val(loader.children(lib, 'name')[0][1]) for lib in loader.children(table, 'lib')}
                self.assertIn('Parts', names)
                self.assertEqual(len(loader.children(table, 'lib')), len(names))
                self.assertFalse(names.intersection(loader.SERVICES))
                self.assertIn('Personal', names)
            with self.assertRaises(ValueError):
                migrate(settings)
