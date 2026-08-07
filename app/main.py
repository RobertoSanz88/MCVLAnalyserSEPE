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
    app.state.con = get_connection()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def raiz():
    return RedirectResponse("/colectivos")
