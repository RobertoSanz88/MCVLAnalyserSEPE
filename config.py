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
import os
import shutil
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


def _dialogo_carpeta(titulo: str) -> str:
    """Selector de carpeta nativo. Devuelve '' si se cancela."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askdirectory(title=titulo) or ""
    finally:
        root.destroy()


def elegir_carpeta_dialogo(titulo: str):
    """Abre el selector de carpeta en un PROCESO APARTE (el servidor es multihilo y Tk no debe usarse desde
    un hilo de peticion). En el .exe se relanza el propio ejecutable con --elegir-carpeta (ver run.py)."""
    import subprocess

    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--elegir-carpeta", titulo]
    else:
        cmd = [sys.executable, str(Path(__file__).resolve().parent / "run.py"), "--elegir-carpeta", titulo]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=900)
    lineas = [x for x in r.stdout.splitlines() if x.strip()]
    return lineas[-1].strip() if r.returncode == 0 and lineas else None


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

# --- Varias muestras (v2.2) -------------------------------------------------------------------------------
# config.json: {"muestras": [{"carpeta": ..., "anio": ...}], "activa": <carpeta>|null, "puerto": ...}.
# Un config.json anterior ({"parquet_dir": ...}) se migra en memoria a una lista de una muestra.


class ErrorMuestra(Exception):
    """Error de validacion al gestionar la lista de muestras (mensaje apto para mostrar al usuario)."""


class FaltaAnio(ErrorMuestra):
    """La carpeta no tiene fecha_corte.txt y no se ha indicado el anio."""


def _clave(carpeta) -> str:
    return os.path.normcase(os.path.normpath(str(carpeta)))


def carpeta_valida(carpeta) -> bool:
    return (Path(carpeta) / _FICHERO_CLAVE_PARQUET).exists()


def leer_anio(carpeta):
    """Anio de la edicion (anio de corte - 1) segun fecha_corte.txt; None si no existe o no es valida."""
    try:
        texto = Path(carpeta, FICHERO_FECHA_CORTE).read_text(encoding="utf-8").strip()
        return datetime.date.fromisoformat(texto).year - 1
    except (OSError, ValueError):
        return None


def anio_de(m: dict):
    return leer_anio(m["carpeta"]) or m.get("anio")


def disponible(m: dict) -> bool:
    return carpeta_valida(m["carpeta"])


def leer_config() -> dict:
    data = {}
    if CONFIG_FILE.exists():
        try:
            leido = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if not isinstance(leido, dict):
                raise ValueError("config.json no es un objeto")
            data = leido
        except (OSError, ValueError):
            data = {}
            _guardar_copia_del_corrupto()
    if "muestras" not in data:
        antigua = data.pop("parquet_dir", None)
        data["muestras"] = []
        data["activa"] = None
        if antigua:
            carpeta = str(Path(antigua))
            data["muestras"] = [{"carpeta": carpeta, "anio": leer_anio(carpeta)}]
            data["activa"] = carpeta
    data.pop("parquet_dir", None)
    data.setdefault("activa", None)
    return data


def _guardar_copia_del_corrupto() -> None:
    """Un config.json corrupto se trata como 'sin muestras', pero antes se copia a config.json.corrupto para que
    la lista anterior no se pierda en silencio cuando se vuelva a escribir el fichero."""
    try:
        shutil.copyfile(CONFIG_FILE, CONFIG_FILE.with_name(CONFIG_FILE.name + ".corrupto"))
    except OSError:
        pass


def guardar_config(cfg: dict) -> None:
    """Escritura atomica (fichero temporal + reemplazo): un corte a mitad no deja un JSON truncado."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporal = CONFIG_FILE.with_name(CONFIG_FILE.name + ".tmp")
    temporal.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporal, CONFIG_FILE)


def muestras_candidatas(cfg: dict) -> list:
    """Muestras disponibles en orden de preferencia para arrancar: la activa primero y despues las mas recientes."""
    disp = sorted((m for m in cfg["muestras"] if disponible(m)), key=lambda m: anio_de(m) or 0, reverse=True)
    activa = buscar_muestra(cfg, cfg["activa"]) if cfg.get("activa") else None
    if activa is not None and activa in disp:
        disp.remove(activa)
        disp.insert(0, activa)
    return disp


def completar_anios(cfg: dict, preguntar) -> dict:
    """Pide el anio (con preguntar(carpeta) -> int|None) de las muestras disponibles que no lo tienen: p. ej. un
    config.json antiguo cuya carpeta no trae fecha_corte.txt."""
    for m in cfg["muestras"]:
        if disponible(m) and anio_de(m) is None:
            a = preguntar(m["carpeta"])
            if a:
                m["anio"] = int(a)
    return cfg


def pedir_anio_dialogo(carpeta) -> "int | None":
    import tkinter as tk
    from tkinter import simpledialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return simpledialog.askinteger("Año de la muestra", f"La carpeta {carpeta}\nno indica el año de la MCVL.\nAño:",
                                       minvalue=2004, maxvalue=2100, parent=root)
    finally:
        root.destroy()


def buscar_muestra(cfg: dict, carpeta):
    for m in cfg["muestras"]:
        if _clave(m["carpeta"]) == _clave(carpeta):
            return m
    return None


def _mas_reciente_disponible(cfg: dict):
    disp = [m for m in cfg["muestras"] if disponible(m)]
    return max(disp, key=lambda m: anio_de(m) or 0) if disp else None


