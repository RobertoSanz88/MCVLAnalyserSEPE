"""Trayectorias: individual (episodios de afiliacion de un IPF) y de colectivo
(evolucion anual de una variable, superponiendo los 4 subgrupos de un
colectivo ya definido en /colectivos). La vista de colectivo vive como
pestana dentro de colectivos.html (no como pagina propia), asi que este
modulo solo expone el endpoint de datos, no una ruta HTML."""

import json
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.routers.colectivos import ORDEN_SUBGRUPOS, SubgruposRequest, calcular_condiciones_subgrupos

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# Registro de variables del motor generico de "trayectoria categorica" (ver
# trayectorias_colectivo_categorica): que tabla/columna consultar para cada
# variable. tipo_pension es la primera; TIPO_CONTRATO y GRU_COTIZACION de
# Afiliacion se anyadiran aqui mas adelante - esa tabla es episodica
# (fecha_alta/fecha_baja, no una fila por anyo como pensiones/bases), asi que
# necesitara antes un paso propio que expanda episodios a filas por anyo
# antes de poder reutilizar esta misma query tal cual.
VARIABLES_CATEGORICAS = {
    "tipo_pension": {"tabla": "pensiones", "columna": "tipo_pension", "columna_anio": "ano_dato"},
}


class TrayectoriaCategoricaRequest(SubgruposRequest):
    variable: Literal["tipo_pension"]


