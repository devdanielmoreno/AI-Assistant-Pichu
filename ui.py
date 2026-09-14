import os
import sys
import json
import urllib.parse
import http.server
import socketserver
import threading

import mute

# Asegurar que Qt use OpenGL compartido antes de cualquier ventana
from PyQt5.QtCore import Qt, QPoint, QPropertyAnimation, QEasingCurve, pyqtSignal, QEvent, QUrl, QTimer, QRectF
from PyQt5.QtWidgets import QApplication, QWidget, QMenu, QVBoxLayout, QSystemTrayIcon, QStyle, QLineEdit, QLabel
from PyQt5.QtGui import QCursor, QIcon, QPixmap, QPainter, QColor, QBrush, QPen
from PyQt5.QtSvg import QSvgRenderer
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEngineSettings, QWebEnginePage

CONFIG_FILE = "pichu_pos.json"
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PICHU_DIR = os.path.abspath(os.path.join(_BASE_DIR, "assets", "Pichu"))
UI_PACK_DIR = os.path.abspath(os.path.join(_BASE_DIR, "UI_Pack"))

# Rutas QSS hacia el pack (forward-slashes, absolutas)
_PACK = UI_PACK_DIR.replace("\\", "/")
_IMG_NOTAS = f"url({_PACK}/Containers/Containers/NoteContainerSimple.png)"
_IMG_BOTON = f"url({_PACK}/Buttons/Round/RoundButton2_wood.png)"
_IMG_BOTON_FOCUS = f"url({_PACK}/Buttons/Square/SquareButton2_wood.png)"

# Marrón madera / pergamino del Cozy UI Pack
_MENU_QSS = f"""
QMenu {{
    background-color: #f6ead0;
    color: #3e2a17;
    border: 2px solid #8a5a2b;
    border-radius: 10px;
    padding: 4px;
    font-family: 'Segoe UI', sans-serif;
    font-size: 12px;
}}
QMenu::item {{
    padding: 5px 10px 5px 8px;
}}
QMenu::item:selected {{
    background-color: #8a5a2b;
    color: #fff3da;
    border-radius: 6px;
}}
QMenu::separator {{
    height: 2px;
    background: #c9a86a;
    margin: 4px 8px;
}}
QMenu::indicator {{
    width: 14px;
    height: 14px;
    padding-left: 2px;
}}
"""

_SVG_MIC = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            'stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
            '<rect x="9" y="2.5" width="6" height="11" rx="3"/>'
            '<path d="M5.5 11.5a6.5 6.5 0 0 0 13 0"/>'
            '<path d="M12 18v3"/></svg>')
_SVG_MIC_OFF = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
                'stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">'
                '<rect x="9" y="2.5" width="6" height="11" rx="3"/>'
                '<path d="M5.5 11.5a6.5 6.5 0 0 0 13 0"/>'
                '<path d="M12 18v3"/></svg>'
                '<path d="M4.5 4.5l15 15"/></svg>')

def _icono_svg(svg: str, color: str):
    """Renderiza un SVG plano (vectorial, dibujado en caliente) a un QIcon."""
    pix = QPixmap(24, 24)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    renderer = QSvgRenderer(bytes(svg.replace("{color}", color).encode("utf-8")))
    renderer.render(p)
    p.end()
    return QIcon(pix)

def _icono_badge(glyph: str, color: str):
    """Icono tipo 'app': cuadrado redondeado relleno de color con el glifo en
    blanco dentro. Más vivo que el trazo suelto."""
    tam = 22
    pix = QPixmap(tam, tam)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor(color)))
    p.drawRoundedRect(0, 0, tam, tam, 6, 6)
    ren = QSvgRenderer(bytes(glyph.replace("{color}", "#FFFFFF").encode("utf-8")))
    ren.render(p, QRectF(3, 3, tam - 6, tam - 6))
    p.end()
    return QIcon(pix)

def _icono_punto(color: str):
    """Círculo plano de color, como icono de estado (expresiones)."""
    pix = QPixmap(16, 16)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    p.setBrush(QBrush(QColor(color)))
    p.drawEllipse(1, 1, 14, 14)
    p.end()
    return QIcon(pix)

