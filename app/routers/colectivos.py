"""Constructor de colectivos por cruce de 2 ejes.

Cada eje enfrenta dos opciones y se obtienen 4 subgrupos cruzando el eje A
con el eje B (2x2). Por defecto cada eje sale de un unico filtro y su
complementario (el resto), p.ej.:

    eje A = filtro1: nacionalidad_agrupado in ["espana"] -> espanoles / extranjeros
    eje B = filtro2: sexo in ["2"]                         -> mujeres / hombres

    subgrupos: espanoles+mujeres, espanoles+hombres,
               extranjeros+mujeres, extranjeros+hombres

Opcionalmente, cada eje puede desactivar ese complementario automatico y
usar en su lugar un segundo filtro independiente (filtro3 para el eje A,
filtro4 para el eje B) con SU PROPIO complementario. Cuando eso pasa,
filtro1 (o filtro2) deja de variar: se aplica fijo, siempre cumplido, a
los 4 subgrupos, y el eje pasa a variar segun filtro3 (o filtro4). P.ej.:

    filtro1 = sexo in ["1"] (Hombres), sin contracolectivo -> fijo, siempre Hombres
    filtro3 = tipo_paro in ["LD"]                          -> eje A: ParadosLD / Resto
    filtro2 = nacionalidad in ["espana"] (Espanoles), sin contracolectivo -> fijo
    filtro4 = edad_actual >= 45                            -> eje B: Mayores 45 / Resto

    subgrupos: Hombres+Espanoles+ParadosLD+Mayores45, Hombres+Espanoles+ParadosLD+Resto,
               Hombres+Espanoles+Resto+Mayores45,      Hombres+Espanoles+Resto+Resto

Ver _eje().

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
    filtro3: Filtro | None = None  # si se da, sustituye al complementario automatico de filtro1
    filtro4: Filtro | None = None  # si se da, sustituye al complementario automatico de filtro2


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


def _lado_filtro(filtro: Filtro, columnas_validas: dict[str, str]) -> tuple[tuple, tuple]:
    """Las 2 opciones (SQL where, params, etiqueta) de un filtro y su propio
    complementario automatico. IS TRUE / IS NOT TRUE en vez de NOT(...) para
    que los NULL caigan siempre del lado "no cumple" en vez de desaparecer
    de ambos lados."""
    cond, params = _condicion_sql(filtro, columnas_validas)
    opcion_si = (f"(({cond}) IS TRUE)", params, _etiqueta_filtro(filtro, True))
    opcion_no = (f"(({cond}) IS NOT TRUE)", params, _etiqueta_filtro(filtro, False))
    return opcion_si, opcion_no


def _eje(
    filtro_principal: Filtro, filtro_secundario: Filtro | None, columnas_validas: dict[str, str]
) -> tuple[Filtro | None, tuple[tuple, tuple]]:
    """(filtro_fijo, (opcion_si, opcion_no)) de un eje del cruce.

    Sin filtro_secundario (contracolectivo automatico activo): el eje varia
    entre cumplir/no cumplir filtro_principal, como antes. Con
    filtro_secundario (contracolectivo desactivado): filtro_principal deja
    de variar (se devuelve como filtro_fijo, para aplicarse siempre cumplido
    a los 4 subgrupos) y el eje pasa a variar segun filtro_secundario y SU
    propio complementario."""
    if filtro_secundario is None:
        return None, _lado_filtro(filtro_principal, columnas_validas)
    return filtro_principal, _lado_filtro(filtro_secundario, columnas_validas)


def _calcular_subgrupos(
    con,
    filtro1: Filtro,
    filtro2: Filtro,
    filtro3: Filtro | None = None,
    filtro4: Filtro | None = None,
) -> dict:
    """Cruza eje A (filtro1, o filtro3 con filtro1 fijo) x eje B (filtro2, o
    filtro4 con filtro2 fijo) en 4 subgrupos y calcula recuento + estadisticas
    (numericas y categoricas) de cada uno. Compartido entre el endpoint JSON y
    el de exportacion a Excel para no calcular las cosas de dos formas distintas."""
    columnas_validas = _columnas_personas(con)
    numericas, categoricas = _clasificar_columnas(columnas_validas)

    fijo_a, (eje_a_si, eje_a_no) = _eje(filtro1, filtro3, columnas_validas)
    fijo_b, (eje_b_si, eje_b_no) = _eje(filtro2, filtro4, columnas_validas)

    condiciones_fijas, params_fijos, etiquetas_fijas = [], [], []
    for filtro_fijo in (fijo_a, fijo_b):
        if filtro_fijo is not None:
            cond, params = _condicion_sql(filtro_fijo, columnas_validas)
            condiciones_fijas.append(f"(({cond}) IS TRUE)")
            params_fijos += params
            etiquetas_fijas.append(_etiqueta_filtro(filtro_fijo, True))

    combos = {
        "filtro1_si__filtro2_si": (eje_a_si, eje_b_si),
        "filtro1_si__filtro2_no": (eje_a_si, eje_b_no),
        "filtro1_no__filtro2_si": (eje_a_no, eje_b_si),
        "filtro1_no__filtro2_no": (eje_a_no, eje_b_no),
    }

    resultados = {}
    for nombre, ((parte_a, params_a, etiq_a), (parte_b, params_b, etiq_b)) in combos.items():
        where = " AND ".join(condiciones_fijas + [parte_a, parte_b])
        params = params_fijos + params_a + params_b
        etiqueta = " + ".join(etiquetas_fijas + [etiq_a, etiq_b])
        total = con.execute(f"SELECT COUNT(*) FROM personas WHERE {where}", params).fetchone()[0]
        resultados[nombre] = {
            "etiqueta": etiqueta,
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
        "subgrupos": _calcular_subgrupos(con, body.filtro1, body.filtro2, body.filtro3, body.filtro4),
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
    resultados = _calcular_subgrupos(con, body.filtro1, body.filtro2, body.filtro3, body.filtro4)
    contenido = _construir_excel(resultados)
    return StreamingResponse(
        io.BytesIO(contenido),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=colectivos.xlsx"},
    )
