import os
import subprocess
import sys

# Ruta de la carpeta del proyecto: se calcula sola, no hay rutas personales.
CARPETA = os.path.dirname(os.path.abspath(__file__))

# pythonw.exe (sin ventana) junto al intérprete de Python que ejecuta este script.
PYTHONW = os.path.join(os.path.dirname(sys.executable), "pythonw.exe") \
    if sys.executable and "python" in os.path.basename(sys.executable).lower() \
    else "pythonw.exe"

os.chdir(CARPETA)
subprocess.Popen([PYTHONW, "main.py"], creationflags=0x08000000)