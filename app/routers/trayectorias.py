"""Trayectorias: individual (episodios de afiliacion de un IPF) y de colectivo
(evolucion anual de una variable, superponiendo los 4 subgrupos de un
colectivo ya definido en /colectivos). La vista de colectivo vive como
pestana dentro de colectivos.html (no como pagina propia), asi que este
modulo solo expone el endpoint de datos, no una ruta HTML."""

import csv
import io
import json
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from openpyxl import Workbook
from openpyxl.styles import Font
from pydantic import BaseModel

from app.db import estado
from etl import reg_cotizacion_agrupado, tipo_contrato_claves_sucesoras
from app.routers.colectivos import (
    GRUPO_SIN_FILTRO,
    SubgruposRequest,
    condiciones_de_subgrupos,
    condiciones_sin_filtro,
)

router = APIRouter()
# Ver comentario equivalente en colectivos.py: ruta relativa al fichero, no al CWD.
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "templates")

# GRU_COTIZACION/REG_COTIZACION reutilizan la categorizacion ya definida en
# el ETL (GRUPOS_COTIZ_DICT en etl/personas_extendida.py, Grupo A; y
# etl/reg_cotizacion_agrupado.py, compartido con Reg_cotiz_ppal_Agrupado) en
# vez de reimplementarla desde cero - pero aqui la "expr" de cada variable en
# VARIABLES_CATEGORICAS produce un
# CODIGO/CUBETA COMPACTO (SMALLINT), NUNCA la etiqueta de texto directamente,
# y solo se decodifica a texto al final, en Python, sobre las pocas filas ya
# agregadas (ver _decodificar_gru_cotizacion/_decodificar_reg_cotizacion mas
# abajo y _empaquetar_categorica). Motivo, dos problemas reales vistos con
# datos reales el 2026-08-09 al intentar decodificar a texto DENTRO de la
# query SQL:
#   1) Rendimiento/memoria: expandir episodios de Afiliacion (decenas de
#      millones de filas) agrupando por una columna de TEXTO largo revienta
#      esta maquina (~3GB libres) - "IO Error: Espacio en disco
#      insuficiente" comprobado con GRU_COTIZACION como texto. Con la cubeta
#      como SMALLINT en vez de texto, la misma query si completa.
#   2) Correccion: GRU_COTIZACION/REG_COTIZACION son mapeos MUCHOS-A-UNO
#      (43 codigos crudos -> 8 regimenes, p.ej.) - si se decodificara a texto
#      DESPUES de agregar por el codigo crudo, dos codigos crudos distintos
#      que decodifican a la MISMA etiqueta se agregarian como si fueran 2
#      grupos independientes y, si una misma persona tiene episodios
#      concurrentes con esos 2 codigos distintos, se contaria 2 veces al
#      sumar sus COUNT(DISTINCT ipf) por separado. Haciendo el pliegue
#      ANTES de agregar (aqui, con la cubeta) el COUNT(DISTINCT ipf) de la
#      query ya deduplica esa persona correctamente dentro de su cubeta.
# GRU_COTIZACION: 0-12 es su propio codigo (mapeo 1 a 1, ver
# _GRUPOS_COTIZ_DICT - la guia documenta hasta el 12 "TRABAJADORES MENORES
# DE 17 AÑOS"; "BETWEEN 0 AND 11" dejaba fuera esa categoria por error,
# corregido 2026-08-20), fuera de ese dominio se pliega a -1 ("NO CONSTA" -
# dominado por el codigo 13, que la guia dice tratar como "no consta" para
# regimenes de cuenta propia, ver Grupo_cotiz_ppal en
# etl/personas_extendida.py).
_SQL_CUBETA_GRU_COTIZACION = "CASE WHEN t.gru_cotizacion BETWEEN 0 AND 12 THEN t.gru_cotizacion ELSE -1 END"

_GRUPOS_COTIZ_DICT = {
    0: "0 NO CONSTA",
    1: "1 INGENIEROS, LICENCIADOS Y ALTA DIRECCIÓN",
    2: "2 INGENIEROS TÉCNICOS, PERITOS Y AYUDANTES",
    3: "3 JEFES ADMINISTRATIVOS Y DE TALLER",
    4: "4 AYUDANTES NO TITULADOS",
    5: "5 OFICIALES ADMINISTRATIVOS",
    6: "6 SUBALTERNOS",
    7: "7 AUXILIARES ADMINISTRATIVOS",
    8: "8 OFICIALES DE PRIMERA Y SEGUNDA",
    9: "9 OFICIALES DE TERCERA Y ESPECIALISTAS",
    10: "10 MAYORES DE 18 AÑOS NO CUALIFICADOS",
    11: "11 TRABAJADORES MENORES DE 18 AÑOS",
    12: "12 TRABAJADORES MENORES DE 17 AÑOS",
}


def _decodificar_gru_cotizacion(cubeta) -> str:
    if cubeta is None or cubeta == -1:
        return "NO CONSTA"
    return _GRUPOS_COTIZ_DICT.get(int(cubeta), "NO CONSTA")


# REG_COTIZACION: cubeta/etiquetas de etl/reg_cotizacion_agrupado.py, el
# mismo modulo que usa Reg_cotiz_ppal_Agrupado (Grupo A) - antes cada uno
# tenia su propia copia verificada a mano para que coincidieran, ahora una
# unica fuente de verdad.
_SQL_CUBETA_REG_COTIZACION = reg_cotizacion_agrupado.sql_cubeta_agrupado("t.reg_cotizacion")
_decodificar_reg_cotizacion = reg_cotizacion_agrupado.decodificar_agrupado


def _decodificar_identidad(codigo) -> str:
    return "NO CONSTA" if codigo is None else str(codigo)