HTML_CONTENT = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <style>
        * { margin: 0; padding: 0; overflow: hidden; user-select: none; }
        body, html {
            width: 100%;
            height: 100%;
            background: transparent !important;
        }
        #canvas { position: relative; width: 100%; height: 100%; display: block; z-index: 1; }
        #fx {
            position: absolute;
            top: 0; left: 0;
            width: 100%; height: 100%;
            pointer-events: none;
            z-index: 5;
        }
    </style>

    <script src="https://cubism.live2d.com/sdk-web/cubismcore/live2dcubismcore.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/pixi.js@6.5.8/dist/browser/pixi.min.js"></script>
    <script src="https://cdn.jsdelivr.net/npm/pixi-live2d-display@0.4.0/dist/cubism4.min.js"></script>

    <script>
        window._isSpeaking = false;
        window._currentExp = "idle";
        window._model = null;

        window.setSpeaking = function(state) {
            window._isSpeaking = !!state;
            if (!state && window._model) {
                window._model.internalModel.coreModel.setParameterValueById('ParamMouthOpenY', 0);
            }
        };

        window.setExpression = function(exp) {
            window._currentExp = exp;
            if (window._model) {
                if (!exp || exp === 'idle') window._model.expression();
                else window._model.expression(exp);
            }
        };

        window.updateGlobalMouse = function(normX, normY) {
            if (window._model && window._model.focus) {
                const cx = (normX + 1) * 0.5 * window.innerWidth;
                const cy = (normY + 1) * 0.5 * window.innerHeight;
                window._model.focus(cx, cy);
            }
        };

        // Bounding box normalizado (0-1) del cuerpo del modelo: sirve para que
        // arrastrar Pichu solo funcione si pulsas encima del muñeco y no en el
        // aire del recuadro. Padding pequeño para perdonar el pelo/orejas.
        window._modelBox = function() {
            if (!window._model) return null;
            const s = window._model.scale.x;
            const pad = 1.12;
            const w = s * window._model.width * 0.5 * pad;
            const h = s * window._model.height * 0.5 * pad;
            const cx = window.innerWidth / 2;
            const cy = window.innerHeight / 2 + 15;
            return {
                x0: (cx - w) / window.innerWidth,
                y0: (cy - h) / window.innerHeight,
                x1: (cx + w) / window.innerWidth,
                y1: (cy + h) / window.innerHeight
            };
        };

        // ---- Explosión de polvo (estilo cartoon) para entrar y salir ----

        function _fxCtx() {
            if (window._fx) return window._fx;
            const c = document.getElementById('fx');
            window._fx = { c: c, ctx: c.getContext('2d') };
            return window._fx;
        }

        window._fxRaf = null;
        window._fxSafety = null;
        window.clearDust = function () {
            // Limpia SIEMPRE el lienzo de polvo: evita quedarse 'pillado' en la
            // explosión si el navegador congela los frames (ventana tapada).
            try {
                const fx = _fxCtx();
                const dpr = window.devicePixelRatio || 1;
                fx.c.width = window.innerWidth * dpr;
                fx.c.height = window.innerHeight * dpr;
                fx.ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
            } catch (e) { /* nada */ }
        };
        window.playDust = function (opts) {
            if (window._fxRaf) { cancelAnimationFrame(window._fxRaf); window._fxRaf = null; window.clearDust(); }
            if (window._fxSafety) { clearTimeout(window._fxSafety); window._fxSafety = null; }
            const a = opts || {};
            const cx = a.x != null ? a.x : window.innerWidth / 2;
            const cy = a.y != null ? a.y : window.innerHeight / 2 + 40;
            const scale = a.scale || 1.1;
            const fx = _fxCtx();
            const dpr = window.devicePixelRatio || 1;
            fx.c.width = window.innerWidth * dpr;
            fx.c.height = window.innerHeight * dpr;
            const ctx = fx.ctx;
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

            // Humo típico de bomba ninja: lóbulos REDONDOS blancos de cel se expanden girando
            const humo = [];
            const n = 34;
            for (let i = 0; i < n; i++) {
                const ang0 = Math.random() * Math.PI * 2;
                const dist0 = 5 + Math.random() * 22 * scale;
                humo.push({
                    ang0: ang0,
                    dist0: dist0,
                    sf: 0.55 + Math.random() * 0.85,
                    sw: (Math.random() - 0.5) * 1.15,
                    r: (9 + Math.random() * 15) * scale,
                    fase: Math.random() * Math.PI * 2,
                    claro: (i % 3 === 0)
                });
            }
            // Líneas de impacto blancas (destello del 'POOF')
            const rayos = [];
            for (let i = 0; i < 12; i++) {
                rayos.push({
                    ang: (i / 12) * Math.PI * 2 + Math.random() * 0.4,
                    largo: (24 + Math.random() * 30) * scale,
                    ancho: (2.2 + Math.random() * 2.4) * scale,
                    retraso: Math.random() * 0.05
                });
            }
            // Polvillo fino flotando
            const granos = [];
            for (let i = 0; i < 22; i++) {
                const ang = Math.random() * Math.PI * 2;
                const dist = Math.sqrt(Math.random()) * 50 * scale;
                granos.push({
                    x: cx + Math.cos(ang) * dist,
                    y: cy + Math.sin(ang) * dist * 0.7,
                    vx: (Math.random() - 0.5) * 110 * scale,
                    vy: -16 * scale + Math.random() * 24 * scale,
                    r: (1 + Math.random() * 1.8) * scale,
                    a: 0.4 + Math.random() * 0.3,
                    d: 0.95 + Math.random() * 0.03
                });
            }
            // Chispas que salen disparadas
            const chispas = [];
            for (let i = 0; i < 9; i++) {
                const ang = Math.random() * Math.PI * 2;
                const vel = (160 + Math.random() * 240) * scale;
                chispas.push({
                    x: cx, y: cy,
                    vx: Math.cos(ang) * vel,
                    vy: Math.sin(ang) * vel - 40 * scale,
                    r: (1.5 + Math.random() * 3) * scale,
                    relleno: (i % 3 === 0) ? '255,246,220' : '188,180,158'
                });
            }
            const inicio = performance.now();
            const dur = 950;
            let previo = inicio;
            const GUSTO_X = 16 * scale;
            const GUSTO_Y = -12 * scale;
            const alcanza = (60 + Math.random() * 30) * scale;
            function suave(v) { return 1 - (1 - v) * (1 - v); }

            function frame(t) {
                const dt = Math.min(30, Math.max(0, t - previo));
                previo = t;
                const p = Math.max(0, Math.min(1, (t - inicio) / dur));
                const q = suave(p);
                ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);

                // Destello inicial: núcleo blanco + líneas de impacto radiales
                if (p < 0.20) {
                    const k = p / 0.20;
                    ctx.globalAlpha = (1 - k) * 0.9;
                    ctx.fillStyle = '#ffffff';
                    ctx.beginPath();
                    ctx.arc(cx, cy, (4 + k * 40) * scale, 0, Math.PI * 2);
                    ctx.fill();
                    ctx.globalAlpha = 1;
                    for (const ry of rayos) {
                        if (p < ry.retraso) continue;
                        const kk = (p - ry.retraso) / (0.20 - ry.retraso);
                        if (kk > 1) continue;
                        const r1 = 16 * scale + kk * ry.largo;
                        ctx.strokeStyle = 'rgba(255,255,255,' + (0.95 * (1 - kk)) + ')';
                        ctx.lineWidth = ry.ancho * (1 - kk * 0.5);
                        ctx.beginPath();
                        ctx.moveTo(cx + Math.cos(ry.ang) * 16 * scale, cy + Math.sin(ry.ang) * 16 * scale);
                        ctx.lineTo(cx + Math.cos(ry.ang) * r1, cy + Math.sin(ry.ang) * r1);
                        ctx.stroke();
                    }
                }

                // Rama de choque tenue (rompe una vez)
                if (p < 0.5) {
                    const rr = (18 + q * 60) * scale;
                    ctx.beginPath();
                    ctx.arc(cx, cy, Math.max(0, rr), 0, Math.PI * 2);
                    ctx.lineWidth = Math.max(0, 3.5 * scale * (1 - p * 2));
                    ctx.strokeStyle = 'rgba(190,184,166,' + (0.4 * (1 - p * 2)) + ')';
                    ctx.stroke();
                }

                // Nube de humo blanco: lóbulos que se expanden girando (billow)
                const wob = performance.now() * 0.0015;
                for (const h of humo) {
                    const ang = h.ang0 + h.sw * p * 2.4;
                    const d = h.dist0 + q * alcanza * h.sf;
                    const x = cx + Math.cos(ang) * d + GUSTO_X * p + Math.sin(wob + h.fase) * 2.2 * scale;
                    const y = cy + Math.sin(ang) * d * 0.78 + GUSTO_Y * p * 1.7 + Math.cos(wob + h.fase * 1.3) * 2 * scale;
                    const r = h.r + q * 5 * scale;
                    const fade = p < 0.6 ? 1 : 1 - (p - 0.6) / 0.4;
                    const op = (h.claro ? 0.92 : 0.84) * fade;

                    // sombreado cel: media luna inferior
                    ctx.beginPath();
                    ctx.arc(x, y + r * 0.22, r * 0.84, 0, Math.PI * 2);
                    ctx.fillStyle = h.claro
                        ? 'rgba(222,217,205,' + (op * 0.9).toFixed(3) + ')'
                        : 'rgba(201,195,180,' + (op * 0.9).toFixed(3) + ')';
                    ctx.fill();

                    // lóbulo principal blanco con contorno nítido
                    ctx.beginPath();
                    ctx.arc(x, y, r, 0, Math.PI * 2);
                    ctx.fillStyle = h.claro
                        ? 'rgba(252,250,246,' + op.toFixed(3) + ')'
                        : 'rgba(238,235,228,' + op.toFixed(3) + ')';
                    ctx.fill();
                    ctx.lineWidth = 1.5 * scale;
                    ctx.strokeStyle = 'rgba(118,110,94,' + (fade * 0.7).toFixed(3) + ')';
                    ctx.stroke();
                }

                // Polvillo fino flotando
                for (const gx of granos) {
                    gx.vx *= gx.d;
                    gx.vy *= gx.d;
                    gx.x += gx.vx * dt * 0.0015;
                    gx.y += gx.vy * dt * 0.0015;
                    ctx.beginPath();
                    ctx.arc(gx.x, gx.y, Math.max(0.4, gx.r * (1 - p * 0.55)), 0, Math.PI * 2);
                    ctx.fillStyle = 'rgba(196,190,176,' + (gx.a * (1 - p)) + ')';
                    ctx.fill();
                }

                for (const ch of chispas) {
                    ch.x += ch.vx * dt * 0.0015;
                    ch.y += ch.vy * dt * 0.0015;
                    ctx.beginPath();
                    ctx.arc(ch.x, ch.y, Math.max(0.5, ch.r * (1 - p)), 0, Math.PI * 2);
                    ctx.fillStyle = 'rgba(' + ch.relleno + ',' + (0.9 * (1 - p)) + ')';
                    ctx.fill();
                }

                if (p < 1) {
                    window._fxRaf = requestAnimationFrame(frame);
                } else {
                    ctx.clearRect(0, 0, window.innerWidth, window.innerHeight);
                    window._fxRaf = null;
                }
            }
            window._fxRaf = requestAnimationFrame(frame);
            // Red de seguridad: pase lo que pase, a los ~1,15 s el polvo se borra
            // del canvas (útil si Chromium congela los frames al quedar tapada).
            window._fxSafety = setTimeout(function () {
                if (window._fxRaf) {
                    cancelAnimationFrame(window._fxRaf);
                    window._fxRaf = null;
                }
                window.clearDust();
            }, dur + 200);
        };

        window._baseScale = 1;
        window.pichuAppear = function () {
            if (!window._model) { window.playDust(); return; }
            window.playDust();
            const base = window._baseScale;
            const t0 = performance.now();
            const c1 = 1.70158, c3 = c1 + 1;
            function easeOutBack(v) { return 1 + c3 * Math.pow(v - 1, 3) + c1 * Math.pow(v - 1, 2); }
            function paso() {
                const p = Math.min(1, (performance.now() - t0) / 420);
                const k = easeOutBack(p);
                const pop = 0.55 + 0.5 * k;
                window._model.scale.set(base * pop);
                if (p < 1) requestAnimationFrame(paso);
                else window._model.scale.set(base);
            }
            paso();
            setTimeout(function () { if (window._model) window._model.scale.set(base); }, 520);
        };

        window.pichuDissolve = function () {
            window.playDust({ scale: 1.4 });
            if (!window._model) return;
            const base = window._baseScale;
            const t0 = performance.now();
            function paso() {
                const p = Math.min(1, (performance.now() - t0) / 300);
                const s = base * Math.max(0.05, (1 - 0.55 * p * p));
                window._model.scale.set(s);
                if (p < 1) requestAnimationFrame(paso);
                else window._model.scale.set(base);
            }
            paso();
            setTimeout(function () { if (window._model) window._model.scale.set(base); }, 400);
        };
    </script>
