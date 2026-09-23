"""Optimizada -> Personas extendida. Transcripcion de
"2. Construccion de Tabla Personas Extendida.ipynb" (version con Baja_modificada en
Fecha_final_Ultimo_Empleo y edades en meses/12, la que ya coincide con el Parquet real actual).

Mismo patron de memoria que el cuaderno: cada funcion de fase/grupo carga solo lo que necesita,
calcula, y el llamador decide cuando liberar con del/gc.collect(). fusionar_grupo() encapsula el
patron repetido (cargar personas_extendida, fusionar extracciones por 01 IPF, guardar) que en el
cuaderno aparecia identico 7 veces.
"""

import gc
import os
import re

import numpy as np
import pandas as pd

from config import FECHA_CORTE as _FECHA_CORTE_ISO
from etl import reg_cotizacion_agrupado
from etl.tipo_contrato_claves_sucesoras import (
    decodificar_agrupado, sql_bin_corregido, sql_corregido, sql_cubeta_agrupado,
)

UN_DIA = pd.Timedelta(days=1)
CERO_DIAS = pd.Timedelta(days=0)

# Sustituye el placeholder 2099 (episodio de afiliacion todavia activo) de FECHA_BAJA_REAL_AFIL.
# Valor real en config.py (raiz del proyecto), no aqui - unica fuente para etl/ y app/.
FECHA_CORTE = pd.to_datetime(_FECHA_CORTE_ISO)


def fijar_fecha_corte(iso):
    """Cambia la fecha de corte de todo este modulo (2026-09-23): la calcula ejecutar_paso.py a
    partir de la casilla "Anyo" (1 de mayo del anyo siguiente, ver config.fecha_corte_para_anio).
    Las funciones leen estos globales al ejecutarse, no al importarse, asi que basta con
    llamarla antes de lanzar cualquier paso."""
    global _FECHA_CORTE_ISO, FECHA_CORTE
    _FECHA_CORTE_ISO = iso
    FECHA_CORTE = pd.to_datetime(iso)


def _ruta(parquet_folder, nombre_parquet):
    return os.path.join(parquet_folder, nombre_parquet)


# ---------------------------------------------------------------------------
# FASE 1 - Afiliacion extendida
# ---------------------------------------------------------------------------

def fase1_afiliacion_extendida(parquet_folder, output_name="tabla_afiliacion_extendida.parquet"):
    """Lee tabla_afiliacion_optimizada.parquet y anyade Baja_modificada, DURACION_EMPLEO,
    y DURACION_DESEMPLEO. Guarda el resultado en
    `output_name` dentro de parquet_folder (parametrizable para no pisar un fichero de
    referencia existente durante la verificacion) y devuelve el DataFrame."""
    df = pd.read_parquet(_ruta(parquet_folder, "tabla_afiliacion_optimizada.parquet"))

    # Baja_modificada y DURACION_EMPLEO
    df["Baja_modificada"] = df["07 FECHA_BAJA_REAL_AFIL"].copy()
    condicion_activo = df["07 FECHA_BAJA_REAL_AFIL"].dt.year == 2099
    df.loc[condicion_activo, "Baja_modificada"] = FECHA_CORTE

    df["DURACION_EMPLEO"] = df["Baja_modificada"] - df["06 FECHA_ALTA_REAL_AFIL"] + UN_DIA

    # Los episodios con TRL 700-799 son periodos de cobro de prestacion/subsidio, no de empleo
    condicion_trl_700 = (df["15 TRL"] >= 700) & (df["15 TRL"] < 800)
    # TRL 400: la guia de campos MCVL dice literalmente "Tienen alguna peculiaridad por la
    # que no se consideran alta laboral" - igual que 700-799, no cuenta como DURACION_EMPLEO.
    # No toca DURACION_DESEMPLEO (mas abajo): esa es especificamente prestacion/subsidio de
    # desempleo (700-799), 400 no encaja ahi y no se ha pedido tratarlo como tal.
    condicion_no_empleo = condicion_trl_700 | (df["15 TRL"] == 400)
    df.loc[condicion_no_empleo, "DURACION_EMPLEO"] = CERO_DIAS

    condicion_duracion_negativa = df["DURACION_EMPLEO"] < CERO_DIAS
    df.loc[condicion_duracion_negativa, "DURACION_EMPLEO"] = pd.NaT

    # DURACION_DESEMPLEO por simetria con DURACION_EMPLEO
    df["DURACION_DESEMPLEO"] = df["Baja_modificada"] - df["06 FECHA_ALTA_REAL_AFIL"] + UN_DIA
    df.loc[~condicion_trl_700, "DURACION_DESEMPLEO"] = CERO_DIAS
    condicion_desempleo_negativa = df["DURACION_DESEMPLEO"] < CERO_DIAS
    df.loc[condicion_desempleo_negativa, "DURACION_DESEMPLEO"] = pd.NaT

    df.to_parquet(_ruta(parquet_folder, output_name), index=False)
    return df


# pandas 3.0 usa microsegundos como resolucion de fecha por defecto (datetime64[us]), no
# nanosegundos como versiones anteriores -- los Timedelta de la tabla de referencia estan en
# microsegundos. Comprobado con datos reales: con nanosegundos salia un factor 1000 de mas.
US_POR_DIA = 86_400_000_000


def fase1_afiliacion_extendida_duckdb(con, parquet_folder, output_name="tabla_afiliacion_extendida.parquet"):
    """Igual que fase1_afiliacion_extendida() pero calculado con DuckDB en vez de pandas: para
    Afiliacion (33M filas) ni siquiera la version ya optimizada cabe entera en la RAM de esta
    maquina (comprobado: pyarrow.lib.ArrowMemoryError al cargarla completa en pandas).

    DIFERENCIA DELIBERADA frente al cuaderno (2026-09-21, decision explicita del usuario): ya no
    se genera ni Alta_desplazada ni DURACION_NO_EMPLEO. DURACION_NO_EMPLEO era el hueco entre el
    fin de un episodio y el alta siguiente por orden de fecha de alta, sin tener en cuenta los
    solapes (pluriempleo): el 11 % de lo que se contaba como "baja" eran solapes (hueco negativo)
    y el 57 % huecos de un mes o menos. T_total_NO_EMPLEO ya es un residuo (Grupo H) y
    num_episodios_baja pasa a contar bajas reales (ver extraer_grupo_c), asi que nada la usa.
    Como efecto, el Fase 1 ya no necesita las ventanas LEAD/ROW_NUMBER sobre 33M filas.
    """
    optimizada = _ruta(parquet_folder, "tabla_afiliacion_optimizada.parquet")
    output_path = _ruta(parquet_folder, output_name)

    con.execute("""
        CREATE OR REPLACE VIEW _fase1_base AS
        SELECT *,
            COALESCE("15 TRL" >= 700 AND "15 TRL" < 800, FALSE) AS _trl700,
            -- TRL 400: la guia de campos MCVL dice literalmente "Tienen alguna peculiaridad
            -- por la que no se consideran alta laboral" - igual que 700-799, no cuenta como
            -- DURACION_EMPLEO. No afecta a DURACION_DESEMPLEO (sigue solo _trl700 mas abajo):
            -- esa es especificamente prestacion/subsidio de desempleo, 400 no encaja ahi.
            COALESCE("15 TRL" >= 700 AND "15 TRL" < 800, FALSE) OR COALESCE("15 TRL" = 400, FALSE) AS _no_empleo,
            CASE WHEN date_part('year', "07 FECHA_BAJA_REAL_AFIL") = 2099
                 THEN TIMESTAMP '__FECHA_CORTE__'
                 ELSE "07 FECHA_BAJA_REAL_AFIL"
            END AS "Baja_modificada",
            -- Empleo_publico (2026-09-08, decision explicita del usuario, confirmada por
            -- AskUserQuestion tras dictado por voz ambiguo): letras de NIF del empleador que
            -- identifican sector publico - P (Corporacion local), Q (Organismo publico), S
            -- (Organo de la Administracion del Estado/CCAA). "SI"/"NO" (no booleano), mismo
            -- criterio de texto ya usado en este fichero para Cobra_pension_*.
            CASE WHEN UPPER("18 EMPLEADOR_LETRA_NIF") IN ('P', 'Q', 'S') THEN 'SI' ELSE 'NO' END
                AS "Empleo_publico"
        FROM read_parquet('{}')
    """.replace("__FECHA_CORTE__", _FECHA_CORTE_ISO).format(optimizada))

    con.execute(f"""
        CREATE OR REPLACE VIEW _fase1_duraciones AS
        SELECT *,
            (date_diff('day', "06 FECHA_ALTA_REAL_AFIL", "Baja_modificada") + 1) AS _dias_bruto,
            CASE WHEN _no_empleo THEN 0
                 WHEN (date_diff('day', "06 FECHA_ALTA_REAL_AFIL", "Baja_modificada") + 1) < 0 THEN NULL
                 ELSE (date_diff('day', "06 FECHA_ALTA_REAL_AFIL", "Baja_modificada") + 1) * {US_POR_DIA}
            END AS "DURACION_EMPLEO",
            CASE WHEN NOT _trl700 THEN 0
                 WHEN (date_diff('day', "06 FECHA_ALTA_REAL_AFIL", "Baja_modificada") + 1) < 0 THEN NULL
                 ELSE (date_diff('day', "06 FECHA_ALTA_REAL_AFIL", "Baja_modificada") + 1) * {US_POR_DIA}
            END AS "DURACION_DESEMPLEO"
        FROM _fase1_base
    """)

    con.execute(f"""
        COPY (
            SELECT * EXCLUDE (_trl700, _no_empleo, _dias_bruto)
            FROM _fase1_duraciones
        ) TO '{output_path}' (FORMAT PARQUET)
    """)
    return output_path


def fase1b_tramos_fusionados_duckdb(
    con, parquet_folder, afiliacion_name="tabla_afiliacion_extendida.parquet",
    output_name="tabla_afiliacion_tramos.parquet",
):
    """PASO 2 (2026-09-08, decision explicita del usuario): fusiona episodios de Afiliacion
    solapados en tramos de calendario sin solape, para poder sumar tiempo de vida laboral sin
    contar dos veces el mismo dia. Problema que resuelve: DURACION_EMPLEO/DESEMPLEO/NO_EMPLEO se
    sumaban hasta ahora por episodio bruto, sin comprobar solapes entre episodios de la misma
    persona - pluriempleo (dos trabajos con fechas solapadas) o solape empleo-desempleo (un
    episodio de trabajo y uno de prestacion/subsidio con fechas que se pisan) sobrecontaban
    tiempo, y eso hacia salir DURACION_NO_EMPLEO negativa en un subconjunto de personas.

    Arquitectura (decidida con el usuario via AskUserQuestion, 2026-09-08):
    tabla_afiliacion_extendida.parquet NO se toca - sigue una fila por episodio bruto con sus
    columnas originales (TIPO_CONTRATO, REG_COTIZACION, CCC_2...), que Grupo A/B/D necesitan
    intactas para su logica de "episodio/regimen ganador" por duracion (un tramo fusionado a
    partir de 2 episodios con distinto TIPO_CONTRATO no tiene un TIPO_CONTRATO valido propio).
    Los tramos fusionados viven en esta tabla NUEVA y ligera, separada: solo
    ipf/fecha_inicio_tramo/fecha_fin_tramo/categoria (EMPLEO/DESEMPLEO/MIXTO - NO_EMPLEO no se
    materializa aqui, se calcula en Grupo C/Paso 3 como residuo: tiempo_total - EMPLEO -
    DESEMPLEO - MIXTO, no como suma de huecos).

    Usa la MISMA clasificacion TRL que ya existe en Fase 1 para DURACION_EMPLEO/DURACION_DESEMPLEO
    (700-799 -> DESEMPLEO, TRL 400 excluido de ambas categorias - "peculiaridad... no se
    considera alta laboral" pero tampoco es prestacion de desempleo -, todo lo demas -> EMPLEO):
    no redefine que TRL cuenta como que, solo corrige como se suman los tramos con solape.

    Parte A (fusion de episodios de la MISMA categoria, ej. pluriempleo): por persona y
    categoria, ordenados por fecha_alta, cada episodio se compara contra el MAXIMO de fecha_baja
    visto hasta el momento (no solo el episodio anterior), para fusionar tambien cadenas de
    solapes (A solapa B, B solapa C, pero C no solapa A directamente -> los 3 se fusionan igual).
    Parte B (barrido de eventos entre categorias): con los tramos ya fusionados de EMPLEO y de
    DESEMPLEO por separado, se generan eventos (+1 al empezar el tramo, -1 al dia siguiente de
    terminarlo) y se recorren cronologicamente por persona, sumando un contador por categoria.
    Entre dos eventos consecutivos, el tramo de calendario se clasifica segun que contadores
    esten activos: solo empleo -> EMPLEO, solo desempleo -> DESEMPLEO, ambos -> MIXTO, ninguno ->
    NO_EMPLEO (no se guarda esa fila).
    """
    afiliacion = _ruta(parquet_folder, afiliacion_name)
    output_path = _ruta(parquet_folder, output_name)

    con.execute(f"""
        CREATE OR REPLACE VIEW _tramos_episodios AS
        SELECT "01 IPF" AS ipf,
            CASE WHEN COALESCE("15 TRL" >= 700 AND "15 TRL" < 800, FALSE) THEN 'DESEMPLEO'
                 ELSE 'EMPLEO' END AS categoria,
            "06 FECHA_ALTA_REAL_AFIL" AS fecha_alta,
            "Baja_modificada" AS fecha_baja
        FROM read_parquet('{afiliacion}')
        -- TRL 400 fuera de la fusion: ni empleo ni desempleo (misma peculiaridad que en Fase 1)
        WHERE NOT COALESCE("15 TRL" = 400, FALSE)
          -- episodios con duracion bruta negativa (dato erroneo, ver DURACION_EMPLEO en Fase 1)
          -- no son un intervalo de calendario valido, se excluyen de la fusion
          AND "Baja_modificada" >= "06 FECHA_ALTA_REAL_AFIL"
    """)

    con.execute("""
        CREATE OR REPLACE VIEW _tramos_max_anterior AS
        SELECT *,
            MAX(fecha_baja) OVER (
                PARTITION BY ipf, categoria ORDER BY fecha_alta
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
            ) AS _max_baja_anterior
        FROM _tramos_episodios
    """)

    # nuevo_grupo si hay un hueco real (>1 dia) respecto al maximo de fecha_baja visto hasta
    # ahora - un hueco de 0 o 1 dia significa que los episodios se solapan o se tocan (el dia de
    # baja cuenta como "en alta", misma convencion que el resto del fichero), asi que se fusionan
    con.execute("""
        CREATE OR REPLACE VIEW _tramos_grupos AS
        SELECT *,
            SUM(CASE
                    WHEN _max_baja_anterior IS NULL
                         OR date_diff('day', _max_baja_anterior, fecha_alta) > 1
                    THEN 1 ELSE 0
                END) OVER (
                PARTITION BY ipf, categoria ORDER BY fecha_alta
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS _grupo_id
        FROM _tramos_max_anterior
    """)

    con.execute("""
        CREATE OR REPLACE VIEW _tramos_fusionados AS
        SELECT ipf, categoria,
            MIN(fecha_alta) AS tramo_alta,
            MAX(fecha_baja) AS tramo_baja
        FROM _tramos_grupos
        GROUP BY ipf, categoria, _grupo_id
    """)

    # Parte B: eventos (+1 al empezar el tramo fusionado, -1 al dia siguiente de terminarlo),
    # agrupados por instante (ipf, ts) antes de acumular - dos eventos en el mismo instante
    # (p.ej. un tramo EMPLEO que termina el mismo dia en que empieza uno DESEMPLEO) deben
    # aplicarse juntos, no en un orden arbitrario entre filas de la union.
    con.execute("""
        CREATE OR REPLACE VIEW _eventos AS
        SELECT ipf, categoria, tramo_alta AS ts, 1 AS delta FROM _tramos_fusionados
        UNION ALL
        SELECT ipf, categoria, tramo_baja + INTERVAL 1 DAY AS ts, -1 AS delta FROM _tramos_fusionados
    """)

    con.execute("""
        CREATE OR REPLACE VIEW _eventos_agrupados AS
        SELECT ipf, ts,
            SUM(CASE WHEN categoria = 'EMPLEO' THEN delta ELSE 0 END) AS delta_empleo,
            SUM(CASE WHEN categoria = 'DESEMPLEO' THEN delta ELSE 0 END) AS delta_desempleo
        FROM _eventos
        GROUP BY ipf, ts
    """)

    con.execute("""
        CREATE OR REPLACE VIEW _eventos_acumulados AS
        SELECT ipf, ts,
            SUM(delta_empleo) OVER (
                PARTITION BY ipf ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS empleo_activo,
            SUM(delta_desempleo) OVER (
                PARTITION BY ipf ORDER BY ts ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS desempleo_activo
        FROM _eventos_agrupados
    """)

    # El tramo de calendario entre un evento y el siguiente (LEAD) se clasifica por los
    # contadores vigentes desde este evento. El ultimo evento de cada persona no tiene
    # "siguiente" (LEAD NULL, fecha_fin_tramo sale NULL) - se descarta junto con los tramos
    # NO_EMPLEO (categoria NULL): por construccion, tras el ultimo evento ambos contadores
    # vuelven a 0 (cada tramo fusionado aporta un +1 y un -1), asi que ese resto ya es NO_EMPLEO
    # y no se materializa aqui (se calcula en Paso 3 como residuo, no como suma de huecos).
    con.execute(f"""
        COPY (
            SELECT ipf, fecha_inicio_tramo, fecha_fin_tramo, categoria
            FROM (
                SELECT ipf,
                    ts AS fecha_inicio_tramo,
                    LEAD(ts) OVER (PARTITION BY ipf ORDER BY ts) - INTERVAL 1 DAY AS fecha_fin_tramo,
                    CASE WHEN empleo_activo > 0 AND desempleo_activo > 0 THEN 'MIXTO'
                         WHEN empleo_activo > 0 THEN 'EMPLEO'
                         WHEN desempleo_activo > 0 THEN 'DESEMPLEO'
                         ELSE NULL END AS categoria
                FROM _eventos_acumulados
            )
            WHERE fecha_fin_tramo IS NOT NULL AND categoria IS NOT NULL
        ) TO '{output_path}' (FORMAT PARQUET)
    """)
    return output_path


