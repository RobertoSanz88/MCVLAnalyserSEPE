"""Construye una muestra pequena (las primeras N personas de cada tabla) a partir de una carpeta de Parquet real,
para probar el cambio de muestra sin copiar 1,5 GB. Uso: python tests/muestra_sintetica.py <origen> <destino> <n> <fecha_corte>"""
import sys
from pathlib import Path

import duckdb

TABLAS = [
    "tabla_personas_extendida.parquet", "tabla_afiliacion_extendida.parquet",
    "tabla_bases_cotizacion_CA_optimizada.parquet", "tabla_bases_cotizacion_CP_optimizada.parquet",
    "tabla_pensiones_extendida.parquet",
]


def crear_muestra_sintetica(origen, destino, n_personas: int, corte: str) -> Path:
    origen, destino = Path(origen), Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET threads=2")
    per = (origen / TABLAS[0]).as_posix()
    con.execute(f'CREATE TEMP TABLE ids AS SELECT "01 IPF" AS ipf FROM read_parquet(\'{per}\') ORDER BY 1 LIMIT {n_personas}')
    for f in TABLAS:
        con.execute(
            f"COPY (SELECT t.* FROM read_parquet('{(origen / f).as_posix()}') t "
            f'WHERE t."01 IPF" IN (SELECT ipf FROM ids)) TO \'{(destino / f).as_posix()}\' (FORMAT parquet)'
        )
    (destino / "fecha_corte.txt").write_text(corte + "\n", encoding="utf-8")
    return destino


if __name__ == "__main__":
    o, d, n, c = sys.argv[1:5]
    print(crear_muestra_sintetica(o, d, int(n), c))
