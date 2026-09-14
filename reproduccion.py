"""Motor de reproduccion ligero de Pichu (sin navegador).

Busca en YouTube Music con `ytmusicapi`, extrae el stream de audio con `yt-dlp`
y lo reproduce con `mpv` en segundo plano (audio-only). Consumo minimo, cero
instancias de Zen y cero anuncios: se reproduce el stream directo.

Tambien se usa como fuente del "musica sonando" para el panel de estado.
"""

import os
import json
import time
import ctypes
import collections
import threading
import subprocess

from ctypes import wintypes

BASE = os.path.dirname(os.path.abspath(__file__))
MPV = os.path.join(BASE, "bin", "mpv", "mpv.exe")
PIPE = r"\\.\pipe\pichu_mpv"
_VIDEO_TPL = "https://music.youtube.com/watch?v={vid}"

_lock = threading.RLock()
_proc = None
_estado = "parado"  # sonando | pausa | parado
_actual = {"busqueda": "", "canciones": [], "indice": 0, "titulo": "", "artista": ""}
_gen = 0  # generación de reproducción: evita que hilos viejos sigan metiendo canciones
_inicio = None  # timestamp en que la canción actual empezó a sonar (None si pausada)
_acumulado = 0.0  # segundos acumulados de la canción actual en pausas
_indice_lanzado = 0  # índice (sobre _actual["canciones"]) con el que arrancó el playlist de mpv

# Observador del mpv real: el pipe queda abierto y cada ~1 s se consultan
# titulo / tiempo / pausa / posición de playlist. Así el widget y los comandos
# de voz ven el título real y el progreso verdaderos aunque mpv avance solo.
_OBS_PERIOD = 1.0
_obs = {"titulo": "", "progreso": None, "pausado": None, "playlist_pos": None}
_obs_lock = threading.RLock()
_h_ipc = None

# Cola de comandos hacia el mpv: SOLO el hilo lector toca el handle del pipe.
# El resto (GUI, voz, prellenado) encola aquí y devuelve al instante; así
# nunca se cruzan WriteFile/ReadFile de dos hilos sobre el mismo handle.
_cola_comandos = collections.deque()
_cola_lock = threading.Lock()


def _congelar_tiempo():
    global _acumulado, _inicio
    if _inicio:
        _acumulado += time.time() - _inicio
        _inicio = None


def _tiempo_actual():
    """Segundos cronometrados de la canción actual (sin consultar IPC)."""
    if _inicio:
        return _acumulado + (time.time() - _inicio)
    return _acumulado


def disponible():
    return os.path.exists(MPV)


def _buscar(busqueda, limite=10):
    """Busca canciones en YouTube Music. Los fallos de la API suelen ser
    intermitentes (visitorData firma caducada, rate-limit), así que se reintenta
    una vez con una sesión fresca antes de rendirse."""
    ultimo_error = None
    for _ in range(2):
        try:
            from ytmusicapi import YTMusic
            yt = YTMusic()
            res = yt.search(busqueda, filter="songs", limit=limite)
            canciones = []
            for r in res:
                vid = r.get("videoId")
                if not vid:
                    continue
                artistas = ", ".join(a.get("name", "") for a in (r.get("artists") or []) if a.get("name"))
                canciones.append({
                    "titulo": r.get("title") or "Desconocida",
                    "artista": artistas or "?",
                    "videoId": vid,
                })
            if canciones:
                return canciones
            ultimo_error = ValueError("La búsqueda de YouTube Music devolvió 0 canciones.")
        except Exception as e:
            ultimo_error = e
            time.sleep(1.0)  # respiro antes del reintento
    if ultimo_error is not None:
        raise ultimo_error
    return []


def _extraer_url(video_id):
    import yt_dlp
    opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "extractor_args": {"youtube": ["skip=webpage"]},
    }
    ultimo_error = None
    for _ in range(2):
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(_VIDEO_TPL.format(vid=video_id), download=False)
            url = info.get("url")
            if url:
                return url
        except Exception as e:
            ultimo_error = e
        time.sleep(1.0)  # a veces el primer intento choca con un muro temporal
    if ultimo_error is not None:
        raise ultimo_error
    return None


def _matar_proc():
    global _proc
    _cerrar_pipe()  # desbloquea al lector IPC si estaba leyendo
    if _proc is None:
        return
    viejo = _proc
    _proc = None  # se corta la referencia ANTES de matar: nada salta solo
    try:
        if viejo.poll() is None:
            viejo.terminate()
            try:
                viejo.wait(timeout=3)
            except Exception:
                viejo.kill()
    except Exception:
        pass


