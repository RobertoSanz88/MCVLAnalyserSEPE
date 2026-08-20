"""Optimizada -> Personas extendida. Transcripcion de
"2. Construccion de Tabla Personas Extendida.ipynb" (version con Baja_modificada en
Fecha_final_Ultimo_Empleo y edades en meses/12, la que ya coincide con el Parquet real actual).

Mismo patron de memoria que el cuaderno: cada funcion de fase/grupo carga solo lo que necesita,
calcula, y el llamador decide cuando liberar con del/gc.collect(). fusionar_grupo() encapsula el
patron repetido (cargar personas_extendida, fusionar extracciones por 01 IPF, guardar) que en el
cuaderno aparecia identico 7 veces.
"""

import gc
import os
import re

import numpy as np
import pandas as pd

from etl import reg_cotizacion_agrupado
from etl.tipo_contrato_claves_sucesoras import decodificar_agrupado, sql_bin_corregido, sql_corregido

UN_DIA = pd.Timedelta(days=1)
CERO_DIAS = pd.Timedelta(days=0)

# Sustituye el placeholder 2099 (episodio de afiliacion todavia activo) de FECHA_BAJA_REAL_AFIL
FECHA_CORTE = pd.to_datetime("2025-06-01")


def _ruta(parquet_folder, nombre_parquet):
    return os.path.join(parquet_folder, nombre_parquet)


# ---------------------------------------------------------------------------
# FASE 1 - Afiliacion extendida
# ---------------------------------------------------------------------------

def fase1_afiliacion_extendida(parquet_folder, output_name="tabla_afiliacion_extendida.parquet"):
    """Lee tabla_afiliacion_optimizada.parquet y anyade Baja_modificada, DURACION_EMPLEO,
    DURACION_DESEMPLEO, Alta_desplazada y DURACION_NO_EMPLEO. Guarda el resultado en
    `output_name` dentro de parquet_folder (parametrizable para no pisar un fichero de
    referencia existente durante la verificacion) y devuelve el DataFrame."""
    df = pd.read_parquet(_ruta(parquet_folder, "tabla_afiliacion_optimizada.parquet"))

    # Baja_modificada y DURACION_EMPLEO
    df["Baja_modificada"] = df["07 FECHA_BAJA_REAL_AFIL"].copy()
    condicion_activo = df["07 FECHA_BAJA_REAL_AFIL"].dt.year == 2099
    df.loc[condicion_activo, "Baja_modificada"] = FECHA_CORTE

    df["DURACION_EMPLEO"] = df["Baja_modificada"] - df["06 FECHA_ALTA_REAL_AFIL"] + UN_DIA

    # Los episodios con TRL 700-799 son periodos de cobro de prestacion/subsidio, no de empleo
    condicion_trl_700 = (df["15 TRL"] >= 700) & (df["15 TRL"] < 800)
    # TRL 400: la guia de campos MCVL dice literalmente "Tienen alguna peculiaridad por la
    # que no se consideran alta laboral" - igual que 700-799, no cuenta como DURACION_EMPLEO.
    # No toca DURACION_DESEMPLEO (mas abajo): esa es especificamente prestacion/subsidio de
    # desempleo (700-799), 400 no encaja ahi y no se ha pedido tratarlo como tal.
    condicion_no_empleo = condicion_trl_700 | (df["15 TRL"] == 400)
    df.loc[condicion_no_empleo, "DURACION_EMPLEO"] = CERO_DIAS

    condicion_duracion_negativa = df["DURACION_EMPLEO"] < CERO_DIAS
    df.loc[condicion_duracion_negativa, "DURACION_EMPLEO"] = pd.NaT

    # DURACION_DESEMPLEO por simetria con DURACION_EMPLEO
    df["DURACION_DESEMPLEO"] = df["Baja_modificada"] - df["06 FECHA_ALTA_REAL_AFIL"] + UN_DIA
    df.loc[~condicion_trl_700, "DURACION_DESEMPLEO"] = CERO_DIAS
    condicion_desempleo_negativa = df["DURACION_DESEMPLEO"] < CERO_DIAS
    df.loc[condicion_desempleo_negativa, "DURACION_DESEMPLEO"] = pd.NaT

    # Alta_desplazada y DURACION_NO_EMPLEO
    df.sort_values(by=["01 IPF", "06 FECHA_ALTA_REAL_AFIL"], inplace=True)
    df.reset_index(drop=True, inplace=True)

    df["Alta_desplazada"] = df["06 FECHA_ALTA_REAL_AFIL"].shift(-1)
    df["DURACION_NO_EMPLEO"] = df["Alta_desplazada"] - df["Baja_modificada"] - UN_DIA
    df.loc[condicion_trl_700, "DURACION_NO_EMPLEO"] = CERO_DIAS

    # Para el ultimo episodio de cada persona no hay "alta siguiente": el hueco llega hasta FECHA_CORTE
    indices_ultimo_empleo = df.groupby("01 IPF")["Baja_modificada"].idxmax()
    df.loc[indices_ultimo_empleo, "DURACION_NO_EMPLEO"] = (
        FECHA_CORTE - df.loc[indices_ultimo_empleo, "Baja_modificada"]
    )

    df.to_parquet(_ruta(parquet_folder, output_name), index=False)
    return df


# pandas 3.0 usa microsegundos como resolucion de fecha por defecto (datetime64[us]), no
# nanosegundos como versiones anteriores -- los Timedelta de la tabla de referencia estan en
# microsegundos. Comprobado con datos reales: con nanosegundos salia un factor 1000 de mas.
US_POR_DIA = 86_400_000_000


def fase1_afiliacion_extendida_duckdb(con, parquet_folder, output_name="tabla_afiliacion_extendida.parquet"):
    """Igual que fase1_afiliacion_extendida() pero calculado con DuckDB en vez de pandas: para
    Afiliacion (33M filas) ni siquiera la version ya optimizada cabe entera en la RAM de esta
    maquina (comprobado: pyarrow.lib.ArrowMemoryError al cargarla completa en pandas).

    DIFERENCIA DELIBERADA frente al cuaderno (acordada con el usuario tras 4 intentos fallidos
    por falta de disco): el cuaderno calcula Alta_desplazada con un shift(-1) GLOBAL (no por
    persona) sobre el DataFrame ordenado por (01 IPF, 06 FECHA_ALTA_REAL_AFIL), asi que en la
    ultima fila de cada IPF "roba" la fecha de alta de la primera fila del IPF siguiente.
    Replicar ese comportamiento exacto exige un ORDER BY global sobre 33M filas, que agotaba el
    disco disponible (~6-7 GB) en varios intentos. Aqui se usa LEAD particionado por 01 IPF (mas
    barato: no necesita el sort global), que da NULL en el ultimo episodio real de cada persona
    en vez de heredar el dato de otra persona. Solo puede diferir del cuaderno en las filas que
    sean a la vez "ultimo episodio por fecha" de su IPF y NO la fila de Baja_modificada maxima
    (la fila de Baja_modificada maxima siempre se sobreescribe con FECHA_CORTE, en ambos casos
    por igual). Verificar cuantas filas reales se ven afectadas al comparar contra la referencia.
    """
    optimizada = _ruta(parquet_folder, "tabla_afiliacion_optimizada.parquet")
    output_path = _ruta(parquet_folder, output_name)

    con.execute("""
        CREATE OR REPLACE VIEW _fase1_base AS
        SELECT *,
            COALESCE("15 TRL" >= 700 AND "15 TRL" < 800, FALSE) AS _trl700,
            -- TRL 400: la guia de campos MCVL dice literalmente "Tienen alguna peculiaridad
            -- por la que no se consideran alta laboral" - igual que 700-799, no cuenta como
            -- DURACION_EMPLEO. No afecta a DURACION_DESEMPLEO (sigue solo _trl700 mas abajo):
            -- esa es especificamente prestacion/subsidio de desempleo, 400 no encaja ahi.
            COALESCE("15 TRL" >= 700 AND "15 TRL" < 800, FALSE) OR COALESCE("15 TRL" = 400, FALSE) AS _no_empleo,
            CASE WHEN date_part('year', "07 FECHA_BAJA_REAL_AFIL") = 2099
                 THEN TIMESTAMP '2025-06-01'
                 ELSE "07 FECHA_BAJA_REAL_AFIL"
            END AS "Baja_modificada"
        FROM read_parquet('{}')
    """.format(optimizada))

    con.execute(f"""
        CREATE OR REPLACE VIEW _fase1_duraciones AS
        SELECT *,
            (date_diff('day', "06 FECHA_ALTA_REAL_AFIL", "Baja_modificada") + 1) AS _dias_bruto,
            CASE WHEN _no_empleo THEN 0
                 WHEN (date_diff('day', "06 FECHA_ALTA_REAL_AFIL", "Baja_modificada") + 1) < 0 THEN NULL
                 ELSE (date_diff('day', "06 FECHA_ALTA_REAL_AFIL", "Baja_modificada") + 1) * {US_POR_DIA}
            END AS "DURACION_EMPLEO",
            CASE WHEN NOT _trl700 THEN 0
                 WHEN (date_diff('day', "06 FECHA_ALTA_REAL_AFIL", "Baja_modificada") + 1) < 0 THEN NULL
                 ELSE (date_diff('day', "06 FECHA_ALTA_REAL_AFIL", "Baja_modificada") + 1) * {US_POR_DIA}
            END AS "DURACION_DESEMPLEO"
        FROM _fase1_base
    """)

    # LEAD y ROW_NUMBER comparten la misma particion (por 01 IPF): DuckDB solo necesita agrupar
    # una vez por IPF y ordenar dentro de cada grupo pequenyo, no un sort global de 33M filas.
    con.execute(f"""
        CREATE OR REPLACE VIEW _fase1_lead AS
        SELECT *,
            LEAD("06 FECHA_ALTA_REAL_AFIL") OVER (
                PARTITION BY "01 IPF" ORDER BY "06 FECHA_ALTA_REAL_AFIL"
            ) AS "Alta_desplazada",
            CASE WHEN _trl700 THEN 0
                 ELSE (date_diff('day', "Baja_modificada",
                     LEAD("06 FECHA_ALTA_REAL_AFIL") OVER (
                         PARTITION BY "01 IPF" ORDER BY "06 FECHA_ALTA_REAL_AFIL"
                     )) - 1) * {US_POR_DIA}
            END AS "DURACION_NO_EMPLEO",
            ROW_NUMBER() OVER (
                PARTITION BY "01 IPF" ORDER BY "Baja_modificada" DESC, "06 FECHA_ALTA_REAL_AFIL" ASC
            ) AS _rn_ultimo
        FROM _fase1_duraciones
    """)

    con.execute(f"""
        COPY (
            SELECT
                * EXCLUDE (_trl700, _no_empleo, _dias_bruto, _rn_ultimo, "DURACION_NO_EMPLEO"),
                CASE WHEN _rn_ultimo = 1
                     THEN date_diff('day', "Baja_modificada", TIMESTAMP '2025-06-01') * {US_POR_DIA}
                     ELSE "DURACION_NO_EMPLEO"
                END AS "DURACION_NO_EMPLEO"
            FROM _fase1_lead
        ) TO '{output_path}' (FORMAT PARQUET)
    """)
    return output_path