# Registro de variables del motor generico de "trayectoria categorica" (ver
# trayectorias_colectivo_categorica): que tabla/columna/expresion consultar
# para cada variable.
#
# tipo_pension: tabla con una fila por (ipf, anyo) ya dada (Pensiones) - ruta
# _trayectoria_categorica_periodica; Tipo_pension ya llega como texto desde
# el ETL, no necesita "decodificar".
#
# Las 5 de Afiliacion son episodicas (fecha_alta/baja, no una fila por anyo):
# usan _trayectoria_categorica_afiliacion, que antes expande episodios
# vigentes a 31 de diciembre a filas (ipf, anyo). "expr" es la expresion SQL
# que produce el codigo/cubeta CRUDO (compacto, nunca texto - ver arriba) a
# partir de la fila de `afiliacion` (alias t). "decodificar" (opcional, por
# defecto el codigo tal cual como texto) traduce ese codigo/cubeta a la
# etiqueta final, aplicado solo al puñado de filas ya agregadas que
# devuelve la query (2 ejes x ~90 anyos x ~20 categorias como mucho, no
# millones de filas) - TRL/CNAE/PROVINCIA se muestran con su codigo crudo
# (no hay diccionario pedido para decodificarlos todavia). "top_n" (solo
# CNAE2009_CC, pedido explicitamente): limita a las N categorias con mas
# peso en toda la tabla `afiliacion` (no solo el colectivo, para que
# 'Resto' signifique lo mismo pase lo que pase se filtre) y pliega el resto
# en la cubeta -1 ("Resto").
#
# TIPO_CONTRATO: a diferencia de las demas, antes de agrupar corrige claves
# sucesoras (04 TIPO_CONTRATO trae 147 codigos crudos, muchos deprecados) -
# logica compartida con etl/personas_extendida.py (Tipo_Contr_ppal_Agrupado,
# Grupo A) via etl/tipo_contrato_claves_sucesoras.py, para no reimplementarla
# en los 2 sitios - ver ese modulo para el detalle completo de las reglas
# (claves sucesoras con 1 vs 2 sucesoras, desambiguacion por COEF_T_PARCIAL,
# codigos huerfanos, agrupacion final en 7 categorias).
VARIABLES_CATEGORICAS = {
    "tipo_pension": {"tabla": "pensiones", "columna": "tipo_pension", "columna_anio": "ano_dato"},
    "trl": {"tabla": "afiliacion", "expr": "t.trl"},
    "gru_cotizacion": {"tabla": "afiliacion", "expr": _SQL_CUBETA_GRU_COTIZACION, "decodificar": _decodificar_gru_cotizacion},
    "reg_cotizacion": {"tabla": "afiliacion", "expr": _SQL_CUBETA_REG_COTIZACION, "decodificar": _decodificar_reg_cotizacion},
    "cnae2009_cc": {"tabla": "afiliacion", "expr": "t.cnae2009_cc", "top_n": 20},
    "provincia_domic_cc_ppal": {"tabla": "afiliacion", "expr": "t.provincia_domic_cc_ppal"},
    "tipo_contrato": {
        "tabla": "afiliacion",
        "expr": tipo_contrato_claves_sucesoras.sql_cubeta_agrupado("t.tipo_contrato", "t.coef_t_parcial"),
        "decodificar": tipo_contrato_claves_sucesoras.decodificar_agrupado,
    },
    # empleo_publico: ya llega como texto "SI"/"NO" desde el ETL (ver Empleo_publico en
    # etl/personas_extendida.py), igual que tipo_pension - no necesita "decodificar".
    "empleo_publico": {"tabla": "afiliacion", "expr": "t.empleo_publico"},
    # causa_baja_afil: codigo crudo de 08 CAUSA_BAJA_AFIL, igual criterio que TRL/CNAE/PROVINCIA
    # (sin diccionario pedido para decodificarlo todavia, ver comentario mas arriba).
    "causa_baja_afil": {"tabla": "afiliacion", "expr": "t.causa_baja_afil"},
}


class TrayectoriaCategoricaRequest(SubgruposRequest):
    variable: Literal[
        "tipo_pension", "trl", "gru_cotizacion", "reg_cotizacion",
        "cnae2009_cc", "provincia_domic_cc_ppal", "tipo_contrato",
        "empleo_publico", "causa_baja_afil",
    ]


# Expresion SQL compartida por la trayectoria categorica Y la numerica de
# Afiliacion: para cada episodio, en que anyos esta vigente a 31 de
# diciembre (fecha_alta_real_afil <= 31/12/anyo Y baja_modificada >=
# 31/12/anyo). Se usa Baja_modificada, no 07 FECHA_BAJA_REAL_AFIL: ya trae
# resuelto el placeholder 2099 de episodios todavia activos (Fase 1,
# Baja_modificada en etl/personas_extendida.py). anio_fin se
# calcula en vez de recorrer todos los anyos posibles (union con una tabla de
# anyos) para que la expansion sea lineal en anyos-por-episodio, no en
# anyos-totales-del-dataset x episodios - mucho mas barato. Si un anyo no
# tiene ningun episodio vigente ese 31 de diciembre, esa persona no aporta
# dato ese anyo (igual que los huecos en Bases/Pensiones).
_CTE_ANIOS_VIGENTES = """
    date_part('year', alta)::INTEGER AS anio_ini,
    CASE WHEN date_part('month', baja) = 12 AND date_part('day', baja) = 31
         THEN date_part('year', baja)::INTEGER
         ELSE date_part('year', baja)::INTEGER - 1
    END AS anio_fin
"""


