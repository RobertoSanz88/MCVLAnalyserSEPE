"""Configuracion de usuario: ruta local a la carpeta de Parquet de la MCVL.

Primer arranque de ESTA instalacion: no existe config.json junto al propio
.exe (o junto a config.py en modo dev), se abre un selector de carpeta
nativo de Windows para pedirla y se guarda ahi. Arranques siguientes: se lee
directamente del JSON, sin preguntar de nuevo - salvo que la carpeta guardada
ya no sea valida (ver get_parquet_dir), en cuyo caso se vuelve a preguntar.

config.json vive JUNTO al propio ejecutable/instalacion (2026-09-11, decision
explicita del usuario), NO en una ubicacion compartida como %APPDATA%: asi
cada copia del .exe recuerda su propia carpeta de Parquet de forma
independiente - p.ej. una version antigua en una carpeta apuntando a tablas
antiguas, y una version nueva en otra carpeta apuntando a tablas nuevas, sin
que una pise la configuracion de la otra. Antes vivia en
%APPDATA%\\MCVLAnalyserSEPE\\, una UNICA ubicacion compartida por CUALQUIER
copia del .exe en el PC sin importar en que carpeta estuviera cada una - un
companyero con una version anterior instalada heredaba en silencio, al
probar la v2.0, la carpeta que hubiera dejado guardada esa version anterior
(bug real reportado por el usuario: la v2.0 "no funcionaba" y no preguntaba
la carpeta).
"""

import datetime
import json
import sys
from pathlib import Path


def _archivo_config() -> Path:
    # sys.frozen=True solo dentro del .exe empaquetado por PyInstaller (ver
    # run.py) - sys.executable es entonces mcvlanalyser.exe, y su carpeta es
    # la propia instalacion (build/dist/mcvlanalyser/ o donde sea que un
    # companyero haya copiado esa carpeta entera - onedir es portable, el
    # config.json viaja con ella). En modo dev (sys.frozen no existe,
    # sys.executable es python.exe) no hay "instalacion" que valga como
    # referencia, se usa la carpeta de este propio fichero (raiz del repo).
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "config.json"
    return Path(__file__).resolve().parent / "config.json"


CONFIG_FILE = _archivo_config()

# Fichero clave para validar una carpeta de Parquet (ver get_parquet_dir) -
# la tabla maestra de la que dependen todas las rutas de la app (app/db.py).
# No se comprueban las demas tablas (Pensiones, Bases...): esta ya es una
# senyal suficiente de "es una carpeta de Parquet real de esta MCVL" sin
# complicar la validacion.
_FICHERO_CLAVE_PARQUET = "tabla_personas_extendida.parquet"


def _pedir_carpeta_parquet() -> Path:
    import tkinter as tk
    from tkinter import filedialog, messagebox

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)

    # Bucle: si la carpeta elegida no tiene el fichero clave, se avisa y se
    # vuelve a preguntar en vez de aceptarla a ciegas (mismo motivo que la
    # validacion de la carpeta ya guardada en get_parquet_dir - una carpeta
    # equivocada aqui llevaria al mismo fallo silencioso al arrancar).
    while True:
        carpeta = filedialog.askdirectory(
            title="Selecciona la carpeta con los Parquet de la MCVL"
        )
        if not carpeta:
            root.destroy()
            raise RuntimeError("No se selecciono ninguna carpeta de Parquet.")

        ruta = Path(carpeta)
        if (ruta / _FICHERO_CLAVE_PARQUET).exists():
            root.destroy()
            return ruta

        messagebox.showerror(
            "Carpeta no válida",
            f"Esa carpeta no tiene {_FICHERO_CLAVE_PARQUET}.\n\n"
            "Selecciona la carpeta correcta con los Parquet de la MCVL.",
        )


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
# 2026-09-21 (decision explicita del usuario): 2025-06-01 -> 2025-05-01. La ultima
# baja real de la muestra es del 30-04-2025, luego se extrajo el 1-05.
# 2026-09-23 (decision explicita del usuario): la fecha de corte de una MCVL es el 1 de mayo del
# anyo SIGUIENTE al de la edicion (edicion 2024 -> 2025-05-01, edicion 2025 -> 2026-05-01). El ETL
# la calcula a partir de la casilla "Anyo" y la deja escrita en FICHERO_FECHA_CORTE dentro de la
# carpeta de Parquet, para que la app (otro proceso, a veces otro PC) use SIEMPRE la misma que se
# uso al construirlos. Un Parquet sin ese fichero (construido antes de este cambio) usa
# FECHA_CORTE, que es la de la edicion 2024. Verificar contra las ultimas altas/bajas reales de
# cada edicion nueva: la formula es una convencion, no un dato.
FECHA_CORTE = "2025-05-01"
FICHERO_FECHA_CORTE = "fecha_corte.txt"


def fecha_corte_para_anio(anio) -> str:
    """Fecha de corte (ISO) de la edicion de MCVL 'anio': 1 de mayo del anyo siguiente."""
    return f"{int(anio) + 1}-05-01"


def escribir_fecha_corte(parquet_dir, anio) -> str:
    """Escribe fecha_corte.txt junto a los Parquet (lo llama el ETL) y devuelve la fecha."""
    iso = fecha_corte_para_anio(anio)
    Path(parquet_dir, FICHERO_FECHA_CORTE).write_text(iso + "\n", encoding="utf-8")
    return iso


def leer_fecha_corte(parquet_dir) -> str:
    """Fecha de corte de los Parquet de 'parquet_dir': la de fecha_corte.txt, o FECHA_CORTE
    (edicion 2024) si no existe o no es valida."""
    try:
        texto = Path(parquet_dir, FICHERO_FECHA_CORTE).read_text(encoding="utf-8").strip()
        datetime.date.fromisoformat(texto)
        return texto
    except (OSError, ValueError):
        return FECHA_CORTE

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
    """Devuelve la carpeta de Parquet de esta instalacion, pidiendola si hace
    falta: en el primer arranque, o si la carpeta guardada ya no es valida
    (se borro/movio, o le falta _FICHERO_CLAVE_PARQUET - p.ej. apunta a una
    carpeta de otra MCVL sin regenerar). Sin esta validacion, una carpeta
    guardada que dejo de existir se reutilizaba en silencio y la app fallaba
    al arrancar sin decir por que (bug real reportado por el usuario,
    2026-09-11)."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        parquet_dir = Path(data["parquet_dir"])
        if (parquet_dir / _FICHERO_CLAVE_PARQUET).exists():
            return parquet_dir
        # Carpeta guardada invalida - se trata como un primer arranque de
        # esta instalacion, cae al bloque de abajo y vuelve a preguntar.

    parquet_dir = _pedir_carpeta_parquet()

    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
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
    {"columna": "situacion_paro_ocupacion", "limite": None},
    {"columna": "situacion_actual_afiliacion", "limite": None},
    {"columna": "grupo_cotiz_1er_empleo_signif", "limite": None},
    {"columna": "grupo_ultimo_empleo", "limite": None},
    {"columna": "reg_cotiz_ppal_agrupado", "limite": None},
    {"columna": "grupo_cotiz_ppal", "limite": None},
    {"columna": "tipo_contr_ppal_agrupado", "limite": None},
    {"columna": "prov_trabajo_ppal", "limite": 15},
    {"columna": "prov_fiscal_actual", "limite": 15},
    {"columna": "act_econ_ppal", "limite": 15},
    {"columna": "cobra_pension_*", "limite": None},
    {"columna": "discap_fiscal", "limite": None},
    {"columna": "discap_afiliacion", "limite": None},
    {"columna": "discap_pension", "limite": None},
]
