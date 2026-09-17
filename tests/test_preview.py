import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import loader
from preview import AlignmentSession, set_vector


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.output = self.root / 'out'
        directory = self.output / 'SnapMagic.pretty'
        directory.mkdir(parents=True)
        self.fp = directory / 'Test.kicad_mod'
        self.fp.write_text('(footprint "Test" (model "some/model.step" (offset (xyz 0 0 0)) (scale (xyz 2 2 2)) (rotate (xyz 10 20 30))))')
        self.session = AlignmentSession(self.output, 'SnapMagic', loader.default_config())

    def test_staged_edits_need_current_preview_and_preserve_scale(self):
        before = self.fp.read_bytes()
        old = self.session.signature(self.fp.name)
        self.session.accept_preview(self.fp.name, old)
        self.assertTrue(self.session.ready())
        self.session.update(0, [270, 0, 90], [0, 0, .2])
        self.assertFalse(self.session.ready())
        self.session.accept_preview(self.fp.name, old)
        self.assertFalse(self.session.ready())
        with self.assertRaises(ValueError): self.session.save()
        self.assertEqual(self.fp.read_bytes(), before)
        self.session.accept_preview(self.fp.name, self.session.signature(self.fp.name))
        saved = self.session.save()
        self.assertEqual(saved[0]['rotation'], [270, 0, 90])
        self.assertEqual(saved[0]['scale'], [2, 2, 2])
        self.assertNotEqual(self.fp.read_bytes(), before)

    def test_invalid_values_do_not_partially_update(self):
        original = self.session.get(0)
        for value in ('nan', 'inf', '1000001'):
            with self.assertRaises(ValueError): self.session.update(0, [0, 0, 0], [0, 0, value])
            self.assertEqual(original, self.session.get(0))

    def test_reset_restores_original_transform(self):
        original = self.session.get(0)
        self.session.update(0, [270, 0, 0], [.2, .4, .6])
        self.session.reset(0)
        self.assertEqual(self.session.get(0), original)

    def test_origin_presets_update_offset_and_are_recorded(self):
        self.fp.write_text(
            '(footprint "Test" (layer "F.Cu") (at 0 0) '
            '(fp_rect (start -4 -2) (end 6 8)) '
            '(pad "1" thru_hole circle (at 1 2) (size 1 1) (drill 1) '
            '(layers "*.Cu" "*.Mask")) '
            '(pad "2" thru_hole circle (at -3 4) (size 1 1) (drill 1) '
            '(layers "*.Cu" "*.Mask")) '
            '(model "some/model.step" (offset (xyz 0 0 0)) '
            '(scale (xyz 1 1 1)) (rotate (xyz 0 0 0))))',
            encoding='utf-8')
        self.session = AlignmentSession(self.output, 'SnapMagic', loader.default_config())
        self.session.set_origin(0, '1番ピン位置')
        self.assertEqual(self.session.get(0)['offset'], [1, 2, 0])
        self.assertEqual(self.session.origins[0], '1番ピン位置')
        self.session.set_origin(0, '外形中心')
        self.assertEqual(self.session.get(0)['offset'], [1, 3, 0])
        self.session.set_origin(0, '手動')
        self.assertEqual(self.session.origins[0], '手動')


class PreviewIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = loader.default_config()
        self.config.update(library_root=str(self.root / 'Libraries'), state_folder=str(self.root / 'state'))
        self.sample = Path.home() / 'Downloads' / 'CD74HC4067SM96E4.zip'
        if not self.sample.exists() or not Path(self.config['kicad_cli']).exists():
            self.skipTest('Local CD74HC4067 sample and KiCad 10 required')

    def test_cancel_does_not_publish(self):
        calls = []
        def cancel(output, service):
            calls.append(service)
            session = AlignmentSession(output, service, self.config)
            session.update(0, [270, 0, 90], [0, 0, 0])
            return None
        engine = loader.Engine(self.config, lambda _: None, reviewer=cancel)
        self.assertEqual(engine.import_zip(self.sample)['status'], 'cancelled')
        self.assertEqual(calls, ['SnapMagic'])
        self.assertFalse(engine.root.exists())

    def test_render_confirm_and_manual_reimport(self):
        screenshots = []
        rotation = [270, 0, 90]
        def review(output, service):
            session = AlignmentSession(output, service, self.config)
            session.update(0, rotation, [0, 0, 0])
            name, signature, png = session.render(0, '上')
            self.assertEqual(png[:8], b'\x89PNG\r\n\x1a\n')
            screenshots.append(png)
            session.accept_preview(name, signature)
            return session.save()
        engine = loader.Engine(self.config, lambda _: None, reviewer=review)
        result = engine.import_zip(self.sample)
        self.assertEqual(result['alignment_review'][0]['rotation'], [270, 0, 90])
        file = engine.root / 'SnapMagic/SnapMagic.pretty/SOP65P780X200-24N.kicad_mod'
        model = loader.children(loader.parse(file.read_text(encoding='utf-8')), 'model')[0]
        angles = [float(v) % 360 for v in loader.children(loader.children(model, 'rotate')[0], 'xyz')[0][1:]]
        self.assertEqual(angles, [270, 0, 90])
        self.assertEqual(engine.import_zip(self.sample)['status'], 'duplicate')
        self.assertEqual(len(screenshots), 1)
        old_manifest = next((engine.root / 'SnapMagic/imports').glob('*.json')).read_bytes()
        engine.import_zip(self.sample, review_existing=True)
        self.assertEqual(len(screenshots), 2)
        self.assertIn(old_manifest, [p.read_bytes() for p in (engine.root / 'SnapMagic/imports').glob('*.json')])
        rotation[2] = 0
        changed = engine.import_zip(self.sample, review_existing=True)
        self.assertIn('__', changed['symbols'][0])
        self.assertEqual(changed['alignment_review'][0]['footprint'], changed['footprints'][0] + '.kicad_mod')
        self.assertEqual(changed['alignment_review'][0]['model'], changed['models'][0])

    def test_dialog_real_render_dirty_state_and_cancel(self):
        import tkinter as tk
        from alignment_ui import AlignmentDialog
        raw = self.root / 'raw'; raw.mkdir()
        loader.unpack(self.sample, raw)
        engine = loader.Engine(self.config, lambda _: None)
        engine.convert(raw, self.root / 'out', 'SnapMagic')
        before = {str(p): p.read_bytes() for p in (self.root / 'out').rglob('*') if p.is_file()}
        window = tk.Tk(); window.withdraw()
        self.addCleanup(window.destroy)
        completed = []
        dialog = AlignmentDialog(window, self.root / 'out', 'SnapMagic', self.config, completed.append)
        deadline = time.monotonic() + 30
        while not dialog.session.ready() and time.monotonic() < deadline:
            window.update(); time.sleep(.02)
        self.assertTrue(dialog.session.ready(), dialog.status.get())
        self.assertEqual(str(dialog.confirm['state']), 'normal')
        dialog.rotation[0].set('270')
        self.assertEqual(str(dialog.confirm['state']), 'disabled')
        dialog.accept()
        self.assertEqual(completed, [])
        dialog.cancel()
        self.assertEqual(completed, [None])
        self.assertEqual(before, {str(p): p.read_bytes() for p in (self.root / 'out').rglob('*') if p.is_file()})

    def test_full_gui_choose_render_adjust_confirm_and_publish(self):
        import tkinter as tk
        import ui
        import alignment_ui
        watch = self.root / 'Downloads'; watch.mkdir()
        self.config.update(watch_folder=str(watch), poll_seconds=.1)
        root = tk.Tk()
        dialog_class = alignment_ui.AlignmentDialog
        timed_out = []

        def automated_dialog(*args, **kwargs):
            dialog = dialog_class(*args, **kwargs)
            phase = [0]
            def adjust():
                if dialog.closed: return
                if dialog.session.ready() and not dialog.running:
                    if phase[0] == 0:
                        dialog.rotation[0].set('270')
                        dialog.rotation[2].set('90')
                        dialog.update_button.invoke()
                        phase[0] = 1
                    else:
                        dialog.confirm.invoke()
                        return
                dialog.window.after(100, adjust)
            dialog.window.after(100, adjust)
            return dialog

        def choose():
            def walk(widget):
                for child in widget.winfo_children():
                    if isinstance(child, ui.ttk.Button) and child.cget('text') == 'ZIPを選んで取り込む':
                        child.invoke(); return True
                    if walk(child): return True
                return False
            walk(root)

        expected = Path(self.config['library_root']) / 'SnapMagic/SnapMagic.pretty/SOP65P780X200-24N.kicad_mod'
        started = time.monotonic()
        def finish():
            if expected.exists() or time.monotonic() - started > 30:
                if not expected.exists(): timed_out.append(True)
                root.tk.eval(root.protocol('WM_DELETE_WINDOW'))
            else:
                root.after(100, finish)

        root.after(150, choose)
        root.after(250, finish)
        with patch.object(ui.tk, 'Tk', return_value=root), \
             patch.object(ui.filedialog, 'askopenfilenames', return_value=[str(self.sample)]), \
             patch.object(alignment_ui, 'AlignmentDialog', side_effect=automated_dialog):
            ui.launch(self.config)
        self.assertFalse(timed_out)
        model = loader.children(loader.parse(expected.read_text(encoding='utf-8')), 'model')[0]
        angles = [float(v) % 360 for v in loader.children(loader.children(model, 'rotate')[0], 'xyz')[0][1:]]
        self.assertEqual(angles, [270, 0, 90])


if __name__ == '__main__':
    unittest.main()
