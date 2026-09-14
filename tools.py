import json
import os
import re
import time
import urllib.request
import ctypes
import unicodedata
import threading
from difflib import SequenceMatcher
from urllib.parse import quote_plus

import win32gui
import win32con

from actions import (
    abrir_url,
    buscar_duckduckgo,
    enfocar_o_lanzar_app,
    buscar_archivos_locales,
    abrir_carpeta_o_disco,
    ejecutar_comando_sistema,
    controlar_volumen,
    hacer_scroll,
    escribir_texto_en_pantalla,
    control_navegador,
    multimedia_play_pause,
    controlar_musica,
    abrir_proyecto_android,
    _poner_stremio_fullscreen,
)
import vision
import stop
import hammer
import memory
import subprocess
import pyautogui as _pg


def _abrir_url(args):
    abrir_url(args.get("url", ""))
    return "URL abierta en el navegador."


def _lanzar_app(args):
    return enfocar_o_lanzar_app(args.get("app", ""))


def _buscar_en_internet(args):
    return buscar_duckduckgo(args.get("consulta", ""))


def _buscar_archivo_local(args):
    return buscar_archivos_locales(args.get("nombre", ""), bool(args.get("abrir", True)))


def _abrir_carpeta(args):
    return abrir_carpeta_o_disco(args.get("destino", ""))


def _comando_powershell(args):
    return ejecutar_comando_sistema(args.get("comando", ""))


def _controlar_volumen(args):
    accion = (args.get("accion") or "").lower()
    valor = args.get("valor")
    if accion in ("fijar", "poner", "al", "establecer") or "fijar" in accion:
        return controlar_volumen("fijar", int(valor or 50))
    if accion in ("subir", "sube", "mÃ¡s", "mas", "mÃ¡s volumen"):
        return controlar_volumen("subir", int(valor or 10))
    if accion in ("bajar", "baja", "menos", "menos volumen"):
        return controlar_volumen("bajar", int(valor or 10))
    return controlar_volumen("mutear")


def _controlar_media(args):
    accion = (args.get("accion") or "play_pausa").lower()
    if accion in ("siguiente", "siguiente canciÃ³n", "siguiente cancion", "next",
                  "adelante", "salta", "skip"):
        return controlar_musica("siguiente")
    if accion in ("anterior", "atras", "atrÃ¡s", "prev", "volver", "retrocede"):
        return controlar_musica("anterior")
    if accion in ("pausa", "pausar", "parar", "para", "quita", "saca"):
        return controlar_musica("pausa")
    if accion in ("reanudar", "play", "pon", "seguir", "sigue", "continua", "continuar"):
        return controlar_musica("reanudar")
    return multimedia_play_pause()


def _scroll(args):
    return hacer_scroll(args.get("direccion", "abajo"), int(args.get("cantidad", 500)))


def _escribir_texto(args):
    return escribir_texto_en_pantalla(args.get("texto", ""), bool(args.get("enter", True)))


def _navegador(args):
    return control_navegador(args.get("accion", "nueva pestaÃ±a"))


def _proyecto_android(args):
    res = abrir_proyecto_android(args.get("proyecto", ""))
    return res or "No encontrÃ© ese proyecto de Android Studio."


def _clic_pantalla(args):
    return vision.clicar_en_pantalla(args.get("elemento", ""))


def _clic_ui(args):
    elemento = args.get("elemento", "")
    obj = (elemento or "").lower()
    # Si piden un ordinal ('tercer video', 'primera pestaÃ±a'...) el nombre
    # accesible no sirve: delegamos en el clic por visiÃ³n con detecciÃ³n ordinal.
    es_posicion = any(w in obj for w in [
        "video", "vÃ­deo", "capitulo", "capÃ­tulo", "primer", "primero", "primera",
        "segundo", "segunda", "tercer", "tercero", "tercera",
        "cuarto", "cuarta", "quinto", "quinta", "sexto", "sexta", "ultimo", "Ãºltimo",
        "1", "2", "3", "4", "5", "6",
    ])
    if es_posicion:
        return vision.clicar_en_pantalla(elemento)
    res = hammer.clicar_elemento(elemento)
    return res or f"No encontrÃ© el elemento '{elemento}' en la interfaz."


def _listar_ventanas(args):
    v = hammer.listar_ventanas()
    if not v:
        return "No hay ventanas con tÃ­tulo accesible."
    return json.dumps(v[:12], ensure_ascii=False)


def _leer_pantalla(args):
    return hammer.leer_pantalla()


def _guardar_recuerdo(args):
    return memory.guardar_recuerdo(args.get("texto", ""))


def _analizar_pantalla(args):
    return vision.analizar_pantalla(args.get("pregunta", "Â¿QuÃ© ves en mi pantalla?"))


def _dormir(args):
    return "Entendido, quedo a la espera en silencio."


# --- PROGRESO DE SERIES ---
def _norm_serie(nombre):
    n = unicodedata.normalize("NFD", (nombre or "").lower())
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", n)).strip()


_CACHE_OBRAS = {}


def _reconocer_obra_con_id(nombre):
    """Consulta el catÃ¡logo de IMDb (endpoint de sugerencias ACMOC, sin clave) y
    devuelve (titulo_canonico, tipo, id_imdb) o (None, None, None) si no hay red
    o no coincide. El id tipo 'tt1234567' sirve para enlaces stremio:///detail.
    Ej: 'grand blue dreaming' -> ('Grand Blue Dreaming', 'series', 'tt...')."""
    nombre = (nombre or "").strip()
    if not nombre:
        return None, None, None
    clave = nombre.lower()
    if clave in _CACHE_OBRAS:
        return _CACHE_OBRAS[clave]
    try:
        enc = quote_plus(nombre)
        letra = enc[0].lower() if enc else "x"
        url = f"https://v3.sg.media-imdb.com/suggestion/{letra}/{enc}.json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode("utf-8"))
        cand = data.get("d") or []
        if not cand:
            _CACHE_OBRAS[clave] = (None, None, None)
            return None, None, None
        mejor, mejor_score = None, 0.0
        objetivo = _norm_serie(nombre)
        for c in cand:
            tt = (c.get("l") or "").strip()
            if not tt:
                continue
            sc = SequenceMatcher(None, objetivo, _norm_serie(tt)).ratio()
            q = ((c.get("q") or "") + " " + (c.get("qid") or "")).lower()
            if "series" in q or "tv" in q:
                sc += 0.15
            if sc > mejor_score:
                mejor_score, mejor = sc, c
        if not mejor or mejor_score < 0.35:
            _CACHE_OBRAS[clave] = (None, None, None)
            return None, None, None
        qop = ((mejor.get("q") or "") + " " + (mejor.get("qid") or "")).lower()
        tipo = "series" if ("series" in qop or "tv" in qop) else "peli"
        titulo = (mejor.get("l") or nombre).strip()
        tt_id = (mejor.get("id") or "").strip() or None
        salida = (titulo, tipo, tt_id)
        _CACHE_OBRAS[clave] = salida
        return salida
    except Exception:
        _CACHE_OBRAS[clave] = (None, None, None)
        return None, None, None


def _reconocer_obra(nombre):
    """Compat: devuelve (titulo_canonico, tipo)."""
    t, tipo, _ = _reconocer_obra_con_id(nombre)
    return t, tipo


def _serie_canonica(nombre):
    """Une variantes ('spark', 'sparcos'...) a una serie ya conocida."""
    norm = _norm_serie(nombre)
    if not norm:
        return norm
    mejor, mejor_score = norm, 0.0
    for s in memory.listar_series():
        sn = s["serie"]
        score = SequenceMatcher(None, norm, sn).ratio()
        if len(norm) >= 4 and (norm in sn or sn.startswith(norm)):
            score = max(score, 0.95)
        if score > mejor_score:
            mejor, mejor_score = sn, score
    return mejor if mejor_score >= 0.75 else norm


