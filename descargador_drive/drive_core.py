"""Lógica para listar y descargar carpetas públicas de Google Drive.

Diseñado para no rendirse nunca:
  * Cada archivo se baja a un ".part" y se reanuda desde donde se quedó
    (cabecera HTTP Range) si se cae la conexión.
  * Los errores de red se reintentan sin límite, con espera creciente.
  * Si Drive dice "cuota excedida", ese archivo se aparca y se reintenta
    más tarde mientras se siguen bajando los demás.
  * Los archivos ya terminados se saltan, así que volver a ejecutar con el
    mismo link y la misma carpeta continúa donde se quedó.
"""

import errno
import html
import os
import queue
import random
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional
from urllib.parse import parse_qs, urlparse

import requests

# URLs base (se pueden cambiar en las pruebas).
EMBEDDED_URL = "https://drive.google.com/embeddedfolderview"
USERCONTENT_URL = "https://drive.usercontent.google.com/download"
DOCS_URL = "https://docs.google.com"
API_URL = "https://www.googleapis.com/drive/v3"

FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"

# Documentos nativos de Google: extensión, formato para la API y URL sin API.
EXPORTS = {
    "document": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                 "/document/d/{id}/export?format=docx"),
    "spreadsheet": ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "/spreadsheets/d/{id}/export?format=xlsx"),
    "presentation": ("pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                     "/presentation/d/{id}/export/pptx"),
    "drawing": ("png", "image/png", "/drawings/d/{id}/export/png"),
}

CHUNK = 1024 * 1024
TIMEOUT = (30, 90)  # (conectar, sin recibir datos)
MAX_BACKOFF = 60
QUOTA_WAIT = 10 * 60
MAX_HARD_FAILURES = 6  # 403/404 repetidos antes de darlo por perdido

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


class QuotaError(Exception):
    """Drive limitó temporalmente las descargas de este archivo."""


class HardError(Exception):
    """Error que probablemente no se arregla reintentando (sin permiso, no existe)."""


class Stopped(Exception):
    """El usuario pidió detener."""


@dataclass
class Item:
    id: str
    name: str
    kind: str  # "file", "folder" o una clave de EXPORTS
    folder: List[str] = field(default_factory=list)  # ruta relativa ya saneada
    size: Optional[int] = None
    resource_key: Optional[str] = None


# --------------------------------------------------------------------------
# Utilidades
# --------------------------------------------------------------------------

def parse_link(link):
    """Devuelve (id, es_carpeta, resource_key) a partir de un link de Drive."""
    link = link.strip()
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", link):
        return link, True, None
    url = urlparse(link)
    qs = parse_qs(url.query)
    rkey = qs.get("resourcekey", [None])[0]
    m = re.search(r"/folders/([A-Za-z0-9_-]+)", url.path)
    if m:
        return m.group(1), True, rkey
    m = re.search(r"/d/([A-Za-z0-9_-]+)", url.path)
    if m:
        return m.group(1), False, rkey
    if "id" in qs:
        return qs["id"][0], "folder" in link, rkey
    raise ValueError("No reconozco ese link de Google Drive.")


_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


def safe_name(name, max_len=180):
    """Nombre válido en Windows."""
    name = _INVALID.sub("_", name).strip().rstrip(". ")
    if not name:
        name = "_"
    if name.split(".")[0].upper() in _RESERVED:
        name = "_" + name
    if len(name) > max_len:
        root, ext = os.path.splitext(name)
        ext = ext[:20]
        name = root[:max_len - len(ext)].rstrip(". ") + ext
    return name


def long_path(path):
    """En Windows evita el límite de 260 caracteres en rutas."""
    path = os.path.abspath(path)
    if os.name == "nt" and not path.startswith("\\\\?\\"):
        if path.startswith("\\\\"):
            return "\\\\?\\UNC\\" + path[2:]
        return "\\\\?\\" + path
    return path


def human_size(n):
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024


def is_disk_full(exc):
    return (getattr(exc, "errno", None) == errno.ENOSPC
            or getattr(exc, "winerror", None) in (39, 112))


class KeepAwake:
    """Evita que Windows se suspenda mientras descarga."""

    def __enter__(self):
        if os.name == "nt":
            try:
                import ctypes
                # ES_CONTINUOUS | ES_SYSTEM_REQUIRED
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
            except Exception:
                pass
        return self

    def __exit__(self, *exc):
        if os.name == "nt":
            try:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
            except Exception:
                pass


