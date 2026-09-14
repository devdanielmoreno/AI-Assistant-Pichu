import atexit
import base64
import io
import json
import os
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
import zipfile

import ctypes
from ctypes import wintypes

_BASE = os.path.dirname(os.path.abspath(__file__))
GECKODRIVER = os.path.join(_BASE, "geckodriver", "geckodriver.exe")
PERFIL = os.path.join(_BASE, "zen_pichu_profile")
GECKO_LOG = os.path.join(_BASE, "zen_gecko.log")
_ACTIVO = None

PREFS = """\
user_pref("media.autoplay.default", 0);
user_pref("media.autoplay.blocking_policy", 0);
user_pref("media.block-autoplay-until-in-foreground", false);
user_pref("browser.startup.page", 0);
user_pref("browser.startup.homepage", "about:blank");
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("startup.homepage_welcome_url", "");
user_pref("startup.homepage_welcome_url.additional", "");
user_pref("browser.aboutwelcome.enabled", false);
user_pref("toolkit.telemetry.enabled", false);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("app.update.auto", false);
user_pref("media.videocontrols.hidden", true);
"""


def _ruta_zen():
    for c in (os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "Zen Browser", "zen.exe"),
              os.path.join(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"), "Zen Browser", "zen.exe"),
              os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "zen", "zen.exe")):
        if c and os.path.exists(c):
            return c
    try:
        import actions
        return actions.obtener_ruta_zen()
    except Exception:
        return None


def _puerto_libre():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _preparar_perfil():
    os.makedirs(PERFIL, exist_ok=True)
    ruta = os.path.join(PERFIL, "user.js")
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            actual = f.read()
    except Exception:
        actual = ""
    if actual != PREFS:
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(PREFS)


def _perfil_b64():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, archivos in os.walk(PERFIL):
            for fn in archivos:
                ruta = os.path.join(root, fn)
                z.write(ruta, os.path.relpath(ruta, PERFIL).replace("\\", "/"))
    return base64.b64encode(buf.getvalue()).decode()