def _extraer_progreso(titulo):
    """Intenta sacar (temporada, episodio) de un tÃ­tulo de YouTube (e.g. 'S1E5' o 'episodio 5')."""
    t = _norm_serie(titulo)
    m = re.search(
        r"(?:^|\s)[st]0*(\d+)\s*(?:[-x]|de)?\s*(?:temporada\s*)?"
        r"(?:episodios?|cap[i]tulos?|cap[i]|ep?s?)\s*0*(\d+)", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(
        r"(?:^|\s)temporada\s*0*(\d+)\s*(?:[-:]|de)?\s*"
        r"(?:episodios?|cap[i]tulos?|cap[i])\s*0*(\d+)", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(?:^|\s)(\d{1,2})\s*[-x]\s*(\d{1,3})", t)
    if m:
        return int(m.group(1)), int(m.group(2))
    m = re.search(r"(?:^|\s)(?:episodios?|cap[i]tulos?|cap[i])\s*0*(\d+)", t)
    if m:
        return None, int(m.group(1))
    return None, None


def _guardar_episodio(args):
    serie = _serie_canonica(args.get("serie", ""))
    temporada = args.get("temporada") or 1
    episodio = args.get("episodio")
    if not serie or episodio is None:
        return "Faltan serie o episodio."
    memory.registrar_progreso_serie(serie, int(temporada), int(episodio))
    return f"Anotado: {serie} temporada {temporada}, visto hasta el episodio {episodio}."


def _escribir_busqueda_campo(busqueda, region, bitacora=None):
    """Verifica el campo de bÃºsqueda de Stremio tras abrir stremio:///search.
    Algunas versiones conservan el Ãºltimo texto (p.ej. 'Mushoku Tensei' de una
    bÃºsqueda anterior que quedÃ³ en la home); si el campo NO muestra la serie
    pedida, lo clica y lo reescribe por teclado. Es un 'no-op' si ya coincide."""
    x0, y0, x1, y1 = region
    objetivo = _norm_serie(busqueda)
    franja = (x0 + int((x1 - x0) * 0.15), y0 + 70, x1 - int((x1 - x0) * 0.20), y0 + 160)
    lineas = vision.ocr_lineas(franja)
    for x, y, n, r in lineas:
        nl = _norm_serie(n)
        if not nl or len(nl) > len(objetivo) + 25:
            continue
        if nl == objetivo or objetivo.startswith(nl) or nl.startswith(objetivo):
            return True  # el campo ya muestra lo pedido
        # Texto distinto -> el campo guarda una bÃºsqueda vieja; seleccionar y reescribir
        vision.mover_y_clicar_ghost(x, y)
        time.sleep(0.5)
        _pg.hotkey("ctrl", "a")
        _pg.press("delete")
        time.sleep(0.3)
        _pg.typewrite(busqueda, interval=0.06)
        time.sleep(0.4)
        _pg.press("enter")
        if bitacora is not None:
            bitacora.append(f"el campo mostraba '{n}'; reescrito '{busqueda}'")
        return True
    if bitacora is not None and lineas:
        bitacora.append(f"(campo sin coincidencia legible: {[l[2] for l in lineas[:6]]})")
    return True


def _abrir_serie_por_busqueda(serie, region, bitacora=None):
    """Abre la serie en Stremio por enlace directo stremio:///search y clicando
    la tarjeta correcta de 'Popular - Series'. Devuelve (ok, hallado)."""
    x0, y0, x1, y1 = region

    # Confirmamos con el catÃ¡logo de IMDb el tÃ­tulo canÃ³nico (funciona con la
    # forma como diga el usuario) y lo usamos para la bÃºsqueda y las
    # comparaciones OCR, en vez de fiarnos solo de lo que se lea en pantalla.
    titulo_obra, tipo_obra = _reconocer_obra(serie)
    busqueda = titulo_obra or serie
    if bitacora is not None:
        if titulo_obra and _norm_serie(titulo_obra) != _norm_serie(serie):
            bitacora.append(f"vÃ­a IMDb: '{titulo_obra}'")
        if tipo_obra == "peli":
            bitacora.append("(IMDb lo cataloga como pelÃ­cula)")

    est = f"stremio:///search?search={quote_plus(busqueda)}"
    try:
        ctypes.windll.shell32.ShellExecuteW(None, "open", est, None, None, 1)
    except Exception:
        os.startfile(est)
    time.sleep(3.5)
    _enfocar_stremio()
    time.sleep(0.8)
    if bitacora is not None:
        bitacora.append("bÃºsqueda con enlace directo")

    # El campo puede conservar el Ãºltimo texto de la home ('Continue watching',
    # p.ej. 'Mushoku Tensei'): verificar y reescribir si hace falta.
    _escribir_busqueda_campo(busqueda, region, bitacora)
    time.sleep(1.0)

    ok, hallado = False, None
    bandas = None
    for x, y, n, r in vision.ocr_lineas((x0, y0 + 100, x1, y0 + 720)):
        if "series" in n and "movie" not in n and len(n.split()) <= 3:
            bandas = y
            break

    if bandas is not None:
        ok, hallado = _clic_serie_en_resultados(busqueda, region, bandas)
        if ok and bitacora is not None:
            bitacora.append(f'abrÃ­ 1Âª serie de la fila ("{hallado}")')

    if not ok:
        ok, hallado = _clic_serie(busqueda, (x0, y0 + 140, x1, y1), banda_y=bandas)
    if not ok:
        _pg.moveTo(int((x0 + x1) / 2), int((y0 + y1) / 2))
        _pg.scroll(-900)
        time.sleep(2.0)
        if stop.toca_parar():
            return False, hallado
        ok, hallado = _clic_serie(busqueda, (x0, y0 + 140, x1, y1), banda_y=bandas)
    if ok and bitacora is not None:
        bitacora.append(f'abrÃ­ "{hallado}"')
    if not ok:
        return False, hallado
    time.sleep(3.2)

    if not _pagina_contiene_serie(busqueda, region):
        return False, hallado
    if _clasificar_item(region) == 'peli':
        return False, hallado
    return True, hallado


def _abrir_episodio_directo(tt_id, temporada, episodio, region):
    """Abre el episodio directamente en Stremio con
    stremio:///detail/series/{tt}/{tt}:{temporada}:{episodio} (videoId compuesto)
    y enfoca la app.
    La comprobaciÃ³n de si saliÃ³ el overlay de fuentes la hace el llamador."""
    est = f"stremio:///detail/series/{tt_id}/{tt_id}:{temporada}:{episodio}"
    try:
        ctypes.windll.shell32.ShellExecuteW(None, "open", est, None, None, 1)
    except Exception:
        os.startfile(est)
    time.sleep(3.2)
    if region:
        _enfocar_stremio()
        time.sleep(0.8)
    return True


def _abrir_serie_en_stremio(tt_id, region):
    """Abre la PÃGINA de la serie (lista de episodios) en Stremio SIN reproducir
    nada: stremio:///detail/series/{tt} (sin videoId -> lista de episodios).
    El episodio siguiente lo decide la lista y su marca WATCHED, no un nÃºmero."""
    est = f"stremio:///detail/series/{tt_id}"
    try:
        ctypes.windll.shell32.ShellExecuteW(None, "open", est, None, None, 1)
    except Exception:
        os.startfile(est)
    time.sleep(3.0)
    if region:
        _enfocar_stremio()
        time.sleep(0.8)
    return True


def _elegir_y_reproducir_fuente(region, serie, temporada, episodio, bitacora, max_intentos=6):
    """En el selector de streams (overlay), clica fuentes Torrentio (fantasma
    primero, ratÃ³n solo si el reproductor no arranca) hasta que JUGANDO.
    Si logra reproducir, registra progreso y lanza el fullscreen."""
    probadas = set()
    for _ in range(max_intentos):
        ok_src, fuente, y_src = _clic_primera_fuente(region, probadas)
        if not ok_src:
            break
        bitacora.append(f"fuente {fuente}")
        time.sleep(4.2)
        if _jugando(region):
            memory.registrar_progreso_serie(serie, temporada, episodio)
            threading.Thread(target=_poner_stremio_fullscreen, daemon=True).start()
            return True
        probadas.add(y_src)
    return False


def _intentar_episodio1_directo(region, serie, tt_id, temporada, bitacora):
    """Serie sin empezar: lanza el capÃ­tulo 1 por enlace directo
    ({tt}:{temporada}:1), sin depender del grid (que a veces tapa la fila 1).
    Devuelve la respuesta final si lo logra; None si hay que seguir el flujo."""
    if not tt_id:
        bitacora.append("; no tengo el tt para lanzar el E1 directo")
        return None
    bitacora.append(f"serie nueva: lanzo el primer episodio (tt={tt_id} T{temporada}E1)")
    _abrir_episodio_directo(tt_id, temporada, 1, region)
    time.sleep(2.6)
    if _reproducir_stream(region, serie, temporada, 1, bitacora):
        return " ".join(bitacora) + f". Te pongo el primer episodio de {serie} (T{temporada}E1)."
    if stop.toca_parar():
        return "Vale, parado."
    bitacora.append("; el E1 directo no reprodujo, sigo con el grid")
    return None


def _ver_siguiente_episodio(args):
    serie = _serie_canonica(args.get("serie", ""))
    if not serie:
        return "Â¿De quÃ© serie?"
    prog = memory.recuperar_progreso_serie(serie)
    bitacora = []

    # 1. Stremio abierto y enfocado
    ventana = _ventana_stremio()
    if not ventana:
        bitacora.append(str(enfocar_o_lanzar_app("stremio")))
        time.sleep(2.5)
        ventana = _ventana_stremio()
        if not ventana:
            return "No encuentro Stremio. " + " ".join(bitacora)
    _enfocar_stremio()
    time.sleep(1.2)
    ventana = _ventana_stremio() or ventana
    region = (ventana["x"], ventana["y"], ventana["x"] + ventana["ancho"], ventana["y"] + ventana["alto"])
    x0, y0, x1, y1 = region
    bitacora.append("Stremio en primer plano." if _enfocar_stremio() else "Stremio visible pero sin foco.")

    # 1b. ATAJO DIRECTO a la PÃGINA de la serie (stremio:///detail/series/{tt}/{tt},
    # SIN reproducir nada): lista de episodios al instante, sin bÃºsqueda OCR.
    # El episodio siguiente NO sale de la memoria: salta la marca WATCHED (o el
    # primero si nada estÃ¡ visto aÃºn). Si no abre la lista, caemos al flujo OCR.
    _, tipo_obra, tt_id = _reconocer_obra_con_id(serie)
    if tipo_obra == "series" and tt_id:
        bitacora.append(f"atajo directo a la serie (tt={tt_id})")
        _abrir_serie_en_stremio(tt_id, region)
        lista_ok = False
        for _ in range(6):
            if _overlay_fuentes(region):
                lista_ok = True
                break
            lineas_lista = vision.ocr_lineas((int(x0 + (x1 - x0) * 0.55), y0 + 120, x1 - 10, y1 - 20))
            if any("search videos" in n or "season" in n or "watched" in n or "episode" in n for x, y, n, r in lineas_lista):
                lista_ok = True
                break
            time.sleep(1.0)
        if lista_ok:
            temporada = int(prog["temporada"] or 1) if prog and prog.get("temporada") else 1
            for x, y, n, r in vision.ocr_lineas((int(x0 + (x1 - x0) * 0.72), y0 + 150, x1 - 20, y1 - 40)):
                m = re.search(r"season\s*(\d+)", n)
                if m:
                    temporada = int(m.group(1))
                    break
            if temporada > 1:
                _elegir_temporada(temporada, region)
            ok, num_ep, hallado = _clic_episodio(region, prog)
            if not ok and hallado == "parado":
                return "Vale, parado."
            if not ok and hallado == "nueva_serie":
                r = _intentar_episodio1_directo(region, serie, tt_id, temporada, bitacora)
                if r:
                    return r
            if ok:
                time.sleep(2.4)
                if not _reproducir_stream(region, serie, temporada, num_ep, bitacora):
                    bitacora.append("no se confirmÃ³ la reproducciÃ³n del episodio")
                if num_ep:
                    return " ".join(bitacora) + f". Te pongo {serie}, temporada {temporada}, episodio {num_ep} ({hallado}). Es el primero que no tiene la marca de visto."
                return " ".join(bitacora) + ". El episodio ya estÃ¡ en reproducciÃ³n, no toco nada mÃ¡s."
            if _jugando_estable(region):
                return " ".join(bitacora) + ". El episodio ha empezado a reproducirse, no toco nada mÃ¡s."
            bitacora.append("; el atajo directo no dejÃ³ la lista de episodios, sigo con bÃºsqueda")
        else:
            bitacora.append("; el atajo directo no abriÃ³ la lista, sigo con bÃºsqueda")
        _enfocar_stremio()
        time.sleep(0.8)

    # 1. Home y atajo de Continue Watching (con verificaciÃ³n)
    _clic_home(region)
    time.sleep(1.6)
    ok_cw, _ = _abrir_desde_continue_watching(serie, region)
    if ok_cw:
        bitacora.append("usÃ© Continue Watching")
        time.sleep(3.2)
        # El CW a veces abre un detalle que devuelve a la home o al picker;
        # si la pantalla ha vuelto a ser la home, no damos la serie por abierta.
        if _en_home(region):
            ok_cw = False
            bitacora.append("(el CW no dejÃ³ abierta la serie; reintento por bÃºsqueda) ")
            time.sleep(1.0)
    else:
        bitacora.append("sin Continue Watching, busco")

    # 2. BÃšSQUEDA (si no hubo Continue Watching o fallÃ³ verificaciÃ³n): por
    # enlace directo stremio:///search (fiable, sin depender del icono ni del foco)
    if not ok_cw:
        ok, hallado = _abrir_serie_por_busqueda(serie, region, bitacora)
        if not ok:
            return " ".join(bitacora) + f". BusquÃ© {serie} en Stremio por enlace directo pero no abrÃ­ la serie ('{hallado}')."

    # 3. Ahora SÃ: decidir la pantalla tras abrir la serie (picker o lista de episodios)
    col = (int(x0 + (x1 - x0) * 0.72), y0 + 200, x1 - 20, y1 - 20)
    lineas = vision.ocr_lineas(col)
    hay_filtro = any("search videos" in n or "season" in n or "next" in n for x, y, n, r in lineas)
    hay_fuentes = any(_es_fuente(n) or "netflix" in n for x, y, n, r in lineas)

    if hay_fuentes and not hay_filtro:
        # Selector de streams: clica la mejor fuente Torrentio
        ok, fuente, _ = _clic_primera_fuente(region)
        if ok:
            bitacora.append(f"fuente {fuente}")
        cabecera = vision.ocr_lineas((x0 + int((x1 - x0) * 0.5), y0 + 80, x1, y0 + 300))
        prog_ep = _episodio_en_lineas(cabecera + lineas) or (None, None)
        time.sleep(1.5)
        temporada = int(prog_ep[0]) if prog_ep and prog_ep[0] else int(prog["temporada"] or 1) if prog and prog.get("temporada") else 1
        episodio = int(prog_ep[1]) if prog_ep and prog_ep[1] else None
        if episodio:
            memory.registrar_progreso_serie(serie, temporada, episodio)
        texto_ep = f" ({serie} T{temporada}E{episodio})" if episodio else ""
        threading.Thread(target=_poner_stremio_fullscreen, daemon=True).start()
        return " ".join(bitacora) + f". Te pongo el siguiente episodio en Stremio{texto_ep}."

    # PÃ¡gina de la serie (lista de episodios con filtro "search videos" o venimos de CW ok)
    temporada = int(prog["temporada"] or 1) if prog and prog.get("temporada") else 1

    # Detectamos la temporada REAL visible en el selector "seasonX" de la lista
    # ANTES de clicar el episodio (el overlay de fuentes tapa esa columna despuÃ©s).
    if not _en_home(region):
        for x, y, n, r in vision.ocr_lineas((int(x0 + (x1 - x0) * 0.72), y0 + 150, x1 - 20, y1 - 40)):
            m = re.search(r"season\s*(\d+)", n)
            if m:
                temporada = int(m.group(1))
                break

    if temporada > 1:
        _elegir_temporada(temporada, region)
    ok, num_ep, hallado = _clic_episodio(region, prog)
    if not ok and hallado == "parado":
        return "Vale, parado."
    if not ok and hallado == "nueva_serie":
        r = _intentar_episodio1_directo(region, serie, tt_id or None, temporada, bitacora)
        if r:
            return r
    if not ok:
        if ok_cw:
            bitacora.append("; el Continue Watching no dejó la lista de episodios, reintento por búsqueda directa ")
            ok_cw = False
            ok_s, hallado = _abrir_serie_por_busqueda(serie, region, bitacora)
            if ok_s:
                for x, y, n, r in vision.ocr_lineas((int(x0 + (x1 - x0) * 0.72), y0 + 150, x1 - 20, y1 - 40)):
                    m = re.search(r"season\s*(\d+)", n)
                    if m:
                        temporada = int(m.group(1))
                        break
                ok, num_ep, hallado = _clic_episodio(region, prog)
        if not ok and hallado == "parado":
            return "Vale, parado."
        if not ok and hallado == "nueva_serie":
            r = _intentar_episodio1_directo(region, serie, tt_id or None, temporada, bitacora)
            if r:
                return r
        if stop.toca_parar():
            return "Vale, parado."
    if not ok:
        if _jugando_estable(region):
            return " ".join(bitacora) + ". El episodio ha empezado a reproducirse, no toco nada más."
        return " ".join(bitacora) + ". Abrí la serie pero no veo ningún episodio sin ver (¿scroll hasta abajo? ¿otra temporada?)."

    # Al clicar el episodio, Stremio (v6/web) abre un OVERLAY con las fuentes
    # (Torrentio/Crunchyroll). Clicamos una fuente Torrentio y verificamos que
    # empiece a reproducir; si no, probamos con la siguiente fuente.
    time.sleep(2.4)
    if not _reproducir_stream(region, serie, temporada, num_ep, bitacora):
        bitacora.append("no se confirmÃ³ la reproducciÃ³n del episodio")

    return " ".join(bitacora) + f". Te pongo {serie}, temporada {temporada}, episodio {num_ep} ({hallado}). Es el primero que no tiene la marca de visto."

def _ventana_stremio():
    for v in hammer.listar_ventanas():
        if "stremio" in (v.get("titulo") or "").lower():
            return v
    return None


def _enfocar_stremio():
    """Trae Stremio al frente de forma FIABLE: AppActivate + SetForegroundWindow
    con el truco del ALT (salta el bloqueo de primer plano) + verificaciÃ³n real."""
    import subprocess as _sp
    try:
        handles = []

        def _cb(h, _):
            if win32gui.IsWindowVisible(h) and "stremio" in (win32gui.GetWindowText(h) or "").lower():
                handles.append(h)

        win32gui.EnumWindows(_cb, None)
        if not handles:
            return False
        objetivo = max(handles, key=lambda h: len(win32gui.GetWindowText(h)))

        try:
            placement = win32gui.GetWindowPlacement(objetivo)
            if placement[1] == win32con.SW_SHOWMINIMIZED:
                win32gui.ShowWindow(objetivo, win32con.SW_RESTORE)
        except Exception:
            pass

        # 1) AppActivate suave
        try:
            _sp.run(["powershell", "-NoProfile", "-Command",
                     "(New-Object -ComObject WScript.Shell).AppActivate('Stremio')"],
                    capture_output=True, text=True, timeout=3,
                    creationflags=0x08000000)  # sin ventana de cmd
        except Exception:
            pass
        time.sleep(0.2)

        if win32gui.GetForegroundWindow() != objetivo:
            # 2) Truco del ALT para saltar el bloqueo de primer plano
            ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)
            try:
                win32gui.SetForegroundWindow(objetivo)
            finally:
                ctypes.windll.user32.keybd_event(0x12, 0, 0x2, 0)
        time.sleep(0.3)
        return win32gui.GetForegroundWindow() == objetivo
    except Exception:
        return False


def _clic_ocr(texto, region, ajuste_y=0, umbral=0.5):
    """Busca texto por OCR dentro de una regiÃ³n y hace clic en su centro."""
    r = vision.buscar_por_ocr(texto, region=region)
    if not r or r[2] < umbral:
        return False, (r[3] if r else None)
    vision.mover_y_clicar(r[0], r[1] + ajuste_y)
    return True, r[3]


def _elegir_temporada(temporada, region):
    """Intenta cambiar al selector de temporada de la pÃ¡gina de la serie."""
    x0, y0, x1, y1 = region
    if stop.toca_parar():
        return False, None
    tope = (x0, y0 + 180, x1, y0 + 380)
    ok, _ = _clic_ocr("season", tope, umbral=0.5)
    if not ok:
        ok, _ = _clic_ocr("temporada", tope, umbral=0.5)
    if not ok:
        vision.mover_y_clicar(int(x0 + (x1 - x0) * 0.83), y0 + 200)
    time.sleep(0.8)
    lista = (x0 + int((x1 - x0) * 0.6), y0 + 250, x1 - 20, y1 - 20)
    ok, hallado = _clic_ocr(str(temporada), lista, umbral=0.6)
    time.sleep(2.0)
    return ok, hallado


def _clic_verificado(x, y, verificar, espera=3.2, reintentos_raton=1):
    """Clic FANTASMA primero (sin mover el ratÃ³n); si el estado no cambia,
    reintenta con el ratÃ³n real. `verificar` es un callable sin argumentos."""
    vision.mover_y_clicar_ghost(x, y)
    time.sleep(espera)
    if verificar():
        return True
    for _ in range(reintentos_raton):
        vision.mover_y_clicar(x, y)
        time.sleep(espera)
        if verificar():
            return True
    return False


def _es_fuente(norm):
    """Una lÃ­nea OCR es una FUENTE si trae tamaÃ±o de vÃ­deo (mb/gb/gib/tb) o un
    proveedor de streams. Torrentio muestra '1.2 gb' en episodios: por eso NO
    basta con buscar 'mb'."""
    if re.search(r"\d[\d\s.]*?\s*(?:mb|gb|gib|tb)\b", norm):
        return True
    return any(w in norm for w in ("torrentio", "realdebrid", "real debrid", "cached"))


def _overlay_fuentes(region):
    """Â¿Se ve el selector de streams (filas con tamaÃ±o 'mb'/'gb' o proveedor)?"""
    x0, y0, x1, y1 = region
    col = (int(x0 + (x1 - x0) * 0.55), y0 + 150, x1 - 10, y1 - 20)
    try:
        return any(_es_fuente(n) for x, y, n, r in vision.ocr_lineas(col))
    except Exception:
        return False


def _es_titulo(objetivo, norm):
    """ComparaciÃ³n LITERAL de tÃ­tulo: el texto OCR ha de ser EL tÃ­tulo, no solo
    contenerlo. Admite que el OCR corte el final ('breaking ba', 'breaking b')."""
    if norm == objetivo:
        return True
    if norm.startswith(objetivo):
        return True
    if objetivo.startswith(norm) and len(norm) >= len(objetivo) - 3:
        return True
    return False


def _en_home(region):
    """True si la pantalla sigue siendo la home de Stremio (filas con 'SeeAll')."""
    x0, y0, x1, y1 = region
    seeall = 0
    for x, y, n, r in vision.ocr_lineas((x0, y0, x1, y0 + 520)):
        if n == "seeall" or "see all" in n or n == "ver todo":
            seeall += 1
    if seeall >= 2:
        return True
    for x, y, n, r in vision.ocr_lineas((x0, y0 + 120, x1, y0 + 220)):
        if "continue watching" in n or "continuar" in n or "next up" in n:
            return True
    return False


def _buscar_linea_serie(serie, region, banda_y=None):
    """Busca la lÃ­nea del tÃ­tulo de la serie con comparaciÃ³n LITERAL (el texto ha de
    ser EL tÃ­tulo, no algo que lo contenga). Premia la coincidencia exacta."""
    objetivo = _norm_serie(serie)
    if not objetivo:
        return None
    mejores = []
    for x, y, norm, raw in vision.ocr_lineas(region):
        if not _es_titulo(objetivo, norm):
            continue
        score = 1.0
        if norm == objetivo:
            score += 0.3
        if norm.startswith(objetivo):
            score += 0.1
        if banda_y is not None:
            if banda_y - 20 <= y <= banda_y + 160:
                score += 0.2
            else:
                score -= 0.3
        mejores.append((score, x, y, norm))
    if not mejores:
        return None
    mejores.sort(reverse=True, key=lambda c: c[0])
    return mejores[0][1], mejores[0][2], mejores[0][3], mejores[0][0]


def _clic_serie(serie, region, banda_y=None):
    """Clica la serie en los resultados usando _buscar_linea_serie.
    Clic fantasma primero; si no abre la serie, reintenta con ratÃ³n real."""
    hallazgo = _buscar_linea_serie(serie, region, banda_y=banda_y)
    if hallazgo:
        # Clavar un poco por encima del texto: acertamos la tarjeta (pÃ³ster) igualmente
        ok = _clic_verificado(hallazgo[0], max(0, hallazgo[1] - 60),
                              lambda: _pagina_contiene_serie(serie, region))
        if ok or _pagina_contiene_serie(serie, region):
            return True, hallazgo[2]
    return False, None


def _tarjetas_continue_watching(region):
    """Devuelve (x, y, etiqueta) de las tarjetas de 'Continue watching' de la
    home. La etiqueta es el tÃ­tulo leÃ­do por el OCR en esa tarjeta."""
    x0, y0, x1, y1 = region
    band = (x0, y0 + 150, x1, y0 + 280)
    lineas = [t for t in vision.ocr_lineas(band)
              if not any(p in t[2] for p in ("continue watching", "continuar", "seeall", "ver todo", "stremio"))
              and t[0] > x0 + int((x1 - x0) * 0.08)]
    if not lineas:
        return []
    buckets = {}
    for x, y, norm, raw in lineas:
        b = int(y // 30)
        buckets.setdefault(b, []).append((x, y, norm))
    mejor = max(buckets.values(), key=len)
    por_x = []
    for x, y, norm in sorted(mejor):
        if por_x and abs(x - por_x[-1][0]) < int((x1 - x0) * 0.10):
            continue
        por_x.append((x, y, norm))
    return por_x


def _pagina_contiene_serie(serie, region):
    if _en_home(region):
        return False
    objetivo = _norm_serie(serie)
    if not objetivo:
        return False
    lineas = list(vision.ocr_lineas(region))
    # Directo: alguna lÃ­nea que sea literalmente el tÃ­tulo
    for x, y, n, r in lineas:
        norm_l = _norm_serie(n)
        if norm_l and _es_titulo(objetivo, norm_l):
            return True
    # Stremio parte el tÃ­tulo en dos lÃ­neas ('grand blue' + 'dreaming'): une
    # lÃ­neas consecutivas (misma zona vertical y x parecida) y lo reintenta
    ordenadas = sorted(((y, x, n) for x, y, n, r in lineas), key=lambda c: (c[0], c[1]))
    for i in range(len(ordenadas) - 1):
        y1, x1, n1 = ordenadas[i]
        y2, x2, n2 = ordenadas[i + 1]
        if y1 >= 0 and 0 <= y2 - y1 <= 60 and abs(x2 - x1) <= 160:
            unido = _norm_serie(f"{n1} {n2}")
            if unido and _es_titulo(objetivo, unido):
                return True
    return False


def _clasificar_item(region):
    """Clasifica lo que hay abierto en Stremio: 'serie', 'peli' o '?'.
    Serie = estructura de temporada/episodios (S1E6, Season, Search Videos).
    Peli = botÃ³n 'Trailer' visible y SIN estructura de episodios."""
    x0, y0, x1, y1 = region
    col = (x0 + int((x1 - x0) * 0.60), y0 + 180, x1 - 20, y1 - 20)
    for x, y, n, r in vision.ocr_lineas(col):
        if ("search videos" in n or "season" in n or "temporada" in n
                or re.search(r"\bs\s*e\s*\d+", n)):
            return 'serie'
    texto = vision.ocr_lineas((x0, y0 + 380, x1, y1))
    if any("trailer" in n for x, y, n, r in texto):
        return 'peli'
    return '?'


def _clic_serie_en_resultados(serie, region, y_header):
    """Clica la serie pedida en los resultados de bÃºsqueda.
    1) Intenta el MATCH LITERAL del tÃ­tulo dentro de la fila 'Popular - Series'
       (leer la tarjeta correcta por su texto).
    2) Solo si el OCR no alcanza a leer el tÃ­tulo, Ãºltimo recurso: clica la
       primera tarjeta de la fila y va probando las siguientes evitando pelis."""
    x0, y0, x1, y1 = region
    franja = (x0, y_header + 20, x1, min(y_header + 280, y1))

    hallado_literal = _buscar_linea_serie(serie, franja, banda_y=y_header)
    if hallado_literal:
        ok = _clic_verificado(
            hallado_literal[0], max(0, hallado_literal[1] - 60),
            lambda: _pagina_contiene_serie(serie, region) and _clasificar_item(region) != 'peli')
        if ok:
            return True, hallado_literal[2]

    cartas = []
    for xx, yy, nn, rr in vision.ocr_lineas(franja):
        if len(nn) < 3:
            continue
        if any(p in nn for p in ("netflix", "torrentio", "subscription", "seeall",
                                 "ver todo", "series", "movies", "popular")):
            continue
        cartas.append((yy, xx, rr))
    if not cartas:
        return False, None
    cartas.sort()
    dedup = []
    for c in cartas:
        if dedup and abs(c[1] - dedup[-1][1]) < 200:
            continue
        dedup.append(c)
    for i in range(min(3, len(dedup))):
        yy, xx, txt = dedup[i]
        ok = _clic_verificado(
            xx, max(0, yy - 110),
            lambda: _pagina_contiene_serie(serie, region) and _clasificar_item(region) != 'peli')
        if ok:
            return True, txt
    return False, None


def _abrir_desde_continue_watching(serie, region):
    """Clica en 'Continue watching' SOLO la tarjeta de la serie pedida (las
    demÃ¡s se descartan SIN abrirlas, para no saltar por pantalla a la serie
    equivocada que el usuario justo ve en su home). Devuelve (ok, origen)."""
    objetivo = _norm_serie(serie)
    tarjetas = _tarjetas_continue_watching(region)
    tarjetas = [(x, y, lbl) for (x, y, lbl) in tarjetas if _es_titulo(objetivo, _norm_serie(lbl))]
    if not tarjetas:
        return False, None
    for tx, ty, lbl in tarjetas[:3]:
        _clic_home(region)
        time.sleep(1.5)
        print(f"[CW] abriendo tarjeta '{lbl}' en ({tx},{ty})")
        ok = _clic_verificado(
            tx, ty,
            lambda: _pagina_contiene_serie(serie, region) and _clasificar_item(region) != 'peli')
        if ok:
            return True, "continue_watching"
    _clic_home(region)
    time.sleep(1.5)
    return False, None


def _episodio_en_lineas(lineas):
    """Busca 'S1E6' / 'SIE6' / 'T1E6' / '1x6' / 'S6' en las lÃ­neas OCR -> (temporada, episodio) o None."""
    patrones = [
        re.compile(r"(?:s|t)\s*(\d+)\s*[-\s]?\s*e\s*(\d+)", re.I),  # S1E6, T1-E6, s 1 e 6
        re.compile(r"\b(\d+)\s*[xX]\s*(\d+)\b"),                     # 1x6
    ]
    for x, y, norm, raw in lineas:
        for p in patrones:
            m = p.search(norm)
            if m:
                return int(m.group(1)), int(m.group(2))
    for x, y, norm, raw in lineas:
        m = re.search(r"\bs\s*i\s*e\s*(\d+)\b", norm, re.I) or re.search(r"\bs\s*([1-9])\s*e\s*(\d+)\b", norm, re.I)
        if m:
            t = 1 if len(m.groups()) == 1 else int(m.group(1))
            ep = int(m.group(1) if len(m.groups()) == 1 else m.group(2))
            return t, ep
    for x, y, norm, raw in lineas:
        if "watching" in norm or "siguiente" in norm:
            m = re.search(r"e\s*(\d+)", norm, re.I)
            if m:
                return 1, int(m.group(1))
    return None


def _clic_home(region):
    """Lleva a la home de Stremio: clica el logo (texto 'Stremio') o, si no aparece, la esquina arriba-izquierda."""
    x0, y0, x1, y1 = region
    r = vision.buscar_por_ocr("stremio", region=(x0, y0, int(x0 + (x1 - x0) * 0.3), y0 + 80))
    if r and r[2] >= 0.7:
        vision.mover_y_clicar_ghost(r[0], r[1])
        return True
    vision.mover_y_clicar_ghost(int(x0 + (x1 - x0) * 0.043), y0 + 15)
    return True


def _clic_primera_fuente(region, excluir=None):
    """En el selector de streams: clica SOLO una fuente Torrentio (la primera que,
    si excluir es un set de Y ya probadas, siga sin probar). NUNCA Netflix.
    Clic fantasma primero; si el reproductor no arranca, reintenta con ratÃ³n
    real y si aun asÃ­ falla, marca la fila como fallida y sigue con otra."""
    x0, y0, x1, y1 = region
    col = (int(x0 + (x1 - x0) * 0.72), y0 + 200, x1 - 20, y1 - 20)
    for x, y, norm, raw in vision.ocr_lineas(col):
        if stop.toca_parar():
            return False, None, None
        if _es_fuente(norm) and (not excluir or all(abs(y - e) > 12 for e in excluir)):
            # El 'mb'/'gb' estÃ¡ en la parte derecha de la fila; anclamos el clic a
            # la IZQUIERDA de la fila (icono/texto Torrentio), mÃ¡s fiable.
            cx = int(x0 + (x1 - x0) * 0.72) + 40
            if _jugando(region) or _clic_verificado(cx, y, lambda: _jugando(region), espera=2.5):
                if excluir is not None:
                    excluir.add(y)
                return True, "torrentio", y
            if excluir is not None:
                excluir.add(y)  # fila muerta (o fantasma ignorado): no reintentar
            time.sleep(0.6)
    return False, None, None


def _episodio_lanzado(region):
    """Un episodio estÃ¡ LANZADO si aparece el selector de fuentes O ya se estÃ¡
    reproduciendo (Stremio a veces arranca el stream directo, sin selector)."""
    return _overlay_fuentes(region) or _jugando(region)


def _clic_episodio_fila(x, y, region):
    """Clic en una fila de episodio: fantasma primero; si ni el selector de
    fuentes ni la reproducciÃ³n aparecen, reintenta con ratÃ³n real."""
    if stop.toca_parar():
        return False
    vision.mover_y_clicar_ghost(x, y)
    time.sleep(2.2)
    if _episodio_lanzado(region):
        return True
    vision.mover_y_clicar(x, y)
    time.sleep(2.2)
    return _episodio_lanzado(region)


def _reproducir_stream(region, serie, temporada, episodio, bitacora, max_intentos=6):
    """Reproduce el stream del episodio. Si ya estÃ¡ sonando (algunas series
    arrancan directo) no clica nada; si no, clica fuentes Torrentio hasta
    verificar JUGANDO. Registra progreso y lanza el fullscreen al conseguirlo."""
    if not episodio:
        return _jugando(region)
    if _jugando(region):
        bitacora.append("episodio ya en reproducciÃ³n, sin tocar el selector")
        memory.registrar_progreso_serie(serie, temporada, episodio)
        threading.Thread(target=_poner_stremio_fullscreen, daemon=True).start()
        return True
    probadas = set()
    ok_play = False
    for _ in range(max_intentos):
        if stop.toca_parar():
            bitacora.append("(parado por clic)")
            return False
        ok_src, fuente, y_src = _clic_primera_fuente(region, probadas)
        if not ok_src:
            break
        bitacora.append(f"fuente {fuente}")
        time.sleep(4.2)
        if _jugando(region):
            ok_play = True
            break
        probadas.add(y_src)
    if not ok_play:
        bitacora.append("probÃ© fuente(s) sin confirmar reproducciÃ³n final")
    if ok_play or _jugando(region):
        memory.registrar_progreso_serie(serie, temporada, episodio)
        threading.Thread(target=_poner_stremio_fullscreen, daemon=True).start()
        return True
    return False


def _jugando(region):
    """Â¿EstÃ¡ reproduciendo? Si aÃºn se leen marcadores de la pÃ¡gina de la serie o
    del selector de fuentes, el vÃ­deo no ha arrancado."""
    x0, y0, x1, y1 = region
    lineas = vision.ocr_lineas((x0, y0, x1, y1))
    if not lineas:
        return True  # sin texto legible -> pantalla oscura de vÃ­deo
    txt = " ".join(n for x, y, n, r in lineas)
    firmas = ("receive notifications", "search videos", "watched", "reproducir",
              "season", "trailer", "cast", "genres", "torrentio", "crunchyroll",
              "1080p", "upcoming", "previous", "subscrip")
    return not any(f in txt for f in firmas)


def _jugando_estable(region, segundos=2.5):
    """_jugando() sostenido unos segundos: no da por reproducido un episodio por
    una pantalla vacÃ­a transitoria (carga de pÃ¡gina)."""
    t0 = time.time()
    while time.time() - t0 < segundos:
        if not _jugando(region):
            return False
        time.sleep(0.6)
    return True


_EPI_LOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "epi_debug.log")


def _epi_log(msg):
    try:
        with open(_EPI_LOG, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


def _peek_next_episodio(lineas_ocr):
    """Stremio muestra en la cabecera de la serie 'Up next: Episode N' (tambiÃ©n
    'Up Next', 'Siguiente', 'Continuar'...). Ese nÃºmero ES el episodio que toca,
    sin depender de la geometrÃ­a de los badges WATCHED (varÃ­a por serie)."""
    texto = " ".join(((n or "") + " ") for _, _, n, _ in lineas_ocr).lower()

    def _ext_ep(cad):
        m = re.search(r"\bs\s*\d+\s*e\s*(\d+)\b", cad)
        if m: return int(m.group(1))
        m = re.search(r"(?:season|temporada)\s*\d+\s*(?:episode|episodio|ep)\s*(\d+)", cad)
        if m: return int(m.group(1))
        m = re.search(r"(?:episode|episodio|ep)\s*(\d+)", cad)
        if m: return int(m.group(1))
        m = re.search(r"\b(\d{1,3})\b", cad)
        if m: return int(m.group(1))
        return None

    def _filtra(n):
        return n if (n is not None and 0 < n < 500) else None

    m_ctx = re.search(r"(?:up next|next up|watch next|next episode|siguiente|continuar(?: viendo)?|sigue)", texto)
    if m_ctx:
        n = _ext_ep(texto[m_ctx.end():m_ctx.end() + 120])
        if n is not None:
            return _filtra(n)

    m = re.search(r"\bs\s*\d+\s*e\s*(\d+)\b", texto)
    if m:
        return _filtra(int(m.group(1)))
    return None


def _clic_episodio(region, prog=None):
    """En la pÃ¡gina de la serie: clica el primer episodio SIN la marca 'WATCHED'
    (el siguiente que toca). Si el OCR no lee NINGUNA marca, se apoya en el
    progreso guardado por Pichu para no quedarse siempre en el episodio 1."""
    x0, y0, x1, y1 = region
    # Ampliamos la regiÃ³n (desde 0.55 en vez de 0.72) para no cortar textos largos
    col = (int(x0 + (x1 - x0) * 0.55), y0 + 150, x1 - 10, y1 - 20)
    _epi_log(f"[clic_episodio] region={region} col={col}")
    prog_ep = int(prog["episodio"] or 0) + 1 if prog and prog.get("episodio") else 1

    # Limpia el cuadro de bÃºsqueda de episodios (un texto residual filtra a 0)
    filtro = vision.buscar_por_ocr("search videos", region=col)
    if filtro:
        vision.mover_y_clicar_ghost(filtro[0], filtro[1])
    else:
        vision.mover_y_clicar_ghost(int(x0 + (x1 - x0) * 0.83), y0 + 270)
    time.sleep(0.4)
    _pg.hotkey("ctrl", "a")
    _pg.press("delete")
    time.sleep(0.8)

    # Sube la lista hasta el principio para partir desde el episodio 1
    # (rueda fantasma sobre la lista sin mover el cursor)
    hammer.scroll_en(int(x0 + (x1 - x0) * 0.83) - 10, int(y0 + (y1 - y0) * 0.5), 20)
    time.sleep(1.2)

    _pendiente = None        # (num, y_bucket) del candidato del intento anterior
    _fondo_consecutivas = 0
    _blanco_ancho = None
    for intento in range(14):
        if stop.toca_parar():
            return False, None, "parado"
        if _jugando_estable(region):
            return True, None, "en reproducciÃ³n"
        filas, badges = [], []
        lineas_ocr = vision.ocr_lineas(col)
        print(f"[Epi debug] lineas detectadas: {[l[2] for l in lineas_ocr]}")

        # Barrido amplio de la cabecera buscando "Up next: Episode N" (puede
        # quedar fuera de la columna estrecha). Se hace solo 2 veces: es fija.
        if intento in (0, 3):
            zona_hero = (int(x0 + (x1 - x0) * 0.05), y0, x1 - 5, y0 + int((y1 - y0) * 0.45))
            lineas_hero = vision.ocr_lineas(zona_hero)
            _epi_log(f"[hero] {intento}: {[l[2] for l in lineas_hero]}")
            _blanco_ancho = _peek_next_episodio(lineas_hero)
            if _blanco_ancho:
                _epi_log(f"[hero] -> up-next={_blanco_ancho}")

        for x, y, norm, raw in lineas_ocr:
            num = None
            m1 = re.match(r"^(\d+)(?: |\s|\.|-)", norm)
            m2 = re.match(r"^(?:ep|episode|episodio)\s*(\d+)", norm)
            if m1:
                num = int(m1.group(1))
            elif m2:
                num = int(m2.group(1))
            if num is not None and num < 1000:
                filas.append((num, x, y, norm))
            if any(w in norm for w in ("watched", "visto", "seen")):
                badges.append(y)

        filas_unicas = []
        # Franja del buscador/hero arriba: los textos "1 tÃ­tulo..." que viven
        # ahÃ­ no son del grid (clicar ahÃ­ no reproduce). Solo grid real (mÃ¡s abajo).
        for f in sorted(filas, key=lambda f: f[2]):
            if f[2] <= y0 + 260:
                continue
            if not filas_unicas or abs(f[2] - filas_unicas[-1][2]) > 15:
                filas_unicas.append(f)
        filas_unicas.sort(key=lambda f: f[0])

        sin_badges = not badges
        ultima_y = max((y for _, _, y, _ in filas_unicas), default=None)
        print(f"[Epi] intento {intento}: filas={[(f[0], f[2]) for f in filas_unicas]} watchers_y={badges}")
        _epi_log(f"[Epi] {intento}: filas={[(f[0], f[2]) for f in filas_unicas]} watchers_y={badges}")

        # Serie NUEVA y la fila 1 no aparece: el hero tapa el capÃ­tulo 1 (caso
        # visto en Attack on Titan, que empezaba en la fila 2 y clicaba el 2).
        # Devolvemos la seÃ±al 'nueva_serie' para que el llamador lo lance por
        # enlace directo {tt}:1:1 en vez de quedarse con un episodio errÃ³neo.
        if sin_badges and prog_ep <= 1 and filas_unicas and min(f[0] for f in filas_unicas) > 1:
            print("[Epi]   serie nueva: fila 1 tapada por el hero -> lanzo E1 directo")
            _epi_log(f"[Epi] {intento}: fila 1 oculta, min={min(f[0] for f in filas_unicas)} -> nueva_serie")
            return False, None, "nueva_serie"

        # ---- SeÃ±al fuerte: 'Up next: Episode N' en la cabecera ----
        blanco = _blanco_ancho or _peek_next_episodio(lineas_ocr)
        if blanco is not None:
            _epi_log(f"[Epi] {intento}: next={blanco} (visto en cabecera)")
            fila_blanco = next((f for f in filas_unicas if f[0] == blanco), None)
            if fila_blanco is not None:
                bucket = (blanco, round(fila_blanco[2] / 40))
                if _pendiente == bucket:
                    _fondo_consecutivas = 0
                    _epi_log(f"[Epi] {intento}: CLIC fila {blanco} ({fila_blanco[3]}) por Up Next")
                    ok = _clic_episodio_fila(fila_blanco[1] - 10, fila_blanco[2], region)
                    if ok:
                        return True, blanco, fila_blanco[3]
                else:
                    _pendiente = bucket
                    print(f"[Epi]   Stremio dice next={blanco} -> confirmo lectura")
                time.sleep(0.8)
                continue
            if not filas_unicas or all(f[0] < blanco for f in filas_unicas):
                print(f"[Epi]   next={blanco} aÃºn no visible -> bajo")
                hammer.scroll_en(int(x0 + (x1 - x0) * 0.83) - 10, int(y0 + (y1 - y0) * 0.5), -3)
                time.sleep(1.6)
                continue

        # ---- Asignar cada badge a la fila MÃS CERCANA verticalmente, SOLO si
        # estÃ¡ a <=45px de su lÃ­nea: los badges viajan pegados a su fila y el
        # pitch de las filas es ~95px, asÃ­ un badge 'colado' del episodio de
        # arriba (reciÃ©n salido por el borde alto) queda descartado en vez de
        # marcar como visto al siguiente episodio.
        vistos = set()
        for by in badges:
            mejor, d_mejor = None, None
            for fnum, _, fy, _ in filas_unicas:
                d = abs(fy - by)
                if d_mejor is None or d < d_mejor:
                    mejor, d_mejor = fnum, d
            if mejor is not None and d_mejor <= 45:
                vistos.add(mejor)

        # --- buscar la PRIMERA fila sin badge ---
        candidato = None
        for num, x, y, norm in filas_unicas:
            tiene_badge = num in vistos
            # El progreso guardado solo respalda cuando el OCR no lee NINGÃšN
            # badge (pantalla con marcas ilegibles). Nunca se impone sobre una
            # marca visible: la lista en pantalla es quien dice la verdad y el
            # progreso de Pichu puede arrastrar aciertos de pruebas anteriores.
            if sin_badges and prog_ep > 1 and num < prog_ep:
                tiene_badge = True
            print(f"[Epi]   fila {num} y={y} -> {'WATCHED' if tiene_badge else 'SIGUIENTE'}")
            if not tiene_badge:
                candidato = (num, x, y)
                break

        # --- sin candidato: todo visible estÃ¡ visto -> bajar ---
        if candidato is None:
            _pendiente = None
            _fondo_consecutivas = 0
            hammer.scroll_en(int(x0 + (x1 - x0) * 0.83) - 10, int(y0 + (y1 - y0) * 0.5), -2)
            time.sleep(1.8)
            continue

        num, x, y = candidato
        min_y = min((fy for _, _, fy, _ in filas_unicas), default=None)
        # Banda inferior: cualquier fila en los ~70px del borde bajo (no solo la
        # Ãºltima) puede tener su badge cortado por el lÃ­mite de la ventana OCR.
        en_banda_baja = ultima_y is not None and y >= ultima_y - 70
        # Banda superior: solo se sospecha si HAY badges (vistos): en una serie
        # nueva (sin vistas) el primer episodio ES el correcto y el borde alto
        # no esconde nada.
        en_banda_alta = min_y is not None and y <= min_y + 40 and bool(vistos)

        if en_banda_baja or en_banda_alta:
            _pendiente = None
            _fondo_consecutivas += 1
            if _fondo_consecutivas >= 3 and en_banda_baja:
                # Persiste en el borde bajo tras varias lecturas: o la lista
                # acaba aquÃ­ o su badge no existe. DÃ¡rsela.
                _fondo_consecutivas = 0
                _epi_log(f"[Epi] {intento}: CLIC fila {num} por borde repetido")
                ok = _clic_episodio_fila(x - 10, y, region)
                if ok:
                    return True, num, norm
            dir_scroll = -3 if en_banda_baja else 3
            print(f"[Epi]   fila {num} en borde ({'bajo' if en_banda_baja else 'alto'}) -> muevo para releer")
            hammer.scroll_en(int(x0 + (x1 - x0) * 0.83) - 10, int(y0 + (y1 - y0) * 0.5), dir_scroll)
            time.sleep(1.8)
            continue

        # --- candidato en medio: confirmar con segunda lectura SIN mover nada ---
        bucket = (num, round(y / 40))
        if _pendiente == bucket:
            # Dos lecturas seguidas ven lo mismo -> seguro
            _fondo_consecutivas = 0
            _epi_log(f"[Epi] {intento}: CLIC fila {num} ({norm}) por badges")
            ok = _clic_episodio_fila(x - 10, y, region)
            if ok:
                return True, num, norm
            # si fallÃ³ el clic, sigue el bucle
        else:
            _pendiente = bucket
            print(f"[Epi]   candidato {num} -> releo 0.8 s para confirmar")

        time.sleep(0.8)

    return False, None, None


def _traer_zen_a_primaria(tema=""):
    """Trae la ventana de Zen al frente y al primario. Prefiere la pestaÃ±a cuyo tÃ­tulo contenga el tema."""
    try:
        handles = []

        def _cb(h, _):
            if win32gui.IsWindowVisible(h) and win32gui.GetWindowText(h):
                handles.append(h)

        win32gui.EnumWindows(_cb, None)
        tokens = set(vision._norm(tema or "").split())

        zen = []
        for h in handles:
            titulo = win32gui.GetWindowText(h)
            if "zen" in titulo.lower() and ("youtube" in titulo.lower() or "busqueda" in titulo.lower() or not tokens or any(t in titulo.lower() for t in tokens)):
                zen.append((h, titulo))

        objetivo = None
        for h, t in zen:
            if tokens and any(tok in t.lower() for tok in tokens):
                objetivo = h
                break
        if objetivo is None and zen:
            objetivo = zen[0][0]
        if objetivo is None:
            return False

        # Windows bloquea SetForegroundWindow desde procesos sin foco; lo
        # reforzamos con AppActivate y verificamos que haya quedado al frente.
        try:
            placement = win32gui.GetWindowPlacement(objetivo)
            if placement[1] == win32con.SW_SHOWMINIMIZED:
                win32gui.ShowWindow(objetivo, win32con.SW_RESTORE)
        except Exception:
            pass
        titulo = win32gui.GetWindowText(objetivo)
        try:
            cmd = (f'(New-Object -ComObject WScript.Shell).AppActivate'
                   f'("{titulo.replace(chr(34), "")}")')
            subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                           capture_output=True, text=True, timeout=3,
                           creationflags=0x08000000)  # sin ventana de cmd
        except Exception:
            pass
        if win32gui.GetForegroundWindow() != objetivo:
            try:
                win32gui.SetForegroundWindow(objetivo)
            except Exception:
                pass
        return win32gui.GetForegroundWindow() == objetivo
    except Exception as e:
        print(f"[Zen primera pantalla]: {e}")
    return False

def _titulo_ventana_activa():
    try:
        return win32gui.GetWindowText(win32gui.GetForegroundWindow()) or ""
    except Exception:
        return ""


def _verificar_reproductor(ancho, alto):
    for token in ("me gusta", "compartir", "guardar", "suscribirte", "suscribirse", "descargar", "vistas", "visualizaciones"):
        r = vision.buscar_por_ocr(token, region=(0, int(alto * 0.4), ancho, alto))
        if r and r[2] >= 0.45:
            return True
    return False

def _es_video_fiable(lineas):
    """LÃ­neas de metadatos (visualizaciones/hace) que delatan un vÃ­deo real."""
    return [(y, x) for x, y, n, raw in lineas
            if ("visualizacion" in n or "vistas" in n or "reproducciones" in n or "hace " in n or "viendo" in n or "emitio" in n)]

def _candidatos_feed(lineas, tokens_tema):
    """TÃ­tulos de vÃ­deo del feed que mencionan el tema; si no hay lÃ­nea de vistas, acepta tÃ­tulos largos."""
    excluir = {"canal", "directo", "videos", "juegos", "lista de",
               "tendencias", "suscripciones", "compartir", "me gusta", "guardar", "mostrar mas",
               "aceptar todo", "rechazar todo", "idioma", "privacidad", "anuncio", "adicional",
               "seguir viendo", "historial", "patrocinado"}
    metas = _es_video_fiable(lineas)
    out = []
    for x, y, norm, raw in lineas:
        if len(norm) < 8:
            continue
        if any(t in norm for t in excluir):
            continue
        # BÃºsqueda mucho mÃ¡s flexible: si alguna palabra del tema estÃ¡ en el texto
        if not any(t in norm for t in tokens_tema):
            continue
        # Ampliamos enormemente el rango para encontrar los metadatos (vistas/hace X tiempo) 
        # porque YouTube cambia constantemente de distancias
        tiene_meta = any(-20 <= (my - y) <= 180 and abs(mx - x) <= 600 for my, mx in metas)
        if not tiene_meta and len(norm) < 15:
            continue
        out.append((y, x, norm))
    out.sort(key=lambda c: (c[0], c[1]))
    uniq = []
    for y, x, n in out:
        if uniq and abs(y - uniq[-1][0]) < 20 and abs(x - uniq[-1][1]) < 60:
            continue
        uniq.append((y, x, n))
    return uniq


def _poner_video_via_zen(z, tema):
    """Busca el tema en YouTube en SEGUNDO PLANO (DOM, sin ratÃ³n ni pestaÃ±as
    duplicadas) y empieza a reproducir la primera coincidencia."""
    vids = z.buscar_videos_youtube(tema, limite=6)
    if not vids:
        return f"No encontrÃ© vÃ­deos de {tema} en YouTube (segundo plano)."
    primero = vids[0]
    z.navegar(primero["u"])
    z.esperar("video", tiempo=45)
    time.sleep(2.5)
    z.reproducir_tab()
    z.volumen_tab(90)
    time.sleep(1.5)
    est = z.estado_video()
    titulo = (primero.get("t") or "").strip()
    if est and est.get("ok"):
        st = "sonando" if not est.get("paused") else "arrancando (pausado)"
        return f"Puesto en segundo plano: '{titulo}' {st} en YouTube."
    return f"Puesto en segundo plano: abrÃ­ '{titulo}'."


def _video_ya_visible(tokens_tema, ancho, alto):
    """Â¿El vÃ­deo del tema YA estÃ¡ visible en pantalla (YouTube al frente)?
    Lo clica ahÃ­ mismo SIN abrir ventanas ni pestaÃ±as nuevas."""
    titulo_v = (_titulo_ventana_activa() or "").lower()
    es_youtube = "youtube" in titulo_v or "busqueda" in titulo_v or " - zen" in titulo_v
    if not es_youtube:
        return False, None
    for y, x, titulo in _candidatos_feed(vision.ocr_lineas(), tokens_tema)[:4]:
        vision.mover_y_clicar(x, y + 8)
        time.sleep(3.2)
        if _verificar_reproductor(ancho, alto):
            return True, titulo
    return False, None


def _poner_video_youtube(args):
    tema = (args.get("tema") or "").strip()
    if not tema:
        return "Dime quÃ© vÃ­deo quiero poner."
    ancho, alto = _pg.size()
    tokens_tema = set(t for t in vision._norm(tema).split() if not t.isdigit())
    cortos = {t for t in tokens_tema if len(t) <= 2}
    if len(tokens_tema) > 1:
        tokens_tema = tokens_tema - cortos
    if not tokens_tema:
        tokens_tema = set(vision._norm(tema).split())

    # 0. EL VÃDEO YA ESTÃ EN PANTALLA: clic ahÃ­ mismo, CERO ventanas nuevas.
    try:
        ya, titulo_ya = _video_ya_visible(tokens_tema, ancho, alto)
        if ya:
            return f"AbrÃ­ ({titulo_ya}): ya estaba a la vista, no abrÃ­ nada nuevo."
    except Exception as e:
        print(f"[ya visible]: {e}")

    # 1. Zen en 2Âº plano: SOLO si ya hay uno gestionado y activo. AquÃ­ NO
    # arrancamos uno nuevo (eso mostrarÃ­a una ventana de Zen y un cmd de
    # geckodriver que no quieres ver).
    try:
        import zen_bidi
        z = zen_bidi.obtener()
        if z.esta_abierto():
            return _poner_video_via_zen(z, tema)
    except Exception as e:
        print(f"[Zen bidi]: {e}")

    # 2. Â¿Zen ya tenÃ­a YouTube al frente? Si es asÃ­, miramos primero si el vÃ­deo
    # ya estÃ¡ a la vista sin abrir nada.
    if _traer_zen_a_primaria("youtube"):
        time.sleep(0.8)
        for y, x, titulo in _candidatos_feed(vision.ocr_lineas(), tokens_tema)[:4]:
            vision.mover_y_clicar(x, y + 8)
            time.sleep(3.2)
            if _verificar_reproductor(ancho, alto):
                return f"Puesto en primera pantalla: abrÃ­ '{titulo}' que ya estaba visible."

    # Una SOLA navegaciÃ³n a los resultados de bÃºsqueda (evita abrir home +
    # resultados = pestaÃ±a duplicada). El buscador de YouTube se activa Ã©l solo.
    q = quote_plus(tema)
    abrir_url(f"https://www.youtube.com/results?search_query={q}")
    time.sleep(3.5)

    # Primer intento: candidatos de feed/tema visibles en esta misma pÃ¡gina.
    for intento in range(3):
        candidatos = _candidatos_feed(vision.ocr_lineas(), tokens_tema)
        if candidatos:
            for y, x, titulo in candidatos[:4]:
                vision.mover_y_clicar(x, y + 8)
                time.sleep(3.2)
                if _verificar_reproductor(ancho, alto):
                    return f"Puesto desde tu feed: abrÃ­ el vÃ­deo '{titulo}'."
        _pg.scroll(-700)
        time.sleep(1.8)

    # Reserva: la pÃ¡gina de resultados puede tardar en renderizar: reintentamos
    # el OCR unas veces antes de rendirnos (misma pestaÃ±a).
    for intento_reserva in range(3):
        lineas = vision.ocr_lineas()
        candidatos = []
        metas = _es_video_fiable(lineas)
        for x, y, norm, raw in lineas:
            if not (int(alto * 0.15) <= y <= int(alto * 0.95)):
                continue
            if len(norm) < 15:
                continue
            if any(t in norm for t in ("canal", "directo", "patrocinado", "anuncio",
                                       "compartir", "me gusta", "acerca", "resultados")):
                continue
            contiene_tema = bool(tokens_tema & set(norm.split()))
            tiene_meta = any(0 <= (my - y) <= 55 and abs(mx - x) <= 340 for my, mx in metas)
            if contiene_tema and tiene_meta:
                candidatos.append((0, y, x, norm))
            elif tiene_meta:
                candidatos.append((1, y, x, norm))

        unicos = []
        for c in sorted(candidatos, key=lambda c: (c[0], c[1])):
            if not unicos or abs(c[1] - unicos[-1][1]) > 25 or abs(c[2] - unicos[-1][2]) > 120:
                unicos.append(c)

        if unicos:
            for prio, y, x, titulo in unicos[:4]:
                vision.mover_y_clicar(x, y)
                time.sleep(3.2)
                if _verificar_reproductor(ancho, alto):
                    return f"Puesto en primera pantalla: busquÃ© {tema} en YouTube y abrÃ­ el vÃ­deo '{titulo}'."
                if prio == 0:
                    return f"Puesto en primera pantalla: busquÃ© {tema} y abrÃ­ '{titulo}'."
                try:
                    _pg.hotkey("alt", "left")
                except Exception:
                    pass
                time.sleep(2.2)
            break
        if intento_reserva < 2:
            time.sleep(3.0)  # la pÃ¡gina aÃºn se estÃ¡ renderizando

    return f"No encontrÃ© ningÃºn vÃ­deo de {tema} en tu feed ni en el buscador de YouTube."


def _reproducir_en_zen(z, busqueda):
    canciones = z.buscar_musica_youtube(busqueda)
    if not canciones:
        z.minimizar()
        return f"No encontrÃ© canciones para '{busqueda}'."
    z.reproducir_cancion(0)
    z.minimizar()
    v = z.esperar_reproduccion(tiempo=25)
    titulo = canciones[0].get("t") or busqueda
    if v.get("ok"):
        return f"Sonando '{titulo}' en YouTube Music en 2Âº plano."
    return f"Abierta la bÃºsqueda de '{busqueda}' en YouTube Music."


def _poner_musica(args):
    busqueda = (args.get("busqueda") or "").strip(" ,.;")
    if not busqueda:
        return "¿Qué canción o artista quieres que ponga?"
    import reproduccion
    # 0) MOTOR LIGERO (mpv + yt-dlp): sin navegador, sin instancias, sin foco.
    #    Es la vía preferida: busca en YouTube Music y suena la primera canción.
    if reproduccion.disponible():
        try:
            cancion, err = reproduccion.reproducir(busqueda)
            if cancion:
                return f"Sonando '{cancion.get('titulo')}' de {cancion.get('artista')} en 2º plano. Iré poniendo más canciones de '{busqueda}' hasta que me digas de parar o quitarlas."
            return err or f"No pude poner '{busqueda}'."
        except Exception as e:
            print(f"[poner_musica motor]: {e}")
    import zen_bidi
    z = zen_bidi.obtener()
    # 1) Si el Zen de fondo de Pichu ya está ACTIVO (sin que yo lo haya abierto
    #    ahora), reproduzco ahí en 2º plano. JAMÁS lanzo una instancia nueva de Zen
    #    y NO abro pestañas visibles: si esto no puede sonar, lo mejor es avisar.
    if z.esta_abierto():
        try:
            return _reproducir_en_zen(z, busqueda)
        except Exception as e:
            print(f"[poner_musica zen]: {e}")
    return ("Mi motor de música no ha podido con esa canción esta vez y no quiero "
            f"abrirte YouTube Music a lo loco. Dímelo otra vez: '{busqueda}', y lo reintento.")


_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "poner_video",
            "description": "Pone un vÃ­deo de un tema jugando con YouTube en SEGUNDO PLANO (suena sin mover el ratÃ³n ni robar foco y sin abrir pestaÃ±as duplicadas). Ãšsala para 'for you page', 'mi inicio', 'mi feed', 'busca un video de [tema]', 'ponme un video de [tema]'. Si ya hay YouTube abierto de fondo, lo retoma; si no, lo abre y busca sola. NUNCA abre un buscador externo (DuckDuckGo/Google) ni combines esta herramienta con abrir_url: poner_video ya abre y navega YouTube por sÃ­ mismo. Ej: 'busca un video de <tema> en mi feed'.",
            "parameters": {
                "type": "object",
                "properties": {"tema": {"type": "string", "description": "QuÃ© buscar en YouTube"}},
                "required": ["tema"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "poner_musica",
            "description": "Busca y pone a sonar una canción o artista de YouTube Music en SEGUNDO PLANO sin abrir nada ni robar foco: usa primero el motor ligero de Pichu (mpv + yt-dlp, sin navegador); si ese motor falla, solo recurre al Zen de fondo de Pichu si ya está activo. JAMÁS abre pestañas y nunca lanza una instancia nueva de Zen. Úsala para 'ponme una canción de [artista]', '[canción] de [artista]', 'música de [algo]', 'busca el grupo [artista]'. NUNCA uses buscar_en_internet para música y NO la combines con abrir_url: poner_musica ya busca y reproduce por sí misma.",
            "parameters": {
                "type": "object",
                "properties": {"busqueda": {"type": "string", "description": "Canción o artista a buscar"}},
                "required": ["busqueda"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "abrir_url",
            "description": "Abre una URL o busca directa en el navegador Zen de la primera pestaÃ±a.",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string", "description": "URL completa o dominio, ej: https://www.youtube.com"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lanzar_app",
            "description": "Lanza o enfoca una aplicaciÃ³n instalada (Zen, Discord, Spotify, juegos de Steam, Android Studio, etc.). TambiÃ©n acepta grupos: 'trabajo' abre Microsoft Teams y TeamViewer a la vez.",
            "parameters": {
                "type": "object",
                "properties": {"app": {"type": "string", "description": "Nombre de la aplicaciÃ³n o grupo, ej: discord, zen browser, trabajo"}},
                "required": ["app"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_en_internet",
            "description": "Busca algo en internet abriendo los resultados en el navegador.",
            "parameters": {
                "type": "object",
                "properties": {"consulta": {"type": "string", "description": "Consulta de bÃºsqueda"}},
                "required": ["consulta"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_archivo_local",
            "description": "Busca un archivo local (Escritorio, Documentos, Descargas, disco D).",
            "parameters": {
                "type": "object",
                "properties": {
                    "nombre": {"type": "string", "description": "Nombre o fragmento del archivo"},
                    "abrir": {"type": "boolean", "description": "Si true lo localiza en el explorador", "default": True},
                },
                "required": ["nombre"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "abrir_carpeta_o_disco",
            "description": "Abre carpetas tÃ­picas o discos (descargas, documentos, disco D, Este Equipo).",
            "parameters": {
                "type": "object",
                "properties": {"destino": {"type": "string", "description": "QuÃ© abrir, ej: descargas, disco D"}},
                "required": ["destino"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ejecutar_comando_powershell",
            "description": "Usa SOLO si el usuario pide algo que solo un comando de sistema puede hacer (apagar, ipconfig, etc.). Con cuidado.",
            "parameters": {
                "type": "object",
                "properties": {"comando": {"type": "string", "description": "Comando de PowerShell"}},
                "required": ["comando"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "controlar_volumen",
            "description": "Sube, baja, fija o silencia el volumen del sistema.",
            "parameters": {
                "type": "object",
                "properties": {
                    "accion": {"type": "string", "enum": ["subir", "bajar", "fijar", "mutear"], "description": "QuÃ© hacer con el volumen"},
                    "valor": {"type": "integer", "description": "Solo para fijar: porcentaje 0-100"},
                },
                "required": ["accion"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "controlar_media",
            "description": "Controla la reproducciÃ³n multimedia (mÃºsica o vÃ­deo) EN SEGUNDO PLANO sin robar el foco: pausa, reanuda, siguiente o anterior canciÃ³n. Ej: 'siguiente canciÃ³n', 'pausa la mÃºsica', 'pon la mÃºsica'.",
            "parameters": {
                "type": "object",
                "properties": {"accion": {"type": "string", "description": "siguiente / anterior / pausa / reanudar / play_pausa"}},
                "required": ["accion"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hacer_scroll",
            "description": "Hace scroll con el ratÃ³n (arriba o abajo).",
            "parameters": {
                "type": "object",
                "properties": {
                    "direccion": {"type": "string", "enum": ["arriba", "abajo"], "default": "abajo"},
                    "cantidad": {"type": "integer", "description": "Magnitud del scroll", "default": 500},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escribir_texto",
            "description": "Escribe texto en el campo o ventana enfocado y pulsa Enter.",
            "parameters": {
                "type": "object",
                "properties": {
                    "texto": {"type": "string", "description": "Texto a escribir"}
                },
                "required": ["texto"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "controlar_navegador",
            "description": "Controla el navegador: nueva pestaÃ±a, cerrar pestaÃ±a, siguiente, anterior, pantalla completa, minimizar.",
            "parameters": {
                "type": "object",
                "properties": {"accion": {"type": "string", "enum": ["nueva pestaÃ±a", "cerrar pestaÃ±a", "siguiente pestaÃ±a", "anterior pestaÃ±a", "pantalla completa", "minimizar"]}},
                "required": ["accion"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "abrir_proyecto_android",
            "description": "Abre un proyecto de Android Studio desde la carpeta de proyectos.",
            "parameters": {
                "type": "object",
                "properties": {"proyecto": {"type": "string", "description": "Nombre parcial del proyecto"}},
                "required": ["proyecto"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clicar_en_la_pantalla",
            "description": "Hace clic en un elemento de la pantalla buscÃ¡ndolo por su texto visible (botones, iconos, vÃ­deos). Para clicar 'el tercer/segundo/primer vÃ­deo' pasa el ORDINAL COMPLETO en elemento (ej: 'tercer video', 'segundo video'), sin omitirlo: admite 'video/vÃ­deo', 'tercer', 'segundo', etc.",
            "parameters": {
                "type": "object",
                "properties": {"elemento": {"type": "string", "description": "Texto del elemento; conservar el ordinal si lo hay, ej: 'tercer video', 'vÃ­deo', 'aceptar', 'tareas'"}},
                "required": ["elemento"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clicar_elemento_ui",
            "description": "Hace clic en un elemento de la interfaz por su nombre accesible de Windows (UIAutomation). MÃ¡s fiable que OCR.",
            "parameters": {
                "type": "object",
                "properties": {"elemento": {"type": "string", "description": "Nombre o texto del elemento y su ventana"}},
                "required": ["elemento"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "listar_ventanas",
            "description": "Devuelve las ventanas abiertas del PC (tÃ­tulo, posiciÃ³n y tamaÃ±o).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "leer_pantalla",
            "description": "Devuelve los elementos interactivos (botones, campos, ventanas) de la pantalla con sus coordenadas.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "guardar_recuerdo",
            "description": "Guarda un dato o preferencia del usuario en la memoria permanente para recordarla en el futuro.",
            "parameters": {
                "type": "object",
                "properties": {"texto": {"type": "string", "description": "El recuerdo en una frase completa en espaÃ±ol"}},
                "required": ["texto"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analizar_pantalla",
            "description": "Analiza lo que hay visible en la pantalla y lo describe (ventanas y elementos accesibles).",
            "parameters": {
                "type": "object",
                "properties": {"pregunta": {"type": "string", "description": "QuÃ© quiere saber el usuario"}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "dormir",
            "description": "Fuecoco se mantiene en espera silenciosa (apaga la conversaciÃ³n).",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ver_siguiente_episodio",
            "description": "Abre la serie en Stremio y reproduce el siguiente episodio que toca ver (recuerda el progreso guardado y lo actualiza). Ej: 'abre <serie> y dale al siguiente que me toca'.",
            "parameters": {
                "type": "object",
                "properties": {"serie": {"type": "string", "description": "Nombre de la serie"}},
                "required": ["serie"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "guardar_episodio",
            "description": "Anota cuÃ¡ntos episodios se ha visto de una serie, para saber cuÃ¡l es el siguiente. Ãšsalo cuando el usuario diga que se ha visto o estÃ¡ en un episodio.",
            "parameters": {
                "type": "object",
                "properties": {
                    "serie": {"type": "string", "description": "Nombre de la serie"},
                    "temporada": {"type": "integer", "description": "Temporada (1 por defecto)"},
                    "episodio": {"type": "integer", "description": "Ãšltimo episodio visto"},
                },
                "required": ["serie", "episodio"],
            },
        },
    },
]

_FUNCS = {
    "poner_video": _poner_video_youtube,
    "poner_musica": _poner_musica,
    "abrir_url": _abrir_url,
    "lanzar_app": _lanzar_app,
    "buscar_en_internet": _buscar_en_internet,
    "buscar_archivo_local": _buscar_archivo_local,
    "abrir_carpeta_o_disco": _abrir_carpeta,
    "ejecutar_comando_powershell": _comando_powershell,
    "controlar_volumen": _controlar_volumen,
    "controlar_media": _controlar_media,
    "hacer_scroll": _scroll,
    "escribir_texto": _escribir_texto,
    "controlar_navegador": _navegador,
    "abrir_proyecto_android": _proyecto_android,
    "clicar_en_la_pantalla": _clic_pantalla,
    "clicar_elemento_ui": _clic_ui,
    "listar_ventanas": _listar_ventanas,
    "leer_pantalla": _leer_pantalla,
    "guardar_recuerdo": _guardar_recuerdo,
    "analizar_pantalla": _analizar_pantalla,
    "dormir": _dormir,
    "ver_siguiente_episodio": _ver_siguiente_episodio,
    "guardar_episodio": _guardar_episodio,
}

TEMA = {
    "poner_video": "gaming",
    "poner_musica": "Feliz",
    "abrir_url": "gaming",
    "lanzar_app": "gaming",
    "buscar_en_internet": "gaming",
    "buscar_archivo_local": "gaming",
    "abrir_carpeta_o_disco": "gaming",
    "ejecutar_comando_powershell": "gaming",
    "controlar_volumen": "Feliz",
    "controlar_media": "Feliz",
    "hacer_scroll": "Feliz",
    "escribir_texto": "gaming",
    "controlar_navegador": "gaming",
    "abrir_proyecto_android": "gaming",
    "clicar_en_la_pantalla": "gaming",
    "clicar_elemento_ui": "gaming",
    "listar_ventanas": "idle",
    "leer_pantalla": "idle",
    "guardar_recuerdo": "idle",
    "analizar_pantalla": "geografia",
    "dormir": "idle",
    "ver_siguiente_episodio": "gaming",
    "guardar_episodio": "idle",
}


TOOLS = _TOOLS


def ejecutar(nombre, args):
    func = _FUNCS.get(nombre)
    if not func:
        return f"[Herramienta desconocida: {nombre}]"
    try:
        return str(func(args or {}))
    except Exception as e:
        return f"[Error ejecutando {nombre}]: {e}"
