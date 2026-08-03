"""Configuracion de usuario: ruta local a la carpeta de Parquet de la MCVL.

Primer arranque: no existe %APPDATA%\\MCVLAnalyserSEPE\\config.json, se abre
un selector de carpeta nativo de Windows para pedirla y se guarda ahi.
Arranques siguientes: se lee directamente del JSON, sin preguntar de nuevo.
"""

import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ["APPDATA"]) / "MCVLAnalyserSEPE"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _pedir_carpeta_parquet() -> Path:
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    carpeta = filedialog.askdirectory(
        title="Selecciona la carpeta con los Parquet de la MCVL"
    )
    root.destroy()

    if not carpeta:
        raise RuntimeError("No se selecciono ninguna carpeta de Parquet.")

    return Path(carpeta)


def get_parquet_dir() -> Path:
    """Devuelve la carpeta de Parquet del usuario, pidiendola la primera vez."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return Path(data["parquet_dir"])

    parquet_dir = _pedir_carpeta_parquet()

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump({"parquet_dir": str(parquet_dir)}, f, ensure_ascii=False, indent=2)

    return parquet_dir
