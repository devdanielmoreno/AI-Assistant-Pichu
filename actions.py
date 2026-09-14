import os
import re
import json
import shutil
import subprocess
import webbrowser
import unicodedata
import ctypes
import time
import threading
from dataclasses import dataclass
from pathlib import Path
from difflib import SequenceMatcher
import pyautogui
import pyperclip
import urllib.parse
from ctypes import wintypes
import hammer
# --- 1. LOCALIZADOR AUTOMÁTICO DE ZEN BROWSER ---
_RUTA_ZEN_CACHE = None

def obtener_ruta_zen():
    global _RUTA_ZEN_CACHE
    if _RUTA_ZEN_CACHE and os.path.exists(_RUTA_ZEN_CACHE):
        return _RUTA_ZEN_CACHE

    archivo_cache = os.path.join(os.path.dirname(os.path.abspath(__file__)), "zen_path.txt")
    if os.path.exists(archivo_cache):
        try:
            with open(archivo_cache, "r", encoding="utf-8") as f:
                p = f.read().strip()
                if os.path.exists(p):
                    _RUTA_ZEN_CACHE = p
                    return p
        except Exception:
            pass

    # 1. Detectar si Zen Browser ya está abierto en Windows
    try:
        cmd = ["powershell", "-NoProfile", "-Command", "(Get-Process zen -ErrorAction SilentlyContinue).Path"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=3, creationflags=0x08000000 if os.name == 'nt' else 0)
        for linea in res.stdout.strip().splitlines():
            ruta_p = linea.strip().strip('"')
            if ruta_p.lower().endswith("zen.exe") and os.path.exists(ruta_p):
                _RUTA_ZEN_CACHE = ruta_p
                with open(archivo_cache, "w", encoding="utf-8") as f:
                    f.write(ruta_p)
                return ruta_p
    except Exception:
        pass

    # 2. Búsqueda en rutas habituales de instalación
    try:
        user = os.getlogin()
    except Exception:
        user = os.environ.get("USERNAME", "usuario")

    candidatos = [
        shutil.which("zen"),
        rf"C:\Users\{user}\AppData\Local\Programs\zen\zen.exe",
        rf"C:\Users\{user}\AppData\Local\Programs\Zen Browser\zen.exe",
        rf"C:\Users\{user}\AppData\Local\Programs\zen-browser\zen.exe",
        rf"C:\Users\{user}\AppData\Local\Zen\zen.exe",
        rf"C:\Users\{user}\AppData\Local\Zen Browser\zen.exe",
        rf"C:\Users\{user}\AppData\Local\zen-browser\zen.exe",
        rf"C:\Users\{user}\scoop\apps\zen-browser\current\zen.exe",
        r"C:\Program Files\Zen Browser\zen.exe",
        r"C:\Program Files\zen browser\zen.exe",
        r"C:\Program Files\Zen\zen.exe",
        r"C:\Program Files (x86)\Zen Browser\zen.exe"
    ]

    for c in candidatos:
        if c and os.path.exists(c):
            _RUTA_ZEN_CACHE = c
            with open(archivo_cache, "w", encoding="utf-8") as f:
                f.write(c)
            return c

    return None

def _hay_zen_abierto():
    """¿Hay ya un proceso zen.exe en el sistema? (para no abrir un navegador 2º)."""
    try:
        import psutil
        for p in psutil.process_iter(["name"]):
            try:
                if (p.info.get("name") or "").lower() == "zen.exe":
                    return True
            except Exception:
                pass
    except Exception:
        try:
            r = subprocess.run(["tasklist", "/FI", "IMAGENAME eq zen.exe", "/NH"],
                               capture_output=True, text=True, timeout=5,
                               creationflags=0x08000000)
            return "zen.exe" in (r.stdout or "")
        except Exception:
            pass
    return False


def abrir_url(url: str):
    # Si el Zen en 2º plano ya está gestionado (zen_bidi), navegamos la pestaña
    # única que controla el driver: cero ventanas/pestañas duplicadas.
    try:
        import zen_bidi
        z = zen_bidi.obtener()
        if z.esta_abierto():
            z.navegar(url, tiempo=45)
            return True
    except Exception as e:
        print(f"[zen abre de fondo]: {e}")
    zen = obtener_ruta_zen()
    if zen:
        if _hay_zen_abierto():
            # Reutiliza la ventana de Zen ya abierta (pestaña nueva, NO otra ventana)
            subprocess.Popen([zen, "--new-tab", url],
                             creationflags=0x08000000)
        else:
            subprocess.Popen([zen, url], creationflags=0x08000000)
    else:
        try:
            subprocess.Popen(["zen", url], creationflags=0x08000000)
        except Exception:
            webbrowser.open(url)