def _trayectorias_bases(con, condiciones: dict) -> dict:
    """Evolucion de 'bases anuales' (CA+CP sumadas por ipf-anyo) de cada grupo
    de `condiciones` (los 4 subgrupos de siempre, o un unico "Toda la MCVL"
    con filtroColectivoGlobal en No - ver GRUPO_SIN_FILTRO en colectivos.py),
    agregada tanto por anyos de vida laboral como por edad - el frontend
    elige con cual pintar el grafico sin pedir otra vez al backend. N por
    punto para que el frontend pueda cortar/atenuar la linea donde la
    muestra es pequenya (< 30)."""
    resultado = {}
    for nombre, c in condiciones.items():
        recuento = con.execute(f"SELECT COUNT(*) FROM personas WHERE {c['where']}", c["params"]).fetchone()[0]

        filas = con.execute(
            f"""
            WITH colectivo AS (
                SELECT ipf, fecha_nacimiento, fecha_ini_primer_empleo
                FROM personas
                WHERE {c['where']}
            ), bases_anio AS (
                -- BCC_ANUAL_TOTAL viene en centimos, no en euros: el percentil
                -- 99,999 en crudo (~6.009.445) solo tiene sentido como tope
                -- maximo de cotizacion real (~60.094 EUR/anyo) dividido entre
                -- 100, nunca como 6 millones de euros de base anual.
                SELECT ipf, ano_cotizacion AS anio, SUM(bcc_anual_total) / 100.0 AS bases_anuales
                FROM (
                    SELECT ipf, ano_cotizacion, bcc_anual_total FROM bases_ca
                    WHERE ipf IN (SELECT ipf FROM colectivo)
                    UNION ALL
                    SELECT ipf, ano_cotizacion, bcc_anual_total FROM bases_cp
                    WHERE ipf IN (SELECT ipf FROM colectivo)
                )
                GROUP BY 1, 2
            ), unido AS (
                SELECT
                    b.bases_anuales,
                    CASE WHEN c.fecha_ini_primer_empleo IS NOT NULL
                         THEN (b.anio - date_part('year', c.fecha_ini_primer_empleo))::INTEGER END AS anos_vl,
                    (b.anio - date_part('year', c.fecha_nacimiento))::INTEGER AS edad
                FROM bases_anio b
                JOIN colectivo c USING (ipf)
            )
            SELECT 'anos_vl' AS eje, anos_vl AS x, AVG(bases_anuales), MEDIAN(bases_anuales), COUNT(*)
            FROM unido WHERE anos_vl >= 0 GROUP BY 1, 2
            UNION ALL
            -- edad >= 16 (no >= 0): investigado el 2026-08-07 por que aparecian
            -- edades menores de 18. El 96,5% de esas filas son edad 16-17
            -- (trabajo legal en Espana desde los 16, dato real, no error) -
            -- se cortaba en 18 igualmente por decision explicita del usuario
            -- en aquel momento; corregido el 2026-08-10 a 16, el limite legal
            -- real, para no ocultar esos dos anyos legitimos. El resto (cola
            -- suave hasta valores negativos, ~5.700 filas, 0,02% del total -
            -- fecha_nacimiento o ano_cotizacion erroneos en el dato MCVL, no
            -- un fallo de esta query) se sigue descartando con el corte en 16.
            SELECT 'edad' AS eje, edad AS x, AVG(bases_anuales), MEDIAN(bases_anuales), COUNT(*)
            FROM unido WHERE edad >= 16 GROUP BY 1, 2
            ORDER BY 1, 2
            """,
            c["params"],
        ).fetchall()

        series = {"anos_vl": [], "edad": []}
        for eje, x, media, mediana, n in filas:
            series[eje].append({"x": x, "media": media, "mediana": mediana, "n": n})

        resultado[nombre] = {"etiqueta": c["etiqueta"], "recuento": recuento, "series": series}

    return resultado


@router.post("/trayectorias/colectivo/bases")
def trayectorias_colectivo_bases(request: Request, body: SubgruposRequest):
    con = request.app.state.con.cursor()
    condiciones = condiciones_de_subgrupos(con, body)
    return _trayectorias_bases(con, condiciones)


# "Filtro de colectivos" en No (switch global, pestana "Seleccion de
# colectivo", 2026-09-09): mismo dato pero sobre TODA la MCVL sin filtrar,
# un unico grupo en vez de 4 subgrupos - sin filtro1/filtro2 que mandar, por
# eso GET sin cuerpo (igual patron que /colectivos/subgrupos-sin-filtro).
@router.get("/trayectorias/colectivo/bases-sin-filtro")
def trayectorias_colectivo_bases_sin_filtro(request: Request):
    con = request.app.state.con.cursor()
    return _trayectorias_bases(con, condiciones_sin_filtro())


def _trayectorias_pensiones(con, condiciones: dict) -> dict:
    """Evolucion de la pension anual (Ingresos_pension_bruto_anual) de cada
    grupo de `condiciones` (ver _trayectorias_bases) - mismo patron.
    Ingresos_pension_bruto_anual ya viene calculada en el ETL a partir de
    IMPORTE_MES_TOTAL_PRESTACION con su excepcion de 12 pagas (clase de
    prestacion 17, ~0,002% de las filas) en vez de 14 - no se recalcula ese
    x14 aqui para no perder esa excepcion (ver fase2_pensiones_extendida en
    etl/personas_extendida.py). No hay variable mensual aparte: es la misma
    cifra sin anualizar, redundante con esta. Si una persona cobra mas de una
    pension el mismo anyo (p.ej. viudedad + orfandad), se suman."""
    resultado = {}
    for nombre, c in condiciones.items():
        recuento = con.execute(f"SELECT COUNT(*) FROM personas WHERE {c['where']}", c["params"]).fetchone()[0]

        filas = con.execute(
            f"""
            WITH colectivo AS (
                SELECT ipf, fecha_nacimiento, fecha_ini_primer_empleo
                FROM personas
                WHERE {c['where']}
            ), pension_anio AS (
                -- Los importes vienen en centimos (igual que bcc_anual_total en
                -- Bases de cotizacion, ver project_bases_cotizacion_centimos).
                SELECT ipf, ano_dato AS anio, SUM(ingresos_pension_bruto_anual) / 100.0 AS valor
                FROM pensiones
                WHERE ipf IN (SELECT ipf FROM colectivo)
                GROUP BY 1, 2
            ), unido AS (
                SELECT
                    p.valor,
                    CASE WHEN c.fecha_ini_primer_empleo IS NOT NULL
                         THEN (p.anio - date_part('year', c.fecha_ini_primer_empleo))::INTEGER END AS anos_vl,
                    (p.anio - date_part('year', c.fecha_nacimiento))::INTEGER AS edad
                FROM pension_anio p
                JOIN colectivo c USING (ipf)
            )
            SELECT 'anos_vl' AS eje, anos_vl AS x, AVG(valor), MEDIAN(valor), COUNT(*)
            FROM unido WHERE anos_vl >= 0 GROUP BY 1, 2
            UNION ALL
            -- edad >= 16: mismo criterio ya aplicado en Bases de cotizacion
            -- (ver /trayectorias/colectivo/bases), misma naturaleza de dato.
            SELECT 'edad' AS eje, edad AS x, AVG(valor), MEDIAN(valor), COUNT(*)
            FROM unido WHERE edad >= 16 GROUP BY 1, 2
            ORDER BY 1, 2
            """,
            c["params"],
        ).fetchall()

        series = {"anos_vl": [], "edad": []}
        for eje, x, media, mediana, n in filas:
            series[eje].append({"x": x, "media": media, "mediana": mediana, "n": n})

        resultado[nombre] = {"etiqueta": c["etiqueta"], "recuento": recuento, "series": series}

    return resultado


@router.post("/trayectorias/colectivo/pensiones")
def trayectorias_colectivo_pensiones(request: Request, body: SubgruposRequest):
    con = request.app.state.con.cursor()
    condiciones = condiciones_de_subgrupos(con, body)
    return _trayectorias_pensiones(con, condiciones)