</head>
<body>
    <canvas id="canvas"></canvas>
    <canvas id="fx"></canvas>

    <script>
        async function init() {
            try {
                const app = new PIXI.Application({
                    view: document.getElementById('canvas'),
                    backgroundAlpha: 0,
                    autoDensity: true,
                    resolution: window.devicePixelRatio || 1,
                    resizeTo: window
                });

                const model = await PIXI.live2d.Live2DModel.from('Pichu.model3.json', { autoInteract: false });
                window._model = model;
                app.stage.addChild(model);

                model.anchor.set(0.5, 0.5);
                model.position.set(window.innerWidth / 2, window.innerHeight / 2 + 15);

                const scale = Math.min(window.innerWidth / model.width, window.innerHeight / model.height) * 0.96;
                model.scale.set(scale);
                window._baseScale = scale;

                let mouthVal = 0;
                let mouthDir = 1;
                app.ticker.add((delta) => {
                    if (window._isSpeaking) {
                        mouthVal += 0.25 * mouthDir * delta;
                        if (mouthVal >= 1.0) { mouthVal = 1.0; mouthDir = -1; }
                        else if (mouthVal <= 0.0) { mouthVal = 0.0; mouthDir = 1; }
                        model.internalModel.coreModel.setParameterValueById('ParamMouthOpenY', mouthVal);
                    }
                });

                if (window._currentExp !== "idle") {
                    window.setExpression(window._currentExp);
                }

                window.pichuAppear();
            } catch (err) {
                console.error("Error Live2D:", err);
            }
        }

        window.onload = init;
    </script>
