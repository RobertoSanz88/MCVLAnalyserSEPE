"""Punto de entrada del ejecutable: levanta uvicorn de forma programatica."""

import multiprocessing
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from config import get_puerto, puerto_libre

MAX_INTENTOS = 200  # ~60s
INTERVALO_S = 0.3


def _abrir_navegador_cuando_arranque(puerto: int) -> None:
    """Solo se usa en el .exe empaquetado (ver comprobacion de sys.frozen mas
    abajo): en dev, iniciar.bat ya abre el navegador el solo via
    esperar_y_abrir.bat + splash.py, no hace falta duplicarlo aqui. El .exe
    no tiene ningun .bat que haga eso, asi que se replica el mismo patron
    (sondear /health hasta que uvicorn responda de verdad, nunca abrir a
    ciegas con un tiempo fijo - ver esperar_y_abrir.bat)."""
    url_salud = f"http://127.0.0.1:{puerto}/health"
    for _ in range(MAX_INTENTOS):
        try:
            with urllib.request.urlopen(url_salud, timeout=0.5) as resp:
                if resp.status == 200:
                    webbrowser.open(f"http://127.0.0.1:{puerto}/")
                    return
        except OSError:
            pass
        time.sleep(INTERVALO_S)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    if "--elegir-carpeta" in sys.argv:
        # Subproceso del selector de carpeta (lo lanza config.elegir_carpeta_dialogo): sin importar app.main.
        from config import _dialogo_carpeta

        pos = sys.argv.index("--elegir-carpeta") + 1
        titulo = sys.argv[pos] if len(sys.argv) > pos else "Selecciona una carpeta"
        sys.stdout.reconfigure(encoding="utf-8")
        print(_dialogo_carpeta(titulo))
        sys.exit(0)
    # puerto_libre: el puerto PREFERIDO (8000 salvo override en config.json)
    # puede estar ocupado de forma permanente por otro programa (visto en el
    # PC corporativo del usuario, un servicio de firma digital) - antes esto
    # exigia editar config.json a mano. Se aplica SIEMPRE (dev y .exe
    # empaquetado, no solo el .exe como antes 2026-08-28): en modo dev,
    # iniciar.bat resuelve este mismo valor por su cuenta (con la misma
    # funcion) para pasarselo a splash.py/esperar_y_abrir.bat - ver ese
    # fichero. La pequenya ventana de carrera entre esa resolucion y esta (dos
    # llamadas independientes a puerto_libre) es la misma que ya asumia
    # puerto_libre() en el .exe, irrelevante en la practica (ver su docstring).
    puerto = puerto_libre(get_puerto())
    if getattr(sys, "frozen", False):
        # Lanzar splash.exe AQUI, antes de "from app.main import app" (mas
        # abajo) - esa importacion arrastra pandas/duckdb/fastapi y tarda
        # varios segundos (mas con antivirus corporativo escaneando cada
        # import, ver CLAUDE.md). Si el splash se lanzara despues de esa
        # importacion, o compartiera el mismo proceso/exe, no apareceria
        # hasta que las dependencias pesadas ya hubieran cargado - justo
        # cuando menos falta hace (visto en pruebas reales). splash.exe es
        # un ejecutable propio, ligero (solo tkinter/urllib/config, ver
        # build/mcvlanalyser.spec), igual que "pythonw splash.py" en dev:
        # arranca casi al instante y hace su propio sondeo de /health hasta
        # cerrarse solo, sin que este proceso tenga que esperarlo.
        ruta_splash = Path(sys.executable).parent / "splash.exe"
        subprocess.Popen([str(ruta_splash), str(puerto)])
        threading.Thread(target=_abrir_navegador_cuando_arranque, args=(puerto,), daemon=True).start()

    import uvicorn

    from app.main import app

    uvicorn.run(app, host="127.0.0.1", port=puerto)
