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
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from openpyxl import Workbook
from openpyxl.styles import Font
from pydantic import BaseModel

from config import CATEGORICAS_COLECTIVOS

router = APIRouter()
# Ruta relativa al propio fichero (no al directorio de trabajo del proceso):
# en dev coincide con "app/templates", pero en el .exe empaquetado con
# PyInstaller el CWD no esta garantizado (solo el .bat de dev fuerza cd a la
# raiz del repo) - mismo criterio que ya usa el mount de /static en main.py.
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")


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
    con = request.app.state.con.cursor()
    columnas_validas = _columnas_personas(con)
    numericas, categoricas = _clasificar_columnas(columnas_validas)
    numericas = [c for c in numericas if c != "ipf"]
    return {"numericas": sorted(numericas), "categoricas": sorted(categoricas)}


@router.get("/colectivos/valores/{columna}")
def valores(request: Request, columna: str):
    """Valores distintos de una columna categorica (para el selector de
    categorias al configurar un filtro)."""
    con = request.app.state.con.cursor()
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


# "Grupo" unico que representa TODA la tabla `personas`, sin filtrar - modo
# "Filtro de colectivos = No" del switch global (2026-09-09, pestana
# "Seleccion de colectivo"). Mismo shape ({where, params, etiqueta}) que cada
# entrada de calcular_condiciones_subgrupos, para que _calcular_estadisticas
# (y todo lo que la usa: tablas, graficos, Excel) no necesite saber si esta
# trabajando con 1 grupo o con 4 - simplemente itera lo que le llega.
GRUPO_SIN_FILTRO = "toda_mcvl"


def condiciones_sin_filtro() -> dict[str, dict]:
    return {GRUPO_SIN_FILTRO: {"where": "TRUE", "params": [], "etiqueta": "Toda la MCVL"}}


def _calcular_estadisticas(con, condiciones: dict, sin_limite_categorico: bool = False) -> dict:
    """Recuento + estadisticas (numericas y categoricas) de cada grupo de
    `condiciones` (1 o N, ver GRUPO_SIN_FILTRO/calcular_condiciones_subgrupos).
    Compartido entre los endpoints JSON y los de exportacion a Excel (con o
    sin filtro) para no calcular las cosas de dos formas distintas.

    sin_limite_categorico=True (solo lo usan los endpoints de exportacion,
    2026-09-10, decision explicita del usuario) ignora el "limite" de
    CATEGORICAS_COLECTIVOS (config.py, hoy 15 para prov_trabajo_ppal y
    act_econ_ppal): el grafico/tabla en pantalla se sigue limitando para no
    desbordar, pero el Excel exportado lleva siempre todas las categorias."""
    columnas_validas = _columnas_personas(con)
    numericas, categoricas = _clasificar_columnas(columnas_validas)
    numericas = [c for c in numericas if c not in COLUMNAS_NUMERICAS_EXCLUIDAS]
    categoricas_visibles = _categoricas_visibles(categoricas)
    if sin_limite_categorico:
        categoricas_visibles = [(c, None) for c, _ in categoricas_visibles]

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


def _calcular_subgrupos(
    con,
    filtro1: Filtro,
    filtro2: Filtro,
    filtro3: Filtro | None = None,
    filtro4: Filtro | None = None,
    sin_limite_categorico: bool = False,
) -> dict:
    """Calcula recuento + estadisticas de cada uno de los 4 subgrupos (ver
    _calcular_estadisticas). Compartido entre el endpoint JSON y el de
    exportacion a Excel."""
    condiciones = calcular_condiciones_subgrupos(con, filtro1, filtro2, filtro3, filtro4)
    return _calcular_estadisticas(con, condiciones, sin_limite_categorico)


@router.post("/colectivos/recuentos")
def recuentos(request: Request, body: SubgruposRequest):
    """Version ligera de /colectivos/subgrupos: solo etiqueta + recuento de
    cada subgrupo, sin las estadisticas numericas/categoricas (mucho mas
    caras de calcular). Pensada para refrescarse en vivo mientras se
    configuran los filtros en el frontend, antes de pulsar Calcular."""
    con = request.app.state.con.cursor()
    condiciones = calcular_condiciones_subgrupos(con, body.filtro1, body.filtro2, body.filtro3, body.filtro4)
    resultado = {}
    for nombre, c in condiciones.items():
        total = con.execute(f"SELECT COUNT(*) FROM personas WHERE {c['where']}", c["params"]).fetchone()[0]
        resultado[nombre] = {"etiqueta": c["etiqueta"], "recuento": total}
    return resultado


@router.post("/colectivos/subgrupos")
def subgrupos(request: Request, body: SubgruposRequest):
    con = request.app.state.con.cursor()
    return {
        "filtro1": body.filtro1,
        "filtro2": body.filtro2,
        "subgrupos": _calcular_subgrupos(con, body.filtro1, body.filtro2, body.filtro3, body.filtro4),
    }


# "Filtro de colectivos" en No (switch global, pestana "Seleccion de
# colectivo", 2026-09-09): "Analisis de colectivos" pasa a analizar TODA la
# MCVL sin filtrar, un unico grupo en vez de 4 subgrupos - sin filtro1/filtro2
# que mandar, por eso GET sin cuerpo (a diferencia de /colectivos/subgrupos).
@router.get("/colectivos/subgrupos-sin-filtro")
def subgrupos_sin_filtro(request: Request):
    con = request.app.state.con.cursor()
    return {"subgrupos": _calcular_estadisticas(con, condiciones_sin_filtro())}


@router.get("/colectivos/exportar-sin-filtro")
def exportar_sin_filtro(request: Request):
    con = request.app.state.con.cursor()
    resultados = _calcular_estadisticas(con, condiciones_sin_filtro(), sin_limite_categorico=True)
    contenido = _construir_excel(resultados, grupos=[GRUPO_SIN_FILTRO])
    return StreamingResponse(
        io.BytesIO(contenido),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=colectivos.xlsx"},
    )


