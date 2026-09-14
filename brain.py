import json
import asyncio
import os
import re
import threading
import pygame
import edge_tts
from openai import OpenAI
import soundfile as sf
import torch
try:
    torch.set_num_threads(os.cpu_count() or 4)
except Exception:
    pass
try:
    _orig_torch_load = torch.load
    torch.load = lambda *args, **kwargs: _orig_torch_load(*args, **{**kwargs, "weights_only": False})
except Exception:
    pass

import tools
import context
from actions import controlar_volumen, multimedia_play_pause, hacer_scroll, abrir_stremio, _limpiar_busqueda_stremio, controlar_musica

try:
    pygame.mixer.init()
except Exception as e:
    print(f"[Audio Skip]: mixer no disponible: {e}")

VOZ_NEURAL = "es-ES-ElviraNeural"
VOLUMEN_VOZ = 0.50
# RVC (clon de voz IlloJuan) solo puede ir en CPU con tu AMD -> tarda mucho.
# False = respuesta inmediata con la voz dulce de Elvira.
RVC_HABILITADO = False
# Si algún día lo activas, solo convierte frases largas (las cortas van al instante).
RVC_MIN_CHARS = 90

# --- LOCALIZADOR INTELIGENTE DEL MODELO RVC ---
def _localizar_modelo_rvc():
    """Busca cualquier archivo .pth y .index en la carpeta rvc/ para evitar fallos de nombre."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    carpeta_rvc = os.path.join(base_dir, "rvc")
    
    if not os.path.exists(carpeta_rvc):
        print(f"[RVC]: La carpeta '{carpeta_rvc}' no existe. Créala y pon los archivos dentro.")
        return None, None

    pth_file = None
    index_file = None
    for f in os.listdir(carpeta_rvc):
        if f.lower().endswith(".pth") and not pth_file:
            pth_file = os.path.join(carpeta_rvc, f)
        elif f.lower().endswith(".index") and not index_file:
            index_file = os.path.join(carpeta_rvc, f)

    return pth_file, index_file

_rvc_instance = None
_rvc_cargado_exitoso = False

def _obtener_rvc():
    global _rvc_instance, _rvc_cargado_exitoso
    if not RVC_HABILITADO:
        return None
    if _rvc_instance is None and not _rvc_cargado_exitoso:
        pth_path, index_path = _localizar_modelo_rvc()
        if not pth_path:
            print("[RVC]: No se encontró ningún archivo .pth en la carpeta 'rvc'.")
            _rvc_cargado_exitoso = True  # Para no reintentar en cada frase
            return None

        try:
            from rvc_python.infer import RVCInference
            print(f"[RVC]: Cargando voz de IlloJuan desde '{os.path.basename(pth_path)}' en CPU...")
            _rvc_instance = RVCInference(device="cpu")
            _rvc_instance.load_model(pth_path)
            
            # Ajustes: f0_up_key=0 (mismo tono masculino), f0_method='pm' para velocidad en CPU
            _rvc_instance.set_params(f0_up_key=0, f0_method="pm", index_rate=0.0)
            print("[RVC]: ¡Voz de IlloJuan lista y cargada en memoria!")
            _rvc_cargado_exitoso = True
        except Exception as e:
            print(f"[RVC Error al cargar]: {e}. Se utilizará la voz de respaldo.")
            _rvc_instance = None
            _rvc_cargado_exitoso = True
            
    return _rvc_instance

client = OpenAI(
    base_url="http://127.0.0.1:7352/v1",
    api_key="dummy-key"
)

MAX_VUELTAS = 6
_evento_interrupcion = threading.Event()
_reproduciendo_audio = False

def esta_hablando() -> bool:
    global _reproduciendo_audio
    return _reproduciendo_audio

def detener_hablar():
    global _reproduciendo_audio
    _evento_interrupcion.set()
    try:
        if pygame.mixer.get_init():
            pygame.mixer.music.stop()
            pygame.mixer.music.unload()
    except Exception:
        pass
    _reproduciendo_audio = False

# --- FAST-PATH ---
def _panel_texto():
    import telemetry
    import reproduccion
    import mute
    st = telemetry.estado_actual()
    lineas = []
    try:
        lineas.append("· " + reproduccion.texto_panel())
    except Exception as e:
        print(f"[panel musica]: {e}")
        lineas.append("· Música: nada sonando")
    micro = "silenciado (bandeja)" if mute.esta_activo() else "escuchando"
    lineas.append(f"· Micro de Pichu: {micro}")
    lineas.append(f"· App enfocada: {st.get('app_activa') or 'ninguna'}")
    lineas.append(f"· {st.get('dia_texto')}, {st.get('hora_texto')}")
    return "\n".join(lineas), "panel"

def _sentido_volumen(t):
    """+1 = subir, -1 = bajar, 0 = sin dirección clara (o ambigua).
    Acepta las formas coloquiales: sube/subir/súbeme/bájame/bájate/un poco/más alta..."""
    sub = any(x in t for x in ("sube", "subelo", "súbela", "subir", "subeme", "súbeme",
                               "subite", "súbete", "subirme", "subeme la",
                               "mas alta", "más alta", "mas alto", "más alto",
                               "mas fuerte", "más fuerte", "subidita", "subida",
                               "arriba la musica", "arriba la música"))
    baj = any(x in t for x in ("baja", "bajalo", "bájala", "bajar", "bajalo abajo",
                               "bajame", "bájame", "bajate", "bájate", "bajarme",
                               "mas baja", "más baja", "mas bajo", "más bajo",
                               "mas bajita", "más bajita", "mas bajito", "más bajito",
                               "bajita", "bajito", "abajo la musica", "abajo la música",
                               "menos volumen", "bajada"))
    if sub and not baj:
        return 1
    if baj and not sub:
        return -1
    return 0

def _pide_volumen_musica(t):
    """¿La orden pide subir/bajar el volumen DE LA MÚSICA (y no de otra app)?
    Nunca debe colisionar con las búsquedas de canciones ('canción de X')."""
    if "musica" not in t and "música" not in t:
        return False
    if _sentido_volumen(t) == 0:
        return False
    if re.search(r"\b(?:música|musica|cancion|canción)\s+(?:de|que)\s+(?!fondo\b)\S+", t):
        return False
    if "volumen" in t:
        return True
    return len(t.split()) <= 9

def _fastpath(t: str):
    # Panel de estado: "pichu, panel / estado / cómo va / qué tal va / resumen del día"
    if (re.search(r"\b(panel|estado)\b", t) or
            any(x in t for x in ("como va", "como vamos", "como te va", "que tal va",
                                 "que tal vamos", "resumen del dia", "resumen de dia",
                                 "dame el estado", "dame el panel"))):
        return _panel_texto()
    # Volumen POR APP (YouTube Music = zen): "volumen de youtube al 30",
    # "sube el volumen de stremio"
    m_yapp = re.search(r"volumen\s+(?:de|en|para)\s+(?:la\s+|el\s+|un\s+|una\s+)?([a-záéíóúñ]{3,})\s+(?:a|al)\s*(\d+)", t)
    if m_yapp:
        import audio_control
        if audio_control.DISPONIBLE:
            res = audio_control.volumen_proceso(m_yapp.group(1), int(m_yapp.group(2)))
            if res is not None:
                return f"Volumen de {m_yapp.group(1)} al {res}%.", "Feliz"
            return f"No veo audio activo de {m_yapp.group(1)}.", "Confundida"
    m_yup = re.search(r"(?:sube|baja|subir|bajar)\s+el\s+volumen\s+(?:de|en|para)\s+(?:la\s+|el\s+|un\s+|una\s+)?([a-záéíóúñ]{3,})", t)
    if m_yup:
        import audio_control
        if audio_control.DISPONIBLE:
            app = m_yup.group(1)
            base = audio_control.volumen_proceso_leer(app)
            if base is not None:
                paso = 10 if "sub" in m_yup.group(0) else -10
                res = audio_control.volumen_proceso(app, max(0, min(100, base + paso)))
                return f"Volumen de {app} al {res}%.", "Feliz"
            if app in ("musica", "música"):
                # No suena Pichu ni el Zen: se avisa sin tocar el volumen general.
                return ("No está sonando música de Pichu ni del Zen; dime una "
                        "canción y te la pongo. El volumen general lo dejo como está.", "Confundida")
            return f"No veo audio activo de {app}.", "Confundida"
    # VOLUMEN DE LA MÚSICA (siempre por app: mpv si suena Pichu, si no el Zen).
    # Nunca toca el maestro. Caza todas las variantes antes de que caigan en el
    # volumen general o en el scroll.
    if _pide_volumen_musica(t):
        sube = _sentido_volumen(t) > 0
        import audio_control
        if not audio_control.DISPONIBLE:
            return "No puedo tocar el volumen ahora.", "Confundida"
        base = audio_control.volumen_proceso_leer("musica")
        if base is None:
            # No suena Pichu ni el Zen: se avisa sin tocar el volumen general.
            return ("No está sonando música de Pichu ni del Zen; dime una "
                    "canción y te la pongo. El volumen general lo dejo como está.", "Confundida")
        nuevo = max(0, min(100, base + (10 if sube else -10)))
        res = audio_control.volumen_proceso("musica", nuevo)
        if res is None:
            return "No encuentro la sesión de audio de la música.", "Confundida"
        return f"Volumen de la música al {res}%.", "Feliz"
    if any(x in t for x in ["sube el volumen", "subir el volumen", "mas volumen", "más volumen"]):
        return controlar_volumen("subir", 10), "Feliz"
    if any(x in t for x in ["baja el volumen", "bajar el volumen", "menos volumen"]):
        return controlar_volumen("bajar", 10), "Feliz"
    match_pct = re.search(r"volumen al (\d+)", t)
    if match_pct:
        return controlar_volumen("fijar", int(match_pct.group(1))), "Feliz"
    if any(x in t for x in ["mutea", "silencia"]) and "música" not in t and "musica" not in t:
        return controlar_volumen("mutear"), "Feliz"

    # Música (YouTube Music) EN SEGUNDO PLANO: SIN robar el foco (importante si estás jugando).
    # 1) BÚSQUEDA: "ponme una canción de X", "música de X", "[artista] en música" -> poner_musica.
    #    OJO: va ANTES que el control. Si estuviera después, el verbo "pon" (de "pon la música")
    #    reanudaría lo que suena en lugar de buscar la canción pedida.
    m_escuchar = re.search(
        r"\b(?:una\s+)?(cancion|canción|musica|música|artista|grupo)\s+(?:de|que|por parte de)\s+"
        r"([a-z0-9ñáéíóúü\-'\. ]{2,60})$",
        t)
    obj = ""
    if m_escuchar:
        obj = re.sub(r"\s+en\s+youtube(?:\s+music)?\s*$", "", m_escuchar.group(2)).strip(" ,.;")
    else:
        # "busca el grupo X", "el artista X" (sin "de")
        m_nombre = re.search(
            r"\b(?:el\s+|un\s+)?(grupo|artista)\s+(?:de\s+|llamado\s+|llamad(o|a|os|as)\s+|a\s+)?"
            r"([a-z0-9ñáéíóúü\-'\. ]{2,40})$",
            t)
        if m_nombre:
            obj = re.sub(r"\s+en\s+youtube(?:\s+music)?\s*$", "", m_nombre.group(3)).strip(" ,.;")
    if obj and obj.split()[0] not in ("fondo", "otro", "otra", "un", "una", "el", "la",
                                      "los", "las", "de", "en", "ya", "ahora", "que", "algo"):
        return tools.ejecutar("poner_musica", {"busqueda": obj}), "Feliz"

    es_musica = any(k in t for k in ("youtube music", "la música", "la musica",
                                     "la cancion", "la canción", "otra cancion",
                                     "otra canción", "música", "musica"))
    if es_musica:
        if any(k in t for k in ("siguiente", "cambia", "cambio", "cambiame", "otra",
                                "salta", "next", "adelante", "sigueme")):
            return controlar_musica("siguiente"), "Feliz"
        if any(k in t for k in ("anterior", "atras", "atrás", "volver", "retroced")):
            return controlar_musica("anterior"), "Feliz"
        if any(k in t for k in ("pon", "reanuda", "sigue", "vuelve", "resume", "suena")):
            # "pon" solo llega aquí si antes no se detectó "música de X" (búsqueda).
            return controlar_musica("reanudar"), "Feliz"
        if any(k in t for k in ("para", "pausa", "quita", "saca", "calla", "deten", "silencia")):
            return controlar_musica("pausa"), "Feliz"

    if any(x in t for x in ["pausa", "play", "reanuda", "pon la musica", "para el video", "parar el video", "para la musica"]):
        return multimedia_play_pause(), "Feliz"
    if "scroll abajo" in t or re.search(r"\bbaja\b", t):
        return hacer_scroll("abajo"), "Feliz"
    if "scroll arriba" in t or re.search(r"\bsube\b", t):
        return hacer_scroll("arriba"), "Feliz"
    if "stremio" in t:
        # Quita muletillas de wake word por si llegaran del reconocimiento
        t = re.sub(r"^\s*(?:oye|hey|eh|ey|oiga|escucha|a ver|venga)\s+(?:pichu\b\s*)?", "", t, flags=re.I)
        # Quita las muletillas "abre stremio y", "en stremio" o "stremio" iniciales
        resto = re.sub(r"^abre\s+stremio(?:\s+y\s*)?", "", t, flags=re.I)
        resto = re.sub(r"\s+y\s+en\s+stremio\b", " ", resto, flags=re.I)
        resto = re.sub(r"\s+en\s+stremio\b", " ", resto, flags=re.I)
        if resto.lower().startswith("stremio "):
            resto = resto[8:]
        resto = resto.strip()

        serie = ""
        play = ""

        # "pon X y pon el primer episodio" -> separar serie de la suborden de reproducir
        m_play = re.search(r"\s+y\s+(pon|pone|ponme|reproduce|reproducir|dame|dale)(?:\s+(?:el\s+)?(?:primer|siguiente|proximo|siguient)\s+(?:episodio|capitulo))?", resto, flags=re.I)
        if m_play:
            serie = _limpiar_busqueda_stremio(resto[:m_play.start()])
            play = m_play.group(1)

        # "pon el primer episodio de X" (sin serie previa)
        if not serie:
            m_first = re.match(r"^(?:pon|pone|ponme|reproduce|reproducir|dame|dale)\s+(?:el\s+)?(?:primer|siguiente|proximo|siguient)\s+(?:episodio|capitulo)\s+(?:de\s+)?(.+)", resto, flags=re.I)
            if m_first and "episodio" in m_first.group(0):
                serie = _limpiar_busqueda_stremio(m_first.group(1))
                play = "pon"

        # "ponme el episodio que (me) toca de X" -> siguiente episodio
        if not serie:
            m_toca = re.search(r"(?:(?:pon|pone|ponme|reproduce|reproducir|dame|dale|ver)\s+)?(?:el\s+)?(?:episodio|cap[ií]tulo)\s+que\s+(?:me\s+)?toca(?:\s+ver)?\s+(?:de\s+)?(.+)", resto, flags=re.I)
            if m_toca:
                serie = _limpiar_busqueda_stremio(m_toca.group(1))
                play = "pon"

        # Caso general: solo el nombre de la serie
        if not serie:
            serie = _limpiar_busqueda_stremio(resto)

        if play and serie:
            return tools.ejecutar("ver_siguiente_episodio", {"serie": serie}), "Happy"
        if serie:
            return abrir_stremio(serie, pantalla_completa=True), "Happy"

        if any(x in t for x in ["abre stremio", "abrir stremio", "inicia stremio", "pon stremio"]) or t.strip().lower() == "stremio":
            return abrir_stremio("", pantalla_completa=True), "Happy"

    return None, None

def _agente(texto_usuario):
    messages = [
        {"role": "system", "content": context.construir_system_prompt(texto_usuario)},
        {"role": "user", "content": texto_usuario},
    ]
    tema = "gaming"
    acciones_ejecutadas = []
    modelo_actual = "auto-fastest"

    for _ in range(MAX_VUELTAS):
        try:
            respuesta = client.chat.completions.create(
                model=modelo_actual,
                messages=messages,
                tools=tools.TOOLS,
                tool_choice="auto",
                temperature=0.3,
            )
            if respuesta.model:
                modelo_actual = respuesta.model
            msg = respuesta.choices[0].message
        except Exception as e:
            print(f"[LLM]: {e}")
            if modelo_actual != "auto-fastest":
                modelo_actual = "auto-fastest"
                continue
            if acciones_ejecutadas:
                return "Listo, hecho.", tema
            return "Tengo un problema de conexión con mi cerebro.", "enfadado"

        if not msg.tool_calls:
            contenido = (msg.content or "Hecho.").strip()
            return contenido, tema

        assistant_msg = {
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [],
        }
        for tc in msg.tool_calls:
            tc_dict = {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.function.name, "arguments": tc.function.arguments},
            }
            extra = getattr(tc, "extra_content", None)
            if extra:
                tc_dict["extra_content"] = extra
            assistant_msg["tool_calls"].append(tc_dict)
        messages.append(assistant_msg)

        for tc in msg.tool_calls:
            nombre = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except Exception:
                args = {}
            resultado = tools.ejecutar(nombre, args)
            tema = tools.TEMA.get(nombre, tema)
            acciones_ejecutadas.append(nombre)
            print(f"[Herramienta] {nombre}({args}) -> {resultado}")
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": resultado})

    return ("Listo, hecho." if acciones_ejecutadas else "Listo."), tema

def procesar_comando(texto_usuario):
    import stop
    stop.reset()
    t = (texto_usuario or "").lower().strip()

    resp, tema = _fastpath(t)
    if resp:
        return resp, tema

    if any(x in t for x in ["calla", "parar de hablar", "silencio", "shh", "basta", "cállate"]):
        detener_hablar()
        return "Silencio.", "idle"

    return _agente(t)

async def _generar_audio_edge(texto, ruta_salida):
    t_limpio = re.sub(r"\[.*?\]|[*_#`]", "", texto).strip()
    comunicador = edge_tts.Communicate(t_limpio, VOZ_NEURAL, pitch="+8Hz", rate="+10%")
    await comunicador.save(ruta_salida)

def hablar(texto):
    global _reproduciendo_audio
    temp_mp3 = "temp_edge.mp3"
    temp_wav_in = "temp_in.wav"
    temp_audio = "voz_illojuan.wav"
    _evento_interrupcion.clear()

    try:
        # 1. Generar la pronunciación en español con Edge-TTS
        asyncio.run(_generar_audio_edge(texto, temp_mp3))
        if _evento_interrupcion.is_set() or not os.path.exists(temp_mp3) or os.path.getsize(temp_mp3) == 0:
            return

        ruta_reproducir = temp_mp3
        rvc = _obtener_rvc()

        # 2. Conversión a IlloJuan mediante RVC (solo si está activo y la frase es larga)
        if rvc is not None and len(texto.strip()) >= RVC_MIN_CHARS:
            try:
                # Pasar MP3 a WAV limpio usando soundfile (cero dependencias externas)
                audio_data, samplerate = sf.read(temp_mp3)
                sf.write(temp_wav_in, audio_data, samplerate)

                # Aplicar la voz clónica de IlloJuan
                rvc.infer_file(temp_wav_in, temp_audio)

                if os.path.exists(temp_audio) and os.path.getsize(temp_audio) > 0:
                    ruta_reproducir = temp_audio
            except Exception as e:
                print(f"[RVC Error durante la conversión]: {e}")

        # 3. Reproducción
        if not pygame.mixer.get_init():
            pygame.mixer.init()

        pygame.mixer.music.load(ruta_reproducir)
        pygame.mixer.music.set_volume(VOLUMEN_VOZ)
        pygame.mixer.music.play()
        _reproduciendo_audio = True

        while pygame.mixer.music.get_busy():
            if _evento_interrupcion.is_set():
                pygame.mixer.music.stop()
                break
            pygame.time.Clock().tick(30)

        pygame.mixer.music.unload()
    except Exception as e:
        print(f"[Audio]: {e}")
    finally:
        _reproduciendo_audio = False
        for f in [temp_mp3, temp_wav_in, temp_audio]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                except Exception:
                    pass