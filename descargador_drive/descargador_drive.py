"""Ventana del Descargador de Drive.

La primera vez muestra un paso a paso, con dibujos de cada pantalla de
Google Cloud, para sacar la API key. Después: pegas el link, eliges carpeta
y descargas.
"""

import json
import os
import queue
import re
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

import drive_core

APP_NAME = "Descargador de Drive"
CONFIG = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                      "descargador_drive.json")
KEY_RE = re.compile(r"AIza[0-9A-Za-z_-]{35}")

PROJECT_URL = "https://console.cloud.google.com/projectcreate"
DRIVE_API_URL = "https://console.cloud.google.com/apis/library/drive.googleapis.com"
CREDENTIALS_URL = "https://console.cloud.google.com/apis/credentials"

FONT = "Segoe UI" if os.name == "nt" else "DejaVu Sans"
MONO = "Consolas" if os.name == "nt" else "DejaVu Sans Mono"
WRAP = 600

# Colores. El texto secundario y el azul pasan 4.5:1 sobre su fondo, y el
# botón azul lleva texto blanco a 5.6:1 en los dos modos.
LIGHT = {
    "bg": "#F2F2F5", "card": "#FFFFFF", "text": "#1D1D1F", "muted": "#5F5F66",
    "line": "#D9D9DE", "field": "#FFFFFF", "track": "#E3E3E8",
    "accent": "#0A64D6", "accent_fill": "#0A64D6", "accent_hover": "#0853B3",
    "button": "#E8E8ED", "button_hover": "#DCDCE1",
    "success": "#1D7A3A", "danger": "#C4231C",
}
DARK = {
    "bg": "#161618", "card": "#242426", "text": "#F2F2F5", "muted": "#A9A9AF",
    "line": "#3A3A3D", "field": "#1C1C1E", "track": "#3A3A3D",
    "accent": "#5AA3FF", "accent_fill": "#0A64D6", "accent_hover": "#1A74E6",
    "button": "#3A3A3D", "button_hover": "#48484C",
    "success": "#4CC16E", "danger": "#FF6B61",
}
# Marca naranja para "toca aquí" en los dibujos (se distingue del azul de Google).
MARK = "#F28C00"


# --------------------------------------------------------------------------
# Configuración guardada
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# Apariencia
# --------------------------------------------------------------------------

def system_is_dark():
    if os.environ.get("DESCARGADOR_TEMA"):
        return os.environ["DESCARGADOR_TEMA"] == "oscuro"
    if os.name != "nt":
        return False
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
        return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except Exception:
        return False


def dark_title_bar(win):
    if os.name != "nt":
        return
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        value = ctypes.c_int(1)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value),
                                                   ctypes.sizeof(value))
    except Exception:
        pass


def setup_styles(root, c):
    root.configure(bg=c["bg"])
    st = ttk.Style(root)
    st.theme_use("clam")
    st.configure(".", background=c["bg"], foreground=c["text"], font=(FONT, 10),
                 bordercolor=c["line"], troughcolor=c["track"], focuscolor=c["accent"])
    st.configure("TFrame", background=c["bg"])
    st.configure("Card.TFrame", background=c["card"])

    for name, bg in (("", c["bg"]), ("Card.", c["card"])):
        st.configure(f"{name}TLabel", background=bg, foreground=c["text"])
        st.configure(f"{name}Muted.TLabel", background=bg, foreground=c["muted"])
        st.configure(f"{name}Danger.TLabel", background=bg, foreground=c["danger"])
        st.configure(f"{name}Success.TLabel", background=bg, foreground=c["success"])
    st.configure("Title.TLabel", font=(FONT, 18, "bold"))
    st.configure("WizardTitle.TLabel", font=(FONT, 17, "bold"), background=c["card"])
    st.configure("Eyebrow.TLabel", font=(FONT, 9, "bold"), background=c["card"],
                 foreground=c["accent"])
    st.configure("Section.TLabel", font=(FONT, 10, "bold"), background=c["card"])
    st.configure("Percent.TLabel", font=(FONT, 30, "bold"), background=c["card"])
    st.configure("PercentDone.TLabel", font=(FONT, 30, "bold"), background=c["card"],
                 foreground=c["success"])
    st.configure("Body.TLabel", font=(FONT, 11), background=c["card"])
    st.configure("Status.TLabel", font=(FONT, 11), background=c["card"])
    st.configure("StatusDanger.TLabel", font=(FONT, 11), background=c["card"],
                 foreground=c["danger"])
    st.configure("StatusSuccess.TLabel", font=(FONT, 11, "bold"), background=c["card"],
                 foreground=c["success"])

    st.configure("TButton", background=c["button"], foreground=c["text"], borderwidth=0,
                 padding=(14, 8), relief="flat")
    st.map("TButton",
           background=[("disabled", c["track"]), ("pressed", c["button_hover"]),
                       ("active", c["button_hover"])],
           foreground=[("disabled", c["muted"])])
    st.configure("Accent.TButton", background=c["accent_fill"], foreground="#FFFFFF",
                 font=(FONT, 10, "bold"), padding=(20, 9))
    st.map("Accent.TButton",
           background=[("disabled", c["track"]), ("pressed", c["accent_hover"]),
                       ("active", c["accent_hover"])],
           foreground=[("disabled", c["muted"])])
    st.configure("Link.TButton", background=c["card"], foreground=c["accent"], padding=(0, 4))
    st.map("Link.TButton", background=[("active", c["card"]), ("pressed", c["card"])])
    st.configure("BgLink.TButton", background=c["bg"], foreground=c["accent"], padding=(0, 4))
    st.map("BgLink.TButton", background=[("active", c["bg"]), ("pressed", c["bg"])])

    st.configure("TEntry", fieldbackground=c["field"], foreground=c["text"],
                 insertcolor=c["text"], bordercolor=c["line"], lightcolor=c["line"],
                 darkcolor=c["line"], padding=(8, 7))
    st.map("TEntry", bordercolor=[("focus", c["accent"])], lightcolor=[("focus", c["accent"])],
           darkcolor=[("focus", c["accent"])])
    st.configure("TSpinbox", fieldbackground=c["field"], foreground=c["text"],
                 background=c["button"], arrowcolor=c["text"], bordercolor=c["line"],
                 lightcolor=c["line"], darkcolor=c["line"], insertcolor=c["text"], padding=4)
    st.configure("Vertical.TScrollbar", background=c["button"], troughcolor=c["field"],
                 bordercolor=c["field"], arrowcolor=c["muted"], lightcolor=c["button"],
                 darkcolor=c["button"])
    for name, color in (("Big", c["accent"]), ("Done", c["success"])):
        st.configure(f"{name}.Horizontal.TProgressbar", troughcolor=c["track"],
                     background=color, bordercolor=c["track"], lightcolor=color,
                     darkcolor=color, thickness=12)
    return st


