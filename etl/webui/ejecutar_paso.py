"""Dispatcher de un paso del ETL, pensado para lanzarse como subproceso desde main.py
(cada print() de las funciones reales de etl/ sale por stdout linea a linea, que main.py
retransmite al navegador via SSE). Tambien se puede lanzar a mano desde la terminal.
"""

import argparse
import os
import shutil
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
    fase1_afiliacion_extendida_duckdb, fase1b_tramos_fusionados_duckdb, fase2_pensiones_extendida,
    fusionar_grupo, fusionar_grupo_d, fusionar_grupo_e, fusionar_grupo_f, fusionar_grupo_g,
    grupo_h_final,
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


def _ruta_extendida(parquet_folder):
    return os.path.join(parquet_folder, "tabla_personas_extendida.parquet")


def _ruta_preh(parquet_folder):
    return os.path.join(parquet_folder, "tabla_personas_extendida_preH.parquet")


def _borrar_optimizada(parquet_folder, nombre_tabla):
    """Borra tabla_<nombre_tabla>_optimizada.parquet una vez que su extendida ya existe:
    en los tres casos donde se usa (personas, pensiones, afiliacion) la extendida parte de
    un SELECT * / COPY sobre la optimizada y solo anade columnas nuevas, nunca modifica ni
    quita las originales - la optimizada queda entonces totalmente redundante y no se quiere
    (un solo fichero por tabla, no dos)."""
    ruta = os.path.join(parquet_folder, f"tabla_{nombre_tabla}_optimizada.parquet")
    if os.path.exists(ruta):
        os.remove(ruta)
        print(f"Borrado {os.path.basename(ruta)} (ya incluido entero en la extendida)")


def ejecutar(accion, year, raw_root, parquet_folder, borrar_txt, provincias_csv, cnae_csv, paises_csv, causa_baja_csv):
    # Fecha de corte = 1 de mayo del anyo siguiente al de la casilla "Anyo"; se fija en el ETL y se
    # deja escrita junto a los Parquet para que la app use la misma (ver config.py).
    import etl.personas_extendida as _pe
    from config import escribir_fecha_corte
    os.makedirs(parquet_folder, exist_ok=True)
    _fecha_corte = escribir_fecha_corte(parquet_folder, year)
    _pe.fijar_fecha_corte(_fecha_corte)
    print(f"Fecha de corte: {_fecha_corte} (1 de mayo del anyo siguiente a {year})")
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
        _borrar_optimizada(parquet_folder, "pensiones")

    elif accion == "afiliacion":
        process_table_grande(TABLE_CONFIGS["1"], year, raw_root, parquet_folder, con, borrar_txt=borrar_txt)
        fase1_afiliacion_extendida_duckdb(con, parquet_folder)
        fase1b_tramos_fusionados_duckdb(con, parquet_folder)
        _borrar_optimizada(parquet_folder, "afiliacion")

    elif accion == "tramos":
        # Solo la fusion de tramos (Paso 2, tabla_afiliacion_tramos.parquet) sobre la
        # tabla_afiliacion_extendida.parquet ya existente (2026-09-24): sin releer los TXT ni recalcular la
        # Fase 1. Para regenerarlos tras un arreglo de fase1b_tramos_fusionados_duckdb; despues hay que repetir
        # "Personas" y el boton final ("Construir tabla de personas extendida") porque los T_total_* salen de aqui.
        fase1b_tramos_fusionados_duckdb(con, parquet_folder)
        print("tabla_afiliacion_tramos.parquet regenerada")

    elif accion == "todas":
        for sub in TODAS_LAS_TABLAS:
            print(f"\n{'#' * 80}\n# {sub.upper()}\n{'#' * 80}")
            ejecutar(sub, year, raw_root, parquet_folder, borrar_txt, provincias_csv, cnae_csv, paises_csv, causa_baja_csv)

    elif accion == "personas_extendida":
        ruta_personas_optimizada = os.path.join(parquet_folder, "tabla_personas_optimizada.parquet")
        if not os.path.exists(ruta_personas_optimizada):
            print("\nERROR: falta tabla_personas_optimizada.parquet. Pulsa primero el")
            print('  boton "Personas" para generarla, y luego repite este paso.')
            sys.exit(1)

        print("Bootstrap...")
        bootstrap_personas_extendida(con, parquet_folder)
        _borrar_optimizada(parquet_folder, "personas")

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

        # Copia de seguridad justo antes del Grupo H (2026-09-23): el H lee y reescribe encima
        # tabla_personas_extendida.parquet, asi que sin esta copia el estado previo se pierde y
        # cualquier ajuste del H obliga a repetir todos los grupos anteriores (~7 min). Con ella,
        # la accion "grupo_h" (abajo) solo repite el H. Es un Parquet intermedio con datos MCVL: no
        # se distribuye (mismo motivo que el resto de Parquet, ver *.parquet en .gitignore).
        shutil.copy2(_ruta_extendida(parquet_folder), _ruta_preh(parquet_folder))
        print(f"Copia previa al Grupo H guardada: {os.path.basename(_ruta_preh(parquet_folder))}")

        print("Grupo H (calculos finales, filtros, seleccion de columnas)...")
        df_final = grupo_h_final(parquet_folder, provincias_csv, cnae_csv, paises_csv, causa_baja_csv)
        print(f"tabla_personas_extendida.parquet lista: {df_final.shape}")

    elif accion == "grupo_h":
        # Solo el Grupo H, sobre la copia que dejo la ultima ejecucion completa de
        # "personas_extendida" (ver arriba). Restaura esa copia encima de la extendida y la
        # reprocesa: para probar cambios del H sin repetir los grupos A-G.
        if not os.path.exists(_ruta_preh(parquet_folder)):
            print(f"\nERROR: falta {os.path.basename(_ruta_preh(parquet_folder))}. Se crea en una ejecucion")
            print('  completa del boton final ("Construir tabla de personas extendida") con este codigo.')
            sys.exit(1)
        shutil.copy2(_ruta_preh(parquet_folder), _ruta_extendida(parquet_folder))
        print("Restaurada la copia previa al Grupo H")
        print("Grupo H (calculos finales, filtros, seleccion de columnas)...")
        df_final = grupo_h_final(parquet_folder, provincias_csv, cnae_csv, paises_csv, causa_baja_csv)
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
    parser.add_argument("--paises-csv", default=None)
    parser.add_argument("--causa-baja-csv", default=None)
    args = parser.parse_args()

    _metadata_dir = os.path.join(_RAIZ_PROYECTO, "etl", "metadata")
    provincias_csv = args.provincias_csv or os.path.join(_metadata_dir, "provincias_dict.csv")
    cnae_csv = args.cnae_csv or os.path.join(_metadata_dir, "cnae_3d_dict.csv")
    paises_csv = args.paises_csv or os.path.join(_metadata_dir, "paises_dict.csv")
    causa_baja_csv = args.causa_baja_csv or os.path.join(_metadata_dir, "causa_baja_dict.csv")

    ejecutar(args.accion, args.year, args.raw, args.parquet, args.borrar_txt, provincias_csv, cnae_csv, paises_csv, causa_baja_csv)
    print("\nHECHO", flush=True)