@router.get("/trayectorias/colectivo/pensiones-sin-filtro")
def trayectorias_colectivo_pensiones_sin_filtro(request: Request):
    con = request.app.state.con.cursor()
    return _trayectorias_pensiones(con, condiciones_sin_filtro())


def _trayectorias_afiliacion(con, condiciones: dict) -> dict:
    """Evolucion de NUM_TRABAJS_CC_2 (tamanyo de la empresa) de cada grupo de
    `condiciones` (ver _trayectorias_bases) - mismo contrato de salida que
    _trayectorias_bases/_pensiones (series anos_vl/edad con x/media/mediana/n),
    pero calculado distinto por debajo: Afiliacion es episodica
    (fecha_alta/baja_modificada), no una fila por anyo, asi que primero se
    expande a (ipf, anyo) con el episodio vigente a 31 de diciembre (ver
    _CTE_ANIOS_VIGENTES, mismo criterio que la trayectoria categorica de
    Afiliacion).

    Cuando hay mas de un episodio vigente a la vez (~1,7% de los pares
    persona-anyo, verificado con datos reales el 2026-08-09), NO se suman
    como en Bases (CA+CP): sumar el tamanyo de 2 empresas distintas no
    describe nada real de ninguna de las dos (a diferencia del dinero, que
    si se suma a un total real). Se usa el 'episodio dominante' - decision
    explicita del usuario tras verificar empates -: el que mas dias estuvo
    vigente DENTRO de ese anyo concreto; si empatan (frecuente, ~40% de los
    solapes: 2 empleos que cubren el anyo entero empatan a 365/366 dias
    aunque uno lleve activo mucho mas tiempo en total), desempata la
    duracion TOTAL del episodio (alta a baja_modificada, no solo la parte
    dentro de ese anyo); si siguen empatando (~7% de los solapes tras ese
    desempate), CCC_PPAL como ultimo criterio deterministico (mismo espiritu
    que el 'val ASC' que ya usa el ETL en casos similares, p.ej.
    Reg_cotiz_ppal_Agrupado en etl/personas_extendida.py)."""
    resultado = {}
    for nombre, c in condiciones.items():
        recuento = con.execute(f"SELECT COUNT(*) FROM personas WHERE {c['where']}", c["params"]).fetchone()[0]

        filas = con.execute(
            f"""
            WITH colectivo AS (
                SELECT ipf, fecha_nacimiento, fecha_ini_primer_empleo
                FROM personas
                WHERE {c['where']}
            ), episodios AS (
                SELECT
                    t.ipf, t.fecha_alta_real_afil AS alta, t.baja_modificada AS baja,
                    t.ccc_ppal AS ccc, t.num_trabajs_cc_2 AS valor
                FROM afiliacion t
                WHERE t.ipf IN (SELECT ipf FROM colectivo) AND t.num_trabajs_cc_2 IS NOT NULL
            ), anios AS (
                SELECT ipf, alta, baja, ccc, valor,
                       date_diff('day', alta, baja) AS dur_total_dias,
                       {_CTE_ANIOS_VIGENTES}
                FROM episodios
            ), expandido AS (
                SELECT ipf, alta, baja, ccc, valor, dur_total_dias,
                       UNNEST(generate_series(anio_ini, anio_fin)) AS anio
                FROM anios WHERE anio_fin >= anio_ini
            ), con_dias AS (
                SELECT ipf, anio, ccc, valor, dur_total_dias,
                       date_diff('day', GREATEST(alta, make_date(anio, 1, 1)), LEAST(baja, make_date(anio, 12, 31))) + 1 AS dias_en_anio
                FROM expandido
            ), dominante AS (
                SELECT ipf, anio, valor,
                       ROW_NUMBER() OVER (
                           PARTITION BY ipf, anio
                           ORDER BY dias_en_anio DESC, dur_total_dias DESC, ccc ASC
                       ) AS rn
                FROM con_dias
            ), unido AS (
                SELECT
                    d.valor,
                    CASE WHEN c.fecha_ini_primer_empleo IS NOT NULL
                         THEN (d.anio - date_part('year', c.fecha_ini_primer_empleo))::INTEGER END AS anos_vl,
                    (d.anio - date_part('year', c.fecha_nacimiento))::INTEGER AS edad
                FROM dominante d
                JOIN colectivo c USING (ipf)
                WHERE d.rn = 1
            )
            SELECT 'anos_vl' AS eje, anos_vl AS x, AVG(valor), MEDIAN(valor), COUNT(*)
            FROM unido WHERE anos_vl >= 0 GROUP BY 1, 2
            UNION ALL
            SELECT 'edad' AS eje, edad AS x, AVG(valor), MEDIAN(valor), COUNT(*)
            FROM unido WHERE edad >= 16 GROUP BY 1, 2
            ORDER BY 1, 2
            """,
            c["params"],
        ).fetchall()

        series = {"anos_vl": [], "edad": []}
        for eje, x, media, mediana, n in filas:
            series[eje].append({"x": x, "media": media, "mediana": mediana, "n": n})

        resultado[nombre] = {"etiqueta": c["etiqueta"], "recuento": recuento, "series": series}

    return resultado


@router.post("/trayectorias/colectivo/afiliacion")
def trayectorias_colectivo_afiliacion(request: Request, body: SubgruposRequest):
    con = request.app.state.con.cursor()
    condiciones = condiciones_de_subgrupos(con, body)
    return _trayectorias_afiliacion(con, condiciones)


@router.get("/trayectorias/colectivo/afiliacion-sin-filtro")
def trayectorias_colectivo_afiliacion_sin_filtro(request: Request):
    con = request.app.state.con.cursor()
    return _trayectorias_afiliacion(con, condiciones_sin_filtro())


def _empaquetar_categorica(etiqueta: str, recuento: int, filas: list, decodificar=lambda v: v) -> dict:
    """Comun a las 2 rutas de trayectoria categorica (periodica/Afiliacion):
    convierte filas (eje, x, n, categoria_cruda, n_categoria) en el JSON de
    salida, decodificando la categoria cruda a su etiqueta final aqui (sobre
    el puñado de filas ya agregadas, no dentro de la query - ver comentario
    junto a _GRUPOS_COTIZ_DICT)."""
    series = {"anos_vl": {}, "edad": {}}
    categorias_vistas = []
    for eje, x, n, categoria_cruda, n_categoria in filas:
        categoria = decodificar(categoria_cruda)
        punto = series[eje].setdefault(x, {"x": x, "n": n, "valores": {}})
        entrada = punto["valores"].setdefault(categoria, {"n": 0, "pct": 0.0})
        entrada["n"] += n_categoria
        entrada["pct"] = round(100 * entrada["n"] / n, 1) if n else 0.0
        if categoria not in categorias_vistas:
            categorias_vistas.append(categoria)

    series = {eje: sorted(puntos.values(), key=lambda p: p["x"]) for eje, puntos in series.items()}
    return {"etiqueta": etiqueta, "recuento": recuento, "categorias": categorias_vistas, "series": series}


