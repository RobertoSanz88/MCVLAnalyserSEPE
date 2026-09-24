"""Diagnostico de tabla_afiliacion_tramos.parquet (2026-09-24). NO modifica ningun fichero existente.

Contexto: la regla C-265 (duracion del primer empleo significativo <= tiempo de empleo) destapo ~83
personas, casi todas agrarias con muchos episodios, cuyos tiempos EMPLEO/MIXTO de
tabla_afiliacion_tramos.parquet salen muy por debajo de sus propios episodios de empleo. Recalculando la
fusion en aislamiento (una muestra de personas) el resultado si cuadra, y difiere del fichero en 82 de
esas 83 y en ~0,2 % de personas al azar. Este script comprueba si la fusion, ejecutada sobre la tabla
completa, es CORRECTA y DETERMINISTA en la maquina donde se lance:

  1. Vuelve a ejecutar fase1b_tramos_fusionados_duckdb con los mismos ajustes que el ETL
     (preserve_insertion_order=false, hilos segun la RAM) -> tabla_afiliacion_tramos_prueba_A.parquet
  2. La repite tal cual -> ..._prueba_A2.parquet (si A y A2 difieren, el calculo no es determinista)
  3. La ejecuta con preserve_insertion_order=true -> ..._prueba_B.parquet (el ajuste del ETL podria
     alterar el orden de los empates de fecha_alta en las ventanas)

Compara por persona los dias de EMPLEO/DESEMPLEO/MIXTO de cada prueba con el fichero actual y entre si, y
cuenta las personas cuyo episodio de empleo mas largo supera su EMPLEO+MIXTO (imposible por construccion:
todos los dias de un episodio de empleo estan en EMPLEO o en MIXTO). Solo imprime recuentos, ningun IPF.

    python etl/verificar_tramos.py --parquet "C:\\ruta\\a\\la\\carpeta de parquet"
    python etl/verificar_tramos.py --parquet <carpeta> --conservar    # no borra los ficheros de prueba

Necesita ~2 x 200 MB libres por prueba y varios minutos en total. Borra los ficheros de prueba al final
salvo con --conservar.
"""

import argparse
import os
import sys
import time

_RAIZ_PROYECTO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _RAIZ_PROYECTO not in sys.path:
    sys.path.insert(0, _RAIZ_PROYECTO)

import duckdb

from etl.personas_extendida import fase1b_tramos_fusionados_duckdb
from etl.recursos import ram_total_gb, threads_optimos

AFILIACION = "tabla_afiliacion_extendida.parquet"
TRAMOS = "tabla_afiliacion_tramos.parquet"
US_POR_DIA = 86_400_000_000

VARIANTES = [
    ("A", False, "como el ETL (preserve_insertion_order=false)"),
    ("A2", False, "repeticion de A"),
    ("B", True, "preserve_insertion_order=true"),
]


def _conexion(hilos, preservar_orden):
    con = duckdb.connect(":memory:")
    con.execute(f"SET preserve_insertion_order={'true' if preservar_orden else 'false'}")
    con.execute(f"SET threads={hilos}")
    return con


def _totales_sql(ruta):
    ruta = ruta.replace("\\", "/")
    return f"""
        SELECT ipf,
               SUM(CASE WHEN categoria = 'EMPLEO' THEN date_diff('day', fecha_inicio_tramo, fecha_fin_tramo) + 1 ELSE 0 END) AS e,
               SUM(CASE WHEN categoria = 'DESEMPLEO' THEN date_diff('day', fecha_inicio_tramo, fecha_fin_tramo) + 1 ELSE 0 END) AS d,
               SUM(CASE WHEN categoria = 'MIXTO' THEN date_diff('day', fecha_inicio_tramo, fecha_fin_tramo) + 1 ELSE 0 END) AS m
        FROM read_parquet('{ruta}') GROUP BY ipf
    """


