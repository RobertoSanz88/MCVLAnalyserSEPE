"""Conexion DuckDB y registro de vistas sobre los Parquet de la MCVL.

Las vistas registradas aqui traducen las columnas crudas del Parquet
(con prefijo numerico, espacios o simbolos) a nombres limpios en
snake_case. El resto de la app (routers/, etc.) debe consultar siempre
estas vistas y usar los nombres limpios, nunca los crudos.
"""

import duckdb

from config import get_parquet_dir

PARQUET_DIR = get_parquet_dir()

# view_name -> (fichero parquet, {columna_cruda: columna_limpia})
TABLES = {
    "personas": (
        "tabla_personas_extendida.parquet",
        {
            "01 IPF": "ipf",
            "02 FECHA_NACIMIENTO": "fecha_nacimiento",
            "03 SEXO": "sexo",
            "04 NACIONALIDAD": "nacionalidad",
            "05 PROV_NACIMIENTO": "prov_nacimiento",
            "06 PROV_1a_AFILIACION": "prov_1a_afiliacion",
            "07 DOMIC_RESID_HABITUAL": "domic_resid_habitual",
            "08 FECHA_FALLECIMIENTO": "fecha_fallecimiento",
            "09 PAIS_NACIMIENTO": "pais_nacimiento",
            "Reg_cotiz_ppal_Agrupado": "reg_cotiz_ppal_agrupado",
            "T_tot_reg_ppal": "t_tot_reg_ppal",
            "Grupo_cotiz_ppal": "grupo_cotiz_ppal",
            "t_tot_grupo_ppal": "t_tot_grupo_ppal",
            "t_contrato_mas_largo": "t_contrato_mas_largo",
            "Causa_Baja_Trab_Ppal": "causa_baja_trab_ppal",
            "T_tot_tipo_contrato_ppal": "t_tot_tipo_contrato_ppal",
            "Tipo_Contrato_ppal_Agrupado": "tipo_contrato_ppal_agrupado",
            "Grupo_Ultimo_Empleo": "grupo_ultimo_empleo",
            "causa_ult_baja": "causa_ult_baja",
            "tipo_contr_ult_baja": "tipo_contr_ult_baja",
            "cnae_ult_baja": "cnae_ult_baja",
            "Duracion_1er_Empleo_Signif": "duracion_1er_empleo_signif",
            "Grupo_cotiz_1er_Empleo_Signif": "grupo_cotiz_1er_empleo_signif",
            "duracion_promedio_EMPLEO": "duracion_promedio_empleo",
            "T_total_EMPLEO": "t_total_empleo",
            "T_total_DESEMPLEO": "t_total_desempleo",
            "T_total_MIXTO_EMPL_DESEMPL": "t_total_mixto_empl_desempl",
            "T_total_NO_EMPLEO": "t_total_no_empleo",
            "Prov_trabajo_ppal": "prov_trabajo_ppal",
            "t_tot_prov_ppal": "t_tot_prov_ppal",
            "Act_econ_ppal": "act_econ_ppal",
            "t_tot_act_econ_ppal": "t_tot_act_econ_ppal",
            "Discap_afiliacion": "discap_afiliacion",
            "Situacion_actual_afiliacion": "situacion_actual_afiliacion",
            "fecha_ultima_baja": "fecha_ultima_baja",
            "Fecha_inicio_ultima_racha_sin_empleo": "fecha_inicio_ultima_racha_sin_empleo",
            "Pension_actual": "pension_actual",
            "Cobra_pension_2024/5": "cobra_pension_2024_5",
            "Pension_todas_cobradas": "pension_todas_cobradas",
            "Discap_pension": "discap_pension",
            "Ingresos_bruto_anual_2024": "ingresos_bruto_anual_2024",
            "Prov_fiscal_actual": "prov_fiscal_actual",
            "Discap_fiscal": "discap_fiscal",
            "Fecha_ini_Primer_Empleo": "fecha_ini_primer_empleo",
            "Edad_Ultimo_Empleo": "edad_ultimo_empleo",
            "Tiempo_total_desde_inicio_VL": "tiempo_total_desde_inicio_vl",
            "Edad_Actual_Fecha_Corte": "edad_actual_fecha_corte",
            "Edad_Primer_Empleo_Signif": "edad_primer_empleo_signif",
            "num_altas_anual_promedio": "num_altas_anual_promedio",
            "num_bajas_anual_promedio": "num_bajas_anual_promedio",
            "Cotizado_anual_promedio": "cotizado_anual_promedio",
            "Nacionalidad_agrupado": "nacionalidad_agrupado",
            "Pais_nacim_agrupado": "pais_nacim_agrupado",
            "Situacion_Paro_Ocupacion": "situacion_paro_ocupacion",
            "duracion_actual_sin_empleo": "duracion_actual_sin_empleo",
            "causa_baja_mas_comun": "causa_baja_mas_comun",
            "causas_baja_distintas": "causas_baja_distintas",
            "%T_total_EMPLEO": "pct_t_total_empleo",
            "%T_total_DESEMPLEO": "pct_t_total_desempleo",
            "%T_total_MIXTO_EMPL_DESEMPL": "pct_t_total_mixto_empl_desempl",
            "%T_total_NO_EMPLEO": "pct_t_total_no_empleo",
        },
    ),
    "afiliacion": (
        "tabla_afiliacion_extendida.parquet",
        {
            "01 IPF": "ipf",
            "02 REG_COTIZACION": "reg_cotizacion",
            "03 GRU_COTIZACION": "gru_cotizacion",
            "04 TIPO_CONTRATO": "tipo_contrato",
            "05 COEF_T_PARCIAL": "coef_t_parcial",
            "06 FECHA_ALTA_REAL_AFIL": "fecha_alta_real_afil",
            "07 FECHA_BAJA_REAL_AFIL": "fecha_baja_real_afil",
            "08 CAUSA_BAJA_AFIL": "causa_baja_afil",
            "09 MINUSV_ALTA_AFIL": "minusv_alta_afil",
            "10 CCC_2": "ccc_2",
            "11 DOMIC_CC_2": "domic_cc_2",
            "12 CNAE2009_CC": "cnae2009_cc",
            "13 NUM_TRABAJS_CC_2": "num_trabajs_cc_2",
            "14 FECHA_ALTA_1ER_TRABAJ_CC": "fecha_alta_1er_trabaj_cc",
            "15 TRL": "trl",
            "16 COLEC_ESPECIAL_CC": "colec_especial_cc",
            "17 EMPLEADOR_TIPO_ID": "empleador_tipo_id",
            "18 EMPLEADOR_LETRA_NIF": "empleador_letra_nif",
            "19 ID_ANONIMADO_PAGADORA": "id_anonimado_pagadora",
            "20 CCC_PPAL": "ccc_ppal",
            "21 PROVINCIA_DOMIC_CC_PPAL": "provincia_domic_cc_ppal",
            "22 FECHA_MODIF_TIPO_CONTR_INI_O_COEF_T_PARCIAL_INI": "fecha_modif_tipo_contr_ini_o_coef_t_parcial_ini",
            "23 TIPO_CONTRATO_INI": "tipo_contrato_ini",
            "24 COEF_T_PARCIAL_INI": "coef_t_parcial_ini",
            "25 FECHA_MODIF_TIPO_CONTR_2_O_COEF_T_PARCIAL_2": "fecha_modif_tipo_contr_2_o_coef_t_parcial_2",
            "26 TIPO_CONTRATO_2": "tipo_contrato_2",
            "27 COEF_T_PARCIAL_2": "coef_t_parcial_2",
            "28 FECHA_MODIF_GRUPO_COTIZ_INI": "fecha_modif_grupo_cotiz_ini",
            "29 GRUPO_COTIZ_INI": "grupo_cotiz_ini",
            "30 CNAE1993_CC": "cnae1993_cc",
            "31 SETA": "seta",
            "32 TR CON OTRAS ENT_O_AUT": "tr_con_otras_ent_o_aut",
            "33 FECHA_EFECTO_ALTA_AFIL": "fecha_efecto_alta_afil",
            "34 FECHA_EFECTO_BAJA_AFIL": "fecha_efecto_baja_afil",
            "35 CONVENIO_COLECTIVO_1": "convenio_colectivo_1",
            "36 CONVENIO_COLECTIVO_2": "convenio_colectivo_2",
            "37 NUM_TRABAJS_CC_PPAL": "num_trabajs_cc_ppal",
            "Baja_modificada": "baja_modificada",
            "DURACION_EMPLEO": "duracion_empleo",
            "DURACION_DESEMPLEO": "duracion_desempleo",
            "Alta_desplazada": "alta_desplazada",
            "DURACION_NO_EMPLEO": "duracion_no_empleo",
            "Empleo_publico": "empleo_publico",
        },
    ),
    # Bases de cotizacion: solo las 3 columnas que usa /trayectorias/colectivo
    # (ipf, anyo, base anual total), no las 12 columnas mensuales del Parquet -
    # sin uso todavia en la app. CA (cuenta ajena) y CP (cuenta propia,
    # autonomos) comparten exactamente el mismo esquema de columnas crudas.
    "bases_ca": (
        "tabla_bases_cotizacion_CA_optimizada.parquet",
        {
            "01 IPF": "ipf",
            "03 ANO_COTIZACION": "ano_cotizacion",
            "16 BCC_ANUAL_TOTAL": "bcc_anual_total",
        },
    ),
    "bases_cp": (
        "tabla_bases_cotizacion_CP_optimizada.parquet",
        {
            "01 IPF": "ipf",
            "03 ANO_COTIZACION": "ano_cotizacion",
            "16 BCC_ANUAL_TOTAL": "bcc_anual_total",
        },
    ),
    # Pensiones: solo las columnas que usan /trayectorias/colectivo/pensiones y
    # /trayectorias/colectivo/categorica. Ingresos_pension_bruto_anual ya viene
    # calculada desde el ETL a partir de IMPORTE_MES_TOTAL_PRESTACION (x14, o
    # x12 para la excepcion de clase de prestacion 17 - ver
    # fase2_pensiones_extendida en etl/personas_extendida.py) - no recalcular
    # ese x14 aqui, perderia esa excepcion. IMPORTE_MES_TOTAL_PRESTACION en si
    # no se registra: es la misma cifra sin anualizar, redundante con la
    # anual. Tipo_pension tambien viene ya calculada del ETL (misma fase,
    # _categorizar_tipo_pension) - no hace falta reimplementar esa
    # categorizacion en la app.
    "pensiones": (
        "tabla_pensiones_extendida.parquet",
        {
            "01 IPF": "ipf",
            "02 ANO_DATO": "ano_dato",
            "Ingresos_pension_bruto_anual": "ingresos_pension_bruto_anual",
            "Tipo_pension": "tipo_pension",
        },
    ),
}


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def get_connection() -> duckdb.DuckDBPyConnection:
    """Abre una conexion DuckDB en memoria y registra una vista por tabla MCVL,
    con columnas ya renombradas a snake_case."""
    con = duckdb.connect(database=":memory:")
    for view_name, (filename, columns) in TABLES.items():
        parquet_path = PARQUET_DIR / filename
        # "src." delante de cada columna cruda (no solo _quote(raw)) a
        # proposito: sin cualificar, columnas como "Reg_cotiz_ppal" AS
        # reg_cotiz_ppal (mismas letras, solo cambia mayus/minus) chocan con
        # un bug real del binder de DuckDB (regresion desde v1.1.1,
        # duckdb/duckdb#14275) que las trata como una autorreferencia
        # circular y lanza BinderException "cannot be referenced before it
        # is defined" - visto en produccion con duckdb mas antiguo que el
        # 1.5.5 de este entorno de desarrollo (que no lo reproduce). Requiere
        # duckdb>=1.5.5 (ver requirements.txt) para evitarlo aunque alguien
        # quite esta cualificacion sin darse cuenta.
        select_list = ", ".join(
            f"src.{_quote(raw)} AS {clean}" for raw, clean in columns.items()
        )
        con.execute(
            f"CREATE OR REPLACE VIEW {view_name} AS "
            f"SELECT {select_list} FROM read_parquet('{parquet_path.as_posix()}') AS src"
        )
    return con