def _sql_categoria_ppal_fusionada(afiliacion, categoria_expr, filtro_extra="TRUE"):
    """BUG corregido 2026-09-22 (decision explicita del usuario, verificado con datos reales):
    las columnas t_tot_reg_ppal, t_tot_grupo_ppal, t_tot_tipo_contrato_ppal, t_tot_prov_ppal y
    t_tot_act_econ_ppal sumaban DURACION_EMPLEO por episodio bruto agrupado por categoria - sin
    fusionar episodios solapados/contiguos de la MISMA categoria de una misma persona (pluriempleo
    simultaneo en el mismo regimen/grupo/tipo de contrato/provincia/actividad se contaba dos
    veces). Mismo fallo que ya se corrigio para T_total_EMPLEO en el Paso 2
    (fase1b_tramos_fusionados_duckdb) al pasar a sumar sobre tramos fusionados en vez de episodios
    brutos - aqui se aplica el mismo criterio de fusion ("hueco > 1 dia" entre fecha_baja y la
    siguiente fecha_alta), pero particionado por (ipf, categoria) en vez de por (ipf, EMPLEO/
    DESEMPLEO/MIXTO). Verificado con datos reales antes de la correccion: 49.420 personas con
    t_tot_prov_ppal mayor que su tiempo total de vida laboral, exceso medio 6,8 anyos.

    Devuelve el SQL (WITH ... hasta la CTE 'ranked', sin ejecutar) con una fila por (ipf, val,
    total) YA quedandose solo con la categoria ganadora de cada persona (rn=1 dentro de
    'ranked' - el propio ranked trae 'total' ordenado DESC por ipf, la fila con rn=1 es la
    ganadora). Cada llamada añade su propio SELECT final (decodificando 'val' si hace falta) para
    reusar la misma fusion en las 5 columnas sin repetir el algoritmo 5 veces.

    categoria_expr: expresion SQL (sobre las columnas crudas de afiliacion) que da el valor de
    categoria de cada episodio (p.ej. la provincia, el regimen ya agrupado en su cubeta...).
    filtro_extra: condicion adicional en el WHERE inicial (p.ej. rango valido del grupo de
    cotizacion) - por defecto TRUE (sin filtro adicional). Los episodios sin empleo real
    (DURACION_EMPLEO nula o 0, ver Fase 1) y con categoria NULL quedan fuera siempre."""
    return f"""
        WITH extraido AS (
            SELECT "01 IPF" AS ipf, ({categoria_expr}) AS val,
                   "06 FECHA_ALTA_REAL_AFIL" AS fecha_alta, "Baja_modificada" AS fecha_baja
            FROM read_parquet('{afiliacion}')
            WHERE "DURACION_EMPLEO" IS NOT NULL AND "DURACION_EMPLEO" != 0
              AND ({filtro_extra})
              AND "Baja_modificada" >= "06 FECHA_ALTA_REAL_AFIL"
        ), _extraido_ok AS (
            SELECT * FROM extraido WHERE val IS NOT NULL
        ), _max_anterior AS (
            SELECT *, MAX(fecha_baja) OVER (
                PARTITION BY ipf, val ORDER BY fecha_alta
                ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
            ) AS _max_baja_ant
            FROM _extraido_ok
        ), _grupos AS (
            SELECT *, SUM(CASE
                    WHEN _max_baja_ant IS NULL OR date_diff('day', _max_baja_ant, fecha_alta) > 1
                    THEN 1 ELSE 0 END) OVER (
                PARTITION BY ipf, val ORDER BY fecha_alta
                ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS _grupo_id
            FROM _max_anterior
        ), _tramos AS (
            SELECT ipf, val, MIN(fecha_alta) AS _ta, MAX(fecha_baja) AS _tb
            FROM _grupos GROUP BY ipf, val, _grupo_id
        ), sumado AS (
            SELECT ipf, val, SUM((date_diff('day', _ta, _tb) + 1) * {US_POR_DIA}) AS total
            FROM _tramos GROUP BY ipf, val
        ), ranked AS (
            SELECT ipf, val, total,
                   ROW_NUMBER() OVER (PARTITION BY ipf ORDER BY total DESC, val ASC) AS rn
            FROM sumado
        )
    """


def fusionar_grupo(parquet_folder, extracciones, post_fillna=None):
    """Carga tabla_personas_extendida.parquet, fusiona (left join por 01 IPF) cada extraccion de
    la lista, aplica post_fillna(df) si se da, guarda y devuelve (shape_antes, shape_despues)."""
    output_path = _ruta(parquet_folder, "tabla_personas_extendida.parquet")
    df = pd.read_parquet(output_path)
    shape_antes = df.shape

    for extraccion in extracciones:
        df = pd.merge(df, extraccion, on="01 IPF", how="left")

    if post_fillna is not None:
        post_fillna(df)

    df.to_parquet(output_path, index=False)
    shape_despues = df.shape

    del df
    gc.collect()
    return shape_antes, shape_despues


# ---------------------------------------------------------------------------
# FASE 3 - Bootstrap
# ---------------------------------------------------------------------------

def bootstrap_personas_extendida(con, parquet_folder):
    """Copia tabla_personas_optimizada.parquet como punto de partida de
    tabla_personas_extendida.parquet (igual que el cuaderno)."""
    origen = _ruta(parquet_folder, "tabla_personas_optimizada.parquet")
    destino = _ruta(parquet_folder, "tabla_personas_extendida.parquet")
    con.execute(f"COPY (SELECT * FROM read_parquet('{origen}')) TO '{destino}' (FORMAT PARQUET)")
    return destino


# ---------------------------------------------------------------------------
# GRUPO A - Afiliacion extendida: regimen/grupo/contrato principal
# ---------------------------------------------------------------------------

def extraer_grupo_a(con, parquet_folder):
    """Devuelve las 4 extracciones del Grupo A como DataFrames pandas (pequenyos,
    a lo sumo un IPF por fila). Calculado con DuckDB sobre afiliacion_extendida
    (33M filas) para no cargarla en pandas."""
    afiliacion = _ruta(parquet_folder, "tabla_afiliacion_extendida.parquet")

    # Reg_cotiz_ppal_Agrupado / T_tot_reg_ppal: familia de regimen (ver
    # etl/reg_cotizacion_agrupado.py) con mas DURACION_EMPLEO acumulada por
    # persona. Agrupa A NIVEL DE EPISODIO, ANTES de sumar por (ipf, familia) -
    # mismo motivo que Tipo_Contr_ppal_Agrupado (ver mas abajo): si se
    # agrupara DESPUES de elegir el codigo crudo ganador, dos codigos de la
    # misma familia (p.ej. 111 y 140, ambos Regimen General) competirian como
    # 2 codigos distintos por la duracion de esa persona en vez de sumar
    # juntos - verificado con datos reales que esto pasaba de verdad: 6.571
    # personas cambiaban de familia ganadora, y otras 227.130 se quedaban en
    # la misma familia pero con una duracion acumulada incorrecta.
    cubeta_reg = reg_cotizacion_agrupado.sql_cubeta_agrupado('"02 REG_COTIZACION"')
    df_reg = con.execute(_sql_categoria_ppal_fusionada(afiliacion, cubeta_reg) + """
        SELECT ipf AS "01 IPF", val AS cubeta, total AS "T_tot_reg_ppal"
        FROM ranked WHERE rn = 1
    """).fetchdf()
    df_reg["Reg_cotiz_ppal_Agrupado"] = df_reg["cubeta"].map(reg_cotizacion_agrupado.decodificar_agrupado)
    df_reg = df_reg.drop(columns=["cubeta"])

    # Grupo_cotiz_ppal / t_tot_grupo_ppal: grupo de cotizacion con mas duracion acumulada.
    # La guia de campos MCVL solo documenta claves 00-12 para este campo, y dice literalmente
    # que "en determinados regimenes como el R.E. de Autonomos... no se asigna grupo de
    # cotizacion" y que "valores distintos de los que figuran en la tabla deben considerarse
    # como casos en que el dato no consta". Verificado con datos reales: claves fuera de 00-12
    # (13, 14, 19, 20, 21, 23, 31, 63 en la muestra actual) aparecen casi todas en regimenes de
    # cuenta propia/autonomos (0721 R.E. Agrario Cuenta Propia, o ligadas a TRL 751/752 de
    # prestacion de desempleo) - exactamente el caso que describe la guia, no una tabla de
    # categorias real sin documentar. Se excluyen del ranking (like NULL) en vez de listar los
    # codigos concretos vistos hoy, para cubrir tambien claves fuera de rango que aparezcan en
    # futuras ediciones de la MCVL sin tener que tocar este codigo otra vez.
    df_grupo = con.execute(_sql_categoria_ppal_fusionada(
        afiliacion, '"03 GRU_COTIZACION"', '"03 GRU_COTIZACION" BETWEEN 0 AND 12'
    ) + """
        SELECT ipf AS "01 IPF", val AS "Grupo_cotiz_ppal", total AS "t_tot_grupo_ppal"
        FROM ranked WHERE rn = 1
    """).fetchdf()

    # t_contrato_mas_largo: episodio individual mas largo de cada persona.
    # Causa_Baja_Trab_Ppal (2026-09-08, decision explicita del usuario, confirmada por
    # AskUserQuestion): "08 CAUSA_BAJA_AFIL" de ESE MISMO episodio (el de empleo mas largo) -
    # codigo crudo, sin decodificar ni agrupar (no hay tabla de claves para este campo en el
    # proyecto, a diferencia de TIPO_CONTRATO). Misma ranking que t_contrato_mas_largo, para
    # garantizar que las dos columnas se refieren siempre al mismo episodio.
    df_maslargo = con.execute(f"""
        WITH validos AS (
            SELECT "01 IPF" AS ipf, "DURACION_EMPLEO" AS dur, "08 CAUSA_BAJA_AFIL" AS causa_baja
            FROM read_parquet('{afiliacion}') WHERE "DURACION_EMPLEO" IS NOT NULL
        ), ranked AS (
            SELECT ipf, dur, causa_baja, ROW_NUMBER() OVER (PARTITION BY ipf ORDER BY dur DESC) AS rn
            FROM validos
        )
        SELECT ipf AS "01 IPF", dur AS "t_contrato_mas_largo", causa_baja AS "Causa_Baja_Trab_Ppal"
        FROM ranked WHERE rn = 1
    """).fetchdf()

    # Tipo_Contr_ppal_Agrupado (nombre acortado 2026-09-08, decision explicita del usuario:
    # "Contrato" -> "Contr", igual abreviatura que tipo_contr_ult_baja) / T_tot_tipo_contrato_ppal.
    # Corrige claves sucesoras de 04 TIPO_CONTRATO A NIVEL DE EPISODIO, antes
    # de sumar DURACION_EMPLEO por (ipf, codigo) - no despues de decidir el
    # contrato principal. Importa por que: si se corrigiera solo el codigo ya
    # ganador, un codigo deprecado (p.ej. "001", sucesora 100) y su propio
    # sucesor "100" competirian como 2 codigos distintos por la duracion
    # acumulada de esa persona, en vez de sumar juntos como el mismo tipo de
    # contrato real - podia hacer ganar a un tercer codigo con menos duracion
    # real. Logica de correccion importada de
    # etl/tipo_contrato_claves_sucesoras.py (compartida con la trayectoria
    # categorica de Afiliacion en app/, no reimplementada aqui).
    val_corregido = sql_corregido('"04 TIPO_CONTRATO"', '"05 COEF_T_PARCIAL"')
    cubeta_ganador = sql_bin_corregido("val")
    df_tipo = con.execute(_sql_categoria_ppal_fusionada(
        afiliacion, val_corregido, '"04 TIPO_CONTRATO" IS NOT NULL'
    ) + f"""
        SELECT ipf AS "01 IPF", ({cubeta_ganador}) AS cubeta, total AS "T_tot_tipo_contrato_ppal"
        FROM ranked WHERE rn = 1
    """).fetchdf()
    # Cubeta (int compacto, ver sql_bin_corregido) -> etiqueta final, igual
    # patron ya usado en este mismo Grupo H para GRUPOS_COTIZ_DICT: 'No
    # consta' (000) y 'Otros' (990 + huerfanos sin sucesora) son categorias
    # explicitas aqui, no NaN - a diferencia de la version anterior (ELSE
    # NULL), que dejaba esos casos como NaN silencioso.
    df_tipo["Tipo_Contr_ppal_Agrupado"] = df_tipo["cubeta"].map(decodificar_agrupado)
    df_tipo = df_tipo.drop(columns=["cubeta"])

    # DuckDB devuelve las duraciones como enteros (microsegundos, ver Fase 1), no como
    # Timedelta de pandas. Se convierten aqui para que la conversion automatica a anyos del
    # Grupo H (que detecta columnas Timedelta) las siga cogiendo igual que en el cuaderno.
    df_reg["T_tot_reg_ppal"] = pd.to_timedelta(df_reg["T_tot_reg_ppal"], unit="us")
    df_grupo["t_tot_grupo_ppal"] = pd.to_timedelta(df_grupo["t_tot_grupo_ppal"], unit="us")
    df_maslargo["t_contrato_mas_largo"] = pd.to_timedelta(df_maslargo["t_contrato_mas_largo"], unit="us")
    df_tipo["T_tot_tipo_contrato_ppal"] = pd.to_timedelta(df_tipo["T_tot_tipo_contrato_ppal"], unit="us")

    return [df_reg, df_grupo, df_maslargo, df_tipo]


# ---------------------------------------------------------------------------
# GRUPO B - Afiliacion extendida: primer/ultimo empleo
# ---------------------------------------------------------------------------

# Tipos de contrato excluidos al buscar el primer empleo "significativo" (parciales,
# practicas, formacion...) - lista original identica al cuaderno, ampliada el
# 2026-08-20 tras verificar codigo a codigo contra la tabla completa de TIPO_CONTRATO
# de la guia MCVL (existian huecos reales, no solo teoricos: afecta a 14.049 personas
# verificado con datos reales).
TIPOS_CONTRATO_A_ELIMINAR = (
    3, 4, 6, 23, 24, 25, 29, 34, 63, 81, 89, 95, 98, 200, 209, 230, 231, 239, 241, 250, 251, 252,
    253, 254, 255, 256, 257, 289, 500, 501, 502, 503, 508, 510, 518, 520, 530, 531, 540, 541, 550,
    551, 552, 557, 26, 36, 46, 58, 420, 7, 27, 37, 47, 85, 97, 404, 421,
    # Formacion/practicas/aprendizaje: familia "FORM.RD.1992/84"/"FOR.RD.1992.MIN"
    # (variantes por edad/FP/fecha), 087 (APRENDIZAJE. DURACION DETERMINADA, sucesora
    # 421 que si estaba en la lista original) y 096 (PRACTICAS discapacidad,
    # sucesora 420, tambien ya en la lista) - confirmados literalmente en la guia,
    # faltaban en el listado original.
    53, 54, 55, 56, 57, 66, 67, 68, 77, 78, 79, 87, 96,
    # Tiempo parcial identificado por "T.PAR"/"T.P." en la denominacion de la guia:
    # familia INTER.SUST.EXC (sustitucion por excedencia familiar/maternidad, variantes
    # a tiempo parcial de 1/2/3 anyos) y otros sueltos (038, 064/065, 102, 186) que
    # tambien faltaban.
    38, 64, 65, 73, 74, 76, 83, 84, 93, 94, 102, 186,
    # Codigos deprecados cuya clave sucesora se bifurca en completo/parcial (005->441
    # /541, 009->130/230, 016->410/510, 017->100/200, 049->100/200, 059->130/230,
    # 069/070/071->100/200) - mismo criterio de precaucion que ya se aplicaba al 029
    # (misma bifurcacion, ya en la lista original): al no poder saber si un episodio
    # historico concreto era completo o parcial, se excluyen por consistencia con el
    # 029 (decision explicita del usuario, 2026-08-20).
    5, 9, 16, 17, 49, 59, 69, 70, 71,
)
DURACION_MINIMA_EMPLEO_US = 270 * 86_400_000_000  # 270 dias en microsegundos


