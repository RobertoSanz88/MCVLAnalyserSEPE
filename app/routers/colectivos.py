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
    edad_min: int | None = Query(None),
    edad_max: int | None = Query(None),
):
    con = request.app.state.con
    edad = "date_part('year', age(current_date, p.fecha_nacimiento))"
    total = con.execute(
        f"""
        SELECT COUNT(DISTINCT p.ipf)
        FROM personas p
        JOIN afiliacion a ON p.ipf = a.ipf
        WHERE (? IS NULL OR p.sexo = ?)
          AND (? IS NULL OR a.fecha_alta_real_afil >= ?)
          AND (? IS NULL OR a.fecha_alta_real_afil <= ?)
          AND (? IS NULL OR {edad} >= ?)
          AND (? IS NULL OR {edad} <= ?)
        """,
        [
            sexo, sexo,
            fecha_desde, fecha_desde,
            fecha_hasta, fecha_hasta,
            edad_min, edad_min,
            edad_max, edad_max,
        ],
    ).fetchone()[0]

    return {
        "filtros": {
            "sexo": sexo,
            "fecha_desde": fecha_desde,
            "fecha_hasta": fecha_hasta,
            "edad_min": edad_min,
            "edad_max": edad_max,
        },
        "recuento": total,
    }