def fusionar_grupo(parquet_folder, extracciones, post_fillna=None):
    """Carga tabla_personas_extendida.parquet, fusiona (left join por 01 IPF) cada extraccion de
    la lista, aplica post_fillna(df) si se da, guarda y devuelve (shape_antes, shape_despues)."""
    output_path = _ruta(parquet_folder, "tabla_personas_extendida.parquet")
    df = pd.read_parquet(output_path)
    shape_antes = df.shape

    for extraccion in extracciones:
        df = pd.merge(df, extraccion, on="01 IPF", how="left")

    if post_fillna is not None:
        post_fillna(df)

    df.to_parquet(output_path, index=False)
    shape_despues = df.shape

    del df
    gc.collect()
    return shape_antes, shape_despues


# ---------------------------------------------------------------------------
# FASE 3 - Bootstrap
# ---------------------------------------------------------------------------

def bootstrap_personas_extendida(con, parquet_folder):
    """Copia tabla_personas_optimizada.parquet como punto de partida de
    tabla_personas_extendida.parquet (igual que el cuaderno)."""
    origen = _ruta(parquet_folder, "tabla_personas_optimizada.parquet")
    destino = _ruta(parquet_folder, "tabla_personas_extendida.parquet")
    con.execute(f"COPY (SELECT * FROM read_parquet('{origen}')) TO '{destino}' (FORMAT PARQUET)")
    return destino


# ---------------------------------------------------------------------------
# GRUPO A - Afiliacion extendida: regimen/grupo/contrato principal
# ---------------------------------------------------------------------------

def extraer_grupo_a(con, parquet_folder):
    """Devuelve las 4 extracciones del Grupo A como DataFrames pandas (pequenyos,
    a lo sumo un IPF por fila). Calculado con DuckDB sobre afiliacion_extendida
    (33M filas) para no cargarla en pandas."""
    afiliacion = _ruta(parquet_folder, "tabla_afiliacion_extendida.parquet")

    # Reg_cotiz_ppal_Agrupado / T_tot_reg_ppal: familia de regimen (ver
    # etl/reg_cotizacion_agrupado.py) con mas DURACION_EMPLEO acumulada por
    # persona. Agrupa A NIVEL DE EPISODIO, ANTES de sumar por (ipf, familia) -
    # mismo motivo que Tipo_Contrato_ppal_Agrupado (ver mas abajo): si se
    # agrupara DESPUES de elegir el codigo crudo ganador, dos codigos de la
    # misma familia (p.ej. 111 y 140, ambos Regimen General) competirian como
    # 2 codigos distintos por la duracion de esa persona en vez de sumar
    # juntos - verificado con datos reales que esto pasaba de verdad: 6.571
    # personas cambiaban de familia ganadora, y otras 227.130 se quedaban en
    # la misma familia pero con una duracion acumulada incorrecta.
    cubeta_reg = reg_cotizacion_agrupado.sql_cubeta_agrupado('"02 REG_COTIZACION"')
    df_reg = con.execute(f"""
        WITH sumado AS (
            SELECT "01 IPF" AS ipf, ({cubeta_reg}) AS cubeta, SUM("DURACION_EMPLEO") AS total
            FROM read_parquet('{afiliacion}') GROUP BY 1, 2
        ), ranked AS (
            SELECT ipf, cubeta, total,
                   ROW_NUMBER() OVER (PARTITION BY ipf ORDER BY total DESC, cubeta ASC) AS rn
            FROM sumado
        )
        SELECT ipf AS "01 IPF", cubeta, total AS "T_tot_reg_ppal"
        FROM ranked WHERE rn = 1
    """).fetchdf()
    df_reg["Reg_cotiz_ppal_Agrupado"] = df_reg["cubeta"].map(reg_cotizacion_agrupado.decodificar_agrupado)
    df_reg = df_reg.drop(columns=["cubeta"])

    # Grupo_cotiz_ppal / t_tot_grupo_ppal: grupo de cotizacion con mas duracion acumulada.
    # La guia de campos MCVL solo documenta claves 00-12 para este campo, y dice literalmente
    # que "en determinados regimenes como el R.E. de Autonomos... no se asigna grupo de
    # cotizacion" y que "valores distintos de los que figuran en la tabla deben considerarse
    # como casos en que el dato no consta". Verificado con datos reales: claves fuera de 00-12
    # (13, 14, 19, 20, 21, 23, 31, 63 en la muestra actual) aparecen casi todas en regimenes de
    # cuenta propia/autonomos (0721 R.E. Agrario Cuenta Propia, o ligadas a TRL 751/752 de
    # prestacion de desempleo) - exactamente el caso que describe la guia, no una tabla de
    # categorias real sin documentar. Se excluyen del ranking (like NULL) en vez de listar los
    # codigos concretos vistos hoy, para cubrir tambien claves fuera de rango que aparezcan en
    # futuras ediciones de la MCVL sin tener que tocar este codigo otra vez.
    df_grupo = con.execute(f"""
        WITH sumado AS (
            SELECT "01 IPF" AS ipf, "03 GRU_COTIZACION" AS val, SUM("DURACION_EMPLEO") AS total
            FROM read_parquet('{afiliacion}') WHERE "03 GRU_COTIZACION" BETWEEN 0 AND 12 GROUP BY 1, 2
        ), ranked AS (
            SELECT ipf, val, total,
                   ROW_NUMBER() OVER (PARTITION BY ipf ORDER BY total DESC, val ASC) AS rn
            FROM sumado
        )
        SELECT ipf AS "01 IPF", val AS "Grupo_cotiz_ppal", total AS "t_tot_grupo_ppal"
        FROM ranked WHERE rn = 1
    """).fetchdf()

    # t_contrato_mas_largo: episodio individual mas largo de cada persona
    df_maslargo = con.execute(f"""
        WITH validos AS (
            SELECT "01 IPF" AS ipf, "DURACION_EMPLEO" AS dur
            FROM read_parquet('{afiliacion}') WHERE "DURACION_EMPLEO" IS NOT NULL
        ), ranked AS (
            SELECT ipf, dur, ROW_NUMBER() OVER (PARTITION BY ipf ORDER BY dur DESC) AS rn
            FROM validos
        )
        SELECT ipf AS "01 IPF", dur AS "t_contrato_mas_largo" FROM ranked WHERE rn = 1
    """).fetchdf()

    # Tipo_Contrato_ppal_Agrupado / T_tot_tipo_contrato_ppal.
    # Corrige claves sucesoras de 04 TIPO_CONTRATO A NIVEL DE EPISODIO, antes
    # de sumar DURACION_EMPLEO por (ipf, codigo) - no despues de decidir el
    # contrato principal. Importa por que: si se corrigiera solo el codigo ya
    # ganador, un codigo deprecado (p.ej. "001", sucesora 100) y su propio
    # sucesor "100" competirian como 2 codigos distintos por la duracion
    # acumulada de esa persona, en vez de sumar juntos como el mismo tipo de
    # contrato real - podia hacer ganar a un tercer codigo con menos duracion
    # real. Logica de correccion importada de
    # etl/tipo_contrato_claves_sucesoras.py (compartida con la trayectoria
    # categorica de Afiliacion en app/, no reimplementada aqui).
    val_corregido = sql_corregido('"04 TIPO_CONTRATO"', '"05 COEF_T_PARCIAL"')
    cubeta_ganador = sql_bin_corregido("val")
    df_tipo = con.execute(f"""
        WITH episodios AS (
            SELECT "01 IPF" AS ipf, ({val_corregido}) AS val, "DURACION_EMPLEO" AS duracion
            FROM read_parquet('{afiliacion}')
            WHERE "04 TIPO_CONTRATO" IS NOT NULL
        ), sumado AS (
            SELECT ipf, val, SUM(duracion) AS total
            FROM episodios GROUP BY 1, 2
        ), ranked AS (
            SELECT ipf, val, total,
                   ROW_NUMBER() OVER (PARTITION BY ipf ORDER BY total DESC, val ASC) AS rn
            FROM sumado
        )
        SELECT ipf AS "01 IPF", ({cubeta_ganador}) AS cubeta, total AS "T_tot_tipo_contrato_ppal"
        FROM ranked WHERE rn = 1
    """).fetchdf()
    # Cubeta (int compacto, ver sql_bin_corregido) -> etiqueta final, igual
    # patron ya usado en este mismo Grupo H para GRUPOS_COTIZ_DICT: 'No
    # consta' (000) y 'Otros' (990 + huerfanos sin sucesora) son categorias
    # explicitas aqui, no NaN - a diferencia de la version anterior (ELSE
    # NULL), que dejaba esos casos como NaN silencioso.
    df_tipo["Tipo_Contrato_ppal_Agrupado"] = df_tipo["cubeta"].map(decodificar_agrupado)
    df_tipo = df_tipo.drop(columns=["cubeta"])

    # DuckDB devuelve las duraciones como enteros (microsegundos, ver Fase 1), no como
    # Timedelta de pandas. Se convierten aqui para que la conversion automatica a anyos del
    # Grupo H (que detecta columnas Timedelta) las siga cogiendo igual que en el cuaderno.
    df_reg["T_tot_reg_ppal"] = pd.to_timedelta(df_reg["T_tot_reg_ppal"], unit="us")
    df_grupo["t_tot_grupo_ppal"] = pd.to_timedelta(df_grupo["t_tot_grupo_ppal"], unit="us")
    df_maslargo["t_contrato_mas_largo"] = pd.to_timedelta(df_maslargo["t_contrato_mas_largo"], unit="us")
    df_tipo["T_tot_tipo_contrato_ppal"] = pd.to_timedelta(df_tipo["T_tot_tipo_contrato_ppal"], unit="us")

    return [df_reg, df_grupo, df_maslargo, df_tipo]


