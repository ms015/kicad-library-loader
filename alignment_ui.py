"""Snapshot alignment dialog. All Tk operations stay on the main thread."""
import base64
import queue
import threading
import tkinter as tk
from tkinter import ttk

from preview import AlignmentSession, ORIGIN_OPTIONS


class AlignmentDialog:
    def __init__(self, parent, output, service, settings, complete):
        self.session = AlignmentSession(output, service, settings)
        self.complete = complete
        self.window = tk.Toplevel(parent)
        self.window.title('3D位置合わせ — ' + service)
        self.window.geometry('890x760')
        self.window.minsize(860, 720)
        self.window.transient(parent)
        self.window.grab_set()
        self.window.protocol('WM_DELETE_WINDOW', self.cancel)
        self.closed = False
        self.closing = False
        self.running = False
        self.dirty = False
        self.current = 0
        self.results = queue.Queue()
        self.controls = []
        self.loading = False
        box = ttk.Frame(self.window, padding=14)
        box.pack(fill='both', expand=True)
        ttk.Label(box, text='3D位置合わせ', font=('Yu Gothic UI', 18, 'bold')).pack(anchor='w')
        ttk.Label(box, text='角度を入力して「プレビュー更新」。上面で端子位置、正面・右から高さを確認できます。').pack(anchor='w', pady=(3, 8))
        self.selection = ttk.Combobox(box, state='readonly', values=[
            name + ' / ' + model for name, _, model in self.session.entries])
        self.selection.pack(fill='x')
        self.selection.current(0)
        self.selection.bind('<<ComboboxSelected>>', self.select)
        self.controls.append((self.selection, 'readonly'))
        body = ttk.Frame(box)
        body.pack(fill='both', expand=True, pady=10)
        self.canvas = tk.Canvas(body, width=560, height=480, background='#26333d', highlightthickness=0)
        self.canvas.pack(side='left')
        self.canvas.create_text(280, 240, text='プレビューを準備しています…', fill='white', tags='placeholder')
        right = ttk.Frame(body, padding=(14, 0, 0, 0))
        right.pack(side='left', fill='both', expand=True)
        ttk.Label(right, text='表示方向').pack(anchor='w')
        self.view = tk.StringVar(value='斜め')
        view_style = ttk.Style(self.window)
        view_style.configure('View.TButton', padding=(5, 3))
        view_style.configure('ViewSelected.TButton', padding=(5, 3), relief='sunken')
        view_box = ttk.Frame(right)
        view_box.pack(anchor='w', pady=(3, 10))
        self.view_buttons = {}
        for column, name in enumerate(('斜め', '上', '正面', '右')):
            button = ttk.Button(view_box, text=name, width=7,
                                command=lambda selected=name: self.select_view(selected))
            button.grid(row=0, column=column, padx=(0 if column == 0 else 3, 0))
            self.view_buttons[name] = button
            self.controls.append((button, 'normal'))
        self.update_view_buttons()
        ttk.Label(right, text='モデル原点').pack(anchor='w', pady=(2, 3))
        self.origin = ttk.Combobox(right, state='readonly', values=ORIGIN_OPTIONS, width=19)
        self.origin.pack(anchor='w', pady=(0, 10))
        self.origin.bind('<<ComboboxSelected>>', self.select_origin)
        self.controls.append((self.origin, 'readonly'))
        self.rotation = self.fields(right, '回転（度）', (0, 0, 0), quick=True)
        self.offset = self.fields(right, '移動（mm）', (0, 0, 0))
        self.scale = tk.StringVar()
        ttk.Label(right, textvariable=self.scale).pack(anchor='w', pady=(8, 6))
        self.update_button = ttk.Button(right, text='プレビュー更新', command=self.render)
        self.update_button.pack(fill='x', pady=4)
        self.controls.append((self.update_button, 'normal'))
        reset = ttk.Button(right, text='元の位置へ戻す', command=self.reset)
        reset.pack(fill='x', pady=4)
        self.controls.append((reset, 'normal'))
        ttk.Label(right, text='倍率は元データを保持します。\n確定前の編集は試験用データです。', wraplength=230).pack(anchor='w', pady=12)
        self.status = tk.StringVar(value='')
        ttk.Label(box, textvariable=self.status, wraplength=840).pack(anchor='w')
        footer = ttk.Frame(box)
        footer.pack(fill='x', pady=(8, 0))
        ttk.Button(footer, text='キャンセル', command=self.cancel).pack(side='left')
        self.confirm = ttk.Button(footer, text='この位置で取り込む', command=self.accept, state='disabled')
        self.confirm.pack(side='right')
        self.load_values()
        self.startup_id = self.window.after(50, self.render)
        self.pump_id = self.window.after(100, self.pump)

    def fields(self, parent, title, default, quick=False):
        ttk.Label(parent, text=title).pack(anchor='w', pady=(6, 4))
        result = []
        for axis, number in zip('XYZ', default):
            row = ttk.Frame(parent)
            row.pack(anchor='w', pady=2)
            ttk.Label(row, text=axis, width=2).pack(side='left')
            variable = tk.StringVar(value=str(number))
            spin = ttk.Spinbox(row, textvariable=variable, from_=-360 if quick else -1000,
                               to=360 if quick else 1000, increment=90 if quick else .1, width=9)
            spin.pack(side='left')
            spin.bind('<Return>', lambda _: self.render())
            variable.trace_add('write', lambda *_args, variable=variable: self.changed(variable))
            result.append(variable)
            self.controls.append((spin, 'normal'))
            if quick:
                button = ttk.Button(row, text='+90°', width=6, command=lambda v=variable: self.turn(v))
                button.pack(side='left', padx=4)
                self.controls.append((button, 'normal'))
        return result

    def changed(self, variable=None):
        if self.loading:
            return
        if variable in self.offset:
            self.origin.set('手動')
        self.dirty = True
        if hasattr(self, 'confirm'):
            self.confirm.configure(state='disabled')
            self.status.set('数値を変更しました。「プレビュー更新」で反映してください。')

    def load_values(self):
        values = self.session.get(self.current)
        self.loading = True
        try:
            for key, variables in [('rotation', self.rotation), ('offset', self.offset)]:
                for variable, value in zip(variables, values[key]):
                    variable.set(format(value, '.9g'))
            self.origin.set(self.session.origins[self.current])
            self.scale.set('倍率: ' + ', '.join(format(v, '.6g') for v in values['scale']))
        finally:
            self.loading = False
        self.dirty = False

    def save_values(self):
        self.session.update(self.current, [float(v.get()) for v in self.rotation],
                            [float(v.get()) for v in self.offset], self.origin.get())
        self.dirty = False

    def update_view_buttons(self):
        for name, button in self.view_buttons.items():
            button.configure(style='ViewSelected.TButton' if name == self.view.get() else 'View.TButton')

    def select_view(self, name):
        self.view.set(name)
        self.update_view_buttons()
        self.render()

    def select_origin(self, _=None):
        try:
            self.save_values()
            self.session.set_origin(self.current, self.origin.get())
            self.load_values()
            self.render()
        except (TypeError, ValueError) as exc:
            self.status.set('原点を変更できません: ' + str(exc))

    def select(self, _=None):
        try:
            self.save_values()
        except ValueError as exc:
            self.selection.current(self.current)
            self.status.set(str(exc))
            return
        self.current = self.selection.current()
        self.load_values()
        self.render()

    def turn(self, variable):
        try:
            variable.set(format((float(variable.get()) + 90) % 360, '.9g'))
            self.render()
        except ValueError:
            self.status.set('角度は数値で入力してください')

    def reset(self):
        self.session.reset(self.current)
        self.load_values()
        self.render()

    def controls_enabled(self, enabled):
        for control, state in self.controls:
            control.configure(state=state if enabled else 'disabled')

    def render(self):
        if self.running or self.closing or self.closed:
            return
        try:
            self.save_values()
        except ValueError as exc:
            self.status.set('入力エラー: ' + str(exc))
            return
        self.running = True
        self.controls_enabled(False)
        self.confirm.configure(state='disabled')
        self.status.set('レンダリング中…')
        entry, view = self.current, self.view.get()

        def work():
            try:
                self.results.put(('ok', self.session.render(entry, view)))
            except Exception as exc:
                self.results.put(('error', str(exc)))

        threading.Thread(target=work, daemon=False).start()

    def pump(self):
        if self.closed:
            return
        try:
            kind, data = self.results.get_nowait()
        except queue.Empty:
            pass
        else:
            self.running = False
            if not self.closing:
                self.controls_enabled(True)
                if kind == 'ok':
                    filename, signature, png = data
                    try:
                        self.image = tk.PhotoImage(data=base64.b64encode(png), master=self.window)
                        self.canvas.delete('all')
                        self.canvas.create_image(280, 240, image=self.image)
                        self.session.accept_preview(filename, signature)
                        reviewed = sum(self.session.reviewed.get(n) == self.session.signature(n) for n in self.session.trees)
                        self.status.set(f'プレビュー {reviewed}/{len(self.session.trees)} 件。端子・1番ピン・高さを確認してください。'
                                        + (' 上の一覧から残りを選択してください。' if not self.session.ready() else ''))
                        self.confirm.configure(state='normal' if self.session.ready() else 'disabled')
                    except tk.TclError as exc:
                        self.status.set('画像の表示に失敗しました: ' + str(exc))
                else:
                    self.status.set('レンダリング失敗: ' + data[:350])
        if self.closing and not self.running:
            self.finish(None)
            return
        self.pump_id = self.window.after(100, self.pump)

    def accept(self):
        if self.running or self.dirty or self.closing or not self.session.ready():
            return
        try:
            self.finish(self.session.save())
        except Exception as exc:
            self.status.set('確定できません: ' + str(exc))

    def cancel(self):
        if self.closed:
            return
        self.closing = True
        self.controls_enabled(False)
        self.confirm.configure(state='disabled')
        self.status.set('キャンセル中… レンダリングの終了を待っています。')
        if not self.running:
            self.finish(None)

    def finish(self, result):
        self.closed = True
        for job in (self.startup_id, self.pump_id):
            self.window.after_cancel(job)
        self.window.grab_release()
        self.window.destroy()
        self.complete(result)
