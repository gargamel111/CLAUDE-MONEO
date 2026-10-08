"""Pruebas del puente entre la ventana y el descargador (sin abrir ventana)."""

import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import app_api  # noqa: E402
import drive_core  # noqa: E402
from test_core import FILES, Handler, State  # noqa: E402

KEY = "AIza" + "x" * 35


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = (app_api.CONFIG, drive_core.API_URL, drive_core.USERCONTENT_URL,
                      drive_core.DOCS_URL, drive_core.MAX_BACKOFF)
        app_api.CONFIG = os.path.join(self.tmp, "config.json")
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.srv.state = State()
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{self.srv.server_port}"
        drive_core.API_URL = base + "/drive/v3"
        drive_core.USERCONTENT_URL = base + "/download"
        drive_core.DOCS_URL = base
        drive_core.MAX_BACKOFF = 0.05

    def tearDown(self):
        self.srv.shutdown()
        (app_api.CONFIG, drive_core.API_URL, drive_core.USERCONTENT_URL,
         drive_core.DOCS_URL, drive_core.MAX_BACKOFF) = self.saved
        shutil.rmtree(self.tmp)

    def test_validaciones(self):
        api = app_api.Api()
        self.assertTrue(api.start("")["error"])
        self.assertTrue(api.start("https://drive.google.com/drive/folders/root").get("need_key"))
        self.assertFalse(api.check_key("hola")["ok"])
        api._key = KEY
        self.assertIn("no es de Google Drive", api.start("https://example.com/x")["error"])
        self.assertEqual(api.set_workers(99), 8)
        self.assertEqual(api.set_workers(0), 1)

    def test_check_key_guarda_la_clave(self):
        api = app_api.Api()
        res = api.check_key(" " + KEY + "\n")
        self.assertTrue(res["ok"], res)
        self.assertEqual(app_api.Api().get_state()["key_mask"], "AIza…xxxx")

    def test_descarga_completa_y_poll(self):
        api = app_api.Api()
        api._key, api._dest = KEY, self.tmp
        self.assertTrue(api.start("https://drive.google.com/drive/folders/root")["ok"])
        seen_running = False
        for _ in range(200):
            s = api.poll(0)
            seen_running |= s["running"]
            if not s["running"] and s["result"]:
                break
            time.sleep(0.05)
        self.assertEqual(s["result"], {"kind": "done", "failed": []})
        self.assertEqual(s["percent"], 100.0)
        self.assertEqual(s["root"], "Mi Carpeta: pruebas")
        self.assertEqual(s["done_files"], 5)
        names = {c["name"] for c in s["completed"]}
        self.assertIn("c.bin", names)
        self.assertTrue(s["log"])
        self.assertEqual(api.poll(s["log_count"])["log"], [])
        with open(os.path.join(self.tmp, "Mi Carpeta_ pruebas", "grande & pesado.bin"), "rb") as fh:
            self.assertEqual(fh.read(), FILES["bigB"])


if __name__ == "__main__":
    unittest.main()