def buscar_duckduckgo(consulta: str):
    abrir_url(f"https://duckduckgo.com/?q={urllib.parse.quote_plus(consulta)}")
    return f"Buscando {consulta} en Zen Browser."

# --- 2. PROYECTOS DE ANDROID STUDIO ---
def abrir_proyecto_android(texto_orden: str):
    try:
        user = os.getlogin()
    except Exception:
        user = os.environ.get("USERNAME", "usuario")

    carpeta_proyectos = rf"C:\Users\{user}\AndroidStudioProjects"
    if not os.path.exists(carpeta_proyectos):
        return None

    nombre_busqueda = texto_orden.lower()
    for m in ["de mis proyectos", "mis proyectos", "proyecto de android studio", "android studio", "ábreme", "abre", "eh", "de"]:
        nombre_busqueda = nombre_busqueda.replace(m, "").strip()

    if not nombre_busqueda:
        return None

    mejor_carpeta = None
    for carpeta in os.listdir(carpeta_proyectos):
        ruta_completa = os.path.join(carpeta_proyectos, carpeta)
        if os.path.isdir(ruta_completa):
            if nombre_busqueda in carpeta.lower() or carpeta.lower() in nombre_busqueda:
                mejor_carpeta = ruta_completa
                break

    if mejor_carpeta:
        candidatos_studio = [
            r"C:\Program Files\Android\Android Studio\bin\studio64.exe",
            rf"C:\Users\{user}\AppData\Local\Programs\Android Studio\bin\studio64.exe"
        ]
        studio_exe = next((c for c in candidatos_studio if os.path.exists(c)), None)

        if studio_exe:
            subprocess.Popen([studio_exe, mejor_carpeta])
            return f"Abriendo '{os.path.basename(mejor_carpeta)}' en Android Studio."
        else:
            subprocess.Popen(["explorer.exe", mejor_carpeta])
            return f"Abriendo carpeta '{os.path.basename(mejor_carpeta)}'."

    return None

# --- 3. MOTOR DE STEAM ---
def steam_pairs(path):
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    return [(k, v.replace(r'\"', '"').replace('\\\\', '\\')) 
            for k, v in re.findall(r'"([^"\\]+)"\s*"((?:\\.|[^"\\])*)"', text)]

def steam_roots():
    roots = []
    if os.name == "nt":
        import winreg
        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
                try:
                    with winreg.OpenKey(hive, r"Software\Valve\Steam", 0, winreg.KEY_READ | view) as key:
                        for field in ("SteamPath", "InstallPath"):
                            try:
                                roots.append(Path(winreg.QueryValueEx(key, field)[0]))
                            except OSError:
                                pass
                except OSError:
                    pass
    for var in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(var):
            roots.append(Path(os.environ[var]) / "Steam")
    if os.path.exists(r"D:\SteamLibrary"):
        roots.append(Path(r"D:\SteamLibrary"))
    return list(dict.fromkeys(r for r in roots if r.is_dir()))

def installed_steam_games():
    roots = steam_roots()
    libraries = set(roots)
    for root in roots:
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if vdf.is_file():
            libraries.update(Path(val) for key, val in steam_pairs(vdf) if key == "path")
    found = []
    for lib in libraries:
        folder = lib / "steamapps"
        for manifest in folder.glob("appmanifest_*.acf"):
            fields = dict(steam_pairs(manifest))
            appid = fields.get("appid", "")
            name = fields.get("name", "")
            if appid.isdigit() and name:
                if not any(x in name.lower() for x in ["steamworks", "redistributable", "dedicated server"]):
                    found.append((name, f"steam://rungameid/{appid}"))
    return found

