"""Adapta las reglas de consistencia (tests/reglas_consistencia_v4.xlsx) a un año de edición
distinto del que tenían escrito literalmente (2024) cuando se redactaron. Ver conversación
2026-10-02 (primera vez que se pasan las reglas contra una edición distinta de 2024, la MCVL
2025): unas 50 reglas referencian columnas con el año en el nombre (`ingresos_*_2024`,
`retenciones_irpf_2024`, `cobra_pension_2024_5`) o fechas relativas a la edición (`DATE
'2024-12-31'`, `202412`, `DATE '2025-04-30'` = fecha de corte − 1 día) de forma literal, en vez de
genérica. Se traducen aquí, en el runner, en vez de reescribir el Excel de 538 reglas: el Excel
sigue documentando el mismo razonamiento, igual de válido, solo que escrito en literal para 2024.
"""

PREFIJOS_INGRESOS = (
    "ingresos_sueldo", "ingresos_pension_y_bajas", "ingresos_prestacion",
    "ingresos_actividad", "ingresos_otras_exentas", "ingresos_bruto_total", "retenciones_irpf",
)


def sustituciones_para_anio(anio_edicion: int) -> dict:
    """anio_edicion: el año real de la muestra activa (el que ya calcula anio_de() en config.py).
    Devuelve {} para 2024 (el año para el que se escribieron las reglas, nada que traducir)."""
    if anio_edicion == 2024:
        return {}
    cutoff_siguiente = anio_edicion + 1  # fecha de corte = 1 de mayo del año siguiente a la edición
    sustituciones = {
        "DATE '2024-12-31'": f"DATE '{anio_edicion}-12-31'",
        "202412": f"{anio_edicion}12",
        "DATE '2025-04-30'": f"DATE '{cutoff_siguiente}-04-30'",  # fecha de corte - 1 dia
        "cobra_pension_2024_5": f"cobra_pension_{anio_edicion}_{str(cutoff_siguiente)[-1]}",
    }
    for prefijo in PREFIJOS_INGRESOS:
        sustituciones[f"{prefijo}_2024"] = f"{prefijo}_{anio_edicion}"
    return sustituciones


def adaptar_sql_al_anio(sql: str, anio_edicion: int) -> str:
    for viejo, nuevo in sustituciones_para_anio(anio_edicion).items():
        sql = sql.replace(viejo, nuevo)
    return sql