def extraer_grupo_b(con, parquet_folder):
    afiliacion = _ruta(parquet_folder, "tabla_afiliacion_extendida.parquet")
    tipos_sql = ", ".join(str(t) for t in TIPOS_CONTRATO_A_ELIMINAR)

    # Grupo_Ultimo_Empleo: grupo de cotizacion del episodio de baja mas reciente.
    # Desempate por fecha_alta DESC: el cuaderno ordena solo por (IPF, fecha_baja) y toma el
    # "last" de un sort estable -  con muchos episodios empatados a fecha_baja=2099 (placeholder
    # de "sigue activo"), el orden entre empates se hereda del orden previo del fichero, que ya
    # esta ordenado por fecha_alta ascendente (de la Fase 1) - "last" equivale a la fecha_alta
    # mas reciente entre los empatados. Verificado contra el parquet de referencia: sin este
    # desempate fallaban 28.123 personas, con el solo quedan 2.717 (empates exactos en ambas
    # fechas alta Y baja, un caso genuinamente ambiguo que no se puede desempatar sin conocer el
    # orden exacto del fichero original).
    # causa_ult_baja / tipo_contr_ult_baja / act_econ_ult_baja (2026-09-08, decision explicita del
    # usuario, confirmada por AskUserQuestion): "08 CAUSA_BAJA_AFIL" / "04 TIPO_CONTRATO" /
    # "12 CNAE2009_CC" de ese MISMO episodio (misma ranking que Grupo_Ultimo_Empleo, para
    # garantizar que las 4 columnas se refieren siempre al mismo episodio).
    # causa_ult_baja / act_econ_ult_baja: codigos crudos, sin decodificar ni agrupar, igual criterio
    # que Causa_Baja_Trab_Ppal.
    # tipo_contr_ult_baja: corregido tras deteccion de error en la interfaz (2026-09-08) - salia
    # como codigo crudo de TIPO_CONTRATO en vez de con la misma agrupacion que
    # Tipo_Contr_ppal_Agrupado, inconsistente en el selector de filtros categoricos. Usa
    # sql_cubeta_agrupado (correccion de claves sucesoras + agrupacion en las 7 categorias de
    # ETIQUETAS_AGRUPADO), igual pipeline que Tipo_Contr_ppal_Agrupado en Grupo A - aqui se
    # aplica a UN SOLO episodio (el de la ultima baja), no a la suma acumulada de todos.
    tipo_ult_cubeta_sql = sql_cubeta_agrupado('"04 TIPO_CONTRATO"', '"05 COEF_T_PARCIAL"')
    df_ultimogrupo = con.execute(f"""
        WITH filtrado AS (
            SELECT "01 IPF" AS ipf, "03 GRU_COTIZACION" AS val, "07 FECHA_BAJA_REAL_AFIL" AS fecha_baja,
                   "06 FECHA_ALTA_REAL_AFIL" AS fecha_alta, "08 CAUSA_BAJA_AFIL" AS causa_baja,
                   ({tipo_ult_cubeta_sql}) AS tipo_contr_cubeta, "12 CNAE2009_CC" AS cnae,
                   "15 TRL" AS trl
            FROM read_parquet('{afiliacion}') WHERE "07 FECHA_BAJA_REAL_AFIL" IS NOT NULL
        ), ranked AS (
            SELECT ipf, val, causa_baja, tipo_contr_cubeta, cnae,
                   ROW_NUMBER() OVER (PARTITION BY ipf ORDER BY fecha_baja DESC, fecha_alta DESC,
                                    {_orden_desempate_ultimo_episodio("trl", "causa_baja")},
                                    val ASC, tipo_contr_cubeta ASC, cnae ASC) AS rn
            FROM filtrado
        )
        SELECT ipf AS "01 IPF", val AS "Grupo_Ultimo_Empleo",
               causa_baja AS "causa_ult_baja", tipo_contr_cubeta AS "_tipo_contr_ult_baja_cubeta",
               cnae AS "act_econ_ult_baja"
        FROM ranked WHERE rn = 1
    """).fetchdf()
    df_ultimogrupo["tipo_contr_ult_baja"] = df_ultimogrupo["_tipo_contr_ult_baja_cubeta"].map(decodificar_agrupado)
    df_ultimogrupo = df_ultimogrupo.drop(columns=["_tipo_contr_ult_baja_cubeta"])

    # Fecha_ini_Primer_Empleo sobrevive a la tabla final (ver NUEVO_ORDEN); Fecha_final_Ultimo_Empleo
    # sigue siendo intermedia (se necesita en el Grupo H para Edad_Ultimo_Empleo, pero no se
    # expone como columna propia).
    # Las dos excluyen episodios de TRL 700-799/400 (prestacion/subsidio/otras peculiaridades sin
    # alta laboral, ver DURACION_EMPLEO) - mismo criterio en ambas, decision explicita del usuario.
    # Fecha_ini_Primer_Empleo corregida 2026-08-20 (commit 84e1ace): sin el filtro, si el episodio
    # MAS ANTIGUO de una persona era de ese tipo (10.027 personas, 0,76%), la fecha de "primer
    # empleo" no era de empleo real. Fecha_final_Ultimo_Empleo corregida el mismo dia: sin el
    # filtro, para 173.291 personas (13,2%, verificado con datos reales - la mayoria cobrando
    # actualmente del SEPE) el episodio MAS RECIENTE era de prestacion/subsidio, asi que
    # Edad_Ultimo_Empleo mostraba su edad actual (o la fecha en que dejo de cobrar) en vez de
    # cuando dejo de TRABAJAR. NULL para quien nunca ha tenido un episodio de empleo real (mismo
    # caso en las dos columnas).
    # Fecha_inicio_vida_laboral_total (2026-09-08, decision explicita del usuario): igual que
    # Fecha_ini_Primer_Empleo pero SIN el filtro de TRL 700-799/400 - aqui interesa el primer
    # episodio de CUALQUIER tipo (empleo o desempleo), es la base de
    # Tiempo_total_desde_inicio_VL en el Grupo H (sustituye a
    # Anos_VL_excluye_desempleo_inicio_y_fin, que salia corta para el mismo subconjunto de
    # personas descrito arriba). No sobrevive a la tabla final (no esta en NUEVO_ORDEN).
    df_fechas = con.execute(f"""
        SELECT "01 IPF" AS "01 IPF",
               MIN("06 FECHA_ALTA_REAL_AFIL") FILTER (
                   WHERE NOT (
                       COALESCE("15 TRL" >= 700 AND "15 TRL" < 800, FALSE) OR COALESCE("15 TRL" = 400, FALSE)
                   )
               ) AS "Fecha_ini_Primer_Empleo",
               MAX("Baja_modificada") FILTER (
                   WHERE NOT (
                       COALESCE("15 TRL" >= 700 AND "15 TRL" < 800, FALSE) OR COALESCE("15 TRL" = 400, FALSE)
                   )
               ) AS "Fecha_final_Ultimo_Empleo",
               MIN("06 FECHA_ALTA_REAL_AFIL") AS "Fecha_inicio_vida_laboral_total"
        FROM read_parquet('{afiliacion}') GROUP BY 1
    """).fetchdf()

    # Fecha_1er_Empleo_Signif / Duracion_1er_Empleo_Signif / Grupo_cotiz_1er_Empleo_Signif:
    # primer episodio (por fecha de alta) que no sea de los tipos excluidos y dure >= 270 dias.
    # Desempate (2026-08-20, pospuesto varias sesiones): sin el, 5.371 personas tenian mas de
    # un episodio candidato con la misma fecha_alta minima, y el resultado de DuckDB no estaba
    # garantizado que fuera estable. Igual criterio que Grupo_Ultimo_Empleo (mas arriba): fecha
    # primero, luego duracion DESC (el episodio "significativo" mas largo entre los empatados,
    # coherente con que la variable ya exige duracion minima), y CCC_PPAL como ultimo criterio
    # deterministico (mismo patron que el "episodio dominante" de NUM_TRABAJS_CC_2 en
    # app/routers/trayectorias.py). Tras los 3 niveles quedan 550 personas genuinamente
    # ambiguas (empatan tambien en duracion y CCC_PPAL) - residual aceptado, mismo espiritu que
    # los 2.717 casos irresolubles de Grupo_Ultimo_Empleo.
    df_signif = con.execute(f"""
        WITH filtrado AS (
            SELECT "01 IPF" AS ipf, "03 GRU_COTIZACION" AS grupo,
                   "06 FECHA_ALTA_REAL_AFIL" AS fecha_alta, "DURACION_EMPLEO" AS dur,
                   "20 CCC_PPAL" AS ccc_ppal
            FROM read_parquet('{afiliacion}')
            WHERE "04 TIPO_CONTRATO" NOT IN ({tipos_sql})
              AND "DURACION_EMPLEO" >= {DURACION_MINIMA_EMPLEO_US}
        ), ranked AS (
            SELECT ipf, grupo, fecha_alta, dur,
                   ROW_NUMBER() OVER (
                       PARTITION BY ipf ORDER BY fecha_alta ASC, dur DESC, ccc_ppal ASC
                   ) AS rn
            FROM filtrado
        )
        SELECT ipf AS "01 IPF", fecha_alta AS "Fecha_1er_Empleo_Signif",
               dur AS "Duracion_1er_Empleo_Signif", grupo AS "Grupo_cotiz_1er_Empleo_Signif"
        FROM ranked WHERE rn = 1
    """).fetchdf()

    df_signif["Duracion_1er_Empleo_Signif"] = pd.to_timedelta(
        df_signif["Duracion_1er_Empleo_Signif"], unit="us"
    )

    df_1ersignif = df_signif[["01 IPF", "Fecha_1er_Empleo_Signif", "Duracion_1er_Empleo_Signif"]]
    df_grupo1ersignif = df_signif[["01 IPF", "Grupo_cotiz_1er_Empleo_Signif"]]

    return [df_ultimogrupo, df_fechas, df_1ersignif, df_grupo1ersignif]


# ---------------------------------------------------------------------------
# GRUPO C - Afiliacion extendida: duraciones, provincia y actividad
# ---------------------------------------------------------------------------

def extraer_grupo_c(con, parquet_folder):
    afiliacion = _ruta(parquet_folder, "tabla_afiliacion_extendida.parquet")
    tramos = _ruta(parquet_folder, "tabla_afiliacion_tramos.parquet")

    # num_episodios_alta / duracion_promedio_EMPLEO (excluye episodios de duracion 0 o NULL)
    df_promedioalta = con.execute(f"""
        SELECT "01 IPF" AS "01 IPF", COUNT(*) AS num_episodios_alta,
               AVG("DURACION_EMPLEO") AS duracion_promedio_EMPLEO
        FROM read_parquet('{afiliacion}')
        WHERE "DURACION_EMPLEO" != 0 AND "DURACION_EMPLEO" IS NOT NULL
        GROUP BY 1
    """).fetchdf()

    # num_episodios_baja (2026-09-21, decision explicita del usuario, opcion A): bajas REALES =
    # los mismos episodios de empleo que cuentan como alta (DURACION_EMPLEO != 0 y no nula: sin
    # TRL 700-799 ni 400) cuya fecha de baja real no es el placeholder 2099 (episodio aun activo).
    # Antes se contaban los episodios con DURACION_NO_EMPLEO != 0 (hueco hasta el alta siguiente
    # ignorando solapes): incluia solapes de pluriempleo (11 %) y huecos de dias dentro de otro
    # empleo. Asi bajas <= altas siempre. Hace falta para num_bajas_anual_promedio en el Grupo H.
    df_promediobaja = con.execute(f"""
        SELECT "01 IPF" AS "01 IPF", COUNT(*) AS num_episodios_baja
        FROM read_parquet('{afiliacion}')
        WHERE "DURACION_EMPLEO" != 0 AND "DURACION_EMPLEO" IS NOT NULL
          AND date_part('year', "07 FECHA_BAJA_REAL_AFIL") <> 2099
        GROUP BY 1
    """).fetchdf()

    # T_total_EMPLEO / T_total_DESEMPLEO / T_total_MIXTO_EMPL_DESEMPL (PASO 3, 2026-09-08,
    # decision explicita del usuario): ya NO se suma por episodio bruto (sobrecontaba tiempo en
    # pluriempleo y en solapes empleo-desempleo, ver Paso 2) - se suma sobre
    # tabla_afiliacion_tramos.parquet, que ya trae los tramos fusionados/sin solape por
    # categoria. T_total_NO_EMPLEO ya no se calcula aqui: pasa a ser un residuo en Grupo H
    # (Tiempo_total_desde_inicio_VL - EMPLEO - DESEMPLEO - MIXTO), no una suma directa
    # (no sobrevive a la tabla final, igual que antes).
    # COALESCE(..., 0): FILTER sobre una persona sin ningun tramo de esa categoria (ej. nunca
    # cobro del SEPE -> 0 tramos DESEMPLEO/MIXTO) da SUM NULL, no 0 - distinto del comportamiento
    # anterior (0 por episodio, sumaba a 0). 0 es el valor correcto aqui (no hubo tiempo en esa
    # categoria, no "se desconoce"), igual convencion que la version por episodio.
    df_totales = con.execute(f"""
        SELECT ipf AS "01 IPF",
               COALESCE(SUM(date_diff('day', fecha_inicio_tramo, fecha_fin_tramo) + 1)
                   FILTER (WHERE categoria = 'EMPLEO'), 0) * {US_POR_DIA} AS "T_total_EMPLEO",
               COALESCE(SUM(date_diff('day', fecha_inicio_tramo, fecha_fin_tramo) + 1)
                   FILTER (WHERE categoria = 'DESEMPLEO'), 0) * {US_POR_DIA} AS "T_total_DESEMPLEO",
               COALESCE(SUM(date_diff('day', fecha_inicio_tramo, fecha_fin_tramo) + 1)
                   FILTER (WHERE categoria = 'MIXTO'), 0) * {US_POR_DIA} AS "T_total_MIXTO_EMPL_DESEMPL"
        FROM read_parquet('{tramos}') GROUP BY 1
    """).fetchdf()

    # Prov_trabajo_ppal / t_tot_prov_ppal: digitos 5-6 del CCC_2, con mas duracion acumulada
    prov_expr = """CASE WHEN LENGTH("10 CCC_2") >= 6
                        THEN TRY_CAST(SUBSTRING("10 CCC_2", 5, 2) AS TINYINT)
                        ELSE NULL END"""
    df_prov = con.execute(_sql_categoria_ppal_fusionada(afiliacion, prov_expr) + """
        SELECT ipf AS "01 IPF", val AS "Prov_trabajo_ppal", total AS "t_tot_prov_ppal"
        FROM ranked WHERE rn = 1
    """).fetchdf()

    # Act_econ_ppal / t_tot_act_econ_ppal: CNAE (excluye 0: autonomos/desconocido)
    df_actecon = con.execute(_sql_categoria_ppal_fusionada(
        afiliacion, '"12 CNAE2009_CC"', '"12 CNAE2009_CC" != 0'
    ) + """
        SELECT ipf AS "01 IPF", val AS "Act_econ_ppal", total AS "t_tot_act_econ_ppal"
        FROM ranked WHERE rn = 1
    """).fetchdf()

    for df, col in [
        (df_promedioalta, "duracion_promedio_EMPLEO"),
        (df_totales, "T_total_EMPLEO"),
        (df_prov, "t_tot_prov_ppal"),
        (df_actecon, "t_tot_act_econ_ppal"),
    ]:
        df[col] = pd.to_timedelta(df[col], unit="us")
    df_totales["T_total_DESEMPLEO"] = pd.to_timedelta(df_totales["T_total_DESEMPLEO"], unit="us")
    df_totales["T_total_MIXTO_EMPL_DESEMPL"] = pd.to_timedelta(
        df_totales["T_total_MIXTO_EMPL_DESEMPL"], unit="us"
    )

    return [df_promedioalta, df_promediobaja, df_totales, df_prov, df_actecon]


# ---------------------------------------------------------------------------
# GRUPO D - Afiliacion extendida: situacion de afiliacion y paro
# ---------------------------------------------------------------------------

# TRL de prestacion/subsidio del SEPE (cobro actual). NO incluye el 400 (corregido
# 2026-08-20, por consistencia con Situacion_actual_afiliacion): la guia solo dice de
# el "alguna peculiaridad por la que no se consideran alta laboral", sin confirmar que
# sea cobro de SEPE - Situacion_actual_afiliacion ya lo trata aparte, en Asimilado_otros,
# no junto a la prestacion/subsidio real (751-756). Afecta a 1.875 personas (verificado
# con datos reales): 625 pierden fecha_inicio_paro (su unico TRL "de cobro" era el 400)
# y 1.250 cambian de fecha (el 400 formaba parte de su racha sin ser el unico motivo).
TRL_COBRO_SEPE = (751, 752, 753, 754, 755, 756)