# ---------------------------------------------------------------------------
# GRUPO B - Afiliacion extendida: primer/ultimo empleo
# ---------------------------------------------------------------------------

# Tipos de contrato excluidos al buscar el primer empleo "significativo" (parciales,
# practicas, formacion...) - lista original identica al cuaderno, ampliada el
# 2026-08-20 tras verificar codigo a codigo contra la tabla completa de TIPO_CONTRATO
# de la guia MCVL (existian huecos reales, no solo teoricos: afecta a 14.049 personas
# verificado con datos reales).
TIPOS_CONTRATO_A_ELIMINAR = (
    3, 4, 6, 23, 24, 25, 29, 34, 63, 81, 89, 95, 98, 200, 209, 230, 231, 239, 241, 250, 251, 252,
    253, 254, 255, 256, 257, 289, 500, 501, 502, 503, 508, 510, 518, 520, 530, 531, 540, 541, 550,
    551, 552, 557, 26, 36, 46, 58, 420, 7, 27, 37, 47, 85, 97, 404, 421,
    # Formacion/practicas/aprendizaje: familia "FORM.RD.1992/84"/"FOR.RD.1992.MIN"
    # (variantes por edad/FP/fecha), 087 (APRENDIZAJE. DURACION DETERMINADA, sucesora
    # 421 que si estaba en la lista original) y 096 (PRACTICAS discapacidad,
    # sucesora 420, tambien ya en la lista) - confirmados literalmente en la guia,
    # faltaban en el listado original.
    53, 54, 55, 56, 57, 66, 67, 68, 77, 78, 79, 87, 96,
    # Tiempo parcial identificado por "T.PAR"/"T.P." en la denominacion de la guia:
    # familia INTER.SUST.EXC (sustitucion por excedencia familiar/maternidad, variantes
    # a tiempo parcial de 1/2/3 anyos) y otros sueltos (038, 064/065, 102, 186) que
    # tambien faltaban.
    38, 64, 65, 73, 74, 76, 83, 84, 93, 94, 102, 186,
    # Codigos deprecados cuya clave sucesora se bifurca en completo/parcial (005->441
    # /541, 009->130/230, 016->410/510, 017->100/200, 049->100/200, 059->130/230,
    # 069/070/071->100/200) - mismo criterio de precaucion que ya se aplicaba al 029
    # (misma bifurcacion, ya en la lista original): al no poder saber si un episodio
    # historico concreto era completo o parcial, se excluyen por consistencia con el
    # 029 (decision explicita del usuario, 2026-08-20).
    5, 9, 16, 17, 49, 59, 69, 70, 71,
)
DURACION_MINIMA_EMPLEO_US = 270 * 86_400_000_000  # 270 dias en microsegundos


def extraer_grupo_b(con, parquet_folder):
    afiliacion = _ruta(parquet_folder, "tabla_afiliacion_extendida.parquet")
    tipos_sql = ", ".join(str(t) for t in TIPOS_CONTRATO_A_ELIMINAR)

    # Grupo_Ultimo_Empleo: grupo de cotizacion del episodio de baja mas reciente.
    # Desempate por fecha_alta DESC: el cuaderno ordena solo por (IPF, fecha_baja) y toma el
    # "last" de un sort estable -  con muchos episodios empatados a fecha_baja=2099 (placeholder
    # de "sigue activo"), el orden entre empates se hereda del orden previo del fichero, que ya
    # esta ordenado por fecha_alta ascendente (de la Fase 1) - "last" equivale a la fecha_alta
    # mas reciente entre los empatados. Verificado contra el parquet de referencia: sin este
    # desempate fallaban 28.123 personas, con el solo quedan 2.717 (empates exactos en ambas
    # fechas alta Y baja, un caso genuinamente ambiguo que no se puede desempatar sin conocer el
    # orden exacto del fichero original).
    df_ultimogrupo = con.execute(f"""
        WITH filtrado AS (
            SELECT "01 IPF" AS ipf, "03 GRU_COTIZACION" AS val, "07 FECHA_BAJA_REAL_AFIL" AS fecha_baja,
                   "06 FECHA_ALTA_REAL_AFIL" AS fecha_alta
            FROM read_parquet('{afiliacion}') WHERE "07 FECHA_BAJA_REAL_AFIL" IS NOT NULL
        ), ranked AS (
            SELECT ipf, val, ROW_NUMBER() OVER (PARTITION BY ipf ORDER BY fecha_baja DESC, fecha_alta DESC) AS rn
            FROM filtrado
        )
        SELECT ipf AS "01 IPF", val AS "Grupo_Ultimo_Empleo" FROM ranked WHERE rn = 1
    """).fetchdf()

    # Fecha_ini_Primer_Empleo sobrevive a la tabla final (ver NUEVO_ORDEN); Fecha_final_Ultimo_Empleo
    # sigue siendo intermedia (se necesita en el Grupo H para Edad_Ultimo_Empleo/
    # Anos_VL_excluye_desempleo_inicio_y_fin, pero no se expone como columna propia).
    # Las dos excluyen episodios de TRL 700-799/400 (prestacion/subsidio/otras peculiaridades sin
    # alta laboral, ver DURACION_EMPLEO) - mismo criterio en ambas, decision explicita del usuario.
    # Fecha_ini_Primer_Empleo corregida 2026-08-20 (commit 84e1ace): sin el filtro, si el episodio
    # MAS ANTIGUO de una persona era de ese tipo (10.027 personas, 0,76%), la fecha de "primer
    # empleo" no era de empleo real. Fecha_final_Ultimo_Empleo corregida el mismo dia: sin el
    # filtro, para 173.291 personas (13,2%, verificado con datos reales - la mayoria cobrando
    # actualmente del SEPE) el episodio MAS RECIENTE era de prestacion/subsidio, asi que
    # Edad_Ultimo_Empleo mostraba su edad actual (o la fecha en que dejo de cobrar) en vez de
    # cuando dejo de TRABAJAR, y Anos_VL contaba ese tiempo cobrando como si fuera vida laboral.
    # NULL para quien nunca ha tenido un episodio de empleo real (mismo caso en las dos columnas).
    df_fechas = con.execute(f"""
        SELECT "01 IPF" AS "01 IPF",
               MIN("06 FECHA_ALTA_REAL_AFIL") FILTER (
                   WHERE NOT (
                       COALESCE("15 TRL" >= 700 AND "15 TRL" < 800, FALSE) OR COALESCE("15 TRL" = 400, FALSE)
                   )
               ) AS "Fecha_ini_Primer_Empleo",
               MAX("Baja_modificada") FILTER (
                   WHERE NOT (
                       COALESCE("15 TRL" >= 700 AND "15 TRL" < 800, FALSE) OR COALESCE("15 TRL" = 400, FALSE)
                   )
               ) AS "Fecha_final_Ultimo_Empleo"
        FROM read_parquet('{afiliacion}') GROUP BY 1
    """).fetchdf()

    # Fecha_1er_Empleo_Signif / Duracion_1er_Empleo_Signif / Grupo_cotiz_1er_Empleo_Signif:
    # primer episodio (por fecha de alta) que no sea de los tipos excluidos y dure >= 270 dias.
    # Desempate (2026-08-20, pospuesto varias sesiones): sin el, 5.371 personas tenian mas de
    # un episodio candidato con la misma fecha_alta minima, y el resultado de DuckDB no estaba
    # garantizado que fuera estable. Igual criterio que Grupo_Ultimo_Empleo (mas arriba): fecha
    # primero, luego duracion DESC (el episodio "significativo" mas largo entre los empatados,
    # coherente con que la variable ya exige duracion minima), y CCC_PPAL como ultimo criterio
    # deterministico (mismo patron que el "episodio dominante" de NUM_TRABAJS_CC_2 en
    # app/routers/trayectorias.py). Tras los 3 niveles quedan 550 personas genuinamente
    # ambiguas (empatan tambien en duracion y CCC_PPAL) - residual aceptado, mismo espiritu que
    # los 2.717 casos irresolubles de Grupo_Ultimo_Empleo.
    df_signif = con.execute(f"""
        WITH filtrado AS (
            SELECT "01 IPF" AS ipf, "03 GRU_COTIZACION" AS grupo,
                   "06 FECHA_ALTA_REAL_AFIL" AS fecha_alta, "DURACION_EMPLEO" AS dur,
                   "20 CCC_PPAL" AS ccc_ppal
            FROM read_parquet('{afiliacion}')
            WHERE "04 TIPO_CONTRATO" NOT IN ({tipos_sql})
              AND "DURACION_EMPLEO" >= {DURACION_MINIMA_EMPLEO_US}
        ), ranked AS (
            SELECT ipf, grupo, fecha_alta, dur,
                   ROW_NUMBER() OVER (
                       PARTITION BY ipf ORDER BY fecha_alta ASC, dur DESC, ccc_ppal ASC
                   ) AS rn
            FROM filtrado
        )
        SELECT ipf AS "01 IPF", fecha_alta AS "Fecha_1er_Empleo_Signif",
               dur AS "Duracion_1er_Empleo_Signif", grupo AS "Grupo_cotiz_1er_Empleo_Signif"
        FROM ranked WHERE rn = 1
    """).fetchdf()

    df_signif["Duracion_1er_Empleo_Signif"] = pd.to_timedelta(
        df_signif["Duracion_1er_Empleo_Signif"], unit="us"
    )

    df_1ersignif = df_signif[["01 IPF", "Fecha_1er_Empleo_Signif", "Duracion_1er_Empleo_Signif"]]
    df_grupo1ersignif = df_signif[["01 IPF", "Grupo_cotiz_1er_Empleo_Signif"]]

    return [df_ultimogrupo, df_fechas, df_1ersignif, df_grupo1ersignif]