# ---------------------------------------------------------------------------
# Pestana "Cruce de variables" (v1.3, 2026-09-09, decision explicita del
# usuario): cruza 2 variables cualesquiera de tabla_personas_extendida (mismo
# universo que /colectivos/columnas - una fila por persona, cruzar es
# directo). Los 4 subgrupos se calculan de una vez en la misma respuesta
# (igual patron que la trayectoria categorica de Afiliacion en
# trayectorias.py): el frontend cambia de subgrupo mostrado con un selector
# de chips, sin volver a pedir al backend.
#
# 3 combinaciones posibles segun el tipo DuckDB de cada variable
# (_clasificar_columnas, igual criterio que el resto de "Colectivos"). NINGUNA
# reordena ejes (2026-09-10, decision explicita del usuario: revierte reglas
# de eje anteriores de este mismo dia 09 - "Variable eje X"/"Variable eje Y"
# en el frontend, antes "Variable 1"/"Variable 2", mapean SIEMPRE directo a
# su eje, la elija el usuario en el orden que la elija):
# - numerica x numerica: Variable eje X al eje X, Variable eje Y al eje Y -
#   esto nunca cambio, no habia reordenacion desde el principio (no hay un
#   criterio objetivo tipo "mas categorias" para 2 numericas). Densidad 2D
#   (rejilla de NBINS_DENSIDAD x NBINS_DENSIDAD, contando personas por celda)
#   en vez de un punto por persona - con hasta ~1,3M personas un scatter de
#   puntos individuales bloquearia el navegador (decision explicita del
#   usuario). Los bordes de la rejilla se calculan sobre el MIN/MAX de toda
#   la tabla (sin filtrar), no por subgrupo, para que los 4 subgrupos
#   compartan la misma escala de ejes y sean comparables entre si al cambiar
#   el selector.
# - categorica x categorica: tabla de contingencia (conteo por cada par de
#   categorias). Los ejes (lista de categorias) tambien se fijan sobre TODA
#   la tabla, mismo motivo que arriba. Eje X = Variable eje X, eje Y =
#   Variable eje Y, sin excepcion (hasta el 2026-09-10 el eje X era la
#   variable con MAS categorias, revertido a peticion del usuario).
# - numerica x categorica: boxplot - resumen por categoria (n, min, max,
#   cuartiles) calculado en SQL, nunca se envian los valores individuales al
#   frontend. El eje que le toque a cada variable (numerica o categorica) lo
#   decide el usuario con Variable eje X/Y, no un criterio fijo - el frontend
#   dibuja el boxplot VERTICAL (categorica en X) u HORIZONTAL (categorica en
#   Y) segun toque, ver variable1_numerica en la respuesta y
#   dibujarCruceBoxplot en colectivos.html (hasta el 2026-09-10 la categorica
#   iba siempre al eje X, revertido a peticion del usuario).
#
# TOP_N_CATEGORICO: mismo limite (20) que ya usa "cnae2009_cc" en
# VARIABLES_CATEGORICAS (app/routers/trayectorias.py) para no desbordar el
# grafico con una variable de muchas categorias (CNAE, causa de baja...) -
# aplica tanto a categorica x categorica (cada eje por separado) como al eje
# categorico de numerica x categorica. Igual que alli, el top-N se calcula
# sobre TODA la tabla (sin filtrar por subgrupo) para que "Resto" signifique
# lo mismo pase lo que pase se filtre despues.
NBINS_DENSIDAD = 20
TOP_N_CATEGORICO = 20


def _top_categorias(con, columna: str, top_n: int | None = TOP_N_CATEGORICO) -> tuple[list, bool]:
    """Hasta top_n valores mas frecuentes de `columna` en toda la tabla
    `personas`. Devuelve (valores, hay_resto) - hay_resto=True si la columna
    tiene mas de top_n valores distintos y hace falta una categoria "Resto"
    para el resto. top_n=None desactiva el limite (usado en la exportacion a
    Excel, ver _construir_excel_cruce): todas las categorias, sin "Resto"."""
    filas = con.execute(
        f"SELECT {columna}, COUNT(*) AS n FROM personas WHERE {columna} IS NOT NULL "
        f"GROUP BY 1 ORDER BY n DESC, {columna} ASC"
    ).fetchall()
    valores = [f[0] for f in filas]
    if top_n is None or len(valores) <= top_n:
        return valores, False
    return valores[:top_n], True


class CruceRequest(SubgruposRequest):
    variable1: str
    variable2: str


# Percentil bajo/alto usado como bordes de rejilla de densidad/histograma en
# vez del MIN/MAX real (2026-09-09, decision explicita del usuario tras ver
# Ingresos_bruto_anual_2024 con un valor de 647.000€: con MIN/MAX real, un
# solo valor extremo estira los bordes y aplasta el resto de los NBINS_*
# practicamente vacios contra un lado del grafico). Los valores fuera de
# [p1, p99] NO se pierden - el mismo LEAST/GREATEST que ya recortaba el
# indice de bin a [0, NBINS-1] los pliega solos en el primer/ultimo bin, sin
# tocar esa parte de la logica.
PERCENTIL_RANGO_BAJO = 0.01
PERCENTIL_RANGO_ALTO = 0.99


def _rango_percentiles(con, columna: str) -> tuple:
    """(percentil 1, percentil 99) de `columna` en toda la tabla `personas`
    (sin filtrar por subgrupo, mismo motivo que _top_categorias: que los 4
    subgrupos compartan siempre la misma escala de eje)."""
    return con.execute(
        f"SELECT QUANTILE_CONT({columna}, {PERCENTIL_RANGO_BAJO}), "
        f"QUANTILE_CONT({columna}, {PERCENTIL_RANGO_ALTO}) "
        f"FROM personas WHERE {columna} IS NOT NULL"
    ).fetchone()


