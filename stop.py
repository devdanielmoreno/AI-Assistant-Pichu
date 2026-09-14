import threading

_evento = threading.Event()


def parar():
    """Frena la acción en curso (clic en la burbuja mientras Pichu hace algo)."""
    _evento.set()


def reset():
    """Se llama al ARRANCAR un comando nuevo: Pichu vuelve a poder actuar."""
    _evento.clear()


def toca_parar():
    return _evento.is_set()