# --- 4. CATÁLOGO DE WINDOWS Y ALIAS ---
SPOKEN_NAMES = {
    "the binding": "the binding of isaac",
    "the binding of": "the binding of isaac",
    "binding of": "the binding of isaac",
    "de binding of": "the binding of isaac",
    "isaac": "the binding of isaac",
    "over": "overwatch 2",
    "overwatch": "overwatch 2",
    "day in the": "die in the dungeon",
    "10 in the": "die in the dungeon",
    "epic": "epic games launcher",
    "epic games": "epic games launcher",
    "ryujin": "ryujinx",
    "riujin": "ryujinx",
    "riujinx": "ryujinx",
    "ryujix": "ryujinx"
}

def normalize(text):
    text = unicodedata.normalize("NFKD", text.casefold())
    return " ".join(re.findall(r"[a-z0-9]+", "".join(c for c in text if not unicodedata.combining(c))))

@dataclass(frozen=True)
class App:
    name: str
    target: str
    kind: str

def discover_all_apps():
    apps = []
    for root in steam_roots():
        exe = root / "Steam.exe"
        if exe.is_file():
            apps.append(App("Steam", str(exe), "exe"))
    for name, url in installed_steam_games():
        apps.append(App(name, url, "steam"))

    for var in ("APPDATA", "PROGRAMDATA"):
        base = os.environ.get(var)
        if base:
            folder = Path(base) / "Microsoft/Windows/Start Menu/Programs"
            for p in folder.rglob("*.lnk"):
                apps.append(App(p.stem, str(p), "shortcut"))
    
    desktops = [Path.home() / "Desktop"]
    if os.environ.get("PUBLIC"):
        desktops.append(Path(os.environ["PUBLIC"]) / "Desktop")
    for d in desktops:
        for p in d.glob("*.lnk"):
            apps.append(App(p.stem, str(p), "shortcut"))

    if os.name == "nt":
        script = "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"
        powershell = str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe")
        try:
            res = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-Command", script],
                                 capture_output=True, text=True, encoding="utf-8", timeout=15, check=True,
                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            rows = json.loads(res.stdout.lstrip("\ufeff") or "[]")
            if isinstance(rows, dict):
                rows = [rows]
            for row in rows:
                if row.get("Name") and row.get("AppID"):
                    apps.append(App(row["Name"], row["AppID"], "appid"))
        except Exception:
            pass

    unique = {}
    for a in apps:
        unique.setdefault(normalize(a.name), a)
    return sorted(unique.values(), key=lambda a: a.name.casefold())

# --- 5. BÚSQUEDA PROFUNDA DE EJECUTABLES ---
def buscar_ejecutable_profundo(nombre_app: str):
    try:
        user = os.getlogin()
    except Exception:
        user = os.environ.get("USERNAME", "usuario")

    busqueda = nombre_app.lower().strip()
    carpetas_busqueda = [
        r"D:\\",
        rf"C:\Users\{user}\Desktop",
        rf"C:\Users\{user}\Downloads",
        rf"C:\Users\{user}\AppData\Local\Programs",
        rf"C:\Users\{user}\AppData\Roaming",
        rf"C:\Users\{user}\AppData\Local",
        r"C:\Program Files",
        r"C:\Program Files (x86)"
    ]

    carpetas_ignorar = {
        "windows", "$recycle.bin", "system volume information", "node_modules",
        ".git", "cache", "temp", "steamlibrary", "package cache", "winsxs"
    }

    for ruta_base in carpetas_busqueda:
        if not os.path.exists(ruta_base):
            continue

        for root, dirs, files in os.walk(ruta_base):
            dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in carpetas_ignorar]
            profundidad = root[len(ruta_base):].count(os.sep)
            if ("appdata" in root.lower() or "program files" in root.lower()) and profundidad > 3:
                continue
            if ruta_base == r"D:\\" and profundidad > 5:
                continue

            for archivo in files:
                if archivo.lower().endswith(".exe"):
                    archivo_clean = archivo.lower()
                    if busqueda in archivo_clean:
                        if not any(x in archivo_clean for x in ["uninstall", "unins", "crash", "update", "setup"]):
                            return os.path.join(root, archivo)
    return None