def _cruce_numerica_numerica(con, condiciones: dict, v1: str, v2: str) -> dict:
    min1, max1 = _rango_percentiles(con, v1)
    min2, max2 = _rango_percentiles(con, v2)

    resultado = {}
    # Si alguna variable no tiene rango (todo NULL, o un unico valor
    # constante en toda la tabla) no hay rejilla que construir - 4 subgrupos
    # vacios en vez de dividir por 0.
    sin_rango = min1 is None or min2 is None or max1 == min1 or max2 == min2
    ancho1 = None if sin_rango else (max1 - min1) / NBINS_DENSIDAD
    ancho2 = None if sin_rango else (max2 - min2) / NBINS_DENSIDAD
    centros_x = [] if sin_rango else [min1 + ancho1 * (i + 0.5) for i in range(NBINS_DENSIDAD)]
    centros_y = [] if sin_rango else [min2 + ancho2 * (i + 0.5) for i in range(NBINS_DENSIDAD)]

    for nombre, c in condiciones.items():
        where, params, etiqueta = c["where"], c["params"], c["etiqueta"]
        total = con.execute(f"SELECT COUNT(*) FROM personas WHERE {where}", params).fetchone()[0]
        z = [[0] * NBINS_DENSIDAD for _ in range(NBINS_DENSIDAD)]
        if not sin_rango:
            filas = con.execute(
                f"""
                WITH bineado AS (
                    SELECT
                        LEAST({NBINS_DENSIDAD - 1}, GREATEST(0,
                            CAST(({v1} - {min1}) / {ancho1} AS INTEGER))) AS bin_x,
                        LEAST({NBINS_DENSIDAD - 1}, GREATEST(0,
                            CAST(({v2} - {min2}) / {ancho2} AS INTEGER))) AS bin_y
                    FROM personas
                    WHERE {where} AND {v1} IS NOT NULL AND {v2} IS NOT NULL
                )
                SELECT bin_x, bin_y, COUNT(*) FROM bineado GROUP BY 1, 2
                """,
                params,
            ).fetchall()
            for bin_x, bin_y, n in filas:
                z[bin_y][bin_x] = n
        resultado[nombre] = {"etiqueta": etiqueta, "recuento": total, "x": centros_x, "y": centros_y, "z": z}
    return resultado


def _cruce_categorica_categorica(
    con, condiciones: dict, v1: str, v2: str, top_n: int | None = TOP_N_CATEGORICO
) -> tuple[str, str, dict]:
    # Eje X = v1, eje Y = v2, SIEMPRE (2026-09-10, decision explicita del
    # usuario: revierte la regla anterior "eje X = la variable con mas
    # categorias" - ahora "Variable eje X"/"Variable eje Y" en el frontend
    # mapean directo a su eje, sin reordenar). v1/v2 se siguen devolviendo
    # en la tupla de salida por compatibilidad con el resto de la funcion,
    # pero ya nunca cambian de valor aqui dentro.
    #
    # top_n=None (exportacion a Excel, 2026-09-10, decision explicita del
    # usuario): sin limite de categorias por eje, sin "Resto" - una hoja de
    # calculo aguanta una rejilla de cientos de filas/columnas sin problema,
    # a diferencia del grafico en pantalla (ver TOP_N_CATEGORICO).
    cat1, resto1 = _top_categorias(con, v1, top_n)
    cat2, resto2 = _top_categorias(con, v2, top_n)
    etiqueta_resto = "Resto"
    ejes1 = cat1 + ([etiqueta_resto] if resto1 else [])
    ejes2 = cat2 + ([etiqueta_resto] if resto2 else [])

    set1, set2 = set(cat1), set(cat2)
    idx1 = {v: i for i, v in enumerate(ejes1)}
    idx2 = {v: i for i, v in enumerate(ejes2)}

    def _normaliza(valor, categorias_top, hay_resto):
        return valor if (not hay_resto or valor in categorias_top) else etiqueta_resto

    resultado = {}
    for nombre, c in condiciones.items():
        where, params, etiqueta = c["where"], c["params"], c["etiqueta"]
        total = con.execute(f"SELECT COUNT(*) FROM personas WHERE {where}", params).fetchone()[0]
        filas = con.execute(
            f"""
            SELECT {v1}, {v2}, COUNT(*) FROM personas
            WHERE {where} AND {v1} IS NOT NULL AND {v2} IS NOT NULL
            GROUP BY 1, 2
            """,
            params,
        ).fetchall()
        z = [[0] * len(ejes1) for _ in range(len(ejes2))]
        for val1, val2, n in filas:
            i1 = idx1[_normaliza(val1, set1, resto1)]
            i2 = idx2[_normaliza(val2, set2, resto2)]
            z[i2][i1] += n
        resultado[nombre] = {"etiqueta": etiqueta, "recuento": total, "x": ejes1, "y": ejes2, "z": z}
    return v1, v2, resultado


NBINS_HISTOGRAMA = 20


