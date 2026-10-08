"""Pruebas con un servidor falso que imita a Google Drive (y se cae a propósito)."""

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import drive_core  # noqa: E402

FILES = {
    "fileA": os.urandom(300_000),
    "fileA2": os.urandom(1000),
    "bigB": os.urandom(5_000_000),
    "fileC": os.urandom(200_000),
    "quotaD": os.urandom(50_000),
}
DOC = b"PK fake docx"

ROOT_PAGE = """<html><head><title>Mi Carpeta: pruebas</title></head><body>
<div class="flip-entry" id="entry-fileA" tabindex="0"><div class="flip-entry-info">
<a href="https://drive.google.com/file/d/fileA/view?usp=drive_web" target="_blank">
<div class="flip-entry-title">a.bin</div></a></div></div>
<div class="flip-entry" id="entry-fileA2" tabindex="0"><div class="flip-entry-info">
<a href="https://drive.google.com/file/d/fileA2/view?usp=drive_web" target="_blank">
<div class="flip-entry-title">a.bin</div></a></div></div>
<div class="flip-entry" id="entry-bigB" tabindex="0"><div class="flip-entry-info">
<a href="https://drive.google.com/file/d/bigB/view?usp=drive_web" target="_blank">
<div class="flip-entry-title">grande &amp; pesado.bin</div></a></div></div>
<div class="flip-entry" id="entry-quotaD" tabindex="0"><div class="flip-entry-info">
<a href="https://drive.google.com/file/d/quotaD/view?usp=drive_web" target="_blank">
<div class="flip-entry-title">cuota.bin</div></a></div></div>
<div class="flip-entry" id="entry-docE" tabindex="0"><div class="flip-entry-info">
<a href="https://docs.google.com/document/d/docE/edit?usp=drive_web" target="_blank">
<div class="flip-entry-title">Notas</div></a></div></div>
<div class="flip-entry" id="entry-subF" tabindex="0"><div class="flip-entry-info">
<a href="https://drive.google.com/drive/folders/subF" target="_blank">
<div class="flip-entry-title">Sub: carpeta</div></a></div></div>
</body></html>"""

SUB_PAGE = """<html><head><title>Sub</title></head><body>
<div class="flip-entry" id="entry-fileC" tabindex="0"><div class="flip-entry-info">
<a href="https://drive.google.com/file/d/fileC/view?usp=drive_web" target="_blank">
<div class="flip-entry-title">c.bin</div></a></div></div></body></html>"""

CONFIRM_PAGE = """<html><body><form id="download-form" action="{base}/download" method="get">
<input type="hidden" name="id" value="bigB"><input type="hidden" name="export" value="download">
<input type="hidden" name="confirm" value="t"><input type="hidden" name="uuid" value="abc-123">
</form></body></html>"""