@router.post("/trayectorias/colectivo/categorica")
def trayectorias_colectivo_categorica(request: Request, body: TrayectoriaCategoricaRequest):
    """Motor generico de 'trayectoria categorica': evolucion de la
    composicion categorica (Tipo_pension en Pensiones; TRL, GRU_COTIZACION,
    REG_COTIZACION, CNAE2009_CC, PROVINCIA_DOMIC_CC_PPAL en Afiliacion) de
    los 4 subgrupos de un colectivo.

    Para cada punto del eje X, el % de cada categoria se calcula sobre el
    numero de IPFs DISTINTOS del colectivo en ese punto (no sobre filas): una
    persona puede tener varias filas el mismo anyo con distinta categoria
    (p.ej. cobra viudedad y orfandad a la vez en Pensiones, o 2 contratos
    concurrentes con distinto regimen en Afiliacion), asi que los porcentajes
    de un mismo punto pueden sumar mas de 100% - no es una distribucion
    exclusiva, el frontend lo deja explicito. N (personas distintas del
    colectivo en ese punto, sin distinguir categoria) sirve para el mismo
    corte por fiabilidad ya usado en /trayectorias/colectivo/bases y
    /pensiones.

    Dos rutas segun la forma de la tabla de origen (ver VARIABLES_CATEGORICAS
    y _empaquetar_categorica para lo compartido): _trayectoria_categorica_
    periodica (una fila por (ipf, anyo) ya dada, como Pensiones) y
    _trayectoria_categorica_afiliacion (episodica, expande primero)."""
    con = request.app.state.con.cursor()
    condiciones = condiciones_de_subgrupos(con, body)
    info = VARIABLES_CATEGORICAS[body.variable]

    if info["tabla"] == "afiliacion":
        return _trayectoria_categorica_afiliacion(con, condiciones, info)
    return _trayectoria_categorica_periodica(con, condiciones, info)


# "Filtro de colectivos" en No: mismo motor pero sobre TODA la MCVL sin
# filtrar (ver GRUPO_SIN_FILTRO en colectivos.py) - `variable` como query
# param (no hay cuerpo con filtro1-4 que mandar, GET simple).
@router.get("/trayectorias/colectivo/categorica-sin-filtro")
def trayectorias_colectivo_categorica_sin_filtro(request: Request, variable: str):
    con = request.app.state.con.cursor()
    if variable not in VARIABLES_CATEGORICAS:
        raise HTTPException(400, f"Variable categorica desconocida: {variable}")
    condiciones = condiciones_sin_filtro()
    info = VARIABLES_CATEGORICAS[variable]

    if info["tabla"] == "afiliacion":
        return _trayectoria_categorica_afiliacion(con, condiciones, info)
    return _trayectoria_categorica_periodica(con, condiciones, info)


# Exportacion a Excel de "Trayectorias de colectivos" (paso 2 del boton
# Exportar, tras el de "Analisis de colectivos" - ver conversacion
# 2026-08-28). A diferencia de ese, aqui NO se recalcula nada: el frontend ya
# tiene en JS el resultado de la tabla/variable activa (ultimoResultadoTrayectorias,
# de uno de los 4 endpoints de arriba) y lo manda tal cual - repetir la
# consulta solo para exportar seria caro de mas (una variable categorica de
# Afiliacion tarda 40-110s, ver project_roadmap_colectivos_graficos punto 9).
# Este endpoint solo reordena ese JSON ya calculado en un .xlsx.
class TrayectoriaExportRequest(BaseModel):
    tabla_etiqueta: str
    variable_etiqueta: str
    variable_tipo: Literal["numerica", "categorica"]
    unidad: str | None = None
    resultado: dict


_ETIQUETA_EJE = {"anos_vl": "Años desde inicio de vida laboral", "edad": "Edad"}
# Nombre de hoja de Excel (2026-09-08): distinto de _ETIQUETA_EJE porque Excel limita los
# nombres de hoja a 31 caracteres - "Años desde inicio de vida laboral" tiene 33 y
# wb.create_sheet() lanzaria excepcion. La version larga se sigue usando en pantalla/cabeceras
# de columna (mas espacio disponible ahi), solo el nombre de la propia hoja se acorta.
_NOMBRE_HOJA_EJE = {"anos_vl": "Años desde inicio VL", "edad": "Edad"}


def _construir_excel_trayectorias(payload: TrayectoriaExportRequest) -> bytes:
    """Hoja 'Colectivos' (igual que en _construir_excel de colectivos.py) +
    una hoja por eje (Años desde inicio de vida laboral / Edad), en formato largo (una fila
    por punto - o por punto+categoria en categoricas, ver conversacion
    2026-08-28): mas facil de manejar en Excel que un formato ancho cuando la
    variable tiene muchas categorias (Provincia, ~53) sin cambiar de forma
    segun la variable.

    `resultado` (WYSIWYG, ver cabecera del fichero) trae las claves de los
    grupos realmente calculados - los 4 subgrupos de siempre, o un unico
    GRUPO_SIN_FILTRO con filtroColectivoGlobal en No -, asi que se itera
    resultado.keys() en vez de asumir ORDEN_SUBGRUPOS fijo."""
    negrita = Font(bold=True)
    wb = Workbook()
    resultado = payload.resultado
    grupos = list(resultado.keys())

    ws_col = wb.active
    ws_col.title = "Colectivos"
    ws_col.cell(row=1, column=1, value="Subcolectivo").font = negrita
    ws_col.cell(row=1, column=2, value="Personas").font = negrita
    for i, nombre in enumerate(grupos):
        ws_col.cell(row=i + 2, column=1, value=resultado[nombre]["etiqueta"])
        ws_col.cell(row=i + 2, column=2, value=resultado[nombre]["recuento"])

    if payload.variable_tipo == "numerica":
        unidad_txt = f" ({payload.unidad})" if payload.unidad else ""
        for eje, etiqueta_eje in _ETIQUETA_EJE.items():
            ws = wb.create_sheet(_NOMBRE_HOJA_EJE[eje])
            for col, titulo in enumerate(
                ["Subcolectivo", etiqueta_eje, f"Media{unidad_txt}", f"Mediana{unidad_txt}", "N"], start=1
            ):
                ws.cell(row=1, column=col, value=titulo).font = negrita
            fila = 2
            for nombre in grupos:
                etiqueta_sub = resultado[nombre]["etiqueta"]
                for punto in resultado[nombre]["series"][eje]:
                    ws.cell(row=fila, column=1, value=etiqueta_sub)
                    ws.cell(row=fila, column=2, value=punto["x"])
                    ws.cell(row=fila, column=3, value=punto["media"])
                    ws.cell(row=fila, column=4, value=punto["mediana"])
                    ws.cell(row=fila, column=5, value=punto["n"])
                    fila += 1
    else:
        for eje, etiqueta_eje in _ETIQUETA_EJE.items():
            ws = wb.create_sheet(_NOMBRE_HOJA_EJE[eje])
            for col, titulo in enumerate(
                ["Subcolectivo", etiqueta_eje, "N total", "Categoría", "N categoría", "%"], start=1
            ):
                ws.cell(row=1, column=col, value=titulo).font = negrita
            fila = 2
            for nombre in grupos:
                etiqueta_sub = resultado[nombre]["etiqueta"]
                for punto in resultado[nombre]["series"][eje]:
                    for categoria, v in punto["valores"].items():
                        ws.cell(row=fila, column=1, value=etiqueta_sub)
                        ws.cell(row=fila, column=2, value=punto["x"])
                        ws.cell(row=fila, column=3, value=punto["n"])
                        ws.cell(row=fila, column=4, value=categoria)
                        ws.cell(row=fila, column=5, value=v["n"])
                        ws.cell(row=fila, column=6, value=v["pct"])
                        fila += 1

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