# ---------------------------------------------------------------------------
# GRUPO C - Afiliacion extendida: duraciones, provincia y actividad
# ---------------------------------------------------------------------------

def extraer_grupo_c(con, parquet_folder):
    afiliacion = _ruta(parquet_folder, "tabla_afiliacion_extendida.parquet")

    # num_episodios_alta / duracion_promedio_EMPLEO (excluye episodios de duracion 0 o NULL)
    df_promedioalta = con.execute(f"""
        SELECT "01 IPF" AS "01 IPF", COUNT(*) AS num_episodios_alta,
               AVG("DURACION_EMPLEO") AS duracion_promedio_EMPLEO
        FROM read_parquet('{afiliacion}')
        WHERE "DURACION_EMPLEO" != 0 AND "DURACION_EMPLEO" IS NOT NULL
        GROUP BY 1
    """).fetchdf()

    # num_episodios_baja / duracion_promedio_NO EMPLEO (esta ultima no sobrevive a la tabla
    # final, pero num_episodios_baja hace falta para num_bajas_anual_promedio en el Grupo H)
    df_promediobaja = con.execute(f"""
        SELECT "01 IPF" AS "01 IPF", COUNT(*) AS num_episodios_baja,
               AVG("DURACION_NO_EMPLEO") AS "duracion_promedio_NO EMPLEO"
        FROM read_parquet('{afiliacion}')
        WHERE "DURACION_NO_EMPLEO" != 0 AND "DURACION_NO_EMPLEO" IS NOT NULL
        GROUP BY 1
    """).fetchdf()

    # T_total_EMPLEO / T_total_DESEMPLEO / T_total_NO_EMPLEO: suma de duraciones por persona
    # (T_total_NO_EMPLEO no sobrevive a la tabla final, se calcula igual que en el cuaderno)
    df_totales = con.execute(f"""
        SELECT "01 IPF" AS "01 IPF",
               SUM("DURACION_EMPLEO") AS "T_total_EMPLEO",
               SUM("DURACION_DESEMPLEO") AS "T_total_DESEMPLEO",
               SUM("DURACION_NO_EMPLEO") AS "T_total_NO_EMPLEO"
        FROM read_parquet('{afiliacion}') GROUP BY 1
    """).fetchdf()

    # Prov_trabajo_ppal / t_tot_prov_ppal: digitos 5-6 del CCC_2, con mas duracion acumulada
    df_prov = con.execute(f"""
        WITH extraido AS (
            SELECT "01 IPF" AS ipf,
                   CASE WHEN LENGTH("10 CCC_2") >= 6
                        THEN TRY_CAST(SUBSTRING("10 CCC_2", 5, 2) AS TINYINT)
                        ELSE NULL END AS prov,
                   "DURACION_EMPLEO" AS dur
            FROM read_parquet('{afiliacion}')
        ), sumado AS (
            SELECT ipf, prov, SUM(dur) AS total FROM extraido WHERE prov IS NOT NULL GROUP BY 1, 2
        ), ranked AS (
            SELECT ipf, prov, total,
                   ROW_NUMBER() OVER (PARTITION BY ipf ORDER BY total DESC, prov ASC) AS rn
            FROM sumado
        )
        SELECT ipf AS "01 IPF", prov AS "Prov_trabajo_ppal", total AS "t_tot_prov_ppal"
        FROM ranked WHERE rn = 1
    """).fetchdf()

    # Act_econ_ppal / t_tot_act_econ_ppal: CNAE (excluye 0: autonomos/desconocido)
    df_actecon = con.execute(f"""
        WITH sumado AS (
            SELECT "01 IPF" AS ipf, "12 CNAE2009_CC" AS val, SUM("DURACION_EMPLEO") AS total
            FROM read_parquet('{afiliacion}')
            WHERE "12 CNAE2009_CC" IS NOT NULL AND "12 CNAE2009_CC" != 0
            GROUP BY 1, 2
        ), ranked AS (
            SELECT ipf, val, total,
                   ROW_NUMBER() OVER (PARTITION BY ipf ORDER BY total DESC, val ASC) AS rn
            FROM sumado
        )
        SELECT ipf AS "01 IPF", val AS "Act_econ_ppal", total AS "t_tot_act_econ_ppal"
        FROM ranked WHERE rn = 1
    """).fetchdf()

    for df, col in [
        (df_promedioalta, "duracion_promedio_EMPLEO"),
        (df_promediobaja, "duracion_promedio_NO EMPLEO"),
        (df_totales, "T_total_EMPLEO"),
        (df_prov, "t_tot_prov_ppal"),
        (df_actecon, "t_tot_act_econ_ppal"),
    ]:
        df[col] = pd.to_timedelta(df[col], unit="us")
    df_totales["T_total_DESEMPLEO"] = pd.to_timedelta(df_totales["T_total_DESEMPLEO"], unit="us")
    df_totales["T_total_NO_EMPLEO"] = pd.to_timedelta(df_totales["T_total_NO_EMPLEO"], unit="us")

    return [df_promedioalta, df_promediobaja, df_totales, df_prov, df_actecon]


# ---------------------------------------------------------------------------
# GRUPO D - Afiliacion extendida: situacion de afiliacion y paro
# ---------------------------------------------------------------------------

# TRL de prestacion/subsidio del SEPE (cobro actual). NO incluye el 400 (corregido
# 2026-08-20, por consistencia con Situacion_actual_afiliacion): la guia solo dice de
# el "alguna peculiaridad por la que no se consideran alta laboral", sin confirmar que
# sea cobro de SEPE - Situacion_actual_afiliacion ya lo trata aparte, en Asimilado_otros,
# no junto a la prestacion/subsidio real (751-756). Afecta a 1.875 personas (verificado
# con datos reales): 625 pierden fecha_inicio_paro (su unico TRL "de cobro" era el 400)
# y 1.250 cambian de fecha (el 400 formaba parte de su racha sin ser el unico motivo).
TRL_COBRO_SEPE = (751, 752, 753, 754, 755, 756)


