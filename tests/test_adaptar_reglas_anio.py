import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from adaptar_reglas_anio import adaptar_sql_al_anio, sustituciones_para_anio


def test_sin_cambios_para_2024():
    assert sustituciones_para_anio(2024) == {}
    sql = "ingresos_sueldo_2024 > 0 AND fecha_ultima_baja > DATE '2025-04-30'"
    assert adaptar_sql_al_anio(sql, 2024) == sql


def test_columnas_de_ingresos_y_retenciones_cambian_de_anio():
    sql = "ingresos_sueldo_2024 > 0 AND retenciones_irpf_2024 >= 0"
    assert adaptar_sql_al_anio(sql, 2025) == "ingresos_sueldo_2025 > 0 AND retenciones_irpf_2025 >= 0"


def test_cobra_pension_usa_el_ultimo_digito_del_anyo_siguiente():
    assert adaptar_sql_al_anio("cobra_pension_2024_5 = 'SI'", 2025) == "cobra_pension_2025_6 = 'SI'"
    assert adaptar_sql_al_anio("cobra_pension_2024_5 = 'SI'", 2029) == "cobra_pension_2029_0 = 'SI'"


def test_fecha_fin_de_anyo_de_la_edicion():
    sql = "fecha_inicio_ultima_racha_sin_empleo > DATE '2024-12-31'"
    assert adaptar_sql_al_anio(sql, 2025) == "fecha_inicio_ultima_racha_sin_empleo > DATE '2025-12-31'"


def test_fecha_yyyymm_fin_de_anyo_de_la_edicion():
    sql = "fecha_fallecimiento BETWEEN 1 AND 202412"
    assert adaptar_sql_al_anio(sql, 2025) == "fecha_fallecimiento BETWEEN 1 AND 202512"


def test_ultima_baja_real_es_el_dia_antes_de_la_fecha_de_corte():
    # fecha de corte = 1 de mayo del anyo SIGUIENTE a la edicion -> ultima baja real = 30 de abril de ese anyo
    sql = "fecha_ultima_baja > DATE '2025-04-30'"
    assert adaptar_sql_al_anio(sql, 2025) == "fecha_ultima_baja > DATE '2026-04-30'"


def test_fecha_fija_no_relacionada_con_la_edicion_no_cambia():
    sql = "fecha_ultima_baja < DATE '1967-01-01'"  # dato desde 1967, constante real, no de la edicion
    assert adaptar_sql_al_anio(sql, 2025) == sql
