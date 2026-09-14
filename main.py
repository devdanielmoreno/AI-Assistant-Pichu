import sys
import os
import subprocess
import torch
import time
import signal
import unicodedata
import speech_recognition as sr
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QThread, pyqtSignal, QTimer

from ui import AvatarApp
from brain import procesar_comando, hablar, detener_hablar, esta_hablando
from memory import init as init_memoria
from patterns import run_diario
from telemetry import TelemetryThread
import mute
import stop

def quitar_tildes(texto: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn')

proceso_relay = None

def apagar_asistente():
    global proceso_relay
    detener_hablar()
    try:
        import reproduccion
        reproduccion.detener()
    except Exception:
        pass
    try:
        import zen_bidi
        z = zen_bidi.obtener()
        if z.esta_abierto():
            z.cerrar()
    except Exception:
        pass
    
    # Matamos el proceso de modelrelay y cualquier subproceso para liberar el puerto 7352
    if proceso_relay:
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proceso_relay.pid)],
                capture_output=True,
                creationflags=0x08000000
            )
        except Exception:
            pass

    print("\n[Pichu apagado] Recursos liberados.")
    os._exit(0)
class AsistenteWorker(QThread):
    cambiar_estado = pyqtSignal(bool, str)
    cambiar_visibilidad = pyqtSignal(bool)
    cambiar_tema = pyqtSignal(str)
    mostrar_panel = pyqtSignal(str)

    def run(self):
        recognizer = sr.Recognizer()
        mic = sr.Microphone()

        # CLAVE: 1.8 segundos te permite pensar mientras hablas sin que te corte la frase
        recognizer.pause_threshold = 1.8
        recognizer.non_speaking_duration = 0.6
        recognizer.dynamic_energy_threshold = True

        print("\n=======================================================")
        print(" Pichu JARVIS Activo (Conversación Natural)")
        print(" - Puedes hacer pausas al hablar, ya no te cortará.")
        print(" - Modo Activo 15s tras la primera orden.")
        print("=======================================================")

        tiempo_despierto_hasta = 0
        esta_despierto = False

        while True:
            if mute.esta_activo():
                time.sleep(0.5)
                continue
            with mic as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.2)
                try:
                    audio = recognizer.listen(source, timeout=4, phrase_time_limit=15)
                    if mute.esta_activo():
                        continue
                    texto = recognizer.recognize_google(audio, language="es-ES")
                    if not texto: continue
                    if mute.esta_activo():
                        continue

                    texto_crudo = texto.lower()
                    texto_limpio = quitar_tildes(texto_crudo).replace(",", "").replace(".", "").replace("¡", "").replace("!", "")

                    palabras_clave = ["oye pichu", "oye pixu", "pichu", "pi chu", "picu"]
                    dijo_palabra = any(p in texto_limpio for p in palabras_clave)

                    ahora = time.time()
                    
                    if ahora > tiempo_despierto_hasta and esta_despierto:
                        esta_despierto = False
                        self.cambiar_visibilidad.emit(False)

                    if not dijo_palabra and not esta_despierto:
                        continue

                    esta_despierto = True
                    tiempo_despierto_hasta = time.time() + 15.0
                    self.cambiar_visibilidad.emit(True)

                    if esta_hablando():
                        if dijo_palabra or any(x in texto_limpio for x in ["calla", "para", "shh", "silencio", "basta"]):
                            detener_hablar()
                            self.cambiar_estado.emit(False, "")

                    orden = texto_limpio
                    for p in palabras_clave:
                        if p in orden:
                            orden = orden.split(p, 1)[-1].strip()
                            break

                    if any(x in orden for x in ["duermete", "apagate", "descansa", "cierra el programa", "cierrate"]):
                        self.cambiar_tema.emit("idle")
                        detener_hablar()
                        hablar("Hasta luego.")
                        esta_despierto = False
                        self.cambiar_visibilidad.emit(False)
                        tiempo_despierto_hasta = 0
                        continue

                    if not orden and dijo_palabra:
                        detener_hablar()
                        self.cambiar_tema.emit("idle")
                        self.cambiar_estado.emit(True, "Dime.")
                        hablar("Dime.")
                        self.cambiar_estado.emit(False, "")
                        continue

                    if any(x in orden for x in ["no me escuches", "no me oigas", "deja de escuchar",
                                                "deja de oir", "deja de oirme", "quedate mudo",
                                                "quedate callado", "muteate", "mute"]):
                        mute.activar()
                        detener_hablar()
                        hablar("Vale, dejo de escuchar. Para reactivarme, toca la bandeja.")
                        esta_despierto = False
                        self.cambiar_visibilidad.emit(False)
                        tiempo_despierto_hasta = 0
                        continue
                    if any(x in orden for x in ["escuchame", "vuelve a escuchar", "reactivate",
                                                "reactivame", "desmutear", "mirame"]):
                        mute.desactivar()
                        hablar("Aqui estoy, escuchando.")
                        continue

                    print(f"\n[Usuario]: {texto_crudo}")
                    detener_hablar()
                    stop.reset()
                    
                    self.cambiar_tema.emit("geografia")

                    respuesta, tema = procesar_comando(orden)

                    if tema == "panel":
                        # El panel se ve en la burbuja, no se lee en voz alta
                        self.cambiar_tema.emit("idle")
                        self.cambiar_estado.emit(False, "")
                        self.mostrar_panel.emit(respuesta)
                        self.cambiar_visibilidad.emit(True)
                        continue

                    self.cambiar_tema.emit(tema)
                    self.cambiar_estado.emit(True, respuesta)
                    hablar(respuesta)
                    self.cambiar_estado.emit(False, "")

                    self.cambiar_tema.emit("idle")
                    tiempo_despierto_hasta = time.time() + 15.0 

                except (sr.WaitTimeoutError, sr.UnknownValueError):
                    ahora = time.time()
                    if ahora > tiempo_despierto_hasta and esta_despierto:
                        esta_despierto = False
                        self.cambiar_visibilidad.emit(False)
                except Exception as e:
                    print(f"Aviso: {e}")
