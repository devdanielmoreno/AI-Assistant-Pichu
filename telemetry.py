import ctypes
import os
import sqlite3
import threading
import time
import psutil
from datetime import datetime

from memory import DB_PATH

INTERVALO = 60
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
_SITIOS = [
    "youtube", "discord", "twitch", "netflix", "spotify", "wikipedia",
    "stack overflow", "stackoverflow", "whatsapp", "github",
]


def _ventana_activa():
    try:
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf, 512)
        titulo = buf.value.strip()
        proceso = ""
        exe = ""
        try:
            p = psutil.Process(pid.value)
            proceso = (p.name() or "").lower()
            try:
                exe = p.exe()
            except Exception:
                pass
        except Exception:
            pass
        return {
            "hwnd": hwnd,
            "ventana": titulo,
            "proceso": proceso,
            "exe": exe,
            "pid": pid.value,
        }
    except Exception as e:
        print(f"[Telemetría ventana_activa]: {e}")
        return None


def _inferir_sitio(ventana, proceso):
    t = ((ventana or "") + " " + (proceso or "")).lower()
    if "youtube music" in t or "music.youtube" in t:
        return "YouTubeMusic"
    for s in _SITIOS:
        if s in t:
            if s in ("stack overflow", "stackoverflow"):
                return "StackOverflow"
            return s.title()
    return None


def ventanas_abiertas():
    """Ventanas visibles (incluyendo minimizadas) con título propio."""
    try:
        import win32gui
    except Exception:
        return []
    censura = {"progman", "shell_traywnd", "tooltips_class32", "gdi+window"}
    salida = []

    def _cb(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return
        cls = (win32gui.GetClassName(hwnd) or "").strip().lower()
        if cls in censura:
            return
        titulo = (win32gui.GetWindowText(hwnd) or "").strip()
        if not titulo:
            return
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        proceso, exe = "", ""
        try:
            p = psutil.Process(pid.value)
            proceso = (p.name() or "").lower()
            try:
                exe = p.exe()
            except Exception:
                pass
        except Exception:
            pass
        salida.append({"ventana": titulo, "clase": cls, "proceso": proceso,
                       "exe": exe, "pid": pid.value})

    win32gui.EnumWindows(_cb, None)
    return salida


def sitios_abiertos():
    """Servicios que el usuario tiene realmente abiertos (no solo el foco)."""
    t = " ".join((v.get("ventana") or "").lower() for v in ventanas_abiertas())
    ytm = ("youtube music" in t) or ("music.youtube" in t)
    res = []
    if ytm:
        res.append("YouTube Music")
    elif "youtube" in t:
        res.append("YouTube")
    for clave, nombre in (("stremio", "Stremio"), ("spotify", "Spotify"),
                          ("netflix", "Netflix"), ("twitch", "Twitch"),
                          ("discord", "Discord"), ("whatsapp", "WhatsApp")):
        if clave in t:
            res.append(nombre)
    return res


def zen_fondo_activo():
    try:
        import zen_bidi
        return zen_bidi.obtener().esta_abierto()
    except Exception:
        return False


def _boot_reciente(minutos=8):
    try:
        return (time.time() - psutil.boot_time()) < minutos * 60
    except Exception:
        return False


def estado_actual():
    info = _ventana_activa()
    now = datetime.now()
    ventana = info["ventana"] if info else ""
    proceso = info["proceso"] if info else ""
    abiertos = sitios_abiertos()
    return {
        "hora_texto": now.strftime("%H:%M"),
        "hora_min": now.hour * 60 + now.minute,
        "dia_semana": now.weekday(),
        "dia_texto": DIAS[now.weekday()],
        "app_activa": ventana,
        "ventana": ventana,
        "proceso": proceso,
        "exe": info["exe"] if info else "",
        "sitio": _inferir_sitio(ventana, proceso),
        "sitios_abiertos": abiertos,
        "abierto_texto": (", ".join(abiertos) if abiertos else "ninguno") + (
            " además del Zen de fondo de Pichu" if zen_fondo_activo() else ""),
        "zen_fondo": zen_fondo_activo(),
        "boot_reciente": _boot_reciente(8),
    }


def _registrar():
    now = datetime.now()
    info = _ventana_activa()
    ventana = info["ventana"] if info else ""
    proceso = info["proceso"] if info else ""
    exe = info["exe"] if info else ""
    sitio = _inferir_sitio(ventana, proceso)
    with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
        c.execute(
            "INSERT INTO telemetry (ts, dow, hora_min, ventana, proceso, exe, sitio)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (int(time.time()), now.weekday(), now.hour * 60 + now.minute,
             ventana, proceso, exe, sitio),
        )


def _registrar_arranque():
    now = datetime.now()
    with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
        last = c.execute("SELECT MAX(ts) FROM arranques").fetchone()[0]
        if last is None or int(time.time()) - last > 10 * 60:
            c.execute(
                "INSERT INTO arranques (ts, dia_semana, hora_min) VALUES (?, ?, ?)",
                (int(time.time()), now.weekday(), now.hour * 60 + now.minute),
            )


class TelemetryThread(threading.Thread):
    def __init__(self, intervalo=INTERVALO, daemon=True):
        super().__init__(daemon=daemon)
        self.intervalo = intervalo
        self._parar = threading.Event()

    def run(self):
        from memory import init as _init_db
        _init_db()
        _registrar_arranque()
        _registrar()
        while not self._parar.wait(self.intervalo):
            try:
                _registrar()
            except Exception as e:
                print(f"[Telemetría]: {e}")

    def detener(self):
        self._parar.set()