API_TREE = {
    "root": [("fileA", "a.bin", "application/octet-stream"),
             ("fileA2", "a.bin", "application/octet-stream"),
             ("bigB", "grande & pesado.bin", "application/octet-stream"),
             ("subF", "Sub: carpeta", drive_core.FOLDER_MIME),
             ("docE", "Notas", "application/vnd.google-apps.document")],
    "subF": [("fileC", "c.bin", "application/octet-stream")],
}


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.drops = {}  # cuántas veces se cortó cada archivo
        self.quota_hits = 0
        self.flaky_listing = 1


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="text/html; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        st = self.server.state
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/embeddedfolderview":
            with st.lock:
                if st.flaky_listing:
                    st.flaky_listing -= 1
                    return self._send(503, b"busy")
            page = {"root": ROOT_PAGE, "subF": SUB_PAGE}.get(q["id"])
            return self._send(200, page.encode()) if page else self._send(404, b"no")
        if url.path == "/download":
            fid = q["id"]
            if fid == "bigB" and "uuid" not in q:
                base = f"http://127.0.0.1:{self.server.server_port}"
                return self._send(200, CONFIRM_PAGE.format(base=base).encode())
            if fid == "quotaD":
                with st.lock:
                    st.quota_hits += 1
                    hit = st.quota_hits
                if hit <= 2:
                    return self._send(200, b"<html>Too many users have viewed or downloaded "
                                           b"this file recently</html>")
            return self._serve_file(fid)
        if url.path.startswith("/document/d/"):
            return self._send(200, DOC, "application/octet-stream")
        if url.path.startswith("/drive/v3/files"):
            return self._api(url, q)
        self._send(404, b"no")

    def _api(self, url, q):
        parts = url.path.split("/")
        if len(parts) == 4:  # /drive/v3/files  → listado
            parent = q["q"].split("'")[1]
            entries = API_TREE[parent]
            start = int(q.get("pageToken", 0))
            page = entries[start:start + 2]  # páginas chiquitas para probar paginación
            data = {"files": [{"id": i, "name": n, "mimeType": m,
                               **({"size": str(len(FILES[i]))} if i in FILES else {})}
                              for i, n, m in page]}
            if start + 2 < len(entries):
                data["nextPageToken"] = str(start + 2)
            return self._send(200, json.dumps(data).encode(), "application/json")
        fid = parts[4]
        if len(parts) == 6 and parts[5] == "export":
            return self._send(200, DOC, "application/octet-stream")
        if q.get("alt") == "media":
            return self._serve_file(fid)
        if fid == "root":
            return self._send(200, json.dumps({"id": "root", "name": "Mi Carpeta: pruebas",
                                               "mimeType": drive_core.FOLDER_MIME}).encode(),
                              "application/json")
        self._send(404, b"{}")

    def _serve_file(self, fid):
        data = FILES[fid]
        start = 0
        rng = self.headers.get("Range")
        if rng:
            start = int(rng.split("=")[1].split("-")[0])
            if start >= len(data):
                return self._send(416, b"")
        body = data[start:]
        st = self.server.state
        with st.lock:
            n = st.drops.get(fid, 0)
            drop = len(body) > 100_000 and n < 3
            if drop:
                st.drops[fid] = n + 1
        self.send_response(206 if rng else 200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        if rng:
            self.send_header("Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}")
        self.end_headers()
        if drop:
            # Manda un pedazo y corta la conexión, como cuando se cae el internet.
            self.wfile.write(body[: len(body) // 3])
            self.wfile.flush()
            self.close_connection = True
            self.connection.shutdown(2)
            return
        self.wfile.write(body)


class DriveTest(unittest.TestCase):
    def setUp(self):
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.srv.state = State()
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{self.srv.server_port}"
        self.saved = {k: getattr(drive_core, k) for k in
                      ("EMBEDDED_URL", "USERCONTENT_URL", "DOCS_URL", "API_URL",
                       "MAX_BACKOFF", "QUOTA_WAIT")}
        drive_core.EMBEDDED_URL = base + "/embeddedfolderview"
        drive_core.USERCONTENT_URL = base + "/download"
        drive_core.DOCS_URL = base
        drive_core.API_URL = base + "/drive/v3"
        drive_core.MAX_BACKOFF = 0.05
        drive_core.QUOTA_WAIT = 0.3
        self.dest = tempfile.mkdtemp()

    def tearDown(self):
        self.srv.shutdown()
        for k, v in self.saved.items():
            setattr(drive_core, k, v)
        shutil.rmtree(self.dest)

    def check_tree(self, with_quota=True):
        root = os.path.join(self.dest, "Mi Carpeta_ pruebas")
        expect = {
            "a.bin": FILES["fileA"],
            "a (2).bin": FILES["fileA2"],
            "grande & pesado.bin": FILES["bigB"],
            "Notas.docx": DOC,
            os.path.join("Sub_ carpeta", "c.bin"): FILES["fileC"],
        }
        if with_quota:
            expect["cuota.bin"] = FILES["quotaD"]
        for rel, data in expect.items():
            with open(os.path.join(root, rel), "rb") as fh:
                self.assertEqual(fh.read(), data, rel)
        leftovers = [f for _, _, fs in os.walk(root) for f in fs if f.endswith(".part")]
        self.assertEqual(leftovers, [])

    def test_sin_api_key(self):
        logs = []
        d = drive_core.Downloader("https://drive.google.com/drive/folders/root?usp=sharing",
                                  self.dest, workers=3, log=logs.append)
        self.assertEqual(d.stats.percent(), 0.0)
        failed = d.run()
        self.assertEqual(failed, [], "\n".join(logs))
        self.assertEqual(d.stats.percent(), 100.0)
        self.check_tree()
        self.assertGreaterEqual(self.srv.state.drops.get("bigB", 0), 3)

        # Segunda vez: todo ya está, no baja nada.
        d2 = drive_core.Downloader("https://drive.google.com/drive/folders/root", self.dest, log=lambda m: None)
        self.assertEqual(d2.run(), [])
        self.assertEqual(d2.stats.skipped_files, 6)
        self.assertEqual(d2.stats.session_bytes, 0)

    def test_con_api_key(self):
        logs = []
        d = drive_core.Downloader("https://drive.google.com/drive/folders/root", self.dest,
                                  api_key="KEY", workers=2, log=logs.append)
        self.assertEqual(d.run(), [], "\n".join(logs))
        self.check_tree(with_quota=False)
        self.assertEqual(d.stats.percent(), 100.0)

    def test_reanuda_part(self):
        root = os.path.join(self.dest, "Mi Carpeta_ pruebas")
        os.makedirs(root)
        with open(os.path.join(root, "grande & pesado.bin.part"), "wb") as fh:
            fh.write(FILES["bigB"][:4_000_000])
        self.srv.state.drops["bigB"] = 99  # que no se corte esta vez
        d = drive_core.Downloader("https://drive.google.com/drive/folders/root", self.dest, log=lambda m: None)
        self.assertEqual(d.run(), [])
        self.check_tree()
        # Sólo bajó lo que faltaba del grande, no los 5 MB de nuevo.
        total_rest = sum(len(v) for k, v in FILES.items() if k != "bigB") + len(DOC)
        self.assertLess(d.stats.session_bytes, total_rest + 1_100_000)

    def test_detener(self):
        d = drive_core.Downloader("https://drive.google.com/drive/folders/root", self.dest, log=lambda m: None)
        d.stop()
        self.assertIsNone(d.run())


class PercentTest(unittest.TestCase):
    def test_por_bytes_si_se_saben_los_tamanos(self):
        st = drive_core.Stats()
        st.total_files, st.total_bytes, st.done_bytes, st.sizes_known = 4, 1000, 250, True
        self.assertAlmostEqual(st.percent(), 25.0)

    def test_por_archivos_si_no(self):
        st = drive_core.Stats()
        st.total_files, st.done_files = 4, 1
        st.active = {"x": ("x", 50, 100)}  # uno a la mitad
        st.total_bytes = 100  # tamaño conocido sólo de uno: no se usa
        self.assertAlmostEqual(st.percent(), 37.5)


class UtilTest(unittest.TestCase):
    def test_parse_link(self):
        self.assertEqual(drive_core.parse_link(
            "https://drive.google.com/drive/folders/1AbC_d-9xyzXYZ?usp=sharing"),
            ("1AbC_d-9xyzXYZ", True, None))
        self.assertEqual(drive_core.parse_link(
            "https://drive.google.com/drive/u/0/folders/1AbC_d-9xyz?resourcekey=0-k"),
            ("1AbC_d-9xyz", True, "0-k"))
        self.assertEqual(drive_core.parse_link(
            "https://drive.google.com/file/d/1FiLe_abcdef/view"), ("1FiLe_abcdef", False, None))
        with self.assertRaises(ValueError):
            drive_core.parse_link("https://example.com/hola")

    def test_safe_name(self):
        self.assertEqual(drive_core.safe_name('a<b>:c"d|e?f*g'), "a_b__c_d_e_f_g")
        self.assertEqual(drive_core.safe_name("con.txt"), "_con.txt")
        self.assertEqual(drive_core.safe_name("fin. "), "fin")
        self.assertTrue(drive_core.safe_name("x" * 300 + ".mp4").endswith(".mp4"))


if __name__ == "__main__":
    unittest.main()
