"""Gestion de muestras (carpetas de Parquet) y cambio de la muestra activa."""
import copy
import json
import re
import sys
import threading
from pathlib import Path

import duckdb
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import config
from app.db import _sql_ruta, abrir_muestra, estado

router = APIRouter()
_bloqueo = threading.Lock()  # serializa las operaciones sobre config.json y el cambio de muestra


class MuestraBody(BaseModel):
    carpeta: str | None = None
    actual: str | None = None
    anio: int | None = None


def _respuesta(cfg, **extra):
    m = estado.muestra
    return {"activa": str(m.carpeta), "anio": m.anio,
            "muestras": config.muestras_con_estado(cfg, activa=str(m.carpeta)), **extra}


def _error(e, carpeta=None):
    if isinstance(e, config.FaltaAnio):
        return HTTPException(409, detail={"falta_anio": True, "carpeta": carpeta, "mensaje": str(e)})
    return HTTPException(400, detail=str(e))


def _abrir_o_400(m):
    try:
        return abrir_muestra(m["carpeta"], config.anio_de(m))
    except Exception as e:  # falta un Parquet, esta danado, disco no disponible...
        raise HTTPException(400, detail=f"No se pudo abrir la muestra: {e}")


@router.get("/muestras")
def listar():
    aviso, estado.aviso_arranque = estado.aviso_arranque, None
    return _respuesta(config.leer_config(), aviso=aviso)


@router.post("/muestras/anadir")
def anadir(body: MuestraBody):
    carpeta = body.carpeta or config.elegir_carpeta_dialogo("Selecciona la carpeta con los Parquet de la muestra")
    if not carpeta:
        return {"cancelado": True}
    with _bloqueo:
        cfg = config.leer_config()
        try:
            config.anadir_muestra(cfg, carpeta, body.anio)
        except config.ErrorMuestra as e:
            raise _error(e, carpeta)
        cfg["activa"] = str(estado.muestra.carpeta)  # anadir nunca cambia la muestra activa
        config.guardar_config(cfg)
        return _respuesta(cfg)


@router.post("/muestras/editar")
def editar(body: MuestraBody):
    if not body.actual:
        raise HTTPException(400, detail="Falta la muestra a editar.")
    nueva = body.carpeta or config.elegir_carpeta_dialogo("Selecciona la nueva carpeta de la muestra")
    if not nueva:
        return {"cancelado": True}
    with _bloqueo:
        cfg = config.leer_config()
        era_activa = config._clave(estado.muestra.carpeta) == config._clave(body.actual)
        try:
            config.editar_muestra(cfg, body.actual, nueva, body.anio)
        except config.ErrorMuestra as e:
            raise _error(e, nueva)
        if era_activa:
            estado.muestra = _abrir_o_400(config.buscar_muestra(cfg, nueva))
        cfg["activa"] = str(estado.muestra.carpeta)
        config.guardar_config(cfg)
        return _respuesta(cfg)


@router.post("/muestras/eliminar")
def eliminar(body: MuestraBody):
    with _bloqueo:
        cfg = config.leer_config()
        nuevo = copy.deepcopy(cfg)
        try:
            config.eliminar_muestra(nuevo, body.carpeta)
        except config.ErrorMuestra as e:
            raise _error(e)
        if config._clave(estado.muestra.carpeta) == config._clave(body.carpeta):
            siguiente, _ = config.muestra_inicial(nuevo)
            if siguiente is None:
                raise HTTPException(400, detail="No queda ninguna otra muestra disponible.")
            estado.muestra = _abrir_o_400(siguiente)
        nuevo["activa"] = str(estado.muestra.carpeta)
        config.guardar_config(nuevo)
        return _respuesta(nuevo)


@router.post("/muestras/activar")
def activar(body: MuestraBody):
    with _bloqueo:
        cfg = config.leer_config()
        try:
            config.activar_muestra(cfg, body.carpeta)
        except config.ErrorMuestra as e:
            raise _error(e)
        estado.muestra = _abrir_o_400(config.buscar_muestra(cfg, body.carpeta))  # solo se sustituye si se abrio bien
        config.guardar_config(cfg)
        return _respuesta(cfg)


def _ruta_recurso(relativa: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent))
    return base / relativa


def _normalizar(col: str) -> str:
    col = re.sub(r"(\d{4})/\d$", "AAAA/A", col)
    return re.sub(r"_(\d{4})$", "_AAAA", col)


@router.get("/variables")
def variables():
    """Descripcion de las variables de Personas y Afiliacion, cruzada con las columnas reales de la muestra activa."""
    catalogo = json.loads(_ruta_recurso("app/static/datos/variables.json").read_text(encoding="utf-8"))
    m = estado.muestra
    con = duckdb.connect()
    salida = {"anio": m.anio}
    for tabla, fichero in (("personas", "tabla_personas_extendida.parquet"), ("afiliacion", "tabla_afiliacion_extendida.parquet")):
        columnas = [(x[0], x[1]) for x in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{_sql_ruta(m.carpeta / fichero)}')").fetchall()]
        filas = []
        for n, t in columnas:
            info = catalogo[tabla].get(_normalizar(n) if tabla == "personas" else n, {})
            filas.append({"nombre": _normalizar(n) if tabla == "personas" else n, "tipo": t,
                          "origen": info.get("origen", ""), "descripcion": info.get("descripcion") or "sin descripción",
                          "construccion": info.get("construccion") or "sin información",
                          "categorias": info.get("categorias", []),
                          "categorias_nota": info.get("categorias_nota", "")})
        salida[tabla] = filas
    return salida


_SECCIONES_CONSTRUCCION = ("## Cómo construir los datos", "## Estructura")
_SECCIONES_TESTS = ("## Reglas de consistencia",)


def partir_readme(texto: str) -> dict:
    """Separa el README en el texto principal y el desplegable final de tests de consistencia. Las secciones
    de construccion de variables (solo para el equipo ETL) se quitan enteras: no se devuelven en ningun sitio."""
    trozos, actual = [], []
    for linea in texto.splitlines():
        if linea.startswith("## ") and actual:
            trozos.append("\n".join(actual))
            actual = []
        actual.append(linea)
    trozos.append("\n".join(actual))
    salida = {"principal": [], "tests": []}
    for t in trozos:
        if t.startswith(_SECCIONES_CONSTRUCCION):
            continue
        elif t.startswith(_SECCIONES_TESTS):
            salida["tests"].append(t)
        else:
            salida["principal"].append(t)
    return {k: "\n\n".join(v) for k, v in salida.items()}


@router.get("/acerca-de")
def acerca_de():
    texto = _ruta_recurso("README.md").read_text(encoding="utf-8")
    casos = json.loads(_ruta_recurso("app/static/datos/casos_raros.json").read_text(encoding="utf-8"))
    return {**partir_readme(texto), "casos": casos}
