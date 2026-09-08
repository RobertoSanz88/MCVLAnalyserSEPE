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


# Fecha de corte de la MCVL procesada (edicion 2024): fecha de extraccion de
# los datos crudos, usada por el ETL para cerrar episodios de afiliacion
# todavia abiertos (placeholder 2099 en FECHA_BAJA_REAL_AFIL), calcular
# edades/duraciones "a fecha de hoy" sobre datos congelados, etc. Hasta
# 2026-08-24 existian dos constantes distintas para esto (FECHA_CORTE
# 2025-06-01 en etl/personas_extendida.py y FECHA_REFERENCIA_45 2025-04-30,
# usada solo para duracion_actual_sin_empleo) - decision explicita del
# usuario: unificarlas en una sola, aqui, para que etl/ y app/ lean siempre
# el mismo valor. Cambiar esto exige regenerar personas_extendida.parquet
# (afecta a columnas ya calculadas por el ETL, no solo a la app).
FECHA_CORTE = "2025-06-01"

PUERTO_POR_DEFECTO = 8000


def get_puerto() -> int:
    """Puerto PREFERIDO donde arrancar el servidor. 8000 salvo que el usuario
    tenga "puerto" en config.json (override manual, para quien quiera fijar
    uno concreto a proposito - p.ej. una regla de firewall). No es el puerto
    FINAL: si esta ocupado, puerto_libre() busca uno libre a partir de este -
    ver ese docstring para el porque (antes esto exigia editar config.json a
    mano, ahora solo hace falta como override opcional)."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("puerto", PUERTO_POR_DEFECTO)
    return PUERTO_POR_DEFECTO


def puerto_libre(preferido: int, intentos: int = 20) -> int:
    """Primer puerto libre en 127.0.0.1 a partir de 'preferido' (prueba
    preferido, preferido+1, preferido+2...). Pensado para el caso (visto en
    el PC corporativo del usuario) de que el puerto preferido este ocupado de
    forma PERMANENTE por otro servicio de la empresa (wgssSigCaptX_Service.exe,
    firma digital) - antes habia que editar "puerto" en config.json a mano
    para arrancar ahi, algo que no se le puede pedir a cada companyero del
    equipo que use el .exe (ellos no tocan config.json, solo el wizard de la
    carpeta de Parquet). Con esto la app se busca sola un puerto libre y
    arranca sin intervencion, en cualquier maquina.

    Bind-test real con un socket propio (no solo "intentarlo y capturar el
    error" en uvicorn.run, que ya es tarde: para entonces splash.exe ya se
    lanzo apuntando al puerto preferido). Pequeña ventana de carrera entre
    esta prueba y el bind real de uvicorn (el socket se cierra aqui antes de
    devolver el puerto) - inevitable con este enfoque, pero irrelevante en la
    practica: es una app local de un unico usuario, no un servidor compartido
    donde algo mas pueda robar el puerto en ese instante."""
    import socket

    for offset in range(intentos):
        puerto = preferido + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", puerto))
                return puerto
            except OSError:
                continue
    return preferido


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
    {"columna": "reg_cotiz_ppal_agrupado", "limite": None},
    {"columna": "grupo_cotiz_ppal", "limite": None},
    {"columna": "tipo_contr_ppal_agrupado", "limite": None},
    {"columna": "prov_trabajo_ppal", "limite": 15},
    {"columna": "act_econ_ppal", "limite": 15},
    {"columna": "discap_fiscal", "limite": None},
    {"columna": "discap_afiliacion", "limite": None},
    {"columna": "discap_pension", "limite": None},
    {"columna": "cobra_pension_*", "limite": None},
    {"columna": "situacion_paro_ocupacion", "limite": None},
]