# --------------------------------------------------------------------------
# Estado compartido con la interfaz
# --------------------------------------------------------------------------

class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.phase = "Preparando"
        self.total_files = 0
        self.done_files = 0
        self.skipped_files = 0
        self.failed: List[Item] = []
        self.total_bytes = 0  # sólo de archivos con tamaño conocido
        self.done_bytes = 0
        self.session_bytes = 0
        self.active = {}  # id -> (nombre, bajado, total)
        self.sizes_known = False  # con API key se sabe el tamaño de todo desde el inicio
        self._speed_samples = []

    def percent(self):
        """Avance total de 0 a 100."""
        with self.lock:
            if self.phase == "Terminado" and not self.failed:
                return 100.0
            if self.total_files == 0:
                return 0.0
            if self.sizes_known and self.total_bytes > 0:
                return min(100.0, 100.0 * self.done_bytes / self.total_bytes)
            # Sin tamaños: cada archivo vale lo mismo y los que se están
            # bajando cuentan por la parte que ya llevan.
            partial = sum(w / t for _, w, t in self.active.values() if t)
            done = self.done_files + len(self.failed) + min(partial, len(self.active))
            return min(100.0, 100.0 * done / self.total_files)

    def add_bytes(self, item, n, written, total):
        with self.lock:
            self.session_bytes += n
            self.done_bytes += n
            self.active[item.id] = (item.name, written, total)

    def speed(self):
        now = time.monotonic()
        with self.lock:
            self._speed_samples.append((now, self.session_bytes))
            self._speed_samples = [s for s in self._speed_samples if now - s[0] <= 5]
            first = self._speed_samples[0]
        dt = now - first[0]
        return (self.session_bytes - first[1]) / dt if dt > 0.5 else 0.0


# --------------------------------------------------------------------------
# Descargador
# --------------------------------------------------------------------------