def extraer_grupo_d(con, parquet_folder):
    afiliacion = _ruta(parquet_folder, "tabla_afiliacion_extendida.parquet")

    # Discap_afiliacion: discapacidad detectada en cualquier episodio de la persona.
    # 130 (no 133 - typo corregido 2026-08-20: "133" no existe en la tabla de TIPO_CONTRATO
    # de la guia, es el codigo real "INDEF. TIEMPO COMPLETO. DISCAPACITADOS", verificado
    # que 0 filas reales tienen 133 y 9.994 filas/6.038 personas tienen 130 - de esas, 752
    # personas no se detectaban como discapacitadas por ningun otro camino).
    df_discap = con.execute(f"""
        SELECT DISTINCT "01 IPF" AS "01 IPF", TRUE AS "Discap_afiliacion"
        FROM read_parquet('{afiliacion}')
        WHERE "04 TIPO_CONTRATO" IN (130, 139, 230, 239, 330, 339, 430, 530)
           OR "09 MINUSV_ALTA_AFIL" > 0
    """).fetchdf()

    # Situacion_actual_afiliacion: clasificacion a partir del episodio mas reciente (por
    # fecha de baja, y de alta como desempate). Para "aun activo" (fecha_baja=2099), el
    # cobro de prestacion/subsidio se separa en 2 categorias (corregido 2026-08-20 a
    # peticion del usuario: antes juntaba los 6 codigos bajo un nombre que sugeria
    # "solo subsidio de mayores de 52/55" sin serlo) - "Asimilado_al_alta_subs52" es
    # especificamente el subsidio >52/55 anyos o fijos discontinuos (TRL 753,754, ver
    # tabla de TRL de la guia), "Asimilado_al_alta_prest_subs" el resto de
    # prestacion/subsidio (751,752,755,756). Para episodios ya finalizados, la
    # distincion previa Baja_de_prestacion (751,752) / Baja_de_subsidio (753-756) no
    # se toca - no se pidio cambiarla.
    df_situacion = con.execute(f"""
        WITH ranked AS (
            SELECT "01 IPF" AS ipf, "07 FECHA_BAJA_REAL_AFIL" AS fecha_baja,
                   "08 CAUSA_BAJA_AFIL" AS causa, "15 TRL" AS trl,
                   ROW_NUMBER() OVER (
                       PARTITION BY "01 IPF"
                       ORDER BY "07 FECHA_BAJA_REAL_AFIL" DESC, "06 FECHA_ALTA_REAL_AFIL" DESC
                   ) AS rn
            FROM read_parquet('{afiliacion}')
        )
        SELECT ipf AS "01 IPF",
            CASE
                WHEN date_part('year', fecha_baja) = 2099 THEN
                    CASE WHEN trl IN (753, 754) THEN 'Asimilado_al_alta_subs52'
                         WHEN trl IN (751, 752, 755, 756) THEN 'Asimilado_al_alta_prest_subs'
                         WHEN trl IN (980, 400) THEN 'Asimilado_otros'
                         ELSE 'Alta_ocupado' END
                WHEN causa = 58 THEN 'Baja_para_pension'
                WHEN trl IN (751, 752) THEN 'Baja_de_prestacion'
                WHEN trl IN (753, 754, 755, 756) THEN 'Baja_de_subsidio'
                WHEN trl IN (980, 400) THEN 'Asimilado_otros'
                ELSE 'Baja_de_actividad'
            END AS "Situacion_actual_afiliacion"
        FROM ranked WHERE rn = 1
    """).fetchdf()

    # fecha_ultima_baja: fecha de baja del episodio mas reciente (por fecha de baja
    # unicamente - no hace falta desempate de fecha_alta, a diferencia de
    # Grupo_Ultimo_Empleo/Situacion_actual_afiliacion: aqui solo se devuelve la propia
    # fecha_baja, y dos episodios empatados en esa fecha dan el mismo valor sea cual sea
    # el que "gane" el desempate)
    df_ultimabaja = con.execute(f"""
        WITH ranked AS (
            SELECT "01 IPF" AS ipf, "07 FECHA_BAJA_REAL_AFIL" AS fecha_baja,
                   ROW_NUMBER() OVER (PARTITION BY "01 IPF" ORDER BY "07 FECHA_BAJA_REAL_AFIL" DESC) AS rn
            FROM read_parquet('{afiliacion}')
        )
        SELECT ipf AS "01 IPF", fecha_baja AS fecha_ultima_baja FROM ranked WHERE rn = 1
    """).fetchdf()

    # Cobrando_del_SEPE_desde_esta_fecha (nombre 2026-08-20, antes "fecha_inicio_paro" -
    # renombrada a peticion del usuario porque "paro" se leia como "estar desempleado" y
    # la variable en realidad mide cuando empezo a COBRAR, no cuando se quedo sin
    # trabajo): para quien esta cobrando del SEPE ahora mismo (episodio mas reciente con
    # TRL de prestacion/subsidio), la fecha de alta del primer episodio de esa racha
    # continua de cobro. Se replica el bucle del cuaderno (que examina hasta 5 episodios y va
    # quitando pares consecutivos que coinciden) con un AND acumulativo por ventana: la racha
    # se corta en el primer episodio (de los 5 mas recientes) que no tenga TRL de cobro.
    # BUG REAL corregido 2026-08-20 (encontrado por el usuario, verificado con ejemplos reales
    # - IPFs con su ultima prestacion cerrada en 1999, 2014, 2023...): la primera version de
    # este fix solo miraba si el TRL coincidia, sin comprobar si el episodio mas reciente de
    # la persona seguia ABIERTO (fecha_baja=2099) o ya se habia cerrado hace anyos/decadas -
    # de las 172.644 personas con un valor, solo 40.606 (23,5%) cobraban de verdad ahora mismo,
    # el resto (132.038) tenia su ultima prestacion/subsidio ya cerrada, algunas desde hace mas
    # de 20 anyos. Ahora se exige ademas que el episodio MAS RECIENTE (rn=1, no solo el que
    # empieza la racha) siga con fecha_baja=2099 - asi el nombre (en presente, "cobrando...
    # desde esta fecha") coincide con lo que realmente mide. NULL para quien no esta cobrando
    # ahora mismo (incluye a los 132.038 recolocados como NULL) - decision explicita del
    # usuario de no rellenar con un texto tipo "No cobra del SEPE en la actualidad" en el
    # propio dato (mezclaria fecha y texto en la misma columna, forzando pandas a "object" y
    # rompiendo o degradando la escritura a Parquet) - ese texto, si se quiere, es cosa del
    # frontend.
    trl_sql = ", ".join(str(t) for t in TRL_COBRO_SEPE)
    df_fechainicioparo = con.execute(f"""
        WITH ranked AS (
            SELECT "01 IPF" AS ipf, "06 FECHA_ALTA_REAL_AFIL" AS fecha_alta,
                   "07 FECHA_BAJA_REAL_AFIL" AS fecha_baja,
                   "15 TRL" IN ({trl_sql}) AS coincide,
                   ROW_NUMBER() OVER (
                       PARTITION BY "01 IPF"
                       ORDER BY "07 FECHA_BAJA_REAL_AFIL" DESC, "06 FECHA_ALTA_REAL_AFIL" DESC
                   ) AS rn
            FROM read_parquet('{afiliacion}')
        ), top5 AS (
            SELECT *,
                   bool_or(CASE WHEN rn = 1 THEN date_part('year', fecha_baja) = 2099 END) OVER (
                       PARTITION BY ipf
                   ) AS aun_activo
            FROM ranked WHERE rn <= 5
        ), acumulado AS (
            SELECT *, bool_and(coincide) OVER (
                PARTITION BY ipf ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS racha
            FROM top5
        ), elegido AS (
            SELECT ipf, fecha_alta, aun_activo,
                   ROW_NUMBER() OVER (PARTITION BY ipf ORDER BY rn DESC) AS rn2
            FROM acumulado WHERE racha
        )
        SELECT ipf AS "01 IPF", fecha_alta AS "Cobrando_del_SEPE_desde_esta_fecha"
        FROM elegido WHERE rn2 = 1 AND aun_activo
    """).fetchdf()

    return [df_discap, df_situacion, df_ultimabaja, df_fechainicioparo]


def fusionar_grupo_d(parquet_folder, con):
    extracciones = extraer_grupo_d(con, parquet_folder)

    def post_fillna(df):
        df["Situacion_actual_afiliacion"] = df["Situacion_actual_afiliacion"].fillna("Nunca_ha_cotizado")
        df["Discap_afiliacion"] = df["Discap_afiliacion"].fillna(False).astype(bool)

    return fusionar_grupo(parquet_folder, extracciones, post_fillna)


# ---------------------------------------------------------------------------
# FASE 2 - Pensiones extendida
# ---------------------------------------------------------------------------

def _categorizar_tipo_pension(valor):
    valor_str = str(valor).strip()
    try:
        valor_num = int(valor_str)
        if 10 <= valor_num <= 19:
            return "Incapacidad"
        elif 20 <= valor_num <= 26:
            return "Jubilación"
        elif 30 <= valor_num <= 32:
            return "Viudedad"
        elif 40 <= valor_num <= 42:
            return "Orfandad"
        elif 51 <= valor_num <= 54:
            return "Pension_a_favor_familiares"
    except ValueError:
        pass

    if re.match(r"^J\d$", valor_str):
        return "Incapacidad>65"
    if valor_str == "2E":
        return "Jubilación"
    return "Otros"


def fase2_pensiones_extendida(parquet_folder):
    """Lee tabla_pensiones_optimizada.parquet, anyade Tipo_pension e
    Ingresos_pension_bruto_anual, guarda tabla_pensiones_extendida.parquet. Devuelve el
    DataFrame resultante (el llamador decide si lo libera)."""
    df = pd.read_parquet(_ruta(parquet_folder, "tabla_pensiones_optimizada.parquet"))

    df["Tipo_pension"] = df["04 CLASE_PRESTACION"].apply(_categorizar_tipo_pension)
    df["Tipo_pension"] = df["Tipo_pension"].astype("category")

    df["Ingresos_pension_bruto_anual"] = np.where(
        df["04 CLASE_PRESTACION"] == 17,
        df["20 IMPORTE_MES_TOTAL_PRESTACION"] * 12,
        df["20 IMPORTE_MES_TOTAL_PRESTACION"] * 14,
    )

    df.to_parquet(_ruta(parquet_folder, "tabla_pensiones_extendida.parquet"), index=False)
    return df


# ---------------------------------------------------------------------------
# GRUPO E - Extracciones desde Pensiones extendida
# ---------------------------------------------------------------------------

def extraer_grupo_e(df_pensiones, anio_datos):
    """A partir de tabla_pensiones_extendida (en memoria), calcula las 4 extracciones del
    Grupo E. Devuelve (lista_de_extracciones, nombre_columna_cobra_pension)."""
    df = df_pensiones

    # Pension_actual / Cobra_pension_{anio}: filtra por el ANYO DE REFERENCIA real
    # (anio_datos, p.ej. 2024 - el mismo que ya usa el nombre de Cobra_pension_2024/5),
    # no por el maximo ANO_DATO propio de cada persona (bug real corregido 2026-08-20).
    # La guia (campo "4.002 ANYO DEL DATO") dice que cada pension tiene una fila por cada
    # anyo natural en que SI se percibio - el maximo propio de una persona puede ser un
    # anyo antiguo si dejo de cobrar hace tiempo, y con el filtro viejo esa persona salia
    # igualmente marcada "Cobra_pension_2024/5=SI" y con esa pension antigua como
    # "Pension_actual", aunque llevara anyos sin cobrar nada. Verificado con datos reales:
    # 19.288 personas (de 408.670 en Pensiones) tenian su ultimo ANO_DATO anterior a 2024.
    # ">=" en vez de "==" para no descartar datos mas recientes que el anyo de referencia
    # si los hubiera (2.523 personas con ANO_DATO=2025 en los datos reales).
    df_ultimo = df[df["02 ANO_DATO"] >= anio_datos]

    df_estudio_pensionactual = df_ultimo.groupby("01 IPF")["Tipo_pension"].apply(list).reset_index()
    df_estudio_pensionactual.columns = ["01 IPF", "Pension_actual"]

    nombre_col_cobra_pension = f"Cobra_pension_{anio_datos}/{str(anio_datos + 1)[-1]}"
    df_estudio_pensionactual[nombre_col_cobra_pension] = "SI"

    # Tipos DISTINTOS de pension cobrados alguna vez (no una entrada por cada anyo de
    # historico) - decision explicita del usuario, 2026-08-20: sin deduplicar, la media
    # real es ~14 filas/anyos por persona pero solo ~1,2 tipos distintos, asi que salia
    # una lista con el mismo tipo repetido muchas veces (ej. 10x "Jubilacion" para
    # alguien que la cobro 10 anyos seguidos). Series.unique() preserva el orden de
    # primera aparicion (no ordena alfabeticamente).
    df_estudio_pensiontodas = df.groupby("01 IPF")["Tipo_pension"].apply(lambda s: list(s.unique())).reset_index()
    df_estudio_pensiontodas.columns = ["01 IPF", "Pension_todas_cobradas"]

    df_por_anio = df[["01 IPF", "02 ANO_DATO", "Ingresos_pension_bruto_anual"]].groupby(
        ["01 IPF", "02 ANO_DATO"]
    ).sum().reset_index()
    df_estudio_ingresospensionhist = df_por_anio.groupby("01 IPF")["Ingresos_pension_bruto_anual"].apply(list).reset_index()
    df_estudio_ingresospensionhist.columns = ["01 IPF", "Ingresos_pension_bruto_historico"]

    cond1 = df["09 CLASE_MINIMO"].isin([5, 6])
    cond2 = df["28 TIPO_SIT_JUBILACION"] == 17
    cond = cond1 | cond2
    df_estudio_discap_pension = df.loc[cond, ["01 IPF"]].drop_duplicates().copy()
    df_estudio_discap_pension["Discap_pension"] = True

    extracciones = [
        df_estudio_pensionactual, df_estudio_pensiontodas,
        df_estudio_ingresospensionhist, df_estudio_discap_pension,
    ]
    return extracciones, nombre_col_cobra_pension


