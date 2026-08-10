"""Splash de arranque: ventana con el icono a tamano grande, centrada en
pantalla, mientras uvicorn carga - da sensacion de app instalada en vez de
un script en negro. Se lanza en paralelo a `python run.py` desde
iniciar.bat (mismo patron que esperar_y_abrir.bat: sondea el servidor en
vez de asumir un tiempo fijo) y se cierra sola en cuanto
http://127.0.0.1:8000/health responde - con un minimo de TIEMPO_MINIMO_S
visible aunque el servidor conteste antes (si no, en un arranque rapido
apenas se llega a ver).
"""

import ctypes
import time
import tkinter as tk
import urllib.request
from ctypes import wintypes
from pathlib import Path

from config import get_puerto

URL_SALUD = f"http://127.0.0.1:{get_puerto()}/health"
IMAGEN = Path(__file__).parent / "app" / "static" / "splash.png"
COLOR_CLAVE = "#010203"  # fuera de la paleta real del icono: se vuelve transparente
INTERVALO_MS = 300
MAX_INTENTOS = 200  # ~60s; si el servidor no arranca en ese tiempo, se cierra igual
TIEMPO_MINIMO_S = 3  # visible al menos esto aunque el servidor este listo antes


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


def monitor_del_cursor() -> tuple[int, int, int, int]:
    """(left, top, width, height) del monitor donde esta el raton en este
    instante - con varias pantallas, tk.winfo_screenwidth/height siempre
    devuelven las de "la pantalla" que Windows tiene marcada como principal,
    sin mirar donde esta realmente el usuario. Como el splash se lanza justo
    al hacer doble clic en el acceso directo, el cursor esta sobre la
    pantalla correcta en ese momento - se usa esa, no la principal."""
    pt = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    MONITOR_DEFAULTTONEAREST = 2
    hmon = ctypes.windll.user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(info))
    r = info.rcMonitor
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def servidor_listo() -> bool:
    try:
        with urllib.request.urlopen(URL_SALUD, timeout=0.5) as resp:
            return resp.status == 200
    except OSError:
        return False


def main():
    root = tk.Tk()
    root.withdraw()  # oculta la ventana por defecto (esquina superior izq) hasta tener geometry() puesta - si no, se ve un flash ahi antes de centrarse
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=COLOR_CLAVE)
    root.wm_attributes("-transparentcolor", COLOR_CLAVE)

    # subsample(2,2): imagen fuente 1254x1254 -> ~627x627. tkinter no tiene
    # filtro de interpolacion (solo decimacion por entero), pero al venir de
    # un icono limpio no se aprecia aliasing perceptible a este tamano.
    imagen = tk.PhotoImage(file=str(IMAGEN)).subsample(2, 2)
    root.imagen = imagen  # referencia viva - si no, el recolector de basura la borra
    ancho, alto = imagen.width(), imagen.height()

    mon_left, mon_top, mon_ancho, mon_alto = monitor_del_cursor()
    x = mon_left + (mon_ancho - ancho) // 2
    y = mon_top + (mon_alto - alto) // 2
    root.geometry(f"{ancho}x{alto}+{x}+{y}")

    tk.Label(root, image=imagen, bd=0, bg=COLOR_CLAVE).pack()
    root.deiconify()

    intentos = 0
    inicio = time.monotonic()

    def comprobar():
        nonlocal intentos
        intentos += 1
        tiempo_cumplido = time.monotonic() - inicio >= TIEMPO_MINIMO_S
        if (servidor_listo() and tiempo_cumplido) or intentos >= MAX_INTENTOS:
            root.destroy()
        else:
            root.after(INTERVALO_MS, comprobar)

    root.after(INTERVALO_MS, comprobar)
    root.mainloop()


if __name__ == "__main__":
    main()
