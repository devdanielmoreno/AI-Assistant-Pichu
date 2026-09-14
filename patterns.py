import sqlite3
import statistics

from memory import DB_PATH, registrar_regla

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MIN_EVENTOS = 3
SLOT_MIN = 60
_PROC_IGNORAR = {
    "explorer.exe", "python.exe", "pythonw.exe", "conhost.exe", "cmd.exe",
    "powershell.exe", "applicationframehost.exe", "searchapp.exe", "startmenuexperiencehost.exe",
    "textinputhost.exe", "shellexperiencehost.exe",
}


def _bucket(hora_min):
    return int(hora_min // SLOT_MIN)


def _frase(dow, hora_centro, sitio, proceso):
    hh = hora_centro // 60
    mm = hora_centro % 60
    hora_texto = f"{hh:02d}:{mm:02d}"
    base = f"Los {DIAS[dow]} sobre las {hora_texto}"
    if sitio:
        return f"{base}, el usuario suele tener abierto {sitio} en el navegador"
    if proceso:
        p = proceso.replace(".exe", "")
        return f"{base}, el usuario suele usar {p}"
    return None


def run_diario(min_eventos=MIN_EVENTOS):
    with sqlite3.connect(DB_PATH, check_same_thread=False) as c:
        rows = c.execute(
            "SELECT ts, dow, hora_min, sitio, proceso, ventana FROM telemetry"
        ).fetchall()

    if not rows:
        return {"reglas": 0, "eventos": 0}

    grupos = {}
    for ts, dow, hora_min, sitio, proceso, ventana in rows:
        proc = (proceso or "").lower()
        if proc in _PROC_IGNORAR and not sitio:
            continue
        clave_sitio = sitio
        clave_proc = proc if not sitio else None
        if not clave_sitio and not clave_proc:
            continue
        if clave_sitio:
            key = (dow, _bucket(hora_min), "sitio", clave_sitio)
        else:
            key = (dow, _bucket(hora_min), "proc", clave_proc)
        grupos.setdefault(key, []).append(hora_min)

    reglas_creadas = 0

    for (dow, bucket, tipo, valor), horas in grupos.items():
        if len(horas) < min_eventos:
            continue
        hora_centro = int(statistics.mean(horas))
        frase = _frase(dow, hora_centro, valor if tipo == "sitio" else None,
                       valor if tipo == "proc" else None)
        if not frase:
            continue
        peso = min(1.0, 0.5 + 0.1 * len(horas))
        if registrar_regla(frase, dia_semana=dow, hora_centro=hora_centro, peso=peso):
            reglas_creadas += 1

    return {"reglas": reglas_creadas, "eventos": len(rows)}