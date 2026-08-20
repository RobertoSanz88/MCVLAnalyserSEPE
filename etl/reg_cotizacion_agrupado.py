"""Agrupacion de 02 REG_COTIZACION (Afiliacion) en las familias de regimen que
la propia guia de campos MCVL dice que se usan en las estadisticas oficiales
("clasificados segun el regimen de encuadramiento correspondiente a las dos
primeras posiciones de la clave").

Compartido entre etl/personas_extendida.py (Reg_cotiz_ppal_Agrupado, Grupo A)
y app/routers/trayectorias.py (trayectoria categorica de Afiliacion) para no
reimplementar la logica en 2 sitios - antes cada uno tenia su propia copia
(_clasificar_regimen en etl/, _SQL_CUBETA_REG_COTIZACION/_ETIQUETAS_REG_COTIZACION
en app/) verificadas a mano para que coincidieran, en vez de compartir una
unica fuente de verdad.

Reglas (decididas con el usuario, 2026-08-20, verificadas contra la guia y con
datos reales antes de aplicarlas):
  - Familia = 2 primeras posiciones del codigo de 4 digitos (011x/012x/013x/
    014x/015x/016x -> 01; 052x/053x/054x -> 05; 061x/064x -> 06; 072x/074x ->
    07; 081x-089x -> 08; 091x/094x -> 09; 121x-124x -> 12).
  - Excepcion: 0138 ("REGIMEN GENERAL S.E.C. EMPLEADOS HOGAR") se queda como
    categoria propia, aparte de "01 Regimen General" - decision explicita del
    usuario, coherente con la granularidad que ya tenia el ETL antes de esta
    sesion (_clasificar_regimen ya lo separaba).
  - 07 (R.E. Agrario Cuenta Propia) se integro en Autonomos (05) desde 2008
    segun la guia, pero se deja como categoria propia (no fusionada con 05)
    por fidelidad a la clasificacion oficial "por las dos primeras
    posiciones" - marcado explicitamente "(extinguido 2008)" en la etiqueta.
  - Cualquier codigo que no encaje en ninguna familia (no visto en datos
    reales, pero posible en futuras ediciones de la MCVL) -> "No consta".
"""

ETIQUETAS_AGRUPADO = {
    0: "01 Régimen General",
    1: "0138 Régimen General (S.E.C. Empleados de Hogar)",
    2: "05 R.E. Autónomos",
    3: "06 R.E. Agrario Cuenta Ajena",
    4: "07 R.E. Agrario Cuenta Propia (extinguido 2008)",
    5: "08 R.E. Mar",
    6: "09 R.E. Minería del Carbón",
    7: "12 R.E. Empleados de Hogar",
}


def sql_cubeta_agrupado(columna_codigo: str) -> str:
    """CASE SQL (DuckDB) que agrupa un codigo crudo de REG_COTIZACION en la
    cubeta final 0-7 (ver ETIQUETAS_AGRUPADO), u 8 para cualquier otro caso
    ("No consta"). Usa division entera ("//", no "/" - en DuckDB "/" entre dos
    enteros hace division real, verificado con datos reales: 1211/100=12.11)
    para extraer las dos primeras posiciones del codigo de 4 digitos."""
    return f"""
        CASE
            WHEN {columna_codigo} IS NULL THEN 8
            WHEN {columna_codigo} = 138 THEN 1
            WHEN ({columna_codigo} // 100) = 1 THEN 0
            WHEN ({columna_codigo} // 100) = 5 THEN 2
            WHEN ({columna_codigo} // 100) = 6 THEN 3
            WHEN ({columna_codigo} // 100) = 7 THEN 4
            WHEN ({columna_codigo} // 100) = 8 THEN 5
            WHEN ({columna_codigo} // 100) = 9 THEN 6
            WHEN ({columna_codigo} // 100) = 12 THEN 7
            ELSE 8
        END
    """


def decodificar_agrupado(cubeta) -> str:
    if cubeta is None:
        return "No consta"
    return ETIQUETAS_AGRUPADO.get(int(cubeta), "No consta")
