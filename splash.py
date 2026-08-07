"""Splash de arranque: ventana con el icono a tamano grande, centrada en
pantalla, mientras uvicorn carga - da sensacion de app instalada en vez de
un script en negro. Se lanza en paralelo a `python run.py` desde
iniciar.bat (mismo patron que esperar_y_abrir.bat: sondea el servidor en
vez de asumir un tiempo fijo) y se cierra sola en cuanto
http://127.0.0.1:8000/health responde.
"""

import tkinter as tk
import urllib.request
from pathlib import Path

URL_SALUD = "http://127.0.0.1:8000/health"
IMAGEN = Path(__file__).parent / "app" / "static" / "splash.png"
COLOR_CLAVE = "#010203"  # fuera de la paleta real del icono: se vuelve transparente
INTERVALO_MS = 300
MAX_INTENTOS = 200  # ~60s; si el servidor no arranca en ese tiempo, se cierra igual


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

    x = (root.winfo_screenwidth() - ancho) // 2
    y = (root.winfo_screenheight() - alto) // 2
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