def fusionar_grupo_e(parquet_folder, df_pensiones, anio_datos):
    extracciones, nombre_col_cobra_pension = extraer_grupo_e(df_pensiones, anio_datos)

    def post_fillna(df):
        df[nombre_col_cobra_pension] = df[nombre_col_cobra_pension].fillna("NO")
        df["Discap_pension"] = df["Discap_pension"].fillna(False).astype(bool)

    return fusionar_grupo(parquet_folder, extracciones, post_fillna), nombre_col_cobra_pension


# ---------------------------------------------------------------------------
# GRUPO F - Extracciones desde Datos fiscales
# ---------------------------------------------------------------------------

def extraer_grupo_f(parquet_folder, anio_datos):
    """Lee tabla_datos_fiscales_optimizada.parquet y calcula las 2 extracciones del Grupo F.
    Devuelve (lista_de_extracciones, nombre_columna_ingresos_brutos)."""
    df = pd.read_parquet(_ruta(parquet_folder, "tabla_datos_fiscales_optimizada.parquet"))

    # /100: el campo fiscal viene en centimos (convencion habitual de la Agencia
    # Tributaria/SEPE), ni el cuaderno ni la version anterior de este script lo
    # dividian - decision explicita del usuario tras detectar el error (medias de
    # ~2,2M € sin sentido en vez de ~22.000 €).
    # Suma "07 PERC_INTEGRA_DIN_NO_DERIVADA_IL" + "08 PERC_INTEGRA_DIN_DERIVADA_IL"
    # (corregido 2026-08-20, decision explicita del usuario): la guia (Tabla 6, Datos
    # Fiscales) describe el campo 07 como "Percepcion integra dineraria NO derivada de
    # IL" (IL = Incapacidad Laboral, baja medica) y el 08 como la parte SI derivada de
    # IL - son dos partidas de ingreso bruto distintas y complementarias, no una
    # alternativa. Sumar solo la 07 (como hacia el codigo anterior) dejaba fuera lo
    # cobrado por prestacion de incapacidad laboral, incompleto para una variable
    # llamada "ingreso bruto anual".
    # fillna(0) antes de sumar: si a alguien no le aplica la parte derivada de IL (lo
    # habitual, nadie cobro baja medica ese anyo) es de esperar que venga NULL, no 0 -
    # sumar directo perderia el valor real del campo 07 (NULL + numero = NULL en pandas).
    df["_ingreso_bruto_total"] = (
        df["07 PERC_INTEGRA_DIN_NO_DERIVADA_IL"].fillna(0) + df["08 PERC_INTEGRA_DIN_DERIVADA_IL"].fillna(0)
    )
    df_sumado = (df.groupby("01 IPF")["_ingreso_bruto_total"].sum() / 100).reset_index()
    nombre_col_ingresos_brutos = f"Ingresos_bruto_anual_{anio_datos}"
    df_estudio_ingresosbrutos = df_sumado.rename(columns={"_ingreso_bruto_total": nombre_col_ingresos_brutos})

    cond = df["20 DISCAPACIDAD"] > 0
    df_estudio_discap_fiscal = df.loc[cond, ["01 IPF"]].drop_duplicates().copy()
    df_estudio_discap_fiscal["Discap_fiscal"] = True

    del df, df_sumado
    gc.collect()

    extracciones = [df_estudio_ingresosbrutos, df_estudio_discap_fiscal]
    return extracciones, nombre_col_ingresos_brutos


def fusionar_grupo_f(parquet_folder, anio_datos):
    extracciones, nombre_col_ingresos_brutos = extraer_grupo_f(parquet_folder, anio_datos)

    def post_fillna(df):
        df["Discap_fiscal"] = df["Discap_fiscal"].fillna(False).astype(bool)

    return fusionar_grupo(parquet_folder, extracciones, post_fillna), nombre_col_ingresos_brutos


# ---------------------------------------------------------------------------
# GRUPO G - Extracciones desde Bases de cotizacion (cuenta ajena)
# ---------------------------------------------------------------------------

def extraer_grupo_g(con, parquet_folder):
    """32M filas (cuenta ajena): agregado con DuckDB, igual que Afiliacion."""
    bases_ca = _ruta(parquet_folder, "tabla_bases_cotizacion_CA_optimizada.parquet")

    df_cotizadototal = con.execute(f"""
        SELECT "01 IPF" AS "01 IPF", SUM("16 BCC_ANUAL_TOTAL") AS "Cotizado_total"
        FROM read_parquet('{bases_ca}') GROUP BY 1
    """).fetchdf()

    # Bases_cotiz_bruto_historico: lista de bases de cotizacion por anyo, ordenada por anyo
    # (igual que el cuaderno: groupby ordena por las claves antes del apply(list)). El agregado
    # por (IPF, anyo) se hace con DuckDB (32M filas de origen), pero el propio DuckDB revienta
    # -por disco y luego por su limite de memoria interno- al construir la lista ordenada por
    # grupo. El resultado intermedio ya es pequenyo (~150 MB, un IPF-anyo por fila), asi que la
    # lista final se construye con pandas (igual que el cuaderno), sin ese problema.
    por_anio_path = _ruta(parquet_folder, "_tmp_bases_ca_por_anio.parquet")
    con.execute(f"""
        COPY (
            SELECT "01 IPF" AS ipf, "03 ANO_COTIZACION" AS anio, SUM("16 BCC_ANUAL_TOTAL") AS total
            FROM read_parquet('{bases_ca}') GROUP BY 1, 2
        ) TO '{por_anio_path}' (FORMAT PARQUET)
    """)
    df_por_anio = pd.read_parquet(por_anio_path).sort_values(["ipf", "anio"])
    df_basescotizhist = df_por_anio.groupby("ipf")["total"].apply(list).reset_index()
    df_basescotizhist.columns = ["01 IPF", "Bases_cotiz_bruto_historico"]
    del df_por_anio
    os.remove(por_anio_path)

    return [df_cotizadototal, df_basescotizhist]


def fusionar_grupo_g(parquet_folder, con):
    extracciones = extraer_grupo_g(con, parquet_folder)
    return fusionar_grupo(parquet_folder, extracciones)


# ---------------------------------------------------------------------------
# GRUPO H - Combinaciones sobre Personas extendida (sin cargar ninguna tabla grande)
# ---------------------------------------------------------------------------

FECHA_REFERENCIA_45 = pd.Timestamp("2025-04-30")
DIAS_POR_ANIO = 365.25

PAISES_LATINOS = ["N03", "N06", "N07", "N08", "N09", "N13", "N22", "N26", "N27"]
PAISES_AFRICANOS = ["N12", "N23"]
# N16 = Reino Unido, ya no es miembro de la UE desde el Brexit (2020) - se queda agrupado
# aqui de todas formas (decision explicita del usuario, 2026-08-20: la MCVL cubre tambien
# muchos anyos anteriores al Brexit), pero el nombre de la lista y la etiqueta que ve el
# analista (mas abajo, "paises_UE_UK") lo dejan explicito para no llevar a confusion.
PAISES_EU_UK = ["N01", "N04", "N10", "N11", "N14", "N15", "N16", "N17", "N19", "N20"]
PAISES_LEJANOS = ["N05", "N18", "N21", "N24", "N25", "N99"]
ESPANA = ["N00"]

GRUPOS_COTIZ_DICT = {
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
    # Faltaba (2026-08-20): la guia documenta hasta el 12 ("Trabajadores menores de
    # 17 años") - sin esta entrada, .map(GRUPOS_COTIZ_DICT).fillna("NO CONSTA") en
    # grupo_h_final convertia silenciosamente cualquier ganador real de codigo 12 en
    # "NO CONSTA", en las 3 columnas que usan este diccionario (Grupo_cotiz_ppal,
    # Grupo_cotiz_1er_Empleo_Signif, Grupo_Ultimo_Empleo). Mismo hueco ya corregido
    # en app/routers/trayectorias.py (commit 34025f5) para la trayectoria categorica.
    12: "12 TRABAJADORES MENORES DE 17 AÑOS",
}

