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
    filtro4 = edad_actual_fecha_corte >= 45                -> eje B: Mayores 45 / Resto

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

from config import CATEGORICAS_COLECTIVOS

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

# Columnas numericas (por tipo DuckDB) que se excluyen de "Estadisticas
# numericas": no son caracteristicas calculadas, sino el identificador o
# columnas originales de Personas (anteriores a las fases 1-3 del ETL) que
# resultan ser numericas por tipo pero no tienen sentido como media (un
# codigo de provincia o una fecha guardada como entero).
COLUMNAS_NUMERICAS_EXCLUIDAS = {"ipf", "domic_resid_habitual", "fecha_fallecimiento"}

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


def _categoricas_visibles(categoricas_disponibles: list[str]) -> list[tuple[str, int | None]]:
    """Cruza CATEGORICAS_COLECTIVOS (config.py) con las columnas categoricas
    realmente disponibles en el Parquet, resolviendo prefijos tipo
    "cobra_pension_*" (columnas cuyo nombre cambia segun el anyo de la MCVL
    procesada). Entradas de config.py sin columna correspondiente se
    ignoran en silencio - permite versionar un config.py valido para varios
    anyos de datos, cada uno con nombres ligeramente distintos."""
    disponibles = set(categoricas_disponibles)
    resultado = []
    for entrada in CATEGORICAS_COLECTIVOS:
        nombre_config, limite = entrada["columna"], entrada.get("limite")
        if nombre_config.endswith("*"):
            prefijo = nombre_config[:-1]
            resultado += [(c, limite) for c in sorted(disponibles) if c.startswith(prefijo)]
        elif nombre_config in disponibles:
            resultado.append((nombre_config, limite))
    return resultado