def _orden_desempate_ultimo_episodio(trl='"15 TRL"', causa='"08 CAUSA_BAJA_AFIL"'):
    """Desempate (2026-09-21, decision explicita del usuario) cuando dos episodios de una persona
    empatan en fecha de baja Y de alta al elegir "el ultimo episodio": gana el de prestacion o
    subsidio (TRL 751-756), y despues TRL ASC y causa de baja ASC para que el resultado no dependa
    del orden fisico del fichero. Sin esto (solo baja DESC, alta DESC) ganaba una fila arbitraria
    en ~9.900 personas: situacion, racha sin empleo y causa/contrato/actividad del ultimo episodio
    dependian de como saliera ordenado el Parquet de Afiliacion. Se anyade tras "alta DESC"."""
    cobro = ", ".join(str(t) for t in TRL_COBRO_SEPE)
    return f"({trl} IN ({cobro})) DESC, {trl} ASC, {causa} ASC"


def extraer_grupo_d(con, parquet_folder):
    afiliacion = _ruta(parquet_folder, "tabla_afiliacion_extendida.parquet")

    # Discap_afiliacion: discapacidad detectada en cualquier episodio de la persona.
    # 130 (no 133 - typo corregido 2026-08-20: "133" no existe en la tabla de TIPO_CONTRATO
    # de la guia, es el codigo real "INDEF. TIEMPO COMPLETO. DISCAPACITADOS", verificado
    # que 0 filas reales tienen 133 y 9.994 filas/6.038 personas tienen 130 - de esas, 752
    # personas no se detectaban como discapacitadas por ningun otro camino).
    df_discap = con.execute(f"""
        SELECT DISTINCT "01 IPF" AS "01 IPF", TRUE AS "Discap_afiliacion"
        FROM read_parquet('{afiliacion}')
        WHERE "04 TIPO_CONTRATO" IN (130, 139, 230, 239, 330, 339, 430, 530)
           OR "09 MINUSV_ALTA_AFIL" > 0
    """).fetchdf()

    # Situacion_actual_afiliacion: clasificacion a partir del episodio mas reciente (por
    # fecha de baja, y de alta como desempate). Para "aun activo" (fecha_baja=2099), el
    # cobro de prestacion/subsidio se separa en 2 categorias (corregido 2026-08-20 a
    # peticion del usuario: antes juntaba los 6 codigos bajo un nombre que sugeria
    # "solo subsidio de mayores de 52/55" sin serlo) - "Asimilado_al_alta_subs52" es
    # especificamente el subsidio >52/55 anyos o fijos discontinuos (TRL 753,754, ver
    # tabla de TRL de la guia), "Asimilado_al_alta_prest_subs" el resto de
    # prestacion/subsidio (751,752,755,756). Para episodios ya finalizados, la
    # distincion previa Baja_de_prestacion (751,752) / Baja_de_subsidio (753-756) no
    # se toca - no se pidio cambiarla.
    df_situacion = con.execute(f"""
        WITH ranked AS (
            SELECT "01 IPF" AS ipf, "07 FECHA_BAJA_REAL_AFIL" AS fecha_baja,
                   "08 CAUSA_BAJA_AFIL" AS causa, "15 TRL" AS trl,
                   ROW_NUMBER() OVER (
                       PARTITION BY "01 IPF"
                       ORDER BY "07 FECHA_BAJA_REAL_AFIL" DESC, "06 FECHA_ALTA_REAL_AFIL" DESC,
                                {_orden_desempate_ultimo_episodio()}
                   ) AS rn
            FROM read_parquet('{afiliacion}')
        )
        SELECT ipf AS "01 IPF",
            CASE
                WHEN date_part('year', fecha_baja) = 2099 THEN
                    CASE WHEN trl IN (753, 754) THEN 'Asimilado_al_alta_subs52'
                         WHEN trl IN (751, 752, 755, 756) THEN 'Asimilado_al_alta_prest_subs'
                         WHEN trl IN (980, 400) THEN 'Asimilado_otros'
                         ELSE 'Alta_ocupado' END
                WHEN causa = 58 THEN 'Baja_para_pension'
                WHEN trl IN (751, 752) THEN 'Baja_de_prestacion'
                WHEN trl IN (753, 754, 755, 756) THEN 'Baja_de_subsidio'
                WHEN trl IN (980, 400) THEN 'Asimilado_otros'
                ELSE 'Baja_de_actividad'
            END AS "Situacion_actual_afiliacion"
        FROM ranked WHERE rn = 1
    """).fetchdf()

    # fecha_ultima_baja: fecha de baja del episodio mas reciente (por fecha de baja
    # unicamente - no hace falta desempate de fecha_alta, a diferencia de
    # Grupo_Ultimo_Empleo/Situacion_actual_afiliacion: aqui solo se devuelve la propia
    # fecha_baja, y dos episodios empatados en esa fecha dan el mismo valor sea cual sea
    # el que "gane" el desempate)
    df_ultimabaja = con.execute(f"""
        WITH ranked AS (
            SELECT "01 IPF" AS ipf, "07 FECHA_BAJA_REAL_AFIL" AS fecha_baja,
                   ROW_NUMBER() OVER (PARTITION BY "01 IPF" ORDER BY "07 FECHA_BAJA_REAL_AFIL" DESC) AS rn
            FROM read_parquet('{afiliacion}')
        )
        SELECT ipf AS "01 IPF", fecha_baja AS fecha_ultima_baja FROM ranked WHERE rn = 1
    """).fetchdf()

    # Fecha_inicio_ultima_racha_sin_empleo (nombre 2026-08-20; antes
    # "Cobrando_del_SEPE_desde_esta_fecha", y antes de eso "fecha_inicio_paro"). Concepto
    # aclarado por el usuario tras una falsa alarma (ver mas abajo): fecha de inicio de la
    # ULTIMA racha continua de episodios con TRL de prestacion/subsidio, exista o no
    # todavia esa racha a fecha de corte. Es decir: si la persona cobra del SEPE ahora
    # mismo, es desde cuando empezo esa racha activa; si ya no cobra (su ultima
    # prestacion/subsidio esta cerrada y no ha vuelto a trabajar desde entonces, categorias
    # Baja_de_prestacion/Baja_de_subsidio en Situacion_actual_afiliacion), sigue siendo la
    # fecha de inicio de esa ultima racha - no se pone a NULL solo porque el episodio ya
    # haya terminado. Se replica el bucle del cuaderno (que examina hasta 5 episodios y va
    # quitando pares consecutivos que coinciden) con un AND acumulativo por ventana: la racha
    # se corta en el primer episodio (de los 5 mas recientes) que no tenga TRL de cobro.
    # NULL para quien no tiene ningun episodio de cobro entre los 5 mas recientes.
    # Falsa alarma 2026-08-20 (commit 839d80a, revertido aqui): se penso que esto era un bug
    # porque de las 172.644 personas con valor, solo 40.606 (23,5%) cobraban de verdad ahora
    # mismo y el resto (132.038) tenia su ultima prestacion/subsidio ya cerrada, algunas desde
    # hace mas de 20 anyos - se anadio una condicion "aun_activo" para devolver NULL en esos
    # 132.038 casos. El usuario aclaro que ese comportamiento SI es el diseno original
    # (necesario para calcular duracion_actual_sin_empleo en los casos
    # Baja_de_prestacion/Baja_de_subsidio, ver grupo_h_final) y que el problema era solo que
    # el nombre de la variable (en presente, "cobrando... desde esta fecha") no dejaba claro
    # que tambien aplica a rachas ya cerradas - de ahi el renombrado a
    # "Fecha_inicio_ultima_racha_sin_empleo", que si es neutro respecto a si la racha sigue
    # abierta o no. Decision explicita del usuario de no rellenar los NULL con un texto tipo
    # "No cobra del SEPE en la actualidad" en el propio dato (mezclaria fecha y texto en la
    # misma columna, forzando pandas a "object" y rompiendo o degradando la escritura a
    # Parquet) - ese texto, si se quiere, es cosa del frontend.
    # BUG corregido 2026-09-22 (decision explicita del usuario): el TRL de "coincide" solo
    # cubria TRL_COBRO_SEPE (751-756), pero usa_cobrando_sepe en grupo_h_final tambien incluye
    # Asimilado_otros (TRL 980/400) - su propio comentario dice "en todas ellas
    # Fecha_inicio_ultima_racha_sin_empleo tiene valor", pero para Asimilado_otros casi nunca lo
    # tenia: de las personas Asimilado_otros clasificadas como Parado, 265 de 266 salian con
    # racha NULL (verificado con datos reales: el episodio mas reciente de 255 de ellas es TRL
    # 400, que no coincidia con ningun valor del filtro). Se anyade 980 y 400 al conjunto.
    trl_sql = ", ".join(str(t) for t in TRL_COBRO_SEPE + (980, 400))
    df_fechainicioparo = con.execute(f"""
        WITH ranked AS (
            SELECT "01 IPF" AS ipf, "06 FECHA_ALTA_REAL_AFIL" AS fecha_alta,
                   "07 FECHA_BAJA_REAL_AFIL" AS fecha_baja,
                   "15 TRL" IN ({trl_sql}) AS coincide,
                   ROW_NUMBER() OVER (
                       PARTITION BY "01 IPF"
                       ORDER BY "07 FECHA_BAJA_REAL_AFIL" DESC, "06 FECHA_ALTA_REAL_AFIL" DESC,
                                {_orden_desempate_ultimo_episodio()}
                   ) AS rn
            FROM read_parquet('{afiliacion}')
        ), top5 AS (
            SELECT * FROM ranked WHERE rn <= 5
        ), acumulado AS (
            SELECT *, bool_and(coincide) OVER (
                PARTITION BY ipf ORDER BY rn ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
            ) AS racha
            FROM top5
        ), elegido AS (
            SELECT ipf, fecha_alta,
                   ROW_NUMBER() OVER (PARTITION BY ipf ORDER BY rn DESC) AS rn2
            FROM acumulado WHERE racha
        )
        SELECT ipf AS "01 IPF", fecha_alta AS "Fecha_inicio_ultima_racha_sin_empleo"
        FROM elegido WHERE rn2 = 1
    """).fetchdf()

    # causa_baja_mas_comun / causas_baja_distintas (2026-09-08, decision explicita del usuario,
    # confirmada por AskUserQuestion): moda y conteo de codigos distintos de "08 CAUSA_BAJA_AFIL"
    # entre TODOS los episodios de la persona. Desempate de la moda por num de episodios DESC,
    # codigo ASC (mismo patron "total DESC, val ASC" ya usado en el resto del fichero para
    # desempates deterministas). causa_baja_mas_comun sin decodificar, igual criterio que
    # Causa_Baja_Trab_Ppal/causa_ult_baja.
    df_causabaja = con.execute(f"""
        WITH conteo AS (
            SELECT "01 IPF" AS ipf, "08 CAUSA_BAJA_AFIL" AS causa, COUNT(*) AS n
            FROM read_parquet('{afiliacion}') GROUP BY 1, 2
        ), ranked AS (
            SELECT ipf, causa, n,
                   ROW_NUMBER() OVER (PARTITION BY ipf ORDER BY n DESC, causa ASC) AS rn
            FROM conteo
        ), distintas AS (
            SELECT ipf, COUNT(*) AS causas_baja_distintas FROM conteo GROUP BY 1
        )
        SELECT r.ipf AS "01 IPF", r.causa AS "causa_baja_mas_comun", d.causas_baja_distintas
        FROM ranked r JOIN distintas d ON r.ipf = d.ipf
        WHERE r.rn = 1
    """).fetchdf()

    return [df_discap, df_situacion, df_ultimabaja, df_fechainicioparo, df_causabaja]


def fusionar_grupo_d(parquet_folder, con):
    extracciones = extraer_grupo_d(con, parquet_folder)

    def post_fillna(df):
        df["Situacion_actual_afiliacion"] = df["Situacion_actual_afiliacion"].fillna("Nunca_ha_cotizado")
        df["Discap_afiliacion"] = df["Discap_afiliacion"].fillna(False).astype(bool)

    return fusionar_grupo(parquet_folder, extracciones, post_fillna)


# ---------------------------------------------------------------------------
# FASE 2 - Pensiones extendida
# ---------------------------------------------------------------------------

def _categorizar_tipo_pension(valor):
    valor_str = str(valor).strip()
    try:
        valor_num = int(valor_str)
        if 10 <= valor_num <= 19:
            return "Incapacidad"
        elif 20 <= valor_num <= 26:
            return "Jubilación"
        elif 30 <= valor_num <= 32:
            return "Viudedad"
        elif 40 <= valor_num <= 42:
            return "Orfandad"
        elif 51 <= valor_num <= 54:
            return "Pension_a_favor_familiares"
    except ValueError:
        pass

    if re.match(r"^J\d$", valor_str):
        return "Incapacidad>65"
    if valor_str == "2E":
        return "Jubilación"
    return "Otros"


def fase2_pensiones_extendida(parquet_folder):
    """Lee tabla_pensiones_optimizada.parquet, anyade Tipo_pension e
    Ingresos_pension_bruto_anual, guarda tabla_pensiones_extendida.parquet. Devuelve el
    DataFrame resultante (el llamador decide si lo libera)."""
    df = pd.read_parquet(_ruta(parquet_folder, "tabla_pensiones_optimizada.parquet"))

    df["Tipo_pension"] = df["04 CLASE_PRESTACION"].apply(_categorizar_tipo_pension)
    df["Tipo_pension"] = df["Tipo_pension"].astype("category")

    df["Ingresos_pension_bruto_anual"] = np.where(
        df["04 CLASE_PRESTACION"] == 17,
        df["20 IMPORTE_MES_TOTAL_PRESTACION"] * 12,
        df["20 IMPORTE_MES_TOTAL_PRESTACION"] * 14,
    )

    df.to_parquet(_ruta(parquet_folder, "tabla_pensiones_extendida.parquet"), index=False)
    return df


# ---------------------------------------------------------------------------
# GRUPO E - Extracciones desde Pensiones extendida
# ---------------------------------------------------------------------------

def extraer_grupo_e(df_pensiones, anio_datos):
    """A partir de tabla_pensiones_extendida (en memoria), calcula las 4 extracciones del
    Grupo E. Devuelve (lista_de_extracciones, nombre_columna_cobra_pension)."""
    df = df_pensiones

    # Pension_actual / Cobra_pension_{anio}: filtra por el ANYO DE REFERENCIA real
    # (anio_datos, p.ej. 2024 - el mismo que ya usa el nombre de Cobra_pension_2024/5),
    # no por el maximo ANO_DATO propio de cada persona (bug real corregido 2026-08-20).
    # La guia (campo "4.002 ANYO DEL DATO") dice que cada pension tiene una fila por cada
    # anyo natural en que SI se percibio - el maximo propio de una persona puede ser un
    # anyo antiguo si dejo de cobrar hace tiempo, y con el filtro viejo esa persona salia
    # igualmente marcada "Cobra_pension_2024/5=SI" y con esa pension antigua como
    # "Pension_actual", aunque llevara anyos sin cobrar nada. Verificado con datos reales:
    # 19.288 personas (de 408.670 en Pensiones) tenian su ultimo ANO_DATO anterior a 2024.
    # ">=" en vez de "==" para no descartar datos mas recientes que el anyo de referencia
    # si los hubiera (2.523 personas con ANO_DATO=2025 en los datos reales).
    df_ultimo = df[df["02 ANO_DATO"] >= anio_datos]

    # .unique() (2026-09-22, decision explicita del usuario, bug real corregido): sin
    # deduplicar, una persona con el MISMO tipo de pension en varias filas de df_ultimo
    # (p.ej. ANO_DATO=2024 y ANO_DATO=2025, ambas "Jubilacion") salia con ese tipo repetido
    # dentro de la propia lista (["Jubilacion", "Jubilacion"]) en vez de una lista de tipos
    # DISTINTOS - mismo criterio que ya se aplicaba en Pension_todas_cobradas (ver abajo),
    # que aqui se habia quedado sin aplicar. Verificado con datos reales: 3.823 personas
    # afectadas (p.ej. ipf 683: ["Jubilacion", "Jubilacion"]).
    df_estudio_pensionactual = df_ultimo.groupby("01 IPF")["Tipo_pension"].apply(lambda s: list(s.unique())).reset_index()
    df_estudio_pensionactual.columns = ["01 IPF", "Pension_actual"]

    nombre_col_cobra_pension = f"Cobra_pension_{anio_datos}/{str(anio_datos + 1)[-1]}"
    df_estudio_pensionactual[nombre_col_cobra_pension] = "SI"

    # Tipos DISTINTOS de pension cobrados alguna vez (no una entrada por cada anyo de
    # historico) - decision explicita del usuario, 2026-08-20: sin deduplicar, la media
    # real es ~14 filas/anyos por persona pero solo ~1,2 tipos distintos, asi que salia
    # una lista con el mismo tipo repetido muchas veces (ej. 10x "Jubilacion" para
    # alguien que la cobro 10 anyos seguidos). Series.unique() preserva el orden de
    # primera aparicion (no ordena alfabeticamente).
    df_estudio_pensiontodas = df.groupby("01 IPF")["Tipo_pension"].apply(lambda s: list(s.unique())).reset_index()
    df_estudio_pensiontodas.columns = ["01 IPF", "Pension_todas_cobradas"]

    df_por_anio = df[["01 IPF", "02 ANO_DATO", "Ingresos_pension_bruto_anual"]].groupby(
        ["01 IPF", "02 ANO_DATO"]
    ).sum().reset_index()
    df_estudio_ingresospensionhist = df_por_anio.groupby("01 IPF")["Ingresos_pension_bruto_anual"].apply(list).reset_index()
    df_estudio_ingresospensionhist.columns = ["01 IPF", "Ingresos_pension_bruto_historico"]

    cond1 = df["09 CLASE_MINIMO"].isin([5, 6])
    cond2 = df["28 TIPO_SIT_JUBILACION"] == 17
    cond = cond1 | cond2
    df_estudio_discap_pension = df.loc[cond, ["01 IPF"]].drop_duplicates().copy()
    df_estudio_discap_pension["Discap_pension"] = True

    extracciones = [
        df_estudio_pensionactual, df_estudio_pensiontodas,
        df_estudio_ingresospensionhist, df_estudio_discap_pension,
    ]
    return extracciones, nombre_col_cobra_pension