def _ocultar_ventana_mpv(pid):
    """Oculta cualquier ventana emergente de mpv (audio-only: no queremos ningÃºn
    rectÃ¡ngulo en pantalla). Un hilo daemon la vigila unos segundos porque algunas
    builds de mpv sacan una ventana negra al arrancar antes de darse cuenta de que
    no hay vÃ­deo."""
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32 = ctypes.windll.user32
    user32.EnumWindows.argtypes = [WNDENUMPROC, ctypes.c_void_p]
    user32.EnumWindows.restype = ctypes.c_bool

    def _cb(hwnd, _):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            proc = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc))
            if proc.value != pid:
                return True
            user32.ShowWindow(hwnd, 0)  # SW_HIDE
        except Exception:
            pass
        return True

    fin = time.time() + 4.0
    while time.time() < fin:
        try:
            user32.EnumWindows(WNDENUMPROC(_cb), 0)
        except Exception:
            pass
        time.sleep(0.2)


def _cerrar_pipe():
    global _h_ipc
    if _h_ipc:
        try:
            ctypes.windll.kernel32.CloseHandle(_h_ipc)
        except Exception:
            pass
        _h_ipc = None


def _conectar_pipe(timeout=6.0):
    """Abre el pipe del mpv en modo lectura+escritura y lo deja abierto."""
    global _h_ipc
    GENERIC_RW = 0xC0000000  # GENERIC_READ | GENERIC_WRITE
    OPEN_EXISTING = 3
    fin = time.time() + timeout
    while time.time() < fin:
        try:
            h = ctypes.windll.kernel32.CreateFileW(
                PIPE, GENERIC_RW, 0, None, OPEN_EXISTING, 0, None)
        except Exception:
            return False
        if h and h != -1:
            _h_ipc = h
            return True
        time.sleep(0.15)
    return False


def _escribir(texto):
    if not _h_ipc:
        return False
    try:
        buf = texto.encode("utf-8")
        visto = wintypes.DWORD(0)
        ok = ctypes.windll.kernel32.WriteFile(_h_ipc, buf, len(buf), ctypes.byref(visto), None)
        return bool(ok)
    except Exception:
        return False


def _pipe_leer():
    if not _h_ipc:
        return b""
    try:
        rb = ctypes.create_string_buffer(8192)
        leido = wintypes.DWORD(0)
        ok = ctypes.windll.kernel32.ReadFile(_h_ipc, rb, 8192, ctypes.byref(leido), None)
        if not ok or not leido.value:
            return b""
        return rb.raw[:leido.value]
    except Exception:
        return b""


def _leer_disponible():
    """Bytes YA disponibles en el pipe (mirada no bloqueante). -1 si no se pudo."""
    if not _h_ipc:
        return 0
    try:
        disp = wintypes.DWORD(0)
        ok = ctypes.windll.kernel32.PeekNamedPipe(
            _h_ipc, None, 0, None, ctypes.byref(disp), None)
        return disp.value if ok else -1
    except Exception:
        return -1


def _procesar_linea(linea):
    try:
        js = json.loads(linea)
    except Exception:
        return
    # Respuestas a get_property: vienen como {"data":..., "request_id":N}
    rid = js.get("request_id")
    if rid == 10:
        with _obs_lock:
            _obs["titulo"] = (js.get("data") or "").strip()
        return
    if rid == 11 and isinstance(js.get("data"), (int, float)):
        with _obs_lock:
            _obs["progreso"] = int(js["data"])
        return
    if rid == 12:
        with _obs_lock:
            _obs["pausado"] = bool(js.get("data"))
        return
    if rid == 13 and isinstance(js.get("data"), int):
        with _obs_lock:
            _obs["playlist_pos"] = int(js["data"])
        return
    # Legado de observe_property (si algún día se usa)
    if js.get("event") != "property-change":
        return
    name = js.get("name")
    data = js.get("data")
    if name == "media-title":
        with _obs_lock:
            _obs["titulo"] = (data or "").strip()
    elif name == "time-pos" and isinstance(data, (int, float)):
        with _obs_lock:
            _obs["progreso"] = int(data)
    elif name == "pause":
        with _obs_lock:
            _obs["pausado"] = bool(data)


