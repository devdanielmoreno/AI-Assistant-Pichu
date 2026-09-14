"""Widget mínimo de música: aparece arriba a la izquierda solo mientras suena
algo (mpv). Muestra título, tiempo y botones (parar/pausa, siguiente, volumen,
cerrar). Estilo del Cozy UI Pack del usuario. Consumo mínimo: un QLabel +
botones, actualizado 2 veces/segundo, y se esconde solo cuando no hay música."""

import json
import os

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton, QApplication

import reproduccion
import audio_control

_POS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "musica_widget_pos.json")

_PACK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "UI_Pack").replace("\\", "/")
_IMG_CONT = f"url({_PACK}/Containers/Containers/WoodenContainer1.png)"
_IMG_BTN = f"url({_PACK}/Buttons/Round/RoundButton2_wood.png)"
_IMG_CERRAR = f"url({_PACK}/Buttons/Square/SquareButton2_red.png)"

_QSS = f"""
QWidget#musica {{
    border-image: {_IMG_CONT} 46 90 46 90 stretch stretch;
}}
QLabel {{ background: transparent; color: #3e2a17; }}
QLabel#lbl_titulo {{ font-size: 12px; font-weight: 700; }}
QLabel#lbl_info {{ color: #5b3f22; font-size: 10px; }}
QLabel#lbl_vol {{ color: #5b3f22; font-size: 10px; }}
QPushButton {{
    border-image: {_IMG_BTN} 18 28 18 28 stretch stretch;
    color: #3e2a17;
    border: none;
    font-size: 11px;
    font-weight: 700;
    min-height: 18px;
    padding: 2px 10px;
}}
QPushButton:hover {{ color: #fff3da; }}
QPushButton:pressed {{ padding-top: 4px; }}
QPushButton#btn_cerrar {{
    border-image: {_IMG_CERRAR} 18 28 18 28 stretch stretch;
    color: #ffffff;
    padding: 2px 8px;
}}
QPushButton#btn_cerrar:hover {{ color: #ffd9d9; }}
"""

P = ord("▶")
PUSA = "❚❚"


def _abrevia(texto, maxlen=34):
    texto = (texto or "").strip()
    if len(texto) <= maxlen:
        return texto
    return texto[: maxlen - 1] + "…"


