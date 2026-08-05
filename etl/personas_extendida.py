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