def _cruce_numerica_categorica(
    con, condiciones: dict, v_num: str, v_cat: str, top_n: int | None = TOP_N_CATEGORICO
) -> dict:
    # top_n=None: ver comentario equivalente en _cruce_categorica_categorica
    # (exportacion a Excel, 2026-09-10).
    top_cats, hay_resto = _top_categorias(con, v_cat, top_n)
    if hay_resto:
        # CASE ... IN (?, ?, ...) ELSE 'Resto': el resumen (min/max/cuartiles/
        # histograma) de "Resto" tiene que salir de UN SOLO GROUP BY sobre los
        # valores crudos agrupados asi, no de combinar a mano los cuartiles ya
        # calculados de cada categoria sobrante (un cuartil no se puede
        # promediar entre grupos sin perder precision).
        placeholders = ", ".join("?" for _ in top_cats)
        cat_expr = f"CASE WHEN {v_cat} IN ({placeholders}) THEN {v_cat} ELSE 'Resto' END"
        params_cat = list(top_cats)
        categorias_finales = top_cats + ["Resto"]
    else:
        cat_expr = v_cat
        params_cat = []
        categorias_finales = top_cats

    # Bordes de histograma FIJOS (percentiles 1-99 de TODA la tabla, sin
    # filtrar - ver _rango_percentiles) para que todas las categorias y los 4
    # subgrupos compartan la misma escala de eje X - mismo criterio que la
    # rejilla de densidad de numerica x numerica (comparabilidad al cambiar
    # de categoria o de subgrupo, y robustez frente a un valor extremo
    # aislado que aplastaria el resto de bins contra un lado del grafico).
    minv, maxv = _rango_percentiles(con, v_num)
    sin_rango = minv is None or maxv == minv
    ancho = None if sin_rango else (maxv - minv) / NBINS_HISTOGRAMA
    centros_bin = [] if sin_rango else [minv + ancho * (i + 0.5) for i in range(NBINS_HISTOGRAMA)]

    resultado = {}
    for nombre, c in condiciones.items():
        where, params, etiqueta = c["where"], c["params"], c["etiqueta"]
        total = con.execute(f"SELECT COUNT(*) FROM personas WHERE {where}", params).fetchone()[0]
        filas = con.execute(
            f"""
            SELECT {cat_expr} AS categoria, COUNT(*) AS n,
                   MIN({v_num}) AS minimo, MAX({v_num}) AS maximo,
                   QUANTILE_CONT({v_num}, 0.25) AS q1,
                   MEDIAN({v_num}) AS mediana,
                   QUANTILE_CONT({v_num}, 0.75) AS q3
            FROM personas
            WHERE {where} AND {v_cat} IS NOT NULL AND {v_num} IS NOT NULL
            GROUP BY 1
            ORDER BY 1
            """,
            # cat_expr (con sus placeholders) va en el SELECT, que aparece
            # ANTES que el WHERE en el texto de la query - los parametros
            # posicionales tienen que ir en ese mismo orden.
            params_cat + params,
        ).fetchall()

        histogramas = {cat: [0] * NBINS_HISTOGRAMA for cat in categorias_finales}
        if not sin_rango:
            filas_hist = con.execute(
                f"""
                WITH bineado AS (
                    SELECT {cat_expr} AS categoria,
                        LEAST({NBINS_HISTOGRAMA - 1}, GREATEST(0,
                            CAST(({v_num} - {minv}) / {ancho} AS INTEGER))) AS bin
                    FROM personas
                    WHERE {where} AND {v_cat} IS NOT NULL AND {v_num} IS NOT NULL
                )
                SELECT categoria, bin, COUNT(*) FROM bineado GROUP BY 1, 2
                """,
                params_cat + params,
            ).fetchall()
            for cat, b, n in filas_hist:
                if cat in histogramas:
                    histogramas[cat][b] = n

        # Bigotes = convencion estandar de Tukey (1,5x RIC desde Q1/Q3),
        # recortados al min/max real si el rango real es mas corto - no el
        # min/max real directamente (2026-09-09, decision explicita del
        # usuario tras ver Ingresos_bruto_anual_2024 con un valor de
        # 647.000€: con el min/max real, un solo valor extremo estira el eje
        # entero y aplasta la caja real contra un lado). No hay outliers
        # sueltos dibujados aparte (exigiria valores individuales, que este
        # endpoint nunca envia) - solo se acorta el bigote.
        def _bigotes(mn, mx, q1, q3):
            if q1 is None or q3 is None:
                return mn, mx
            ric = q3 - q1
            return max(mn, q1 - 1.5 * ric), min(mx, q3 + 1.5 * ric)

        resultado[nombre] = {
            "etiqueta": etiqueta,
            "recuento": total,
            "cajas": [
                {
                    "categoria": cat, "n": n, "min": mn, "max": mx, "q1": q1, "mediana": med, "q3": q3,
                    **dict(zip(("bigote_inf", "bigote_sup"), _bigotes(mn, mx, q1, q3))),
                }
                for cat, n, mn, mx, q1, med, q3 in filas
            ],
            "histograma_bins": centros_bin,
            "histograma_por_categoria": histogramas,
        }
    return resultado


@router.post("/colectivos/cruce")
def cruce(request: Request, body: CruceRequest):
    con = request.app.state.con.cursor()
    columnas_validas = _columnas_personas(con)
    numericas, categoricas = _clasificar_columnas(columnas_validas)
    numericas_validas = set(numericas) - {"ipf"}
    categoricas_validas = set(categoricas)

    v1, v2 = body.variable1, body.variable2
    if v1 == v2:
        raise HTTPException(400, "Las 2 variables deben ser distintas")

    def _tipo(v):
        if v in numericas_validas:
            return "numerica"
        if v in categoricas_validas:
            return "categorica"
        raise HTTPException(400, f"'{v}' no es una variable valida para cruzar")

    tipo1, tipo2 = _tipo(v1), _tipo(v2)
    condiciones = calcular_condiciones_subgrupos(con, body.filtro1, body.filtro2, body.filtro3, body.filtro4)

    if tipo1 == "numerica" and tipo2 == "numerica":
        return {
            "tipo": "numerica_numerica", "variable1": v1, "variable2": v2,
            "subgrupos": _cruce_numerica_numerica(con, condiciones, v1, v2),
        }
    if tipo1 == "categorica" and tipo2 == "categorica":
        v_x, v_y, subgrupos = _cruce_categorica_categorica(con, condiciones, v1, v2)
        return {"tipo": "categorica_categorica", "variable1": v_x, "variable2": v_y, "subgrupos": subgrupos}
    # numerica x categorica: variable1/variable2 se devuelven en el MISMO
    # orden que las eligio el usuario (eje X/eje Y), nunca reordenadas - hasta
    # el 2026-09-10 aqui se normalizaba siempre a (v_num, v_cat) para que la
    # categorica cayera en el eje X del boxplot, revertido a peticion
    # explicita del usuario. v_num/v_cat internamente SI hace falta saber
    # cual es cual (para la consulta SQL de _cruce_numerica_categorica, que
    # no cambia), por eso se calculan aparte de lo que se devuelve.
    # variable1_numerica le dice al frontend si el eje X (variable1) es la
    # numerica o la categorica, para elegir boxplot vertical u horizontal
    # (ver dibujarCruceBoxplot en colectivos.html) sin tener que inferirlo de
    # otro sitio (nunca de columnasDisponibles.numericas en el propio
    # request, que podria no coincidir si el usuario cambio de seleccion
    # entre pedir y dibujar).
    v_num, v_cat = (v1, v2) if tipo1 == "numerica" else (v2, v1)
    return {
        "tipo": "numerica_categorica", "variable1": v1, "variable2": v2,
        "variable1_numerica": tipo1 == "numerica",
        "subgrupos": _cruce_numerica_categorica(con, condiciones, v_num, v_cat),
    }


class CruceCentralRequest(BaseModel):
    variable1: str
    variable2: str
    variable_central: str


