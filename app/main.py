"""FastAPI app: analisis de la MCVL sobre Parquet local via DuckDB."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.db import get_connection
from app.routers import colectivos, trayectorias

app = FastAPI(title="MCVL Analyser SEPE")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(trayectorias.router)
app.include_router(colectivos.router)


@app.on_event("startup")
def startup():
    # Una unica conexion DuckDB para toda la app; los endpoints NO deben usarla
    # directamente (peticiones concurrentes de verdad corrompen el estado
    # interno de una misma Connection - visto con datos reales: una peticion
    # devolvia "Columna desconocida" para una columna que si existe, al
    # solaparse con otra peticion en curso). Cada endpoint saca su propio
    # con.cursor() (ver routers/colectivos.py y routers/trayectorias.py) - es
    # barato, comparte la misma base de datos en memoria, y aisla la ejecucion
    # entre peticiones simultaneas.
    app.state.con = get_connection()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def raiz():
    return RedirectResponse("/colectivos")