def modo_prueba_texto():
    """Permite probar comandos por texto sin micrófono."""
    print("\n=== MODO PRUEBA POR TEXTO ===")
    print("Escribe comandos (teclea 'salir' para terminar).")
    print("Atajos: 'historial' -> últimas órdenes; 'salir'/'exit' -> termina.")
    print("Ejemplos: 'abre zen browser', 'volumen de youtube al 30', 'dime la hora'")
    historial = []

    while True:
        try:
            comando = input("\n> ").strip()
            if comando.lower() in ['salir', 'exit', 'quit']:
                break
            if comando.lower() in ['historial', 'historia']:
                if historial:
                    print("\n  -- Historial --")
                    for prev in historial[-8:]:
                        print(f"  · {prev}")
                else:
                    print("\n  (todavía no hay órdenes en esta sesión)")
                continue
            if comando.lower() in ['panel', 'estado']:
                respuesta_texto, tema = procesar_comando('panel')
                print(respuesta_texto)
                continue
            if not comando:
                continue

            print(f"\n\u2501\u2501 [Usuario]: {comando} \u2501\u2501")
            respuesta, tema = procesar_comando(comando)
            print(f"[Pichu]: {respuesta}  (tema: {tema})")
            historial.append(f"{comando}  ->  {respuesta}")
            if len(historial) > 200:
                historial = historial[-100:]

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"Error: {e}")
class SilentTextWorker(QThread):
    terminado = pyqtSignal(str, str)

    def __init__(self, texto):
        super().__init__()
        self.texto = texto

    def run(self):
        respuesta, tema = procesar_comando(self.texto)
        self.terminado.emit(respuesta, tema)

text_worker = None

def procesar_orden_silenciosa(texto_comando):
    global text_worker
    if not texto_comando.strip():
        return
    detener_hablar()
    stop.reset()

    # Hilo en segundo plano: Pichu NO se congelará mientras la IA piensa
    text_worker = SilentTextWorker(texto_comando)

    def al_terminar(respuesta, tema):
        ventana.set_theme(tema)
        ventana.mostrar_respuesta_silenciosa(respuesta)
        QTimer.singleShot(4000, lambda: ventana.set_theme("idle"))

    text_worker.terminado.connect(al_terminar)
    text_worker.start()
if __name__ == "__main__":
    import subprocess

    # 1. ARRANCAR MODELRELAY EN SEGUNDO PLANO
    try:
        CREATE_NO_WINDOW = 0x08000000
        proceso_relay = subprocess.Popen(
            "modelrelay",
            shell=True,
            creationflags=CREATE_NO_WINDOW
        )
        print("[ModelRelay]: Servidor arrancado en segundo plano (puerto 7352).")
    except Exception as e:
        print(f"[ModelRelay Error]: No se pudo arrancar: {e}")

    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        init_memoria()
        patrones = run_diario()
        print(f"[Memoria]: {patrones['reglas']} reglas nuevas generadas.")
        modo_prueba_texto()
        sys.exit(0)
    signal.signal(signal.SIGINT, lambda *args: apagar_asistente())
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  
    app.aboutToQuit.connect(apagar_asistente)
    ventana = AvatarApp()
    ventana.hide()
    from musica_widget import MusicaWidget
    ventana.hud_musica = MusicaWidget()

    timer_sigint = QTimer()
    timer_sigint.timeout.connect(lambda: None)
    timer_sigint.start(250)

    worker = AsistenteWorker()
    worker.cambiar_estado.connect(ventana.set_speaking)
    worker.cambiar_visibilidad.connect(ventana.set_visibility)
    worker.cambiar_tema.connect(ventana.set_theme)
    worker.mostrar_panel.connect(ventana.mostrar_panel)
    
    # NUEVO: Conectar clic en la interfaz a detener audio Y frenar la acción
    def _clic_freno():
        detener_hablar()
        stop.parar()

    ventana.clicked.connect(_clic_freno)
    ventana.comando_texto.connect(procesar_orden_silenciosa)
    worker.start()
    init_memoria()
    patrones = run_diario()
    print(f"[Memoria]: {patrones['reglas']} reglas nuevas generadas de {patrones['eventos']} eventos registrados.")
    telemetria = TelemetryThread()
    telemetria.start()
    print("[Memoria]: Motor de telemetría activo (observador silencioso cada 60s).")

    sys.exit(app.exec_())