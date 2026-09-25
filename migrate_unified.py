"""Consolidate the four Loader libraries, preserving originals and provenance."""
import json
from pathlib import Path
import shutil
import tempfile

from loader import Engine, LIBRARY, SERVICES, children, config, dump, parse, process_lock, val


def migrate(settings):
    engine = Engine(settings)
    with process_lock(engine.state):
        engine.recover()
        engine.check()
        if (engine.root / LIBRARY).exists():
            raise ValueError('Parts already exists; migration will not overwrite it')
        with tempfile.TemporaryDirectory(prefix='unify-', dir=engine.state) as tmp:
            tmp = Path(tmp)
            staged = Engine(dict(settings, library_root=str(tmp / 'Libraries'),
                                 state_folder=str(tmp / 'state')), lambda _: None)
            report = {}
            for service in SERVICES:
                source = engine.root / service
                if not source.exists():
                    continue
                out = tmp / service
                out.mkdir()
                for ext in ('.3dshapes', '.pretty', '.kicad_symdir'):
                    if ext == '.3dshapes' and not (source / (service + ext)).exists():
                        (out / (service + ext)).mkdir()
                    else:
                        shutil.copytree(source / (service + ext), out / (service + ext))
                original = dict(symbols=sorted(p.stem for p in (out / (service + '.kicad_symdir')).iterdir()),
                                footprints=sorted(p.stem for p in (out / (service + '.pretty')).iterdir()),
                                models=sorted(p.name for p in (out / (service + '.3dshapes')).iterdir()))
                result = dict(service=service, warnings=[], **original)
                staged.publish(out, service, result, staged.root / LIBRARY / 'migration' / (service + '.json'))
                maps = {key: dict(zip(original[key], result[key])) for key in original}
                report[service] = maps
                for path in (source / 'imports').glob('*.json'):
                    record = json.loads(path.read_text(encoding='utf-8-sig'))
                    record['library'] = LIBRARY
                    for key, mapping in maps.items():
                        record[key] = [mapping.get(n, n) for n in record.get(key, [])]
                    for review in record.get('alignment_review', []):
                        fp = Path(review['footprint'])
                        review['footprint'] = maps['footprints'].get(fp.stem, fp.stem) + fp.suffix
                        review['model'] = maps['models'].get(review['model'], review['model'])
                    target = staged.root / LIBRARY / 'imports' / service / path.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
            # Retain unrelated registrations; remove only verified Loader registrations.
            for table in ('sym-lib-table', 'fp-lib-table'):
                source = engine.root / 'Tables' / table
                tree = parse(source.read_text(encoding='utf-8-sig'))
                for lib in list(children(tree, 'lib')):
                    name = val(children(lib, 'name')[0][1])
                    if name in SERVICES:
                        uri = val(children(lib, 'uri')[0][1])
                        ext = '.pretty' if table == 'fp-lib-table' else '.kicad_symdir'
                        if uri != '${KICAD_SYNC_ROOT}/Libraries/' + name + '/' + name + ext:
                            raise ValueError('Unexpected registration: ' + name)
                        tree.remove(lib)
                staged_table = staged.root / 'Tables' / table
                added = parse(staged_table.read_text(encoding='utf-8-sig'))
                tree.extend(children(added, 'lib'))
                staged_table.write_text(dump(tree) + '\n', encoding='utf-8')
            # Validate every new reference before touching the live library.
            for path in (staged.root / LIBRARY / (LIBRARY + '.kicad_symdir')).glob('*.kicad_sym'):
                for sym in children(parse(path.read_text(encoding='utf-8')), 'symbol'):
                    for prop in children(sym, 'property'):
                        if val(prop[1]) == 'Footprint' and val(prop[2]).startswith(LIBRARY + ':'):
                            name = val(prop[2]).split(':', 1)[1]
                            if not (staged.root / LIBRARY / (LIBRARY + '.pretty') / (name + '.kicad_mod')).exists():
                                raise ValueError('Missing footprint: ' + name)
            for path in (staged.root / LIBRARY / (LIBRARY + '.pretty')).glob('*.kicad_mod'):
                for model in children(parse(path.read_text(encoding='utf-8')), 'model'):
                    name = val(model[1]).rsplit('/', 1)[-1]
                    if not (staged.root / LIBRARY / (LIBRARY + '.3dshapes') / name).exists():
                        raise ValueError('Missing model: ' + name)
            updates = [(engine.root / p.relative_to(staged.root), p.read_bytes())
                       for p in staged.root.rglob('*') if p.is_file()]
            # Originals stay available as backups and for existing placed-model paths.
            engine.commit(updates)
            return report


if __name__ == '__main__':
    print(json.dumps(migrate(config()), ensure_ascii=False, indent=2))