def anadir_muestra(cfg: dict, carpeta, anio=None) -> dict:
    if not carpeta_valida(carpeta):
        raise ErrorMuestra(f"Esa carpeta no tiene {_FICHERO_CLAVE_PARQUET}.")
    if buscar_muestra(cfg, carpeta):
        raise ErrorMuestra("Esa carpeta ya está en la lista de muestras.")
    a = leer_anio(carpeta) or anio
    if a is None:
        raise FaltaAnio("La carpeta no tiene fecha_corte.txt: indica el año de la muestra.")
    cfg["muestras"].append({"carpeta": str(Path(carpeta)), "anio": int(a)})
    if not cfg.get("activa"):
        cfg["activa"] = str(Path(carpeta))
    return cfg


def editar_muestra(cfg: dict, actual, nueva, anio=None) -> dict:
    m = buscar_muestra(cfg, actual)
    if m is None:
        raise ErrorMuestra("La muestra no está en la lista.")
    if not carpeta_valida(nueva):
        raise ErrorMuestra(f"Esa carpeta no tiene {_FICHERO_CLAVE_PARQUET}.")
    otra = buscar_muestra(cfg, nueva)
    if otra is not None and otra is not m:
        raise ErrorMuestra("Esa carpeta ya está en la lista de muestras.")
    a = leer_anio(nueva) or anio
    if a is None:
        raise FaltaAnio("La carpeta no tiene fecha_corte.txt: indica el año de la muestra.")
    era_activa = cfg.get("activa") and _clave(cfg["activa"]) == _clave(m["carpeta"])
    m["carpeta"], m["anio"] = str(Path(nueva)), int(a)
    if era_activa:
        cfg["activa"] = m["carpeta"]
    return cfg


def eliminar_muestra(cfg: dict, carpeta) -> dict:
    """Quita la muestra de la lista. NUNCA borra ficheros. La unica que queda no se puede quitar."""
    m = buscar_muestra(cfg, carpeta)
    if m is None:
        raise ErrorMuestra("La muestra no está en la lista.")
    if len(cfg["muestras"]) == 1:
        raise ErrorMuestra("Es la única muestra: añade otra antes de quitarla.")
    era_activa = cfg.get("activa") and _clave(cfg["activa"]) == _clave(m["carpeta"])
    cfg["muestras"].remove(m)
    if era_activa:
        nueva = _mas_reciente_disponible(cfg)
        cfg["activa"] = nueva["carpeta"] if nueva else None
    return cfg


def activar_muestra(cfg: dict, carpeta) -> dict:
    m = buscar_muestra(cfg, carpeta)
    if m is None:
        raise ErrorMuestra("La muestra no está en la lista.")
    if not disponible(m):
        raise ErrorMuestra("La carpeta de esta muestra no está disponible.")
    cfg["activa"] = m["carpeta"]
    return cfg


def muestras_con_estado(cfg: dict, activa=None) -> list:
    activa = activa if activa is not None else cfg.get("activa")
    filas = [{"carpeta": m["carpeta"], "anio": anio_de(m), "disponible": disponible(m),
              "activa": bool(activa) and _clave(activa) == _clave(m["carpeta"])} for m in cfg["muestras"]]
    return sorted(filas, key=lambda f: f["anio"] or 0, reverse=True)


def muestra_inicial(cfg: dict):
    """(muestra, aviso): la activa si esta disponible; si no, la mas reciente disponible con un aviso."""
    if cfg.get("activa"):
        m = buscar_muestra(cfg, cfg["activa"])
        if m is not None and disponible(m):
            return m, None
    m = _mas_reciente_disponible(cfg)
    if m is None:
        return None, None
    aviso = f"La última muestra usada no está disponible; se abre la {anio_de(m)}." if cfg.get("activa") else None
    return m, aviso


PUERTO_POR_DEFECTO = 8000


def get_puerto() -> int:
    """Puerto PREFERIDO donde arrancar el servidor. 8000 salvo que el usuario
    tenga "puerto" en config.json (override manual, para quien quiera fijar
    uno concreto a proposito - p.ej. una regla de firewall). No es el puerto
    FINAL: si esta ocupado, puerto_libre() busca uno libre a partir de este -
    ver ese docstring para el porque (antes esto exigia editar config.json a
    mano, ahora solo hace falta como override opcional)."""
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        puerto = data.get("puerto", PUERTO_POR_DEFECTO) if isinstance(data, dict) else PUERTO_POR_DEFECTO
        return puerto if isinstance(puerto, int) else PUERTO_POR_DEFECTO
    except (OSError, ValueError):  # sin config.json, vacio o corrupto: puerto por defecto
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


def pedir_primera_muestra(cfg: dict) -> dict:
    """Primer arranque (sin muestras): pide la carpeta (validada) y, si no tiene fecha_corte.txt, el anio."""
    import tkinter as tk
    from tkinter import messagebox, simpledialog

    def _raiz():
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        return root

    while True:
        carpeta = _dialogo_carpeta("Selecciona la carpeta con los Parquet de la MCVL")
        if not carpeta:
            raise RuntimeError("No se selecciono ninguna carpeta de Parquet.")
        if carpeta_valida(carpeta):
            break
        root = _raiz()
        messagebox.showerror("Carpeta no válida", f"Esa carpeta no tiene {_FICHERO_CLAVE_PARQUET}.\n\n"
                             "Selecciona la carpeta correcta con los Parquet de la MCVL.")
        root.destroy()
    anio = None
    if leer_anio(carpeta) is None:
        root = _raiz()
        anio = simpledialog.askinteger("Año de la muestra", "Esta carpeta no indica el año de la MCVL.\nAño:",
                                       minvalue=2004, maxvalue=2100, parent=root)
        root.destroy()
        if anio is None:
            raise RuntimeError("No se indico el año de la muestra.")
    anadir_muestra(cfg, carpeta, anio)
    guardar_config(cfg)
    return cfg


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
