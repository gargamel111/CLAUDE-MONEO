"""Descargador de Drive: abre la ventana (HTML en una ventana nativa).

La interfaz está en ui/ y habla con app_api.Api. La descarga en sí está en
drive_core.py.

    python descargador_drive.py            abre el programa
    python descargador_drive.py --smoke    abre la ventana, comprueba que cargó y cierra
"""

import os
import sys
import threading

import webview

from app_api import Api

APP_NAME = "Descargador de Drive"
WIDTH, HEIGHT = 1180, 740  # tamaño fijo; en pantallas chicas la interfaz se achica


def resource(*parts):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def window_size():
    w, h = WIDTH, HEIGHT
    try:
        screen = webview.screens[0]
        w = min(w, screen.width - 60)
        h = min(h, screen.height - 100)
    except Exception:
        pass
    # Misma proporción que el diseño, para que al achicarse no sobre espacio.
    scale = min(w / WIDTH, h / HEIGHT, 1.0)
    return int(WIDTH * scale), int(HEIGHT * scale)


def smoke_check(window):
    """Para las pruebas: espera a que la interfaz arranque y cierra."""
    ok = False
    for _ in range(60):
        try:
            if window.evaluate_js("window.__ready === true"):
                ok = True
                break
        except Exception:
            pass
        threading.Event().wait(0.5)
    print("ventana OK" if ok else "la ventana no cargó", flush=True)
    window.destroy()
    os._exit(0 if ok else 1)


def main():
    api = Api()
    w, h = window_size()
    window = webview.create_window(
        APP_NAME, resource("ui", "index.html"), js_api=api,
        width=w, height=h, resizable=False, background_color="#F4F2F3",
    )
    api.set_window(window)
    window.events.closing += api.on_closing
    if "--smoke" in sys.argv:
        webview.start(smoke_check, window, http_server=True)
    else:
        webview.start(http_server=True)


if __name__ == "__main__":
    main()
