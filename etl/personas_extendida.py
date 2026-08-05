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
    df.loc[condicion_trl_700, "DURACION_EMPLEO"] = CERO_DIAS

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
            CASE WHEN _trl700 THEN 0
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
                * EXCLUDE (_trl700, _dias_bruto, _rn_ultimo, "DURACION_NO_EMPLEO"),
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

    # Reg_cotiz_ppal / T_tot_reg_ppal: regimen con mas DURACION_EMPLEO acumulada por persona
    df_reg = con.execute(f"""
        WITH sumado AS (
            SELECT "01 IPF" AS ipf, "02 REG_COTIZACION" AS val, SUM("DURACION_EMPLEO") AS total
            FROM read_parquet('{afiliacion}') WHERE "02 REG_COTIZACION" IS NOT NULL GROUP BY 1, 2
        ), ranked AS (
            SELECT ipf, val, total,
                   ROW_NUMBER() OVER (PARTITION BY ipf ORDER BY total DESC, val ASC) AS rn
            FROM sumado
        )
        SELECT ipf AS "01 IPF", val AS "Reg_cotiz_ppal", total AS "T_tot_reg_ppal"
        FROM ranked WHERE rn = 1
    """).fetchdf()

    # Grupo_cotiz_ppal / t_tot_grupo_ppal: grupo de cotizacion con mas duracion acumulada
    df_grupo = con.execute(f"""
        WITH sumado AS (
            SELECT "01 IPF" AS ipf, "03 GRU_COTIZACION" AS val, SUM("DURACION_EMPLEO") AS total
            FROM read_parquet('{afiliacion}') WHERE "03 GRU_COTIZACION" IS NOT NULL GROUP BY 1, 2
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

    # Tipo_Contrato_ppal_Agrupado / T_tot_tipo_contrato_ppal
    df_tipo = con.execute(f"""
        WITH sumado AS (
            SELECT "01 IPF" AS ipf, "04 TIPO_CONTRATO" AS val, SUM("DURACION_EMPLEO") AS total
            FROM read_parquet('{afiliacion}') WHERE "04 TIPO_CONTRATO" IS NOT NULL GROUP BY 1, 2
        ), ranked AS (
            SELECT ipf, val, total,
                   ROW_NUMBER() OVER (PARTITION BY ipf ORDER BY total DESC, val ASC) AS rn
            FROM sumado
        )
        SELECT ipf AS "01 IPF",
               CASE
                   WHEN val >= 100 AND val < 200 THEN 'indefinido_t_completo'
                   WHEN val >= 200 AND val < 300 THEN 'Indefinido_t_parcial'
                   WHEN val >= 300 AND val < 400 THEN 'Indefinido_fijo_discontinuo'
                   WHEN val >= 400 AND val < 500 THEN 'Temporal_t_completo'
                   WHEN val >= 500 AND val < 600 THEN 'Temporal_t_parcial'
                   ELSE NULL
               END AS "Tipo_Contrato_ppal_Agrupado",
               total AS "T_tot_tipo_contrato_ppal"
        FROM ranked WHERE rn = 1
    """).fetchdf()

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
# practicas, formacion...) - mismo listado que el cuaderno.
TIPOS_CONTRATO_A_ELIMINAR = (
    3, 4, 6, 23, 24, 25, 29, 34, 63, 81, 89, 95, 98, 200, 209, 230, 231, 239, 241, 250, 251, 252,
    253, 254, 255, 256, 257, 289, 500, 501, 502, 503, 508, 510, 518, 520, 530, 531, 540, 541, 550,
    551, 552, 557, 26, 36, 46, 58, 420, 7, 27, 37, 47, 85, 97, 404, 421,
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

    # Fecha_final_Ultimo_Empleo (Fecha_ini_Primer_Empleo es intermedia, no sobrevive al final,
    # pero se necesita en el Grupo H para Edad_Ultimo_Empleo/Anos_VL, asi que se calcula igual)
    df_fechas = con.execute(f"""
        SELECT "01 IPF" AS "01 IPF",
               MIN("06 FECHA_ALTA_REAL_AFIL") AS "Fecha_ini_Primer_Empleo",
               MAX("Baja_modificada") AS "Fecha_final_Ultimo_Empleo"
        FROM read_parquet('{afiliacion}') GROUP BY 1
    """).fetchdf()

    # Fecha_1er_Empleo_Signif / Duracion_1er_Empleo_Signif / Grupo_cotiz_1er_Empleo_Signif:
    # primer episodio (por fecha de alta) que no sea de los tipos excluidos y dure >= 270 dias
    df_signif = con.execute(f"""
        WITH filtrado AS (
            SELECT "01 IPF" AS ipf, "03 GRU_COTIZACION" AS grupo,
                   "06 FECHA_ALTA_REAL_AFIL" AS fecha_alta, "DURACION_EMPLEO" AS dur
            FROM read_parquet('{afiliacion}')
            WHERE "04 TIPO_CONTRATO" NOT IN ({tipos_sql})
              AND "DURACION_EMPLEO" >= {DURACION_MINIMA_EMPLEO_US}
        ), ranked AS (
            SELECT ipf, grupo, fecha_alta, dur,
                   ROW_NUMBER() OVER (PARTITION BY ipf ORDER BY fecha_alta ASC) AS rn
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

# TRL de prestacion/subsidio del SEPE (cobro actual)
TRL_COBRO_SEPE = (751, 752, 753, 754, 755, 756, 400)


def extraer_grupo_d(con, parquet_folder):
    afiliacion = _ruta(parquet_folder, "tabla_afiliacion_extendida.parquet")

    # Discap_afiliacion: discapacidad detectada en cualquier episodio de la persona
    df_discap = con.execute(f"""
        SELECT DISTINCT "01 IPF" AS "01 IPF", TRUE AS "Discap_afiliacion"
        FROM read_parquet('{afiliacion}')
        WHERE "04 TIPO_CONTRATO" IN (133, 139, 230, 239, 330, 339, 430, 530)
           OR "09 MINUSV_ALTA_AFIL" > 0
    """).fetchdf()

    # Situacion_actual_afiliacion: clasificacion en 9 situaciones a partir del episodio
    # mas reciente (por fecha de baja, y de alta como desempate)
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
                    CASE WHEN trl IN (751, 752, 753, 754, 755, 756) THEN 'Asimilado_al_alta_prest_subs52'
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

    # fecha_ultima_baja + TRL del episodio mas reciente (por fecha de baja unicamente)
    df_ultimabaja = con.execute(f"""
        WITH ranked AS (
            SELECT "01 IPF" AS ipf, "07 FECHA_BAJA_REAL_AFIL" AS fecha_baja, "15 TRL" AS trl,
                   ROW_NUMBER() OVER (PARTITION BY "01 IPF" ORDER BY "07 FECHA_BAJA_REAL_AFIL" DESC) AS rn
            FROM read_parquet('{afiliacion}')
        )
        SELECT ipf AS "01 IPF", fecha_baja AS fecha_ultima_baja FROM ranked WHERE rn = 1
    """).fetchdf()

    # fecha_inicio_paro: para quien esta cobrando del SEPE ahora mismo (episodio mas reciente
    # con TRL de prestacion/subsidio), la fecha de alta del primer episodio de esa racha
    # continua de cobro. Se replica el bucle del cuaderno (que examina hasta 5 episodios y va
    # quitando pares consecutivos que coinciden) con un AND acumulativo por ventana: la racha
    # se corta en el primer episodio (de los 5 mas recientes) que no tenga TRL de cobro.
    trl_sql = ", ".join(str(t) for t in TRL_COBRO_SEPE)
    df_fechainicioparo = con.execute(f"""
        WITH ranked AS (
            SELECT "01 IPF" AS ipf, "06 FECHA_ALTA_REAL_AFIL" AS fecha_alta,
                   "15 TRL" IN ({trl_sql}) AS coincide,
                   ROW_NUMBER() OVER (
                       PARTITION BY "01 IPF"
                       ORDER BY "07 FECHA_BAJA_REAL_AFIL" DESC, "06 FECHA_ALTA_REAL_AFIL" DESC
                   ) AS rn
            FROM read_parquet('{afiliacion}')
        ), top5 AS (
            SELECT * FROM ranked WHERE rn <= 5
        ), acumulado AS (
            SELECT *, bool_and(coincide) OVER (
                PARTITION BY ipf ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS racha
            FROM top5
        ), elegido AS (
            SELECT ipf, fecha_alta, ROW_NUMBER() OVER (PARTITION BY ipf ORDER BY rn DESC) AS rn2
            FROM acumulado WHERE racha
        )
        SELECT ipf AS "01 IPF", fecha_alta AS fecha_inicio_paro FROM elegido WHERE rn2 = 1
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

    df_ultimo_anio = df.groupby("01 IPF")["02 ANO_DATO"].max().reset_index()
    df_ultimo_anio.columns = ["01 IPF", "ultimo_anio"]
    df_con_ultimo = pd.merge(df, df_ultimo_anio, on="01 IPF")
    df_ultimo = df_con_ultimo[df_con_ultimo["02 ANO_DATO"] == df_con_ultimo["ultimo_anio"]]

    df_estudio_pensionactual = df_ultimo.groupby("01 IPF")["Tipo_pension"].apply(list).reset_index()
    df_estudio_pensionactual.columns = ["01 IPF", "Pension_actual"]

    nombre_col_cobra_pension = f"Cobra_pension_{anio_datos}/{str(anio_datos + 1)[-1]}"
    df_estudio_pensionactual[nombre_col_cobra_pension] = "SI"

    df_estudio_pensiontodas = df.groupby("01 IPF")["Tipo_pension"].apply(list).reset_index()
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

    df_sumado = df.groupby("01 IPF")["07 PERC_INTEGRA_DIN_NO_DERIVADA_IL"].sum().reset_index()
    nombre_col_ingresos_brutos = f"Ingresos_bruto_anual_{anio_datos}"
    df_estudio_ingresosbrutos = df_sumado.rename(
        columns={"07 PERC_INTEGRA_DIN_NO_DERIVADA_IL": nombre_col_ingresos_brutos}
    )

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
PAISES_UE = ["N01", "N04", "N10", "N11", "N14", "N15", "N16", "N17", "N19", "N20"]
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
}

# Columnas finales de tabla_personas_extendida, mismo orden que el cuaderno (nuevo_orden)
NUEVO_ORDEN = [
    "01 IPF", "02 FECHA_NACIMIENTO", "03 SEXO", "04 NACIONALIDAD", "05 PROV_NACIMIENTO",
    "06 PROV_1a_AFILIACION", "07 DOMIC_RESID_HABITUAL", "08 FECHA_FALLECIMIENTO", "09 PAIS_NACIMIENTO",
    "Nacionalidad_agrupado", "Pais_nacim_agrupado",
    "Edad_Primer_Empleo_Signif", "Duracion_1er_Empleo_Signif", "Grupo_cotiz_1er_Empleo_Signif",
    "Edad_Ultimo_Empleo", "Grupo_Ultimo_Empleo",
    "t_contrato_mas_largo", "Anos_VL",
    "duracion_promedio_EMPLEO", "Cotizado_anual_promedio",
    "num_altas_anual_promedio", "num_bajas_anual_promedio",
    "T_total_EMPLEO", "T_total_DESEMPLEO",
    "%T_total_EMPLEO", "%T_total_DESEMPLEO",
    "Reg_cotiz_ppal", "T_tot_reg_ppal",
    "Grupo_cotiz_ppal", "t_tot_grupo_ppal",
    "Tipo_Contrato_ppal_Agrupado", "T_tot_tipo_contrato_ppal",
    "Prov_trabajo_ppal", "t_tot_prov_ppal",
    "Act_econ_ppal", "t_tot_act_econ_ppal",
    "Ingresos_bruto_anual_2024",
    "Discap_fiscal", "Discap_afiliacion", "Discap_pension",
    "Pension_todas_cobradas", "Ingresos_pension_bruto_historico", "Bases_cotiz_bruto_historico",
    "Pension_actual", "Cobra_pension_2024/5",
    "fecha_ultima_baja", "Situacion_actual_afiliacion", "Situacion_Paro_Ocupacion",
    "fecha_inicio_paro", "duracion_paro_dias",
]


def _clasificar_regimen(codigo):
    if pd.isna(codigo):
        return "NO CONSTA"
    try:
        codigo_int = int(codigo)
    except (ValueError, TypeError):
        return "NO CONSTA"

    if codigo_int == 138:
        return "RÉGIMEN GENERAL (S.E.C. EMPLEADOS HOGAR)"

    codigo_str = str(codigo_int)
    if len(codigo_str) < 3:
        return "NO CONSTA"

    tercer_digito = codigo_str[-3]
    if tercer_digito == "1":
        return "RÉGIMEN GENERAL"
    elif tercer_digito == "5":
        return "R.E. AUTÓNOMOS"
    elif tercer_digito == "6":
        return "R.E. AGRARIO CUENTA AJENA"
    elif tercer_digito == "7":
        return "R.E. AGRARIO CUENTA PROPIA"
    elif tercer_digito == "8":
        return "R.E. MAR"
    elif tercer_digito == "9":
        return "R.E. MINERÍA CARBÓN"
    elif tercer_digito == "2":
        if len(codigo_str) >= 4 and codigo_str[-4] == "1":
            return "R.E. EMPLEADOS HOGAR"
        return "NO CONSTA"
    return "NO CONSTA"


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
    df["DURACION_1er_Empleo_a_Ultimo_Empleo"] = df["Fecha_final_Ultimo_Empleo"] - df["Fecha_ini_Primer_Empleo"]
    df.drop(columns=["Fecha_ini_Primer_Empleo_completa", "Fecha_final_Ultimo_Empleo_completa"], inplace=True)

    # Edad_Primer_Empleo_Signif
    df["Fecha_1er_Empleo_Signif_completa"] = df["Fecha_1er_Empleo_Signif"].dt.year * 12 + df["Fecha_1er_Empleo_Signif"].dt.month
    df["Edad_Primer_Empleo_Signif"] = df["Fecha_1er_Empleo_Signif_completa"] - df["02 FECHA_NACIMIENTO_completa"]
    df.drop(columns=["Fecha_1er_Empleo_Signif_completa", "02 FECHA_NACIMIENTO_completa"], inplace=True)

    df["Edad_Ultimo_Empleo"] = df["Edad_Ultimo_Empleo"] / 12
    df["Edad_Primer_Empleo_Signif"] = df["Edad_Primer_Empleo_Signif"] / 12

    # num_altas_anual_promedio / num_bajas_anual_promedio: normalizados por anyos de vida laboral
    df["num_altas_anual_promedio"] = df["num_episodios_alta"] / df["DURACION_1er_Empleo_a_Ultimo_Empleo"].dt.days * 365
    df["num_bajas_anual_promedio"] = df["num_episodios_baja"] / df["DURACION_1er_Empleo_a_Ultimo_Empleo"].dt.days * 365
    df.drop(columns=["num_episodios_alta", "num_episodios_baja"], inplace=True)

    # Cotizado_anual_promedio: normalizado por anyos de vida laboral
    df["Cotizado_anual_promedio"] = df["Cotizado_total"] / df["DURACION_1er_Empleo_a_Ultimo_Empleo"].dt.days * 365 / 100

    # Nacionalidad_agrupado / Pais_nacim_agrupado
    condiciones = [
        df["04 NACIONALIDAD"].isin(PAISES_LATINOS), df["04 NACIONALIDAD"].isin(PAISES_AFRICANOS),
        df["04 NACIONALIDAD"].isin(PAISES_UE), df["04 NACIONALIDAD"].isin(PAISES_LEJANOS),
        df["04 NACIONALIDAD"].isin(ESPANA),
    ]
    elecciones = ["paises_latinos", "paises_africanos", "paises_UE", "paises_lejanos", "espana"]
    # default=np.nan con choices de texto revienta en numpy reciente (StrDType vs float no
    # promocionan a un dtype comun); se usa un centinela de texto y se convierte despues.
    df["Nacionalidad_agrupado"] = np.select(condiciones, elecciones, default="_SIN_DATO_")
    df["Nacionalidad_agrupado"] = df["Nacionalidad_agrupado"].replace("_SIN_DATO_", np.nan)

    condiciones_pais_nacim = [
        df["09 PAIS_NACIMIENTO"].isin(PAISES_LATINOS), df["09 PAIS_NACIMIENTO"].isin(PAISES_AFRICANOS),
        df["09 PAIS_NACIMIENTO"].isin(PAISES_UE), df["09 PAIS_NACIMIENTO"].isin(PAISES_LEJANOS),
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

    # Reclasificacion de Parado en Parado_LD / Parado_NoLD (vectorizado igual)
    usa_fecha_inicio_paro = df["Situacion_actual_afiliacion"].isin(
        ["Asimilado_otros", "Asimilado_al_alta_prest_subs52", "Baja_de_prestacion", "Baja_de_subsidio"]
    )
    dias_desde_inicio = (FECHA_REFERENCIA_45 - df["fecha_inicio_paro"]).dt.days
    dias_desde_ultima_baja = (FECHA_REFERENCIA_45 - df["fecha_ultima_baja"]).dt.days

    es_parado = df["Situacion_Paro_Ocupacion"] == "Parado"
    duracion_paro_dias = pd.Series(np.nan, index=df.index)
    mask_inicio = es_parado & usa_fecha_inicio_paro & df["fecha_inicio_paro"].notna()
    duracion_paro_dias[mask_inicio] = dias_desde_inicio[mask_inicio]
    mask_ultima_baja = es_parado & ~usa_fecha_inicio_paro & df["fecha_ultima_baja"].notna()
    duracion_paro_dias[mask_ultima_baja] = dias_desde_ultima_baja[mask_ultima_baja]
    df["duracion_paro_dias"] = duracion_paro_dias

    umbral_ld = 365
    es_ld = es_parado & duracion_paro_dias.notna() & (duracion_paro_dias > umbral_ld)
    df.loc[es_parado & es_ld, "Situacion_Paro_Ocupacion"] = "Parado_LD"
    df.loc[es_parado & ~es_ld, "Situacion_Paro_Ocupacion"] = "Parado_NoLD"

    # %T_total_EMPLEO / %T_total_DESEMPLEO
    df["%T_total_EMPLEO"] = df["T_total_EMPLEO"] / df["DURACION_1er_Empleo_a_Ultimo_Empleo"]
    df["%T_total_DESEMPLEO"] = df["T_total_DESEMPLEO"] / df["DURACION_1er_Empleo_a_Ultimo_Empleo"]

    # Conversion de columnas Timedelta a anyos (in-place)
    for col in df.columns:
        if pd.api.types.is_timedelta64_dtype(df[col]):
            df[col] = df[col].dt.days / DIAS_POR_ANIO
    df = df.rename(columns={"DURACION_1er_Empleo_a_Ultimo_Empleo": "Anos_VL"})

    # Diccionario de grupos de cotizacion
    for columna in ["Grupo_cotiz_ppal", "Grupo_cotiz_1er_Empleo_Signif", "Grupo_Ultimo_Empleo"]:
        df[columna] = df[columna].map(GRUPOS_COTIZ_DICT).fillna("NO CONSTA").astype("category")

    # Regimenes de cotizacion
    df["Reg_cotiz_ppal"] = df["Reg_cotiz_ppal"].apply(_clasificar_regimen).astype("category")

    # Filtrar personas sin fecha de nacimiento
    antes = df.shape[0]
    df = df[df["02 FECHA_NACIMIENTO"].notna()].copy()
    print(f"Filas eliminadas por no tener fecha de nacimiento: {antes - df.shape[0]}")

    # Filtrar outliers de Anos_VL (± 3 desviaciones estandar)
    media = df["Anos_VL"].mean()
    std = df["Anos_VL"].std()
    limite_inferior = media - 3 * std
    limite_superior = media + 3 * std
    outliers = df[(df["Anos_VL"] < limite_inferior) | (df["Anos_VL"] > limite_superior)]
    print(f"Outliers de Anos_VL: {len(outliers)} ({len(outliers) / len(df) * 100:.2f}%)")
    df = df[~df.index.isin(outliers.index)].copy()

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
    # filas).
    codigo_cnae = df["Act_econ_ppal"].astype("Int64").astype(str).str.zfill(3)
    df["Act_econ_ppal"] = np.where(
        codigo_cnae == "000",
        "SIN ACTIVIDAD ECONÓMICA (RELACIÓN NO LABORAL / SIN DATO)",
        codigo_cnae + " " + codigo_cnae.map(cnae_3d_dict),
    )
    df["Act_econ_ppal"] = df["Act_econ_ppal"].fillna("NO CONSTA").astype("category")

    # Reordenar y seleccionar las columnas finales
    columnas_presentes = [c for c in NUEVO_ORDEN if c in df.columns]
    columnas_faltantes = [c for c in NUEVO_ORDEN if c not in df.columns]
    if columnas_faltantes:
        print(f"AVISO: columnas del esquema objetivo que todavia no existen en df: {columnas_faltantes}")
    df = df[columnas_presentes]
    print(f"Grupo H: forma final {df.shape}")

    df.to_parquet(output_path, index=False)
    return df
