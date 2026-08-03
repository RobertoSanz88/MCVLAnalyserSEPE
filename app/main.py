"""FastAPI app: analisis de la MCVL sobre Parquet local via DuckDB."""

from fastapi import FastAPI

from app.db import get_connection
from app.routers import colectivos, trayectorias

app = FastAPI(title="MCVL Analyser SEPE")
app.include_router(trayectorias.router)
app.include_router(colectivos.router)


@app.on_event("startup")
def startup():
    app.state.con = get_connection()


@app.get("/health")
def health():
    return {"status": "ok"}