def _cruce_categorica_categorica_central(
    con, v1: str, v2: str, v_central: str, top_n: int | None = TOP_N_CATEGORICO
) -> tuple[str, str, dict]:
    """Modo "3 variables" de Cruce (switch "Aplicar filtro de colectivos" en
    No, 2026-09-09): heatmap de la variable central (numerica) agregada por
    cada combinacion de v1 x v2 (categoricas), sobre TODA la tabla (sin
    filtro de colectivo - por eso esta funcion no toma `condiciones` como las
    de mas arriba, solo hay un resultado, no 4 subgrupos). Mismo top-N=20 +
    "Resto" que _cruce_categorica_categorica. Eje X = v1, eje Y = v2,
    SIEMPRE (2026-09-10, decision explicita del usuario: revierte la regla
    anterior "eje X = la variable con mas categorias" que si tenia esta
    funcion al principio - "Variable eje X"/"Variable eje Y" en el frontend
    mapean directo a su eje).

    Media Y mediana se calculan siempre las dos en la misma consulta (mismo
    patron que trayectorias.py con AVG/MEDIAN en el mismo SELECT) - el switch
    Media/Mediana del frontend solo elige que matriz dibujar, sin volver a
    pedir al backend.

    El "Resto" de cada eje se agrupa con una expresion CASE en el propio
    GROUP BY (no combinando a mano medias/medianas ya calculadas de cada
    categoria sobrante despues de la consulta): una mediana no se puede
    promediar entre grupos sin perder precision, igual que ya se explica en
    _cruce_numerica_categorica para sus cajas. top_n=None: ver comentario
    equivalente en _cruce_categorica_categorica (exportacion a Excel,
    2026-09-10)."""
    cat1, resto1 = _top_categorias(con, v1, top_n)
    cat2, resto2 = _top_categorias(con, v2, top_n)
    etiqueta_resto = "Resto"
    ejes1 = cat1 + ([etiqueta_resto] if resto1 else [])
    ejes2 = cat2 + ([etiqueta_resto] if resto2 else [])

    def _expr(columna, categorias_top, hay_resto):
        if not hay_resto:
            return columna, []
        placeholders = ", ".join("?" for _ in categorias_top)
        return f"CASE WHEN {columna} IN ({placeholders}) THEN {columna} ELSE 'Resto' END", list(categorias_top)

    expr1, params1 = _expr(v1, cat1, resto1)
    expr2, params2 = _expr(v2, cat2, resto2)
    idx1 = {v: i for i, v in enumerate(ejes1)}
    idx2 = {v: i for i, v in enumerate(ejes2)}

    filas = con.execute(
        f"""
        SELECT {expr1} AS cat1, {expr2} AS cat2,
               AVG({v_central}) AS media, MEDIAN({v_central}) AS mediana, COUNT(*) AS n
        FROM personas
        WHERE {v1} IS NOT NULL AND {v2} IS NOT NULL AND {v_central} IS NOT NULL
        GROUP BY 1, 2
        """,
        # cat_expr (con sus placeholders) va en el SELECT, que aparece ANTES
        # que el WHERE en el texto de la query - los parametros posicionales
        # tienen que ir en ese mismo orden (igual que _cruce_numerica_categorica).
        params1 + params2,
    ).fetchall()

    # Celdas sin ninguna persona quedan a None (no a 0, que se confundiria en
    # el heatmap con "el agregado vale 0") - Plotly pinta None como hueco.
    z_media = [[None] * len(ejes1) for _ in range(len(ejes2))]
    z_mediana = [[None] * len(ejes1) for _ in range(len(ejes2))]
    n_celda = [[0] * len(ejes1) for _ in range(len(ejes2))]
    for val1, val2, media, mediana, n in filas:
        i1, i2 = idx1[val1], idx2[val2]
        z_media[i2][i1] = media
        z_mediana[i2][i1] = mediana
        n_celda[i2][i1] = n

    total = con.execute(
        f"SELECT COUNT(*) FROM personas WHERE {v1} IS NOT NULL AND {v2} IS NOT NULL AND {v_central} IS NOT NULL"
    ).fetchone()[0]

    return v1, v2, {
        "x": ejes1, "y": ejes2, "z_media": z_media, "z_mediana": z_mediana, "n": n_celda, "recuento": total,
    }


@router.post("/colectivos/cruce-central")
def cruce_central(request: Request, body: CruceCentralRequest):
    con = request.app.state.con.cursor()
    columnas_validas = _columnas_personas(con)
    numericas, categoricas = _clasificar_columnas(columnas_validas)
    numericas_validas = set(numericas) - {"ipf"}
    categoricas_validas = set(categoricas)

    v1, v2, v_central = body.variable1, body.variable2, body.variable_central
    if len({v1, v2, v_central}) < 3:
        raise HTTPException(400, "Las 3 variables deben ser distintas")
    if v1 not in categoricas_validas or v2 not in categoricas_validas:
        raise HTTPException(400, "Variable 1 y Variable 2 deben ser categoricas en el modo de 3 variables")
    if v_central not in numericas_validas:
        raise HTTPException(400, "La variable central debe ser numerica")

    v_x, v_y, resultado = _cruce_categorica_categorica_central(con, v1, v2, v_central)
    return {
        "tipo": "categorica_categorica_central",
        "variable1": v_x, "variable2": v_y, "variable_central": v_central,
        **resultado,
    }


class PuntosRequest(SubgruposRequest):
    variable1: str
    variable2: str
    subgrupo: str