def _comparar(con, nombre_a, ruta_a, nombre_b, ruta_b):
    fila = con.execute(f"""
        SELECT COUNT(*),
               SUM((COALESCE(a.e,0) <> COALESCE(b.e,0) OR COALESCE(a.d,0) <> COALESCE(b.d,0)
                    OR COALESCE(a.m,0) <> COALESCE(b.m,0))::INT),
               MAX(ABS(COALESCE(a.e,0) - COALESCE(b.e,0)))
        FROM ({_totales_sql(ruta_a)}) a FULL JOIN ({_totales_sql(ruta_b)}) b USING (ipf)
    """).fetchone()
    print(f"  {nombre_a} vs {nombre_b}: personas comparadas {fila[0]:,}, con tiempos distintos {int(fila[1] or 0):,}"
          f" (mayor diferencia de dias EMPLEO: {fila[2]})")


def _violaciones(con, afiliacion, ruta_tramos):
    """Personas cuyo episodio de empleo mas largo (dias) supera EMPLEO + MIXTO + 1 (imposible)."""
    af = afiliacion.replace("\\", "/")
    return con.execute(f"""
        SELECT COUNT(*) FROM (
            SELECT "01 IPF" AS ipf, MAX("DURACION_EMPLEO") // {US_POR_DIA} AS max_dias
            FROM read_parquet('{af}') WHERE "DURACION_EMPLEO" IS NOT NULL AND "DURACION_EMPLEO" <> 0
            GROUP BY 1
        ) ep LEFT JOIN ({_totales_sql(ruta_tramos)}) t USING (ipf)
        WHERE ep.max_dias > COALESCE(t.e, 0) + COALESCE(t.m, 0) + 1
    """).fetchone()[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--parquet", required=True, help="Carpeta con los Parquet (la que contiene " + AFILIACION + ")")
    parser.add_argument("--conservar", action="store_true", help="No borrar los ficheros de prueba al terminar")
    args = parser.parse_args()

    carpeta = args.parquet
    afiliacion = os.path.join(carpeta, AFILIACION)
    tramos = os.path.join(carpeta, TRAMOS)
    for f in (afiliacion, tramos):
        if not os.path.exists(f):
            print(f"ERROR: falta {f}")
            sys.exit(1)

    ram = ram_total_gb()
    hilos = threads_optimos(ram)
    print(f"RAM {ram:.1f} GB, hilos DuckDB {hilos}, DuckDB {duckdb.__version__}")

    pruebas = {}
    for nombre, preservar, descripcion in VARIANTES:
        salida = f"tabla_afiliacion_tramos_prueba_{nombre}.parquet"
        print(f"\nPrueba {nombre}: {descripcion} ...", flush=True)
        t0 = time.time()
        con = _conexion(hilos, preservar)
        fase1b_tramos_fusionados_duckdb(con, carpeta, output_name=salida)
        con.close()
        pruebas[nombre] = os.path.join(carpeta, salida)
        print(f"  hecha en {time.time() - t0:.0f} s")

    con = duckdb.connect(":memory:")
    con.execute(f"SET threads={hilos}")
    print("\n=== Comparacion por persona (dias de EMPLEO/DESEMPLEO/MIXTO) ===")
    _comparar(con, "fichero actual", tramos, "A", pruebas["A"])
    _comparar(con, "A", pruebas["A"], "A2 (determinismo)", pruebas["A2"])
    _comparar(con, "A", pruebas["A"], "B (orden de insercion)", pruebas["B"])
    _comparar(con, "fichero actual", tramos, "B", pruebas["B"])

    print("\n=== Personas con un episodio de empleo mas largo que su EMPLEO+MIXTO (deberia ser 0) ===")
    print(f"  fichero actual: {_violaciones(con, afiliacion, tramos):,}")
    for nombre in pruebas:
        print(f"  prueba {nombre}: {_violaciones(con, afiliacion, pruebas[nombre]):,}")

    if not args.conservar:
        for ruta in pruebas.values():
            os.remove(ruta)
        print("\nFicheros de prueba borrados.")
    print("\nHECHO")


if __name__ == "__main__":
    main()
