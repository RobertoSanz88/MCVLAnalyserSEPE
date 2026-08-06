"""Dispatcher de un paso del ETL, pensado para lanzarse como subproceso desde main.py
(cada print() de las funciones reales de etl/ sale por stdout linea a linea, que main.py
retransmite al navegador via SSE). Tambien se puede lanzar a mano desde la terminal.
"""

import argparse
import os
import sys

# Aseguramos que la raiz del proyecto este en sys.path, se ejecute como se ejecute este
# fichero (subproceso con ruta absoluta, `python -m`, etc.) - de lo contrario "from etl...."
# falla con ModuleNotFoundError segun desde donde se lance.
_RAIZ_PROYECTO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _RAIZ_PROYECTO not in sys.path:
    sys.path.insert(0, _RAIZ_PROYECTO)

import duckdb
import pandas as pd

from etl.optimize_types import TABLE_CONFIGS, process_table, process_table_grande
from etl.personas_extendida import (
    bootstrap_personas_extendida, extraer_grupo_a, extraer_grupo_b, extraer_grupo_c,
    fase1_afiliacion_extendida_duckdb, fase2_pensiones_extendida, fusionar_grupo,
    fusionar_grupo_d, fusionar_grupo_e, fusionar_grupo_f, fusionar_grupo_g, grupo_h_final,
)
from etl.recursos import ram_total_gb, threads_optimos

TABLAS_SIMPLES = {"personas": "2", "fiscal": "6", "bases_cp": "4"}
TABLAS_GRANDES = {"bases_ca": "3"}

TODAS_LAS_TABLAS = ["personas", "fiscal", "bases_cp", "bases_ca", "pensiones", "afiliacion"]


def _nueva_conexion(hilos):
    con = duckdb.connect(":memory:")
    con.execute("SET preserve_insertion_order=false")
    con.execute(f"SET threads={hilos}")
    return con


def ejecutar(accion, year, raw_root, parquet_folder, borrar_txt, provincias_csv, cnae_csv):
    ram_gb = ram_total_gb()
    hilos = threads_optimos(ram_gb)
    print(f"RAM detectada: {ram_gb:.1f} GB -> hilos DuckDB={hilos}"
          f"{' (modo rapido, RAM de sobra)' if hilos > 2 else ' (modo conservador)'}")
    con = _nueva_conexion(hilos)

    if accion in TABLAS_SIMPLES:
        df = process_table(TABLE_CONFIGS[TABLAS_SIMPLES[accion]], year, raw_root, parquet_folder)
        del df

    elif accion in TABLAS_GRANDES:
        process_table_grande(
            TABLE_CONFIGS[TABLAS_GRANDES[accion]], year, raw_root, parquet_folder, con, borrar_txt=borrar_txt
        )

    elif accion == "pensiones":
        df = process_table(TABLE_CONFIGS["5"], year, raw_root, parquet_folder)
        del df
        fase2_pensiones_extendida(parquet_folder)

    elif accion == "afiliacion":
        process_table_grande(TABLE_CONFIGS["1"], year, raw_root, parquet_folder, con, borrar_txt=borrar_txt)
        fase1_afiliacion_extendida_duckdb(con, parquet_folder)

    elif accion == "todas":
        for sub in TODAS_LAS_TABLAS:
            print(f"\n{'#' * 80}\n# {sub.upper()}\n{'#' * 80}")
            ejecutar(sub, year, raw_root, parquet_folder, borrar_txt, provincias_csv, cnae_csv)

    elif accion == "personas_extendida":
        print("Bootstrap...")
        bootstrap_personas_extendida(con, parquet_folder)

        print("Grupo A (afiliacion: regimen/grupo/contrato)...")
        fusionar_grupo(parquet_folder, extraer_grupo_a(con, parquet_folder))

        print("Grupo B (afiliacion: primer/ultimo empleo)...")
        fusionar_grupo(parquet_folder, extraer_grupo_b(con, parquet_folder))

        print("Grupo C (afiliacion: duraciones/provincia/actividad)...")
        fusionar_grupo(parquet_folder, extraer_grupo_c(con, parquet_folder))

        print("Grupo D (afiliacion: situacion/paro)...")
        fusionar_grupo_d(parquet_folder, con)

        print("Grupo E (pensiones)...")
        df_pen = pd.read_parquet(os.path.join(parquet_folder, "tabla_pensiones_extendida.parquet"))
        fusionar_grupo_e(parquet_folder, df_pen, int(year))
        del df_pen

        print("Grupo F (datos fiscales)...")
        fusionar_grupo_f(parquet_folder, int(year))

        print("Grupo G (bases de cotizacion)...")
        fusionar_grupo_g(parquet_folder, con)

        print("Grupo H (calculos finales, filtros, seleccion de columnas)...")
        df_final = grupo_h_final(parquet_folder, provincias_csv, cnae_csv)
        print(f"tabla_personas_extendida.parquet lista: {df_final.shape}")

    else:
        raise ValueError(f"Accion desconocida: {accion!r}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accion", required=True)
    parser.add_argument("--year", default="2024")
    parser.add_argument("--raw", required=True, help="Carpeta con los TXT brutos")
    parser.add_argument("--parquet", required=True, help="Carpeta de salida de los Parquet")
    parser.add_argument("--borrar-txt", action="store_true")
    parser.add_argument("--provincias-csv", default=None)
    parser.add_argument("--cnae-csv", default=None)
    args = parser.parse_args()

    provincias_csv = args.provincias_csv or os.path.join(os.path.dirname(args.parquet.rstrip("/\\")), "provincias_dict.csv")
    cnae_csv = args.cnae_csv or os.path.join(os.path.dirname(args.parquet.rstrip("/\\")), "cnae_3d_dict.csv")

    ejecutar(args.accion, args.year, args.raw, args.parquet, args.borrar_txt, provincias_csv, cnae_csv)
    print("\nHECHO", flush=True)
