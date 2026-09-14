import time
import ctypes
from ctypes import wintypes
import pyautogui

try:
    import uiautomation as auto
    DISPONIBLE = True
except Exception:
    DISPONIBLE = False

_user32 = ctypes.windll.user32
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MOUSEWHEEL = 0x020A
_WHEEL_DELTA = 120

MAX_PROFUNDIDAD = 8
MAX_HIJOS = 80


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _ventana_bajo(x, y):
    return _user32.WindowFromPoint(_POINT(int(x), int(y)))


def clicar_coordenadas(x, y, boton="izq"):
    """Clic FANTASMA en (x, y): se envía el mensaje de clic a la ventana que
    está bajo ese punto SIN mover el cursor del usuario. False si no hay
    ventana (ahí el que llama decide si usar ratón de respaldo)."""
    hwnd = _ventana_bajo(x, y)
    if not hwnd:
        return False
    pt = _POINT(int(x), int(y))
    if not _user32.ScreenToClient(hwnd, ctypes.byref(pt)):
        return False
    lp = ((pt.y & 0xFFFF) << 16) | (pt.x & 0xFFFF)
    if boton == "der":
        _user32.SendMessageW(hwnd, WM_RBUTTONDOWN, 1, lp)
        _user32.SendMessageW(hwnd, WM_RBUTTONUP, 0, lp)
    else:
        _user32.SendMessageW(hwnd, WM_LBUTTONDOWN, 1, lp)
        _user32.SendMessageW(hwnd, WM_LBUTTONUP, 0, lp)
    time.sleep(0.05)
    return True


def clicar_coordenadas_o_raton(x, y):
    """Intentaba clic fantasma; si la ventana no estaba, real. Devuelve True."""
    if clicar_coordenadas(x, y):
        return True
    pyautogui.moveTo(int(x), int(y), duration=0.15, tween=pyautogui.easeOutQuad)
    time.sleep(0.06)
    pyautogui.click()
    return True


def doble_clic_en(x, y):
    """Doble clic fantasma en (x, y). True si se hizo sin ratón."""
    if clicar_coordenadas(x, y):
        time.sleep(0.03)
        clicar_coordenadas(x, y)
        return True
    return False


def scroll_en(x, y, pasos):
    """Rueda sobre el punto (x, y) SIN mover el cursor. pasos>0 arriba."""
    hwnd = _ventana_bajo(x, y)
    if not hwnd:
        return False
    pt = _POINT(int(x), int(y))
    _user32.ScreenToClient(hwnd, ctypes.byref(pt))
    wparam = ((int(pasos) * _WHEEL_DELTA) & 0xFFFF) << 16
    lparam = ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)
    _user32.SendMessageW(hwnd, WM_MOUSEWHEEL, wparam, lparam)
    return True


def listar_ventanas():
    if not DISPONIBLE:
        return []
    res = []
    try:
        root = auto.GetRootControl()
        for w in root.GetChildren():
            try:
                if not w.Name:
                    continue
                r = w.BoundingRectangle
                if r.right - r.left <= 0 or r.bottom - r.top <= 0:
                    continue
                res.append({
                    "titulo": w.Name,
                    "tipo": w.ControlTypeName,
                    "x": r.left,
                    "y": r.top,
                    "ancho": r.right - r.left,
                    "alto": r.bottom - r.top,
                })
            except Exception:
                continue
    except Exception as e:
        print(f"[UIA listar_ventanas]: {e}")
    return res


def _centro(ctrl):
    r = ctrl.BoundingRectangle
    return int(r.left + (r.right - r.left) / 2), int(r.top + (r.bottom - r.top) / 2)


def encontrar_puntos(texto, max_resultados=5):
    if not DISPONIBLE:
        return []
    objetivo = (texto or "").lower().strip()
    if not objetivo:
        return []
    coincidencias = []

    def recorrer(ctrl, profundidad=0):
        if len(coincidencias) >= max_resultados:
            return
        if profundidad > MAX_PROFUNDIDAD:
            return
        try:
            nombre = (ctrl.Name or "").lower()
            if objetivo in nombre:
                try:
                    x, y = _centro(ctrl)
                    coincidencias.append((x, y, ctrl.Name, ctrl.ControlTypeName))
                except Exception:
                    pass
            hijos = ctrl.GetChildren()
            for h in hijos[:MAX_HIJOS]:
                recorrer(h, profundidad + 1)
        except Exception:
            return

    try:
        fg = auto.GetForegroundControl()
        if fg:
            recorrer(fg)
        if not coincidencias:
            root = auto.GetRootControl()
            for w in root.GetChildren()[:40]:
                recorrer(w)
    except Exception as e:
        print(f"[UIA encontrar_puntos]: {e}")
    # Primero las coincidencias exactas y luego las más cortas (probablemente
    # botones pequeños en vez de elementos largos del menú lateral).
    coincidencias.sort(key=lambda c: (
        0 if (c[2] or "").lower().strip() == objetivo else 1,
        len(c[2] or ""),
    ))
    return coincidencias


