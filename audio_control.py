"""Control de volumen EXACTO con WASAPI (pycaw): maestro, mutear y por proceso.
Sin teclas multimedia ni mover el ratón. El objetivo por proceso se elige por su
nombre de proceso (p.ej. 'zen' -> zen.exe, que en nuestro caso suena YouTube Music)."""

import threading

try:
    from pycaw.pycaw import AudioUtilities
    DISPONIBLE = True
except Exception:
    DISPONIBLE = False

# Sinónimos de "app" -> nombre real del proceso
_PROCEDURES = {
    "youtube": "zen.exe",
    "youtube music": "zen.exe",
    "musica": "zen.exe",
    "música": "zen.exe",
    "zen": "zen.exe",
    "mpv": "mpv.exe",
    "stremio": "stremio-shell-ng.exe",
    "spotify": "spotify.exe",
}

_lock = threading.Lock()


def volumen_maestro(pct=None):
    """Lee (None) o fija (%) el volumen maestro. Devuelve el % resultante."""
    if not DISPONIBLE:
        return None
    with _lock:
        vol = AudioUtilities.GetSpeakers().EndpointVolume
        if pct is None:
            return round(vol.GetMasterVolumeLevelScalar() * 100)
        vol.SetMasterVolumeLevelScalar(max(0, min(100, int(pct))) / 100.0, None)
        return round(vol.GetMasterVolumeLevelScalar() * 100)


def mutear(estado=None):
    """Lee (None), fija (True/False) el silencio. Devuelve el estado resultante."""
    if not DISPONIBLE:
        return None
    with _lock:
        vol = AudioUtilities.GetSpeakers().EndpointVolume
        if estado is None:
            return bool(vol.GetMute())
        vol.SetMute(1 if estado else 0, None)
        return bool(vol.GetMute())


def _exe_de(nombre):
    nombre = (nombre or "").lower().strip()
    exe = _PROCEDURES.get(nombre)
    if exe:
        return exe
    return nombre if nombre.endswith(".exe") else nombre + ".exe"


def _sesion_de(nombre):
    exes = [_exe_de(nombre)]
    # "música" / "youtube music": si Pichu está reproduciendo con su motor ligero
    # (mpv), ese es "la música"; si no, el Zen (pestaña de YouTube Music). Solo
    # cuentan apps que estén emitiendo audio.
    if nombre in ("musica", "música", "youtube music", "youtube"):
        mpv_primero = False
        try:
            import reproduccion
            mpv_primero = bool(reproduccion.estado().get("activo"))
        except Exception:
            pass
        if mpv_primero:
            exes = ["mpv.exe"] + exes
        else:
            exes.append("mpv.exe")
    for exe in exes:
        for s in AudioUtilities.GetAllSessions():
            try:
                proc = s.Process
                simple = s.SimpleAudioVolume
                if proc and simple and proc.name().lower() == exe:
                    return simple
            except Exception:
                continue
    return None


def volumen_proceso_leer(nombre):
    """Volumen actual (%) de una app por su proceso (o None si no emite)."""
    if not DISPONIBLE:
        return None
    simple = _sesion_de(nombre)
    if simple is None:
        return None
    try:
        return round(simple.GetMasterVolume() * 100)
    except Exception:
        return None


def volumen_proceso(nombre, pct):
    """Fija el volumen (%) de una app por su proceso. Devuelve el % resultante
    o None si no hay sesión de audio activa con ese proceso."""
    if not DISPONIBLE:
        return None
    simple = _sesion_de(nombre)
    if simple is None:
        return None
    try:
        simple.SetMasterVolume(max(0, min(100, int(pct))) / 100.0, None)
        return round(simple.GetMasterVolume() * 100)
    except Exception:
        return None