@router.post("/colectivos/cruce/puntos")
def cruce_puntos(request: Request, body: PuntosRequest):
    """Puntos individuales (x,y) de UN SOLO subgrupo, para el "Diagrama de
    dispersion" de numerica x numerica en "Cruce de variables" - decision
    explicita del usuario (2026-09-09): prefiere ver todos los puntos, aunque
    sean muchos (hasta ~1,3M), en vez de solo la densidad agregada (que sigue
    siendo el grafico por defecto, /colectivos/cruce). Va en un endpoint
    APARTE, a peticion (no dentro de /colectivos/cruce): hasta 1,3M puntos x2
    coordenadas ya es un payload notable, no tiene sentido mandarlo x4 (uno
    por subgrupo) si el usuario solo mira un subgrupo cada vez - el frontend
    lo pide solo la primera vez que se elige "Dispersion" para cada
    subgrupo, y lo cachea. El frontend dibuja esto con Plotly 'scattergl'
    (WebGL), no 'scatter' (SVG): SVG no aguanta mas de unos pocos miles de
    puntos sin congelar el navegador, WebGL si."""
    con = request.app.state.con.cursor()
    columnas_validas = _columnas_personas(con)
    numericas, _ = _clasificar_columnas(columnas_validas)
    numericas_validas = set(numericas) - {"ipf"}
    if body.variable1 not in numericas_validas or body.variable2 not in numericas_validas:
        raise HTTPException(400, "Las 2 variables deben ser numericas")
    if body.subgrupo not in ORDEN_SUBGRUPOS:
        raise HTTPException(400, f"Subgrupo desconocido: {body.subgrupo}")

    condiciones = calcular_condiciones_subgrupos(con, body.filtro1, body.filtro2, body.filtro3, body.filtro4)
    c = condiciones[body.subgrupo]
    filas = con.execute(
        f"SELECT {body.variable1}, {body.variable2} FROM personas "
        f"WHERE {c['where']} AND {body.variable1} IS NOT NULL AND {body.variable2} IS NOT NULL",
        c["params"],
    ).fetchall()
    return {"x": [f[0] for f in filas], "y": [f[1] for f in filas]}


# Exportacion a Excel de "Cruce de variables" (2026-09-09, RECALCULO parcial
# anyadido 2026-09-10): igual que /trayectorias/colectivo/exportar para
# numerica_numerica - el frontend manda tal cual el resultado del cruce
# activo (ultimoResultadoCruce), NO se recalcula nada. Pero
# categorica_categorica, numerica_categorica y categorica_categorica_central
# YA NO son WYSIWYG (decision explicita del usuario, 2026-09-10): el grafico
# en pantalla se sigue truncando a TOP_N_CATEGORICO para no desbordar, pero
# el Excel exportado tiene que llevar TODAS las categorias reales sin
# truncar (p.ej. actividad economica x provincia completas, no el top-20+
# "Resto" del heatmap en pantalla) - por eso esas 3 se recalculan aqui mismo
# con top_n=None (ver _construir_excel_cruce), y el frontend manda ademas
# filtro1-4 (para categorica_categorica/numerica_categorica; el modo "3
# variables" no filtra por colectivo, no hace falta). Con filtro de
# colectivo (3 tipos "normales"), UNA HOJA POR SUBGRUPO, los 4 de un tiron
# (decision explicita del usuario, 2026-09-09: la primera version solo
# exportaba el subgrupo marcado en pantalla - "de un tiron los cuatro
# subcolectivos, uno en cada pestaña"); sin filtro (modo "3 variables") no
# hay subgrupos, es poblacion completa. Excepcion deliberada: "Diagrama de
# dispersion" (numerica x numerica) no manda sus puntos individuales (hasta
# 1,3M, no caben en una hoja de Excel - limite ~1.048.576 filas) - el
# frontend manda siempre la rejilla de densidad para ese tipo, aunque el
# grafico en pantalla sea de puntos. Otra excepcion: el modo "3 variables"
# exporta Media Y Mediana en 2 hojas siempre, aunque el switch del grafico
# solo muestre una de las 2 a la vez - ambas ya salen calculadas juntas en
# la misma consulta, no tiene sentido limitarse a la activa.
#
# Formato REJILLA (matriz: una fila por valor de un eje, una columna por
# valor del otro) para todo lo que ya es conceptualmente una tabla de 2 ejes
# - numerica_numerica, categorica_categorica, el histograma de
# numerica_categorica, y las 3 hojas del modo "3 variables" - decision
# explicita del usuario, 2026-09-09, tras ver el formato largo inicial (2
# columnas con una fila por celda): mas facil de leer como la propia tabla
# de contingencia/heatmap que ya se ve en pantalla. El boxplot de
# numerica_categorica es la unica excepcion real: no es una tabla de 2 ejes
# (cada fila ya es una categoria distinta con varias estadisticas), se queda
# en su forma natural de una fila por categoria.
# Sin limite de categorias en la exportacion a Excel (2026-09-10, decision
# explicita del usuario): el grafico en pantalla se sigue truncando a
# TOP_N_CATEGORICO para no desbordar (p.ej. un heatmap de actividad
# economica x provincia, 20x20 celdas visibles), pero el Excel exportado
# lleva SIEMPRE todas las categorias reales (p.ej. esa misma tabla completa,
# cientos de filas x 52 provincias), sin "Resto". Por eso categorica_categorica
# y numerica_categorica ya NO son WYSIWYG del grafico (dejan de mandar
# "datos", el backend RECALCULA sin top-N a partir de los filtros de
# colectivo) - numerica_numerica es la unica que se queda igual que antes
# (la rejilla de densidad nunca tuvo top-N que quitar) y
# categorica_categorica_central tampoco necesita filtros (poblacion
# completa, sin filtro de colectivo).
class CruceExportRequest(BaseModel):
    tipo: Literal[
        "numerica_numerica", "categorica_categorica", "numerica_categorica", "categorica_categorica_central",
    ]
    variable1: str
    variable2: str
    variable_central: str | None = None
    tipo_grafico: str
    # Solo hace falta para numerica_numerica (unico tipo que sigue siendo
    # WYSIWYG, ver comentario de arriba) - mismo dict que
    # ultimoResultadoCruce.subgrupos en el frontend.
    datos: dict | None = None
    # Filtros de colectivo, misma forma que SubgruposRequest - solo hacen
    # falta para recalcular categorica_categorica/numerica_categorica sin
    # limite (categorica_categorica_central no filtra por colectivo).
    filtro1: Filtro | None = None
    filtro2: Filtro | None = None
    filtro3: Filtro | None = None
    filtro4: Filtro | None = None