class ZenBidi:
    """Instancia de Zen de Pichu en segundo plano, conducida por WebDriver
    (geckodriver). Todo se hace por JS inyectado: ni mueve el ratón ni roba
    el foco salvo que le pidas traer algo al frente."""

    def __init__(self, binario=None):
        self.gd = None
        self.sid = None
        self._base = None
        self.proceso = None
        self._wnd = None
        self._bloqueo_hwnd = threading.Lock()
        self._binario = binario or _ruta_zen()

    # ---- sesión ----
    def arrancar(self, tiempo_espera=70):
        if self.sid:
            return True
        _preparar_perfil()
        if not os.path.exists(GECKODRIVER):
            raise RuntimeError(f"Falta geckodriver en {GECKODRIVER}")
        if not self._binario or not os.path.exists(self._binario):
            raise RuntimeError("No encuentro el ejecutable de Zen Browser.")

        puerto = _puerto_libre()
        self._base = f"http://127.0.0.1:{puerto}"
        self.gd = subprocess.Popen(
            [GECKODRIVER, "--port", str(puerto), "--binary", self._binario],
            stdout=subprocess.DEVNULL,
            stderr=open(GECKO_LOG, "a", encoding="utf-8", errors="replace"),
            creationflags=0x08000000,  # CREATE_NO_WINDOW: nada de ventana de cmd
        )
        # No dejes que se vea ni un instante: un hilo lo minimiza en cuanto
        # aparece la ventana (aunque la sesión tarde en arrancar).
        self._mantener_minimizado(dur=15)
        fin = time.time() + tiempo_espera
        while time.time() < fin:
            try:
                urllib.request.urlopen(self._base + "/status", timeout=2).read()
                break
            except Exception:
                time.sleep(0.3)
        else:
            self._matar_proceso()
            raise RuntimeError("geckodriver no arrancó.")

        caps = {"capabilities": {"alwaysMatch": {
            "browserName": "firefox",
            "acceptInsecureCerts": True,
            "moz:firefoxOptions": {"args": ["-no-remote"], "profile": _perfil_b64()},
        }}}
        try:
            valor = self._post("/session", caps)
        except urllib.error.HTTPError as e:
            self._matar_proceso()
            raise RuntimeError("Zen no aceptó la sesión WebDriver: " + e.read().decode(errors="replace")[:300])
        except Exception:
            self._matar_proceso()
            raise

        self.sid = valor.get("sessionId")
        self.proceso = (valor.get("capabilities") or {}).get("moz:processID")
        if not self.sid:
            self._matar_proceso()
            raise RuntimeError("geckodriver no dio sessionId.")
        atexit.register(self.cerrar)
        self.minimizar()
        return True

    def _post(self, ruta, body, tiempo=30):
        req = urllib.request.Request(self._base + ruta,
                                     data=json.dumps(body).encode("utf-8"),
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        with urllib.request.urlopen(req, timeout=tiempo) as r:
            return json.loads(r.read().decode("utf-8")).get("value", {})

    def _get(self, ruta, tiempo=20):
        with urllib.request.urlopen(self._base + ruta, timeout=tiempo) as r:
            return json.loads(r.read().decode("utf-8")).get("value", None)

    def navegar(self, url, tiempo=90):
        self._post(f"/session/{self.sid}/url", {"url": url}, tiempo=tiempo)

    def evaluar(self, expr, tiempo=25):
        ultimo = None
        for _ in range(3):
            try:
                return self._post(f"/session/{self.sid}/execute/sync",
                                  {"script": expr, "args": []}, tiempo=tiempo)
            except Exception as e:  # esta build de Zen es intermitente
                ultimo = e
                time.sleep(0.5)
        raise RuntimeError(f"evaluar JS falló: {ultimo}")

    def _mantener_minimizado(self, dur=15):
        """Minimiza la ventana de Zen en cuanto aparece y durante `dur` segundos."""
        def _loop():
            fin = time.time() + dur
            while time.time() < fin:
                h = self._hwnd()
                if h:
                    try:
                        ctypes.windll.user32.ShowWindow(h, 6)  # SW_MINIMIZE
                    except Exception:
                        pass
                time.sleep(0.2)
        threading.Thread(target=_loop, daemon=True).start()

    def _consentimiento(self, tiempo=25):
        """Acepta el aviso de cookies ('Before you continue') de YouTube/Música."""
        fin = time.time() + tiempo
        while time.time() < fin:
            try:
                href = self.evaluar("return location.href;", tiempo=8) or ""
            except Exception:
                href = ""
            if "consent" not in href.lower():
                return True
            js = ("return (()=>{const e=[...document.querySelectorAll("
                  "'button,[role=button],input[type=submit]')]"
                  ".find(x=>{const t=((x.textContent||x.value||'')+' '"
                  "+(x.getAttribute('aria-label')||'')).toLowerCase();"
                  "return /accept|i agree|aceptar|continua/.test(t);});"
                  "if(e){e.click();return true;}return false;})();")
            try:
                if self.evaluar(js, tiempo=8):
                    time.sleep(1.8)
                    continue
            except Exception:
                pass
            time.sleep(1.0)
        try:
            return "consent" not in (self.evaluar("return location.href;") or "")
        except Exception:
            return False

    def esperar(self, selector, tiempo=25):
        """Espera a que el selector exista (varias comprobaciones JS breves)."""
        js = f"return !!document.querySelector({json.dumps(selector)});"
        fin = time.time() + tiempo
        while time.time() < fin:
            try:
                if self.evaluar(js, tiempo=8):
                    return True
            except Exception:
                pass
            time.sleep(0.4)
        return False

    def titulo(self):
        try:
            return self._get(f"/session/{self.sid}/title") or ""
        except Exception:
            return ""

    def asegurar(self):
        try:
            self._get(f"/session/{self.sid}/title", tiempo=3)
            return self.sid is not None
        except Exception:
            self.cerrar()
            try:
                return self.arrancar()
            except Exception:
                return False

    # ---- control de la ventana ----
    def _hwnd(self):
        with self._bloqueo_hwnd:
            if self._wnd:
                return self._wnd
            if not self.proceso:
                return None
            user32 = ctypes.windll.user32
            encontrado = []

            def _cb(h, _):
                if user32.IsWindowVisible(h):
                    pid = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
                    if pid.value == self.proceso:
                        encontrado.append(h)
                return True

            WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            user32.EnumWindows(WNDENUMPROC(_cb), 0)
            if encontrado:
                self._wnd = encontrado[0]
            return self._wnd

    def minimizar(self):
        h = self._hwnd()
        if h:
            ctypes.windll.user32.ShowWindow(h, 6)  # SW_MINIMIZE

    def restaurar(self):
        h = self._hwnd()
        if h:
            ctypes.windll.user32.ShowWindow(h, 9)  # SW_RESTORE

    def traer_al_frente(self):
        h = self._hwnd()
        if h:
            ctypes.windll.user32.ShowWindow(h, 9)
            ctypes.windll.user32.SetForegroundWindow(h)

    def esta_abierto(self):
        return self.sid is not None

    # ---- helpers para YouTube ----
    def buscar_videos_youtube(self, tema, limite=6):
        from urllib.parse import quote_plus
        self.navegar("https://www.youtube.com/results?search_query=" + quote_plus(tema))
        self._consentimiento()
        self.esperar("a[href*='/watch']", tiempo=25)
        js = ("return (()=>{const a=[...document.querySelectorAll('a')]"
              ".filter(x=>x&&(x.href||'').includes('/watch')&&(x.getAttribute('title')||x.textContent||'').trim().length>3)"
              ".map(x=>({t:(x.getAttribute('title')||x.textContent||'').trim(),u:x.href||''}));"
              "const vistos=new Set();const salida=[];"
              "for(const v of a){if(vistos.has(v.u.split('&')[0]))continue;vistos.add(v.u.split('&')[0]);salida.push(v);if(salida.length>=" + str(limite) + ")break;}"
              "return salida;})();")
        for _ in range(8):
            try:
                r = self.evaluar(js)
                if isinstance(r, list) and r:
                    return r
            except Exception:
                pass
            time.sleep(0.7)
        return []

    def estado_video(self):
        js = ("return (()=>{const v=document.querySelector('video');if(!v)return{ok:false};"
              "return{ok:true,pausado:v.paused,paused:v.paused,titulo:(document.title||''),"
              "hora:v.currentTime||0,dur:v.duration||0,muted:v.muted,vol:v.volume};})();")
        try:
            r = self.evaluar(js)
            return r if isinstance(r, dict) else {"ok": False}
        except Exception:
            return {"ok": False}

    def reproducir_tab(self):
        try:
            return bool(self.evaluar("return (()=>{const v=document.querySelector('video');if(v)v.play();return !!v;})();"))
        except Exception:
            return False

    def pausar_tab(self):
        try:
            return bool(self.evaluar("return (()=>{const v=document.querySelector('video');if(v)v.pause();return !!v;})();"))
        except Exception:
            return False

    # ---- helpers para YouTube Music ----
    def aceptar_avisos_yt(self):
        """Cierra avisos (consentimiento / 'Prueba Premium') si aparecen."""
        js = ("return (()=>{const b=[...document.querySelectorAll('button')]"
              ".filter(x=>{const t=(x.textContent||'').trim().toLowerCase();"
              "const a=(x.getAttribute('aria-label')||'').toLowerCase();"
              "return /aceptar|accept|entendido|continuar|rechazar|quiz[sá]s m[aá]s tarde|no, gracias|tal vez m[aá]s tarde|detr[aá]s/.test(t+a)||"
              "a.includes('accept')||a.includes('dismiss');});"
              "if(b.length){b[0].click();return true;}return false;})();")
        try:
            return bool(self.evaluar(js))
        except Exception:
            return False

    def buscar_musica_youtube(self, tema, limite=6):
        from urllib.parse import quote_plus
        self.navegar("https://music.youtube.com/search?q=" + quote_plus(tema))
        self._consentimiento()
        if not self.esperar("ytmusic-search-page, ytmusic-responsive-list-item-renderer, ytmusic-shelf-renderer",
                            tiempo=35):
            return []
        self.aceptar_avisos_yt()
        js = ("return (()=>{const f=[...document.querySelectorAll('ytmusic-responsive-list-item-renderer')];"
              "const salida=[];"
              "for(const el of f){"
              "const t=(el.getAttribute('title')||el.querySelector('.title,span.title')?.textContent||"").trim();"
              "const s=(el.querySelector('.subtitle,span.subtitle')?.textContent||'').trim();"
              "if(t)salida.push({t,s});"
              "if(salida.length>=" + str(limite) + ")break;"
              "}return salida;})();")
        for _ in range(12):
            try:
                r = self.evaluar(js)
                if isinstance(r, list) and r:
                    return r
            except Exception:
                pass
            time.sleep(1.2)
        return []

    def reproducir_cancion(self, idx=0):
        js = ("return (()=>{const f=[...document.querySelectorAll('ytmusic-responsive-list-item-renderer')];"
              "const el=f[" + str(idx) + "];if(!el)return false;"
              "const b=el.querySelector(\"button#play-button, ytmusic-play-button-renderer button, "
              "button[aria-label*='Reproducir'], button[aria-label*='Play']\");"
              "if(b){b.click();return true;}"
              "const a=el.querySelector('.title a, a#video-title');if(a){a.click();return true;}"
              "el.click();return true;})();")
        try:
            return bool(self.evaluar(js))
        except Exception:
            return False

    def esperar_reproduccion(self, tiempo=22):
        fin = time.time() + tiempo
        while time.time() < fin:
            v = self.estado_video()
            if v.get("ok") and not v.get("paused"):
                return v
            time.sleep(0.6)
        return self.estado_video()

    def volumen_tab(self, pct):
        try:
            self.evaluar(f"return (function(){{const v=document.querySelector('video');"
                         f"if(v)v.volume=Math.max(0,Math.min(1,{pct}/100));return !!v;}})();")
            return True
        except Exception:
            return False

    def cerrar(self):
        if self.sid and self._base:
            try:
                urllib.request.urlopen(
                    urllib.request.Request(f"{self._base}/session/{self.sid}", method="DELETE"),
                    timeout=6)
            except Exception:
                pass
        self.sid = None
        self._wnd = None
        self._matar_proceso()

    def _matar_proceso(self):
        if self.proceso:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proceso)],
                               capture_output=True, timeout=10, creationflags=0x08000000)
            except Exception:
                pass
            self.proceso = None
        if self.gd:
            try:
                self.gd.terminate()
                try:
                    self.gd.wait(timeout=5)
                except Exception:
                    self.gd.kill()
            except Exception:
                pass
            self.gd = None


def obtener():
    global _ACTIVO
    if _ACTIVO is None:
        _ACTIVO = ZenBidi()
    return _ACTIVO


def estado_texto():
    if not _ACTIVO or not _ACTIVO.esta_abierto():
        return "Zen en segundo plano NO está arrancado."
    v = _ACTIVO.estado_video()
    if v.get("ok"):
        st = "sonando" if not v.get("paused") else "en pausa"
        return f"Zen en 2º plano arrancado; vídeo {st} ('{v.get('titulo','')[:40]}')."
    return "Zen en 2º plano arrancado (sin vídeo abierto)."


if __name__ == "__main__":
    z = obtener()
    if z.arrancar():
        import time
        vids = z.buscar_videos_youtube("overwatch guia", limite=4)
        print("Videos:", json.dumps([v["t"] for v in vids], ensure_ascii=False))
        if vids:
            z.navegar(vids[0]["u"])
            z.esperar("video", tiempo=50)
            time.sleep(4)
            print("Estado:", json.dumps(z.estado_video(), ensure_ascii=False))
            z.pausar_tab()
        z.cerrar()