# Columnas finales de tabla_personas_extendida, mismo orden que el cuaderno (nuevo_orden)
NUEVO_ORDEN = [
    "01 IPF", "02 FECHA_NACIMIENTO", "03 SEXO", "04 NACIONALIDAD", "05 PROV_NACIMIENTO",
    "06 PROV_1a_AFILIACION", "07 DOMIC_RESID_HABITUAL", "08 FECHA_FALLECIMIENTO", "09 PAIS_NACIMIENTO",
    "Nacionalidad_agrupado", "Pais_nacim_agrupado",
    "Edad_Actual_Fecha_Corte",
    "Edad_Primer_Empleo_Signif", "Duracion_1er_Empleo_Signif", "Grupo_cotiz_1er_Empleo_Signif",
    "Edad_Ultimo_Empleo", "Grupo_Ultimo_Empleo",
    "t_contrato_mas_largo", "Fecha_ini_Primer_Empleo", "Anos_VL_excluye_desempleo_inicio_y_fin",
    "duracion_promedio_EMPLEO", "Cotizado_anual_promedio",
    "num_altas_anual_promedio", "num_bajas_anual_promedio",
    "T_total_EMPLEO", "T_total_DESEMPLEO",
    "%T_total_EMPLEO", "%T_total_DESEMPLEO",
    "Reg_cotiz_ppal_Agrupado", "T_tot_reg_ppal",
    "Grupo_cotiz_ppal", "t_tot_grupo_ppal",
    "Tipo_Contrato_ppal_Agrupado", "T_tot_tipo_contrato_ppal",
    "Prov_trabajo_ppal", "t_tot_prov_ppal",
    "Act_econ_ppal", "t_tot_act_econ_ppal",
    "Ingresos_bruto_anual_2024",
    "Discap_fiscal", "Discap_afiliacion", "Discap_pension",
    "Pension_todas_cobradas",
    "Pension_actual", "Cobra_pension_2024/5",
    "fecha_ultima_baja", "Situacion_actual_afiliacion", "Situacion_Paro_Ocupacion",
    "Cobrando_del_SEPE_desde_esta_fecha", "duracion_paro_dias",
]


def _tiene_jubilacion(x):
    if x is None:
        return False
    if isinstance(x, (list, np.ndarray)):
        lista = list(x) if isinstance(x, np.ndarray) else x
        return "Jubilación" in lista
    return False