@router.post("/trayectorias/colectivo/exportar")
def exportar_trayectorias(payload: TrayectoriaExportRequest):
    contenido = _construir_excel_trayectorias(payload)
    return StreamingResponse(
        io.BytesIO(contenido),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=trayectorias.xlsx"},
    )


def _trayectoria_categorica_periodica(con, condiciones: dict, info: dict) -> dict:
    tabla, columna, columna_anio = info["tabla"], info["columna"], info["columna_anio"]

    resultado = {}
    for nombre, c in condiciones.items():
        recuento = con.execute(f"SELECT COUNT(*) FROM personas WHERE {c['where']}", c["params"]).fetchone()[0]

        filas = con.execute(
            f"""
            WITH colectivo AS (
                SELECT ipf, fecha_nacimiento, fecha_ini_primer_empleo
                FROM personas
                WHERE {c['where']}
            ), datos AS (
                -- DISTINCT: si la tabla de origen tuviera una fila repetida
                -- para el mismo (ipf, anyo, categoria) no debe contar doble.
                SELECT DISTINCT t.ipf, t.{columna_anio} AS anio, t.{columna} AS categoria
                FROM {tabla} t
                WHERE t.ipf IN (SELECT ipf FROM colectivo) AND t.{columna} IS NOT NULL
            ), unido AS (
                SELECT
                    d.ipf, d.categoria,
                    CASE WHEN c.fecha_ini_primer_empleo IS NOT NULL
                         THEN (d.anio - date_part('year', c.fecha_ini_primer_empleo))::INTEGER END AS anos_vl,
                    (d.anio - date_part('year', c.fecha_nacimiento))::INTEGER AS edad
                FROM datos d
                JOIN colectivo c USING (ipf)
            ), total_por_x AS (
                -- Denominador: IPFs distintos en ese punto, sin distinguir
                -- categoria (una persona con 2 categorias el mismo punto
                -- cuenta 1 vez aqui, aunque cuente en las 2 categorias abajo).
                SELECT 'anos_vl' AS eje, anos_vl AS x, COUNT(DISTINCT ipf) AS n
                FROM unido WHERE anos_vl >= 0 GROUP BY 1, 2
                UNION ALL
                -- edad >= 16: mismo criterio que Bases/Pensiones (numerico).
                SELECT 'edad' AS eje, edad AS x, COUNT(DISTINCT ipf) AS n
                FROM unido WHERE edad >= 16 GROUP BY 1, 2
            ), por_categoria AS (
                SELECT 'anos_vl' AS eje, anos_vl AS x, categoria, COUNT(DISTINCT ipf) AS n_categoria
                FROM unido WHERE anos_vl >= 0 GROUP BY 1, 2, 3
                UNION ALL
                SELECT 'edad' AS eje, edad AS x, categoria, COUNT(DISTINCT ipf) AS n_categoria
                FROM unido WHERE edad >= 16 GROUP BY 1, 2, 3
            )
            SELECT t.eje, t.x, t.n, pc.categoria, pc.n_categoria
            FROM total_por_x t
            JOIN por_categoria pc USING (eje, x)
            ORDER BY 1, 2, 4
            """,
            c["params"],
        ).fetchall()

        resultado[nombre] = _empaquetar_categorica(c["etiqueta"], recuento, filas)

    return resultado


