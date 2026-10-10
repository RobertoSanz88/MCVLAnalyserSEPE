"""Herramienta interna para construir los Parquet de la MCVL desde la web, con progreso en
vivo (SSE). Deliberadamente FUERA de app/: etl/ es de uso restringido (ver CLAUDE.md), esta
herramienta no se reparte al resto del equipo ni viaja en el .exe de la app de analisis.
"""

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import subprocess

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

app = FastAPI(title="MCVLRefinery")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

ACCIONES = [
    ("personas", "Personas", "-> tabla_personas_optimizada.parquet"),
    ("fiscal", "Datos fiscales", "-> tabla_datos_fiscales_optimizada.parquet"),
    ("bases_ca", "Bases cotización cuenta ajena", "-> tabla_bases_cotizacion_CA_optimizada.parquet"),
    ("bases_cp", "Bases cotización cuenta propia", "-> tabla_bases_cotizacion_CP_optimizada.parquet"),
    ("pensiones", "Pensiones", "-> tabla_pensiones_extendida.parquet (Fase 2 incluida)"),
    ("afiliacion", "Afiliación", "-> tabla_afiliacion_extendida.parquet (Fase 1 incluida)"),
]


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request, "index.html", {"acciones": ACCIONES})


@app.get("/ejecutar")
def ejecutar_sse(accion: str, raw: str, parquet: str, year: str = "2024", borrar_txt: bool = False):
    script = str(Path(__file__).parent / "ejecutar_paso.py")
    cmd = [sys.executable, "-u", script, "--accion", accion, "--year", year, "--raw", raw, "--parquet", parquet]
    if borrar_txt:
        cmd.append("--borrar-txt")

    def stream():
        yield f"data: $ {' '.join(cmd)}\n\n"
        proceso = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        for linea in proceso.stdout:
            for sublinea in linea.rstrip("\n").split("\r"):
                if sublinea:
                    yield f"data: {sublinea}\n\n"
        proceso.wait()
        if proceso.returncode == 0:
            yield "event: fin\ndata: OK\n\n"
        else:
            yield f"event: fin\ndata: ERROR (código de salida {proceso.returncode})\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
