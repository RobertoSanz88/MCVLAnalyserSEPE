"""Correccion de claves sucesoras para 04 TIPO_CONTRATO (Afiliacion) y
agrupacion final en las 5 categorias conocidas + No consta + Otros.

Compartido entre etl/personas_extendida.py (Tipo_Contr_ppal_Agrupado,
Grupo A) y app/routers/trayectorias.py (trayectoria categorica de
Afiliacion) para no reimplementar la logica en 2 sitios. Sin dependencias
pesadas (solo csv/pathlib de la libreria estandar) para que app/ pueda
importarlo sin arrastrar pandas/numpy - ver CLAUDE.md sobre la separacion
etl//app/.

Fuente de los datos: etl/metadata/tipo_contrato_claves_sucesoras.csv
(tabla publica de codigos de la guia oficial de la MCVL, no dato
restringido - mismo criterio que provincias_dict.csv/cnae_3d_dict.csv).

Reglas (decididas explicitamente con el usuario, 2026-08-10, verificadas
con datos reales antes de aplicarlas):
  - Codigo deprecado (columna "deprecada"="B") con 1 sola clave sucesora
    -> se sustituye por ella directamente.
  - Codigo deprecado con 2 claves sucesoras (17 codigos; solo 014 y 015
    tienen peso real, ~1,5% cada uno) -> COEF_T_PARCIAL del episodio > 0
    -> la sucesora de tiempo PARCIAL (clave_sucesora_2), si no (0 o NULL)
    -> la de tiempo COMPLETO (clave_sucesora_1). El umbral es ">0", NO
    "<1": COEF_T_PARCIAL va en escala 0-999 (0 = no aplica/tiempo
    completo, >0 = jornada parcial, p.ej. 500 = 50%), no en escala 0-1 -
    verificado que 99%+ de las filas de 014/015 tienen COEF_T_PARCIAL=0.
  - Codigo deprecado SIN sucesora listada (~30 codigos, 1,09% del total
    en conjunto) -> se queda con su codigo crudo (no se corrige), que
    luego no encaja en ningun bin [100,600)/000/990 y pliega en 'Otros'.
"""

import csv
from pathlib import Path

_RUTA_CSV = Path(__file__).resolve().parent / "metadata" / "tipo_contrato_claves_sucesoras.csv"

ETIQUETAS_AGRUPADO = {
    0: "No consta",
    1: "Indefinido_t_completo",
    2: "Indefinido_t_parcial",
    3: "Indefinido_fijo_discontinuo",
    4: "Temporal_t_completo",
    5: "Temporal_t_parcial",
    6: "Otros",
}


def cargar_claves_sucesoras() -> dict[int, dict]:
    """codigo deprecado -> {"sucesora_1": int|None, "sucesora_2": int|None}."""
    claves = {}
    with open(_RUTA_CSV, encoding="utf-8") as f:
        for fila in csv.DictReader(f):
            if fila["deprecada"] != "B":
                continue
            suc1 = fila["clave_sucesora_1"].strip()
            suc2 = fila["clave_sucesora_2"].strip()
            claves[int(fila["clave"])] = {
                "sucesora_1": int(suc1) if suc1 else None,
                "sucesora_2": int(suc2) if suc2 else None,
            }
    return claves


def sql_corregido(columna_codigo: str, columna_coef: str) -> str:
    """CASE SQL (DuckDB) que sustituye codigos deprecados por su clave
    sucesora. Codigos sin entrada en el CSV o sin ninguna sucesora
    listada llegan al ELSE tal cual (codigo original)."""
    whens = []
    for codigo, info in cargar_claves_sucesoras().items():
        s1, s2 = info["sucesora_1"], info["sucesora_2"]
        if s1 is not None and s2 is not None:
            whens.append(
                f"WHEN {columna_codigo} = {codigo} "
                f"THEN CASE WHEN {columna_coef} > 0 THEN {s2} ELSE {s1} END"
            )
        elif s1 is not None:
            whens.append(f"WHEN {columna_codigo} = {codigo} THEN {s1}")
    return f"CASE {' '.join(whens)} ELSE {columna_codigo} END"


def sql_bin_corregido(columna_val: str) -> str:
    """CASE SQL que agrupa un codigo YA CORREGIDO (ver sql_corregido) en
    la cubeta final 0-6 (ver ETIQUETAS_AGRUPADO). No corrige claves
    sucesoras - para eso, sql_corregido primero."""
    return f"""
        CASE
            WHEN {columna_val} = 0 THEN 0
            WHEN {columna_val} >= 100 AND {columna_val} < 200 THEN 1
            WHEN {columna_val} >= 200 AND {columna_val} < 300 THEN 2
            WHEN {columna_val} >= 300 AND {columna_val} < 400 THEN 3
            WHEN {columna_val} >= 400 AND {columna_val} < 500 THEN 4
            WHEN {columna_val} >= 500 AND {columna_val} < 600 THEN 5
            ELSE 6
        END
    """


def sql_cubeta_agrupado(columna_codigo: str, columna_coef: str) -> str:
    """Composicion de sql_corregido + sql_bin_corregido: de codigo crudo a
    cubeta final 0-6 en un solo paso. Pensado para tablas grandes
    (Afiliacion, ~33M filas) donde conviene que la cubeta viaje como
    SMALLINT compacto en vez de texto durante la parte cara de una query -
    ver comentario en app/routers/trayectorias.py (VARIABLES_CATEGORICAS)."""
    corregido = f"({sql_corregido(columna_codigo, columna_coef)})"
    return sql_bin_corregido(corregido)


def decodificar_agrupado(cubeta) -> str:
    return ETIQUETAS_AGRUPADO.get(cubeta, "Otros")
