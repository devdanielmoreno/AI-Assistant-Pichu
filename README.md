
# Asistente IA Pichu

<p align="center">
  <i>Tu mascota de escritorio con voz para Windows</i>
</p>

---
<p align="center">
  <img src="https://i.pinimg.com/originals/ef/8d/85/ef8d85ee15ec7a364874d455c553322a.gif" alt="Pichu animado" width="360">
</p>

Asistente de escritorio con voz para Windows, hecho con **PyQt5**, que controla
apps reales (Stremio, Zen Browser, mpv, Android Studio) y un **modelo Live2D**
(conejito) al que le hablas por micrófono y te responde.

Por defecto se comporta como una "mascota" agachada en la esquina de la
pantalla, con un widget flotante de música y un chat silencioso por voz. Todo
corre en local: la generación de respuestas se hace a través de un **ModelRelay**
(en el puerto 7352) que tú mismo montas; Pichu solo es el "manos".

> **Aviso**: la voz de IlloJuan (RVC) y el modelo whisper NO vienen en el
> repositorio. Son opcionales; sin ellos Pichu funciona con Edge-TTS.

---

## Qué hace

- **Voz siempre activa** (`speech_recognition`): "oye Pichu, ponme una canción
  de aespa", "abre el episodio que me toca de Arcane", "busca un video de
  Hollow Knight", etc.
- **Reproduce música en segundo plano** con `mpv` + `yt-dlp` + `ytmusicapi`
  (sin navegador, sin foco, sin ventanas). Si el motor falla **no** te abre
  pestañas a lo loco: te lo dice y lo reintenta.
- **Stremio**: abre series/películas y reproduce el siguiente episodio de tus
  series por OCR/visión, en pantalla completa y a pantalla completa.
- **Zen Browser por WebDriver (BiDi/geckodriver)** para YouTube y búsquedas,
  abriendo solo instancias ya existentes (nunca abre una nueva).
- **Volumen por proceso** con WASAPI (pycaw): rueda del ratón sobre Pichu o el
  widget para subir/bajar SOLO el volumen de la música, jamás el del sistema.
- **Widget de música** arrastrable con fondos del *Cozy UI Pack*.
- **Memoria persistente** (SQLite) + patrones de hábitos + telemetría local.

---

## Qué necesitas para que funcione

### 1. Python y dependencias

Windows + **Python 3.11** (64 bits). Instala las dependencias:

```bash
pip install PyQt5 PyQtWebEngine pyautogui pyperclip pywin32 comtypes \
            pycaw pygame edge-tts openai soundfile torch numpy \
            speechrecognition Pillow psutil ytmusicapi yt-dlp
```

### 2. Binarios (imprescindibles para cada módulo)

| Ruta en el proyecto | De qué es | Dónde conseguirlo |
|---|---|---|
| `bin/mpv/mpv.exe` | Motor de reproducción ligera (mpv) | https://mpv.io/ (descomprimir solo el `.exe` en `bin/mpv/`) |
| `geckodriver/geckodriver.exe` | Controlar Zen/YouTube en 2º plano | https://github.com/mozilla/geckodriver/releases |
| `assets/Pichu/` | Modelo Live2D del conejito | Cualquier `.model3.json` de Live2D (píxel: el bun de Pichu) |
| `UI_Pack/` | Imágenes del widget y menús | *Cozy UI Pack* (o sustituye por tu pack) |

### 3. El "cerebro" (ModelRelay) en `127.0.0.1:7352`

`brain.py` apunta a `http://127.0.0.1:7352/v1` con `api_key="dummy-key"`.
Necesitas un servidor OpenAI-compatible en ese puerto (por ejemplo un proxy a
una API local o en la nube). Sin él, los comandos "inteligentes" responden
"Tengo un problema de conexión con mi cerebro". El fastpath local (música,
volumen, Stremio, etc.) NO necesita el ModelRelay.

### 4. Aplicaciones externas que controla

- **Zen Browser** (`zen.exe`) para YouTube Music y búsquedas.
- **Stremio** (`stremio-shell-ng.exe`) para series y películas.
- **Android Studio** opcional (comando "abre el proyecto X en Android Studio").

---

## Cómo ejecutarlo

```bash
python main.py        # con ventana de consola (recomendado para depurar)
```

O si prefieres sin ventana:

```bash
python lanzador.py    # usa pythonw.exe y se queda en segundo plano
```

Pichu arranca oculto, como mascota en la esquina. Interacciones:

- **Clic en Pichu** (con el cuerpo): detienes la voz y la acción en curso.
- **Arrástralo** pulsando sobre el cuerpo para moverlo (arriba/por cualquier
  pantalla); la posición se guarda en `pichu_pos.json`.
- **Rueda del ratón** sobre Pichu: volumen de la to**musa** (por proceso, no
  maestro).
- **Clic derecho** en Pichu: menú (expresión, mute, escribir un comando,
  ocultar, salir).
- **Widget de música**: aparece mientras suena algo; arrástralo, pausa,
  siguiente, volumen y cerrar.

---

## Estructura

```
actions.py        Herramientas de escritorio (Windows apps, URLs, carpetas...)
audio_control.py  Volumen por proceso con WASAPI (pycaw)
brain.py          Fastpath + agente LLM (ModelRelay) + TTS de respuesta
context.py        Prompt de contexto (memoria + telemetría)
hammer.py         Helpers de ventanas de Windows (win32)
lanzador.py       Arranca sin ventana con pythonw.exe
main.py           Entrada: worker de micrófono + GUI + tray
memory.py         Memoria persistente (SQLite)
musica_widget.py  Widget flotante de música (arrastrable, UI Pack)
mute.py           Estado de "no escuchar"
patterns.py       Patrones de hábitos a partir de la memoria
reproduccion.py   Motor mpv + yt-dlp (música en 2º plano) + widget de estado
stop.py           Bandera global de "parar acción" (clic en Pichu)
telemetry.py      Observador silencioso de actividad (SQLite local)
tools.py          Herramientas del agente LLM (Stremio, Zen, música, OCR...)
ui.py             Avatar Live2D (QtWebEngine + PixiLive2D) + tray + chat
vision.py         OCR y clics sobre la pantalla (Tesseract/Windows OCR)
zen_bidi.py       Driver WebDriver (BiDi + geckodriver) hacia Zen
```

---

## Muestra del modelo

El conejito es un modelo Live2D con expresiones que cambian según lo que hace
(hablando, sorprendido, enfadado, feliz...). Unas muestras:

| Normal | Feliz | Enfadado |
|---|---|---|
| ![Normal](imgrapido/normal.png) | ![Feliz](imgrapido/feliz.png) | ![Enfadado](imgrapido/enfadado.png) |

---

## Notas

- El código asume Windows (win32, WASAPI, rutas de usuario por `USERNAME`).
- Nada de esto sube datos a Internet: todo queda en tu PC (memoria, telemetría,
  audio de tus propias apps).
