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

# GRU_COTIZACION/REG_COTIZACION reutilizan la categorizacion ya definida en
# el ETL (GRUPOS_COTIZ_DICT / _clasificar_regimen en
# etl/personas_extendida.py, Fase H) en vez de reimplementarla desde cero -
# pero aqui la "expr" de cada variable en VARIABLES_CATEGORICAS produce un
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
# GRU_COTIZACION: 0-11 es su propio codigo (mapeo 1 a 1, ver
# _GRUPOS_COTIZ_DICT), fuera de ese dominio se pliega a -1 ("NO CONSTA",
# ~5,3% de las filas, dominado por el codigo 13 - ver informe al usuario).
_SQL_CUBETA_GRU_COTIZACION = "CASE WHEN t.gru_cotizacion BETWEEN 0 AND 11 THEN t.gru_cotizacion ELSE -1 END"

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
}


def _decodificar_gru_cotizacion(cubeta) -> str:
    if cubeta is None or cubeta == -1:
        return "NO CONSTA"
    return _GRUPOS_COTIZ_DICT.get(int(cubeta), "NO CONSTA")


# REG_COTIZACION: replica _clasificar_regimen (codigo 138 -> caso especial,
# si no el 3er digito desde el final decide el regimen - "2" requiere ademas
# que el 4o digito sea "1" -, cualquier otro caso -> NO CONSTA/cubeta 0) pero
# devolviendo una cubeta SMALLINT 0-8, no la etiqueta de texto. SUBSTR(x,-3,1)
# en DuckDB es el mismo caracter que codigo_str[-3] en Python (verificado).
# Verificado tambien que las 9 etiquetas (via _ETIQUETAS_REG_COTIZACION)
# coinciden exactamente con _clasificar_regimen para las 43 REG_COTIZACION
# reales del Parquet.
_SQL_CUBETA_REG_COTIZACION = """
    CASE
        WHEN t.reg_cotizacion IS NULL THEN 0
        WHEN t.reg_cotizacion = 138 THEN 8
        WHEN LENGTH(CAST(t.reg_cotizacion AS VARCHAR)) < 3 THEN 0
        WHEN SUBSTR(CAST(t.reg_cotizacion AS VARCHAR), -3, 1) = '1' THEN 1
        WHEN SUBSTR(CAST(t.reg_cotizacion AS VARCHAR), -3, 1) = '5' THEN 2
        WHEN SUBSTR(CAST(t.reg_cotizacion AS VARCHAR), -3, 1) = '6' THEN 3
        WHEN SUBSTR(CAST(t.reg_cotizacion AS VARCHAR), -3, 1) = '7' THEN 4
        WHEN SUBSTR(CAST(t.reg_cotizacion AS VARCHAR), -3, 1) = '8' THEN 5
        WHEN SUBSTR(CAST(t.reg_cotizacion AS VARCHAR), -3, 1) = '9' THEN 6
        WHEN SUBSTR(CAST(t.reg_cotizacion AS VARCHAR), -3, 1) = '2'
             AND LENGTH(CAST(t.reg_cotizacion AS VARCHAR)) >= 4
             AND SUBSTR(CAST(t.reg_cotizacion AS VARCHAR), -4, 1) = '1' THEN 7
        ELSE 0
    END
"""

_ETIQUETAS_REG_COTIZACION = {
    0: "NO CONSTA",
    1: "RÉGIMEN GENERAL",
    2: "R.E. AUTÓNOMOS",
    3: "R.E. AGRARIO CUENTA AJENA",
    4: "R.E. AGRARIO CUENTA PROPIA",
    5: "R.E. MAR",
    6: "R.E. MINERÍA CARBÓN",
    7: "R.E. EMPLEADOS HOGAR",
    8: "RÉGIMEN GENERAL (S.E.C. EMPLEADOS HOGAR)",
}


def _decodificar_reg_cotizacion(cubeta) -> str:
    return _ETIQUETAS_REG_COTIZACION.get(cubeta, "NO CONSTA")


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
# en la cubeta -1 ("Resto"). TIPO_CONTRATO queda fuera a proposito: 147
# codigos crudos, muy asimetrico, y el bucketing existente en Personas
# (Tipo_Contrato_ppal_Agrupado) no corrige claves sucesoras - mismo problema
# de fondo, no es sustituto valido.
VARIABLES_CATEGORICAS = {
    "tipo_pension": {"tabla": "pensiones", "columna": "tipo_pension", "columna_anio": "ano_dato"},
    "trl": {"tabla": "afiliacion", "expr": "t.trl"},
    "gru_cotizacion": {"tabla": "afiliacion", "expr": _SQL_CUBETA_GRU_COTIZACION, "decodificar": _decodificar_gru_cotizacion},
    "reg_cotizacion": {"tabla": "afiliacion", "expr": _SQL_CUBETA_REG_COTIZACION, "decodificar": _decodificar_reg_cotizacion},
    "cnae2009_cc": {"tabla": "afiliacion", "expr": "t.cnae2009_cc", "top_n": 20},
    "provincia_domic_cc_ppal": {"tabla": "afiliacion", "expr": "t.provincia_domic_cc_ppal"},
}


class TrayectoriaCategoricaRequest(SubgruposRequest):
    variable: Literal["tipo_pension", "trl", "gru_cotizacion", "reg_cotizacion", "cnae2009_cc", "provincia_domic_cc_ppal"]


# Expresion SQL compartida por la trayectoria categorica Y la numerica de
# Afiliacion: para cada episodio, en que anyos esta vigente a 31 de
# diciembre (fecha_alta_real_afil <= 31/12/anyo Y baja_modificada >=
# 31/12/anyo). Se usa Baja_modificada, no 07 FECHA_BAJA_REAL_AFIL: ya trae
# resuelto el placeholder 2099 de episodios todavia activos (Fase 1,
# Alta_desplazada/Baja_modificada en etl/personas_extendida.py). anio_fin se
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


@router.post("/trayectorias/colectivo/afiliacion")
def trayectorias_colectivo_afiliacion(request: Request, body: SubgruposRequest):
    """Evolucion de NUM_TRABAJS_CC_2 (tamanyo de la empresa) de los 4
    subgrupos de un colectivo - mismo contrato de salida que
    /trayectorias/colectivo/bases y /pensiones (series anos_vl/edad con
    x/media/mediana/n), pero calculado distinto por debajo: Afiliacion es
    episodica (fecha_alta/baja_modificada), no una fila por anyo, asi que
    primero se expande a (ipf, anyo) con el episodio vigente a 31 de
    diciembre (ver _CTE_ANIOS_VIGENTES, mismo criterio que la trayectoria
    categorica de Afiliacion).

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
    Reg_cotiz_ppal en etl/personas_extendida.py)."""
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
    condiciones = calcular_condiciones_subgrupos(con, body.filtro1, body.filtro2, body.filtro3, body.filtro4)
    info = VARIABLES_CATEGORICAS[body.variable]

    if info["tabla"] == "afiliacion":
        return _trayectoria_categorica_afiliacion(con, condiciones, info)
    return _trayectoria_categorica_periodica(con, condiciones, info)


def _trayectoria_categorica_periodica(con, condiciones: dict, info: dict) -> dict:
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
    for nombre in ORDEN_SUBGRUPOS:
        c = condiciones[nombre]
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
            c["params"] + params_extra,
        ).fetchall()

        resultado[nombre] = _empaquetar_categorica(c["etiqueta"], recuento, filas, decodificar)

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
