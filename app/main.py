"""FastAPI app: analisis de la MCVL sobre Parquet local via DuckDB."""

from fastapi import FastAPI

from app.db import get_connection

app = FastAPI(title="MCVL Analyser SEPE")


@app.on_event("startup")
def startup():
    app.state.con = get_connection()


@app.get("/health")
def health():
    return {"status": "ok"}