</body>
</html>"""

# Servidor HTTP local con soporte para subcarpetas de texturas
class RobustPichuHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urllib.parse.unquote(self.path.split('?')[0])

        if parsed_path in ("/", "/index.html"):
            content = HTML_CONTENT.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(content)
            return

        rel_path = parsed_path.lstrip("/").replace("/", os.sep)

        def localizar(raiz):
            file_path = os.path.join(raiz, rel_path)
            if os.path.exists(file_path):
                return file_path
            curr = raiz
            for part in rel_path.split(os.sep):
                matched = False
                if os.path.isdir(curr):
                    for item in os.listdir(curr):
                        if item.lower() == part.lower():
                            curr = os.path.join(curr, item)
                            matched = True
                            break
                if not matched:
                    return None
            return curr if os.path.exists(curr) else None

        file_path = localizar(PICHU_DIR) or localizar(UI_PACK_DIR)

        if not file_path or not os.path.exists(file_path):
            self.send_error(404, "Not Found")
            return

        mime = "application/octet-stream"
        if file_path.endswith(".png"): mime = "image/png"
        elif file_path.endswith(".json"): mime = "application/json"
        elif file_path.endswith(".moc3"): mime = "application/octet-stream"

        try:
            with open(file_path, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(data)
        except Exception as e:
            self.send_error(500, str(e))

    def log_message(self, format, *args):
        pass

def start_server():
    httpd = socketserver.TCPServer(("127.0.0.1", 0), RobustPichuHandler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return port

SERVER_PORT = start_server()

# Redirigir mensajes JS a la terminal para que nunca falle en silencio
class CustomWebPage(QWebEnginePage):
    def javaScriptConsoleMessage(self, level, message, lineNumber, sourceID):
        if "error" in message.lower() or "uncaught" in message.lower():
            print(f"[Live2D Error]: {message} (línea {lineNumber})")

# Burbuja "fantasma": mantiene los métodos para que nada se rompa, pero no muestra nada
class SilentSpeechBubble(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
    def set_texto(self, texto: str): pass
    def show(self): pass
    def hide(self): pass
    def move(self, *args): pass
    def width(self): return 0
    def height(self): return 0

class AvatarApp(QWidget):
    clicked = pyqtSignal()
    comando_texto = pyqtSignal(str)  # <--- AÑADE ESTA LÍNEA AQUÍ

    def __init__(self):
        super().__init__()
        self.tema_actual = "idle"
        self._hablando = False
        self._web_listo = False
        self.drag_start = None
        self.window_start = None
        self._model_box = None

        self.initUI()
        self.reset_timer = QTimer(self)
        self.reset_timer.setSingleShot(True)
        self.reset_timer.timeout.connect(lambda: self.set_expression("idle"))

        self.mouse_timer = QTimer(self)
        self.mouse_timer.timeout.connect(self._track_global_mouse)
        self.mouse_timer.start(16)

        # Icono en la bandeja del sistema
        self.crear_icono_bandeja()
        self.init_chat_ui()
        self._crear_hud()
        self._crear_vol_eco()

    def crear_icono_bandeja(self):
        self.tray = QSystemTrayIcon(self)
        self._actualizar_estado_bandeja()
        self.tray.setToolTip("Pichu JARVIS (Activo)")

        menu = QMenu()
        menu.setStyleSheet(_MENU_QSS)

        self._icono_volumen = _icono_badge(_SVG_MIC, "#2ECC71")
        self._icono_volumen_mut = _icono_badge(_SVG_MIC_OFF, "#E74C3C")

        a_mostrar = menu.addAction("Mostrar / Ocultar")
        a_mostrar.triggered.connect(lambda: self.set_visibility(not self.isVisible()))

        self.a_mute = menu.addAction(self._icono_volumen, "No escuchar")
        self.a_mute.setCheckable(True)
        self.a_mute.setChecked(mute.esta_activo())
        self.a_mute.toggled.connect(self._alternar_mute)

        menu.addSeparator()
        a_salir = menu.addAction("Salir")
        a_salir.triggered.connect(self.salir_con_efecto)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda motivo: self.set_visibility(not self.isVisible()) 
                                   if motivo in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) else None)
        self.tray.show()

    def _icono_pichu(self, silenciado: bool):
        """Icono de la bandeja: el muñeco de Pichu + un punto de estado en la esquina
        (verde = escuchando, rojo con aspa = silenciado)."""
        pix = QPixmap(64, 64)
        pix.fill(Qt.transparent)
        base_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pichu.ico")
        if os.path.exists(base_path):
            pintor = QPainter(pix)
            pintor.drawPixmap(0, 0, QIcon(base_path).pixmap(64, 64))
            color = QColor(231, 76, 60) if silenciado else QColor(46, 204, 113)
            pintor.setPen(QPen(QColor(255, 255, 255), 3))
            pintor.setBrush(QBrush(color))
            pintor.drawEllipse(46, 46, 16, 16)
            if silenciado:
                pintor.setPen(QPen(QColor(255, 255, 255), 2.5))
                pintor.drawLine(50, 50, 58, 58)
                pintor.drawLine(58, 50, 50, 58)
            pintor.end()
        else:
            pix = self.style().standardIcon(QStyle.SP_ComputerIcon).pixmap(64, 64)
        return QIcon(pix)

    def _actualizar_estado_bandeja(self):
        """Refresca el icono y el check del mute según el estado real."""
        sil = mute.esta_activo()
        self.tray.setIcon(self._icono_pichu(sil))
        if hasattr(self, "a_mute"):
            self.a_mute.setIcon(self._icono_volumen_mut if sil else self._icono_volumen)
            self._sincronizar_checks_mute(sil)

    def _sincronizar_checks_mute(self, activo: bool):
        for acc in (getattr(self, "a_mute", None), getattr(self, "a_mute_ctx", None)):
            if acc is not None and acc.isChecked() != activo:
                acc.blockSignals(True)
                acc.setChecked(activo)
                acc.blockSignals(False)

    def _alternar_mute(self, activo: bool):
        if activo:
            mute.activar()
            self.tray.setToolTip("Pichu JARVIS (Silenciado)")
            self.set_visibility(False)
            try:
                from brain import detener_hablar
                detener_hablar()
            except Exception:
                pass
        else:
            mute.desactivar()
            self.tray.setToolTip("Pichu JARVIS (Activo)")
        self.tray.setIcon(self._icono_pichu(activo))
        self._sincronizar_checks_mute(activo)

    def init_chat_ui(self):
        # Burbuja de respuesta de Pichu
        self.lbl_respuesta = QLabel(self)
        self.lbl_respuesta.setWordWrap(True)
        self.lbl_respuesta.setStyleSheet("""
            QLabel {
                background-color: #f6ead0;
                color: #3e2a17;
                border: 2px solid #8a5a2b;
                border-radius: 10px;
                padding: 6px 12px;
                font-size: 12px;
                font-family: 'Segoe UI', sans-serif;
            }
        """)
        self.lbl_respuesta.hide()
        self.layout().addWidget(self.lbl_respuesta)

        # Barra para escribir
        self.input_texto = QLineEdit(self)
        self.input_texto.setPlaceholderText("Escribe a Pichu... (Enter)")
        self.input_texto.setStyleSheet(f"""
            QLineEdit {{
                background-color: #b07a45;
                color: #2b1a0c;
                border: 2px solid #8a5a2b;
                border-image: {_IMG_BOTON} 64 64 64 64 stretch;
                border-radius: 14px;
                padding: 6px 12px;
                font-size: 12px;
                font-family: 'Segoe UI', sans-serif;
                font-weight: bold;
            }}
            QLineEdit:focus {{
                border-image: {_IMG_BOTON_FOCUS} 64 64 64 64 stretch;
                background-color: #8a5a2b;
            }}
        """)
        self.input_texto.hide()
        self.input_texto.returnPressed.connect(self._enviar_comando_texto)
        self.layout().addWidget(self.input_texto)

    def toggle_chat(self):
        """Muestra u oculta la caja de chat."""
        abrir = not self.input_texto.isVisible()
        self.input_texto.setVisible(abrir)
        if abrir:
            self.input_texto.setFocus()
        else:
            self.lbl_respuesta.hide()

    def _enviar_comando_texto(self):
        txt = self.input_texto.text().strip()
        if not txt:
            return
        self.input_texto.clear()
        self.input_texto.hide()  # <--- OCULTA LA BARRA AL PULSAR ENTER para no estorbar
        self.lbl_respuesta.setText("Pensando...")
        self.lbl_respuesta.show()
        self.comando_texto.emit(txt)

    def mostrar_respuesta_silenciosa(self, texto):
        self.lbl_respuesta.setText(texto)
        self.lbl_respuesta.show()
        # Se oculta la burbuja automáticamente a los 5 segundos
        QTimer.singleShot(5000, self.lbl_respuesta.hide)

    def _crear_hud(self):
        """HUD flotante para el panel de estado: siempre encima y justo encima
        de Pichu. Se muestra solo cuando se pide (no toca el modo limpio)."""
        self.hud = QLabel()
        self.hud.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.hud.setAttribute(Qt.WA_TranslucentBackground)
        self.hud.setWordWrap(True)
        self.hud.setMaximumWidth(360)
        self.hud.setStyleSheet("""
            QLabel {
                background-color: #f6ead0;
                color: #3e2a17;
                border: 2px solid #8a5a2b;
                border-radius: 12px;
                padding: 10px 14px;
                font-size: 13px;
                font-family: 'Segoe UI', sans-serif;
            }
        """)
        self.hud.hide()
        self.hud_timer = QTimer(self)
        self.hud_timer.setSingleShot(True)
        self.hud_timer.timeout.connect(self.hud.hide)

    def mostrar_panel(self, texto):
        self.hud.setText(texto)
        self.hud.adjustSize()
        pantalla = QApplication.primaryScreen().availableGeometry()
        x = self.x() + (self.width() - self.hud.width()) // 2
        y = self.y() - self.hud.height() - 10
        if y < pantalla.top():
            y = self.y() + self.height() + 10
        x = max(pantalla.left(), min(x, pantalla.right() - self.hud.width()))
        self.hud.move(x, y)
        self.hud.show()
        self.hud.raise_()
        self.hud_timer.start(8000)

    def _crear_vol_eco(self):
        """Eco flotante del volumen al usar la rueda del ratón sobre Pichu."""
        self.lbl_vol = QLabel()
        self.lbl_vol.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.lbl_vol.setAttribute(Qt.WA_TranslucentBackground)
        self.lbl_vol.setAlignment(Qt.AlignCenter)
        self.lbl_vol.setStyleSheet("""
            QLabel {
                background-color: #f6ead0;
                color: #2e7d32;
                border: 2px solid #8a5a2b;
                border-radius: 10px;
                padding: 4px 12px;
                font-size: 13px;
                font-weight: bold;
                font-family: 'Segoe UI', sans-serif;
            }
        """)
        self.lbl_vol.hide()
        self.vol_timer = QTimer(self)
        self.vol_timer.setSingleShot(True)
        self.vol_timer.timeout.connect(self.lbl_vol.hide)

    def _mostrar_vol_eco(self, texto: str):
        self.lbl_vol.setText(texto)
        self.lbl_vol.adjustSize()
        pantalla = QApplication.primaryScreen().availableGeometry()
        x = self.x() + (self.width() - self.lbl_vol.width()) // 2
        y = self.y() - self.lbl_vol.height() - 12
        if y < pantalla.top():
            y = self.y() + self.height() + 12
        x = max(pantalla.left(), min(x, pantalla.right() - self.lbl_vol.width()))
        self.lbl_vol.move(x, y)
        self.lbl_vol.show()
        self.lbl_vol.raise_()
        self.vol_timer.start(700)

    def _rueda_volumen(self, event):
        """La rueda del ratón sobre Pichu sube/baja el volumen DEL REPRODUCTOR
        de música (mpv si suena Pichu, si no el Zen). Nunca toca el volumen
        general del sistema."""
        try:
            import audio_control
            if not audio_control.DISPONIBLE:
                return False
            delta = event.angleDelta().y()
            if delta == 0:
                return True
            base = audio_control.volumen_proceso_leer("musica")
            if base is None:
                self._mostrar_vol_eco("No hay música sonando")
                return True
            paso = int((delta / 120.0) * 2)
            if paso == 0:
                paso = 2 if delta > 0 else -2
            nuevo = max(0, min(100, base + paso))
            res = audio_control.volumen_proceso("musica", nuevo)
            if res is not None:
                self._mostrar_vol_eco(f"Volumen {res}%")
        except Exception as e:
            print(f"[rueda volumen]: {e}")
        return True

    def wheelEvent(self, event):
        if self._rueda_volumen(event):
            event.accept()
        else:
            super().wheelEvent(event)
    def _pantalla_secundaria(self):
        """Pantalla 'secundaria': primera que no es la principal (o la principal
        si solo hay una)."""
        screens = QApplication.screens() or [QApplication.primaryScreen()]
        if len(screens) > 1:
            pri = QApplication.primaryScreen()
            for s in screens:
                if s is not pri:
                    return s
        return screens[0]

    def initUI(self):
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)

        ancho, alto = 300, 310
        pos = self.cargar_posicion()
        if pos:
            self.setGeometry(pos[0], pos[1], ancho, alto)
        else:
            g = self._pantalla_secundaria().geometry()
            self.setGeometry(g.left() - 30, g.bottom() - alto - 15, ancho, alto)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.web_view = QWebEngineView(self)
        self.web_page = CustomWebPage(self.web_view)
        self.web_view.setPage(self.web_page)

        self.web_page.setBackgroundColor(Qt.transparent)
        self.web_view.setAttribute(Qt.WA_TranslucentBackground)
        self.web_view.setStyleSheet("background: transparent;")
        self.web_view.settings().setAttribute(QWebEngineSettings.ShowScrollBars, False)
        self.web_view.setContextMenuPolicy(Qt.NoContextMenu)
        self.web_view.load(QUrl(f"http://127.0.0.1:{SERVER_PORT}/index.html"))
        self.web_view.loadFinished.connect(self._on_web_loaded)

        layout.addWidget(self.web_view)
        self.web_view.focusProxy().installEventFilter(self)

        # Burbuja silenciosa (no muestra texto en pantalla)
        self.burbuja = SilentSpeechBubble()

        # Forzar que la ventana siempre sea visible al crearse
        self.show()

    def _on_web_loaded(self, ok):
        self._web_listo = True
        if ok:
            self.run_js("if (window.pichuAppear) window.pichuAppear();")
            self.web_view.page().runJavaScript(
                "window._modelBox ? JSON.stringify(window._modelBox()) : 'null'",
                self._recibir_model_box)

    def _recibir_model_box(self, resultado):
        try:
            box = json.loads(resultado)
            if (isinstance(box, dict) and 0 <= box.get("x0", -1) < box.get("x1", 2) <= 1
                    and 0 <= box.get("y0", -1) < box.get("y1", 2) <= 1):
                self._model_box = box
                return
        except Exception:
            pass
        self._model_box = None

    def _click_sobre_modelo(self, pos_widget):
        """True si el punto (en coords del widget) cae sobre el cuerpo del modelo.
        Si la web aún no ha dado el bbox, se permite arrastrar desde cualquier lado."""
        box = self._model_box
        if not box:
            return True
        lx = pos_widget.x() / max(1, self.width())
        ly = pos_widget.y() / max(1, self.height())
        return box["x0"] <= lx <= box["x1"] and box["y0"] <= ly <= box["y1"]

    def run_js(self, codigo: str):
        if self._web_listo:
            self.web_view.page().runJavaScript(codigo)

    def _track_global_mouse(self):
        cursor_pos = QCursor.pos()
        center_x = self.x() + self.width() / 2
        center_y = self.y() + self.height() / 2

        dx = (cursor_pos.x() - center_x) / (QApplication.primaryScreen().geometry().width() / 2)
        dy = (cursor_pos.y() - center_y) / (QApplication.primaryScreen().geometry().height() / 2)

        dx = max(-1.0, min(1.0, dx))
        dy = max(-1.0, min(1.0, dy))

        self.run_js(
            f"if (typeof window.updateGlobalMouse === 'function') window.updateGlobalMouse({dx}, {dy});"
        )

    def eventFilter(self, source, event):
        if source == self.web_view.focusProxy():
            if event.type() == QEvent.Wheel:
                return self._rueda_volumen(event)
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                if self._click_sobre_modelo(event.pos()):
                    self.drag_start = event.globalPos()
                    self.window_start = self.pos()
            elif event.type() == QEvent.MouseMove and self.drag_start is not None:
                if event.buttons() & Qt.LeftButton:
                    delta = event.globalPos() - self.drag_start
                    self.move(self.window_start + delta)
                    return True
            elif event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                if self.drag_start is not None:
                    delta = event.globalPos() - self.drag_start
                    if delta.manhattanLength() < 5:
                        self.clicked.emit()
                    self.drag_start = None
                    self.guardar_posicion()
        return super().eventFilter(source, event)

    def reposicionar_burbuja(self):
        pass

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.setStyleSheet(_MENU_QSS)

        exp_menu = menu.addMenu("Expresion")
        exp_menu.setStyleSheet(_MENU_QSS)
        a_normal = exp_menu.addAction("Normal")
        a_happy = exp_menu.addAction("Feliz")
        a_angry = exp_menu.addAction("Enfadado")
        a_sad = exp_menu.addAction("Triste")
        a_shock = exp_menu.addAction("Sorpresa")
        a_dispair = exp_menu.addAction("Desesperacion")
        a_normal.setIcon(_icono_punto("#FFD000"))
        a_happy.setIcon(_icono_punto("#2ECC71"))
        a_angry.setIcon(_icono_punto("#E74C3C"))
        a_sad.setIcon(_icono_punto("#3498DB"))
        a_shock.setIcon(_icono_punto("#F39C12"))
        a_dispair.setIcon(_icono_punto("#9B59B6"))

        menu.addSeparator()
        self.a_mute_ctx = menu.addAction(
            self._icono_volumen_mut if mute.esta_activo() else self._icono_volumen,
            "No escuchar")
        self.a_mute_ctx.setCheckable(True)
        self.a_mute_ctx.setChecked(mute.esta_activo())
        self.a_mute_ctx.toggled.connect(self._alternar_mute)
        a_chat = menu.addAction("Escribir (Chat)")
        a_dormir = menu.addAction("Ocultar")
        a_salir = menu.addAction("Salir")

        accion = menu.exec_(event.globalPos())
        if accion == a_normal: self.set_expression("idle")
        elif accion == a_happy: self.set_expression("Happy")
        elif accion == a_angry: self.set_expression("Angry")
        elif accion == a_sad: self.set_expression("Sad")
        elif accion == a_shock: self.set_expression("Shock")
        elif accion == a_dispair: self.set_expression("Dispair")
        elif accion == a_chat: self.toggle_chat()      # <--- ACTIVA EL CHAT
        elif accion == a_dormir: self.set_visibility(False)
        elif accion == a_salir: self.salir_con_efecto()

    def set_expression(self, nombre: str, auto_reset_segundos: float = 0):
        mapping = {
            "idle": "idle", "normal": "idle", "hablando": "idle",
            "happy": "Happy", "feliz": "Happy",
            "angry": "Angry", "enojado": "Angry", "enfadado": "Angry",
            "sad": "Sad", "triste": "Sad",
            "shock": "Shock", "sorpresa": "Shock",
            "despair": "Dispair", "dispair": "Dispair", "desesperacion": "Dispair"
        }
        exp = mapping.get(str(nombre).lower(), nombre)
        self.run_js(f"window.setExpression('{exp}');")

        # Si le ponemos un tiempo, volverá solo a normal tras esos segundos
        self.reset_timer.stop()
        if auto_reset_segundos > 0 and exp != "idle":
            self.reset_timer.start(int(auto_reset_segundos * 1000))

    def set_theme(self, tema: str):
        # Si main.py le pasa palabras genéricas como "hablando" o "idle", no pone caras raras
        if str(tema).lower() in ["idle", "hablando", "default", "normal", "panel"]:
            self.set_expression("idle")
            return
        # Si es una emoción específica, dura 4 segundos y se calma solo
        self.set_expression(tema, auto_reset_segundos=4.0)

    def set_speaking(self, hablando: bool, texto: str = ""):
        self._hablando = hablando
        js_val = "true" if hablando else "false"
        self.run_js(f"window.setSpeaking({js_val});")

        if hablando:
            if texto:
                t = texto.lower()
                # Sentido del humor según lo que dice:
                if any(w in t for w in ["hola", "buen", "gracias", "genial", "bien", "perfecto", "excelente", "feliz", "jaja", "claro"]):
                    self.set_expression("Happy")
                elif any(w in t for w in ["error", "fallo", "lo siento", "perdón", "disculpa", "problema", "triste", "mal", "no pude"]):
                    self.set_expression("Sad")
                elif any(w in t for w in ["ojo", "cuidado", "alarma", "vaya", "alerta", "espera", "¿qué", "¿cómo"]):
                    self.set_expression("Shock")
                elif any(w in t for w in ["odio", "pesado", "cállate", "furioso", "enojado", "basta"]):
                    self.set_expression("Angry")
                else:
                    self.set_expression("idle")
        else:
            # Al terminar de hablar, siempre vuelve a su cara tranquila por defecto
            self.set_expression("idle")

    def guardar_posicion(self):
        try:
            with open(CONFIG_FILE, "w") as f:
                json.dump({"x": self.x(), "y": self.y()}, f)
        except Exception:
            pass

    def cargar_posicion(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    data = json.load(f)
                    x, y = data.get("x", -1), data.get("y", -1)
                    margen = 40
                    for s in QApplication.screens():
                        g = s.geometry()
                        if (g.left() - margen <= x <= g.right() + margen - self.width()
                                and g.top() - margen <= y <= g.bottom() + margen - self.height()):
                            return x, y
            except Exception:
                pass
        return None

    def set_visibility(self, visible: bool):
        if visible:
            # Solo explota y hace la animación de aparecer si NO estaba ya visible en pantalla
            if not self.isVisible():
                self.show()
                self.run_js("if (window.pichuAppear) window.pichuAppear();")
        else:
            # Solo hace la animación de despedida si estaba visible
            if self.isVisible():
                self.run_js("if (window.pichuDissolve) window.pichuDissolve();")
                QTimer.singleShot(300, lambda: (self.run_js("window.clearDust();"), self.hide()))

    def salir_con_efecto(self):
        self.run_js("if (window.pichuDissolve) window.pichuDissolve();")
        QTimer.singleShot(700, lambda: (self.run_js("window.clearDust();"), QApplication.instance().quit()))

if __name__ == "__main__":
    QApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv)
    avatar = AvatarApp()
    sys.exit(app.exec_())