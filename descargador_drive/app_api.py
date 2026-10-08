"""Puente entre la ventana (HTML) y el descargador.

Cada método público de Api se llama desde JavaScript con
`window.pywebview.api.<método>(...)` y devuelve datos simples (JSON).
"""

import json
import os
import re
import threading
import webbrowser

import drive_core

CONFIG = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                      "descargador_drive.json")
KEY_RE = re.compile(r"AIza[0-9A-Za-z_-]{35}")
MAX_LOG = 2000


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


def read_clipboard():
    """Texto del portapapeles de Windows (vacío en otros sistemas)."""
    if os.name != "nt":
        return ""
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    if not user32.OpenClipboard(None):
        return ""
    try:
        handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(ptr) if ptr else ""
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def mask_key(key):
    return f"{key[:4]}…{key[-4:]}" if key else ""


def eta_text(seconds):
    if seconds >= 3600:
        return f"{seconds // 3600} h {seconds % 3600 // 60} min"
    return f"{max(1, seconds // 60)} min"


class Api:
    def __init__(self):
        cfg = load_config()
        self._key = cfg.get("api_key", "")
        self._dest = cfg.get("dest") or os.path.join(os.path.expanduser("~"), "Downloads")
        self._workers = int(cfg.get("workers", 3))
        self._window = None
        self._downloader = None
        self._thread = None
        self._result = None
        self._logs = []
        self._log_base = 0  # número de líneas descartadas al principio
        self._lock = threading.Lock()

    def set_window(self, window):
        self._window = window

    def _save(self):
        save_config({"api_key": self._key, "dest": self._dest, "workers": self._workers})

    def _log(self, line):
        with self._lock:
            self._logs.append(line)
            if len(self._logs) > MAX_LOG:
                drop = len(self._logs) - MAX_LOG
                del self._logs[:drop]
                self._log_base += drop

    def _running(self):
        return bool(self._thread and self._thread.is_alive())

    # -- estado y ajustes ------------------------------------------------------

    def get_state(self):
        return {"has_key": bool(self._key), "key_mask": mask_key(self._key),
                "dest": self._dest, "workers": self._workers, "running": self._running()}

    def open_url(self, url):
        if isinstance(url, str) and url.startswith("https://"):
            webbrowser.open(url)

    def paste(self):
        try:
            return read_clipboard().strip()
        except Exception:
            return ""

    def pick_folder(self):
        if self._window is not None and not self._running():
            import webview
            result = self._window.create_file_dialog(webview.FOLDER_DIALOG,
                                                     directory=self._dest)
            if result:
                chosen = result[0] if isinstance(result, (list, tuple)) else result
                self._dest = os.path.normpath(chosen)
                self._save()
        return self._dest

    def set_workers(self, n):
        try:
            self._workers = max(1, min(8, int(n)))
        except (TypeError, ValueError):
            pass
        self._save()
        return self._workers

    def open_dest(self):
        path = self._dest
        d = self._downloader
        if d is not None and getattr(d, "base_dir", None) and os.path.isdir(d.base_dir):
            path = d.base_dir
        if os.path.isdir(path) and hasattr(os, "startfile"):
            os.startfile(path)

    # -- API key ---------------------------------------------------------------

    def check_key(self, key):
        key = re.sub(r"\s+", "", key or "")
        if not KEY_RE.fullmatch(key):
            return {"ok": False, "reason": "Eso no parece una API key. Empiezan con «AIza» "
                                           "(con i mayúscula) y tienen 39 caracteres."}
        ok, reason = drive_core.check_api_key(key)
        if ok is False:
            return {"ok": False, "reason": reason}
        self._key = key
        self._save()
        warning = (f"{reason} La guardé igual; si no funciona, cámbiala en Ajustes."
                   if ok is None else "")
        return {"ok": True, "warning": warning, "key_mask": mask_key(key)}

    # -- descarga --------------------------------------------------------------

    def start(self, link):
        link = (link or "").strip()
        if self._running():
            return {"ok": False, "error": "Ya hay una descarga en curso."}
        if not link:
            return {"ok": False, "error": "Pega aquí el link de la carpeta de Drive."}
        if not self._key:
            return {"ok": False, "error": "Falta tu API key.", "need_key": True}
        try:
            d = drive_core.Downloader(link, self._dest, self._key, self._workers, log=self._log)
        except ValueError:
            return {"ok": False, "error": "Ese link no es de Google Drive. Cópialo desde Drive "
                                          "con «Compartir → Copiar enlace»."}
        with self._lock:
            self._logs, self._log_base = [], 0
        self._downloader, self._result = d, None
        self._thread = threading.Thread(target=self._run, args=(d,), daemon=True)
        self._thread.start()
        return {"ok": True}

    def _run(self, d):
        try:
            failed = d.run()
            if failed is None:
                self._result = {"kind": "paused"}
            else:
                self._result = {"kind": "done",
                                "failed": ["/".join(f.folder + [f.name]) for f in failed]}
        except Exception as e:  # carpeta privada, inexistente, etc.
            self._result = {"kind": "error", "message": str(e)}

    def stop(self):
        if self._downloader:
            self._downloader.stop()

    def on_closing(self):
        # Al cerrar se pausa: la próxima vez sigue donde se quedó.
        self.stop()

    def poll(self, since=0):
        """Estado de la descarga y las líneas de registro nuevas desde `since`."""
        with self._lock:
            start = max(0, int(since or 0) - self._log_base)
            new = self._logs[start:]
            count = self._log_base + len(self._logs)
        out = {"running": self._running(), "result": self._result, "log": new,
               "log_count": count}
        d = self._downloader
        if d is None:
            return out
        s = d.stats
        speed = s.speed()
        percent = s.percent()
        with s.lock:
            active = [{"name": n, "done": w, "total": t,
                       "folder": s.active_folder.get(i, "")}
                      for i, (n, w, t) in s.active.items()]
            completed = list(s.completed)[-12:]
            out.update({
                "phase": s.phase, "percent": round(percent, 1), "root": s.root_name,
                "total_files": s.total_files, "done_files": s.done_files,
                "failed": len(s.failed), "done_bytes": s.done_bytes,
                "total_bytes": s.total_bytes, "sizes_known": s.sizes_known,
            })
        for a in active:
            a["done_h"] = drive_core.human_size(a["done"])
            a["total_h"] = drive_core.human_size(a["total"]) if a["total"] else ""
            a["pct"] = round(100 * a["done"] / a["total"], 1) if a["total"] else None
        for c in completed:
            c["size_h"] = drive_core.human_size(c["size"])
        out["active"] = active
        out["completed"] = completed[::-1]
        out["done_h"] = drive_core.human_size(out["done_bytes"])
        out["total_h"] = (drive_core.human_size(out["total_bytes"])
                          if out["sizes_known"] else "")
        out["speed_h"] = drive_core.human_size(speed) + "/s"
        eta = ""
        if out["sizes_known"] and speed > 0 and out["done_bytes"] < out["total_bytes"]:
            eta = eta_text(int((out["total_bytes"] - out["done_bytes"]) / speed))
        out["eta"] = eta
        return out