class MusicaWidget(QWidget):
    def __init__(self):
        super().__init__(None)
        self._ultimo_titulo = ""
        self.setObjectName("musica")
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setStyleSheet(_QSS)
        self.setVisible(False)

        self.lbl_titulo = QLabel("")
        self.lbl_titulo.setObjectName("lbl_titulo")
        self.lbl_titulo.setMinimumWidth(150)
        self.lbl_titulo.setMaximumWidth(280)

        self.lbl_info = QLabel("0:00")
        self.lbl_info.setObjectName("lbl_info")

        self.btn_pausa = QPushButton(PUSA)
        self.btn_pausa.setToolTip("Pausar / reanudar")
        self.btn_pausa.clicked.connect(self._alternar_pausa)
        self.btn_sig = QPushButton(">>")
        self.btn_sig.setToolTip("Siguiente")
        self.btn_sig.clicked.connect(lambda: reproduccion.siguiente())
        self.btn_menos = QPushButton("Vol -")
        self.btn_menos.setToolTip("Bajar volumen")
        self.btn_menos.clicked.connect(lambda: self._ajustar_vol(-2))
        self.btn_mas = QPushButton("Vol +")
        self.btn_mas.setToolTip("Subir volumen")
        self.btn_mas.clicked.connect(lambda: self._ajustar_vol(2))
        self.btn_cerrar = QPushButton("x")
        self.btn_cerrar.setObjectName("btn_cerrar")
        self.btn_cerrar.setToolTip("Parar música")
        self.btn_cerrar.clicked.connect(lambda: reproduccion.detener())

        self.lbl_vol = QLabel("")
        self.lbl_vol.setObjectName("lbl_vol")

        fila_info = QHBoxLayout()
        fila_info.setContentsMargins(0, 0, 0, 0)
        fila_info.setSpacing(8)
        fila_info.addWidget(self.lbl_info)
        fila_info.addWidget(self.lbl_vol, 0, Qt.AlignRight)

        fila_botones = QHBoxLayout()
        fila_botones.setContentsMargins(0, 0, 0, 0)
        fila_botones.setSpacing(4)
        fila_botones.addWidget(self.btn_pausa)
        fila_botones.addWidget(self.btn_sig)
        fila_botones.addWidget(self.btn_menos)
        fila_botones.addWidget(self.btn_mas)
        fila_botones.addStretch(1)
        fila_botones.addWidget(self.btn_cerrar)

        col = QVBoxLayout(self)
        col.setContentsMargins(8, 6, 8, 6)
        col.setSpacing(3)
        col.addWidget(self.lbl_titulo)
        col.addLayout(fila_info)
        col.addLayout(fila_botones)

        self.adjustSize()

        self._t = QTimer(self)
        self._t.setInterval(500)
        self._t.timeout.connect(self._refrescar)
        self._t.start()

        self._arrastrando = False
        self._drag_start = None
        self._window_start = None
        self._movido_por_usuario = False
        self._cargar_posicion()

    # --- ARRASTRE CON RATÓN ---
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._arrastrando = True
            self._drag_start = event.globalPos()
            self._window_start = self.pos()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._arrastrando and self._drag_start is not None:
            self.move(self._window_start + (event.globalPos() - self._drag_start))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._arrastrando and event.button() == Qt.LeftButton:
            self._arrastrando = False
            self._drag_start = None
            self._movido_por_usuario = True
            self._guardar_posicion()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _guardar_posicion(self):
        try:
            with open(_POS_FILE, "w") as f:
                json.dump({"x": self.x(), "y": self.y()}, f)
        except Exception:
            pass

    def _cargar_posicion(self):
        try:
            if os.path.exists(_POS_FILE):
                with open(_POS_FILE, "r") as f:
                    data = json.load(f)
                x, y = data.get("x"), data.get("y")
                if x is None or y is None:
                    return
                margen = 40
                dentro = False
                for s in QApplication.screens():
                    g = s.geometry()
                    if (g.left() - margen <= x <= g.right() + margen - self.width()
                            and g.top() - margen <= y <= g.bottom() + margen - self.height()):
                        dentro = True
                        break
                if dentro:
                    self.move(x, y)
                    self._movido_por_usuario = True
        except Exception:
            pass

    def _secundaria(self):
        """Pantalla 'secundaria': primera que no es la principal (o la principal
        si solo hay una)."""
        screens = QApplication.screens() or [QApplication.primaryScreen()]
        if len(screens) > 1:
            pri = QApplication.primaryScreen()
            for s in screens:
                if s is not pri:
                    return s
        return screens[0]

    def _recolocar(self):
        """Ancla el widget arriba a la derecha de la pantalla secundaria. Si el
        usuario lo movió a mano (o hay posición guardada), no lo vuelve a tocar."""
        if self._movido_por_usuario:
            return
        scr = self._secundaria()
        if not scr:
            return
        g = scr.geometry()
        self.move(g.right() - self.width() - 14, g.top() + 14)

    def _alternar_pausa(self):
        est = reproduccion.estado()
        if est.get("pausado"):
            reproduccion.reanudar()
        else:
            reproduccion.pausar()

    def _ajustar_vol(self, delta):
        cur = audio_control.volumen_proceso_leer("musica")
        audio_control.volumen_proceso("musica", (cur or 0) + delta)

    def _refrescar(self):
        try:
            est = reproduccion.estado()
        except Exception:
            est = {}
        activo = bool(est.get("activo"))
        if activo:
            if not self.isVisible():
                self._recolocar()
                self.setVisible(True)
        else:
            self.setVisible(False)
        if not activo:
            return

        titulo = f"{est.get('titulo') or ''}{' - ' + est.get('artista') if est.get('artista') else ''}"
        if titulo != self._ultimo_titulo:
            self._ultimo_titulo = titulo
            self.lbl_titulo.setText(_abrevia(titulo))
            self.adjustSize()
            self._recolocar()

        prog = int(est.get("progreso") or 0)
        self.lbl_info.setText(f"{prog // 60}:{prog % 60:02d}")

        vol = audio_control.volumen_proceso_leer("musica")
        self.lbl_vol.setText(f"vol {vol}%" if vol is not None else "")