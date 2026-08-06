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

import io
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from openpyxl import Workbook
from openpyxl.styles import Font
from pydantic import BaseModel

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/colectivos", response_class=HTMLResponse)
def pagina_colectivos(request: Request):
    return templates.TemplateResponse(request, "colectivos.html", {})

ORDEN_SUBGRUPOS = [
    "filtro1_si__filtro2_si",
    "filtro1_si__filtro2_no",
    "filtro1_no__filtro2_si",
    "filtro1_no__filtro2_no",
]

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


@router.get("/colectivos/columnas")
def columnas(request: Request):
    """Columnas disponibles para usar como filtro, separadas por tipo (para el
    selector de atributos del panel izquierdo)."""
    con = request.app.state.con
    columnas_validas = _columnas_personas(con)
    numericas, categoricas = _clasificar_columnas(columnas_validas)
    numericas = numericas + list(COLUMNAS_VIRTUALES.keys())
    return {"numericas": sorted(numericas), "categoricas": sorted(categoricas)}


@router.get("/colectivos/valores/{columna}")
def valores(request: Request, columna: str):
    """Valores distintos de una columna categorica (para el selector de
    categorias al configurar un filtro)."""
    con = request.app.state.con
    columnas_validas = _columnas_personas(con)
    _, categoricas = _clasificar_columnas(columnas_validas)
    if columna not in categoricas:
        raise HTTPException(400, f"'{columna}' no es una columna categorica valida")
    filas = con.execute(
        f"SELECT DISTINCT {columna} FROM personas WHERE {columna} IS NOT NULL ORDER BY {columna}"
    ).fetchall()
    return {"columna": columna, "valores": [fila[0] for fila in filas]}


_OPERADOR_OPUESTO = {">=": "<", ">": "<=", "<=": ">", "<": ">=", "==": "!="}


def _etiqueta_filtro(filtro: Filtro, cumple: bool) -> str:
    """Texto legible para un lado (cumple/no cumple) de un filtro, usado como
    parte del nombre de cada subgrupo en la UI y en el Excel exportado."""
    if filtro.tipo == "numerica":
        operador = filtro.operador if cumple else _OPERADOR_OPUESTO[filtro.operador]
        valor = f"{filtro.valor:g}"
        return f"{filtro.columna} {operador} {valor}"
    if cumple:
        return "/".join(filtro.categorias)
    return f"Resto de {filtro.columna}"


def _calcular_subgrupos(con, filtro1: Filtro, filtro2: Filtro) -> dict:
    """Cruza filtro1 x filtro2 en 4 subgrupos y calcula recuento + estadisticas
    (numericas y categoricas) de cada uno. Compartido entre el endpoint JSON y
    el de exportacion a Excel para no calcular las cosas de dos formas distintas."""
    columnas_validas = _columnas_personas(con)
    numericas, categoricas = _clasificar_columnas(columnas_validas)

    cond1, params1 = _condicion_sql(filtro1, columnas_validas)
    cond2, params2 = _condicion_sql(filtro2, columnas_validas)

    # IS TRUE / IS NOT TRUE en vez de NOT(...) para que los NULL caigan
    # siempre en el "no cumple" en lugar de desaparecer de ambos lados.
    cumple1, no_cumple1 = f"(({cond1}) IS TRUE)", f"(({cond1}) IS NOT TRUE)"
    cumple2, no_cumple2 = f"(({cond2}) IS TRUE)", f"(({cond2}) IS NOT TRUE)"

    combos = {
        "filtro1_si__filtro2_si": (cumple1, True, cumple2, True),
        "filtro1_si__filtro2_no": (cumple1, True, no_cumple2, False),
        "filtro1_no__filtro2_si": (no_cumple1, False, cumple2, True),
        "filtro1_no__filtro2_no": (no_cumple1, False, no_cumple2, False),
    }

    resultados = {}
    for nombre, (parte1, si1, parte2, si2) in combos.items():
        where = f"{parte1} AND {parte2}"
        params = params1 + params2
        total = con.execute(f"SELECT COUNT(*) FROM personas WHERE {where}", params).fetchone()[0]
        resultados[nombre] = {
            "etiqueta": f"{_etiqueta_filtro(filtro1, si1)} + {_etiqueta_filtro(filtro2, si2)}",
            "recuento": total,
            "numericas": _stats_numericas(con, where, params, numericas),
            "categoricas": _stats_categoricas(con, where, params, categoricas, total),
        }
    return resultados