def _escribir_grid_cruce(ws, fila_inicio: int, titulo_esquina: str, x: list, y: list, valores: list, negrita: Font):
    """Tabla-rejilla en `ws` a partir de `fila_inicio`: esa fila es la
    cabecera de columnas (titulo_esquina en la esquina + un valor de `x` por
    columna), y a continuacion una fila por cada valor de `y` con los datos
    de `valores` (matriz [fila_y][col_x], mismo shape que ya usa el backend
    para z/z_media/z_mediana/n). Celdas sin dato (0 o None) se dejan en
    blanco en vez de escribir un 0 - relevante sobre todo en
    z_media/z_mediana, donde None es literalmente "nadie en esa celda", no
    "el promedio vale 0"."""
    ws.cell(row=fila_inicio, column=1, value=titulo_esquina).font = negrita
    for ix, cat_x in enumerate(x):
        ws.cell(row=fila_inicio, column=2 + ix, value=cat_x).font = negrita
    for iy, cat_y in enumerate(y):
        ws.cell(row=fila_inicio + 1 + iy, column=1, value=cat_y).font = negrita
        for ix, cat_x in enumerate(x):
            v = valores[iy][ix]
            if v:
                ws.cell(row=fila_inicio + 1 + iy, column=2 + ix, value=v)


def _escribir_hoja_cruce_subgrupo(ws, sub: dict, variable1: str, variable2: str, tipo: str, tipo_grafico: str, negrita: Font):
    """Una hoja = un subgrupo: cabecera con etiqueta/recuento del subgrupo
    (para saber de que numeros habla la hoja sin volver a la app, mismo
    espiritu que la hoja 'Colectivos' de _construir_excel) + la tabla de
    datos a partir de la fila 4."""
    ws.cell(row=1, column=1, value="Subcolectivo").font = negrita
    ws.cell(row=1, column=2, value=sub["etiqueta"])
    ws.cell(row=2, column=1, value="Personas").font = negrita
    ws.cell(row=2, column=2, value=sub["recuento"])

    fila_cabecera = 4
    if tipo == "numerica_categorica" and tipo_grafico == "histograma":
        categorias = list(sub["histograma_por_categoria"].keys())
        valores = [sub["histograma_por_categoria"][cat] for cat in categorias]
        _escribir_grid_cruce(ws, fila_cabecera, f"{variable2} \\ {variable1}", sub["histograma_bins"], categorias, valores, negrita)

    elif tipo == "numerica_categorica":
        # Boxplot (por defecto): una fila por categoria, no es una tabla de 2
        # ejes - se queda en su forma natural, ver cabecera del fichero.
        for col, titulo in enumerate(["Categoría", "N", "Mínimo", "Q1", "Mediana", "Q3", "Máximo"], start=1):
            ws.cell(row=fila_cabecera, column=col, value=titulo).font = negrita
        for i, caja in enumerate(sub["cajas"]):
            fila = fila_cabecera + 1 + i
            ws.cell(row=fila, column=1, value=caja["categoria"])
            ws.cell(row=fila, column=2, value=caja["n"])
            ws.cell(row=fila, column=3, value=caja["min"])
            ws.cell(row=fila, column=4, value=caja["q1"])
            ws.cell(row=fila, column=5, value=caja["mediana"])
            ws.cell(row=fila, column=6, value=caja["q3"])
            ws.cell(row=fila, column=7, value=caja["max"])

    else:
        # numerica_numerica (rejilla de densidad, ver cabecera) o
        # categorica_categorica (tabla de contingencia): misma forma.
        _escribir_grid_cruce(ws, fila_cabecera, f"{variable2} \\ {variable1}", sub["x"], sub["y"], sub["z"], negrita)


def _construir_excel_cruce(payload: CruceExportRequest, con) -> bytes:
    negrita = Font(bold=True)
    wb = Workbook()
    ws = wb.active

    if payload.tipo == "categorica_categorica_central":
        # Recalculada sin limite de categorias (top_n=None, ver cabecera del
        # fichero) - poblacion completa, sin filtro de colectivo, por eso no
        # hace falta leer payload.filtro1-4 aqui. 3 hojas (Media/Mediana/N),
        # no solo la que estaba activa en el switch del grafico - Media y
        # Mediana ya salen calculadas de golpe en la misma consulta (ver
        # _cruce_categorica_categorica_central), y N (cuantas personas hay
        # detras de cada celda) es necesario para interpretar si una
        # media/mediana es fiable o son 3 personas sueltas.
        if payload.variable_central is None:
            raise HTTPException(400, "Falta variable_central para el modo '3 variables'")
        v_x, v_y, resultado = _cruce_categorica_categorica_central(
            con, payload.variable1, payload.variable2, payload.variable_central, top_n=None
        )
        titulo_esquina = f"{v_y} \\ {v_x}"
        for i, (clave, etiqueta_valor) in enumerate((("z_media", "Media"), ("z_mediana", "Mediana"), ("n", "N"))):
            hoja = ws if i == 0 else wb.create_sheet(etiqueta_valor)
            hoja.title = etiqueta_valor
            _escribir_grid_cruce(hoja, 1, titulo_esquina, resultado["x"], resultado["y"], resultado[clave], negrita)
        buffer = io.BytesIO()
        wb.save(buffer)
        return buffer.getvalue()

    if payload.tipo == "numerica_numerica":
        # Unica combinacion que sigue siendo WYSIWYG del grafico (ver
        # cabecera del fichero): la rejilla de densidad nunca tuvo top-N de
        # categorias que quitar, no hace falta recalcular nada.
        if payload.datos is None:
            raise HTTPException(400, "Falta 'datos' para exportar numerica_numerica")
        datos = payload.datos
    else:
        # categorica_categorica / numerica_categorica: RECALCULADAS sin
        # limite de categorias a partir de los filtros de colectivo que
        # manda el frontend (ya no son WYSIWYG del grafico en pantalla, que
        # sigue truncado a TOP_N_CATEGORICO - ver cabecera del fichero).
        if payload.filtro1 is None or payload.filtro2 is None:
            raise HTTPException(400, "Faltan los filtros de colectivo para recalcular la exportacion")
        condiciones = calcular_condiciones_subgrupos(
            con, payload.filtro1, payload.filtro2, payload.filtro3, payload.filtro4
        )
        if payload.tipo == "categorica_categorica":
            _, _, datos = _cruce_categorica_categorica(
                con, condiciones, payload.variable1, payload.variable2, top_n=None
            )
        else:
            columnas_validas = _columnas_personas(con)
            numericas, _ = _clasificar_columnas(columnas_validas)
            numericas_validas = set(numericas) - {"ipf"}
            v_num, v_cat = (
                (payload.variable1, payload.variable2)
                if payload.variable1 in numericas_validas
                else (payload.variable2, payload.variable1)
            )
            datos = _cruce_numerica_categorica(con, condiciones, v_num, v_cat, top_n=None)

    # Los 3 tipos "normales": una hoja por cada uno de los 4 subgrupos, de un
    # tiron. "Subgrupo N" (no la etiqueta completa, p.ej. "sexo Hombre +
    # nacionalidad Espana") porque Excel limita el nombre de hoja a 31
    # caracteres y no admite ciertos caracteres - la etiqueta completa va
    # dentro de la propia hoja (fila 1), no hace falta acortarla ahi.
    for i, nombre in enumerate(ORDEN_SUBGRUPOS):
        hoja = ws if i == 0 else wb.create_sheet()
        hoja.title = f"Subgrupo {i + 1}"
        _escribir_hoja_cruce_subgrupo(
            hoja, datos[nombre], payload.variable1, payload.variable2, payload.tipo, payload.tipo_grafico, negrita,
        )

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