def fusionar_grupo_e(parquet_folder, df_pensiones, anio_datos):
    extracciones, nombre_col_cobra_pension = extraer_grupo_e(df_pensiones, anio_datos)

    def post_fillna(df):
        df[nombre_col_cobra_pension] = df[nombre_col_cobra_pension].fillna("NO")
        df["Discap_pension"] = df["Discap_pension"].fillna(False).astype(bool)

    return fusionar_grupo(parquet_folder, extracciones, post_fillna), nombre_col_cobra_pension


# ---------------------------------------------------------------------------
# GRUPO F - Extracciones desde Datos fiscales
# ---------------------------------------------------------------------------

# Tipos de percepcion del modelo 190 (v2.2, decision explicita del usuario). "05 CLAVE_PERCEPCION" es
# una letra que identifica el tipo de ingreso y "06 SUBCLAVE_PERCEPCION" un codigo de 2 digitos que
# lo detalla (la subclave sola no significa nada: 01 es una pension en la clave B, una actividad
# profesional en la G y unas dietas en la L). Claves que llegan a la MCVL: A trabajo por cuenta
# ajena, B pensiones, C prestaciones/subsidios de desempleo, F cursos y obras, G actividades
# profesionales, H actividades agrarias y de estimacion objetiva, L rentas exentas. El resto (E
# consejeros, I, J, K premios, pagos unicos de desempleo...) la MCVL las excluye (guia, Tabla 6).
CLAVES_PERCEPCION_PREVISTAS = ["A", "B", "C", "F", "G", "H", "L"]
# Pensiones exentas de IRPF que van en clave L pero son pensiones: L06 incapacidad permanente
# absoluta y gran invalidez, L07 incapacidad de Clases Pasivas, L08 pensiones de orfandad y
# prestaciones familiares exentas (art. 7.h de la Ley del IRPF).
TIPOS_PENSION_EXENTA = ["L06", "L07", "L08"]
_CLAVE_PERCEPCION_CSV = os.path.join(os.path.dirname(os.path.abspath(__file__)), "metadata", "clave_percepcion_dict.csv")


def extraer_grupo_f(parquet_folder, anio_datos):
    """Lee tabla_datos_fiscales_optimizada.parquet y calcula las extracciones del Grupo F
    (ingresos por tipo, retenciones, discapacidad, provincia y tipo de percepcion principal).
    Devuelve (lista_de_extracciones, nombre_columna_ingresos_brutos)."""
    df = pd.read_parquet(_ruta(parquet_folder, "tabla_datos_fiscales_optimizada.parquet"))

    # /100: el campo fiscal viene en centimos (convencion habitual de la Agencia
    # Tributaria/SEPE), ni el cuaderno ni la version anterior de este script lo
    # dividian - decision explicita del usuario tras detectar el error (medias de
    # ~2,2M € sin sentido en vez de ~22.000 €).
    # Suma "07 PERC_INTEGRA_DIN_NO_DERIVADA_IL" + "08 PERC_INTEGRA_DIN_DERIVADA_IL"
    # (corregido 2026-08-20, decision explicita del usuario): la guia (Tabla 6, Datos
    # Fiscales) describe el campo 07 como "Percepcion integra dineraria NO derivada de
    # IL" (IL = Incapacidad Laboral, baja medica) y el 08 como la parte SI derivada de
    # IL - son dos partidas de ingreso bruto distintas y complementarias, no una
    # alternativa. Sumar solo la 07 (como hacia el codigo anterior) dejaba fuera lo
    # cobrado por prestacion de incapacidad laboral, incompleto para una variable
    # llamada "ingreso bruto anual".
    # fillna(0) antes de sumar: si a alguien no le aplica la parte derivada de IL (lo
    # habitual, nadie cobro baja medica ese anyo) es de esperar que venga NULL, no 0 -
    # sumar directo perderia el valor real del campo 07 (NULL + numero = NULL en pandas).
    df["_ingreso_bruto_total"] = (
        df["07 PERC_INTEGRA_DIN_NO_DERIVADA_IL"].fillna(0) + df["08 PERC_INTEGRA_DIN_DERIVADA_IL"].fillna(0)
    )
    # v2.2 (decision explicita del usuario): ingresos_bruto_total_{anio} es el antiguo
    # Ingresos_bruto_anual_{anio} (mismo valor, solo cambia el nombre, incluye TODAS las claves)
    # y se reparte ademas en 5 columnas por tipo de percepcion que suman exactamente el total:
    # sueldo (clave A), pension (clave B + pensiones exentas L06/L07/L08), prestacion por
    # desempleo (C), actividad economica y profesional (F+G+H) y otras rentas exentas (resto de
    # L: dietas, indemnizaciones por despido, maternidad/paternidad, sepelio...). Cada tipo vale 0
    # para quien tiene datos fiscales pero no cobra de ese tipo; quien no aparece en la tabla
    # fiscal queda vacio (NULL) al fusionar, como ya pasaba con el total.
    # retenciones_irpf_{anio}: 09 + 10 (retencion sobre la percepcion dineraria no derivada y
    # derivada de IL), tambien en centimos.
    df["_retenciones"] = (
        df["09 RETENC_SOBRE_P_DINER_NO_DERIV_IL"].fillna(0) + df["10 RETENC_SOBRE_P_DINER_DERIV_IL"].fillna(0)
    )
    clave = df["05 CLAVE_PERCEPCION"].fillna("").astype(str).str.strip()
    tipo = clave + df["06 SUBCLAVE_PERCEPCION"].fillna("").astype(str).str.strip()
    grupos = {
        f"ingresos_sueldo_{anio_datos}": clave == "A",
        f"ingresos_pension_{anio_datos}": (clave == "B") | tipo.isin(TIPOS_PENSION_EXENTA),
        f"ingresos_prestacion_{anio_datos}": clave == "C",
        f"ingresos_actividad_{anio_datos}": clave.isin(["F", "G", "H"]),
        f"ingresos_otras_exentas_{anio_datos}": (clave == "L") & ~tipo.isin(TIPOS_PENSION_EXENTA),
    }
    fuera = ~clave.isin(CLAVES_PERCEPCION_PREVISTAS)
    if fuera.any():
        print(f"AVISO Grupo F: {int(fuera.sum())} filas fiscales con una clave de percepcion no prevista "
              f"({sorted(clave[fuera].unique())}): entran en el total pero en ninguna de las 5 columnas por tipo")
    for nombre, mascara in grupos.items():
        df["_" + nombre] = df["_ingreso_bruto_total"].where(mascara, 0)
    nombre_col_ingresos_brutos = f"ingresos_bruto_total_{anio_datos}"
    renombrar = {"_ingreso_bruto_total": nombre_col_ingresos_brutos, "_retenciones": f"retenciones_irpf_{anio_datos}"}
    renombrar.update({"_" + n: n for n in grupos})
    df_estudio_ingresosbrutos = (df.groupby("01 IPF")[list(renombrar)].sum() / 100).reset_index().rename(columns=renombrar)

    # clave_subclave_ppal / clave_subclave_todas_cobradas (v2.2, decision explicita del usuario):
    # tipo de percepcion (clave + subclave, p.ej. "B01") con MAYOR importe total sumado de la
    # persona - no la fila individual mas grande: quien cobra 3 sueldos de 6.000 EUR y una pension
    # de 10.000 EUR es principalmente "sueldo" (18.000). Desempate por codigo. La principal sale
    # con etiqueta ("B01 Pension SS / Clases Pasivas", etl/metadata/clave_percepcion_dict.csv; un
    # codigo sin etiqueta sale con el codigo solo) y "todas cobradas" es la lista de codigos
    # distintos con importe > 0, de mayor a menor importe (el primero coincide con la principal).
    por_tipo = df.assign(_tipo=tipo).loc[tipo != ""].groupby(["01 IPF", "_tipo"], as_index=False)["_ingreso_bruto_total"].sum()
    por_tipo = por_tipo.sort_values(["01 IPF", "_ingreso_bruto_total", "_tipo"], ascending=[True, False, True])
    etiquetas_df = pd.read_csv(_CLAVE_PERCEPCION_CSV, sep=";", encoding="utf-8", dtype=str)
    etiquetas = dict(zip(etiquetas_df["codigo"], etiquetas_df["descripcion"]))
    # Solo entre los tipos con importe > 0 (2026-09-22, decision explicita del usuario): sin este
    # filtro, 278 personas con datos fiscales pero NINGUN importe positivo (todos <= 0) recibian
    # igualmente un "tipo principal" arbitrario (el de menor codigo entre los empatados a 0 o
    # negativos) en vez de NO CONSTA - inconsistente con clave_subclave_todas_cobradas, que para
    # esas mismas 278 personas ya salia vacia (ver mas abajo, tambien filtrado a > 0). Al no
    # quedar fila para ellas aqui, el left merge en fusionar_grupo_f las deja en NaN, y
    # post_fillna (mas abajo) las convierte a "NO CONSTA" igual que a quien no tiene datos
    # fiscales en absoluto.
    df_estudio_tipo_ppal = por_tipo[por_tipo["_ingreso_bruto_total"] > 0].drop_duplicates(subset="01 IPF", keep="first")[["01 IPF", "_tipo"]].copy()
    df_estudio_tipo_ppal["clave_subclave_ppal"] = df_estudio_tipo_ppal["_tipo"].map(
        lambda t: f"{t} {etiquetas[t]}" if t in etiquetas else t
    )
    df_estudio_tipo_ppal = df_estudio_tipo_ppal[["01 IPF", "clave_subclave_ppal"]]
    df_estudio_tipo_todas = (
        por_tipo[por_tipo["_ingreso_bruto_total"] > 0].groupby("01 IPF")["_tipo"].apply(list).reset_index()
        .rename(columns={"_tipo": "clave_subclave_todas_cobradas"})
    )

    cond = df["20 DISCAPACIDAD"] > 0
    df_estudio_discap_fiscal = df.loc[cond, ["01 IPF"]].drop_duplicates().copy()
    df_estudio_discap_fiscal["Discap_fiscal"] = True

    # Prov_fiscal_actual (2026-09-08, decision explicita del usuario): codigo de
    # "04 PROVINCIA" en Datos fiscales (decodificado a nombre despues, en Grupo H, junto con
    # 05 PROV_NACIMIENTO/06 PROV_1a_AFILIACION - ver comentario alli) - un unico ejercicio por
    # edicion de la MCVL (esta tabla no tiene columna de anyo, a diferencia de Pensiones), pero
    # SI puede haber varias filas por persona (varios pagadores/percepciones en el mismo anyo,
    # ver conteo de filas vs personas de esta sesion): 51.014 personas (4,2%) tienen mas de un
    # valor distinto de 04 PROVINCIA.
    # Desempate confirmado por el usuario via AskUserQuestion: provincia del pagador con MAYOR
    # ingreso bruto (mismo _ingreso_bruto_total ya calculado arriba para ingresos_bruto_total),
    # con la propia provincia como desempate final para determinismo en empates exactos de
    # ingreso (mismo patron "total DESC, val ASC" ya usado en el resto del fichero).
    df_ordenado = df.sort_values(
        ["01 IPF", "_ingreso_bruto_total", "04 PROVINCIA"], ascending=[True, False, True]
    )
    df_estudio_provincia_actual = df_ordenado.drop_duplicates(subset="01 IPF", keep="first")[
        ["01 IPF", "04 PROVINCIA"]
    ].rename(columns={"04 PROVINCIA": "Prov_fiscal_actual"})

    del df, df_ordenado, por_tipo
    gc.collect()

    extracciones = [
        df_estudio_ingresosbrutos, df_estudio_discap_fiscal, df_estudio_provincia_actual,
        df_estudio_tipo_ppal, df_estudio_tipo_todas,
    ]
    return extracciones, nombre_col_ingresos_brutos


def fusionar_grupo_f(parquet_folder, anio_datos):
    extracciones, nombre_col_ingresos_brutos = extraer_grupo_f(parquet_folder, anio_datos)

    def post_fillna(df):
        df["Discap_fiscal"] = df["Discap_fiscal"].fillna(False).astype(bool)
        df["clave_subclave_ppal"] = df["clave_subclave_ppal"].fillna("NO CONSTA").astype("category")

    return fusionar_grupo(parquet_folder, extracciones, post_fillna), nombre_col_ingresos_brutos


# ---------------------------------------------------------------------------
# GRUPO G - Extracciones desde Bases de cotizacion (cuenta ajena + cuenta propia)
# ---------------------------------------------------------------------------

def extraer_grupo_g(con, parquet_folder):
    """~32M + ~4M filas (cuenta ajena + cuenta propia): agregado con DuckDB, igual que
    Afiliacion. BUG corregido 2026-09-22 (decision explicita del usuario): solo se sumaba
    cuenta ajena (CA); cuenta propia (CP, autonomos y R.E. Agrario Cuenta Propia) se quedaba
    fuera, asi que quien cotizo unicamente como autonomo/agrario cuenta propia salia con
    Cotizado_total y cotizado_anual_promedio NULL pese a tener historia laboral completa.
    Verificado con datos reales: explicaba 56.029 de las 79.383 personas afectadas (71%; el
    resto, con regimenes de cuenta ajena, no tiene ninguna fila en Bases CA - hueco de
    cobertura de la propia MCVL, no de este calculo)."""
    bases_ca = _ruta(parquet_folder, "tabla_bases_cotizacion_CA_optimizada.parquet")
    bases_cp = _ruta(parquet_folder, "tabla_bases_cotizacion_CP_optimizada.parquet")
    bases_union = f"""
        SELECT "01 IPF", "03 ANO_COTIZACION", "16 BCC_ANUAL_TOTAL" FROM read_parquet('{bases_ca}')
        UNION ALL
        SELECT "01 IPF", "03 ANO_COTIZACION", "16 BCC_ANUAL_TOTAL" FROM read_parquet('{bases_cp}')
    """

    df_cotizadototal = con.execute(f"""
        SELECT "01 IPF" AS "01 IPF", SUM("16 BCC_ANUAL_TOTAL") AS "Cotizado_total"
        FROM ({bases_union}) GROUP BY 1
    """).fetchdf()

    # Bases_cotiz_bruto_historico: lista de bases de cotizacion por anyo, ordenada por anyo
    # (igual que el cuaderno: groupby ordena por las claves antes del apply(list)). El agregado
    # por (IPF, anyo) se hace con DuckDB (32M+4M filas de origen), pero el propio DuckDB revienta
    # -por disco y luego por su limite de memoria interno- al construir la lista ordenada por
    # grupo. El resultado intermedio ya es pequenyo (~150 MB, un IPF-anyo por fila), asi que la
    # lista final se construye con pandas (igual que el cuaderno), sin ese problema. Si una
    # persona cotizo el mismo anyo por CA y CP a la vez (pluriempleo/pluriactividad), la base de
    # ese anyo se suma (mismo criterio que Cotizado_total: es la base total cotizada ese anyo).
    por_anio_path = _ruta(parquet_folder, "_tmp_bases_ca_por_anio.parquet")
    con.execute(f"""
        COPY (
            SELECT "01 IPF" AS ipf, "03 ANO_COTIZACION" AS anio, SUM("16 BCC_ANUAL_TOTAL") AS total
            FROM ({bases_union}) GROUP BY 1, 2
        ) TO '{por_anio_path}' (FORMAT PARQUET)
    """)
    df_por_anio = pd.read_parquet(por_anio_path).sort_values(["ipf", "anio"])
    df_basescotizhist = df_por_anio.groupby("ipf")["total"].apply(list).reset_index()
    df_basescotizhist.columns = ["01 IPF", "Bases_cotiz_bruto_historico"]
    del df_por_anio
    os.remove(por_anio_path)

    return [df_cotizadototal, df_basescotizhist]


def fusionar_grupo_g(parquet_folder, con):
    extracciones = extraer_grupo_g(con, parquet_folder)
    return fusionar_grupo(parquet_folder, extracciones)


# ---------------------------------------------------------------------------
# GRUPO H - Combinaciones sobre Personas extendida (sin cargar ninguna tabla grande)
# ---------------------------------------------------------------------------

DIAS_POR_ANIO = 365.25

PAISES_LATINOS = ["N03", "N06", "N07", "N08", "N09", "N13", "N22", "N26", "N27"]
PAISES_AFRICANOS = ["N12", "N23"]
# N16 = Reino Unido, ya no es miembro de la UE desde el Brexit (2020) - se queda agrupado
# aqui de todas formas (decision explicita del usuario, 2026-08-20: la MCVL cubre tambien
# muchos anyos anteriores al Brexit), pero el nombre de la lista y la etiqueta que ve el
# analista (mas abajo, "paises_UE_UK") lo dejan explicito para no llevar a confusion.
PAISES_EU_UK = ["N01", "N04", "N10", "N11", "N14", "N15", "N16", "N17", "N19", "N20"]
PAISES_LEJANOS = ["N05", "N18", "N21", "N24", "N25", "N99"]
ESPANA = ["N00"]