def _trayectoria_categorica_afiliacion(con, condiciones: dict, info: dict) -> dict:
    """Version episodica de la trayectoria categorica: primero expande cada
    episodio de Afiliacion a los anyos en los que esta vigente a 31 de
    diciembre (_CTE_ANIOS_VIGENTES), despues mismo patron que la periodica
    (IPFs distintos por categoria y punto, denominador = IPFs distintos
    totales en ese punto)."""
    expr = info["expr"]
    top_n = info.get("top_n")
    decodificar_base = info.get("decodificar", _decodificar_identidad)

    if top_n:
        # Top-N global (toda la tabla afiliacion, no solo el colectivo
        # filtrado): asi 'Resto' significa siempre el mismo conjunto de
        # codigos, sea cual sea el colectivo que se construya despues. -1
        # como cubeta de "Resto" (compacta, no texto - ver comentario junto
        # a VARIABLES_CATEGORICAS): ningun codigo real de CNAE2009_CC (unica
        # variable con top_n hoy) es negativo.
        top_categorias = [
            fila[0]
            for fila in con.execute(
                f"SELECT {expr} AS categoria, COUNT(*) AS n FROM afiliacion t "
                f"WHERE ({expr}) IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT {top_n}"
            ).fetchall()
        ]
        placeholders = ", ".join(["?"] * len(top_categorias))
        categoria_expr = f"CASE WHEN ({expr}) IN ({placeholders}) THEN ({expr}) ELSE -1 END"
        params_extra = list(top_categorias)
        decodificar = lambda cubeta: "Resto" if cubeta == -1 else decodificar_base(cubeta)  # noqa: E731
    else:
        categoria_expr = expr
        params_extra = []
        decodificar = decodificar_base

    resultado = {}
    for nombre, c in condiciones.items():
        recuento = con.execute(f"SELECT COUNT(*) FROM personas WHERE {c['where']}", c["params"]).fetchone()[0]

        filas = con.execute(
            f"""
            WITH colectivo AS (
                SELECT ipf, fecha_nacimiento, fecha_ini_primer_empleo
                FROM personas
                WHERE {c['where']}
            ), episodios AS (
                SELECT
                    t.ipf, t.fecha_alta_real_afil AS alta, t.baja_modificada AS baja,
                    {categoria_expr} AS categoria
                FROM afiliacion t
                WHERE t.ipf IN (SELECT ipf FROM colectivo) AND ({expr}) IS NOT NULL
            ), anios AS (
                SELECT ipf, categoria, {_CTE_ANIOS_VIGENTES}
                FROM episodios
            ), expandido AS (
                -- Sin DISTINCT aqui a proposito (revienta por memoria en esta
                -- maquina con categorias de texto largo - visto con datos
                -- reales, OutOfMemoryException con GRU_COTIZACION): no hace
                -- falta, total_por_x/por_categoria ya deduplican con
                -- COUNT(DISTINCT ipf) mas abajo, asi que una persona con 2
                -- episodios de la MISMA categoria en el mismo anyo sigue
                -- contando 1 vez pese a llegar aqui en 2 filas; con
                -- categoria distinta cuentan aparte en su categoria - eso es
                -- lo que permite superar el 100%.
                SELECT ipf, categoria, UNNEST(generate_series(anio_ini, anio_fin)) AS anio
                FROM anios WHERE anio_fin >= anio_ini
            ), unido AS (
                SELECT
                    e.ipf, e.categoria,
                    CASE WHEN c.fecha_ini_primer_empleo IS NOT NULL
                         THEN (e.anio - date_part('year', c.fecha_ini_primer_empleo))::INTEGER END AS anos_vl,
                    (e.anio - date_part('year', c.fecha_nacimiento))::INTEGER AS edad
                FROM expandido e
                JOIN colectivo c USING (ipf)
            ), total_por_x AS (
                SELECT 'anos_vl' AS eje, anos_vl AS x, COUNT(DISTINCT ipf) AS n
                FROM unido WHERE anos_vl >= 0 GROUP BY 1, 2
                UNION ALL
                SELECT 'edad' AS eje, edad AS x, COUNT(DISTINCT ipf) AS n
                FROM unido WHERE edad >= 16 GROUP BY 1, 2
            ), por_categoria AS (
                SELECT 'anos_vl' AS eje, anos_vl AS x, categoria, COUNT(DISTINCT ipf) AS n_categoria
                FROM unido WHERE anos_vl >= 0 GROUP BY 1, 2, 3
                UNION ALL
                SELECT 'edad' AS eje, edad AS x, categoria, COUNT(DISTINCT ipf) AS n_categoria
                FROM unido WHERE edad >= 16 GROUP BY 1, 2, 3
            )
            SELECT t.eje, t.x, t.n, pc.categoria, pc.n_categoria
            FROM total_por_x t
            JOIN por_categoria pc USING (eje, x)
            ORDER BY 1, 2, 4
            """,
            c["params"] + params_extra,
        ).fetchall()

        resultado[nombre] = _empaquetar_categorica(c["etiqueta"], recuento, filas, decodificar)

    return resultado


# Fecha de corte de esta MCVL (misma constante que etl/personas_extendida.py,
# ambas leidas de config.py - ver comentario equivalente sobre
# reg_cotizacion_agrupado mas arriba). "07 FECHA_BAJA_REAL_AFIL" = 2099 es el
# placeholder de "episodio todavia activo en la fecha de extraccion", no una
# fecha real - _episodios_afiliacion lo convierte a None (episodio abierto) y
# el frontend dibuja la barra hasta FECHA_CORTE, nunca hasta la fecha real de
# hoy (el dato esta congelado a fecha de corte, no se actualiza solo por
# pasar el tiempo). Ya viene en formato ISO desde config.py, no hace falta
# convertir.


# Regimen/tipo de contrato agrupados con la MISMA logica que ya usa la
# trayectoria categorica de colectivo (VARIABLES_CATEGORICAS mas arriba) y el
# propio etl/personas_extendida.py - reutilizados aqui para la columna de
# cada episodio en "Trayectorias individuales", en vez de mostrar el codigo
# crudo (147 codigos de TIPO_CONTRATO, poco legibles de un vistazo).
_REGIMEN_CUBETA_SQL = reg_cotizacion_agrupado.sql_cubeta_agrupado("reg_cotizacion")
_TIPO_CONTRATO_CUBETA_SQL = tipo_contrato_claves_sucesoras.sql_cubeta_agrupado("tipo_contrato", "coef_t_parcial")


# Nombre de provincia para la columna "Prov." de "Trayectorias individuales" -
# a diferencia de TRL/CNAE (que se siguen mostrando con su codigo crudo, a
# peticion explicita del usuario), aqui si se pidio el nombre. Mismo fichero
# de referencia que ya usa el ETL para Prov_trabajo_ppal
# (etl/personas_extendida.py, grupo_h_final) pero decodificado aqui, no en el
# Parquet: es solo para esta columna de pantalla, no hace falta regenerar
# datos por un retoque de visualizacion. El nombre en el CSV ya trae el
# codigo delante ("01 ÁLAVA"); se quita aqui porque el codigo de provincia no
# es lo que se pidio mostrar (ver columna nueva de actividad economica, que
# es justo lo contrario: codigo sin nombre).
_PROVINCIAS_DICT_PATH = Path(__file__).resolve().parent.parent.parent / "etl" / "metadata" / "provincias_dict.csv"


def _cargar_provincias_dict() -> dict[int, str]:
    with open(_PROVINCIAS_DICT_PATH, encoding="utf-8") as f:
        filas = csv.DictReader(f, delimiter=";")
        return {int(fila["codigo"]): fila["nombre"].split(" ", 1)[1] for fila in filas}


_PROVINCIAS_DICT = _cargar_provincias_dict()


def _decodificar_provincia(codigo) -> str | None:
    if codigo is None:
        return None
    return _PROVINCIAS_DICT.get(int(codigo), "NO CONSTA")