def _stats_categoricas(
    con, where: str, params: list, columnas: list[tuple[str, int | None]], total: int
) -> list[dict]:
    resultado = []
    for c, limite in columnas:
        limit_sql = f" LIMIT {int(limite)}" if limite else ""
        filas = con.execute(
            f"SELECT {c}, COUNT(*) FROM personas WHERE {where} GROUP BY {c} ORDER BY 2 DESC{limit_sql}",
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
    selector de atributos del panel izquierdo). "ipf" es el identificador de
    persona (clave de union entre tablas), no un atributo por el que tenga
    sentido filtrar o construir un colectivo."""
    con = request.app.state.con
    columnas_validas = _columnas_personas(con)
    numericas, categoricas = _clasificar_columnas(columnas_validas)
    numericas = [c for c in numericas if c != "ipf"]
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


def _valor_str(v) -> str:
    # bool en minusculas ("true"/"false"), igual que el checkbox en el
    # frontend (JS stringifica un booleano en minusculas); el resto de tipos
    # (texto, codigos) se deja tal cual, sin tocar mayusculas/minusculas.
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


# Columnas que se etiquetan como binarias aunque tengan mas valores en el
# Parquet: el resto de valores de la columna (aqui, los "NO CONSTA" de sexo)
# no aparecen aparte, se pliegan en silencio dentro del lado complementario
# (ya ocurre asi en el SQL sin tocar nada: el complementario de "sexo
# Hombre" es "NOT (sexo IN ('Hombre'))", que ya incluye Mujer Y NO CONSTA
# de forma natural). Este diccionario solo cambia la ETIQUETA de ese lado,
# de "Resto sexo Hombre" a "sexo Mujer". Decision explicita del usuario
# (2026-08-07), pensada para sexo en concreto.
PARES_BINARIOS_ESPECIALES: dict[str, tuple[str, str]] = {
    "sexo": ("Hombre", "Mujer"),
}


def _valor_complementario_categorico(con, filtro: FiltroCategorico) -> str | None:
    """Si se selecciono solo 1 categoria como colectivo, devuelve el otro
    valor de una alternativa binaria (p.ej. "NO" para un filtro "SI") para
    usarlo como etiqueta del lado "no cumple" en vez del generico "Resto
    columna valor" - mas claro cuando hay una alternativa binaria natural.
    Dos formas de llegar a esa alternativa:
    - PARES_BINARIOS_ESPECIALES: columna tratada como binaria por decision
      explicita aunque tenga mas valores (ver comentario arriba).
    - Genérico: la columna tiene EXACTAMENTE 2 valores distintos en los
      datos.
    None si no aplica (mas de una categoria seleccionada, o ninguna de las
    dos vias anteriores encaja)."""
    if len(filtro.categorias) != 1:
        return None
    col = filtro.columna
    seleccionado = filtro.categorias[0]

    par = PARES_BINARIOS_ESPECIALES.get(col)
    if par is not None:
        if seleccionado == par[0]:
            return par[1]
        if seleccionado == par[1]:
            return par[0]
        return None  # categoria fuera del par (p.ej. NO CONSTA solo): sin atajo, usa el fallback generico

    total = con.execute(
        f"SELECT COUNT(DISTINCT {col}) FROM personas WHERE {col} IS NOT NULL"
    ).fetchone()[0]
    if total != 2:
        return None
    fila = con.execute(
        f"SELECT DISTINCT {col} FROM personas WHERE {col} IS NOT NULL AND {col} NOT IN (?)",
        list(filtro.categorias),
    ).fetchone()
    return _valor_str(fila[0]) if fila else None


def _etiqueta_filtro(con, filtro: Filtro, cumple: bool) -> str:
    """Texto legible para un lado (cumple/no cumple) de un filtro, usado como
    parte del nombre de cada subgrupo en la UI y en el Excel exportado.
    Incluye siempre el nombre de columna (tambien en el lado "cumple" de un
    filtro categorico) porque una cabecera puede combinar varios filtros y
    "SI" o "TRUE" solos no dicen a que columna pertenecen."""
    if filtro.tipo == "numerica":
        operador = filtro.operador if cumple else _OPERADOR_OPUESTO[filtro.operador]
        valor = f"{filtro.valor:g}"
        return f"{filtro.columna} {operador} {valor}"
    valores = "/".join(filtro.categorias)
    if cumple:
        return f"{filtro.columna} {valores}"
    complementario = _valor_complementario_categorico(con, filtro)
    if complementario is not None:
        return f"{filtro.columna} {complementario}"
    return f"Resto {filtro.columna} {valores}"


def _lado_filtro(con, filtro: Filtro, columnas_validas: dict[str, str]) -> tuple[tuple, tuple]:
    """Las 2 opciones (SQL where, params, etiqueta) de un filtro y su propio
    complementario automatico. IS TRUE / IS NOT TRUE en vez de NOT(...) para
    que los NULL caigan siempre del lado "no cumple" en vez de desaparecer
    de ambos lados."""
    cond, params = _condicion_sql(filtro, columnas_validas)
    opcion_si = (f"(({cond}) IS TRUE)", params, _etiqueta_filtro(con, filtro, True))
    opcion_no = (f"(({cond}) IS NOT TRUE)", params, _etiqueta_filtro(con, filtro, False))
    return opcion_si, opcion_no


def _eje(
    con, filtro_principal: Filtro, filtro_secundario: Filtro | None, columnas_validas: dict[str, str]
) -> tuple[Filtro | None, tuple[tuple, tuple]]:
    """(filtro_fijo, (opcion_si, opcion_no)) de un eje del cruce.

    Sin filtro_secundario (contracolectivo automatico activo): el eje varia
    entre cumplir/no cumplir filtro_principal, como antes. Con
    filtro_secundario (contracolectivo desactivado): filtro_principal deja
    de variar (se devuelve como filtro_fijo, para aplicarse siempre cumplido
    a los 4 subgrupos) y el eje pasa a variar segun filtro_secundario y SU
    propio complementario."""
    if filtro_secundario is None:
        return None, _lado_filtro(con, filtro_principal, columnas_validas)
    return filtro_principal, _lado_filtro(con, filtro_secundario, columnas_validas)


def calcular_condiciones_subgrupos(
    con,
    filtro1: Filtro,
    filtro2: Filtro,
    filtro3: Filtro | None = None,
    filtro4: Filtro | None = None,
) -> dict[str, dict]:
    """Cruza eje A (filtro1, o filtro3 con filtro1 fijo) x eje B (filtro2, o
    filtro4 con filtro2 fijo) en los 4 subgrupos (ORDEN_SUBGRUPOS) y devuelve,
    para cada uno, el WHERE SQL sobre la vista `personas` (con sus parametros
    ?) y su etiqueta legible - sin calcular todavia ninguna estadistica.
    Extraido de _calcular_subgrupos para que /trayectorias/colectivo pueda
    reutilizar exactamente la misma definicion de colectivo (mismos 4
    subgrupos, mismas etiquetas) en vez de reimplementar el cruce de ejes."""
    columnas_validas = _columnas_personas(con)
    fijo_a, (eje_a_si, eje_a_no) = _eje(con, filtro1, filtro3, columnas_validas)
    fijo_b, (eje_b_si, eje_b_no) = _eje(con, filtro2, filtro4, columnas_validas)

    condiciones_fijas, params_fijos, etiquetas_fijas = [], [], []
    for filtro_fijo in (fijo_a, fijo_b):
        if filtro_fijo is not None:
            cond, params = _condicion_sql(filtro_fijo, columnas_validas)
            condiciones_fijas.append(f"(({cond}) IS TRUE)")
            params_fijos += params
            etiquetas_fijas.append(_etiqueta_filtro(con, filtro_fijo, True))

    combos = {
        "filtro1_si__filtro2_si": (eje_a_si, eje_b_si),
        "filtro1_si__filtro2_no": (eje_a_si, eje_b_no),
        "filtro1_no__filtro2_si": (eje_a_no, eje_b_si),
        "filtro1_no__filtro2_no": (eje_a_no, eje_b_no),
    }

    resultado = {}
    for nombre, ((parte_a, params_a, etiq_a), (parte_b, params_b, etiq_b)) in combos.items():
        where = " AND ".join(condiciones_fijas + [parte_a, parte_b])
        params = params_fijos + params_a + params_b
        etiqueta = " + ".join(etiquetas_fijas + [etiq_a, etiq_b])
        resultado[nombre] = {"where": where, "params": params, "etiqueta": etiqueta}
    return resultado


def _calcular_subgrupos(
    con,
    filtro1: Filtro,
    filtro2: Filtro,
    filtro3: Filtro | None = None,
    filtro4: Filtro | None = None,
) -> dict:
    """Calcula recuento + estadisticas (numericas y categoricas) de cada uno de
    los 4 subgrupos. Compartido entre el endpoint JSON y el de exportacion a
    Excel para no calcular las cosas de dos formas distintas."""
    columnas_validas = _columnas_personas(con)
    numericas, categoricas = _clasificar_columnas(columnas_validas)
    numericas = [c for c in numericas if c not in COLUMNAS_NUMERICAS_EXCLUIDAS]
    categoricas_visibles = _categoricas_visibles(categoricas)

    condiciones = calcular_condiciones_subgrupos(con, filtro1, filtro2, filtro3, filtro4)

    resultados = {}
    for nombre, c in condiciones.items():
        where, params, etiqueta = c["where"], c["params"], c["etiqueta"]
        total = con.execute(f"SELECT COUNT(*) FROM personas WHERE {where}", params).fetchone()[0]
        resultados[nombre] = {
            "etiqueta": etiqueta,
            "recuento": total,
            "numericas": _stats_numericas(con, where, params, numericas),
            "categoricas": _stats_categoricas(con, where, params, categoricas_visibles, total),
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
