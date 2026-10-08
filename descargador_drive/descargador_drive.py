"""Ventana del Descargador de Drive: pegas el link, eliges carpeta y listo."""

import json
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import drive_core

APP_NAME = "Descargador de Drive"
CONFIG = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                      "descargador_drive.json")


def load_config():
    try:
        with open(CONFIG, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_config(data):
    try:
        with open(CONFIG, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except OSError:
        pass


class App:
    def __init__(self, root):
        self.root = root
        self.downloader = None
        self.thread = None
        self.logs = queue.Queue()
        cfg = load_config()

        root.title(APP_NAME)
        root.geometry("760x560")
        root.minsize(620, 460)

        main = ttk.Frame(root, padding=12)
        main.pack(fill="both", expand=True)
        main.columnconfigure(1, weight=1)

        ttk.Label(main, text="Link de la carpeta de Drive:").grid(row=0, column=0, sticky="w")
        self.link = tk.StringVar()
        link_entry = ttk.Entry(main, textvariable=self.link)
        link_entry.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(8, 0), pady=4)
        link_entry.focus()

        ttk.Label(main, text="Guardar en:").grid(row=1, column=0, sticky="w")
        self.dest = tk.StringVar(value=cfg.get("dest", os.path.join(
            os.path.expanduser("~"), "Downloads")))
        ttk.Entry(main, textvariable=self.dest).grid(row=1, column=1, sticky="ew",
                                                     padx=(8, 4), pady=4)
        ttk.Button(main, text="Elegir…", command=self.pick_dest).grid(row=1, column=2)

        adv = ttk.LabelFrame(main, text="Opciones avanzadas (opcional)", padding=8)
        adv.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 4))
        adv.columnconfigure(1, weight=1)
        ttk.Label(adv, text="API key de Google:").grid(row=0, column=0, sticky="w")
        self.api_key = tk.StringVar(value=cfg.get("api_key", ""))
        ttk.Entry(adv, textvariable=self.api_key, show="•").grid(row=0, column=1, sticky="ew",
                                                                 padx=8)
        ttk.Label(adv, text="Descargas a la vez:").grid(row=0, column=2, sticky="w")
        self.workers = tk.IntVar(value=cfg.get("workers", 3))
        ttk.Spinbox(adv, from_=1, to=8, width=4, textvariable=self.workers).grid(
            row=0, column=3, padx=(8, 0))
        ttk.Label(adv, foreground="#666",
                  text="Sin API key funciona igual. Úsala si la carpeta tiene muchísimos "
                       "archivos por carpeta (más de 50).").grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(4, 0))

        buttons = ttk.Frame(main)
        buttons.grid(row=3, column=0, columnspan=3, sticky="ew", pady=8)
        self.start_btn = ttk.Button(buttons, text="Descargar", command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(buttons, text="Detener", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=8)
        ttk.Button(buttons, text="Abrir carpeta", command=self.open_dest).pack(side="right")

        self.status = tk.StringVar(value="Pega un link y dale a Descargar.")
        ttk.Label(main, textvariable=self.status, font=("Segoe UI", 10, "bold")).grid(
            row=4, column=0, columnspan=3, sticky="w")
        self.progress = ttk.Progressbar(main, maximum=1000)
        self.progress.grid(row=5, column=0, columnspan=3, sticky="ew", pady=4)
        self.detail = tk.StringVar()
        ttk.Label(main, textvariable=self.detail).grid(row=6, column=0, columnspan=3, sticky="w")
        self.current = tk.StringVar()
        ttk.Label(main, textvariable=self.current, foreground="#444").grid(
            row=7, column=0, columnspan=3, sticky="w")

        log_frame = ttk.Frame(main)
        log_frame.grid(row=8, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
        main.rowconfigure(8, weight=1)
        self.log_text = tk.Text(log_frame, height=10, state="disabled", wrap="word",
                                font=("Consolas", 9))
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.tick()

    # -- acciones --------------------------------------------------------------

    def pick_dest(self):
        d = filedialog.askdirectory(initialdir=self.dest.get() or None)
        if d:
            self.dest.set(os.path.normpath(d))

    def open_dest(self):
        d = self.dest.get()
        if os.path.isdir(d) and hasattr(os, "startfile"):
            os.startfile(d)

    def start(self):
        link, dest = self.link.get().strip(), self.dest.get().strip()
        if not link:
            return messagebox.showwarning(APP_NAME, "Pega el link de la carpeta de Drive.")
        if not dest:
            return messagebox.showwarning(APP_NAME, "Elige dónde guardar.")
        try:
            workers = max(1, min(8, int(self.workers.get())))
        except (tk.TclError, ValueError):
            workers = 3
        try:
            self.downloader = drive_core.Downloader(link, dest, self.api_key.get(), workers,
                                                    log=self.logs.put)
        except ValueError as e:
            return messagebox.showerror(APP_NAME, str(e))
        save_config({"dest": dest, "api_key": self.api_key.get().strip(), "workers": workers})

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.progress.configure(value=0)
        self.thread = threading.Thread(target=self._run, args=(self.downloader,), daemon=True)
        self.thread.start()

    def _run(self, d):
        try:
            failed = d.run()
        except Exception as e:  # p. ej. link privado o carpeta inexistente
            self.logs.put(("error", str(e)))
            return
        self.logs.put(("done", failed))

    def stop(self):
        if self.downloader:
            self.downloader.stop()
            self.status.set("Deteniendo…")
            self.stop_btn.configure(state="disabled")

    def on_close(self):
        if self.thread and self.thread.is_alive():
            if not messagebox.askyesno(
                    APP_NAME, "Hay una descarga en curso. ¿Salir?\n\n"
                              "No se pierde nada: la próxima vez continúa donde se quedó."):
                return
            self.downloader.stop()
        self.root.destroy()

    # -- refresco de pantalla --------------------------------------------------

    def append_log(self, line):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        if int(self.log_text.index("end-1c").split(".")[0]) > 3000:
            self.log_text.delete("1.0", "500.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def finish(self):
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")

    def tick(self):
        try:
            while True:
                msg = self.logs.get_nowait()
                if isinstance(msg, tuple):
                    kind, value = msg
                    self.finish()
                    if kind == "error":
                        self.status.set("No se pudo empezar")
                        messagebox.showerror(APP_NAME, value)
                    elif value is None:
                        self.status.set("Detenido. Dale a Descargar para continuar.")
                    elif value:
                        self.status.set(f"Terminado con {len(value)} archivo(s) fallido(s)")
                        names = "\n".join("/".join(f.folder + [f.name]) for f in value[:15])
                        messagebox.showwarning(
                            APP_NAME, "Estos archivos no se pudieron bajar (sin permiso o "
                                      "borrados):\n\n" + names +
                                      "\n\nDale a Descargar otra vez para reintentarlos.")
                    else:
                        self.status.set("¡Listo! Todo descargado.")
                        self.progress.configure(value=1000)
                else:
                    self.append_log(msg)
        except queue.Empty:
            pass

        d = self.downloader
        if d and self.thread and self.thread.is_alive():
            s = d.stats
            speed = s.speed()
            with s.lock:
                phase, total, done = s.phase, s.total_files, s.done_files
                tb, db, failed = s.total_bytes, s.done_bytes, len(s.failed)
                active = list(s.active.values())
            if phase == "Descargando":
                if tb:
                    frac = min(1.0, db / tb)
                    self.status.set(f"Descargando… {frac * 100:.1f}%")
                else:
                    frac = done / total if total else 0
                    self.status.set(f"Descargando… {done} de {total}")
                self.progress.configure(value=frac * 1000)
                eta = ""
                if tb and speed > 0 and db < tb:
                    secs = int((tb - db) / speed)
                    eta = f" · Falta ≈ {secs // 3600}h {secs % 3600 // 60}m"
                self.detail.set(
                    f"Archivos: {done}/{total}" + (f" · Fallidos: {failed}" if failed else "")
                    + f" · {drive_core.human_size(db)}"
                    + (f" de {drive_core.human_size(tb)}" if tb else "")
                    + f" · {drive_core.human_size(speed)}/s{eta}")
                self.current.set("  |  ".join(
                    f"{n} ({drive_core.human_size(w)}"
                    + (f" / {drive_core.human_size(t)})" if t else ")")
                    for n, w, t in active[:3]))
            else:
                self.status.set(phase)
                self.progress.step(20)  # se mueve para que se note que trabaja
        self.root.after(500, self.tick)


def main():
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
