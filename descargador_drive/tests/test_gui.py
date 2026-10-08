"""Prueba que las pantallas se pueden armar (se salta si no hay pantalla)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import tkinter as tk
    _root = tk.Tk()
    _root.destroy()
    HAS_TK = True
except Exception:
    HAS_TK = False


@unittest.skipUnless(HAS_TK, "sin tkinter o sin pantalla")
class GuiTest(unittest.TestCase):
    def setUp(self):
        import descargador_drive as d
        self.d = d
        self.saved = d.load_config, d.save_config
        d.save_config = lambda data: None
        self.root = tk.Tk()

    def tearDown(self):
        self.d.load_config, self.d.save_config = self.saved
        self.root.destroy()

    def test_sin_key_empieza_con_el_paso_a_paso(self):
        d = self.d
        d.load_config = lambda: {}
        app = d.App(self.root)
        self.assertIsInstance(app.screen, d.Wizard)
        wiz = app.screen
        for _ in range(d.KEY_STEP):
            wiz.next()
            self.root.update()
        self.assertEqual(wiz.step, d.KEY_STEP)
        wiz.key.set("esto no es una clave")
        wiz.next()
        self.assertIn("AIza", wiz.message.cget("text"))
        self.assertEqual(wiz.step, d.KEY_STEP)

    def test_con_key_muestra_la_ventana_principal(self):
        d = self.d
        d.load_config = lambda: {"api_key": "AIza" + "x" * 35}
        app = d.App(self.root)
        self.assertNotIsInstance(app.screen, d.Wizard)
        app.set_percent(37.5)
        self.root.update()
        self.assertEqual(app.percent_label.cget("text"), "37.5%")
        app.set_percent(100, done=True)
        self.assertEqual(app.percent_label.cget("text"), "100%")
        app.start()  # sin link: muestra el aviso y no arranca
        self.assertIn("link", app.link_error.cget("text"))


if __name__ == "__main__":
    unittest.main()