def _lector_mpv(gen):
    """Hilo daemon DUEÑO del pipe del mpv: envía los comandos encolados y cada
    ~1 s consulta titulo / tiempo / pausa reales, leyendo el pipe solo cuando
    hay datos (no bloquea). Responde así en vivo a los cambios de canción."""
    if not _conectar_pipe(timeout=6.0):
        return
    pend = b""
    ultima_peticion = 0.0
    while _gen == gen and _proc is not None and _proc.poll() is None:
        while True:
            with _cola_lock:
                if not _cola_comandos:
                    break
                cmd = _cola_comandos.popleft()
            if not _escribir(cmd + "\n"):
                break
        ahora = time.time()
        if ahora - ultima_peticion >= _OBS_PERIOD:
            _escribir('{"command":["get_property_string","media-title"],"request_id":10}\n')
            _escribir('{"command":["get_property","time-pos"],"request_id":11}\n')
            _escribir('{"command":["get_property","pause"],"request_id":12}\n')
            _escribir('{"command":["get_property","playlist-pos"],"request_id":13}\n')
            ultima_peticion = ahora
        if _leer_disponible() > 0:
            trozo = _pipe_leer()
            if not trozo:
                break
            pend += trozo
            while b"\n" in pend:
                linea, pend = pend.split(b"\n", 1)
                _procesar_linea(linea)
        else:
            time.sleep(0.03)
    _cerrar_pipe()


def _ipc(comando):
    """Encola un comando hacia el mpv; lo envía el hilo dueño del pipe. Esta
    llamada nunca toca el handle (no puede bloquear); es best-effort."""
    with _cola_lock:
        _cola_comandos.append(comando)
    return True


def _prellenar(indice, canciones, gen):
    """Añade el resto de canciones a la playlist del mpv en marcha (el propio
    mpv irá a la siguiente cuando terminen, y con `--loop-playlist=inf` nunca
    para). Si se lanza otra reproducción mientras tanto, se descarta."""
    n = len(canciones)
    for k in range(1, n):
        if _gen != gen:
            return
        idx = (indice + k) % n
        c = canciones[idx]
        try:
            url = _extraer_url(c["videoId"])
        except Exception:
            continue
        if not url:
            continue
        if _gen != gen:
            return
        time.sleep(0.3)
        if not _ipc(f'loadfile "{url}" append'):
            return


def _lanzar(indice):
    global _proc, _gen, _inicio, _acumulado, _indice_lanzado
    canciones = _actual["canciones"]
    if not (0 <= indice < len(canciones)):
        return False
    c = canciones[indice]
    try:
        url = _extraer_url(c["videoId"])
    except Exception as e:
        print(f"[reproduccion extraer]: {e}")
        return False
    if not url:
        return False
    _matar_proc()
    args = [MPV, "--no-video", "--volume=75", "--ao=wasapi", "--quiet",
            "--force-window=no", "--terminal=no", "--no-ontop",
            "--loop-playlist=inf", f"--input-ipc-server={PIPE}", url]
    _proc = subprocess.Popen(args, creationflags=0x08000000)
    threading.Thread(target=_ocultar_ventana_mpv, args=(_proc.pid,), daemon=True).start()
    _actual.update({"indice": indice, "titulo": c["titulo"], "artista": c["artista"]})
    with _obs_lock:
        _obs.update({"titulo": "", "progreso": None, "pausado": False, "playlist_pos": None})
    _indice_lanzado = indice
    _acumulado = 0.0
    _inicio = time.time()
    _gen += 1
    gen = _gen
    threading.Thread(target=_lector_mpv, args=(gen,), daemon=True).start()
    threading.Thread(target=_prellenar, args=(indice, list(canciones), gen), daemon=True).start()
    return True


def reproducir(busqueda):
    """Busca la primera cancion y la pone a sonar. Devuelve (info, error)."""
    global _estado
    with _lock:
        try:
            canciones = _buscar(busqueda)
        except Exception as e:
            _estado = "parado"
            return None, f"No pude buscar '{busqueda}' en YouTube Music ahora mismo ({e}). Inténtalo otra vez."
        if not canciones:
            _estado = "parado"
            return None, f"No encontré canciones para '{busqueda}'."
        _actual.update({"busqueda": busqueda, "canciones": canciones, "indice": 0})
        if not _lanzar(0):
            _estado = "parado"
            return None, f"No pude obtener el audio de la primera canción de '{busqueda}'. Inténtalo otra vez."
        _estado = "sonando"
        time.sleep(1.0)  # dejar que arranque el audio antes de confirmar
        return dict(canciones[0]), None


def pausar():
    global _estado
    with _lock:
        if _proc is None or _proc.poll() is not None:
            return "No hay nada sonando."
        _ipc("cycle pause")
        _congelar_tiempo()
        _estado = "pausa"
        return "Música en pausa (2º plano)."


