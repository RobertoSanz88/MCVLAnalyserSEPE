"""Constructor de colectivos por cruce de 2 filtros.

Cada filtro define un colectivo (columna + criterio) y su complementario
(el resto). Cruzando 2 filtros se obtienen 4 subgrupos, p.ej.:

    filtro1 = nacionalidad_agrupado in ["espana"]   -> espanoles / extranjeros
    filtro2 = sexo in ["2"]                          -> mujeres / hombres

    subgrupos: espanoles+mujeres, espanoles+hombres,
               extranjeros+mujeres, extranjeros+hombres

Un filtro numerico usa un operador de comparacion (umbral y su
complementario); un filtro categorico usa un conjunto de valores (esos
valores y su complementario).

Para cada subgrupo se calculan ademas estadisticas del resto de columnas
de personas: numericas (media, desviacion, mediana) y categoricas
(conteo y porcentaje por valor). Las columnas de fecha y las de tipo
lista (historicos por anyo) no tienen una estadistica directa asi que se
omiten.
"""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()

# Columnas derivadas que no existen tal cual en el Parquet pero son utiles
# como filtro (p.ej. "mayores de 45 anos" no es una columna real).
COLUMNAS_VIRTUALES = {
    "edad_actual": "date_part('year', age(current_date, fecha_nacimiento))",
}

# Clasificacion de columnas de personas para las estadisticas por subgrupo.
# TINYINT son codigos (sexo, provincia...) -> se tratan como categoricas,
# no como numeros con los que tenga sentido calcular una media.
TIPOS_NUMERICOS = {"DOUBLE", "INTEGER", "BIGINT", "FLOAT", "SMALLINT", "HUGEINT"}
TIPOS_CATEGORICOS = {"VARCHAR", "BOOLEAN", "TINYINT"}


def _clasificar_columnas(columnas: dict[str, str]) -> tuple[list[str], list[str]]:
    numericas, categoricas = [], []
    for nombre, tipo in columnas.items():
        if tipo in TIPOS_NUMERICOS:
            numericas.append(nombre)
        elif tipo in TIPOS_CATEGORICOS:
            categoricas.append(nombre)
        # TIMESTAMP y los tipos lista (VARCHAR[], BIGINT[]) se omiten.
    return numericas, categoricas


class FiltroNumerico(BaseModel):
    columna: str
    tipo: Literal["numerica"] = "numerica"
    operador: Literal[">=", ">", "<=", "<", "=="]
    valor: float


class FiltroCategorico(BaseModel):
    columna: str
    tipo: Literal["categorica"] = "categorica"
    categorias: list[str]


Filtro = FiltroNumerico | FiltroCategorico


class SubgruposRequest(BaseModel):
    filtro1: Filtro
    filtro2: Filtro


def _columnas_personas(con) -> dict[str, str]:
    filas = con.execute("DESCRIBE personas").fetchall()
    return {nombre: tipo for nombre, tipo, *_ in filas}


def _columna_sql(nombre: str, columnas_validas: dict[str, str]) -> str:
    if nombre in COLUMNAS_VIRTUALES:
        return COLUMNAS_VIRTUALES[nombre]
    if nombre not in columnas_validas:
        raise HTTPException(400, f"Columna desconocida: {nombre}")
    return nombre


def _condicion_sql(filtro: Filtro, columnas_validas: dict[str, str]) -> tuple[str, list]:
    col = _columna_sql(filtro.columna, columnas_validas)
    if filtro.tipo == "numerica":
        return f"{col} {filtro.operador} ?", [filtro.valor]
    placeholders = ", ".join("?" for _ in filtro.categorias)
    if not filtro.categorias:
        raise HTTPException(400, "El filtro categorico necesita al menos una categoria")
    return f"{col} IN ({placeholders})", list(filtro.categorias)


def _stats_numericas(con, where: str, params: list, columnas: list[str]) -> list[dict]:
    if not columnas:
        return []

    def _limpio(c: str) -> str:
        # Algunos promedios del ETL (num_altas_anual_promedio y similares) dan
        # inf cuando dividen por 0 anyos. STDDEV_SAMP desborda con inf/nan de
        # por medio, asi que se tratan como NULL en las estadisticas.
        d = f"CAST({c} AS DOUBLE)"
        return f"CASE WHEN isinf({d}) OR isnan({d}) THEN NULL ELSE {d} END"

    select = ", ".join(
        f"AVG({_limpio(c)}), STDDEV_SAMP({_limpio(c)}), MEDIAN({_limpio(c)})" for c in columnas
    )
    fila = con.execute(f"SELECT {select} FROM personas WHERE {where}", params).fetchone()
    return [
        {
            "columna": columnas[i],
            "media": fila[i * 3],
            "desviacion": fila[i * 3 + 1],
            "mediana": fila[i * 3 + 2],
        }
        for i in range(len(columnas))
    ]


def _stats_categoricas(con, where: str, params: list, columnas: list[str], total: int) -> list[dict]:
    resultado = []
    for c in columnas:
        filas = con.execute(
            f"SELECT {c}, COUNT(*) FROM personas WHERE {where} GROUP BY {c} ORDER BY 2 DESC",
            params,
        ).fetchall()
        resultado.append(
            {
                "columna": c,
                "valores": [
                    {
                        "valor": valor,
                        "conteo": conteo,
                        "porcentaje": round(100 * conteo / total, 1) if total else 0.0,
                    }
                    for valor, conteo in filas
                ],
            }
        )
    return resultado


@router.post("/colectivos/subgrupos")
def subgrupos(request: Request, body: SubgruposRequest):
    con = request.app.state.con
    columnas_validas = _columnas_personas(con)
    numericas, categoricas = _clasificar_columnas(columnas_validas)

    cond1, params1 = _condicion_sql(body.filtro1, columnas_validas)
    cond2, params2 = _condicion_sql(body.filtro2, columnas_validas)

    # IS TRUE / IS NOT TRUE en vez de NOT(...) para que los NULL caigan
    # siempre en el "no cumple" en lugar de desaparecer de ambos lados.
    cumple1, no_cumple1 = f"(({cond1}) IS TRUE)", f"(({cond1}) IS NOT TRUE)"
    cumple2, no_cumple2 = f"(({cond2}) IS TRUE)", f"(({cond2}) IS NOT TRUE)"

    combos = {
        "filtro1_si__filtro2_si": (cumple1, cumple2),
        "filtro1_si__filtro2_no": (cumple1, no_cumple2),
        "filtro1_no__filtro2_si": (no_cumple1, cumple2),
        "filtro1_no__filtro2_no": (no_cumple1, no_cumple2),
    }

    resultados = {}
    for nombre, (parte1, parte2) in combos.items():
        where = f"{parte1} AND {parte2}"
        params = params1 + params2
        total = con.execute(f"SELECT COUNT(*) FROM personas WHERE {where}", params).fetchone()[0]
        resultados[nombre] = {
            "recuento": total,
            "numericas": _stats_numericas(con, where, params, numericas),
            "categoricas": _stats_categoricas(con, where, params, categoricas, total),
        }

    return {
        "filtro1": body.filtro1,
        "filtro2": body.filtro2,
        "subgrupos": resultados,
    }