GRUPOS_COTIZ_DICT = {
    0: "0 NO CONSTA",
    1: "1 INGENIEROS, LICENCIADOS Y ALTA DIRECCIÓN",
    2: "2 INGENIEROS TÉCNICOS, PERITOS Y AYUDANTES",
    3: "3 JEFES ADMINISTRATIVOS Y DE TALLER",
    4: "4 AYUDANTES NO TITULADOS",
    5: "5 OFICIALES ADMINISTRATIVOS",
    6: "6 SUBALTERNOS",
    7: "7 AUXILIARES ADMINISTRATIVOS",
    8: "8 OFICIALES DE PRIMERA Y SEGUNDA",
    9: "9 OFICIALES DE TERCERA Y ESPECIALISTAS",
    10: "10 MAYORES DE 18 AÑOS NO CUALIFICADOS",
    11: "11 TRABAJADORES MENORES DE 18 AÑOS",
    # Faltaba (2026-08-20): la guia documenta hasta el 12 ("Trabajadores menores de
    # 17 años") - sin esta entrada, .map(GRUPOS_COTIZ_DICT).fillna("NO CONSTA") en
    # grupo_h_final convertia silenciosamente cualquier ganador real de codigo 12 en
    # "NO CONSTA", en las 3 columnas que usan este diccionario (Grupo_cotiz_ppal,
    # Grupo_cotiz_1er_Empleo_Signif, Grupo_Ultimo_Empleo). Mismo hueco ya corregido
    # en app/routers/trayectorias.py (commit 34025f5) para la trayectoria categorica.
    12: "12 TRABAJADORES MENORES DE 17 AÑOS",
}

# Columnas finales de tabla_personas_extendida, mismo orden que el cuaderno (nuevo_orden)
NUEVO_ORDEN = [
    "01 IPF", "02 FECHA_NACIMIENTO", "03 SEXO", "04 NACIONALIDAD", "05 PROV_NACIMIENTO",
    "06 PROV_1a_AFILIACION", "07 DOMIC_RESID_HABITUAL", "prov_resid_habitual",
    "08 FECHA_FALLECIMIENTO", "edad_fallecimiento", "ha_fallecido", "09 PAIS_NACIMIENTO",
    "Nacionalidad_agrupado", "Pais_nacim_agrupado",
    "Edad_Actual_Fecha_Corte",
    "Edad_Primer_Empleo_Signif", "Duracion_1er_Empleo_Signif", "Grupo_cotiz_1er_Empleo_Signif",
    "Edad_Ultimo_Empleo", "Grupo_Ultimo_Empleo",
    "causa_ult_baja", "tipo_contr_ult_baja", "act_econ_ult_baja",
    "t_contrato_mas_largo", "Causa_Baja_Trab_Ppal", "Fecha_ini_Primer_Empleo", "Tiempo_total_desde_inicio_VL",
    "duracion_promedio_EMPLEO", "Cotizado_anual_promedio",
    "num_altas_anual_promedio", "num_bajas_anual_promedio",
    "T_total_EMPLEO", "T_total_DESEMPLEO", "T_total_MIXTO_EMPL_DESEMPL", "T_total_NO_EMPLEO",
    "%T_total_EMPLEO", "%T_total_DESEMPLEO", "%T_total_MIXTO_EMPL_DESEMPL", "%T_total_NO_EMPLEO",
    "Reg_cotiz_ppal_Agrupado", "T_tot_reg_ppal",
    "Grupo_cotiz_ppal", "t_tot_grupo_ppal",
    "Tipo_Contr_ppal_Agrupado", "T_tot_tipo_contrato_ppal",
    "Prov_trabajo_ppal", "t_tot_prov_ppal",
    "Act_econ_ppal", "t_tot_act_econ_ppal",
    # Las columnas fiscales/de pension con el anyo grabado en el propio nombre (ingresos_*_AAAA,
    # retenciones_irpf_AAAA, Cobra_pension_AAAA/A) NO se listan aqui con un anyo fijo (2026-09-23,
    # decision explicita del usuario): antes decian literalmente "2024", asi que procesar
    # cualquier otro anyo las descartaba en silencio de la tabla final (solo quedaba un AVISO por
    # consola, sin frenar la ejecucion). Se detectan por patron justo antes de seleccionar las
    # columnas finales (ver mas abajo, _columnas_fiscales_pension) y se insertan aqui mismo.
    "clave_subclave_ppal", "clave_subclave_todas_cobradas", "Prov_fiscal_actual",
    "Discap_fiscal", "Discap_afiliacion", "Discap_pension",
    "Pension_todas_cobradas",
    "Pension_actual",
    "fecha_ultima_baja", "Situacion_actual_afiliacion", "Situacion_Paro_Ocupacion",
    "Fecha_inicio_ultima_racha_sin_empleo", "duracion_actual_sin_empleo",
    "causa_baja_mas_comun", "causas_baja_distintas",
]

# Patrones de las columnas fiscales/de pension con el anyo grabado en el nombre (ver comentario
# en NUEVO_ORDEN). Compartido con app/db.py conceptualmente (alli se repite el mismo criterio,
# sobre el Parquet ya generado, para reconocerlas sin tener que escribir el anyo a mano).
_PATRON_INGRESOS_RETENCIONES = re.compile(r"^(ingresos_\w+|retenciones_irpf)_\d{4}$")
_PATRON_COBRA_PENSION = re.compile(r"^Cobra_pension_\d{4}/\d$")
_PATRON_INGRESOS_LEGACY = re.compile(r"^Ingresos_bruto_anual_\d{4}$")


def _columnas_fiscales_pension_presentes(columnas):
    """Devuelve, de entre 'columnas' (las de df en Grupo H), las que son fiscales/de pension con
    el anyo en el nombre - para cualquier anyo, no solo 2024. Orden estable: ingresos/retenciones
    primero (alfabetico), Cobra_pension al final (va justo despues de Pension_actual)."""
    ingresos = sorted(c for c in columnas if _PATRON_INGRESOS_RETENCIONES.match(c) or _PATRON_INGRESOS_LEGACY.match(c))
    cobra_pension = sorted(c for c in columnas if _PATRON_COBRA_PENSION.match(c))
    return ingresos, cobra_pension


def _codificar_domicilio(codigo, provincias_dict):
    """07 DOMIC_RESID_HABITUAL (v2.2, decision explicita del usuario): pasa de numerica a
    categorica y se deriva la provincia. Segun la guia MCVL (1.007) el codigo es el del INE,
    5 digitos = 2 de provincia + 3 de municipio; solo los municipios de mas de 40.000
    habitantes llevan su codigo, el resto de cada provincia se agrupa como 'PP000'; '00000' =
    sin dato (no residente en Espanya) y una provincia superior a 52 es un dato erroneo. Aqui
    ambos casos ('00000' y provincia fuera de 1-52) quedan como 'NO CONSTA', igual que el
    resto de categoricas del fichero. Devuelve (domicilio, provincia): el domicilio como
    texto de 5 digitos ('28079') y la provincia con la misma etiqueta que Prov_trabajo_ppal
    ('28 MADRID')."""
    codigo = codigo.astype("Int64")
    provincia = codigo // 1000
    valido = provincia.between(1, 52).fillna(False).astype(bool)
    domicilio = codigo.astype(str).str.zfill(5).where(valido, "NO CONSTA")
    nombre_provincia = provincia.map(provincias_dict).where(valido).fillna("NO CONSTA")
    return domicilio.astype("category"), nombre_provincia.astype("category")