class Downloader:
    def __init__(self, link, dest, api_key="", workers=3,
                 log: Callable[[str], None] = print):
        self.root_id, self.is_folder, self.root_rkey = parse_link(link)
        self.dest = dest
        self.api_key = api_key.strip()
        self.workers = max(1, int(workers))
        self._log = log
        self.stats = Stats()
        self.stop_event = threading.Event()
        self._local = threading.local()
        self._log_file = None

    # -- infraestructura -----------------------------------------------------

    def log(self, msg):
        line = time.strftime("%H:%M:%S ") + msg
        self._log(line)
        if self._log_file:
            try:
                self._log_file.write(line + "\n")
                self._log_file.flush()
            except OSError:
                pass

    def stop(self):
        self.stop_event.set()

    def _session(self):
        s = getattr(self._local, "session", None)
        if s is None:
            s = requests.Session()
            s.headers["User-Agent"] = USER_AGENT
            self._local.session = s
        return s

    def _reset_session(self):
        s = getattr(self._local, "session", None)
        if s is not None:
            s.close()
        self._local.session = None

    def _sleep(self, seconds):
        if self.stop_event.wait(seconds):
            raise Stopped()

    def _retry(self, what, fn):
        """Ejecuta fn() reintentando para siempre los errores de red/servidor."""
        attempt = 0
        while True:
            if self.stop_event.is_set():
                raise Stopped()
            try:
                return fn()
            except (Stopped, HardError, QuotaError):
                raise
            except Exception as e:
                attempt += 1
                wait = min(MAX_BACKOFF, 2 ** min(attempt, 6))
                wait += random.uniform(0, wait * 0.25)
                self.log(f"Error {what}: {e!s:.200} — reintento {attempt} en {wait:.0f}s")
                self._reset_session()
                self._sleep(wait)

    def _get(self, url, **kw):
        kw.setdefault("timeout", TIMEOUT)
        r = self._session().get(url, **kw)
        if r.status_code in (429,) or r.status_code >= 500:
            r.close()
            raise requests.HTTPError(f"HTTP {r.status_code}")
        return r

    # -- listado ---------------------------------------------------------------

    def _list_children(self, folder_id, rkey):
        if self.api_key:
            return self._list_api(folder_id, rkey)
        return self._list_embedded(folder_id, rkey)

    def _list_api(self, folder_id, rkey):
        items, token = [], None
        headers = {"X-Goog-Drive-Resource-Keys": f"{folder_id}/{rkey}"} if rkey else {}
        while True:
            params = {
                "q": f"'{folder_id}' in parents and trashed=false",
                "fields": "nextPageToken,files(id,name,mimeType,size,resourceKey,"
                          "shortcutDetails(targetId,targetMimeType,targetResourceKey))",
                "pageSize": 1000,
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
                "key": self.api_key,
            }
            if token:
                params["pageToken"] = token

            def fetch():
                r = self._get(f"{API_URL}/files", params=params, headers=headers)
                _check_api(r)
                r.raise_for_status()
                return r.json()

            data = self._retry("listando carpeta", fetch)
            for f in data.get("files", []):
                mime, fid, frkey = f["mimeType"], f["id"], f.get("resourceKey")
                if mime == SHORTCUT_MIME and "shortcutDetails" in f:
                    sd = f["shortcutDetails"]
                    mime, fid = sd.get("targetMimeType", ""), sd["targetId"]
                    frkey = sd.get("targetResourceKey")
                if mime == FOLDER_MIME:
                    kind = "folder"
                elif mime.startswith("application/vnd.google-apps."):
                    kind = mime.rsplit(".", 1)[1]
                    if kind not in EXPORTS:
                        self.log(f"Omitido (tipo de Google no descargable): {f['name']}")
                        continue
                else:
                    kind = "file"
                size = int(f["size"]) if f.get("size") is not None else None
                items.append(Item(fid, f["name"], kind, size=size, resource_key=frkey))
            token = data.get("nextPageToken")
            if not token:
                return items

    def _list_embedded(self, folder_id, rkey):
        params = {"id": folder_id}
        if rkey:
            params["resourcekey"] = rkey

        def fetch():
            r = self._get(EMBEDDED_URL, params=params)
            if 400 <= r.status_code < 500 and r.status_code != 408:
                raise HardError(
                    f"HTTP {r.status_code}: la carpeta no existe o no es pública "
                    "(debe estar compartida como 'Cualquier persona con el enlace')")
            r.raise_for_status()
            return r.text

        page = self._retry("listando carpeta", fetch)
        items = parse_embedded(page)
        if len(items) == 50:
            self.log("Aviso: esta carpeta muestra justo 50 elementos; si tiene más, "
                     "usa una API key (ver 'Opciones avanzadas') para listarla completa.")
        return items

    def _folder_name_embedded(self, folder_id, rkey):
        params = {"id": folder_id}
        if rkey:
            params["resourcekey"] = rkey

        def fetch():
            r = self._get(EMBEDDED_URL, params=params)
            if 400 <= r.status_code < 500 and r.status_code != 408:
                raise HardError(
                    f"HTTP {r.status_code}: la carpeta no existe o no es pública "
                    "(debe estar compartida como 'Cualquier persona con el enlace')")
            r.raise_for_status()
            return r.text

        m = re.search(r"<title>(.*?)</title>", self._retry("leyendo carpeta", fetch), re.S)
        return html.unescape(m.group(1)).strip() if m else folder_id

    def _root_info(self):
        """Nombre de la carpeta (o archivo) raíz."""
        if self.api_key:
            headers = ({"X-Goog-Drive-Resource-Keys": f"{self.root_id}/{self.root_rkey}"}
                       if self.root_rkey else {})

            def fetch():
                r = self._get(f"{API_URL}/files/{self.root_id}", headers=headers, params={
                    "fields": "id,name,mimeType,size,resourceKey",
                    "supportsAllDrives": "true", "key": self.api_key})
                _check_api(r)
                r.raise_for_status()
                return r.json()

            return self._retry("leyendo carpeta", fetch)
        if self.is_folder:
            return {"name": self._folder_name_embedded(self.root_id, self.root_rkey),
                    "mimeType": FOLDER_MIME}
        return {"name": None, "mimeType": "file"}

    def list_all(self):
        info = self._root_info()
        if info.get("mimeType") != FOLDER_MIME:
            mime = info.get("mimeType", "file")
            kind = mime.rsplit(".", 1)[1] if mime.startswith("application/vnd.google-apps.") else "file"
            size = int(info["size"]) if info.get("size") is not None else None
            return info.get("name"), [Item(self.root_id, info.get("name") or "", kind,
                                           size=size, resource_key=self.root_rkey)]

        root_name = info.get("name") or self.root_id
        files, pending = [], [([], self.root_id, self.root_rkey)]
        seen = {self.root_id}
        while pending:
            path, fid, rkey = pending.pop()
            if self.stop_event.is_set():
                raise Stopped()
            self.log("Listando: /" + "/".join(path))
            try:
                children = self._list_children(fid, rkey)
            except HardError as e:
                if not path:
                    raise
                self.log(f"No se pudo listar /{'/'.join(path)}: {e}")
                continue
            # Orden estable para que los nombres repetidos se resuelvan igual
            # en cada ejecución (y así se pueda reanudar).
            children.sort(key=lambda it: (it.name, it.id))
            used = set()
            for it in children:
                name = safe_name(it.name)
                if it.kind in EXPORTS and not name.lower().endswith("." + EXPORTS[it.kind][0]):
                    name += "." + EXPORTS[it.kind][0]
                name = _dedupe(name, used)
                it.name, it.folder = name, path
                if it.kind == "folder":
                    if it.id not in seen:
                        seen.add(it.id)
                        pending.append((path + [name], it.id, it.resource_key))
                else:
                    files.append(it)
            with self.stats.lock:
                self.stats.total_files = len(files)
                self.stats.phase = f"Listando… {len(files)} archivos encontrados"
        return root_name, files

    # -- descarga --------------------------------------------------------------

    def _download_urls(self, it):
        """Lista de (url, params, headers) a probar para bajar el archivo."""
        headers = ({"X-Goog-Drive-Resource-Keys": f"{it.id}/{it.resource_key}"}
                   if it.resource_key else {})
        if it.kind in EXPORTS:
            ext, mime, path = EXPORTS[it.kind]
            if self.api_key:
                return [(f"{API_URL}/files/{it.id}/export",
                         {"mimeType": mime, "key": self.api_key}, headers)]
            return [(DOCS_URL + path.format(id=it.id), {}, headers)]
        params = {"id": it.id, "export": "download", "confirm": "t"}
        if it.resource_key:
            params["resourcekey"] = it.resource_key
        urls = [(USERCONTENT_URL, params, headers)]
        if self.api_key:
            urls.insert(0, (f"{API_URL}/files/{it.id}",
                            {"alt": "media", "supportsAllDrives": "true",
                             "acknowledgeAbuse": "true", "key": self.api_key}, headers))
        return urls

    def _guess_file_name(self, it):
        """Sin API key no sabemos el nombre de un archivo suelto: se lo preguntamos a Drive."""
        url, params, headers = self._download_urls(it)[-1]
        try:
            r = self._get(url, params=params, headers=headers, stream=True)
        except Exception:
            return None
        try:
            cd = r.headers.get("Content-Disposition", "")
            m = re.search(r"filename\*=UTF-8''([^;]+)", cd) or re.search(r'filename="([^"]+)"', cd)
            if m:
                from urllib.parse import unquote
                return unquote(m.group(1))
            if "text/html" in r.headers.get("Content-Type", ""):
                m = re.search(r'class="uc-name-size"><a[^>]*>(.*?)</a>', r.text, re.S)
                if m:
                    return html.unescape(m.group(1)).strip()
        finally:
            r.close()
        return None

    def _download_once(self, it, final, part):
        offset = os.path.getsize(part) if os.path.exists(part) else 0
        if it.size is not None and offset > it.size:
            os.remove(part)
            with self.stats.lock:
                self.stats.done_bytes -= offset
            offset = 0
        if it.size is not None and offset == it.size and it.size > 0:
            return

        last_quota = None
        for url, params, headers in self._download_urls(it):
            try:
                self._stream(it, url, params, headers, part, offset)
                return
            except QuotaError as e:
                last_quota = e
                # La API pudo fallar por cuota de la key; probar la siguiente vía.
                continue
            except HardError:
                if url.startswith(API_URL) and it.kind not in EXPORTS:
                    continue
                raise
        if last_quota:
            raise last_quota
        raise HardError("No hay forma de descargar este archivo")

    def _stream(self, it, url, params, headers, part, offset, hops=0):
        headers = dict(headers)
        if offset and it.kind not in EXPORTS:
            headers["Range"] = f"bytes={offset}-"
        r = self._get(url, params=params, headers=headers, stream=True)
        try:
            if r.status_code == 416:
                return  # ya estaba completo
            if r.status_code in (401, 403, 404):
                body = r.text[:5000]
                if _looks_like_quota(body) or "rateLimitExceeded" in body or \
                        "downloadQuotaExceeded" in body or "userRateLimitExceeded" in body:
                    raise QuotaError("Drive limitó las descargas de este archivo")
                raise HardError(f"HTTP {r.status_code}: sin acceso o no existe")
            if 400 <= r.status_code < 500 and r.status_code != 408:
                raise HardError(f"HTTP {r.status_code}")
            r.raise_for_status()

            ctype = r.headers.get("Content-Type", "")
            if "text/html" in ctype:
                body = r.text
                if _looks_like_quota(body):
                    raise QuotaError("Drive dice que hay demasiadas descargas de este archivo")
                form = parse_confirm_form(body)
                if form and hops < 3:
                    action, fields = form
                    r.close()
                    return self._stream(it, action, fields, headers, part, offset, hops + 1)
                if re.search(r"accounts\.google\.com|ServiceLogin", body):
                    raise HardError("Archivo privado: requiere iniciar sesión")
                raise requests.HTTPError("Drive devolvió una página en lugar del archivo")

            total = None
            if r.status_code == 206:
                m = re.search(r"/(\d+)\s*$", r.headers.get("Content-Range", ""))
                if m:
                    total = int(m.group(1))
                mode = "ab"
            else:
                if offset:
                    self.log(f"El servidor no permitió reanudar, reinicio: {it.name}")
                    with self.stats.lock:
                        self.stats.done_bytes -= offset
                offset = 0
                mode = "wb"
                if r.headers.get("Content-Length") and "gzip" not in r.headers.get("Content-Encoding", ""):
                    total = int(r.headers["Content-Length"])
            if total is None:
                total = it.size
            elif it.size is None:
                with self.stats.lock:
                    self.stats.total_bytes += total
                it.size = total

            written = offset
            with open(part, mode) as fh:
                for chunk in r.iter_content(CHUNK):
                    if self.stop_event.is_set():
                        raise Stopped()
                    if not chunk:
                        continue
                    fh.write(chunk)
                    written += len(chunk)
                    self.stats.add_bytes(it, len(chunk), written, total)
            if total is not None and written < total:
                raise requests.ConnectionError(
                    f"conexión cortada ({human_size(written)} de {human_size(total)})")
        finally:
            r.close()

    def _download_file(self, it):
        folder = os.path.join(self.base_dir, *it.folder)
        final = long_path(os.path.join(folder, it.name))
        part = final + ".part"
        if os.path.exists(final) and (it.size is None or os.path.getsize(final) == it.size):
            with self.stats.lock:
                self.stats.skipped_files += 1
                self.stats.done_files += 1
                if it.size:
                    self.stats.done_bytes += it.size
            return
        os.makedirs(long_path(folder), exist_ok=True)
        if os.path.exists(part):
            with self.stats.lock:
                self.stats.done_bytes += os.path.getsize(part)

        def attempt():
            try:
                self._download_once(it, final, part)
            except OSError as e:
                if is_disk_full(e):
                    self.log("¡Disco lleno! Libera espacio; sigo intentando…")
                raise
            if it.size is not None and os.path.getsize(part) != it.size:
                raise requests.ConnectionError("tamaño incorrecto, reanudando")

        self._retry(f"en {it.name}", attempt)
        if os.path.exists(final):
            os.remove(final)
        os.replace(part, final)
        with self.stats.lock:
            self.stats.done_files += 1
            self.stats.active.pop(it.id, None)
        self.log(f"✔ {'/'.join(it.folder + [it.name])}")

    def _worker(self, tasks):
        try:
            self._work(tasks)
        finally:
            self._reset_session()

    def _work(self, tasks):
        while not self.stop_event.is_set():
            try:
                not_before, n, hard, it = tasks.get(timeout=0.5)
            except queue.Empty:
                with self.stats.lock:
                    if self._remaining == 0:
                        return
                continue
            delay = not_before - time.time()
            if delay > 0:
                # Todavía no toca: si hay otros pendientes, que vayan primero.
                tasks.put((not_before, n, hard, it))
                self.stop_event.wait(min(delay, 1.0))
                continue
            try:
                self._download_file(it)
                done = True
            except Stopped:
                return
            except QuotaError as e:
                with self.stats.lock:
                    self.stats.active.pop(it.id, None)
                self.log(f"⏸ {it.name}: {e}. Lo reintento en {QUOTA_WAIT // 60} min "
                         "y mientras sigo con los demás.")
                tasks.put((time.time() + QUOTA_WAIT, n, hard, it))
                done = False
            except HardError as e:
                with self.stats.lock:
                    self.stats.active.pop(it.id, None)
                hard += 1
                if hard >= MAX_HARD_FAILURES:
                    self.log(f"✘ {it.name}: {e}. Lo dejo como fallido.")
                    with self.stats.lock:
                        self.stats.failed.append(it)
                    done = True
                else:
                    wait = 30 * hard
                    self.log(f"⚠ {it.name}: {e}. Reintento en {wait}s.")
                    tasks.put((time.time() + wait, n, hard, it))
                    done = False
            except Exception as e:  # nunca debería pasar, pero no se cae el hilo
                with self.stats.lock:
                    self.stats.active.pop(it.id, None)
                self.log(f"⚠ Error inesperado en {it.name}: {e!r}. Reintento en 60s.")
                tasks.put((time.time() + 60, n, hard, it))
                done = False
            if done:
                with self.stats.lock:
                    self._remaining -= 1

    def run(self):
        """Lista y descarga todo. Devuelve la lista de archivos fallidos."""
        os.makedirs(long_path(self.dest), exist_ok=True)
        try:
            self._log_file = open(long_path(os.path.join(self.dest, "_descarga_drive_log.txt")),
                                  "a", encoding="utf-8")
        except OSError:
            self._log_file = None
        try:
            with KeepAwake():
                self.log(f"Iniciando descarga de {self.root_id}")
                try:
                    root_name, files = self.list_all()
                except HardError as e:
                    with self.stats.lock:
                        self.stats.phase = "Error"
                    self.log(f"✘ {e}")
                    raise
                self.base_dir = (os.path.join(self.dest, safe_name(root_name))
                                 if self.is_folder or len(files) != 1 else self.dest)
                if not self.is_folder and files and not files[0].name:
                    files[0].name = safe_name(self._guess_file_name(files[0]) or files[0].id)
                with self.stats.lock:
                    self.stats.total_files = len(files)
                    self.stats.total_bytes = sum(f.size or 0 for f in files)
                    self.stats.sizes_known = all(f.size is not None for f in files)
                    self.stats.phase = "Descargando"
                self.log(f"{len(files)} archivos en total. Guardando en: {self.base_dir}")

                tasks = queue.PriorityQueue()
                for n, it in enumerate(files):
                    tasks.put((0, n, 0, it))
                self._remaining = len(files)
                threads = [threading.Thread(target=self._worker, args=(tasks,), daemon=True)
                           for _ in range(self.workers)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
                if self.stop_event.is_set():
                    raise Stopped()
                with self.stats.lock:
                    self.stats.phase = "Terminado"
                    failed = list(self.stats.failed)
                self.log(f"Terminado. {len(files) - len(failed)} de {len(files)} archivos listos"
                         + (f", {len(failed)} fallidos." if failed else "."))
                return failed
        except Stopped:
            with self.stats.lock:
                self.stats.phase = "Detenido"
            self.log("Detenido. Vuelve a darle a Descargar para continuar donde se quedó.")
            return None
        finally:
            if self._log_file:
                self._log_file.close()
                self._log_file = None


# --------------------------------------------------------------------------
# Análisis de páginas de Drive
# --------------------------------------------------------------------------

def parse_embedded(page):
    """Extrae los elementos de la página embeddedfolderview de Drive."""
    items = []
    for chunk in re.split(r'<div class="flip-entry"', page)[1:]:
        m_id = re.search(r'id="entry-([A-Za-z0-9_-]+)"', chunk)
        m_href = re.search(r'<a href="([^"]+)"', chunk)
        m_title = re.search(r'<div class="flip-entry-title">(.*?)</div>', chunk, re.S)
        if not (m_id and m_title):
            continue
        href = html.unescape(m_href.group(1)) if m_href else ""
        fid = m_id.group(1)
        if "/folders/" in href:
            kind = "folder"
        else:
            kind = "file"
            m_doc = re.search(r"docs\.google\.com/(document|spreadsheets|presentation|drawings)/", href)
            if m_doc:
                kind = {"spreadsheets": "spreadsheet", "drawings": "drawing"}.get(
                    m_doc.group(1), m_doc.group(1))
            elif re.search(r"docs\.google\.com/forms/", href):
                continue  # los formularios no se pueden descargar
        rkey = parse_qs(urlparse(href).query).get("resourcekey", [None])[0]
        name = html.unescape(re.sub(r"<[^>]+>", "", m_title.group(1))).strip()
        items.append(Item(fid, name, kind, resource_key=rkey))
    return items


def parse_confirm_form(page):
    """Formulario de 'no se pudo analizar en busca de virus' → (action, campos)."""
    m = re.search(r'<form[^>]*id="download-form"[^>]*>(.*?)</form>', page, re.S)
    if not m:
        return None
    form = m.group(0)
    action = re.search(r'action="([^"]+)"', form)
    fields = {}
    for inp in re.finditer(r"<input[^>]*>", m.group(1)):
        tag = inp.group(0)
        n = re.search(r'name="([^"]+)"', tag)
        v = re.search(r'value="([^"]*)"', tag)
        if n:
            fields[html.unescape(n.group(1))] = html.unescape(v.group(1)) if v else ""
    url = html.unescape(action.group(1)) if action else USERCONTENT_URL
    return url, fields


def _looks_like_quota(body):
    low = body.lower()
    return ("quota exceeded" in low or "too many users have viewed or downloaded" in low
            or "cuota de descarga" in low or "demasiados usuarios" in low)


def _check_api(r):
    """Errores de la API: los de límite de velocidad se reintentan, el resto no."""
    if 400 <= r.status_code < 500 and r.status_code != 408:
        if "rateLimitExceeded" in r.text or "userRateLimitExceeded" in r.text:
            raise requests.HTTPError(f"HTTP {r.status_code}: límite de velocidad de la API")
        raise HardError(_api_error(r))


def _api_error(r):
    text = r.text
    if "API key not valid" in text or "API_KEY_INVALID" in text:
        return ("La API key no es válida. Revisa que la copiaste completa. "
                "Si la acabas de crear, espera 1 minuto y vuelve a intentar.")
    if "SERVICE_DISABLED" in text or "has not been used in project" in text:
        return ("Tu API key funciona, pero falta habilitar 'Google Drive API' en tu proyecto "
                "de Google Cloud. Habilítala, espera un par de minutos y vuelve a intentar.")
    if "API_KEY_SERVICE_BLOCKED" in text or "are blocked" in text:
        return ("Tu API key está restringida a otra API. En Google Cloud, edítala y marca "
                "'Google Drive API' en las restricciones.")
    try:
        msg = r.json()["error"]["message"]
    except Exception:
        msg = text[:200]
    if r.status_code == 404:
        msg += " (¿la carpeta está compartida como 'Cualquier persona con el enlace'?)"
    return f"HTTP {r.status_code}: {msg}"


def check_api_key(key, timeout=15):
    """Comprueba una API key pidiendo a Drive un archivo que no existe.

    Devuelve (True, "") si funciona, (False, motivo) si no, y (None, motivo)
    si no se pudo comprobar (sin internet o Google caído).
    """
    try:
        r = requests.get(f"{API_URL}/files/{'0' * 33}",
                         params={"key": key, "fields": "id"},
                         headers={"User-Agent": USER_AGENT}, timeout=timeout)
    except requests.RequestException:
        return None, "No pude comprobarla porque no hay conexión a internet."
    if r.status_code in (200, 404):
        return True, ""
    if r.status_code >= 500 or "rateLimitExceeded" in r.text:
        return None, "Google no respondió bien ahora mismo."
    return False, _api_error(r)


def _dedupe(name, used):
    candidate, n = name, 2
    root, ext = os.path.splitext(name)
    while candidate.lower() in used:
        candidate = f"{root} ({n}){ext}"
        n += 1
    used.add(candidate.lower())
    return candidate