# --- 6. LANZADOR UNIVERSAL INTELIGENTE ---
def lanzar_cualquier_cosa(objetivo: str):
    busqueda = objetivo.lower().strip()

    if any(x in busqueda for x in ["proyecto", "cosas de casa", "android studio"]):
        res_proj = abrir_proyecto_android(busqueda)
        if res_proj:
            return res_proj

    if any(x in busqueda for x in ["disco", "carpeta", "este equipo", "mi pc", "descargas", "documentos"]):
        return abrir_carpeta_o_disco(busqueda)

    for prefijo in ["el ", "la ", "los ", "las ", "juego ", "app ", "programa "]:
        if busqueda.startswith(prefijo):
            busqueda = busqueda[len(prefijo):].strip()

    busqueda = SPOKEN_NAMES.get(busqueda, busqueda)
    if "stremio" in busqueda:
        serie = _limpiar_busqueda_stremio(busqueda)
        return abrir_stremio(serie, pantalla_completa=True)
    if "youtube music" in busqueda:
        abrir_url("https://music.youtube.com/")
        return "Abriendo YouTube Music en Zen Browser."
    elif "youtube" in busqueda:
        abrir_url("https://www.youtube.com/")
        return "Abriendo YouTube en Zen Browser."

    catalogo = discover_all_apps()
    query_norm = normalize(busqueda)

    match = next((a for a in catalogo if normalize(a.name) == query_norm), None)
    if not match:
        palabras = set(query_norm.split())
        match = next((a for a in catalogo if palabras <= set(normalize(a.name).split())), None)

    if not match:
        similares = []
        for a in catalogo:
            norm_a = normalize(a.name)
            if norm_a in ["run", "ejecutar"] and query_norm not in ["run", "ejecutar"]:
                continue
            ratio = SequenceMatcher(None, query_norm, norm_a).ratio()
            similares.append((ratio, a))

        similares.sort(reverse=True, key=lambda x: x[0])
        if similares and similares[0][0] >= 0.72:
            match = similares[0][1]

    if match:
        if match.kind == "exe":
            subprocess.Popen([match.target], shell=False)
        elif match.kind == "shortcut":
            os.startfile(match.target)
        elif match.kind == "appid":
            os.startfile("shell:AppsFolder\\" + match.target)
        elif match.kind == "steam":
            os.startfile(match.target)
        return f"Iniciando {match.name}."

    if "whatsapp" in busqueda:
        try:
            os.startfile("whatsapp:")
            return "Abriendo WhatsApp."
        except Exception:
            abrir_url("https://web.whatsapp.com/")
            return "Abriendo WhatsApp Web en Zen Browser."

    exe_encontrado = buscar_ejecutable_profundo(busqueda)
    if exe_encontrado:
        try:
            subprocess.Popen([exe_encontrado], shell=False, creationflags=0x08000000)
            return f"Iniciado {os.path.basename(exe_encontrado)}."
        except Exception as e:
            return f"Error al abrir {exe_encontrado}: {e}"

    return f"No encontré '{objetivo}'."

# --- 7. CARPETAS Y ARCHIVOS ---
def abrir_carpeta_o_disco(destino: str):
    d = destino.lower().strip()
    try:
        user = os.getlogin()
    except Exception:
        user = os.environ.get("USERNAME", "usuario")

    if any(x in d for x in ["disco d", "en d", "el d", "d:"]):
        subprocess.Popen(["explorer.exe", r"D:\\"])
        return "Abriendo el disco D."
    elif any(x in d for x in ["disco c", "en c", "el c", "c:"]):
        subprocess.Popen(["explorer.exe", r"C:\\"])
        return "Abriendo el disco C."
    if any(x in d for x in ["disco", "discos", "este equipo", "mi pc", "mis carpetas"]):
        subprocess.Popen(["explorer.exe", "shell:MyComputerFolder"])
        return "Abriendo Este Equipo."
    carpetas = {
        "descargas": rf"C:\Users\{user}\Downloads",
        "documentos": rf"C:\Users\{user}\Documents",
        "escritorio": rf"C:\Users\{user}\Desktop",
        "imagenes": rf"C:\Users\{user}\Pictures",
        "videos": rf"C:\Users\{user}\Videos"
    }
    for k, ruta in carpetas.items():
        if k in d:
            subprocess.Popen(["explorer.exe", ruta])
            return f"Abriendo carpeta {k.title()}."
    subprocess.Popen(["explorer.exe", "shell:MyComputerFolder"])
    return "Abriendo el Explorador de archivos."