@router.post("/trayectorias/colectivo/bases")
def trayectorias_colectivo_bases(request: Request, body: SubgruposRequest):
    """Evolucion de 'bases anuales' (CA+CP sumadas por ipf-anyo) de los 4
    subgrupos de un colectivo, agregada tanto por anyos de vida laboral como
    por edad - el frontend elige con cual pintar el grafico sin pedir otra
    vez al backend. N por punto para que el frontend pueda cortar/atenuar la
    linea donde la muestra es pequenya (< 30)."""
    con = request.app.state.con.cursor()
    condiciones = calcular_condiciones_subgrupos(con, body.filtro1, body.filtro2, body.filtro3, body.filtro4)

    resultado = {}
    for nombre in ORDEN_SUBGRUPOS:
        c = condiciones[nombre]
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
            -- edad >= 18 (no >= 0): investigado el 2026-08-07 por que aparecian
            -- edades menores. El 96,5% de las filas con edad<18 son edad 16-17
            -- (trabajo legal en Espana desde los 16, dato real, no error); el
            -- resto es una cola suave hasta valores negativos (~5.700 filas,
            -- 0,02% del total) - unos pocos casos de fecha_nacimiento o
            -- ano_cotizacion erroneos en el dato MCVL, no un fallo de esta
            -- query. Decision explicita del usuario: no representar nada por
            -- debajo de 18 en ningun caso, exista o no.
            SELECT 'edad' AS eje, edad AS x, AVG(bases_anuales), MEDIAN(bases_anuales), COUNT(*)
            FROM unido WHERE edad >= 18 GROUP BY 1, 2
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
    """Evolucion de la pension anual (Ingresos_pension_bruto_anual) de los 4
    subgrupos de un colectivo - mismo patron que /trayectorias/colectivo/bases.
    Ingresos_pension_bruto_anual ya viene calculada en el ETL a partir de
    IMPORTE_MES_TOTAL_PRESTACION con su excepcion de 12 pagas (clase de
    prestacion 17, ~0,002% de las filas) en vez de 14 - no se recalcula ese
    x14 aqui para no perder esa excepcion (ver fase2_pensiones_extendida en
    etl/personas_extendida.py). No hay variable mensual aparte: es la misma
    cifra sin anualizar, redundante con esta. Si una persona cobra mas de una
    pension el mismo anyo (p.ej. viudedad + orfandad), se suman."""
    con = request.app.state.con.cursor()
    condiciones = calcular_condiciones_subgrupos(con, body.filtro1, body.filtro2, body.filtro3, body.filtro4)

    resultado = {}
    for nombre in ORDEN_SUBGRUPOS:
        c = condiciones[nombre]
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
            -- edad >= 18: mismo criterio ya aplicado en Bases de cotizacion
            -- (ver /trayectorias/colectivo/bases), misma naturaleza de dato.
            SELECT 'edad' AS eje, edad AS x, AVG(valor), MEDIAN(valor), COUNT(*)
            FROM unido WHERE edad >= 18 GROUP BY 1, 2
            ORDER BY 1, 2
            """,
            c["params"],
        ).fetchall()

        series = {"anos_vl": [], "edad": []}
        for eje, x, media, mediana, n in filas:
            series[eje].append({"x": x, "media": media, "mediana": mediana, "n": n})

        resultado[nombre] = {"etiqueta": c["etiqueta"], "recuento": recuento, "series": series}

    return resultado


@router.post("/trayectorias/colectivo/categorica")
def trayectorias_colectivo_categorica(request: Request, body: TrayectoriaCategoricaRequest):
    """Motor generico de 'trayectoria categorica': evolucion de la
    composicion categorica (p.ej. Tipo_pension) de los 4 subgrupos de un
    colectivo. Pensado para reutilizarse con TIPO_CONTRATO/GRU_COTIZACION de
    Afiliacion mas adelante (ver VARIABLES_CATEGORICAS) - esta query en si ya
    es generica (tabla/columna vienen del registro), solo falta que Afiliacion
    tenga una tabla en forma de una fila por (ipf, anyo) como pensiones.

    Para cada punto del eje X, el % de cada categoria se calcula sobre el
    numero de IPFs DISTINTOS del colectivo en ese punto (no sobre filas): una
    persona puede tener varias filas el mismo anyo con distinta categoria
    (p.ej. cobra viudedad y orfandad a la vez), asi que los porcentajes de un
    mismo punto pueden sumar mas de 100% - no es una distribucion exclusiva,
    el frontend lo deja explicito. N (personas distintas del colectivo en ese
    punto, sin distinguir categoria) sirve para el mismo corte por
    fiabilidad ya usado en /trayectorias/colectivo/bases y /pensiones."""
    con = request.app.state.con.cursor()
    condiciones = calcular_condiciones_subgrupos(con, body.filtro1, body.filtro2, body.filtro3, body.filtro4)

    info = VARIABLES_CATEGORICAS[body.variable]
    tabla, columna, columna_anio = info["tabla"], info["columna"], info["columna_anio"]

    resultado = {}
    for nombre in ORDEN_SUBGRUPOS:
        c = condiciones[nombre]
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
                -- edad >= 18: mismo criterio que Bases/Pensiones (numerico).
                SELECT 'edad' AS eje, edad AS x, COUNT(DISTINCT ipf) AS n
                FROM unido WHERE edad >= 18 GROUP BY 1, 2
            ), por_categoria AS (
                SELECT 'anos_vl' AS eje, anos_vl AS x, categoria, COUNT(DISTINCT ipf) AS n_categoria
                FROM unido WHERE anos_vl >= 0 GROUP BY 1, 2, 3
                UNION ALL
                SELECT 'edad' AS eje, edad AS x, categoria, COUNT(DISTINCT ipf) AS n_categoria
                FROM unido WHERE edad >= 18 GROUP BY 1, 2, 3
            )
            SELECT t.eje, t.x, t.n, pc.categoria, pc.n_categoria
            FROM total_por_x t
            JOIN por_categoria pc USING (eje, x)
            ORDER BY 1, 2, 4
            """,
            c["params"],
        ).fetchall()

        series = {"anos_vl": {}, "edad": {}}
        categorias_vistas = []
        for eje, x, n, categoria, n_categoria in filas:
            punto = series[eje].setdefault(x, {"x": x, "n": n, "valores": {}})
            punto["valores"][categoria] = {"n": n_categoria, "pct": round(100 * n_categoria / n, 1) if n else 0.0}
            if categoria not in categorias_vistas:
                categorias_vistas.append(categoria)

        series = {eje: sorted(puntos.values(), key=lambda p: p["x"]) for eje, puntos in series.items()}

        resultado[nombre] = {
            "etiqueta": c["etiqueta"],
            "recuento": recuento,
            "categorias": categorias_vistas,
            "series": series,
        }

    return resultado


@router.get("/trayectorias/{ipf}", response_class=HTMLResponse)
def trayectoria(request: Request, ipf: int):
    con = request.app.state.con.cursor()
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
