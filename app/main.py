"""FastAPI app: analisis de la MCVL sobre Parquet local via DuckDB."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

import config
from app.db import ConexionActiva, abrir_muestra, estado
from app.routers import colectivos, muestras, trayectorias

app = FastAPI(title="MCVL Analyser SEPE")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(trayectorias.router)
app.include_router(colectivos.router)
app.include_router(muestras.router)


@app.on_event("startup")
def startup():
    # Los endpoints NO usan una Connection compartida directamente (peticiones concurrentes la corrompen):
    # cada uno saca su cursor con request.app.state.con.cursor(). app.state.con delega en la muestra activa.
    cfg = config.leer_config()
    m, aviso = config.muestra_inicial(cfg)
    if m is None:
        cfg = config.pedir_primera_muestra(cfg)
        m, aviso = config.muestra_inicial(cfg)
    estado.muestra = abrir_muestra(m["carpeta"], config.anio_de(m))
    estado.aviso_arranque = aviso
    cfg["activa"] = m["carpeta"]
    config.guardar_config(cfg)  # persiste tambien la migracion del config.json antiguo
    app.state.con = ConexionActiva()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def raiz():
    return RedirectResponse("/colectivos")