def buscar_archivos_locales(nombre_archivo: str, abrir: bool = True):
    try:
        user = os.getlogin()
    except Exception:
        user = os.environ.get("USERNAME", "usuario")

    carpetas_clave = [
        rf"C:\Users\{user}\Desktop",
        rf"C:\Users\{user}\Documents",
        rf"C:\Users\{user}\Downloads",
        r"D:\\"
    ]
    busqueda = nombre_archivo.lower().strip()
    coincidencias = []
    for base in carpetas_clave:
        if not os.path.exists(base):
            continue
        for root, dirs, files in os.walk(base):
            dirs[:] = [d for d in dirs if not d.startswith('.') and d.lower() not in ['appdata', '$recycle.bin', 'windows', '.git']]
            if root[len(base):].count(os.sep) > 4:
                continue
            for f in files:
                if busqueda in f.lower():
                    coincidencias.append(os.path.join(root, f))
                    if len(coincidencias) >= 3:
                        break
            if len(coincidencias) >= 3:
                break
    if coincidencias:
        mejor = coincidencias[0]
        if abrir:
            subprocess.Popen(f'explorer.exe /select,"{mejor}"')
            return f"Encontré '{os.path.basename(mejor)}'."
        return f"Encontré: {os.path.basename(mejor)}"
    return f"No encontré '{nombre_archivo}'."

def ejecutar_comando_sistema(comando: str):
    try:
        subprocess.Popen(["powershell", "-NoProfile", "-Command", comando],
                         shell=True, creationflags=0x08000000)
        return "Comando ejecutado."
    except Exception as e:
        return f"Error en PowerShell: {e}"

# --- 8. CONTROL NATIVO DE VOLUMEN ---
VK_VOLUME_MUTE = 0xAD
VK_VOLUME_DOWN = 0xAE
VK_VOLUME_UP = 0xAF

def _presionar_tecla_volumen(vk_code, veces=1):
    for _ in range(veces):
        ctypes.windll.user32.keybd_event(vk_code, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk_code, 0, 2, 0)
        time.sleep(0.02)

