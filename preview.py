"""Staged 3D alignment and KiCad CLI snapshots; no additional dependencies."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import tempfile

from loader import children, descendants, dump, parse, quote, run, val


ORIGIN_OPTIONS = ('フットプリント原点', '1番ピン位置', '外形中心', '手動')


def vector(model, key, default):
    nodes = children(model, key)
    return [float(v) for v in children(nodes[0], 'xyz')[0][1:4]] if nodes else list(default)


def set_vector(model, key, values):
    values = [float(v) for v in values]
    if len(values) != 3 or not all(math.isfinite(v) and abs(v) <= 1e6 for v in values):
        raise ValueError('有限の数値を3つ入力してください（絶対値1000000以下）')
    nodes = children(model, key)
    data = [key, ['xyz', *(format(v, '.12g') for v in values)]]
    if nodes:
        model[model.index(nodes[0])] = data
    else:
        model.append(data)


class AlignmentSession:
    def __init__(self, output, service, settings):
        self.output, self.service, self.settings = Path(output), service, settings
        self.directory = self.output / (service + '.pretty')
        self.trees = {}
        self.originals = {}
        self.entries = []
        for path in sorted(self.directory.glob('*.kicad_mod')):
            tree = parse(path.read_text(encoding='utf-8-sig'))
            models = children(tree, 'model')
            if not models:
                continue
            self.trees[path.name] = tree
            self.originals[path.name] = copy.deepcopy(tree)
            for index, model in enumerate(models):
                self.entries.append((path.name, index, val(model[1]).replace('\\', '/').split('/')[-1]))
        self.reviewed = {}
        self.origins = ['フットプリント原点'] * len(self.entries)

    def model(self, entry):
        name, index, _ = self.entries[entry]
        return children(self.trees[name], 'model')[index]

    def get(self, entry):
        model = self.model(entry)
        return dict(rotation=vector(model, 'rotate', (0, 0, 0)),
                    offset=vector(model, 'offset', (0, 0, 0)),
                    scale=vector(model, 'scale', (1, 1, 1)))

    def update(self, entry, rotation, offset, origin=None):
        model = copy.deepcopy(self.model(entry))
        set_vector(model, 'rotate', rotation)
        set_vector(model, 'offset', offset)
        name, index, _ = self.entries[entry]
        tree = self.trees[name]
        old = children(tree, 'model')[index]
        tree[tree.index(old)] = model
        if origin is not None:
            self.origins[entry] = origin

    @staticmethod
    def _xy(node, key='at'):
        nodes = children(node, key)
        if not nodes or len(nodes[0]) < 3:
            return None
        try:
            return float(nodes[0][1]), float(nodes[0][2])
        except (TypeError, ValueError):
            return None

    def _footprint_anchor(self, entry, origin):
        name = self.entries[entry][0]
        tree = self.trees[name]
        if origin == 'フットプリント原点':
            return 0.0, 0.0
        pads = children(tree, 'pad')
        if origin == '1番ピン位置':
            for pad in pads:
                if len(pad) > 1 and val(pad[1]) == '1':
                    point = self._xy(pad)
                    if point is not None:
                        return point
            return 0.0, 0.0
        points = []
        for pad in pads:
            point = self._xy(pad)
            if point is not None:
                points.append(point)
        geometry = ('fp_line', 'fp_rect', 'fp_circle', 'fp_arc', 'fp_poly')
        for shape in geometry:
            for graphic in children(tree, shape):
                for key in ('start', 'end', 'center', 'mid', 'xy'):
                    for node in descendants(graphic, key):
                        point = self._xy([node], key=key)
                        if point is not None:
                            points.append(point)
        if not points:
            return 0.0, 0.0
        xs, ys = zip(*points)
        return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2

    def set_origin(self, entry, origin):
        if origin not in ORIGIN_OPTIONS:
            raise ValueError('不明な原点指定です: ' + str(origin))
        if origin == '手動':
            self.origins[entry] = origin
            return
        current = self.get(entry)
        x, y = self._footprint_anchor(entry, origin)
        self.update(entry, current['rotation'], [x, y, current['offset'][2]], origin)

    def reset(self, entry):
        name, index, _ = self.entries[entry]
        model = copy.deepcopy(children(self.originals[name], 'model')[index])
        old = self.model(entry)
        self.trees[name][self.trees[name].index(old)] = model

    def signature(self, filename):
        return hashlib.sha256(dump(self.trees[filename]).encode()).hexdigest()

    def accept_preview(self, filename, signature):
        if signature == self.signature(filename):
            self.reviewed[filename] = signature

    def ready(self):
        return bool(self.trees) and all(self.reviewed.get(name) == self.signature(name) for name in self.trees)

    def save(self):
        if not self.ready():
            raise ValueError('すべてのフットプリントの最新プレビューを確認してください')
        result = []
        for name, tree in self.trees.items():
            (self.directory / name).write_text(dump(tree), encoding='utf-8')
        for i, (name, _, modelname) in enumerate(self.entries):
            result.append(dict(footprint=name, model=modelname, origin=self.origins[i], **self.get(i)))
        return result

    def render(self, entry, view='斜め'):
        """Return a PNG snapshot; call off the Tk thread, without concurrent edits."""
        filename = self.entries[entry][0]
        signature = self.signature(filename)
        tree = copy.deepcopy(self.trees[filename])
        # Point the preview at staged models, never at an installed older version.
        modeldir = self.output / (self.service + '.3dshapes')
        for model in children(tree, 'model'):
            basename = val(model[1]).replace('\\', '/').split('/')[-1]
            modelpath = modeldir / basename
            if not modelpath.is_file():
                raise ValueError('プレビュー用3Dモデルが見つかりません: ' + basename)
            model[1] = quote(modelpath.resolve().as_posix())
        cli = Path(self.settings['kicad_cli'])
        python = Path(self.settings.get('kicad_python', cli.parent / 'python.exe'))
        if not python.is_file():
            raise ValueError('KiCad付属のpython.exeが必要です。kicad_pythonを設定してください')
        with tempfile.TemporaryDirectory(prefix='preview-', dir=self.output.parent) as tmp:
            tmp = Path(tmp)
            library = tmp / 'preview.pretty'
            library.mkdir()
            (library / filename).write_text(dump(tree), encoding='utf-8')
            job = tmp / 'job.json'
            job.write_text(json.dumps(dict(library=str(library), footprint=Path(filename).stem,
                                           board=str(tmp / 'preview.kicad_pcb'))), encoding='utf-8')
            run([python, Path(__file__).with_name('render_board.py'), job], timeout=30)
            # Tilt toward the front edge, so the board recedes into the screen.
            views = {'斜め': ('top', '-35,0,30'), '上': ('top', None),
                     '正面': ('front', None), '右': ('right', None)}
            side, rotate = views[view]
            args = [cli, 'pcb', 'render', tmp / 'preview.kicad_pcb', '-o', tmp / 'preview.png',
                    '--width', '560', '--height', '480', '--quality', 'basic',
                    '--background', 'opaque', '--side', side, '--zoom', '0.85']
            if rotate:
                args += ['--rotate', rotate]
            run(args, timeout=90)
            image = (tmp / 'preview.png').read_bytes()
            if not image.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError('レンダリング結果がPNGではありません')
        return filename, signature, image