def grupo_h_final(parquet_folder, provincias_dict_path, cnae_dict_path):
    """Ultimo paso: calculos derivados (edades, tasas anuales, agrupaciones, filtros de
    calidad) y seleccion final de columnas. Todo en pandas: para cuando se llega aqui, la
    tabla ya esta reducida a una fila por persona (~1,36M filas), cabe de sobra en memoria."""
    output_path = _ruta(parquet_folder, "tabla_personas_extendida.parquet")
    df = pd.read_parquet(output_path)
    print(f"Grupo H: partiendo de {df.shape}")

    # Edad_Ultimo_Empleo / DURACION_1er_Empleo_a_Ultimo_Empleo
    df["Fecha_ini_Primer_Empleo_completa"] = df["Fecha_ini_Primer_Empleo"].dt.year * 12 + df["Fecha_ini_Primer_Empleo"].dt.month
    df["Fecha_final_Ultimo_Empleo_completa"] = df["Fecha_final_Ultimo_Empleo"].dt.year * 12 + df["Fecha_final_Ultimo_Empleo"].dt.month
    df["02 FECHA_NACIMIENTO_completa"] = df["02 FECHA_NACIMIENTO"].dt.year * 12 + df["02 FECHA_NACIMIENTO"].dt.month

    df["Edad_Ultimo_Empleo"] = df["Fecha_final_Ultimo_Empleo_completa"] - df["02 FECHA_NACIMIENTO_completa"]
    # +UN_DIA (corregido 2026-08-20, encontrado por el usuario): igual convencion que
    # DURACION_EMPLEO (el dia de baja cuenta como "en alta", confirmado contra la guia -
    # "el dia de la fecha de baja se considera como dia en alta"). Sin el +1, una resta simple
    # daba 0 dias cuando el primer y el ultimo empleo real caian el mismo dia (990 personas con
    # datos reales) en vez del minimo logico de 1 dia.
    df["DURACION_1er_Empleo_a_Ultimo_Empleo"] = df["Fecha_final_Ultimo_Empleo"] - df["Fecha_ini_Primer_Empleo"] + UN_DIA
    # Aun con el +1, puede quedar <= 0 si Fecha_final_Ultimo_Empleo es anterior a
    # Fecha_ini_Primer_Empleo por mas de 1 dia - error de dato (la guia ya avisa de casos raros
    # de fecha_alta posterior a fecha_baja) en vez de una duracion valida. Se pone a NaT (no un
    # valor invalido) para que Anos_VL_excluye_desempleo_inicio_y_fin y las variables que
    # dividen por esta duracion (num_altas_anual_promedio, num_bajas_anual_promedio,
    # Cotizado_anual_promedio, %T_total_EMPLEO/DESEMPLEO) den NULL en vez de "inf" o un valor
    # con el signo invertido.
    df.loc[df["DURACION_1er_Empleo_a_Ultimo_Empleo"] <= CERO_DIAS, "DURACION_1er_Empleo_a_Ultimo_Empleo"] = pd.NaT
    df.drop(columns=["Fecha_ini_Primer_Empleo_completa", "Fecha_final_Ultimo_Empleo_completa"], inplace=True)

    # Edad_Primer_Empleo_Signif
    df["Fecha_1er_Empleo_Signif_completa"] = df["Fecha_1er_Empleo_Signif"].dt.year * 12 + df["Fecha_1er_Empleo_Signif"].dt.month
    df["Edad_Primer_Empleo_Signif"] = df["Fecha_1er_Empleo_Signif_completa"] - df["02 FECHA_NACIMIENTO_completa"]
    df.drop(columns=["Fecha_1er_Empleo_Signif_completa", "02 FECHA_NACIMIENTO_completa"], inplace=True)

    df["Edad_Ultimo_Empleo"] = df["Edad_Ultimo_Empleo"] / 12
    df["Edad_Primer_Empleo_Signif"] = df["Edad_Primer_Empleo_Signif"] / 12

    # Edad negativa (episodio de Afiliacion anterior a la fecha de nacimiento) -> NULL, no un
    # valor imposible (decision explicita del usuario, 2026-08-20, para no falsear
    # estadisticas). Verificado con datos reales: 194 personas en Edad_Primer_Empleo_Signif
    # (hasta -33,7 anyos) y 14 en Edad_Ultimo_Empleo - casos aislados, probable reutilizacion
    # del IPF entre dos personas distintas (la guia MCVL ya avisa de que el IPF, aunque
    # normalmente unico de por vida, "en la mayoria de los casos" puede excepcionalmente
    # reasignarse). No hay forma de saber cual de los dos datos (fecha de nacimiento o
    # episodio de Afiliacion) es el erroneo para estos casos concretos.
    df.loc[df["Edad_Ultimo_Empleo"] < 0, "Edad_Ultimo_Empleo"] = np.nan
    df.loc[df["Edad_Primer_Empleo_Signif"] < 0, "Edad_Primer_Empleo_Signif"] = np.nan

    # Edad_Actual_Fecha_Corte: edad a la fecha de corte de esta MCVL (FECHA_CORTE), NUNCA la
    # fecha real del sistema - si no, se desincroniza del resto del snapshot segun cuando se
    # ejecute la app.
    fecha_nacimiento_completa = df["02 FECHA_NACIMIENTO"].dt.year * 12 + df["02 FECHA_NACIMIENTO"].dt.month
    fecha_corte_completa = FECHA_CORTE.year * 12 + FECHA_CORTE.month
    df["Edad_Actual_Fecha_Corte"] = (fecha_corte_completa - fecha_nacimiento_completa) / 12

    # num_altas_anual_promedio / num_bajas_anual_promedio: normalizados por anyos de vida laboral
    df["num_altas_anual_promedio"] = df["num_episodios_alta"] / df["DURACION_1er_Empleo_a_Ultimo_Empleo"].dt.days * 365
    df["num_bajas_anual_promedio"] = df["num_episodios_baja"] / df["DURACION_1er_Empleo_a_Ultimo_Empleo"].dt.days * 365
    df.drop(columns=["num_episodios_alta", "num_episodios_baja"], inplace=True)

    # Cotizado_anual_promedio: normalizado por anyos de vida laboral
    df["Cotizado_anual_promedio"] = df["Cotizado_total"] / df["DURACION_1er_Empleo_a_Ultimo_Empleo"].dt.days * 365 / 100

    # Nacionalidad_agrupado / Pais_nacim_agrupado
    condiciones = [
        df["04 NACIONALIDAD"].isin(PAISES_LATINOS), df["04 NACIONALIDAD"].isin(PAISES_AFRICANOS),
        df["04 NACIONALIDAD"].isin(PAISES_EU_UK), df["04 NACIONALIDAD"].isin(PAISES_LEJANOS),
        df["04 NACIONALIDAD"].isin(ESPANA),
    ]
    elecciones = ["paises_latinos", "paises_africanos", "paises_UE_UK", "paises_lejanos", "espana"]
    # default=np.nan con choices de texto revienta en numpy reciente (StrDType vs float no
    # promocionan a un dtype comun); se usa un centinela de texto y se convierte despues.
    df["Nacionalidad_agrupado"] = np.select(condiciones, elecciones, default="_SIN_DATO_")
    df["Nacionalidad_agrupado"] = df["Nacionalidad_agrupado"].replace("_SIN_DATO_", np.nan)

    condiciones_pais_nacim = [
        df["09 PAIS_NACIMIENTO"].isin(PAISES_LATINOS), df["09 PAIS_NACIMIENTO"].isin(PAISES_AFRICANOS),
        df["09 PAIS_NACIMIENTO"].isin(PAISES_EU_UK), df["09 PAIS_NACIMIENTO"].isin(PAISES_LEJANOS),
        df["09 PAIS_NACIMIENTO"].isin(ESPANA),
    ]
    df["Pais_nacim_agrupado"] = np.select(condiciones_pais_nacim, elecciones, default="_SIN_DATO_")
    df["Pais_nacim_agrupado"] = df["Pais_nacim_agrupado"].replace("_SIN_DATO_", np.nan)

    # Situacion_Paro_Ocupacion: Ocupado / Parado / Pensionista
    # Vectorizado (no df.apply(axis=1), que construye una Series de ~50 columnas por cada una
    # de las 1,36M filas y revienta por memoria en esta maquina - MemoryError comprobado).
    tiene_jubilacion = df["Pension_actual"].apply(_tiene_jubilacion)
    df["Situacion_Paro_Ocupacion"] = np.select(
        [
            df["Situacion_actual_afiliacion"] == "Alta_ocupado",
            df["Situacion_actual_afiliacion"] == "Nunca_ha_cotizado",
            df["Situacion_actual_afiliacion"] == "Baja_para_pension",
            tiene_jubilacion,
        ],
        ["Ocupado", "Pensionista", "Pensionista", "Pensionista"],
        default="Parado",
    )

    # Reclasificacion de Parado en Parado_LD / Parado_NoLD (vectorizado igual).
    # "Asimilado_al_alta_prest_subs52" ya no existe como categoria (se separo en
    # Asimilado_al_alta_subs52 / Asimilado_al_alta_prest_subs, commit 730d910) - corregido
    # aqui tambien (bug real: se habia quedado con el nombre viejo, que ya no aparece
    # nunca en Situacion_actual_afiliacion, asi que ninguna de esas personas entraba en
    # usa_fecha_inicio_paro por esta via).
    # "Baja_de_prestacion"/"Baja_de_subsidio" QUITADAS de esta lista (corregido 2026-08-20,
    # efecto colateral del fix de Cobrando_del_SEPE_desde_esta_fecha en commit 839d80a): esas
    # 2 categorias, por construccion, SOLO se dan cuando el episodio ya termino (nunca cuando
    # sigue activo) - con el fix de esa variable (ahora solo tiene valor si sigue activo),
    # Cobrando_del_SEPE_desde_esta_fecha nunca tiene valor para estas 2 categorias, dejando a
    # 82.524 personas sin duracion_paro_dias por ninguna de las 2 vias. Ahora caen en la rama
    # de fecha_ultima_baja (dias desde que se les acabo la prestacion/subsidio) - decision
    # explicita del usuario. "Asimilado_otros" SI se queda (puede darse tanto con episodio
    # activo como ya terminado, no es mutuamente excluyente con "aun activo" de la misma forma).
    usa_cobrando_sepe = df["Situacion_actual_afiliacion"].isin(
        ["Asimilado_otros", "Asimilado_al_alta_subs52", "Asimilado_al_alta_prest_subs"]
    )
    dias_desde_inicio = (FECHA_REFERENCIA_45 - df["Cobrando_del_SEPE_desde_esta_fecha"]).dt.days
    dias_desde_ultima_baja = (FECHA_REFERENCIA_45 - df["fecha_ultima_baja"]).dt.days

    es_parado = df["Situacion_Paro_Ocupacion"] == "Parado"
    duracion_paro_dias = pd.Series(np.nan, index=df.index)
    mask_inicio = es_parado & usa_cobrando_sepe & df["Cobrando_del_SEPE_desde_esta_fecha"].notna()
    duracion_paro_dias[mask_inicio] = dias_desde_inicio[mask_inicio]
    mask_ultima_baja = es_parado & ~usa_cobrando_sepe & df["fecha_ultima_baja"].notna()
    duracion_paro_dias[mask_ultima_baja] = dias_desde_ultima_baja[mask_ultima_baja]
    df["duracion_paro_dias"] = duracion_paro_dias

    umbral_ld = 365
    es_ld = es_parado & duracion_paro_dias.notna() & (duracion_paro_dias > umbral_ld)
    df.loc[es_parado & es_ld, "Situacion_Paro_Ocupacion"] = "Parado_LD"
    df.loc[es_parado & ~es_ld, "Situacion_Paro_Ocupacion"] = "Parado_NoLD"

    # %T_total_EMPLEO / %T_total_DESEMPLEO: proporcion (0-1) sobre la duracion total de
    # vida laboral, x100 para que sea un porcentaje real (0-100) acorde al nombre de la
    # columna. El cuaderno reconstruyo esta formula (el codigo original de 2023 que la
    # produjo se habia perdido) sin el x100 y con una advertencia sin resolver
    # ("REVISAR ... verificar que es la definicion que se queria") que esta version
    # anterior de este script tampoco llevaba - decision explicita del usuario tras
    # detectar que los valores (0,8 / 0,9 / 0,1...) no podian ser un porcentaje real.
    df["%T_total_EMPLEO"] = df["T_total_EMPLEO"] / df["DURACION_1er_Empleo_a_Ultimo_Empleo"] * 100
    df["%T_total_DESEMPLEO"] = df["T_total_DESEMPLEO"] / df["DURACION_1er_Empleo_a_Ultimo_Empleo"] * 100

    # Conversion de columnas Timedelta a anyos (in-place)
    for col in df.columns:
        if pd.api.types.is_timedelta64_dtype(df[col]):
            df[col] = df[col].dt.days / DIAS_POR_ANIO
    # Renombrada 2026-08-20 (decision explicita del usuario): "Anos_VL" no dejaba claro que,
    # tras los fixes de Fecha_ini_Primer_Empleo/Fecha_final_Ultimo_Empleo (commits 84e1ace,
    # c7d9469), los extremos del periodo excluyen episodios de desempleo/prestacion - pero
    # un hueco de desempleo EN MEDIO del periodo (entre dos empleos reales) si sigue contando.
    df = df.rename(columns={"DURACION_1er_Empleo_a_Ultimo_Empleo": "Anos_VL_excluye_desempleo_inicio_y_fin"})

    # Diccionario de grupos de cotizacion
    for columna in ["Grupo_cotiz_ppal", "Grupo_cotiz_1er_Empleo_Signif", "Grupo_Ultimo_Empleo"]:
        df[columna] = df[columna].map(GRUPOS_COTIZ_DICT).fillna("NO CONSTA").astype("category")

    # Filtrar personas sin fecha de nacimiento
    antes = df.shape[0]
    df = df[df["02 FECHA_NACIMIENTO"].notna()].copy()
    print(f"Filas eliminadas por no tener fecha de nacimiento: {antes - df.shape[0]}")

    # Filtrar outliers de Anos_VL_excluye_desempleo_inicio_y_fin (± 3 desviaciones estandar)
    media = df["Anos_VL_excluye_desempleo_inicio_y_fin"].mean()
    std = df["Anos_VL_excluye_desempleo_inicio_y_fin"].std()
    limite_inferior = media - 3 * std
    limite_superior = media + 3 * std
    outliers = df[
        (df["Anos_VL_excluye_desempleo_inicio_y_fin"] < limite_inferior)
        | (df["Anos_VL_excluye_desempleo_inicio_y_fin"] > limite_superior)
    ]
    print(f"Outliers de Anos_VL_excluye_desempleo_inicio_y_fin: {len(outliers)} ({len(outliers) / len(df) * 100:.2f}%)")
    df = df[~df.index.isin(outliers.index)].copy()

    # Sexo: 0/1/2 -> "NO CONSTA"/"Hombre"/"Mujer". El cuaderno no transforma esta columna (se
    # copia tal cual del CSV crudo con esos 3 codigos numericos); decision explicita del usuario
    # de relabelarla aqui, con el mismo criterio "NO CONSTA" ya usado para el resto de columnas
    # categoricas con codigos ausentes/desconocidos (Prov_trabajo_ppal, Act_econ_ppal...).
    df["03 SEXO"] = df["03 SEXO"].map({0: "NO CONSTA", 1: "Hombre", 2: "Mujer"}).fillna("NO CONSTA").astype("category")

    # Provincia de trabajo principal / Actividad economica principal (CNAE-2009, nivel Grupo)
    provincias_df = pd.read_csv(provincias_dict_path, sep=";", encoding="utf-8", dtype={"codigo": int})
    provincias_dict = dict(zip(provincias_df["codigo"], provincias_df["nombre"]))
    cnae_df = pd.read_csv(cnae_dict_path, sep=";", encoding="utf-8", dtype={"codigo": str})
    cnae_3d_dict = dict(zip(cnae_df["codigo"], cnae_df["descripcion"]))

    df["Prov_trabajo_ppal"] = df["Prov_trabajo_ppal"].map(provincias_dict).fillna("NO CONSTA").astype("category")

    # A diferencia de provincias_dict.csv (el nombre YA lleva el codigo, "01 ÁLAVA"),
    # cnae_3d_dict.csv trae codigo y descripcion en columnas separadas: hay que concatenarlos a
    # mano para que el resultado final sea "561 Restaurantes y puestos de comidas" (comprobado
    # contra el parquet de referencia: sin el codigo delante, difiere en pr�cticamente todas las
    # filas). No hace falta un caso especial para "000": extraer_grupo_c ya excluye el codigo 0
    # de la competicion por Act_econ_ppal (verificado con datos reales: 0 filas con "000"),
    # cualquier persona sin actividad economica valida llega aqui como NULL y cae en "NO CONSTA"
    # igual que el resto de categoricas.
    codigo_cnae = df["Act_econ_ppal"].astype("Int64").astype(str).str.zfill(3)
    df["Act_econ_ppal"] = (codigo_cnae + " " + codigo_cnae.map(cnae_3d_dict)).fillna("NO CONSTA").astype("category")

    # Reordenar y seleccionar las columnas finales
    columnas_presentes = [c for c in NUEVO_ORDEN if c in df.columns]
    columnas_faltantes = [c for c in NUEVO_ORDEN if c not in df.columns]
    if columnas_faltantes:
        print(f"AVISO: columnas del esquema objetivo que todavia no existen en df: {columnas_faltantes}")
    df = df[columnas_presentes]
    print(f"Grupo H: forma final {df.shape}")

    df.to_parquet(output_path, index=False)
    return df