def _columnas_fallecimiento(codigo, nacimiento):
    """edad_fallecimiento / ha_fallecido (v2.2, decision explicita del usuario): 08
    FECHA_FALLECIMIENTO es un entero aaaamm (0 = no ha fallecido, guia 1.008) que no sirve para
    filtrar con > o < en la interfaz. edad_fallecimiento = edad en anyos al fallecer, con
    precision de mes (tanto el fallecimiento como 02 FECHA_NACIMIENTO solo traen anyo y mes),
    NULL si no ha fallecido; ha_fallecido = 'SI'/'NO' (mismo formato que Cobra_pension_*).
    No se corrige un fallecimiento anterior al nacimiento (dato erroneo de origen): sale una
    edad negativa, para que se pueda detectar."""
    codigo = codigo.fillna(0).astype("int64")
    ha_fallecido = codigo != 0
    meses = (codigo // 100 - nacimiento.dt.year) * 12 + (codigo % 100 - nacimiento.dt.month)
    edad = (meses / 12).where(ha_fallecido)
    return edad, ha_fallecido.map({True: "SI", False: "NO"}).astype("category")


def grupo_h_final(parquet_folder, provincias_dict_path, cnae_dict_path, paises_dict_path, causa_baja_dict_path):
    """Ultimo paso: calculos derivados (edades, tasas anuales, agrupaciones, filtros de
    calidad) y seleccion final de columnas. Todo en pandas: para cuando se llega aqui, la
    tabla ya esta reducida a una fila por persona (~1,36M filas), cabe de sobra en memoria."""
    output_path = _ruta(parquet_folder, "tabla_personas_extendida.parquet")
    df = pd.read_parquet(output_path)
    print(f"Grupo H: partiendo de {df.shape}")

    # Edad_Ultimo_Empleo
    df["Fecha_ini_Primer_Empleo_completa"] = df["Fecha_ini_Primer_Empleo"].dt.year * 12 + df["Fecha_ini_Primer_Empleo"].dt.month
    df["Fecha_final_Ultimo_Empleo_completa"] = df["Fecha_final_Ultimo_Empleo"].dt.year * 12 + df["Fecha_final_Ultimo_Empleo"].dt.month
    df["02 FECHA_NACIMIENTO_completa"] = df["02 FECHA_NACIMIENTO"].dt.year * 12 + df["02 FECHA_NACIMIENTO"].dt.month

    df["Edad_Ultimo_Empleo"] = df["Fecha_final_Ultimo_Empleo_completa"] - df["02 FECHA_NACIMIENTO_completa"]
    df.drop(columns=["Fecha_ini_Primer_Empleo_completa", "Fecha_final_Ultimo_Empleo_completa"], inplace=True)

    # Tiempo_total_desde_inicio_VL (2026-09-08, decision explicita del usuario):
    # sustituye a Anos_VL_excluye_desempleo_inicio_y_fin (que a su vez venia de
    # DURACION_1er_Empleo_a_Ultimo_Empleo = Fecha_final_Ultimo_Empleo - Fecha_ini_Primer_Empleo).
    # Esas dos fechas EXCLUYEN episodios de TRL 700-799/400 en los extremos (ver
    # extraer_grupo_b), asi que para el 0,76%/13,2% de personas cuyo episodio mas
    # antiguo/reciente es de ese tipo, la duracion se quedaba corta respecto al periodo real
    # cubierto por todos los episodios - relevante porque esta columna es la base de una resta
    # en T_total_NO_EMPLEO (tiempo_total - EMPLEO - DESEMPLEO - MIXTO) donde el residuo podia
    # salir negativo para ese mismo subconjunto con la version antigua.
    # fecha_fin_vl = MIN(FECHA_CORTE, fecha de fallecimiento). "08 FECHA_FALLECIMIENTO" es un
    # codigo YYYYMM (0 = no aplica, no un NULL real - confirmado en el Paso 0 de verificacion
    # de esta misma sesion), asi que se decodifica al ULTIMO dia de ese mes, no al primero: la
    # fecha solo tiene precision de mes, y usar el primer dia recortaria como "fuera de la vida
    # laboral" actividad legitima ocurrida mas tarde en el mismo mes del fallecimiento -
    # verificado con datos reales (Paso 0): de los 14.751 fallecidos con afiliacion, usar fin de
    # mes en vez de inicio de mes reduce de 1.109 a 21 el numero de personas cuyo ultimo episodio
    # (o hueco activo) excede su propia fecha de fallecimiento; de esos 21, 15 son episodios
    # "aun activos" (placeholder 2099) en personas que ya constan fallecidas.
    codigo_fallecimiento = df["08 FECHA_FALLECIMIENTO"]
    tiene_fallecimiento = codigo_fallecimiento != 0
    fecha_fallecimiento_fin_mes = pd.Series(pd.NaT, index=df.index).astype("datetime64[us]")
    if tiene_fallecimiento.any():
        inicio_mes_fallec = pd.to_datetime(pd.DataFrame({
            "year": codigo_fallecimiento.loc[tiene_fallecimiento] // 100,
            "month": codigo_fallecimiento.loc[tiene_fallecimiento] % 100,
            "day": 1,
        }))
        fecha_fallecimiento_fin_mes.loc[tiene_fallecimiento] = inicio_mes_fallec + pd.offsets.MonthEnd(0)
    fecha_fin_vl = fecha_fallecimiento_fin_mes.fillna(FECHA_CORTE).clip(upper=FECHA_CORTE)

    # +UN_DIA (2026-09-08, correccion tras validacion del Paso 3 - no estaba en la version
    # inicial de este calculo, que seguia la formula literal fecha_fin_vl -
    # Fecha_inicio_vida_laboral_total sin la convencion "dia de baja cuenta como en alta" del
    # resto del fichero, por parecer una diferencia de <=1 dia irrelevante a escala de anyos).
    # Resulto NO ser irrelevante: T_total_EMPLEO/DESEMPLEO/MIXTO_EMPL_DESEMPL (Grupo C, tramos
    # fusionados del Paso 2) SI usan esa convencion +1 (inclusive en ambos extremos), asi que sin
    # este +1 aqui, una persona con CERO hueco real de NO_EMPLEO en toda su vida laboral (cubierta
    # sin solucion de continuidad desde su primer episodio hasta fecha_fin_vl) generaba
    # T_total_NO_EMPLEO = -1 dia exacto en el residuo del Paso 3 (Tiempo_total - EMPLEO -
    # DESEMPLEO - MIXTO) - verificado con datos reales: 72.897 de 72.899 residuos negativos
    # detectados eran exactamente -1 dia, y desaparecen con este +1 (cuadre algebraico: la suma
    # inclusive de tramos que cubren el rango [S, E] completo sin huecos da E-S+1 dias, igual que
    # Tiempo_total con este +1; solo 2 residuos negativos reales quedan, ambos fallecidos cuyo
    # episodio real excede su propia fecha de fallecimiento - ver Paso 0, impacto ya aceptado
    # como marginal).
    df["Tiempo_total_desde_inicio_VL"] = fecha_fin_vl - df["Fecha_inicio_vida_laboral_total"] + UN_DIA
    # <= 0 -> NaT (mismo criterio que el resto de duraciones de este fichero): en teoria no
    # deberia poder pasar (fecha_fin_vl >= cualquier Baja_modificada real, ver Fase 1), pero se
    # protege igual que se hacia con DURACION_1er_Empleo_a_Ultimo_Empleo, por si un caso de dato
    # erroneo (fallecimiento anterior al primer episodio de Afiliacion) se colara.
    df.loc[
        df["Tiempo_total_desde_inicio_VL"] <= CERO_DIAS, "Tiempo_total_desde_inicio_VL"
    ] = pd.NaT

    # Edad_Primer_Empleo_Signif
    df["Fecha_1er_Empleo_Signif_completa"] = df["Fecha_1er_Empleo_Signif"].dt.year * 12 + df["Fecha_1er_Empleo_Signif"].dt.month
    df["Edad_Primer_Empleo_Signif"] = df["Fecha_1er_Empleo_Signif_completa"] - df["02 FECHA_NACIMIENTO_completa"]
    df.drop(columns=["Fecha_1er_Empleo_Signif_completa", "02 FECHA_NACIMIENTO_completa"], inplace=True)

    df["Edad_Ultimo_Empleo"] = df["Edad_Ultimo_Empleo"] / 12
    df["Edad_Primer_Empleo_Signif"] = df["Edad_Primer_Empleo_Signif"] / 12

    # Edad negativa (episodio de Afiliacion anterior a la fecha de nacimiento) -> NULL, no un
    # valor imposible (decision explicita del usuario, 2026-08-20, para no falsear
    # estadisticas). Verificado con datos reales: 194 personas en Edad_Primer_Empleo_Signif
    # (hasta -33,7 anyos) y 14 en Edad_Ultimo_Empleo - casos aislados, probable reutilizacion
    # del IPF entre dos personas distintas (la guia MCVL ya avisa de que el IPF, aunque
    # normalmente unico de por vida, "en la mayoria de los casos" puede excepcionalmente
    # reasignarse). No hay forma de saber cual de los dos datos (fecha de nacimiento o
    # episodio de Afiliacion) es el erroneo para estos casos concretos.
    df.loc[df["Edad_Ultimo_Empleo"] < 0, "Edad_Ultimo_Empleo"] = np.nan
    df.loc[df["Edad_Primer_Empleo_Signif"] < 0, "Edad_Primer_Empleo_Signif"] = np.nan

    # Edad_Actual_Fecha_Corte: edad a la fecha de corte de esta MCVL (FECHA_CORTE), NUNCA la
    # fecha real del sistema - si no, se desincroniza del resto del snapshot segun cuando se
    # ejecute la app.
    fecha_nacimiento_completa = df["02 FECHA_NACIMIENTO"].dt.year * 12 + df["02 FECHA_NACIMIENTO"].dt.month
    fecha_corte_completa = FECHA_CORTE.year * 12 + FECHA_CORTE.month
    df["Edad_Actual_Fecha_Corte"] = (fecha_corte_completa - fecha_nacimiento_completa) / 12
    # Comprobacion de cordura (2026-09-23): una regeneracion salio con esta columna a 0 para las
    # 1.359.056 personas y no se noto hasta ejecutar las reglas de consistencia, 30 minutos despues.
    # Si no hay ni una edad positiva, parar aqui con un error claro en vez de escribir el Parquet.
    if not (df["Edad_Actual_Fecha_Corte"] > 0).any():
        raise ValueError(
            "Edad_Actual_Fecha_Corte no tiene ningun valor positivo (todo 0 o nulo): "
            f"FECHA_CORTE={FECHA_CORTE.date()}, primeras fechas de nacimiento="
            f"{df['02 FECHA_NACIMIENTO'].head(3).tolist()}. Revisar antes de continuar."
        )

    # num_altas_anual_promedio / num_bajas_anual_promedio: normalizados por anyos de vida laboral.
    # Denominador cambiado de DURACION_1er_Empleo_a_Ultimo_Empleo a
    # Tiempo_total_desde_inicio_VL el 2026-09-08 (consecuencia automatica de que la
    # primera columna ya no existe, ver PASO 1 - NOTA pendiente de revision en otra sesion:
    # decidir si "todo el periodo" es la base correcta para estas 2 tasas o si conviene
    # separarlas de nuevo).
    df["num_altas_anual_promedio"] = df["num_episodios_alta"] / df["Tiempo_total_desde_inicio_VL"].dt.days * 365
    df["num_bajas_anual_promedio"] = df["num_episodios_baja"] / df["Tiempo_total_desde_inicio_VL"].dt.days * 365
    df.drop(columns=["num_episodios_alta", "num_episodios_baja"], inplace=True)

    # Cotizado_anual_promedio: normalizado por tiempo de empleo + desempleo + mixto, NO por
    # Tiempo_total_desde_inicio_VL (corregido 2026-09-22, decision explicita del usuario, deja
    # de compartir denominador con num_altas/bajas_anual_promedio - la "NOTA pendiente" de
    # arriba solo les afecta a esas dos). Motivo: Tiempo_total_desde_inicio_VL incluye tiempo
    # SIN ningun episodio ("no empleo", huecos), que diluia el promedio con tiempo donde nunca
    # pudo haber cotizacion. Pero el desempleo SI hay que incluirlo (no excluirlo del todo,
    # decision explicita del usuario): la prestacion contributiva de desempleo y el subsidio
    # >52/55 anyos cotizan a efectos de jubilacion, y se ha verificado con datos reales que ese
    # tiempo puede llevar aparejada una base de cotizacion real en Bases CA/CP - de las 1.248
    # personas cuya carrera entera es solo desempleo, 669 (54%) tienen Cotizado_total > 0 (99%
    # en el caso del TRL 753, subsidio >52/55). Excluir el desempleo del denominador dejaria en
    # el numerador dinero de anyos que el denominador no cuenta, inflando el promedio.
    _base_cotizado = df["T_total_EMPLEO"] + df["T_total_DESEMPLEO"] + df["T_total_MIXTO_EMPL_DESEMPL"]
    df["Cotizado_anual_promedio"] = df["Cotizado_total"] / _base_cotizado.dt.days * 365 / 100

    # Nacionalidad_agrupado / Pais_nacim_agrupado
    condiciones = [
        df["04 NACIONALIDAD"].isin(PAISES_LATINOS), df["04 NACIONALIDAD"].isin(PAISES_AFRICANOS),
        df["04 NACIONALIDAD"].isin(PAISES_EU_UK), df["04 NACIONALIDAD"].isin(PAISES_LEJANOS),
        df["04 NACIONALIDAD"].isin(ESPANA),
    ]
    elecciones = ["paises_latinos", "paises_africanos", "paises_UE_UK", "paises_lejanos", "espana"]
    # default=np.nan con choices de texto revienta en numpy reciente (StrDType vs float no
    # promocionan a un dtype comun); se usa un centinela de texto y se convierte despues.
    df["Nacionalidad_agrupado"] = np.select(condiciones, elecciones, default="_SIN_DATO_")
    df["Nacionalidad_agrupado"] = df["Nacionalidad_agrupado"].replace("_SIN_DATO_", np.nan)

    condiciones_pais_nacim = [
        df["09 PAIS_NACIMIENTO"].isin(PAISES_LATINOS), df["09 PAIS_NACIMIENTO"].isin(PAISES_AFRICANOS),
        df["09 PAIS_NACIMIENTO"].isin(PAISES_EU_UK), df["09 PAIS_NACIMIENTO"].isin(PAISES_LEJANOS),
        df["09 PAIS_NACIMIENTO"].isin(ESPANA),
    ]
    df["Pais_nacim_agrupado"] = np.select(condiciones_pais_nacim, elecciones, default="_SIN_DATO_")
    df["Pais_nacim_agrupado"] = df["Pais_nacim_agrupado"].replace("_SIN_DATO_", np.nan)

    # Situacion_Paro_Ocupacion: Ocupado / Parado / Pensionista
    # Vectorizado (no df.apply(axis=1), que construye una Series de ~50 columnas por cada una
    # de las 1,36M filas y revienta por memoria en esta maquina - MemoryError comprobado).
    df["Situacion_Paro_Ocupacion"] = np.select(
        [
            df["Situacion_actual_afiliacion"] == "Alta_ocupado",
            df["Situacion_actual_afiliacion"] == "Nunca_ha_cotizado",
            df["Situacion_actual_afiliacion"] == "Baja_para_pension",
        ],
        ["Ocupado", "Pensionista", "Pensionista"],
        default="Parado",
    )

    # Reclasificacion de Parado en Parado_LD / Parado_NoLD (vectorizado igual).
    # "Asimilado_al_alta_prest_subs52" ya no existe como categoria (se separo en
    # Asimilado_al_alta_subs52 / Asimilado_al_alta_prest_subs, commit 730d910) - corregido
    # aqui tambien (bug real: se habia quedado con el nombre viejo, que ya no aparece
    # nunca en Situacion_actual_afiliacion, asi que ninguna de esas personas entraba en
    # usa_fecha_inicio_paro por esta via).
    # duracion_actual_sin_empleo (nombre 2026-08-20; antes "duracion_paro_dias"): dias sin
    # empleo hasta FECHA_CORTE (antes de 2026-08-24 usaba una fecha de referencia distinta,
    # FECHA_REFERENCIA_45 = 2025-04-30, un mes anterior - unificada en FECHA_CORTE por
    # decision explicita del usuario, ver config.py), con origen distinto segun la via por
    # la que se llego a "Parado" (concepto aclarado por el usuario, ver tambien el comentario de
    # Fecha_inicio_ultima_racha_sin_empleo mas arriba): si esta cobrando del SEPE ahora
    # mismo, o de baja de esa prestacion/subsidio sin haber vuelto a trabajar desde entonces
    # (Asimilado_otros/Asimilado_al_alta_subs52/Asimilado_al_alta_prest_subs/
    # Baja_de_prestacion/Baja_de_subsidio), se cuenta desde que empezo esa ultima racha de
    # cobro (Fecha_inicio_ultima_racha_sin_empleo); en cualquier otro caso (de baja de
    # actividad sin pasar por el SEPE), se cuenta desde la fecha de la ultima baja de
    # afiliacion (fecha_ultima_baja). Las 5 categorias de la primera rama comparten esto: en
    # todas ellas Fecha_inicio_ultima_racha_sin_empleo (que no exige que la racha siga
    # activa) tiene valor porque hubo episodios de cobro entre los 5 mas recientes.
    usa_cobrando_sepe = df["Situacion_actual_afiliacion"].isin(
        [
            "Asimilado_otros",
            "Asimilado_al_alta_subs52",
            "Asimilado_al_alta_prest_subs",
            "Baja_de_prestacion",
            "Baja_de_subsidio",
        ]
    )
    dias_desde_inicio = (
        FECHA_CORTE - df["Fecha_inicio_ultima_racha_sin_empleo"]
    ).dt.days
    dias_desde_ultima_baja = (FECHA_CORTE - df["fecha_ultima_baja"]).dt.days

    es_parado = df["Situacion_Paro_Ocupacion"] == "Parado"
    duracion_actual_sin_empleo = pd.Series(np.nan, index=df.index)
    mask_inicio = (
        es_parado & usa_cobrando_sepe & df["Fecha_inicio_ultima_racha_sin_empleo"].notna()
    )
    duracion_actual_sin_empleo[mask_inicio] = dias_desde_inicio[mask_inicio]
    mask_ultima_baja = es_parado & ~usa_cobrando_sepe & df["fecha_ultima_baja"].notna()
    duracion_actual_sin_empleo[mask_ultima_baja] = dias_desde_ultima_baja[mask_ultima_baja]
    df["duracion_actual_sin_empleo"] = duracion_actual_sin_empleo

    umbral_ld = 365
    es_ld = es_parado & duracion_actual_sin_empleo.notna() & (duracion_actual_sin_empleo > umbral_ld)
    df.loc[es_parado & es_ld, "Situacion_Paro_Ocupacion"] = "Parado_LD"
    df.loc[es_parado & ~es_ld, "Situacion_Paro_Ocupacion"] = "Parado_NoLD"

    # Categoria compuesta "<situacion>_y_pensionista" (2026-09-10, decision
    # explicita del usuario tras un bug real que reporto: en "Trayectorias de
    # colectivos", el CONTRACOLECTIVO de "Pensionista" (situacion_paro_ocupacion)
    # mostraba gente con cuantia de pension - porque el criterio de arriba
    # daba prioridad absoluta a "esta Ocupado ahora" (o a estar Parado) sobre
    # "tambien cobra pension" y la silenciaba sin mas. Verificado con datos
    # reales: 589 personas no-"Pensionista" con pension activa el ultimo anyo
    # (241 Ocupado, 348 Parado_LD/NoLD), de las cuales solo 147 eran de
    # Jubilacion (unico tipo que el codigo viejo comprobaba via
    # _tiene_jubilacion, ya eliminada) - el resto son Incapacidad/Viudedad/
    # Orfandad/a favor de familiares, compatibles con trabajar o estar en
    # paro segun la normativa real. Cobra_pension_2024/5 (SI/NO, ya calculado
    # en el Grupo E - ver extraer_grupo_e) cubre CUALQUIER tipo de pension, no
    # solo Jubilacion, a peticion explicita del usuario.
    # Solo se combina con Ocupado/Parado_LD/Parado_NoLD - quien ya es
    # "Pensionista" puro (Baja_para_pension/Nunca_ha_cotizado, arriba) no
    # necesita el sufijo, ya representa "cobra pension" por si mismo. Esto es
    # solo un RELABEL de filas que ya tenian exactamente una categoria cada
    # una (nunca anyade ni quita filas), asi que las categorias resultantes
    # siguen siendo mutuamente excluyentes y su suma sigue dando el total
    # exacto de personas - condicion explicita del usuario.
    # Nombre de columna independiente del anyo (2026-09-23, decision explicita del usuario,
    # mismo motivo que NUEVO_ORDEN mas abajo): antes decia literalmente "Cobra_pension_2024/5",
    # asi que procesar un anyo distinto de 2024 reventaba aqui con KeyError.
    _, _col_cobra_pension_detectada = _columnas_fiscales_pension_presentes(df.columns)
    if not _col_cobra_pension_detectada:
        raise KeyError("No se encuentra ninguna columna Cobra_pension_AAAA/A en df - revisar Grupo E (fusionar_grupo_e)")
    cobra_pension = df[_col_cobra_pension_detectada[0]] == "SI"

    # Jubilacion excluida de Parado_*_y_pensionista (2026-09-23, decision explicita del usuario,
    # matiz sobre la decision de arriba que unifico en Cobra_pension_2024/5 para todo tipo de
    # pension): un jubilado no esta "en paro" en el sentido que pretende esta categoria - la
    # categoria compuesta quiere representar a quien alterna trabajo y cobro de pension (p.ej.
    # incapacidad parcial, viudedad - compatibles con trabajar) y esta AHORA MISMO
    # temporalmente sin empleo, no a alguien retirado. Ocupado_y_pensionista NO se toca: trabajar
    # y cobrar jubilacion a la vez es un caso real y legitimo (jubilacion activa). Verificado con
    # datos reales: 53.077 Parado_LD_y_pensionista y 1.248 Parado_NoLD_y_pensionista tenian
    # Jubilacion en Pension_actual, edad media 78 anyos, mayoria con ultima baja de afiliacion
    # "Baja_de_actividad" normal (no "pase a pensionista") anyos antes de empezar a cobrar.
    tiene_jubilacion = df["Pension_actual"].apply(lambda l: isinstance(l, (list, np.ndarray)) and "Jubilación" in l)

    combinable_con_pension = df["Situacion_Paro_Ocupacion"].isin(["Ocupado", "Parado_LD", "Parado_NoLD"])
    es_parado_actual = df["Situacion_Paro_Ocupacion"].isin(["Parado_LD", "Parado_NoLD"])

    # Parado + jubilacion -> Pensionista puro (no sufijo): ya no es un sufijo, es la
    # categoria en si, igual criterio que Baja_para_pension/Nunca_ha_cotizado (arriba).
    mask_parado_jubilado = es_parado_actual & cobra_pension & tiene_jubilacion
    df.loc[mask_parado_jubilado, "Situacion_Paro_Ocupacion"] = "Pensionista"

    # Renombrado 2026-09-23 (decision explicita del usuario): "Parado_LD/NoLD_y_pensionista" se
    # queda como "Parado_LD/NoLD_pension_no_jubilacion" - tras la exclusion de arriba, quien
    # conserva esta categoria NUNCA tiene jubilacion (siempre incapacidad/viudedad/orfandad/etc.),
    # asi que el nombre antiguo podia confundir (sonaba a "puede incluir jubilados"). Ocupado
    # sigue con el sufijo "_y_pensionista" de siempre (SI puede incluir jubilacion, ver arriba).
    mask_pension_ocupado = (df["Situacion_Paro_Ocupacion"] == "Ocupado") & cobra_pension
    df.loc[mask_pension_ocupado, "Situacion_Paro_Ocupacion"] = "Ocupado_y_pensionista"
    mask_pension_parado = es_parado_actual & cobra_pension & ~mask_parado_jubilado
    df.loc[mask_pension_parado, "Situacion_Paro_Ocupacion"] = (
        df.loc[mask_pension_parado, "Situacion_Paro_Ocupacion"] + "_pension_no_jubilacion"
    )

    # Correccion de tramos que se extienden mas alla del fallecimiento (2026-09-23, decision
    # explicita del usuario, alternativa explorada al rediseño de Fase 1 - ver comentario de mas
    # abajo, "impacto ya aceptado como marginal", que databa de antes de esta correccion): Fase 1
    # sustituye el placeholder 2099 (episodio "aun activo") por FECHA_CORTE sin conocer la fecha
    # de fallecimiento (esa columna no existe en Afiliacion, solo llega a Personas) - si alguien
    # fallece teniendo un episodio todavia activo, ese tramo se cuenta hasta FECHA_CORTE en vez de
    # hasta su muerte real. Se corrige aqui, sin tocar Fase 1 ni su arquitectura: se relee
    # tabla_afiliacion_tramos.parquet (ya generado en el Paso 2) restringido a fallecidos, y se
    # resta de T_total_EMPLEO/DESEMPLEO/MIXTO_EMPL_DESEMPL (la categoria que corresponda en cada
    # caso) los dias de cada tramo que caen DESPUES de fecha_fin_vl (la fecha de fallecimiento ya
    # calculada arriba). Solo afecta a quien tiene un tramo que de verdad se extiende mas alla de
    # su propia muerte - verificado con datos reales: 14 personas de 18.804 fallecidas con
    # afiliacion (0,07%). Probado contra los 2 casos con T_total_NO_EMPLEO negativo conocidos
    # (ipf 118384, exceso de 335 dias en EMPLEO; ipf 4725529, 396 dias en EMPLEO): tras la
    # correccion, sus residuos dejan de ser negativos.
    import duckdb
    _tramos_path = _ruta(parquet_folder, "tabla_afiliacion_tramos.parquet")
    _fallecidos = pd.DataFrame({"ipf": df.loc[tiene_fallecimiento, "01 IPF"].values,
                                 "fecha_fin_vl": fecha_fin_vl.loc[tiene_fallecimiento].values})
    if len(_fallecidos):
        _con_correccion = duckdb.connect()
        _con_correccion.register("_fallecidos", _fallecidos)
        _exceso = _con_correccion.execute(f"""
            SELECT t.ipf AS "01 IPF", t.categoria,
                   SUM(date_diff('day', GREATEST(t.fecha_inicio_tramo, f.fecha_fin_vl + INTERVAL 1 DAY), t.fecha_fin_tramo) + 1)
                       AS dias_exceso
            FROM read_parquet('{_tramos_path}') t
            JOIN _fallecidos f ON t.ipf = f.ipf
            WHERE t.fecha_fin_tramo > f.fecha_fin_vl
            GROUP BY 1, 2
        """).fetchdf()
        _con_correccion.close()
        if len(_exceso):
            print(f"Corrigiendo {_exceso['01 IPF'].nunique()} fallecidos con tramos mas alla de su fecha de fallecimiento")
            _exceso_us = pd.to_timedelta(_exceso["dias_exceso"], unit="D")
            for _categoria, _col in (("EMPLEO", "T_total_EMPLEO"), ("DESEMPLEO", "T_total_DESEMPLEO"),
                                      ("MIXTO", "T_total_MIXTO_EMPL_DESEMPL")):
                _mask = _exceso["categoria"] == _categoria
                if not _mask.any():
                    continue
                _resta = pd.Series(_exceso_us[_mask].values, index=_exceso.loc[_mask, "01 IPF"].values)
                _resta = _resta.reindex(df["01 IPF"]).fillna(CERO_DIAS)
                _resta.index = df.index
                df[_col] = df[_col] - _resta

    # T_total_NO_EMPLEO (PASO 3, 2026-09-08, decision explicita del usuario - materializada como
    # columna el mismo dia, a peticion expresa del usuario): residuo, no suma directa -
    # Tiempo_total_desde_inicio_VL menos las 3 categorias ya fusionadas/sin solape
    # (Paso 2: T_total_EMPLEO/DESEMPLEO/MIXTO_EMPL_DESEMPL). Por construccion no deberia poder
    # salir negativo - verificado con datos reales (tras el fix del +1 dia en
    # Tiempo_total_desde_inicio_VL, ver mas arriba, y la correccion de tramos mas alla del
    # fallecimiento justo encima): 0 residuos negativos en 1,31M personas.
    df["T_total_NO_EMPLEO"] = (
        df["Tiempo_total_desde_inicio_VL"]
        - df["T_total_EMPLEO"] - df["T_total_DESEMPLEO"] - df["T_total_MIXTO_EMPL_DESEMPL"]
    )
    _negativos_no_empleo = (df["T_total_NO_EMPLEO"] < CERO_DIAS).sum()
    print(
        f"T_total_NO_EMPLEO negativo (deberia ser 0 por construccion; "
        f"si no lo es, fallo en el barrido del Paso 2): {_negativos_no_empleo}"
    )

    # %T_total_EMPLEO / %T_total_DESEMPLEO / %T_total_MIXTO_EMPL_DESEMPL / %T_total_NO_EMPLEO
    # (PASO 3, 2026-09-08): proporcion (0-100) sobre Tiempo_total_desde_inicio_VL, con
    # T_total_EMPLEO/DESEMPLEO/MIXTO_EMPL_DESEMPL/NO_EMPLEO ya fusionados/sin solape (Paso 2) o
    # residuo (Paso 3) como numerador - antes de este cambio, suma por episodio bruto sin
    # comprobar solapes (ver comentario 2026-08-20 mas abajo sobre el x100) y con
    # DURACION_1er_Empleo_a_Ultimo_Empleo como denominador (ver Paso 1 mas arriba).
    # %T_total_NO_EMPLEO materializada a peticion expresa del usuario (2026-09-08, junto con
    # T_total_NO_EMPLEO mas arriba) - las 4 suman 100 por construccion (T_total_NO_EMPLEO es
    # el residuo exacto de las otras 3), verificado con datos reales en la validacion del Paso 3.
    # El x100 en si (proporcion 0-1 -> porcentaje real 0-100) viene del cuaderno original de
    # 2023 (codigo perdido, reconstruido sin el x100 y con una advertencia sin resolver -
    # "REVISAR ... verificar que es la definicion que se queria" - que esta version anterior de
    # este script tampoco llevaba) - decision explicita del usuario tras detectar que los
    # valores (0,8 / 0,9 / 0,1...) no podian ser un porcentaje real.
    df["%T_total_EMPLEO"] = df["T_total_EMPLEO"] / df["Tiempo_total_desde_inicio_VL"] * 100
    df["%T_total_DESEMPLEO"] = df["T_total_DESEMPLEO"] / df["Tiempo_total_desde_inicio_VL"] * 100
    df["%T_total_MIXTO_EMPL_DESEMPL"] = (
        df["T_total_MIXTO_EMPL_DESEMPL"] / df["Tiempo_total_desde_inicio_VL"] * 100
    )
    df["%T_total_NO_EMPLEO"] = df["T_total_NO_EMPLEO"] / df["Tiempo_total_desde_inicio_VL"] * 100

    # Conversion de columnas Timedelta a anyos (in-place)
    for col in df.columns:
        if pd.api.types.is_timedelta64_dtype(df[col]):
            df[col] = df[col].dt.days / DIAS_POR_ANIO

    # Diccionario de grupos de cotizacion
    for columna in ["Grupo_cotiz_ppal", "Grupo_cotiz_1er_Empleo_Signif", "Grupo_Ultimo_Empleo"]:
        df[columna] = df[columna].map(GRUPOS_COTIZ_DICT).fillna("NO CONSTA").astype("category")

    # Filtrar personas sin fecha de nacimiento
    antes = df.shape[0]
    df = df[df["02 FECHA_NACIMIENTO"].notna()].copy()
    print(f"Filas eliminadas por no tener fecha de nacimiento: {antes - df.shape[0]}")

    # Filtrar IPF duplicado (2026-09-22, decision explicita del usuario): "01 IPF" deberia ser
    # unico (una fila por persona, es la clave de join de toda la app), pero 200 IPF (453 filas)
    # vienen duplicados ya en el origen (tabla Personas cruda) con sexo y fecha_nacimiento
    # DISTINTOS entre las filas de un mismo IPF - son dos personas fisicas distintas que
    # comparten IPF, no una fila repetida por el ETL, asi que no hay forma fiable de elegir
    # cual de las dos es "la buena". Se borran todas las filas de esos IPF (no solo una de cada
    # dos), para no arrastrar un dato de origen ambiguo a la tabla final.
    antes = df.shape[0]
    ipf_duplicado = df["01 IPF"][df["01 IPF"].duplicated(keep=False)]
    df = df[~df["01 IPF"].isin(ipf_duplicado)].copy()
    print(f"Filas eliminadas por IPF duplicado en origen: {antes - df.shape[0]} "
          f"({ipf_duplicado.nunique()} IPF distintos)")

    # Filtrar outliers de Tiempo_total_desde_inicio_VL (± 3 desviaciones estandar)
    media = df["Tiempo_total_desde_inicio_VL"].mean()
    std = df["Tiempo_total_desde_inicio_VL"].std()
    limite_inferior = media - 3 * std
    limite_superior = media + 3 * std
    outliers = df[
        (df["Tiempo_total_desde_inicio_VL"] < limite_inferior)
        | (df["Tiempo_total_desde_inicio_VL"] > limite_superior)
    ]
    print(f"Outliers de Tiempo_total_desde_inicio_VL: {len(outliers)} ({len(outliers) / len(df) * 100:.2f}%)")
    df = df[~df.index.isin(outliers.index)].copy()

    # Sexo: 0/1/2 -> "NO CONSTA"/"Hombre"/"Mujer". El cuaderno no transforma esta columna (se
    # copia tal cual del CSV crudo con esos 3 codigos numericos); decision explicita del usuario
    # de relabelarla aqui, con el mismo criterio "NO CONSTA" ya usado para el resto de columnas
    # categoricas con codigos ausentes/desconocidos (Prov_trabajo_ppal, Act_econ_ppal...).
    df["03 SEXO"] = df["03 SEXO"].map({0: "NO CONSTA", 1: "Hombre", 2: "Mujer"}).fillna("NO CONSTA").astype("category")

    # Provincia de trabajo principal / Actividad economica principal (CNAE-2009, nivel Grupo)
    provincias_df = pd.read_csv(provincias_dict_path, sep=";", encoding="utf-8", dtype={"codigo": int})
    provincias_dict = dict(zip(provincias_df["codigo"], provincias_df["nombre"]))
    cnae_df = pd.read_csv(cnae_dict_path, sep=";", encoding="utf-8", dtype={"codigo": str})
    cnae_3d_dict = dict(zip(cnae_df["codigo"], cnae_df["descripcion"]))

    df["Prov_trabajo_ppal"] = df["Prov_trabajo_ppal"].map(provincias_dict).fillna("NO CONSTA").astype("category")

    # 05 PROV_NACIMIENTO / 06 PROV_1a_AFILIACION / Prov_fiscal_actual (2026-09-08, decision
    # explicita del usuario tras detectar la inconsistencia en la interfaz: solo
    # Prov_trabajo_ppal se decodificaba, estas 3 se quedaban como codigo INE crudo en vez de
    # nombre - filtro categorico ilegible). Mismo diccionario y mismo criterio "NO CONSTA" que
    # Prov_trabajo_ppal.
    df["05 PROV_NACIMIENTO"] = df["05 PROV_NACIMIENTO"].map(provincias_dict).fillna("NO CONSTA").astype("category")
    df["06 PROV_1a_AFILIACION"] = (
        df["06 PROV_1a_AFILIACION"].map(provincias_dict).fillna("NO CONSTA").astype("category")
    )
    df["Prov_fiscal_actual"] = df["Prov_fiscal_actual"].map(provincias_dict).fillna("NO CONSTA").astype("category")

    # v2.2: 07 DOMIC_RESID_HABITUAL pasa a categorica (codigo INE de municipio) y se anyade
    # prov_resid_habitual; edad_fallecimiento y ha_fallecido derivan de 08 FECHA_FALLECIMIENTO,
    # que se conserva tal cual (ver _codificar_domicilio y _columnas_fallecimiento).
    df["07 DOMIC_RESID_HABITUAL"], df["prov_resid_habitual"] = _codificar_domicilio(
        df["07 DOMIC_RESID_HABITUAL"], provincias_dict
    )
    df["edad_fallecimiento"], df["ha_fallecido"] = _columnas_fallecimiento(
        df["08 FECHA_FALLECIMIENTO"], df["02 FECHA_NACIMIENTO"]
    )

    # A diferencia de provincias_dict.csv (el nombre YA lleva el codigo, "01 ÁLAVA"),
    # cnae_3d_dict.csv trae codigo y descripcion en columnas separadas: hay que concatenarlos a
    # mano para que el resultado final sea "561 Restaurantes y puestos de comidas" (comprobado
    # contra el parquet de referencia: sin el codigo delante, difiere en pr�cticamente todas las
    # filas). No hace falta un caso especial para "000": extraer_grupo_c ya excluye el codigo 0
    # de la competicion por Act_econ_ppal (verificado con datos reales: 0 filas con "000"),
    # cualquier persona sin actividad economica valida llega aqui como NULL y cae en "NO CONSTA"
    # igual que el resto de categoricas.
    codigo_cnae = df["Act_econ_ppal"].astype("Int64").astype(str).str.zfill(3)
    df["Act_econ_ppal"] = (codigo_cnae + " " + codigo_cnae.map(cnae_3d_dict)).fillna("NO CONSTA").astype("category")

    # act_econ_ult_baja (2026-09-08, decision explicita del usuario): corregido para llevar la
    # MISMA codificacion que Act_econ_ppal ("561 Restaurantes y puestos de comidas", no solo el
    # codigo crudo) - de paso resuelve tambien el bug de tipo (float64/DOUBLE por el NaN del
    # left join, ver comentario de Causa_Baja_Trab_Ppal mas arriba), porque el resultado final
    # es VARCHAR igual que Act_econ_ppal.
    codigo_cnae_ult_baja = df["act_econ_ult_baja"].astype("Int64").astype(str).str.zfill(3)
    df["act_econ_ult_baja"] = (
        (codigo_cnae_ult_baja + " " + codigo_cnae_ult_baja.map(cnae_3d_dict))
        .fillna("NO CONSTA")
        .astype("category")
    )

    # Nacionalidad / Pais de nacimiento: mismo tratamiento que Prov_trabajo_ppal/Act_econ_ppal,
    # decodificando el codigo crudo ("N01") a "codigo nombre" ("N01 Alemania") con la tabla
    # oficial de la guia MCVL (1.004 NACIONALIDAD, identica para 1.009 PAIS_NACIMIENTO segun la
    # propia guia - etl/metadata/paises_dict.csv). Tiene que ir DESPUES de calcular
    # Nacionalidad_agrupado/Pais_nacim_agrupado (mas arriba), que clasifican por el codigo crudo
    # via PAISES_LATINOS/AFRICANOS/EU_UK/LEJANOS/ESPANA - decodificar antes rompería ese isin().
    paises_df = pd.read_csv(paises_dict_path, sep=";", encoding="utf-8", dtype={"codigo": str})
    paises_dict = dict(zip(paises_df["codigo"], paises_df["nombre"]))
    df["04 NACIONALIDAD"] = (
        (df["04 NACIONALIDAD"] + " " + df["04 NACIONALIDAD"].map(paises_dict))
        .fillna("NO CONSTA")
        .astype("category")
    )
    df["09 PAIS_NACIMIENTO"] = (
        (df["09 PAIS_NACIMIENTO"] + " " + df["09 PAIS_NACIMIENTO"].map(paises_dict))
        .fillna("NO CONSTA")
        .astype("category")
    )

    # Causa_Baja_Trab_Ppal / causa_ult_baja / causa_baja_mas_comun (2026-09-08, decision
    # explicita del usuario): bug detectado en la interfaz - estos codigos crudos llegan aqui
    # como float64 (DOUBLE en el Parquet), no como entero, porque el left join de
    # fusionar_grupo() introduce NaN para las 45.638 personas sin ningun episodio de Afiliacion,
    # y pandas sube de tipo entero a float en cuanto hay un NaN de por medio. La interfaz
    # clasifica "numerica" vs "categorica" por el tipo DuckDB (ver app/routers/colectivos.py,
    # TIPOS_NUMERICOS/TIPOS_CATEGORICOS), asi que estos codigos identificadores salian mal
    # clasificados como "numerica". Se convierten a texto y "NO CONSTA" + category, mismo
    # criterio que el resto de columnas categoricas de este fichero.
    # Decodificadas a "codigo descripcion" (2026-09-09, decision explicita del usuario, texto
    # de la guia MCVL acortado a mano por el usuario - ver etl/metadata/causa_baja_dict.csv,
    # p.ej. "51 Voluntaria" en vez de "DIMISION/BAJA VOLUNTARIA DEL TRABAJADOR"): mismo patron
    # que Act_econ_ppal/act_econ_ult_baja (codigo de 2 digitos con zfill, no 3 como CNAE).
    # causas_baja_distintas de-incluida (2026-09-08, revertido): SI es numerica de verdad (un
    # conteo), decision revertida por el usuario - se queda con su dtype natural (float64 con
    # NaN para quien no tiene episodios, igual que cualquier otra columna numerica del fichero).
    # act_econ_ult_baja NO va en este bloque: se decodifica aparte, igual que Act_econ_ppal (ver
    # mas arriba).
    causa_baja_df = pd.read_csv(causa_baja_dict_path, sep=";", encoding="utf-8", dtype={"codigo": str})
    causa_baja_dict = dict(zip(causa_baja_df["codigo"], causa_baja_df["descripcion"]))

    def _decodificar_causa_baja(serie):
        entero = serie.astype("Int64")
        codigo = entero.astype(str).str.zfill(2).mask(entero.isna())
        return (codigo + " " + codigo.map(causa_baja_dict)).fillna("NO CONSTA").astype("category")

    for columna in ["Causa_Baja_Trab_Ppal", "causa_ult_baja", "causa_baja_mas_comun"]:
        df[columna] = _decodificar_causa_baja(df[columna])

    # Reordenar y seleccionar las columnas finales. Las fiscales/de pension con el anyo en el
    # nombre (ver NUEVO_ORDEN) se insertan aqui, detectadas por patron sobre las columnas reales
    # de df - no estan en NUEVO_ORDEN con un anyo fijo, asi que se anyaden justo despues de
    # "t_tot_act_econ_ppal" (ingresos/retenciones) y "Pension_actual" (Cobra_pension), que es
    # donde vivian antes con el anyo escrito a mano.
    _ingresos_detectados, _cobra_pension_detectada = _columnas_fiscales_pension_presentes(df.columns)
    orden_final = []
    for c in NUEVO_ORDEN:
        orden_final.append(c)
        if c == "t_tot_act_econ_ppal":
            orden_final.extend(_ingresos_detectados)
        elif c == "Pension_actual":
            orden_final.extend(_cobra_pension_detectada)
    if _ingresos_detectados or _cobra_pension_detectada:
        print(f"Columnas fiscales/de pension detectadas por patron: {_ingresos_detectados + _cobra_pension_detectada}")

    columnas_presentes = [c for c in orden_final if c in df.columns]
    columnas_faltantes = [c for c in orden_final if c not in df.columns]
    if columnas_faltantes:
        print(f"AVISO: columnas del esquema objetivo que todavia no existen en df: {columnas_faltantes}")
    df = df[columnas_presentes]
    print(f"Grupo H: forma final {df.shape}")

    df.to_parquet(output_path, index=False)
    return df