def card(parent, c, **grid):
    """Tarjeta con borde fino."""
    outer = tk.Frame(parent, bg=c["card"], highlightthickness=1,
                     highlightbackground=c["line"], highlightcolor=c["line"])
    inner = ttk.Frame(outer, style="Card.TFrame", padding=20)
    inner.pack(fill="both", expand=True)
    outer.grid(**grid)
    return inner


def middle_ellipsis(text, n=60):
    return text if len(text) <= n else text[: n // 2 - 1] + "…" + text[-(n // 2 - 1):]


# --------------------------------------------------------------------------
# Dibujos de las pantallas de Google Cloud
# --------------------------------------------------------------------------

def rounded(cv, x1, y1, x2, y2, r=8, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
           x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


def mark(cv, x1, y1, x2, y2, n=None):
    """Recuadro naranja alrededor de lo que hay que tocar."""
    rounded(cv, x1 - 5, y1 - 5, x2 + 5, y2 + 5, r=10, outline=MARK, width=3, fill="")
    if n is not None:
        cv.create_oval(x2 - 4, y1 - 16, x2 + 16, y1 + 4, fill=MARK, outline=MARK)
        cv.create_text(x2 + 6, y1 - 6, text=str(n), fill="#FFFFFF", font=(FONT, 9, "bold"))


def g_button(cv, x, y, text, filled=True):
    w = tkfont.Font(family=FONT, size=9, weight="bold").measure(text) + 28
    if filled:
        rounded(cv, x, y, x + w, y + 30, r=6, fill="#1A73E8", outline="#1A73E8")
        cv.create_text(x + w / 2, y + 15, text=text, fill="#FFFFFF", font=(FONT, 9, "bold"))
    else:
        rounded(cv, x, y, x + w, y + 30, r=6, fill="#FFFFFF", outline="#DADCE0")
        cv.create_text(x + w / 2, y + 15, text=text, fill="#1A73E8", font=(FONT, 9, "bold"))
    return x, y, x + w, y + 30


def drive_logo(cv, x, y, s=1.0):
    cv.create_polygon(x + 10 * s, y, x + 20 * s, y, x + 30 * s, y + 17 * s, x + 20 * s,
                      y + 17 * s, fill="#FBBC04", outline="")
    cv.create_polygon(x + 10 * s, y, x + 15 * s, y + 9 * s, x + 5 * s, y + 26 * s, x,
                      y + 17 * s, fill="#0F9D58", outline="")
    cv.create_polygon(x + 5 * s, y + 26 * s, x + 25 * s, y + 26 * s, x + 30 * s, y + 17 * s,
                      x + 10 * s, y + 17 * s, fill="#4285F4", outline="")


def draw_screen(cv, kind, c):
    """Dibuja un estimado de la pantalla de Google Cloud de cada paso."""
    W, H = int(cv["width"]), int(cv["height"])
    cv.delete("all")
    # Ventana del navegador
    rounded(cv, 2, 2, W - 2, H - 2, r=12, fill="#FFFFFF", outline=c["line"], width=1)
    cv.create_rectangle(3, 14, W - 3, 34, fill="#F1F3F4", outline="")
    rounded(cv, 3, 3, W - 3, 34, r=11, fill="#F1F3F4", outline="")
    for i, col in enumerate(("#FF5F57", "#FEBC2E", "#28C840")):
        cv.create_oval(14 + i * 16, 13, 24 + i * 16, 23, fill=col, outline="")
    rounded(cv, 70, 9, W - 20, 28, r=9, fill="#FFFFFF", outline="")
    cv.create_text(84, 18, anchor="w", text="console.cloud.google.com", fill="#5F6368",
                   font=(FONT, 8))
    # Barra de Google Cloud
    cv.create_rectangle(3, 34, W - 3, 66, fill="#FFFFFF", outline="")
    cv.create_line(3, 66, W - 3, 66, fill="#E0E0E0")
    for i, col in enumerate(("#4285F4", "#EA4335", "#FBBC04", "#34A853")):
        cv.create_rectangle(16 + i * 6, 45, 21 + i * 6, 55, fill=col, outline="")
    cv.create_text(48, 50, anchor="w", text="Google Cloud", fill="#3C4043",
                   font=(FONT, 10, "bold"))
    rounded(cv, 180, 41, 320, 59, r=4, fill="#FFFFFF", outline="#DADCE0")
    cv.create_text(190, 50, anchor="w", text="descargador  ▾", fill="#3C4043", font=(FONT, 8))
    t = (FONT, 9)

    if kind == "project":
        cv.create_text(30, 88, anchor="w", text="Nuevo proyecto", fill="#202124",
                       font=(FONT, 12, "bold"))
        cv.create_text(30, 114, anchor="w", text="Nombre del proyecto *", fill="#5F6368", font=t)
        rounded(cv, 30, 124, 330, 152, r=4, fill="#FFFFFF", outline="#1A73E8", width=2)
        cv.create_text(42, 138, anchor="w", text="descargador", fill="#202124", font=t)
        mark(cv, 30, 124, 330, 152, 2)
        b = g_button(cv, 30, 168, "CREAR")
        mark(cv, *b, 3)
    elif kind == "enable":
        drive_logo(cv, 34, 86, 1.5)
        cv.create_text(92, 92, anchor="w", text="Google Drive API", fill="#202124",
                       font=(FONT, 13, "bold"))
        cv.create_text(92, 114, anchor="w", text="Google Enterprise API", fill="#5F6368",
                       font=t)
        b = g_button(cv, 92, 146, "HABILITAR")
        mark(cv, *b, 3)
        g_button(cv, b[2] + 18, 146, "PROBAR ESTA API", filled=False)
        mark(cv, 180, 41, 320, 59, 2)
    elif kind == "credentials":
        cv.create_text(30, 86, anchor="w", text="Credenciales", fill="#202124",
                       font=(FONT, 12, "bold"))
        x1, y1, x2, y2 = 180, 74, 370, 98
        cv.create_text(x1 + 6, (y1 + y2) / 2, anchor="w", text="+ CREAR CREDENCIALES",
                       fill="#1A73E8", font=(FONT, 9, "bold"))
        mark(cv, x1, y1, x2, y2, 2)
        rounded(cv, 180, 110, 450, H - 12, r=6, fill="#FFFFFF", outline="#DADCE0")
        cv.create_rectangle(181, 116, 449, 144, fill="#E8F0FE", outline="")
        cv.create_text(194, 130, anchor="w", text="Clave de API", fill="#202124",
                       font=(FONT, 9, "bold"))
        mark(cv, 182, 116, 448, 144, None)
        cv.create_text(194, 160, anchor="w", text="ID de cliente de OAuth", fill="#5F6368",
                       font=t)
        cv.create_text(194, 184, anchor="w", text="Cuenta de servicio", fill="#5F6368",
                       font=t)
    elif kind == "key":
        rounded(cv, 40, 76, W - 40, H - 8, r=8, fill="#FFFFFF", outline="#DADCE0")
        cv.create_text(60, 100, anchor="w", text="Se creó la clave de API", fill="#202124",
                       font=(FONT, 12, "bold"))
        cv.create_text(60, 124, anchor="w", text="Tu clave de API", fill="#5F6368", font=t)
        rounded(cv, 60, 134, W - 110, 162, r=4, fill="#F8F9FA", outline="#DADCE0")
        cv.create_text(72, 148, anchor="w", text="AIzaSyB3x••••••••••••••••••••••••Qk",
                       fill="#202124", font=(MONO, 9))
        cv.create_rectangle(W - 92, 138, W - 78, 154, outline="#1A73E8", width=2)
        cv.create_rectangle(W - 88, 142, W - 74, 158, outline="#1A73E8", width=2,
                            fill="#FFFFFF")
        mark(cv, W - 96, 136, W - 70, 160, 4)
        g_button(cv, 60, H - 44, "CERRAR", filled=False)
    elif kind == "restrict":
        cv.create_text(30, 86, anchor="w", text="Restricciones de API", fill="#202124",
                       font=(FONT, 11, "bold"))
        cv.create_oval(30, 100, 44, 114, outline="#5F6368", width=2)
        cv.create_text(52, 107, anchor="w", text="No restringir clave", fill="#5F6368",
                       font=t)
        cv.create_oval(30, 134, 44, 148, outline="#1A73E8", width=2)
        cv.create_oval(34, 138, 40, 144, fill="#1A73E8", outline="")
        cv.create_text(52, 141, anchor="w", text="Restringir clave", fill="#202124", font=t)
        mark(cv, 28, 132, 160, 150, 2)
        rounded(cv, 230, 120, 490, 160, r=4, fill="#FFFFFF", outline="#DADCE0")
        cv.create_rectangle(242, 133, 256, 147, fill="#1A73E8", outline="#1A73E8")
        cv.create_text(249, 140, text="✓", fill="#FFFFFF", font=(FONT, 8, "bold"))
        cv.create_text(266, 140, anchor="w", text="Google Drive API", fill="#202124", font=t)
        mark(cv, 232, 124, 488, 156, 3)
        b = g_button(cv, 30, H - 42, "GUARDAR")
        mark(cv, *b, None)


def draw_overview(cv, c):
    """Los cuatro pasos de un vistazo, para la pantalla de bienvenida."""
    labels = ["Crear un\nproyecto", "Activar\nDrive API", "Crear y copiar\nla clave",
              "Pegarla\naquí"]
    W = int(cv["width"])
    gap = W / len(labels)
    for i, text in enumerate(labels):
        x = gap * i + gap / 2
        if i:
            cv.create_line(x - gap + 22, 22, x - 22, 22, fill=c["line"], width=2)
        cv.create_oval(x - 18, 4, x + 18, 40, fill=c["accent_fill"], outline="")
        cv.create_text(x, 22, text=str(i + 1), fill="#FFFFFF", font=(FONT, 12, "bold"))
        cv.create_text(x, 64, text=text, fill=c["text"], font=(FONT, 10), justify="center")


# --------------------------------------------------------------------------
# Paso a paso de la API key
# --------------------------------------------------------------------------

STEPS = [
    {
        "eyebrow": "ANTES DE EMPEZAR",
        "title": "Necesitas una API key de Google (es gratis)",
        "body": "Es como un pase gratis que deja al programa ver las carpetas completas de "
                "Drive, sin límite de archivos.\n\n"
                "Se saca una sola vez, toma unos 3 minutos y queda guardada solo en esta "
                "compu. No da acceso a tu correo ni a tus archivos.\n\n"
                "Te voy guiando paso por paso. En cada paso te muestro cómo se ve la pantalla "
                "de Google y te marco en naranja dónde tocar.",
        "overview": True,
        "next": "Empezar",
    },
    {
        "title": "Crea un proyecto",
        "body": "1.  Dale a «Abrir Google Cloud» y entra con tu cuenta de Google.\n"
                "2.  Ponle cualquier nombre, por ejemplo «descargador».\n"
                "3.  Dale a «Crear» y espera unos segundos.",
        "link": ("Abrir Google Cloud", PROJECT_URL),
        "screen": "project",
        "hint": "Si es tu primera vez, Google te pide aceptar sus términos. Acéptalos y sigue.",
        "next": "Ya lo creé",
    },
    {
        "title": "Activa Google Drive API",
        "body": "1.  Dale a «Abrir Google Drive API».\n"
                "2.  Arriba, revisa que esté elegido tu proyecto «descargador».\n"
                "3.  Dale al botón azul «Habilitar».",
        "link": ("Abrir Google Drive API", DRIVE_API_URL),
        "screen": "enable",
        "hint": "Si en vez de «Habilitar» dice «Administrar», ya está activada. Sigue.",
        "next": "Ya la activé",
    },
    {
        "title": "Crea tu API key",
        "body": "1.  Dale a «Abrir Credenciales».\n"
                "2.  Arriba, dale a «+ Crear credenciales» y elige «Clave de API».\n"
                "3.  Si te pregunta qué API va a usar, elige «Google Drive API».",
        "link": ("Abrir Credenciales", CREDENTIALS_URL),
        "screen": "credentials",
        "next": "Siguiente",
    },
    {
        "title": "Copia tu API key",
        "body": "Google te muestra la clave en una ventanita.\n"
                "4.  Dale al botón de copiar que está a la derecha de la clave.",
        "screen": "key",
        "hint": "La clave empieza con «AIza» y es larga. Si cerraste la ventanita, la puedes "
                "ver de nuevo en Credenciales → «Mostrar clave».",
        "next": "Ya la copié",
    },
    {
        "title": "Protégela (recomendado)",
        "body": "Así, aunque alguien la viera, solo le serviría para Google Drive.\n"
                "1.  En Credenciales, haz clic en el nombre de tu clave.\n"
                "2.  En «Restricciones de API», elige «Restringir clave».\n"
                "3.  Marca solo «Google Drive API» y dale a «Guardar».",
        "link": ("Abrir Credenciales", CREDENTIALS_URL),
        "screen": "restrict",
        "hint": "Si en el paso 3 ya elegiste Google Drive API, ya está protegida.",
        "next": "Ya la protegí",
        "skip": "Saltar",
    },
    {
        "title": "Pega tu API key",
        "body": "Pégala aquí abajo. La voy a comprobar con Google antes de guardarla.",
        "entry": True,
        "next": "Comprobar y guardar",
    },
    {
        "eyebrow": "LISTO",
        "title": "¡Tu API key funciona!",
        "body": "Quedó guardada en esta compu, así que no te la vuelvo a pedir.\n\n"
                "Ahora pega el link de una carpeta de Drive y dale a descargar.",
        "next": "Empezar a descargar",
    },
]
KEY_STEP = 6


class Wizard:
    """Configuración de la API key, un paso por pantalla."""

    def __init__(self, app, start=0, can_cancel=False):
        self.app, self.c = app, app.colors
        self.can_cancel = can_cancel
        self.step = start
        self.key = tk.StringVar(value=app.api_key)
        self.checking = False
        self.message = None
        self.frame = ttk.Frame(app.root, padding=(28, 22))
        self.frame.pack(fill="both", expand=True)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)

        top = ttk.Frame(self.frame)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 14))
        ttk.Label(top, text=APP_NAME, font=(FONT, 11, "bold")).pack(side="left")
        if can_cancel:
            ttk.Button(top, text="Cancelar", style="BgLink.TButton",
                       command=self.app.show_main).pack(side="right", padx=(12, 0))
        self.dots = tk.Canvas(top, height=10, width=KEY_STEP * 16, bg=self.c["bg"],
                              highlightthickness=0)
        self.dots.pack(side="right")

        self.body = card(self.frame, self.c, row=1, column=0, sticky="new")
        self.body.columnconfigure(0, weight=1)

        nav = ttk.Frame(self.frame)
        nav.grid(row=2, column=0, sticky="ew", pady=(16, 0))
        self.back_btn = ttk.Button(nav, text="←  Atrás", command=self.back)
        self.next_btn = ttk.Button(nav, style="Accent.TButton", command=self.next)
        self.next_btn.pack(side="right")
        self.skip_btn = ttk.Button(nav, command=self.next)
        self.render()

    def destroy(self):
        self.frame.destroy()

    def render(self):
        s = STEPS[self.step]
        for w in self.body.winfo_children():
            w.destroy()
        self.message = None

        eyebrow = s.get("eyebrow") or f"PASO {self.step} DE {KEY_STEP}"
        ttk.Label(self.body, text=eyebrow, style="Eyebrow.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(self.body, text=s["title"], style="WizardTitle.TLabel", wraplength=WRAP,
                  justify="left").grid(row=1, column=0, columnspan=2, sticky="w", pady=(4, 10))
        ttk.Label(self.body, text=s["body"], style="Body.TLabel", wraplength=WRAP,
                  justify="left").grid(row=2, column=0, columnspan=2, sticky="w")
        row = 3
        if "link" in s:
            text, url = s["link"]
            ttk.Button(self.body, text=text + "  ↗", style="Accent.TButton",
                       command=lambda: webbrowser.open(url)).grid(
                row=row, column=0, sticky="w", pady=(14, 0))
            row += 1
        if s.get("overview"):
            cv = tk.Canvas(self.body, width=WRAP, height=96, bg=self.c["card"],
                           highlightthickness=0)
            cv.grid(row=row, column=0, columnspan=2, sticky="w", pady=(24, 0))
            draw_overview(cv, self.c)
            row += 1
        if "screen" in s:
            cv = tk.Canvas(self.body, width=WRAP, height=212, bg=self.c["card"],
                           highlightthickness=0)
            cv.grid(row=row, column=0, columnspan=2, sticky="w", pady=(16, 0))
            draw_screen(cv, s["screen"], self.c)
            ttk.Label(self.body, style="Card.Muted.TLabel", font=(FONT, 9),
                      text="Así se ve más o menos. Puede cambiar un poco según tu cuenta."
                      ).grid(row=row + 1, column=0, columnspan=2, sticky="w", pady=(4, 0))
            row += 2
        if s.get("entry"):
            entry = ttk.Entry(self.body, textvariable=self.key, font=(MONO, 11))
            entry.grid(row=row, column=0, sticky="ew", pady=(16, 0))
            entry.bind("<Return>", lambda e: self.next())
            entry.focus_set()
            ttk.Button(self.body, text="Pegar", command=self.paste).grid(
                row=row, column=1, padx=(8, 0), pady=(16, 0))
            self.message = ttk.Label(self.body, text="", style="Card.Muted.TLabel",
                                     wraplength=WRAP, justify="left")
            self.message.grid(row=row + 1, column=0, columnspan=2, sticky="w", pady=(10, 0))
            row += 2
        if s.get("hint"):
            ttk.Label(self.body, text=s["hint"], style="Card.Muted.TLabel", wraplength=WRAP,
                      justify="left").grid(row=row, column=0, columnspan=2, sticky="w",
                                           pady=(12, 0))

        self.next_btn.configure(text=s["next"] + "  →", state="normal")
        if 0 < self.step < len(STEPS) - 1:
            self.back_btn.pack(side="left")
        else:
            self.back_btn.pack_forget()
        if s.get("skip"):
            self.skip_btn.configure(text=s["skip"])
            self.skip_btn.pack(side="right", padx=8)
        else:
            self.skip_btn.pack_forget()
        self.draw_dots()

    def draw_dots(self):
        self.dots.delete("all")
        if not 0 < self.step <= KEY_STEP:
            return
        for i in range(KEY_STEP):
            x = 3 + i * 16
            color = self.c["accent"] if i < self.step else self.c["track"]
            self.dots.create_oval(x, 1, x + 8, 9, fill=color, outline=color)

    def show_message(self, text, kind="muted"):
        if self.message is not None:
            style = {"muted": "Card.Muted.TLabel", "danger": "Card.Danger.TLabel",
                     "success": "Card.Success.TLabel"}[kind]
            self.message.configure(text=text, style=style)

    def paste(self):
        try:
            self.key.set(self.app.root.clipboard_get().strip())
        except tk.TclError:
            self.show_message("No hay nada copiado. Copia la clave en Google Cloud.", "danger")

    def back(self):
        if self.step > 0 and not self.checking:
            self.step -= 1
            self.render()

    def next(self):
        if self.checking:
            return
        if self.step == KEY_STEP:
            return self.check_key()
        if self.step == len(STEPS) - 1:
            return self.app.show_main()
        self.step += 1
        self.render()

    def check_key(self):
        key = re.sub(r"\s+", "", self.key.get())
        self.key.set(key)
        if not KEY_RE.fullmatch(key):
            return self.show_message(
                "Eso no parece una API key. Empiezan con «AIza» y tienen 39 caracteres. "
                "Vuelve a copiarla completa.", "danger")
        self.checking = True
        self.next_btn.configure(state="disabled", text="Comprobando…")
        self.show_message("Comprobando con Google…")

        def work():
            result = drive_core.check_api_key(key)
            self.app.root.after(0, lambda: self.key_checked(key, *result))

        threading.Thread(target=work, daemon=True).start()

    def key_checked(self, key, ok, reason):
        self.checking = False
        if not self.frame.winfo_exists():
            return
        if ok is False:
            self.next_btn.configure(state="normal", text=STEPS[KEY_STEP]["next"] + "  →")
            return self.show_message(reason, "danger")
        self.app.set_api_key(key)
        self.step = KEY_STEP + 1
        self.render()
        if ok is None:
            ttk.Label(self.body, style="Card.Muted.TLabel", wraplength=WRAP, justify="left",
                      text=f"{reason} La guardé igual; si no funciona, cámbiala desde la "
                           "ventana principal.").grid(row=9, column=0, sticky="w", pady=(16, 0))


