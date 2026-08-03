"""Trayectoria individual: episodios de afiliacion de un IPF, ordenados por fecha."""

import json

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/trayectorias/{ipf}", response_class=HTMLResponse)
def trayectoria(request: Request, ipf: int):
    con = request.app.state.con
    episodios = con.execute(
        """
        SELECT
            fecha_alta_real_afil,
            fecha_baja_real_afil,
            ccc_ppal,
            tipo_contrato,
            reg_cotizacion,
            gru_cotizacion
        FROM afiliacion
        WHERE ipf = ?
        ORDER BY fecha_alta_real_afil
        """,
        [ipf],
    ).fetchall()

    filas = [
        {
            "empresa": ccc_ppal or "(sin CCC)",
            "inicio": fecha_alta_real_afil.isoformat() if fecha_alta_real_afil else None,
            "fin": fecha_baja_real_afil.isoformat() if fecha_baja_real_afil else None,
            "tipo_contrato": tipo_contrato,
            "regimen": reg_cotizacion,
            "grupo_cotizacion": gru_cotizacion,
        }
        for (
            fecha_alta_real_afil,
            fecha_baja_real_afil,
            ccc_ppal,
            tipo_contrato,
            reg_cotizacion,
            gru_cotizacion,
        ) in episodios
    ]

    return templates.TemplateResponse(
        request,
        "trayectoria.html",
        {
            "ipf": ipf,
            "episodios_json": json.dumps(filas, ensure_ascii=False),
        },
    )
