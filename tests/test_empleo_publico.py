"""Empleo_publico se calculaba sobre TODOS los episodios de Afiliacion, incluidos los de
prestacion/subsidio (TRL 700-799/400) - el NIF del organismo que paga la prestacion (el SEPE,
letra Q) marcaba como "empleo publico" a gente que solo esta cobrando el paro, no trabajando en
la administracion (bug real detectado por el usuario, 2026-10-10: verificado con datos reales que
el 94,94% de los episodios de prestacion/subsidio tiene letra Q). Estas pruebas fijan que
Empleo_publico solo sea SI para episodios de empleo real."""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
import duckdb  # noqa: E402
import pandas as pd  # noqa: E402
from etl.personas_extendida import fase1_afiliacion_extendida_duckdb, fijar_fecha_corte  # noqa: E402


def _episodios_de_prueba():
    comunes = {"01 IPF": 1, "06 FECHA_ALTA_REAL_AFIL": pd.Timestamp("2010-01-01")}
    return pd.DataFrame([
        {**comunes, "15 TRL": 100, "18 EMPLEADOR_LETRA_NIF": "Q",
         "07 FECHA_BAJA_REAL_AFIL": pd.Timestamp("2010-06-30")},  # empleo real, publico -> SI
        {**comunes, "15 TRL": 100, "18 EMPLEADOR_LETRA_NIF": "A",
         "07 FECHA_BAJA_REAL_AFIL": pd.Timestamp("2010-06-30")},  # empleo real, privado -> NO
        {**comunes, "15 TRL": 753, "18 EMPLEADOR_LETRA_NIF": "Q",
         "07 FECHA_BAJA_REAL_AFIL": pd.Timestamp("2010-06-30")},  # prestacion/subsidio (SEPE) -> NO
        {**comunes, "15 TRL": 400, "18 EMPLEADOR_LETRA_NIF": "Q",
         "07 FECHA_BAJA_REAL_AFIL": pd.Timestamp("2010-06-30")},  # TRL 400, sin alta laboral -> NO
    ])


def test_empleo_publico_excluye_prestacion_y_subsidio(tmp_path):
    fijar_fecha_corte("2025-05-01")
    _episodios_de_prueba().to_parquet(tmp_path / "tabla_afiliacion_optimizada.parquet")

    con = duckdb.connect()
    salida = fase1_afiliacion_extendida_duckdb(con, str(tmp_path))
    df = con.execute(f"SELECT * FROM read_parquet('{salida}') ORDER BY \"15 TRL\"").fetchdf()

    esperado = {100: "SI", 400: "NO", 753: "NO"}
    # TRL 100 privado y TRL 100 publico comparten TRL=100: comprobamos por combinacion TRL+letra.
    por_fila = dict(zip(zip(df["15 TRL"], df["18 EMPLEADOR_LETRA_NIF"]), df["Empleo_publico"]))
    assert por_fila[(100, "Q")] == "SI"
    assert por_fila[(100, "A")] == "NO"
    assert por_fila[(753, "Q")] == "NO"
    assert por_fila[(400, "Q")] == "NO"