# --------------------------------------------------------------------------
# Ventana principal
# --------------------------------------------------------------------------

class App:
    def __init__(self, root):
        self.root = root
        self.colors = DARK if system_is_dark() else LIGHT
        setup_styles(root, self.colors)
        if self.colors is DARK:
            dark_title_bar(root)

        self.downloader = None
        self.thread = None
        self.logs = queue.Queue()
        self.screen = None
        self.status = None
        cfg = load_config()
        self.api_key = cfg.get("api_key", "")
        self.link = tk.StringVar()
        self.dest = tk.StringVar(value=cfg.get("dest") or os.path.join(
            os.path.expanduser("~"), "Downloads"))
        self.workers = tk.IntVar(value=cfg.get("workers", 3))

        root.title(APP_NAME)
        root.geometry("760x740")
        root.minsize(700, 680)
        root.protocol("WM_DELETE_WINDOW", self.on_close)

        if self.api_key:
            self.show_main()
        else:
            self.show_wizard()
        self.tick()

    # -- pantallas -------------------------------------------------------------

    def clear(self):
        if self.screen is not None:
            self.screen.destroy()
        self.screen = None
        self.status = None

    def show_wizard(self, start=0):
        self.clear()
        self.screen = Wizard(self, start, can_cancel=bool(self.api_key))

    def show_main(self):
        self.clear()
        self.screen = self.build_main()

    def build_main(self):
        c = self.colors
        main = ttk.Frame(self.root, padding=(26, 22))
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(3, weight=1)

        ttk.Label(main, text=APP_NAME, style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(main, style="Muted.TLabel",
                  text="Baja carpetas completas de Google Drive, aunque pesen miles de GB."
                  ).grid(row=1, column=0, sticky="w", pady=(2, 16))

        # Qué y dónde
        what = card(main, c, row=2, column=0, sticky="ew")
        what.columnconfigure(0, weight=1)
        ttk.Label(what, text="Link de la carpeta de Drive", style="Section.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w")
        self.link_entry = ttk.Entry(what, textvariable=self.link)
        self.link_entry.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        self.link_entry.bind("<Return>", lambda e: self.start())
        ttk.Button(what, text="Pegar", command=self.paste_link).grid(
            row=1, column=1, padx=(8, 0), pady=(6, 0))
        self.link_error = ttk.Label(what, text="", style="Card.Danger.TLabel")
        self.link_error.grid(row=2, column=0, columnspan=2, sticky="w")

        ttk.Label(what, text="Guardar en", style="Section.TLabel").grid(
            row=3, column=0, columnspan=2, sticky="w", pady=(10, 0))
        self.dest_label = ttk.Label(what, style="Card.Muted.TLabel")
        self.dest_label.grid(row=4, column=0, sticky="w", pady=(4, 0))
        ttk.Button(what, text="Cambiar…", command=self.pick_dest).grid(
            row=4, column=1, padx=(8, 0), pady=(4, 0))
        self.update_dest_label()

        actions = ttk.Frame(what, style="Card.TFrame")
        actions.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(18, 0))
        self.start_btn = ttk.Button(actions, text="Descargar carpeta", style="Accent.TButton",
                                    command=self.start)
        self.start_btn.pack(side="left")
        self.stop_btn = ttk.Button(actions, text="Detener", command=self.stop)
        self.stop_btn.pack(side="left", padx=8)
        ttk.Spinbox(actions, from_=1, to=8, width=3, textvariable=self.workers).pack(
            side="right")
        ttk.Label(actions, text="Descargas a la vez", style="Card.Muted.TLabel").pack(
            side="right", padx=(0, 8))

        # Progreso
        prog = card(main, c, row=3, column=0, sticky="new", pady=(14, 0))
        prog.columnconfigure(0, weight=1)
        prog.rowconfigure(5, weight=1)
        head = ttk.Frame(prog, style="Card.TFrame")
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        self.percent_label = ttk.Label(head, text="0%", style="Percent.TLabel")
        self.percent_label.grid(row=0, column=0, sticky="w")
        self.status = ttk.Label(head, text="Pega un link para empezar.", style="Status.TLabel")
        self.status.grid(row=0, column=1, sticky="e")
        self.progress = ttk.Progressbar(prog, maximum=1000, style="Big.Horizontal.TProgressbar")
        self.progress.grid(row=1, column=0, sticky="ew", pady=(6, 10))
        self.detail = ttk.Label(prog, text="", style="Card.Muted.TLabel", wraplength=640,
                                justify="left")
        self.detail.grid(row=2, column=0, sticky="w")
        self.current = ttk.Label(prog, text="", style="Card.Muted.TLabel")
        self.current.grid(row=3, column=0, sticky="w")

        bar = ttk.Frame(prog, style="Card.TFrame")
        bar.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        self.log_btn = ttk.Button(bar, text="▸  Ver detalles", style="Link.TButton",
                                  command=self.toggle_log)
        self.log_btn.pack(side="left")
        ttk.Button(bar, text="Abrir carpeta", style="Link.TButton",
                   command=self.open_dest).pack(side="right")
        self.log_frame = ttk.Frame(prog, style="Card.TFrame")
        self.log_text = tk.Text(self.log_frame, height=8, state="disabled", wrap="word",
                                font=(MONO, 9), bg=c["field"], fg=c["muted"], relief="flat",
                                highlightthickness=1, highlightbackground=c["line"],
                                padx=8, pady=6)
        scroll = ttk.Scrollbar(self.log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # Pie: la API key
        foot = ttk.Frame(main)
        foot.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        self.key_label = ttk.Label(foot, style="Muted.TLabel")
        self.key_label.pack(side="left")
        ttk.Button(foot, text="Cambiar API key", style="BgLink.TButton",
                   command=lambda: self.show_wizard(KEY_STEP)).pack(side="left", padx=10)
        self.update_key_label()

        self.set_running(bool(self.thread and self.thread.is_alive()))
        self.link_entry.focus_set()
        return main

    # -- ayudas ----------------------------------------------------------------

    def set_api_key(self, key):
        self.api_key = key
        self.save_settings()

    def save_settings(self):
        try:
            workers = max(1, min(8, int(self.workers.get())))
        except (tk.TclError, ValueError):
            workers = 3
        save_config({"dest": self.dest.get().strip(), "api_key": self.api_key,
                     "workers": workers})
        return workers

    def update_key_label(self):
        k = self.api_key
        self.key_label.configure(text=f"Tu API key: {k[:4]}…{k[-4:]}" if k else "Sin API key")

    def update_dest_label(self):
        self.dest_label.configure(text=middle_ellipsis(self.dest.get(), 64))

    def set_running(self, running):
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")

    def set_percent(self, pct, done=False):
        self.progress.configure(value=pct * 10, style="Done.Horizontal.TProgressbar" if done
                                else "Big.Horizontal.TProgressbar")
        text = f"{int(pct)}%" if pct >= 100 or pct == 0 else f"{pct:.1f}%"
        self.percent_label.configure(text=text, style="PercentDone.TLabel" if done
                                     else "Percent.TLabel")
        self.root.title(f"{text} · {APP_NAME}" if 0 < pct < 100 or done else APP_NAME)

    def set_status(self, text, kind=""):
        style = {"": "Status.TLabel", "danger": "StatusDanger.TLabel",
                 "success": "StatusSuccess.TLabel"}[kind]
        self.status.configure(text=text, style=style)

    def toggle_log(self, show=None):
        visible = bool(self.log_frame.grid_info())
        if show is None:
            show = not visible
        if show and not visible:
            self.log_frame.grid(row=5, column=0, sticky="nsew", pady=(6, 0))
            self.log_btn.configure(text="▾  Ocultar detalles")
        elif not show and visible:
            self.log_frame.grid_forget()
            self.log_btn.configure(text="▸  Ver detalles")

    def append_log(self, line):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        if int(self.log_text.index("end-1c").split(".")[0]) > 3000:
            self.log_text.delete("1.0", "500.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # -- acciones --------------------------------------------------------------

    def paste_link(self):
        try:
            self.link.set(self.root.clipboard_get().strip())
            self.link_error.configure(text="")
        except tk.TclError:
            pass

    def pick_dest(self):
        d = filedialog.askdirectory(initialdir=self.dest.get() or None)
        if d:
            self.dest.set(os.path.normpath(d))
            self.update_dest_label()
            self.save_settings()

    def open_dest(self):
        d = self.dest.get()
        if os.path.isdir(d) and hasattr(os, "startfile"):
            os.startfile(d)

    def start(self):
        link, dest = self.link.get().strip(), self.dest.get().strip()
        self.link_error.configure(text="")
        if not link:
            self.link_error.configure(text="Pega aquí el link de la carpeta de Drive.")
            return self.link_entry.focus_set()
        if not self.api_key:
            return self.show_wizard(KEY_STEP)
        workers = self.save_settings()
        try:
            self.downloader = drive_core.Downloader(link, dest, self.api_key, workers,
                                                    log=self.logs.put)
        except ValueError:
            self.link_error.configure(text="Ese link no es de Google Drive. Cópialo desde "
                                           "Drive con «Compartir → Copiar enlace».")
            return self.link_entry.focus_set()
        self.set_running(True)
        self.set_percent(0)
        self.set_status("Empezando…")
        self.detail.configure(text="")
        self.current.configure(text="")
        self.thread = threading.Thread(target=self._run, args=(self.downloader,), daemon=True)
        self.thread.start()

    def _run(self, d):
        try:
            failed = d.run()
        except Exception as e:  # p. ej. carpeta privada o inexistente
            self.logs.put(("error", str(e)))
            return
        self.logs.put(("done", failed))

    def stop(self):
        if self.downloader:
            self.downloader.stop()
            self.set_status("Deteniendo…")
            self.stop_btn.configure(state="disabled")

    def on_close(self):
        if self.thread and self.thread.is_alive():
            if not messagebox.askyesno(
                    APP_NAME, "Hay una descarga en curso. ¿Quieres salir?\n\n"
                              "No se pierde nada: la próxima vez sigue donde se quedó."):
                return
            self.downloader.stop()
        self.root.destroy()

    # -- refresco --------------------------------------------------------------

    def on_finished(self, kind, value):
        self.set_running(False)
        self.current.configure(text="")
        if kind == "error":
            self.set_status("No se pudo descargar", "danger")
            self.detail.configure(text=value)
            self.toggle_log(True)
        elif value is None:
            self.set_status("En pausa. Dale a «Descargar carpeta» para seguir.")
        elif value:
            self.set_status(f"Terminó, pero {len(value)} archivo(s) fallaron", "danger")
            names = ", ".join(f.name for f in value[:5]) + ("…" if len(value) > 5 else "")
            self.detail.configure(text=f"Sin permiso o borrados: {names}. "
                                       "Dale a «Descargar carpeta» para reintentarlos.")
            self.toggle_log(True)
        else:
            self.set_percent(100, done=True)
            self.set_status("¡Listo! Todo descargado.", "success")

    def tick(self):
        on_main = self.status is not None
        try:
            while True:
                msg = self.logs.get_nowait()
                if not on_main:
                    continue
                if isinstance(msg, tuple):
                    self.on_finished(*msg)
                else:
                    self.append_log(msg)
        except queue.Empty:
            pass

        d = self.downloader
        if on_main and d and self.thread and self.thread.is_alive():
            s = d.stats
            speed = s.speed()
            pct = s.percent()
            with s.lock:
                phase, total, done = s.phase, s.total_files, s.done_files
                tb, db, failed = s.total_bytes, s.done_bytes, len(s.failed)
                known = s.sizes_known
                active = list(s.active.values())
            self.set_percent(pct)
            if phase == "Descargando":
                self.set_status(f"{done} de {total} archivos")
                eta = ""
                if known and tb and speed > 0 and db < tb:
                    secs = int((tb - db) / speed)
                    eta = (f" · falta ≈ {secs // 3600} h {secs % 3600 // 60} min"
                           if secs >= 3600 else f" · falta ≈ {max(1, secs // 60)} min")
                self.detail.configure(
                    text=drive_core.human_size(db)
                    + (f" de {drive_core.human_size(tb)}" if known and tb else "")
                    + f" · {drive_core.human_size(speed)}/s{eta}"
                    + (f" · {failed} con error" if failed else ""))
                self.current.configure(text=(
                    "Bajando: " + middle_ellipsis(active[0][0], 50)
                    + (f" y {len(active) - 1} más" if len(active) > 1 else "")) if active else "")
            else:
                self.set_status(phase)
        self.root.after(500, self.tick)


def main():
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
