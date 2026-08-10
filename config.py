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


PUERTO_POR_DEFECTO = 8000


def get_puerto() -> int:
    """Puerto donde arranca el servidor. 8000 salvo que el usuario tenga
    "puerto" en config.json - pensado para el caso (visto en el PC
    corporativo) de que el 8000 ya este ocupado de forma permanente por otro
    servicio de la empresa (wgssSigCaptX_Service.exe, firma digital) y no se
    pueda liberar. Sin wizard propio: se edita config.json a mano si hace
    falta, es un caso raro que no justifica una pantalla nueva."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("puerto", PUERTO_POR_DEFECTO)
    return PUERTO_POR_DEFECTO


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


# Columnas categoricas que se muestran en "Distribucion categorica" del
# constructor de colectivos (app/routers/colectivos.py). Personas tiene
# decenas de columnas categoricas; mostrarlas todas satura la pantalla, asi
# que aqui se elige explicitamente el subconjunto util para el Observatorio.
#
# "columna": nombre limpio tal cual lo deja app/db.py (snake_case), o un
#     prefijo acabado en "*" para columnas cuyo nombre cambia segun el anyo
#     de la MCVL procesada (p.ej. cobra_pension_2024_5 -> "cobra_pension_*",
#     asi el filtro sigue funcionando sin tocar codigo cuando cambie el anyo).
# "limite": maximo de categorias a mostrar, las mas frecuentes primero
#     (None = todas). Pensado para columnas con muchas categorias posibles,
#     como act_econ_ppal (CNAE, ~230 valores) o prov_trabajo_ppal (~52
#     provincias). Se aplica por subcolectivo (backend, LIMIT en la query de
#     cada uno de los 4 filtros), y el grafico/tabla del frontend pinta
#     despues la UNION de esos 4 top-N (_datosColumnaCategorica en
#     colectivos.html) - si los subcolectivos no coinciden en sus valores mas
#     frecuentes, el total visible puede superar el limite. 15 (en vez de un
#     numero mas alto) es a proposito para dejar margen a esa union sin que
#     el grafico acabe mostrando demasiadas categorias.
#
# Entradas sin columna correspondiente en el Parquet actual se ignoran en
# silencio (permite un config.py valido para varios anyos de datos).
CATEGORICAS_COLECTIVOS = [
    {"columna": "sexo", "limite": None},
    {"columna": "nacionalidad_agrupado", "limite": None},
    {"columna": "pais_nacim_agrupado", "limite": None},
    {"columna": "situacion_actual_afiliacion", "limite": None},
    {"columna": "grupo_cotiz_1er_empleo_signif", "limite": None},
    {"columna": "grupo_ultimo_empleo", "limite": None},
    {"columna": "reg_cotiz_ppal", "limite": None},
    {"columna": "grupo_cotiz_ppal", "limite": None},
    {"columna": "tipo_contrato_ppal_agrupado", "limite": None},
    {"columna": "prov_trabajo_ppal", "limite": 15},
    {"columna": "act_econ_ppal", "limite": 15},
    {"columna": "discap_fiscal", "limite": None},
    {"columna": "discap_afiliacion", "limite": None},
    {"columna": "discap_pension", "limite": None},
    {"columna": "cobra_pension_*", "limite": None},
    {"columna": "situacion_paro_ocupacion", "limite": None},
]