def _episodios_afiliacion(con, ipf: int) -> list[dict]:
    """Episodios de afiliacion de un IPF, ordenados por fecha de alta - usado
    tanto por la pagina propia /trayectorias/{ipf} como por el endpoint JSON
    de la pestana "Trayectorias individuales" de colectivos.html. TRL y
    actividad economica (CNAE2009_CC) se dejan con su codigo crudo (a
    peticion del usuario, mismo criterio que ya usa la trayectoria categorica
    de colectivo con estas variables - "no hay diccionario pedido para
    decodificarlos todavia"); provincia si se decodifica a nombre
    (_decodificar_provincia)."""
    episodios = con.execute(
        f"""
        SELECT
            fecha_alta_real_afil,
            fecha_baja_real_afil,
            ccc_ppal,
            trl,
            provincia_domic_cc_ppal,
            cnae2009_cc,
            gru_cotizacion,
            ({_REGIMEN_CUBETA_SQL}) AS regimen_cubeta,
            ({_TIPO_CONTRATO_CUBETA_SQL}) AS tipo_contrato_cubeta
        FROM afiliacion
        WHERE ipf = ?
        ORDER BY fecha_alta_real_afil
        """,
        [ipf],
    ).fetchall()

    return [
        {
            "empresa": ccc_ppal or "(sin CCC)",
            "inicio": fecha_alta_real_afil.isoformat() if fecha_alta_real_afil else None,
            "fin": fecha_baja_real_afil.isoformat()
            if fecha_baja_real_afil and fecha_baja_real_afil.year != 2099
            else None,
            "trl": trl,
            "provincia": _decodificar_provincia(provincia_domic_cc_ppal),
            "actividad_economica": cnae2009_cc,
            "grupo_cotizacion": gru_cotizacion,
            "regimen": reg_cotizacion_agrupado.decodificar_agrupado(regimen_cubeta),
            "tipo_contrato": tipo_contrato_claves_sucesoras.decodificar_agrupado(tipo_contrato_cubeta),
        }
        for (
            fecha_alta_real_afil,
            fecha_baja_real_afil,
            ccc_ppal,
            trl,
            provincia_domic_cc_ppal,
            cnae2009_cc,
            gru_cotizacion,
            regimen_cubeta,
            tipo_contrato_cubeta,
        ) in episodios
    ]


def _persona_resumen(con, ipf: int) -> dict:
    """Sexo/edad/nacionalidad de la cabecera de "Trayectorias individuales" -
    a diferencia de los episodios, son datos de la PERSONA (constantes, no
    cambian entre episodios), asi que se muestran una sola vez."""
    fila = con.execute(
        "SELECT sexo, edad_actual_fecha_corte, nacionalidad FROM personas WHERE ipf = ?",
        [ipf],
    ).fetchone()
    if fila is None:
        # IPF inexistente (p.ej. /trayectorias/999999999 escrito a mano): antes
        # esto desempaquetaba None y daba un 500 (encontrado en la prueba de
        # humo del 2026-09-21).
        raise HTTPException(404, f"No existe el IPF {ipf}")
    sexo, edad, nacionalidad = fila
    return {"sexo": sexo, "edad": edad, "nacionalidad": nacionalidad}


class TrayectoriaAleatoriaRequest(BaseModel):
    """Body opcional del switch "Aplicar filtro de colectivo" en la pestana
    "Trayectorias individuales": sin `colectivo`, IPF al azar de toda
    Personas (switch en No, comportamiento de siempre). Con `colectivo` (los
    mismos filtro1-4 que ya define el panel compartido con Analisis/
    Trayectorias de colectivo), `subgrupo` elige de cual de los 1-4
    subgrupos del cruce sacar el aleatorio - una persona puede pertenecer a
    varios subgrupos NO son excluyentes entre si salvo que sean cada eje su
    propio complementario, asi que hace falta saber a cual."""

    colectivo: SubgruposRequest | None = None
    subgrupo: str | None = None


@router.post("/trayectorias/individual/aleatorio")
def trayectoria_individual_aleatoria(request: Request, body: TrayectoriaAleatoriaRequest = TrayectoriaAleatoriaRequest()):
    """IPF al azar (de Personas, no de Afiliacion - para no favorecer a quien
    tiene mas episodios) + sus episodios, para el boton "IPF aleatorio" de la
    pestana "Trayectorias individuales" en colectivos.html. USING SAMPLE (no
    ORDER BY random() LIMIT 1) para no forzar un sort de 1,36M filas solo
    para escoger 1."""
    con = request.app.state.con.cursor()
    if body.colectivo is None:
        ipf = con.execute("SELECT ipf FROM personas USING SAMPLE 1").fetchone()[0]
    else:
        condiciones = condiciones_de_subgrupos(con, body.colectivo)
        if body.subgrupo not in condiciones:
            raise HTTPException(400, "subgrupo invalido")
        c = condiciones[body.subgrupo]
        # Subconsulta imprescindible (bug real de DuckDB verificado con datos
        # sinteticos): "SELECT ... WHERE cond USING SAMPLE n" (n filas, no
        # porcentaje) aplica el muestreo ANTES del WHERE, no despues -
        # devuelve NULL en cuanto el filtro es algo selectivo, aunque haya
        # miles de filas que lo cumplan. Envolviendo el WHERE en una
        # subconsulta y aplicando USING SAMPLE a esa subconsulta ya resuelta,
        # el muestreo si actua sobre las filas ya filtradas.
        fila = con.execute(
            f"SELECT ipf FROM (SELECT ipf FROM personas WHERE {c['where']}) USING SAMPLE 1",
            c["params"],
        ).fetchone()
        if fila is None:
            raise HTTPException(404, "Este subgrupo no tiene ninguna persona")
        ipf = fila[0]
    return {
        "ipf": ipf,
        "persona": _persona_resumen(con, ipf),
        "episodios": _episodios_afiliacion(con, ipf),
        "fecha_corte": estado.muestra.fecha_corte,
    }


@router.get("/trayectorias/{ipf}", response_class=HTMLResponse)
def trayectoria(request: Request, ipf: int):
    con = request.app.state.con.cursor()
    return templates.TemplateResponse(
        request,
        "trayectoria.html",
        {
            "ipf": ipf,
            "persona_json": json.dumps(_persona_resumen(con, ipf), ensure_ascii=False),
            "episodios_json": json.dumps(_episodios_afiliacion(con, ipf), ensure_ascii=False),
            "fecha_corte": estado.muestra.fecha_corte,
        },
    )