def controlar_volumen(accion: str, valor: int = None):
    import audio_control
    act = accion.lower().strip()
    if "mute" in act or "silenc" in act:
        if audio_control.DISPONIBLE and audio_control.mutear(True) is not None:
            return "Audio silenciado."
        _presionar_tecla_volumen(VK_VOLUME_MUTE, 1)
        return "Audio silenciado."
    if "commutar" in act or "quitar" in act:
        if audio_control.DISPONIBLE and audio_control.mutear(False) is not None:
            return "Sonido restaurado."
        _presionar_tecla_volumen(VK_VOLUME_MUTE, 1)
        return "Sonido restaurado."
    if "sub" in act or "baj" in act:
        sube = "sub" in act
        paso = (valor or 10)
        if audio_control.DISPONIBLE:
            base = audio_control.volumen_maestro()
            if base is not None:
                nuevo = max(0, min(100, base + (paso if sube else -paso)))
                audio_control.volumen_maestro(nuevo)
                return f"Volumen {'subido' if sube else 'bajado'} al {nuevo}%."
        pasos = (valor // 2) if (valor and valor > 0) else 5
        _presionar_tecla_volumen(VK_VOLUME_UP if sube else VK_VOLUME_DOWN, pasos)
        return f"Volumen{' subido' if sube else ' bajado'}."
    if valor is not None:
        pct = max(0, min(100, valor))
        if audio_control.DISPONIBLE:
            audio_control.volumen_maestro(pct)
            return f"Volumen al {pct}%."
        _presionar_tecla_volumen(VK_VOLUME_DOWN, 50)
        _presionar_tecla_volumen(VK_VOLUME_UP, pct // 2)
        return f"Volumen al {pct}%."
    return "Volumen ajustado."

# --- 9. ACCIONES DE NAVEGADOR, SCROLL Y TECLADO ---
def hacer_scroll(direccion: str = "abajo", cantidad: int = 500):
    pasos = -cantidad if "baj" in direccion.lower() else cantidad
    # En segundo plano: rueda sobre el centro de la ventana activa sin mover el cursor
    try:
        import win32gui
        h = win32gui.GetForegroundWindow()
        r = win32gui.GetWindowRect(h)
        if r[2] - r[0] > 100 and r[3] - r[1] > 100:
            cx = (r[0] + r[2]) // 2
            cy = (r[1] + r[3]) // 2
            if hammer.scroll_en(cx, cy, pasos // 120 or pasos):
                return f"Scroll hacia {direccion} (en 2º plano)."
    except Exception:
        pass
    pyautogui.scroll(pasos)
    return f"Scroll hacia {direccion}."
# --- 10. ACTIVADOR DE VENTANAS EN SEGUNDO PLANO / BARRA DE TAREAS ---
def enfocar_o_lanzar_app(nombre_app: str):
    """Si la app ya está abierta en la barra de tareas la trae al frente; si no, la abre."""
    app_limpia = nombre_app.lower().strip()

    # Mapeo de nombres comunes a títulos de ventana de Windows
    alias = {
        "visual studio code": "Visual Studio Code",
        "visual studio": "Visual Studio Code",
        "vs code": "Visual Studio Code",
        "code": "Visual Studio Code",
        "overwatch": "Overwatch",
        "steamio": "Steam.io",
        "discord": "Discord",
        "spotify": "Spotify",
        "zen": "Zen Browser",
        "zen browser": "Zen Browser",
    }

    titulo_buscar = alias.get(app_limpia, app_limpia)

    # 1. Intentar traer al frente si ya está abierta en la barra de tareas
    try:
        cmd = f'(New-Object -ComObject WScript.Shell).AppActivate("{titulo_buscar}")'
        res = subprocess.run(["powershell", "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=2,
                             creationflags=0x08000000)
        if "True" in res.stdout:
            return f"Mostrando {titulo_buscar} de la barra de tareas."
    except Exception:
        pass

    # 2. Si no estaba abierta o no se pudo activar, la lanzamos desde el catálogo
    return lanzar_cualquier_cosa(titulo_buscar)
def escribir_texto_en_pantalla(texto: str, presionar_enter: bool = True):
    pyperclip.copy(texto)
    pyautogui.hotkey('ctrl', 'v')
    if presionar_enter:
        time.sleep(0.1)
        pyautogui.press('enter')
    return f"Escribiendo '{texto}'."

def control_navegador(accion: str):
    act = accion.lower()
    if "nueva" in act or "abrir pestaña" in act:
        pyautogui.hotkey('ctrl', 't')
        return "Nueva pestaña."
    elif "cerrar" in act or "cierra pestaña" in act:
        pyautogui.hotkey('ctrl', 'w')
        return "Pestaña cerrada."
    elif "siguiente" in act:
        pyautogui.hotkey('ctrl', 'tab')
        return "Siguiente pestaña."
    elif "anterior" in act:
        pyautogui.hotkey('ctrl', 'shift', 'tab')
        return "Pestaña anterior."
    elif "pantalla completa" in act:
        pyautogui.press('f11')
        return "Pantalla completa."
    elif "minimizar" in act:
        pyautogui.hotkey('win', 'd')
        return "Escritorio mostrado."
    return "Comando ejecutado."

def multimedia_play_pause():
    VK_MEDIA_PLAY_PAUSE = 0xB3
    ctypes.windll.user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, 0, 0)
    ctypes.windll.user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, 2, 0)
    return "Reproducción pausada o reanudada."

def multimedia_next_track():
    VK_MEDIA_NEXT_TRACK = 0xB0
    ctypes.windll.user32.keybd_event(VK_MEDIA_NEXT_TRACK, 0, 0, 0)
    ctypes.windll.user32.keybd_event(VK_MEDIA_NEXT_TRACK, 0, 2, 0)
    return "Siguiente canción."

def multimedia_prev_track():
    VK_MEDIA_PREV_TRACK = 0xB1
    ctypes.windll.user32.keybd_event(VK_MEDIA_PREV_TRACK, 0, 0, 0)
    ctypes.windll.user32.keybd_event(VK_MEDIA_PREV_TRACK, 0, 2, 0)
    return "Anterior canción."

_MUSICA_JS = {
    "siguiente": """return (()=>{const el=document.querySelector('ytmusic-player-bar .next-button')||Array.from(document.querySelectorAll('button[aria-label]')).find(b=>/siguiente|next|skip/i.test(b.getAttribute('aria-label')));if(el){el.click();return true;}return false;})();""",
    "anterior": """return (()=>{const el=document.querySelector('ytmusic-player-bar .previous-button')||Array.from(document.querySelectorAll('button[aria-label]')).find(b=>/anterior|previous|prev/i.test(b.getAttribute('aria-label')));if(el){el.click();return true;}return false;})();""",
    "reanudar": """return (()=>{const v=document.querySelector('video');if(v&&v.paused){v.play();return true;}return true;})();""",
    "pausa": """return (()=>{const v=document.querySelector('video');if(v&&!v.paused){v.pause();return true;}return true;})();""",
}

def controlar_musica(accion: str = "pausa"):
    """Controla la música (YouTube Music) EN SEGUNDO PLANO, SIN cambiar el foco.
    Si Pichu reproduce con su motor ligero (mpv) lo manda ahí; si no, prefiere el
    Zen gestionado por zen_bidi (DOM puro, cero foco); si tampoco hay un Zen de
    fondo activo, usa las teclas multimedia del sistema (van por el SMTC aunque
    estés jugando)."""
    act = (accion or "").strip().lower()
    try:
        import reproduccion
        if reproduccion.estado()["activo"]:
            if any(k in act for k in ("siguiente", "next", "otra", "cambi", "salta")):
                return reproduccion.siguiente()
            if any(k in act for k in ("anterior", "atras", "prev", "volver")):
                return reproduccion.anterior()
            if any(k in act for k in ("para", "pausa", "quita", "saca", "deten", "silencia", "calla")):
                return reproduccion.pausar()
            return reproduccion.reanudar()
    except Exception as e:
        print(f"[musica motor]: {e}")
    try:
        import zen_bidi
        z = zen_bidi.obtener()
        if z.esta_abierto():
            if any(k in act for k in ("siguiente", "next", "otra", "cambi", "salta")):
                if z.evaluar(_MUSICA_JS["siguiente"], tiempo=10):
                    return "Siguiente canción (2º plano)."
            elif any(k in act for k in ("anterior", "atras", "prev", "volver")):
                if z.evaluar(_MUSICA_JS["anterior"], tiempo=10):
                    return "Otra vez la canción anterior (2º plano)."
            else:
                js = _MUSICA_JS["reanudar"] if any(k in act for k in ("reanudar", "resum", "pon")) else _MUSICA_JS["pausa"]
                z.evaluar(js, tiempo=10)
                est = z.estado_video()
                st = "sonando" if est.get("ok") and not est.get("paused") else "en pausa"
                return f"Música {st} (2º plano)."
    except Exception as e:
        print(f"[musica zen]: {e}")

    if any(k in act for k in ("siguiente", "next", "otra", "cambi", "salta")):
        return multimedia_next_track()
    if any(k in act for k in ("anterior", "atras", "prev", "volver")):
        return multimedia_prev_track()
    return multimedia_play_pause()

    # --- CONTROL AUTOMÁTICO DE STREMIO ---
def _poner_stremio_fullscreen():
    """Trae Stremio al frente y activa pantalla completa.
    Primero F11 (si el foco coopera) y, como vía robusta (el foco suele quedar
    bloqueado en scripts), DOBLE CLIC sobre el vídeo, que no necesita foco."""
    user32 = ctypes.windll.user32
    screen_w = user32.GetSystemMetrics(0)
    screen_h = user32.GetSystemMetrics(1)
    try:
        import win32gui
        import win32con
        import vision as _vis
    except Exception:
        return

    def _handles():
        hs = []

        def _cb(h, _):
            if win32gui.IsWindowVisible(h) and "stremio" in (win32gui.GetWindowText(h) or "").lower():
                hs.append(h)

        try:
            win32gui.EnumWindows(_cb, None)
        except Exception:
            return []
        return hs

    def _cubre(h):
        r2 = wintypes.RECT()
        user32.GetWindowRect(h, ctypes.byref(r2))
        return (r2.left <= 0 and r2.top <= 0 and
                (r2.right - r2.left) >= screen_w and
                (r2.bottom - r2.top) >= screen_h)

    f11_enviado = False
    for _ in range(40):  # hasta ~20s mientras arranca / carga el episodio
        handles = _handles()
        if not handles:
            time.sleep(0.3)
            continue
        objetivo = max(handles, key=lambda h: len(win32gui.GetWindowText(h)))
        try:
            placement = win32gui.GetWindowPlacement(objetivo)
            if placement[1] == win32con.SW_SHOWMINIMIZED:
                win32gui.ShowWindow(objetivo, win32con.SW_RESTORE)
        except Exception:
            pass
        user32.keybd_event(0x12, 0, 0, 0)
        try:
            win32gui.SetForegroundWindow(objetivo)
        finally:
            user32.keybd_event(0x12, 0, 0x2, 0)
        time.sleep(0.25)

        if _cubre(objetivo):
            return

        if win32gui.GetForegroundWindow() == objetivo and not f11_enviado:
            pyautogui.press('f11')
            f11_enviado = True
            time.sleep(0.8)
            if _cubre(objetivo):
                return

        # Doble clic sobre el centro del vídeo (vía ratón: funciona aunque el
        # foco siga en otra ventana). Solo si la zona parece el reproductor
        # (pocas líneas de texto). Se repite cada vez que vuelva a haber un
        # reproductor visible sin pantalla completa.
        try:
            r = wintypes.RECT()
            user32.GetWindowRect(objetivo, ctypes.byref(r))
            ancho = r.right - r.left
            alto = r.bottom - r.top
            if ancho > 200 and alto > 150:
                zona = (r.left + ancho // 4, r.top + alto // 4,
                        r.right - ancho // 4, r.bottom - alto // 4)
                lineas = _vis.ocr_lineas(zona)
                if len(lineas) <= 8:
                    cx = r.left + ancho // 2
                    cy = r.top + alto // 2
                    time.sleep(0.6)
                    # Doble clic FANTASMA (sin mover el cursor); si la ventana
                    # no procesa mensajes, respaldo a ratón real.
                    if not hammer.doble_clic_en(cx, cy):
                        pyautogui.doubleClick(cx, cy)
                    time.sleep(0.9)
                    if _cubre(objetivo):
                        return
        except Exception:
            pass
        time.sleep(0.25)

def _limpiar_busqueda_stremio(texto: str) -> str:
    """Deja solo el nombre de la serie; quita verbos de acción, 'stremio', 'en/la/Las pantalla completa' y coletillas tipo 'y pon el primer episodio'."""
    t = (texto or "").lower().strip()
    t = re.sub(r"^(?:oye|hey|eh|ey|oiga|escucha|a ver|venga|bueno)\s+(?:pichu\s*)?", "", t)
    t = re.sub(r"\b(?:pichu|picu|pixu|pi\s?chu)\b", " ", t)
    t = re.sub(r"^abre\s+stremio(?:\s+y\s*)?", "", t)
    t = re.sub(r"\b(en\s+)?(la\s+)?pantalla\s+completa\b", " ", t)
    t = re.sub(r"\s+en\s+stremio\b", " ", t)
    t = re.sub(r"\bstremio\b", " ", t)
    t = re.sub(r"\b(?:el\s+)?(?:primer|siguiente|proximo|siguient)\s+(?:episodio|capitulo)\b", " ", t)
    t = re.sub(r"\b(?:el\s+)?(?:episodio|cap[ií]tulo)\s+que\s+(?:me\s+)?toca(?:\s+ver)?\b", " ", t)
    t = re.sub(r"\s+y\s+(?:pon|pone|ponme|reproduce|reproducir|reproducime|dame|ver|dale)\b.*", " ", t)
    t = re.sub(r"^\s*(?:abre|abrir|pon|pone|ponme|reproduce|reproducir|reproducime|dame|busca|buscar|ver)\s+", "", t)
    t = re.sub(r"^(?:a\s+)?(?:la\s+)?(?:serie|series)\s+", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    if t in ["esta serie", "la serie", "una serie", "algo", "una peli", "una pelicula", "eso", "eso mismo"]:
        return ""
    return t


def abrir_stremio(serie: str = "", pantalla_completa: bool = True):
    serie_limpia = _limpiar_busqueda_stremio(serie)

    # 1. Abrir con búsqueda directa o abrir Stremio normal
    if serie_limpia:
        url = f"stremio:///search?search={urllib.parse.quote(serie_limpia)}"
        try:
            os.startfile(url)
        except Exception:
            enfocar_o_lanzar_app("Stremio")
        mensaje = f"Buscando '{serie_limpia}' en Stremio."
    else:
        try:
            os.startfile("stremio:///")
        except Exception:
            enfocar_o_lanzar_app("Stremio")
        mensaje = "Abriendo Stremio."

    # 2. Hilo en segundo plano para poner pantalla completa sin frenar la voz de Pichu
    if pantalla_completa:
        threading.Thread(target=_poner_stremio_fullscreen, daemon=True).start()
        mensaje += " Poniendo pantalla completa."

    return mensaje