@router.post("/colectivos/cruce/exportar")
def exportar_cruce(request: Request, payload: CruceExportRequest):
    con = request.app.state.con.cursor()
    contenido = _construir_excel_cruce(payload, con)
    return StreamingResponse(
        io.BytesIO(contenido),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=cruce.xlsx"},
    )


def _construir_excel(resultados: dict, grupos: list[str] = ORDEN_SUBGRUPOS) -> bytes:
    """Vuelca los mismos datos de _calcular_estadisticas a un .xlsx: una hoja
    'Colectivos' (nombre + recuento de cada grupo, para saber de que numeros
    hablan las 2 hojas siguientes sin volver a la app), una hoja 'Numericas'
    (columna x grupo x media/mediana/desviacion) y una hoja 'Categoricas' con
    una tabla apilada por columna categorica (grupo con Conteo/Porcentaje por
    cada uno), igual que el formato ya usado a mano por el Observatorio -
    hueco en blanco si una categoria no aparece en algun grupo, fila en
    blanco entre columnas.

    `grupos` son los 4 subgrupos de siempre (por defecto) o [GRUPO_SIN_FILTRO]
    (un unico "Toda la MCVL", switch "Filtro de colectivos" en No) - incluso
    con un unico grupo el formato se queda igual (una "columna de grupo" en
    vez de 4), no un formato aparte."""
    negrita = Font(bold=True)
    wb = Workbook()

    # --- Hoja Colectivos ---
    ws_col = wb.active
    ws_col.title = "Colectivos"
    ws_col.cell(row=1, column=1, value="Subcolectivo").font = negrita
    ws_col.cell(row=1, column=2, value="Personas").font = negrita
    for i, nombre in enumerate(grupos):
        ws_col.cell(row=i + 2, column=1, value=resultados[nombre]["etiqueta"])
        ws_col.cell(row=i + 2, column=2, value=resultados[nombre]["recuento"])

    # --- Hoja Numericas ---
    ws_num = wb.create_sheet("Numericas")
    ws_num.cell(row=1, column=1, value="Columna").font = negrita

    columnas_numericas = [d["columna"] for d in resultados[grupos[0]]["numericas"]]

    col = 2
    for nombre in grupos:
        ws_num.merge_cells(start_row=1, start_column=col, end_row=1, end_column=col + 2)
        ws_num.cell(row=1, column=col, value=resultados[nombre]["etiqueta"]).font = negrita
        for offset, titulo in enumerate(["Media", "Mediana", "Desv. tipica"]):
            ws_num.cell(row=2, column=col + offset, value=titulo).font = negrita
        col += 3

    for i, nombre_col in enumerate(columnas_numericas):
        fila = i + 3
        ws_num.cell(row=fila, column=1, value=nombre_col)
        col = 2
        for nombre in grupos:
            stats = resultados[nombre]["numericas"][i]
            ws_num.cell(row=fila, column=col, value=stats["media"])
            ws_num.cell(row=fila, column=col + 1, value=stats["mediana"])
            ws_num.cell(row=fila, column=col + 2, value=stats["desviacion"])
            col += 3

    # --- Hoja Categoricas ---
    ws_cat = wb.create_sheet("Categoricas")
    columnas_categoricas = [d["columna"] for d in resultados[grupos[0]]["categoricas"]]

    fila = 1
    for idx_col, nombre_col in enumerate(columnas_categoricas):
        # Union de valores presentes en cualquiera de los grupos (orden de aparicion).
        mapas, vistos, vistos_set = {}, [], set()
        for nombre in grupos:
            entrada = resultados[nombre]["categoricas"][idx_col]
            mapas[nombre] = {v["valor"]: v for v in entrada["valores"]}
            for v in entrada["valores"]:
                if v["valor"] not in vistos_set:
                    vistos_set.add(v["valor"])
                    vistos.append(v["valor"])

        col = 2
        for nombre in grupos:
            ws_cat.merge_cells(start_row=fila, start_column=col, end_row=fila, end_column=col + 1)
            ws_cat.cell(row=fila, column=col, value=resultados[nombre]["etiqueta"]).font = negrita
            col += 2
        fila += 1

        ws_cat.cell(row=fila, column=1, value=nombre_col).font = negrita
        col = 2
        for _ in grupos:
            ws_cat.cell(row=fila, column=col, value="Conteo").font = negrita
            ws_cat.cell(row=fila, column=col + 1, value="Porcentaje (%)").font = negrita
            col += 2
        fila += 1

        for valor in vistos:
            ws_cat.cell(row=fila, column=1, value=str(valor))
            col = 2
            for nombre in grupos:
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
    con = request.app.state.con.cursor()
    resultados = _calcular_subgrupos(con, body.filtro1, body.filtro2, body.filtro3, body.filtro4, sin_limite_categorico=True)
    contenido = _construir_excel(resultados)
    return StreamingResponse(
        io.BytesIO(contenido),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=colectivos.xlsx"},
    )
