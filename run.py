"""Punto de entrada del ejecutable: levanta uvicorn de forma programatica."""

import multiprocessing
import sys
import threading
import time
import urllib.request
import webbrowser

import uvicorn

from app.main import app
from config import get_puerto

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
        threading.Thread(target=_abrir_navegador_cuando_arranque, args=(puerto,), daemon=True).start()
    uvicorn.run(app, host="127.0.0.1", port=puerto)
