"""KiCad 10 library importer. No third-party code is used for ZIP conversion."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

APP = Path(__file__).resolve().parent
SERVICES = ('CSE', 'UltraLibrarian', 'SnapMagic', 'LCSC')
LIBRARY = 'Parts'
WATCH_FORMAT_VERSION = 3


def default_config():
    return dict(watch_folder=str(Path.home() / 'Downloads'),
                library_root=r'C:\KiCadSync\Libraries',
                state_folder=str(Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'KiCadLibraryLoader'),
                kicad_cli=r'C:\Program Files (user)\KiCad\10.0\bin\kicad-cli.exe',
                poll_seconds=3, stable_seconds=6)


def config(path=None):
    c = default_config()
    p = Path(path or APP / 'config.json')
    if p.exists():
        c.update(json.loads(p.read_text(encoding='utf-8-sig')))
    return c


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('.' + path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with tmp.open('wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def json_write(path, data):
    atomic(path, json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))


# Keep atom spelling and quoting; unknown KiCad fields survive unchanged.
TOKEN = re.compile(r'\s*(\(|\)|"(?:\\.|[^"\\])*"|[^\s()]+)')


def parse(text):
    tokens = TOKEN.findall(text)
    stack, roots = [], []
    for token in tokens:
        if token == '(':
            node = []
            (stack[-1] if stack else roots).append(node)
            stack.append(node)
        elif token == ')':
            if not stack:
                raise ValueError('Unexpected closing parenthesis')
            stack.pop()
        elif stack:
            stack[-1].append(token)
        else:
            raise ValueError('Unexpected text outside S-expression')
    if stack or len(roots) != 1:
        raise ValueError('Invalid S-expression')
    return roots[0]


def dump(node):
    return '(' + ' '.join(dump(x) if isinstance(x, list) else x for x in node) + ')'


def val(token):
    if token.startswith('"'):
        return re.sub(r'\\(["\\])', r'\1', token[1:-1])
    return token


def quote(text):
    return '"' + str(text).replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n') + '"'


def children(node, kind):
    return [x for x in node if isinstance(x, list) and x and x[0] == kind]


def descendants(node, kind):
    for child in node:
        if isinstance(child, list):
            if child and child[0] == kind:
                yield child
            yield from descendants(child, kind)


def semantic(node):
    return [semantic(x) if isinstance(x, list) else x for x in node
            if not (isinstance(x, list) and x and x[0] in ('uuid', 'tstamp'))]


def safe_name(name):
    if (not name or name in ('.', '..') or re.search(r'[<>:"/\\|?*\x00-\x1f]', name)
            or name.endswith((' ', '.')) or name.split('.')[0].upper() in
            {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(10)), *(f'LPT{i}' for i in range(10))}):
        raise ValueError(f'Unsafe filename: {name!r}')
    return name


def run(args, timeout=120):
    r = subprocess.run([str(a) for a in args], capture_output=True, encoding='utf-8',
                       errors='replace', timeout=timeout,
                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if r.returncode:
        raise RuntimeError(f'{Path(args[0]).name} failed ({r.returncode}): {r.stderr}\n{r.stdout}')
    return r.stdout + r.stderr


class UnsupportedArchive(ValueError):
    pass


def is_snapeda(archive, infos):
    """Identify the source from embedded metadata, not generic KiCad filenames."""
    for info in infos:
        if info.file_size > 8 * 1024**2:
            continue
        if info.filename.lower().endswith('.kicad_sym'):
            try:
                tree = parse(archive.read(info).decode('utf-8-sig'))
                for prop in descendants(tree, 'property'):
                    if len(prop) >= 3 and val(prop[1]) in ('SnapEDA_Link', 'SNAPEDA_PACKAGE_ID', 'SNAPEDA_PN'):
                        return True
            except (ValueError, UnicodeError):
                continue
        elif Path(info.filename).name.lower() == 'how-to-import.htm':
            text = archive.read(info).decode('utf-8-sig', errors='replace')
            if re.search(r'https://(?:www\.)?(?:snapeda|snapmagic)\.com/', text, re.I):
                return True
    return False


def unpack(path, destination):
    """Only extract CAD/text data. Never run scripts in downloaded archives."""
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        if len(infos) > 10000 or sum(i.file_size for i in infos) > 256 * 1024**2:
            raise ValueError('ZIP exceeds 256 MiB / 10000 entries limit')
        names = [i.filename.replace('\\', '/') for i in infos]
        cse = any(n.lower().endswith('.epw') for n in names) and any('/kicad/' in '/' + n.lower() for n in names)
        ul = any(re.search(r'(^|/)kicadv?\d*/', n, re.I) for n in names) and any('importguide' in n.lower() for n in names)
        if cse:
            service = 'CSE'
        elif ul:
            service = 'UltraLibrarian'
        elif is_snapeda(z, infos):
            service = 'SnapMagic'
            symbol_infos = [i for i in infos if i.filename.lower().endswith('.kicad_sym')]
            if any(PurePosixPath(n).name.lower() == 'snapeda-library.kicad_sym' for n in names):
                raise UnsupportedArchive('SnapEDAの一括ZIPは対象外です。個別ZIPを使用してください')
            if symbol_infos:
                count = sum(len(children(parse(z.read(i).decode('utf-8-sig')), 'symbol')) for i in symbol_infos)
                if count != 1:
                    raise UnsupportedArchive('SnapEDAは1部品の個別ZIPのみ対応しています')
            else:
                legacy = [i for i in infos if i.filename.lower().endswith('.lib')]
                count = sum(len(re.findall(rb'^DEF\s', z.read(i), re.M)) for i in legacy)
                if count != 1:
                    raise UnsupportedArchive('SnapEDAは1部品の個別ZIPのみ対応しています')
        else:
            raise UnsupportedArchive('CSE / UltraLibrarian / SnapEDA のKiCad ZIPではありません')
        seen = set()
        for info, name in zip(infos, names):
            p = PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or any(':' in part for part in p.parts):
                raise ValueError('Unsafe ZIP entry: ' + name)
            if info.is_dir():
                continue
            if p.suffix.lower() not in ('.kicad_sym', '.kicad_mod', '.lib', '.step', '.stp', '.wrl'):
                continue
            if p.suffix.lower() == '.lib' and not any(part.lower().startswith('kicad') for part in p.parts):
                if service != 'SnapMagic' or not z.read(info).lstrip().startswith(b'EESchema-LIBRARY'):
                    continue
            for part in p.parts:
                safe_name(part)
            if name.casefold() in seen:
                raise ValueError('Duplicate ZIP path: ' + name)
            seen.add(name.casefold())
            target = destination.joinpath(*p.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(info))
        return service


@contextmanager
def process_lock(state):
    state.mkdir(parents=True, exist_ok=True)
    with (state / 'writer.lock').open('a+b') as f:
        f.seek(0)
        if os.name == 'nt':
            import msvcrt
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise RuntimeError('別のLibrary Loaderが処理中です') from None
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            f.seek(0)
            if os.name == 'nt':
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


class Engine:
    def __init__(self, settings, log=print, reviewer=None):
        self.c, self._display = settings, log
        self.reviewer = reviewer
        self.root = Path(settings['library_root']).resolve()
        self.state = Path(settings['state_folder']).resolve()
        self.cli = Path(settings['kicad_cli'])

    def log(self, message):
        self.state.mkdir(parents=True, exist_ok=True)
        with (self.state / 'loader.log').open('a', encoding='utf-8') as stream:
            stream.write(time.strftime('%Y-%m-%d %H:%M:%S ') + message + '\n')
        self._display(message)

    def check(self):
        version = run([self.cli, '--version']).strip()
        if not re.match(r'10\.', version):
            raise RuntimeError('KiCad 10が必要です: ' + version)

    def recover(self):
        journal = self.state / 'transaction.json'
        if not journal.exists():
            return
        data = json.loads(journal.read_text(encoding='utf-8'))
        for item in reversed(data['files']):
            target = Path(item['target'])
            if not target.is_relative_to(self.root):
                raise RuntimeError('Recovery target outside configured library root')
            if item['backup']:
                atomic(target, Path(item['backup']).read_bytes())
            else:
                target.unlink(missing_ok=True)
        journal.unlink()
        self.log('前回中断した書き込みを復元しました')

    def commit(self, updates):
        backup = self.state / 'backups' / (time.strftime('%Y%m%d-%H%M%S-') + uuid.uuid4().hex[:8])
        backup.mkdir(parents=True)
        files = []
        for target, data in updates:
            old = None
            if target.exists():
                old = backup / str(len(files))
                old.write_bytes(target.read_bytes())
            files.append(dict(target=str(target), backup=str(old) if old else None))
        journal = self.state / 'transaction.json'
        json_write(backup / 'manifest.json', dict(files=files))
        json_write(journal, dict(files=files))
        try:
            for target, data in updates:
                atomic(target, data)
            journal.unlink()
        except BaseException:
            self.recover()
            raise

    def import_zip(self, filename, review_existing=False):
        filename = Path(filename)
        with process_lock(self.state):
            self.recover()
            self.check()
            with tempfile.TemporaryDirectory(prefix='convert-', dir=self.state) as tmp:
                tmp = Path(tmp)
                # Snapshot the download so detection, provenance and conversion use identical bytes.
                if filename.stat().st_size > 256 * 1024**2:
                    raise UnsupportedArchive('ZIP exceeds supported component archive size (256 MiB)')
                data = filename.read_bytes()
                source = tmp / 'source.zip'
                source.write_bytes(data)
                service = unpack(source, tmp / 'raw')
                sha = digest(data)
                manifest = self.root / LIBRARY / 'imports' / service / (sha + '.json')
                previously_imported = manifest.exists()
                if previously_imported and not (review_existing and self.reviewer):
                    self.log(f'{filename.name}: 取り込み済み')
                    return dict(status='duplicate', service=service)
                result = self.convert(tmp / 'raw', tmp / 'out', service)
                result.update(source=filename.name, sha256=sha)
                if not self.review(tmp / 'out', service, result):
                    self.log(filename.name + ': 取り込みをキャンセルしました')
                    return dict(status='cancelled', service=service)
                if previously_imported:
                    review_hash = digest(json.dumps(result.get('alignment_review', []), sort_keys=True).encode())[:12]
                    manifest = manifest.with_name(sha + '__review_' + review_hash + '.json')
                atomic(self.state / 'archives' / (sha + '.zip'), data)
                self.publish(tmp / 'out', service, result, manifest)
                return result

    def import_lcsc(self, part):
        part = part.strip().upper()
        if not re.fullmatch(r'C[1-9][0-9]*', part):
            raise ValueError('LCSC番号をC付きで入力してください（例: C2040）')
        with process_lock(self.state):
            self.recover()
            self.check()
            manifest = self.root / LIBRARY / 'imports' / 'LCSC' / (part + '.json')
            if manifest.exists():
                self.log(part + ': 取り込み済み')
                return dict(status='duplicate', service='LCSC')
            with tempfile.TemporaryDirectory(prefix='lcsc-', dir=self.state) as tmp:
                tmp = Path(tmp)
                raw = tmp / 'raw'
                raw.mkdir()
                self.log(part + ': easyeda2kicadで取得中…')
                output = run([sys.executable, '-m', 'easyeda2kicad', '--full', '--lcsc_id', part,
                              '--output', raw / 'LCSC'], timeout=180)
                self.log(output.strip())
                result = self.convert(raw, tmp / 'out', 'LCSC', part)
                result.update(source=part, converter='easyeda2kicad 1.0.1')
                if not self.review(tmp / 'out', 'LCSC', result):
                    self.log(part + ': 取り込みをキャンセルしました')
                    return dict(status='cancelled', service='LCSC')
                self.publish(tmp / 'out', 'LCSC', result, manifest)
                return result

    def review(self, out, service, result):
        if self.reviewer is None:
            return True
        linked = any(children(parse(p.read_text(encoding='utf-8-sig')), 'model')
                     for p in (out / (service + '.pretty')).glob('*.kicad_mod'))
        if not linked:
            return True
        reviewed = self.reviewer(out, service)
        if reviewed is None:
            return False
        result['alignment_review'] = reviewed
        result['warnings'] = [w for w in result['warnings'] if '位置合わせ未確認' not in w]
        run([self.cli, 'fp', 'upgrade', '--force', out / (service + '.pretty')])
        return True

    def convert(self, raw, out, service, source_id=''):
        out.mkdir(parents=True)
        symdir = out / (service + '.kicad_symdir')
        pretty = out / (service + '.pretty')
        models = out / (service + '.3dshapes')
        for p in (symdir, pretty, models):
            p.mkdir()
        syms = sorted(raw.rglob('*.kicad_sym'))
        if not syms:
            syms = sorted(raw.rglob('*.lib'))
        if not syms:
            raise ValueError('KiCadシンボルが見つかりません')
        seen_symbols = set()
        for index, source in enumerate(syms):
            # CLI handles legacy syntax and KiCad 10 directory-library serialization.
            intermediate = out / f'convert-{index}.kicad_symdir'
            intermediate.mkdir()
            run([self.cli, 'sym', 'upgrade', '--force', source, '-o', intermediate])
            for p in intermediate.glob('*.kicad_sym'):
                safe_name(p.stem)
                if p.name.casefold() in seen_symbols:
                    raise ValueError('Ambiguous duplicate symbol: ' + p.name)
                seen_symbols.add(p.name.casefold())
                shutil.move(p, symdir / p.name)
            intermediate.rmdir()
        if not seen_symbols:
            raise ValueError('変換後のシンボルが空です')
        part_names = {}
        if service == 'SnapMagic':
            for p in symdir.glob('*.kicad_sym'):
                for sym in children(parse(p.read_text(encoding='utf-8-sig')), 'symbol'):
                    props = {val(x[1]): val(x[2]) for x in children(sym, 'property')}
                    fp = props.get('Footprint', '').split(':')[-1]
                    names = {val(sym[1])} | {props[k] for k in ('MP', 'MPN', 'SNAPEDA_PN') if props.get(k)}
                    part_names.setdefault(fp, set()).update(names)
        model_aliases = {}
        for p in raw.rglob('*'):
            if p.suffix.lower() in ('.step', '.stp', '.wrl'):
                filename = p.name
                # The supplied SnapEDA collection appends "0" to model names.
                # Normalize only when a symbol's exact MPN/name establishes the mapping.
                matches = {name for names in part_names.values() for name in names
                           if p.stem.casefold() in (name.casefold(), (name + '0').casefold())}
                if len(matches) == 1:
                    filename = next(iter(matches)) + p.suffix.lower()
                safe_name(filename)
                model_aliases[p.name.casefold()] = filename
                target = models / filename
                existing = [x for x in models.iterdir() if x.name.casefold() == filename.casefold()]
                if existing and existing[0].read_bytes() != p.read_bytes():
                    raise ValueError('Conflicting 3D model: ' + p.name)
                target.write_bytes(p.read_bytes())
        fp_names = set()
        missing = []
        warnings = []
        footprints = sorted(raw.rglob('*.kicad_mod'))
        for p in footprints:
            name = safe_name(p.stem)
            if name.casefold() in {n.casefold() for n in fp_names}:
                raise ValueError('Ambiguous duplicate footprint: ' + name)
            fp_names.add(name)
            tree = parse(p.read_text(encoding='utf-8-sig'))
            if tree[0] not in ('footprint', 'module'):
                raise ValueError('Invalid footprint: ' + name)
            # UL variants share the internal name; preserve the unique filenames.
            tree[1] = quote(name)
            # SnapEDA may ship STEP files without references or transforms.
            # Use symbol MPN-to-footprint mapping, with a single-pair fallback.
            if service == 'SnapMagic' and not children(tree, 'model'):
                steps = [f for f in models.iterdir() if f.suffix.lower() in ('.step', '.stp')]
                matched = [f for f in steps if f.stem.casefold() in {s.casefold() for s in part_names.get(name, set())}]
                if not matched and len(footprints) == 1 and len(steps) == 1:
                    matched = steps
                if len(matched) == 1:
                    tree.append(['model', quote(matched[0].name), ['offset', ['xyz', '0', '0', '0']],
                                 ['scale', ['xyz', '1', '1', '1']], ['rotate', ['xyz', '0', '0', '0']]])
                    warnings.append('3D参照がないため標準位置・倍率でリンク（位置合わせ未確認）: ' + name)
                elif list(models.iterdir()):
                    warnings.append('3Dモデルの対応が一意でないため自動リンクを省略: ' + name)
            for model in children(tree, 'model'):
                basename = val(model[1]).replace('\\', '/').split('/')[-1]
                basename = model_aliases.get(basename.casefold(), basename)
                matches = [f for f in models.iterdir() if f.name.casefold() == basename.casefold()]
                if not matches:
                    missing.append(name + ': ' + basename)
                    tree.remove(model)
                else:
                    model[1] = quote('${KICAD_SYNC_ROOT}/Libraries/' + service + '/' + models.name + '/' + matches[0].name)
            (pretty / p.name).write_text(dump(tree), encoding='utf-8')
        if not fp_names:
            raise ValueError('KiCadフットプリントが見つかりません')
        run([self.cli, 'fp', 'upgrade', '--force', pretty])
        symbols = []
        for p in symdir.glob('*.kicad_sym'):
            tree = parse(p.read_text(encoding='utf-8-sig'))
            for sym in children(tree, 'symbol'):
                symbols.append(val(sym[1]))
                props = {val(x[1]): x for x in children(sym, 'property')}
                fp = props.get('Footprint')
                if fp and val(fp[2]):
                    name = val(fp[2]).split(':')[-1]
                    if name not in fp_names:
                        raise ValueError('Missing footprint for symbol: ' + name)
                    fp[2] = quote(service + ':' + name)
                    foot = parse((pretty / (name + '.kicad_mod')).read_text(encoding='utf-8-sig'))
                    pads = {val(x[1]) for x in children(foot, 'pad')}
                    pins = {val(children(x, 'number')[0][1]) for x in descendants(sym, 'pin')}
                    if pins - pads:
                        raise ValueError('シンボルのピン番号に対応するパッドがありません: ' + ', '.join(sorted(pins - pads)))
                def field(key, value):
                    if key not in props and value:
                        sym.append(['property', quote(key), quote(value), ['at', '0', '0', '0'],
                                    ['effects', ['font', ['size', '1.27', '1.27']], ['hide', 'yes']]])
                field('MPN', next((val(props[k][2]) for k in ('Manufacturer_Part_Number', 'Manufacturer Part', 'MPN', 'MP', 'SNAPEDA_PN') if k in props), val(sym[1])))
                field('Manufacturer', next((val(props[k][2]) for k in ('Manufacturer_Name', 'Manufacturer', 'MF', 'MANUFACTURER') if k in props), ''))
                field('CAD Source', service)
                field('CAD Source ID', source_id or (val(props['SnapEDA_Link'][2]) if 'SnapEDA_Link' in props else ''))
                if service == 'LCSC':
                    field('LCSC Part Number', source_id)
            p.write_text(dump(tree), encoding='utf-8')
        run([self.cli, 'sym', 'upgrade', '--force', symdir])
        # Both the parsing and rendering path must accept the final symbol output.
        run([self.cli, 'sym', 'export', 'svg', symdir, '-o', out / 'preview'])
        shutil.rmtree(out / 'preview', ignore_errors=True)
        return dict(status='imported', service=service, symbols=symbols,
                    footprints=sorted(fp_names), models=[p.name for p in models.iterdir()],
                    warnings=warnings + (['同梱3Dモデルなし'] if not list(models.iterdir()) else []) +
                             ['同梱されていない3D参照を除去: ' + m for m in missing],
                    kicad_version=run([self.cli, '--version']).strip())

    def table_updates(self, service):
        updates = []
        for filename, header, extension in [('sym-lib-table', 'sym_lib_table', '.kicad_symdir'),
                                             ('fp-lib-table', 'fp_lib_table', '.pretty')]:
            path = self.root / 'Tables' / filename
            tree = parse(path.read_text(encoding='utf-8-sig')) if path.exists() else [header, ['version', '7']]
            uri = '${KICAD_SYNC_ROOT}/Libraries/' + service + '/' + service + extension
            entries = [entry for entry in children(tree, 'lib') if val(children(entry, 'name')[0][1]) == service]
            if entries:
                if len(entries) != 1 or val(children(entries[0], 'uri')[0][1]) != uri:
                    raise ValueError('既存ライブラリ登録との衝突: ' + service)
            else:
                tree.append(['lib', ['name', quote(service)], ['type', '"KiCad"'],
                             ['uri', quote(uri)], ['options', '""'], ['descr', '"KiCad Library Loader"']])
                updates.append((path, (dump(tree) + '\n').encode('utf-8')))
        return updates

    def unify(self, out, service):
        """Retarget staged files after source-specific conversion and review."""
        if service == LIBRARY:
            return
        for ext in ('.3dshapes', '.pretty', '.kicad_symdir'):
            directory = out / (service + ext)
            if ext != '.3dshapes':
                for path in directory.iterdir():
                    tree = parse(path.read_text(encoding='utf-8-sig'))
                    if ext == '.pretty':
                        for model in children(tree, 'model'):
                            base = val(model[1]).replace('\\', '/').rsplit('/', 1)[-1]
                            model[1] = quote('${KICAD_SYNC_ROOT}/Libraries/Parts/Parts.3dshapes/' + base)
                    else:
                        for prop in descendants(tree, 'property'):
                            if val(prop[1]) == 'Footprint' and val(prop[2]).startswith(service + ':'):
                                prop[2] = quote(LIBRARY + ':' + val(prop[2]).split(':', 1)[1])
                    path.write_text(dump(tree), encoding='utf-8')
            directory.rename(out / (LIBRARY + ext))

    def publish(self, out, service, result, manifest):
        self.unify(out, service)
        service = LIBRARY
        result['library'] = LIBRARY
        self.rename_conflicts(out, service, result)
        updates = []
        # Models then footprints then symbols, registration last. Never overwrite conflicting parts.
        for ext in ('.3dshapes', '.pretty', '.kicad_symdir'):
            for p in sorted((out / (service + ext)).iterdir()):
                target = self.root / service / (service + ext) / p.name
                data = p.read_bytes()
                if target.exists():
                    same = target.read_bytes() == data
                    if not same and ext != '.3dshapes':
                        same = semantic(parse(target.read_text(encoding='utf-8-sig'))) == semantic(parse(data.decode('utf-8-sig')))
                    if not same:
                        raise ValueError('同名の異なる部品があります。既存データを保持しました: ' + str(target))
                    continue
                updates.append((target, data))
        updates += self.table_updates(service)
        result['imported_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
        updates.append((manifest, json.dumps(result, ensure_ascii=False, indent=2).encode('utf-8')))
        self.commit(updates)
        self.log(f"{service}: {', '.join(result['symbols'])} を登録しました")
        for warning in result['warnings']:
            self.log('注意: ' + warning)

    def rename_conflicts(self, out, service, result):
        """Version a complete incoming set so all internal references stay together."""
        extensions = ('.3dshapes', '.pretty', '.kicad_symdir')
        files = [(ext, p) for ext in extensions for p in sorted((out / (service + ext)).iterdir())]

        def content(ext, path):
            if ext == '.3dshapes':
                return path.read_bytes()
            return dump(semantic(parse(path.read_text(encoding='utf-8-sig')))).encode('utf-8')

        conflicts = [p.name for ext, p in files
                     if (target := self.root / service / (service + ext) / p.name).exists()
                     and content(ext, p) != content(ext, target)]
        if not conflicts:
            return
        fingerprint = hashlib.sha256()
        for ext, p in files:
            fingerprint.update((ext + '/' + p.name + '\0').encode())
            fingerprint.update(hashlib.sha256(content(ext, p)).digest())
        suffix = '__' + fingerprint.hexdigest()[:12]
        maps = {ext: {p.stem: p.stem + suffix for e, p in files if e == ext} for ext in extensions}
        model_names = {p.name: maps[ext][p.stem] + p.suffix for ext, p in files if ext == '.3dshapes'}
        for ext, p in files:
            old, new = p.stem, maps[ext][p.stem]
            if ext != '.3dshapes':
                tree = parse(p.read_text(encoding='utf-8-sig'))
                if ext == '.pretty':
                    tree[1] = quote(new)
                    for model in children(tree, 'model'):
                        path = val(model[1])
                        base = path.rsplit('/', 1)[-1]
                        if base in model_names:
                            model[1] = quote(path.rsplit('/', 1)[0] + '/' + model_names[base])
                else:
                    for sym in children(tree, 'symbol'):
                        old_sym = val(sym[1])
                        new_sym = maps[ext].get(old_sym, new)
                        sym[1] = quote(new_sym)
                        for unit in descendants(sym, 'symbol'):
                            name = val(unit[1])
                            if name.startswith(old_sym + '_'):
                                unit[1] = quote(new_sym + name[len(old_sym):])
                        for parent in children(sym, 'extends'):
                            parent[1] = quote(maps[ext].get(val(parent[1]), val(parent[1])))
                        for prop in children(sym, 'property'):
                            if val(prop[1]) == 'Footprint':
                                name = val(prop[2]).split(':')[-1]
                                if name in maps['.pretty']:
                                    prop[2] = quote(service + ':' + maps['.pretty'][name])
                p.write_text(dump(tree), encoding='utf-8')
            p.rename(p.with_name(new + p.suffix))
        run([self.cli, 'fp', 'upgrade', '--force', out / (service + '.pretty')])
        run([self.cli, 'sym', 'upgrade', '--force', out / (service + '.kicad_symdir')])
        run([self.cli, 'sym', 'export', 'svg', out / (service + '.kicad_symdir'), '-o', out / 'preview'])
        shutil.rmtree(out / 'preview', ignore_errors=True)
        result['renamed_due_to_conflicts'] = conflicts
        result['symbols'] = [maps['.kicad_symdir'].get(n, n) for n in result['symbols']]
        result['footprints'] = [maps['.pretty'].get(n, n) for n in result['footprints']]
        result['models'] = [model_names.get(n, n) for n in result['models']]
        for review in result.get('alignment_review', []):
            footprint = Path(review['footprint'])
            review['footprint'] = maps['.pretty'].get(footprint.stem, footprint.stem) + footprint.suffix
            review['model'] = model_names.get(review['model'], review['model'])
        result['warnings'].append('同名データとの差分を検出したため、関連ファイルを別名で登録: ' + suffix)


class Watcher:
    def __init__(self, engine):
        self.engine = engine
        self.seen = {}
        self.ledger = engine.state / 'watch-history.json'
        self.done = json.loads(self.ledger.read_text(encoding='utf-8')) if self.ledger.exists() else {}

    def scan(self):
        folder = Path(self.engine.c['watch_folder'])
        if not folder.is_dir():
            raise ValueError('監視フォルダが存在しません: ' + str(folder))
        for path in folder.rglob('*.zip'):
            try:
                stat = path.stat()
                key = str(path.resolve())
                signature = [stat.st_size, stat.st_mtime_ns]
                record = self.done.get(key, {})
                if record.get('signature') == signature and record.get('format_version') == WATCH_FORMAT_VERSION:
                    continue
                old = self.seen.get(key)
                if not old or old[0] != signature:
                    self.seen[key] = (signature, time.monotonic())
                    continue
                if time.monotonic() - old[1] < self.engine.c['stable_seconds']:
                    continue
                # A download must be unchanged over the stability window and a complete ZIP.
                if not zipfile.is_zipfile(path):
                    continue
                try:
                    result = self.engine.import_zip(path)
                    status = result['status']
                except UnsupportedArchive:
                    status = 'ignored'
                except Exception as exc:
                    status = 'error: ' + str(exc)
                    self.engine.log(path.name + ': ' + status)
                self.done[key] = dict(signature=signature, status=status, format_version=WATCH_FORMAT_VERSION)
                json_write(self.ledger, self.done)
            except OSError as exc:
                self.engine.log(str(exc))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--config')
    sub = p.add_subparsers(dest='command')
    z = sub.add_parser('zip'); z.add_argument('path')
    l = sub.add_parser('lcsc'); l.add_argument('part')
    sub.add_parser('watch')
    sub.add_parser('gui')
    args = p.parse_args()
    c = config(args.config)
    if args.command in (None, 'gui'):
        from ui import launch
        launch(c)
        return
    e = Engine(c)
    if args.command == 'zip':
        print(json.dumps(e.import_zip(args.path), ensure_ascii=False, indent=2))
    elif args.command == 'lcsc':
        print(json.dumps(e.import_lcsc(args.part), ensure_ascii=False, indent=2))
    elif args.command == 'watch':
        w = Watcher(e)
        while True:
            w.scan()
            time.sleep(c['poll_seconds'])


if __name__ == '__main__':
    main()
