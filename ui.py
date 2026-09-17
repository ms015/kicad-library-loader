"""Small native Windows UI. Conversion and polling run on one background worker."""
import os
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext, ttk

from loader import Engine, Watcher


def launch(settings):
    window = tk.Tk()
    window.title('KiCad Library Loader')
    window.geometry('850x560')
    window.minsize(700, 420)
    style = ttk.Style()
    style.theme_use('vista' if 'vista' in style.theme_names() else 'clam')
    style.configure('.', font=('Yu Gothic UI', 10))
    box = ttk.Frame(window, padding=18)
    box.pack(fill='both', expand=True)
    ttk.Label(box, text='KiCad Library Loader', font=('Yu Gothic UI', 20, 'bold')).pack(anchor='w')
    ttk.Label(box, text='CSE / UltraLibrarian / SnapEDA個別ZIP → KiCad 10    •    LCSC番号入力').pack(anchor='w', pady=(0, 12))
    ttk.Label(box, text='監視先: ' + settings['watch_folder']).pack(anchor='w')
    ttk.Label(box, text='保存先: ' + settings['library_root']).pack(anchor='w')
    ttk.Label(box, text='起動中はサブフォルダも監視します。3Dモデル付きは位置合わせの確認後に登録します。').pack(anchor='w', pady=(3, 10))
    events, jobs = queue.Queue(), queue.Queue()
    stop, watching = threading.Event(), threading.Event()
    watching.set()
    active_dialog = [None]

    def review(output, service):
        request = dict(kind='alignment', output=output, service=service,
                       ready=threading.Event(), result=None)
        events.put(request)
        request['ready'].wait()
        return request['result']

    engine = Engine(settings, events.put, reviewer=review)
    status = tk.StringVar(value='監視中')
    toolbar = ttk.Frame(box)
    toolbar.pack(fill='x', pady=4)

    def choose_zip():
        paths = filedialog.askopenfilenames(title='CSE / UltraLibrarian / SnapEDA個別ZIP', filetypes=[('ZIP', '*.zip')])
        for path in paths:
            jobs.put(('zip', path))

    def toggle():
        if watching.is_set():
            watching.clear()
            pause.configure(text='監視を再開')
            status.set('監視停止中')
        else:
            watching.set()
            pause.configure(text='監視を停止')
            status.set('監視中')

    ttk.Button(toolbar, text='ZIPを選んで取り込む', command=choose_zip).pack(side='left')
    pause = ttk.Button(toolbar, text='監視を停止', command=toggle)
    pause.pack(side='left', padx=8)
    ttk.Button(toolbar, text='保存先を開く', command=lambda: os.startfile(settings['library_root'])).pack(side='left')
    lcsc = ttk.Frame(box)
    lcsc.pack(fill='x', pady=10)
    ttk.Label(lcsc, text='LCSC番号').pack(side='left')
    part = ttk.Entry(lcsc, width=24)
    part.pack(side='left', padx=8)

    def add_lcsc():
        value = part.get().strip()
        if value:
            jobs.put(('lcsc', value))
            part.delete(0, 'end')

    part.bind('<Return>', lambda _: add_lcsc())
    ttk.Button(lcsc, text='取得して登録', command=add_lcsc).pack(side='left')
    ttk.Label(lcsc, text='例: C2040').pack(side='left', padx=10)
    ttk.Label(box, textvariable=status).pack(anchor='w')
    log = scrolledtext.ScrolledText(box, height=12, font=('Yu Gothic UI', 10), state='disabled')
    log.pack(fill='both', expand=True, pady=(6, 0))
    busy = threading.Event()

    def worker():
        try:
            watcher = Watcher(engine)
            while not stop.is_set():
                try:
                    job = jobs.get(timeout=settings['poll_seconds'])
                except queue.Empty:
                    job = None
                if stop.is_set():
                    break
                busy.set()
                try:
                    if job:
                        events.put('処理中: ' + job[1])
                        if job[0] == 'zip':
                            engine.import_zip(job[1], review_existing=True)
                        else:
                            engine.import_lcsc(job[1])
                    elif watching.is_set():
                        watcher.scan()
                except Exception as exc:
                    events.put('エラー: ' + str(exc))
                finally:
                    busy.clear()
        except Exception as exc:
            events.put('監視を開始できません: ' + str(exc))

    thread = threading.Thread(target=worker, daemon=False)
    thread.start()

    def pump():
        while True:
            try:
                text = events.get_nowait()
            except queue.Empty:
                break
            if isinstance(text, dict) and text.get('kind') == 'alignment':
                def complete(result, request=text):
                    active_dialog[0] = None
                    request['result'] = result
                    request['ready'].set()
                if stop.is_set():
                    complete(None)
                else:
                    try:
                        from alignment_ui import AlignmentDialog
                        active_dialog[0] = AlignmentDialog(window, text['output'], text['service'], settings, complete)
                    except Exception as exc:
                        events.put('位置合わせ画面を開けません: ' + str(exc))
                        complete(None)
                continue
            log.configure(state='normal')
            log.insert('end', text + '\n')
            log.see('end')
            log.configure(state='disabled')
        if stop.is_set() and not thread.is_alive():
            window.destroy()
            return
        window.after(150, pump)

    def close():
        watching.clear()
        stop.set()
        if active_dialog[0] is not None:
            active_dialog[0].cancel()
        status.set('終了中 — 実行中の変換が完了するまでお待ちください')
        jobs.put(None)

    window.protocol('WM_DELETE_WINDOW', close)
    pump()
    window.mainloop()
