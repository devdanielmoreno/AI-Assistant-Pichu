"""Silencio global del microfono de Pichu, compartido entre la bandeja (ui.py)
y el bucle de escucha (main.py). Con mute activo, Pichu deja de escuchar todo;
para reactivarlo hay que tocarlo desde la bandeja (o volver con la voz mientras
aun no este mudo)."""

import threading

_activo = threading.Event()


def activar():
    _activo.set()


def desactivar():
    _activo.clear()


def esta_activo():
    return _activo.is_set()


def alternar():
    if _activo.is_set():
        _activo.clear()
        return False
    _activo.set()
    return True