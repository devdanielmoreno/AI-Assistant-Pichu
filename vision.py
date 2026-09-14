import io
import re
import time
import ctypes
import unicodedata
from difflib import SequenceMatcher
import pyautogui
from PIL import ImageGrab
import hammer

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except:
    try: ctypes.windll.user32.SetProcessDPIAware()
    except: pass

pyautogui.FAILSAFE = True
pyautogui.PAUSE = 0.05

HAS_WINOCR = False
try:
    import winocr
    HAS_WINOCR = True
except ImportError:
    pass

def mover_y_clicar(target_x: int, target_y: int):
    """Movimiento directo y eficiente hasta el objetivo, sin rodeos por el centro."""
    pyautogui.moveTo(target_x, target_y, duration=0.18, tween=pyautogui.easeOutQuad)
    time.sleep(0.06)
    pyautogui.click()


def mover_y_clicar_ghost(target_x: int, target_y: int):
    """Clic FANTASMA: se envía el mensaje a la ventana bajo el punto sin mover el
    cursor del usuario. Si no hay ventana, cae al clic real."""
    if hammer.clicar_coordenadas(target_x, target_y):
        return
    mover_y_clicar(target_x, target_y)

def _centro_de_linea(linea):
    """Devuelve (x, y) del centro de una línea de OCR usando sus palabras."""
    pts = []
    for p in linea.get("words", []):
        bx = p.get("bounding_rect") or p.get("BoundingBox") or {}
        x, y = bx.get("x"), bx.get("y")
        if x is None or y is None:
            continue
        pts.append((int(x) + (int(bx.get("width") or 0) // 2),
                    int(y) + (int(bx.get("height") or 0) // 2)))
    if not pts:
        return None
    return int(sum(p[0] for p in pts) / len(pts)), int(sum(p[1] for p in pts) / len(pts))


def _norm(texto: str) -> str:
    n = unicodedata.normalize("NFD", (texto or "").lower())
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", n)).strip()


def _capturar(region=None):
    """Captura la pantalla primaria o una región (x0,y0,x1,y1) absoluta."""
    ancho, alto = pyautogui.size()
    if region is None:
        x0, y0, x1, y1 = 0, 0, ancho, alto
    else:
        x0, y0, x1, y1 = (int(v) for v in region)
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(ancho, x1), min(alto, y1)
    if x1 <= x0 or y1 <= y0:
        return None, 0, 0
    return ImageGrab.grab(bbox=(x0, y0, x1, y1)), x0, y0


def ocr_lineas(region=None):
    """Devuelve [(x, y, texto_normalizado, texto_original)] del OCR de la pantalla o región."""
    if not HAS_WINOCR:
        return []
    img, x0, y0 = _capturar(region)
    if img is None:
        return []
    try:
        res = winocr.recognize_pil_sync(img, 'es')
        out = []
        for l in res.get('lines', []):
            txt = (l.get('text') or '').strip()
            if not txt:
                continue
            c = _centro_de_linea(l)
            if c:
                out.append((x0 + c[0], y0 + c[1], _norm(txt), txt))
        return out
    except Exception as e:
        print(f"[Aviso OCR]: {e}")
        return []


def buscar_por_ocr(objetivo: str, region=None):
    """Escanea el texto de tu pantalla (o región) y devuelve el mejor match como (x, y, score, texto)."""
    if not HAS_WINOCR:
        return None

    try:
        img, x0, y0 = _capturar(region)
        if img is None:
            return None
        res = winocr.recognize_pil_sync(img, 'es')
        objetivo_norm = _norm(objetivo)
        if not objetivo_norm:
            return None
        palabras_obj = set(objetivo_norm.split())

        candidatos = []
        for linea in res.get('lines', []):
            linea_txt = _norm(linea.get('text', ''))
            if not linea_txt:
                continue
            ratio = SequenceMatcher(None, objetivo_norm, linea_txt).ratio()
            coinciden = len(palabras_obj & set(linea_txt.split()))
            score = max(ratio, coinciden / max(len(palabras_obj), 1) * 0.85)
            centro = _centro_de_linea(linea)
            if centro and score >= 0.42:
                candidatos.append((score, x0 + centro[0], y0 + centro[1], linea_txt))

        if not candidatos:
            return None
        candidatos.sort(reverse=True, key=lambda c: c[0])
        return candidatos[0][1], candidatos[0][2], candidatos[0][0], candidatos[0][3]
    except Exception as e:
        print(f"[Aviso OCR]: {e}")
    return None

_ORDINALES = {
    "primer": 0, "primero": 0, "primera": 0, "1": 0,
    "segundo": 1, "segunda": 1, "2": 1,
    "tercer": 2, "tercero": 2, "tercera": 2, "3": 2,
    "cuarto": 3, "cuarta": 3, "4": 3,
    "quinto": 4, "quinta": 4, "5": 4,
    "sexto": 5, "sexta": 5, "6": 5,
}

_EXCLUIR_UI = {
    "canal", "directo", "videos", "juegos", "lista de", "tendencias",
    "suscripciones", "compartir", "me gusta", "guardar", "mostrar mas",
    "aceptar todo", "rechazar todo", "idioma", "privacidad", "anuncio",
    "adicional", "seguir viendo", "historial", "patrocinado", "suscribirte",
    "todos los episodios", "ver todos", "continuar viendo",
}

def _lineas_videos(region=None):
    """Líneas OCR que parecen títulos de vídeo (texto largo, fuera de la UI)."""
    ancho, alto = pyautogui.size()
    out = []
    for x, y, norm, raw in ocr_lineas(region):
        if len(norm) < 12:
            continue
        if "http" in norm or "https" in norm or "www." in norm:
            continue
        # Excluir barra lateral de YouTube y cabecera del navegador
        if x < ancho * 0.08 or y < alto * 0.06:
            continue
        if any(t in norm for t in _EXCLUIR_UI):
            continue
        if len(norm.split()) < 2:
            continue
        out.append((y, x, norm))
    out.sort(key=lambda c: (c[0], c[1]))
    uniq = []
    for y, x, n in out:
        if uniq and abs(y - uniq[-1][0]) < 20 and abs(x - uniq[-1][1]) < 60:
            continue
        uniq.append((y, x, n))
    return uniq


def _es_meta_linea(norm):
    """Línea de metadatos de un vídeo de YouTube: '123K visualizaciones', 'hace 2 días'..."""
    if any(p in norm for p in ("visualizacion", "reproducciones", "emitio", "estreno", "visto", "vistas")):
        return True
    if "hace" in norm and any(u in norm for u in (
            "segundo", "minuto", "hora", "dia", "día", "mes", "meses", "ano", "año", "seman", "visto")):
        return True
    return False


def _tarjetas_videos(region=None):
    """Tarjetas de vídeo REALES: título con metadatos (vistas / hace X) justo debajo."""
    lineas = ocr_lineas(region)
    metas = [(x, y) for x, y, n, r in lineas if _es_meta_linea(n)]
    cartas = []
    for x, y, n, r in lineas:
        if len(n) < 20 or _es_meta_linea(n):
            continue
        if "http" in n or "https" in n or "www." in n:
            continue
        if any(t in n for t in _EXCLUIR_UI):
            continue
        cerca = [my for mx, my in metas if (my - y) >= 0 and (my - y) <= 130 and abs(mx - x) <= 700]
        if not cerca:
            continue
        cartas.append((y, x, n))
    cartas.sort(key=lambda c: (c[0], c[1]))
    uniq = []
    for y, x, n in cartas:
        if uniq and abs(y - uniq[-1][0]) < 20 and abs(x - uniq[-1][1]) < 60:
            continue
        uniq.append((y, x, n))
    return uniq

def clicar_en_pantalla(objetivo: str, reintentos: int = 1) -> str:
    obj = objetivo.lower().strip()
    ancho, alto = pyautogui.size()

    print(f"[Visión Omnipresente]: Escaneando la pantalla en busca de '{objetivo}'...")

    # Cuando se pide un vídeo u una posición ordinal, el UIA falla a veces
    # (encuentra "Vídeos (anclado)" en la barra lateral en vez de un vídeo),
    # así que saltamos accesibilidad y vamos directos a OCR/grid.
    es_posicion = any(w in obj for w in [
        "video", "vídeo", "capitulo", "capítulo", "primer", "primero", "primera",
        "segundo", "segunda", "tercer", "tercero", "tercera",
        "cuarto", "cuarta", "quinto", "quinta", "sexto", "sexta", "ultimo", "último",
        "1", "2", "3", "4", "5", "6",
    ])

    # 0. Accesibilidad (UIAutomation): el método más fiable, sin OCR
    if not es_posicion:
        coord_uia = hammer.encontrar_puntos(obj)
        if coord_uia:
            x, y = coord_uia[0][0], coord_uia[0][1]
            mover_y_clicar(x, y)
            return f"Clic en el elemento '{coord_uia[0][2]}'."

    # 1. OCR Global: Lee texto en iconos de Windows, botones o webs
    # (para posiciones ordinales lo saltamos: 'videos' de cualquier menú/página
    #  falsaría el match y blocaría un clic donde no toca)
    ocr = None
    if not es_posicion:
        ocr = buscar_por_ocr(obj)
    if ocr:
        x, y, score, texto_hallado = ocr
        print(f"    OCR match '{texto_hallado}' (score {score:.2f}) -> ({x},{y})")
        # Si es YouTube, el texto está abajo del vídeo, ajustamos el clic hacia arriba a la miniatura
        ajuste_y = y - 80 if ("video" in obj or "vídeo" in obj) else y
        mover_y_clicar(x, ajuste_y)
        return f"Clicando en: {objetivo}."

    # 2. Posiciones ordinales: detectar los títulos de vídeo REALES de la
    #    pantalla y clicar el que toca (en vez de inventar una cuadrícula).
    indice = None
    if "ultimo" in obj or "último" in obj:
        indice = "ultimo"
    else:
        for k, v in _ORDINALES.items():
            if k in obj:
                indice = v
                break
    if indice is not None:
        dados = _tarjetas_videos() or _lineas_videos()
        if not dados:
            return f"No veo ningún vídeo claro en la pantalla para '{objetivo}'."
        if indice == "ultimo":
            candidato = dados[-1]
        else:
            candidato = dados[min(indice, len(dados) - 1)]
        y, x, titulo = candidato
        # Clic sobre el título (el texto es enlace y navega de forma fiable en
        # YouTube; clavar por encima en la miniatura falla a veces en resultados).
        mover_y_clicar(x, y + 8)
        return f"Clic en el vídeo '{titulo}'."

    # 3. Acciones genéricas en el SO
    if "cerrar" in obj or "cruz" in obj or obj in ("x", "equis"):
        # Esquina superior derecha de Windows
        mover_y_clicar(ancho - 20, 20)
        return "Cerrando ventana."

    if "inicio" in obj or "windows" in obj:
        # Botón de inicio de Windows
        pyautogui.press('win')
        return "Abriendo menú inicio."

    return f"No encontré '{objetivo}' en la pantalla."

def analizar_pantalla(pregunta: str = "¿Qué ves en mi pantalla?") -> str:
    try:
        ventanas = hammer.listar_ventanas()
        elementos = hammer.leer_pantalla()
        bloque = []
        if ventanas:
            bloque.append(f"Ventanas abiertas: {len(ventanas)}.")
            for v in ventanas[:6]:
                bloque.append(f" - {v['titulo']} ({v['tipo']})")
        if elementos:
            bloque.append("Elementos interactivos:")
            bloque.append(elementos[:700])
        if not bloque:
            return "No veo nada accesible en la pantalla."
        return "\n".join(bloque)
    except Exception as e:
        return f"No pude analizar la pantalla: {e}"