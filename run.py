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
    puerto = get_puerto()
    if getattr(sys, "frozen", False):
        # puerto_libre: el puerto PREFERIDO (8000 salvo override en
        # config.json) puede estar ocupado de forma permanente por otro
        # programa (visto en el PC corporativo del usuario, un servicio de
        # firma digital) - antes esto exigia editar config.json a mano, algo
        # que no se le puede pedir a cada companyero que solo usa el .exe. Se
        # resuelve aqui, ANTES de lanzar splash.exe, para pasarle el puerto
        # real por argv (ver splash.py) - si splash siguiera sondeando el
        # puerto preferido original y uvicorn arranca en otro, splash nunca
        # veria el servidor listo y se cerraria solo a los 60s sin que la
        # app llegase a abrirse (fallo real, visto en pruebas: el .exe se
        # quedaba con la ventana de consola sin hacer nada aparente).
        puerto = puerto_libre(puerto)

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
