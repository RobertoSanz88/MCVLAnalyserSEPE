"""Splash de arranque: ventana con el icono a tamano grande, centrada en
pantalla, mientras uvicorn carga - da sensacion de app instalada en vez de
un script en negro. Se lanza en paralelo a `python run.py` desde
iniciar.bat (mismo patron que esperar_y_abrir.bat: sondea el servidor en
vez de asumir un tiempo fijo) y se cierra sola en cuanto
http://127.0.0.1:8000/health responde.
"""

import ctypes
import tkinter as tk
import urllib.request
from ctypes import wintypes
from pathlib import Path

URL_SALUD = "http://127.0.0.1:8000/health"
IMAGEN = Path(__file__).parent / "app" / "static" / "splash.png"
COLOR_CLAVE = "#010203"  # fuera de la paleta real del icono: se vuelve transparente
INTERVALO_MS = 300
MAX_INTENTOS = 200  # ~60s; si el servidor no arranca en ese tiempo, se cierra igual


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
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=COLOR_CLAVE)
    root.wm_attributes("-transparentcolor", COLOR_CLAVE)

    # subsample(4,4): imagen fuente 1254x1254 -> ~313x313, tamano razonable
    # de splash sin necesitar Pillow (subsample es lo unico que trae tkinter
    # de fabrica para reducir una PhotoImage).
    imagen = tk.PhotoImage(file=str(IMAGEN)).subsample(4, 4)
    root.imagen = imagen  # referencia viva - si no, el recolector de basura la borra
    ancho, alto = imagen.width(), imagen.height()

    mon_left, mon_top, mon_ancho, mon_alto = monitor_del_cursor()
    x = mon_left + (mon_ancho - ancho) // 2
    y = mon_top + (mon_alto - alto) // 2
    root.geometry(f"{ancho}x{alto}+{x}+{y}")

    tk.Label(root, image=imagen, bd=0, bg=COLOR_CLAVE).pack()

    intentos = 0

    def comprobar():
        nonlocal intentos
        intentos += 1
        if servidor_listo() or intentos >= MAX_INTENTOS:
            root.destroy()
        else:
            root.after(INTERVALO_MS, comprobar)

    root.after(INTERVALO_MS, comprobar)
    root.mainloop()


if __name__ == "__main__":
    main()
