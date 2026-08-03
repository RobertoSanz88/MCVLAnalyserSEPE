"""Constructor de colectivos: filtros + recuento de personas distintas.

Un colectivo es el conjunto de personas con al menos un episodio de
afiliacion que cumple los filtros. Primer corte minimo: sexo y rango
de fechas de alta. Mas filtros y estadisticas se anaden en commits
sucesivos.
"""

from datetime import date

from fastapi import APIRouter, Query, Request

router = APIRouter()


@router.get("/colectivos/recuento")
def recuento(
    request: Request,
    sexo: int | None = Query(None),
    fecha_desde: date | None = Query(None),
    fecha_hasta: date | None = Query(None),
):
    con = request.app.state.con
    total = con.execute(
        """
        SELECT COUNT(DISTINCT p.ipf)
        FROM personas p
        JOIN afiliacion a ON p.ipf = a.ipf
        WHERE (? IS NULL OR p.sexo = ?)
          AND (? IS NULL OR a.fecha_alta_real_afil >= ?)
          AND (? IS NULL OR a.fecha_alta_real_afil <= ?)
        """,
        [sexo, sexo, fecha_desde, fecha_desde, fecha_hasta, fecha_hasta],
    ).fetchone()[0]

    return {
        "filtros": {
            "sexo": sexo,
            "fecha_desde": fecha_desde,
            "fecha_hasta": fecha_hasta,
        },
        "recuento": total,
    }