def _controles_para(texto, max_resultados=5):
    if not DISPONIBLE:
        return []
    objetivo = (texto or "").lower().strip()
    if not objetivo:
        return []
    encontrados = []

    def recorrer(ctrl, profundidad=0):
        if len(encontrados) >= max_resultados:
            return
        if profundidad > MAX_PROFUNDIDAD:
            return
        try:
            nombre = (ctrl.Name or "").lower()
            if objetivo in nombre:
                try:
                    x, y = _centro(ctrl)
                    encontrados.append((ctrl, x, y, nombre, ctrl.ControlTypeName))
                except Exception:
                    pass
            for h in list(ctrl.GetChildren())[:MAX_HIJOS]:
                recorrer(h, profundidad + 1)
        except Exception:
            return

    try:
        fg = auto.GetForegroundControl()
        if fg:
            recorrer(fg)
        if not encontrados:
            for w in list(auto.GetRootControl().GetChildren())[:40]:
                recorrer(w)
    except Exception as e:
        print(f"[UIA _controles_para]: {e}")
    encontrados.sort(key=lambda c: (
        0 if c[3].strip() == objetivo else 1,
        len(c[3] or ""),
    ))
    return encontrados


def _activar_sin_movimiento(ctrl):
    """Intenta 'pulsar' el control SIN mover el ratón: patrones de UIA."""
    try:
        ctrl.GetInvokePattern().Invoke()
        return "Invoke"
    except Exception:
        pass
    try:
        ctrl.GetSelectionItemPattern().Select()
        return "Select"
    except Exception:
        pass
    try:
        ctrl.GetLegacyIAccessiblePattern().DoDefaultAction()
        return "DoDefaultAction"
    except Exception:
        pass
    return None


def clicar_elemento(texto):
    """Clic 'fantasma': primero patrones UIA (Invoke/Select/DoDefaultAction),
    luego clic por mensaje a la ventana bajo el punto, y solo si nada funciona
    se mueve el ratón de verdad. NUNCA roba foco de forma adicional."""
    controles = _controles_para(texto)
    if not controles:
        return None
    ctrl, x, y, nombre, tipo = controles[0]
    metodo = _activar_sin_movimiento(ctrl)
    if metodo:
        return f"Clic fantasma en '{nombre}' (tipo {tipo}) vía {metodo} en ({x},{y})."
    if clicar_coordenadas(x, y):
        return f"Clic fantasma por ventana en '{nombre}' (tipo {tipo}) en ({x},{y})."
    pyautogui.moveTo(x, y, duration=0.15, tween=pyautogui.easeOutQuad)
    time.sleep(0.06)
    pyautogui.click()
    return f"Clic (ratón, sin UIA) en '{nombre}' (tipo {tipo}) en ({x},{y})."


def leer_pantalla(limite=120):
    if not DISPONIBLE:
        return "UIA no disponible; no puedo enumerar elementos."
    lineas = []
    conteo = [0]

    def recorrer(ctrl, profundidad=0, prefijo=""):
        if conteo[0] >= limite or profundidad > MAX_PROFUNDIDAD:
            return
        try:
            nombre = ctrl.Name or ""
            if nombre.strip():
                x, y = _centro(ctrl)
                lineas.append(f"{ctrl.ControlTypeName} '{nombre[:60]}' ({x},{y})")
                conteo[0] += 1
            for h in ctrl.GetChildren()[:MAX_HIJOS]:
                recorrer(h, profundidad + 1, prefijo + "  ")
        except Exception:
            return

    try:
        fg = auto.GetForegroundControl()
        if fg:
            recorrer(fg)
    except Exception as e:
        return f"No pude leer la pantalla: {e}"
    if not lineas:
        return "No hay elementos accesibles visibles."
    return "\n".join(lineas)