def reanudar():
    global _estado, _inicio
    with _lock:
        if _proc is not None and _proc.poll() is None:
            _ipc("cycle pause")
            _inicio = time.time()
            _estado = "sonando"
            return "Música de nuevo (2º plano)."
        if _lanzar(_actual["indice"]):
            _estado = "sonando"
            time.sleep(1.0)
            return f"De nuevo '{_actual['titulo']}' (2º plano)."
        _estado = "parado"
        return "No pude reanudar la música."


def siguiente():
    global _estado, _inicio, _acumulado
    with _lock:
        if _proc is not None and _proc.poll() is None and _ipc("playlist-next"):
            _estado = "sonando"
            if _actual["canciones"]:
                n = len(_actual["canciones"])
                _actual["indice"] = (_actual["indice"] + 1) % n
                c = _actual["canciones"][_actual["indice"]]
                _actual.update({"titulo": c["titulo"], "artista": c["artista"]})
            _inicio = time.time()
            _acumulado = 0.0
            return "Siguiente canción."
        if not _actual["canciones"]:
            return "No hay cola de canciones."
        nuevo = (_actual["indice"] + 1) % len(_actual["canciones"])
        if not _lanzar(nuevo):
            return "No pude pasar a la siguiente canción."
        _estado = "sonando"
        time.sleep(1.0)
        return f"Siguiente: '{_actual['titulo']}' de {_actual['artista']} (2º plano)."


def anterior():
    global _estado, _inicio, _acumulado
    with _lock:
        if _proc is not None and _proc.poll() is None and _ipc("playlist-prev"):
            _estado = "sonando"
            if _actual["canciones"]:
                n = len(_actual["canciones"])
                _actual["indice"] = (_actual["indice"] - 1) % n
                c = _actual["canciones"][_actual["indice"]]
                _actual.update({"titulo": c["titulo"], "artista": c["artista"]})
            _inicio = time.time()
            _acumulado = 0.0
            return "Canción anterior."
        if not _actual["canciones"]:
            return "No hay cola de canciones."
        nuevo = (_actual["indice"] - 1) % len(_actual["canciones"])
        if not _lanzar(nuevo):
            return "No pude volver a la canción anterior."
        _estado = "sonando"
        time.sleep(1.0)
        return f"Otra vez '{_actual['titulo']}' de {_actual['artista']} (2º plano)."


def detener():
    global _estado, _inicio, _acumulado, _indice_lanzado
    with _lock:
        _estado = "parado"
        _inicio = None
        _acumulado = 0.0
        _indice_lanzado = 0
        with _obs_lock:
            _obs.update({"titulo": "", "progreso": None, "pausado": None, "playlist_pos": None})
        _matar_proc()
        return "Música detenida."


def estado():
    global _inicio, _acumulado
    with _lock:
        vivo = bool(_proc and _proc.poll() is None)
        with _obs_lock:
            pos = _obs["playlist_pos"]
            progreso = _obs["progreso"]
            pausado = _obs["pausado"]
        # El título mostrado es el REAl de la búsqueda, no el media-title de mpv
        # (para streams directos es basura tipo "webm;rqh=1..."). Para saber cuándo
        # cambia de canción seguimos la posición real del playlist: la entrada p de
        # mpv se corresponde con canciones[(_indice_lanzado + p) % n].
        canciones = _actual["canciones"]
        if pos is not None and canciones and _indice_lanzado is not None:
            n = len(canciones)
            idx = (_indice_lanzado + pos) % n
            if idx != _actual["indice"]:
                _actual["indice"] = idx
                c = canciones[idx]
                _actual.update({"titulo": c["titulo"], "artista": c["artista"]})
            _inicio = time.time()
            _acumulado = 0.0
        if progreso is None:
            progreso = int(_tiempo_actual())
        if pausado is None:
            pausado = _estado == "pausa"
        return {
            "activo": vivo,
            "sonando": vivo and _estado == "sonando" and not bool(pausado),
            "pausado": vivo and bool(pausado),
            "titulo": _actual["titulo"],
            "artista": _actual["artista"],
            "progreso": progreso,
            "motor": "mpv",
        }


def texto_panel():
    """Bloque 'música sonando' para el panel de estado de Pichu."""
    est = estado()
    if est["activo"] and est["titulo"]:
        estado_str = "pausada" if est["pausado"] else "sonando"
        return f"Música {estado_str}: {est['titulo']} de {est['artista']}"
    return "Música: nada sonando"