@router.post("/colectivos/subgrupos")
def subgrupos(request: Request, body: SubgruposRequest):
    con = request.app.state.con
    return {
        "filtro1": body.filtro1,
        "filtro2": body.filtro2,
        "subgrupos": _calcular_subgrupos(con, body.filtro1, body.filtro2),
    }


def _construir_excel(resultados: dict) -> bytes:
    """Vuelca los mismos datos de _calcular_subgrupos a un .xlsx: una hoja
    'Numericas' (columna x 4 subgrupos x media/mediana/desviacion) y una hoja
    'Categoricas' con una tabla apilada por columna categorica (subgrupo con
    Conteo/Porcentaje por cada uno), igual que el formato ya usado a mano por
    el Observatorio - hueco en blanco si una categoria no aparece en algun
    subgrupo, fila en blanco entre columnas."""
    negrita = Font(bold=True)
    wb = Workbook()

    # --- Hoja Numericas ---
    ws_num = wb.active
    ws_num.title = "Numericas"
    ws_num.cell(row=1, column=1, value="Columna").font = negrita

    columnas_numericas = [d["columna"] for d in resultados[ORDEN_SUBGRUPOS[0]]["numericas"]]

    col = 2
    for nombre in ORDEN_SUBGRUPOS:
        ws_num.merge_cells(start_row=1, start_column=col, end_row=1, end_column=col + 2)
        ws_num.cell(row=1, column=col, value=resultados[nombre]["etiqueta"]).font = negrita
        for offset, titulo in enumerate(["Media", "Mediana", "Desv. tipica"]):
            ws_num.cell(row=2, column=col + offset, value=titulo).font = negrita
        col += 3

    for i, nombre_col in enumerate(columnas_numericas):
        fila = i + 3
        ws_num.cell(row=fila, column=1, value=nombre_col)
        col = 2
        for nombre in ORDEN_SUBGRUPOS:
            stats = resultados[nombre]["numericas"][i]
            ws_num.cell(row=fila, column=col, value=stats["media"])
            ws_num.cell(row=fila, column=col + 1, value=stats["mediana"])
            ws_num.cell(row=fila, column=col + 2, value=stats["desviacion"])
            col += 3

    # --- Hoja Categoricas ---
    ws_cat = wb.create_sheet("Categoricas")
    columnas_categoricas = [d["columna"] for d in resultados[ORDEN_SUBGRUPOS[0]]["categoricas"]]

    fila = 1
    for idx_col, nombre_col in enumerate(columnas_categoricas):
        # Union de valores presentes en cualquiera de los 4 subgrupos (orden de aparicion).
        mapas, vistos, vistos_set = {}, [], set()
        for nombre in ORDEN_SUBGRUPOS:
            entrada = resultados[nombre]["categoricas"][idx_col]
            mapas[nombre] = {v["valor"]: v for v in entrada["valores"]}
            for v in entrada["valores"]:
                if v["valor"] not in vistos_set:
                    vistos_set.add(v["valor"])
                    vistos.append(v["valor"])

        col = 2
        for nombre in ORDEN_SUBGRUPOS:
            ws_cat.merge_cells(start_row=fila, start_column=col, end_row=fila, end_column=col + 1)
            ws_cat.cell(row=fila, column=col, value=resultados[nombre]["etiqueta"]).font = negrita
            col += 2
        fila += 1

        ws_cat.cell(row=fila, column=1, value=nombre_col).font = negrita
        col = 2
        for _ in ORDEN_SUBGRUPOS:
            ws_cat.cell(row=fila, column=col, value="Conteo").font = negrita
            ws_cat.cell(row=fila, column=col + 1, value="Porcentaje (%)").font = negrita
            col += 2
        fila += 1

        for valor in vistos:
            ws_cat.cell(row=fila, column=1, value=str(valor))
            col = 2
            for nombre in ORDEN_SUBGRUPOS:
                entrada = mapas[nombre].get(valor)
                if entrada is not None:
                    ws_cat.cell(row=fila, column=col, value=entrada["conteo"])
                    ws_cat.cell(row=fila, column=col + 1, value=entrada["porcentaje"])
                col += 2
            fila += 1

        fila += 1  # fila en blanco entre columnas

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


@router.post("/colectivos/exportar")
def exportar(request: Request, body: SubgruposRequest):
    con = request.app.state.con
    resultados = _calcular_subgrupos(con, body.filtro1, body.filtro2)
    contenido = _construir_excel(resultados)
    return StreamingResponse(
        io.BytesIO(contenido),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=colectivos.xlsx"},
    )
