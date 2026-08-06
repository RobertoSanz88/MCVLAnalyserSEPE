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
            "Reg_cotiz_ppal": "reg_cotiz_ppal",
            "T_tot_reg_ppal": "t_tot_reg_ppal",
            "Grupo_cotiz_ppal": "grupo_cotiz_ppal",
            "t_tot_grupo_ppal": "t_tot_grupo_ppal",
            "t_contrato_mas_largo": "t_contrato_mas_largo",
            "T_tot_tipo_contrato_ppal": "t_tot_tipo_contrato_ppal",
            "Tipo_Contrato_ppal_Agrupado": "tipo_contrato_ppal_agrupado",
            "Grupo_Ultimo_Empleo": "grupo_ultimo_empleo",
            "Duracion_1er_Empleo_Signif": "duracion_1er_empleo_signif",
            "Grupo_cotiz_1er_Empleo_Signif": "grupo_cotiz_1er_empleo_signif",
            "duracion_promedio_EMPLEO": "duracion_promedio_empleo",
            "T_total_EMPLEO": "t_total_empleo",
            "T_total_DESEMPLEO": "t_total_desempleo",
            "Prov_trabajo_ppal": "prov_trabajo_ppal",
            "t_tot_prov_ppal": "t_tot_prov_ppal",
            "Act_econ_ppal": "act_econ_ppal",
            "t_tot_act_econ_ppal": "t_tot_act_econ_ppal",
            "Discap_afiliacion": "discap_afiliacion",
            "Situacion_actual_afiliacion": "situacion_actual_afiliacion",
            "fecha_ultima_baja": "fecha_ultima_baja",
            "fecha_inicio_paro": "fecha_inicio_paro",
            "Pension_actual": "pension_actual",
            "Cobra_pension_2024/5": "cobra_pension_2024_5",
            "Pension_todas_cobradas": "pension_todas_cobradas",
            "Ingresos_pension_bruto_historico": "ingresos_pension_bruto_historico",
            "Discap_pension": "discap_pension",
            "Ingresos_bruto_anual_2024": "ingresos_bruto_anual_2024",
            "Discap_fiscal": "discap_fiscal",
            "Bases_cotiz_bruto_historico": "bases_cotiz_bruto_historico",
            "Edad_Ultimo_Empleo": "edad_ultimo_empleo",
            "Anos_VL": "anos_vl",
            "Edad_Actual_Fecha_Corte": "edad_actual_fecha_corte",
            "Edad_Primer_Empleo_Signif": "edad_primer_empleo_signif",
            "num_altas_anual_promedio": "num_altas_anual_promedio",
            "num_bajas_anual_promedio": "num_bajas_anual_promedio",
            "Cotizado_anual_promedio": "cotizado_anual_promedio",
            "Nacionalidad_agrupado": "nacionalidad_agrupado",
            "Pais_nacim_agrupado": "pais_nacim_agrupado",
            "Situacion_Paro_Ocupacion": "situacion_paro_ocupacion",
            "duracion_paro_dias": "duracion_paro_dias",
            "%T_total_EMPLEO": "pct_t_total_empleo",
            "%T_total_DESEMPLEO": "pct_t_total_desempleo",
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
        select_list = ", ".join(
            f"{_quote(raw)} AS {clean}" for raw, clean in columns.items()
        )
        con.execute(
            f"CREATE OR REPLACE VIEW {view_name} AS "
            f"SELECT {select_list} FROM read_parquet('{parquet_path.as_posix()}')"
        )
    return con
