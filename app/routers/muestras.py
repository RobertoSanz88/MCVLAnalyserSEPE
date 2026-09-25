"""Gestion de muestras (carpetas de Parquet) y cambio de la muestra activa."""
import copy
import threading

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import config
from app.db import abrir_muestra, estado

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
