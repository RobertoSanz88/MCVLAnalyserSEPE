"""CSV/TXT crudos de la MCVL -> Parquet optimizado (un fichero por tabla).

Transcripcion de "1. Lectura y optimiz tipos datos y tipo archivo.ipynb":
misma logica de optimizacion de tipos, misma config de las 6 tablas. Unica
diferencia real: sin input() interactivo, todo por parametros de funcion,
para poder llamarse desde un CLI o mas adelante desde la UI con progreso.
"""

import glob
import os
import re
import time
import warnings

import numpy as np
import pandas as pd

# numexpr desactivado (2026-09-24): fallaba de forma intermitente en el PC corporativo dejando columnas
# enteras a 0 en operaciones de mas de 1 millon de filas. Ver el comentario de etl/personas_extendida.py.
pd.set_option("compute.use_numexpr", False)

from etl.recursos import chunksize_optimo

# view_name -> (nombre, plantillas de fichero, columnas, parquet de salida)
TABLE_CONFIGS = {
    "1": {
        "nombre": "Afiliación",
        "file_templates": [f"MCVL{{year}}AFILIAD{i}_CDF" for i in range(1, 5)],
        "column_names": [
            "01 IPF", "02 REG_COTIZACION", "03 GRU_COTIZACION", "04 TIPO_CONTRATO", "05 COEF_T_PARCIAL",
            "06 FECHA_ALTA_REAL_AFIL", "07 FECHA_BAJA_REAL_AFIL",
            "08 CAUSA_BAJA_AFIL", "09 MINUSV_ALTA_AFIL", "10 CCC_2", "11 DOMIC_CC_2", "12 CNAE2009_CC",
            "13 NUM_TRABAJS_CC_2", "14 FECHA_ALTA_1ER_TRABAJ_CC",
            "15 TRL", "16 COLEC_ESPECIAL_CC", "17 EMPLEADOR_TIPO_ID", "18 EMPLEADOR_LETRA_NIF",
            "19 ID_ANONIMADO_PAGADORA", "20 CCC_PPAL", "21 PROVINCIA_DOMIC_CC_PPAL",
            "22 FECHA_MODIF_TIPO_CONTR_INI_O_COEF_T_PARCIAL_INI", "23 TIPO_CONTRATO_INI", "24 COEF_T_PARCIAL_INI",
            "25 FECHA_MODIF_TIPO_CONTR_2_O_COEF_T_PARCIAL_2",
            "26 TIPO_CONTRATO_2", "27 COEF_T_PARCIAL_2", "28 FECHA_MODIF_GRUPO_COTIZ_INI", "29 GRUPO_COTIZ_INI",
            "30 CNAE1993_CC", "31 SETA", "32 TR CON OTRAS ENT_O_AUT",
            "33 FECHA_EFECTO_ALTA_AFIL", "34 FECHA_EFECTO_BAJA_AFIL", "35 CONVENIO_COLECTIVO_1",
            "36 CONVENIO_COLECTIVO_2", "37 NUM_TRABAJS_CC_PPAL",
        ],
        "output_parquet": "tabla_afiliacion_optimizada.parquet",
    },
    "2": {
        "nombre": "Personas",
        "file_templates": ["MCVL{year}PERSONAL_CDF"],
        "column_names": [
            "01 IPF", "02 FECHA_NACIMIENTO", "03 SEXO", "04 NACIONALIDAD", "05 PROV_NACIMIENTO",
            "06 PROV_1a_AFILIACION", "07 DOMIC_RESID_HABITUAL",
            "08 FECHA_FALLECIMIENTO", "09 PAIS_NACIMIENTO", "10 NIVEL_EDUCATIVO",
        ],
        "output_parquet": "tabla_personas_optimizada.parquet",
    },
    "3": {
        "nombre": "Bases de cotización cuenta ajena",
        "file_templates": [f"MCVL{{year}}COTIZA{i}_CDF" for i in range(1, 13)],
        "column_names": [
            "01 IPF", "02 CCC_2", "03 ANO_COTIZACION", "04 BCC_ENERO", "05 BCC_FEBRERO", "06 BCC_MARZO",
            "07 BCC_ABRIL", "08 BCC_MAYO", "09 BCC_JUNIO", "10 BCC_JULIO", "11 BCC_AGOSTO",
            "12 BCC_SEPTIEMBRE", "13 BCC_OCTUBRE", "14 BCC_NOVIEMBRE", "15 BCC_DICIEMBRE", "16 BCC_ANUAL_TOTAL",
        ],
        "output_parquet": "tabla_bases_cotizacion_CA_optimizada.parquet",
    },
    "4": {
        "nombre": "Bases de cotización cuenta propia",
        "file_templates": ["MCVL{year}COTIZA13_CDF"],
        "column_names": [
            "01 IPF", "02 CCC_2", "03 ANO_COTIZACION", "04 BCC_ENERO", "05 BCC_FEBRERO", "06 BCC_MARZO",
            "07 BCC_ABRIL", "08 BCC_MAYO", "09 BCC_JUNIO", "10 BCC_JULIO", "11 BCC_AGOSTO",
            "12 BCC_SEPTIEMBRE", "13 BCC_OCTUBRE", "14 BCC_NOVIEMBRE", "15 BCC_DICIEMBRE", "16 BCC_ANUAL_TOTAL",
        ],
        "output_parquet": "tabla_bases_cotizacion_CP_optimizada.parquet",
    },
    "5": {
        "nombre": "Pensiones",
        "file_templates": ["MCVL{year}PRESTAC_CDF"],
        "column_names": [
            "01 IPF", "02 ANO_DATO", "03 ID_PRESTACION", "04 CLASE_PRESTACION", "05 SIT_SUJETO_CAUSANTE",
            "06 GRADO_INCAPACIDAD",
            "07 FECHA_MINUSVALIA", "08 NORMA_SOVI", "09 CLASE_MINIMO", "10 REGIMEN_PENSION",
            "11 FECHA_EFEC_ECON_PENSION", "12 BASE_REGULADORA (BR)",
            "13 PORC_APLICADO_A_BR", "14 ANOS_BONIFICADOS", "15 ANOS_COTIZADOS_JUBILACION",
            "16 IMPORTE_MES_PENSION_EFEC",
            "17 IMPORTE_MES_REVALORIZACION", "18 IMPORTE_MES_COMPL_GARANTIA_MIN",
            "19 IMPORTE_MES_OTROS COMPL", "20 IMPORTE_MES_TOTAL_PRESTACION",
            "21 SIT_PRESTACION (CAUSA DE BAJA)", "22 FECHA_SIT_PRESTACION", "23 PROVINCIA_GESTION_PRESTACION",
            "25 PRORRATA_CONVENIO_INTERN",
            "26 PRORRATA_DIVORCIO", "27 COEFICIENTE_REDUCTOR_TOTAL", "28 TIPO_SIT_JUBILACION",
            "29 COEF_PARCIALIDAD (JUBILACION)",
            "30 PREST_VITALICIA (ORFANDAD Y VIUDEDAD)", "31 CONCURRENCIA_CON_PRESTACION_AJENA",
            "35 ANO_NACIM_CAUSANTE_PENSION_SUPERVIVENCIA",
            "36 PENSION_LIMITADA", "37 COEF_REDUCTOR_LIMITE_MAXIMO", "38 COMPATIBILIDAD_JUBILACION_TRABAJO",
            "39 FECHA_ORDINARIA_JUBILACION",
            "40 PERIODO_COTIZ_EDAD_ORD_JUBIL", "41 PERIODO_COTIZACION", "42 PORCENTAJE_POR ANOS_COTIZADOS",
            "43 IMPORTE_MES_TOTAL_COMPL_MATERNIDAD",
            "44 PORCENTAJE_COMPL_MATERNIDAD", "45 COEFICIENTE_GLOBAL_PARCIALIDAD",
            "46 IMPORTE MES_COMPL_BRECHA_GENERO",
            "47 NUM_HIJOS_EFEC_COMPL_BRECHA_GENERO", "48 CLAVE_COMPL_ESPECIAL", "49 CLAVE_PREST_ESPECIAL",
            "50 CLAVE_COLECTIVO_ESPECIAL",
        ],
        "output_parquet": "tabla_pensiones_optimizada.parquet",
    },
    "6": {
        "nombre": "Datos fiscales",
        "file_templates": ["MCVL{year}FISCAL_CDF"],
        "column_names": [
            "01 IPF", "02 LETRA_NIF_ENT_PAGADORA", "03 ID_ENT_PAGADORA", "04 PROVINCIA", "05 CLAVE_PERCEPCION",
            "06 SUBCLAVE_PERCEPCION",
            "07 PERC_INTEGRA_DIN_NO_DERIVADA_IL", "08 PERC_INTEGRA_DIN_DERIVADA_IL",
            "09 RETENC_SOBRE_P_DINER_NO_DERIV_IL",
            "10 RETENC_SOBRE_P_DINER_DERIV_IL", "11 PERC_ESPECIE_NO_DERIV_IL", "12 PERC_ESPECIE_DERIV_IL",
            "13 INGR_CUENTA_EFEC_PERCEP_ESPECIE_NO_DERIV_IL",
            "14 INGR_CUENTA_EFEC_PERCEP_ESPECIE_DERIV_IL", "15 INGR_CUENTA_REPERCUTIDOS_PER_ESPECIE_NO_DERIV_IL",
            "16 INGR_CUENTA_REPERCUTIDOS_PER_ESPECIE_DERIV_IL", "17 CEUTA_O_MELILLA", "18 EJERCICIO_NACIMIENTO",
            "19 SIT_FAMILIAR", "20 DISCAPACIDAD",
            "21 TIPO_CONTRATO_O_RELACION", "22 MOVILIDAD_GEOGRAFICA_3", "23 REDUCCIONES", "24 GASTOS_DEDUCIBLES",
            "25 PENSIONES_COMPENSATORIAS",
            "26 ANUALIDADES_ALIMENTOS", "27 DESCENDIENTES <3_ANOS", "28 DESCENDIENTES <3_ANOS_POR_ENTERO",
            "29 RESTO_DESCENDIENTES",
            "30 RESTO_DESCENDIENTES_POR_ENTERO", "31 DESC_DISC_C_MIN=>33%Y<65%",
            "32 DESC_DISC_C_MIN=>33%Y<65%_POR_ENTERO",
            "33 DESC_DISC_C_MIN=>33%Y<65%_C_MOV_REDUCIDA", "34 DESC_DISC_C_MIN=>33%Y<65%_C_MOV_RED_P_ENTERO",
            "35 DESC_DISC_C_MIN>=65%",
            "36 DESC_DISC_C_MIN>=65%_POR_ENTERO", "37 NUM_TOTAL_DESCENDIENTES", "38 ASCENDIENTES<75_ANOS",
            "39 ASCENDIENTES<75_ANOS_POR_ENTERO",
            "40 ASCENDIENTES>=75_ANOS", "41 ASCENDIENTES>=75_ANOS_POR_ENTERO", "42 ASC_DISC_C_MIN=>33%Y<65%",
            "43 ASC_DISC_C_MIN=>33%Y<65%_POR_ENTERO",
            "44 ASC_DISC_C_MIN=>33%Y<65%_C_MOV_REDUCIDA", "45 ASC_DISC_C_MIN=>33%Y<65%_C_MOV_RED_P_ENTERO",
            "46 ASC_DISC_C_MIN>=65%",
            "47 ASC_DISC_C_MIN>=65%_POR_ENTERO", "48 NUM_TOTAL_ASCENDIENTES",
        ],
        "output_parquet": "tabla_datos_fiscales_optimizada.parquet",
    },
}


def _parsear_columna_fecha(df, col):
    """Convierte una columna de fecha (AAAAMM o AAAAMMDD, como entero) a datetime real.
    Modifica df in-place. No depende de nada mas del DataFrame, es seguro llamarla por
    fichero suelto sin esperar al resto de columnas."""
    serie = df[col]
    if pd.api.types.is_datetime64_any_dtype(serie):
        return  # ya optimizada (permite llamar dos veces sin corromper fechas)
    try:
        serie_int = pd.to_numeric(serie, errors="coerce").astype("Int64")
        serie_str = serie_int.astype(str).mask(serie_int.isna())
        longitudes = serie_str[serie_str != "0"].dropna().str.len()
        formato = "%Y%m%d" if not longitudes.empty and longitudes.mode().iloc[0] == 8 else "%Y%m"
        df[col] = pd.to_datetime(serie_str, format=formato, errors="coerce")
    except Exception as e:
        print(f"  Aviso: no se pudo convertir '{col}' a fecha ({e}). Se deja tal cual.")


def optimize_dataframe_types(
    df,
    columnas_fecha_completa=("02 FECHA_NACIMIENTO", "06 FECHA_ALTA_REAL_AFIL", "07 FECHA_BAJA_REAL_AFIL"),
):
    """
    Optimiza los tipos de datos de un DataFrame, columna a columna. Objetivo: minimizar el
    TIEMPO DE CARGA (no la memoria) - se prioriza sobre el tamano en RAM.

    - Solo las columnas listadas en 'columnas_fecha_completa' se convierten a datetime real: son
      las unicas que de verdad se usan como fecha (con .dt, restas, comparaciones...) mas adelante.
      El resto de columnas cuyo nombre contiene 'FECHA' se dejan como el entero original (AAAAMM o
      AAAAMMDD) - parsear a datetime es lo mas caro de esta funcion y no compensa si la columna
      nunca se usa como fecha.
    - Columnas de texto ('object' o el StringDtype de pandas >= 3.0): se limpian espacios y
      strings vacios, se intenta convertir a numerico y, si no se puede, a 'category' SOLO
      cuando la cardinalidad es baja tanto en relativo (<50% de valores distintos) como en
      absoluto (<= MAX_CATEGORIAS valores distintos). Columnas casi-unicas (p.ej. CCC_2, con
      millones de codigos de empresa distintos) se dejan como texto plano: categorizarlas apenas
      ahorra memoria relativa y en cambio hace la lectura mucho mas lenta, porque hay que
      reconstruir un diccionario categorico enorme cada vez que se lee el parquet.
    - Columnas enteras: se reducen al tipo entero mas pequeno que quepa (version 'nullable' si la
      columna tiene NA, para no romper con "cannot convert NA to integer").
    - Columnas float cuyos valores no nulos son todos enteros (huecos por NaN): se convierten al
      tipo entero 'nullable' mas pequeno que quepa (Int8/16/32/64).
    - Resto de columnas float: se reducen a float32 si el rango lo permite.
    """
    MAX_CATEGORIAS = 10_000

    def es_texto(serie):
        return serie.dtype == "object" or isinstance(serie.dtype, pd.StringDtype)

    for col in df.columns:
        serie = df[col]

        if col in columnas_fecha_completa:
            _parsear_columna_fecha(df, col)
            continue

        if es_texto(serie):
            serie = serie.str.strip()
            serie = serie.replace("", np.nan)
            try:
                serie = pd.to_numeric(serie)
            except (ValueError, TypeError):
                pass
            if es_texto(serie):
                num_unique_values = serie.nunique()
                num_total_values = len(serie)
                cardinalidad_baja = num_total_values > 0 and num_unique_values / num_total_values < 0.5
                pocas_categorias = num_unique_values <= MAX_CATEGORIAS
                if cardinalidad_baja and pocas_categorias:
                    serie = serie.astype("category")
                df[col] = serie
                continue
            df[col] = serie

        if pd.api.types.is_integer_dtype(df[col]):
            # OJO: is_integer_dtype tambien es True para los enteros 'nullable' de pandas
            # (Int8/16/32/64), que SI pueden tener NA. Si se intenta convertir una columna asi a
            # un entero numpy normal (np.int8, etc.) sin nulos, revienta con
            # "ValueError: cannot convert NA to integer". Por eso hay que elegir la version
            # 'nullable' cuando la columna todavia tiene NA (p.ej. porque ya paso antes por la
            # rama de floats, o porque esta funcion se llama dos veces sobre el mismo DataFrame).
            tiene_nulos = df[col].isna().any()
            minimo, maximo = df[col].min(), df[col].max()
            if minimo >= np.iinfo(np.int8).min and maximo <= np.iinfo(np.int8).max:
                df[col] = df[col].astype("Int8" if tiene_nulos else np.int8)
            elif minimo >= np.iinfo(np.int16).min and maximo <= np.iinfo(np.int16).max:
                df[col] = df[col].astype("Int16" if tiene_nulos else np.int16)
            elif minimo >= np.iinfo(np.int32).min and maximo <= np.iinfo(np.int32).max:
                df[col] = df[col].astype("Int32" if tiene_nulos else np.int32)
            else:
                df[col] = df[col].astype("Int64" if tiene_nulos else np.int64)
        elif pd.api.types.is_float_dtype(df[col]):
            valores_no_nulos = df[col].dropna()
            if not valores_no_nulos.empty and (valores_no_nulos % 1 == 0).all():
                minimo, maximo = valores_no_nulos.min(), valores_no_nulos.max()
                if minimo >= -128 and maximo <= 127:
                    df[col] = df[col].astype("Int8")
                elif minimo >= -32768 and maximo <= 32767:
                    df[col] = df[col].astype("Int16")
                elif minimo >= -2147483648 and maximo <= 2147483647:
                    df[col] = df[col].astype("Int32")
                else:
                    df[col] = df[col].astype("Int64")
            elif not valores_no_nulos.empty and valores_no_nulos.min() >= np.finfo(np.float32).min and valores_no_nulos.max() <= np.finfo(np.float32).max:
                df[col] = df[col].astype(np.float32)
    return df


def _leer_csv_seguro(ruta, column_names):
    """Lee un CSV/TXT con el engine C y dtype nativo (rapido, poca memoria: la mayoria de
    columnas son numericas y se leen directamente como tal).

    El engine C con low_memory=True (su valor por defecto) infiere el tipo de cada columna
    troceando el fichero en bloques internos; si una columna es numerica en un bloque y no en
    otro (p.ej. codigos "00"/"01" que en algun bloque no ve ningun valor no-numerico como 'XX'),
    los valores del borde entre bloques se pierden como NaN al reconciliarlos. Forzar dtype=str
    en TODAS las columnas lo evita pero es mucho mas lento y, en una maquina con poca RAM, puede
    agotar la memoria en ficheros grandes.

    En vez de eso: se lee una vez con dtype nativo: pandas avisa (DtypeWarning) exactamente de
    que columnas tuvieron tipos mixtos. Solo esas pocas columnas (normalmente 0-2 de las ~40-50
    de una tabla) se releen aparte forzando dtype=str, mucho mas barato que forzarlo en todas.
    """
    with warnings.catch_warnings(record=True) as capturados:
        warnings.simplefilter("always", pd.errors.DtypeWarning)
        df = pd.read_csv(ruta, header=None, names=column_names, sep=";")

    columnas_afectadas = []
    for w in capturados:
        if issubclass(w.category, pd.errors.DtypeWarning):
            match = re.search(r"Columns \(([^)]+)\) have mixed types", str(w.message))
            if match:
                for item in match.group(1).split(","):
                    columnas_afectadas.append(item.split(":", 1)[1].strip())

    if columnas_afectadas:
        print(f"  Tipos mixtos detectados, releyendo como texto: {columnas_afectadas}")
        df_texto = pd.read_csv(
            ruta, header=None, names=column_names, sep=";",
            usecols=columnas_afectadas, dtype=str,
        )
        for col in columnas_afectadas:
            df[col] = df_texto[col].values
        del df_texto

    return df


def leer_csv_por_trozos_a_parquet(
    ruta, column_names, columnas_fecha_completa, temp_path, chunksize=None,
):
    """Lee un CSV/TXT grande en trozos de `chunksize` filas y los va escribiendo directamente
    a un Parquet temporal, sin mantener nunca mas de un trozo en memoria a la vez.

    Para tablas como Afiliacion (varios GB de texto, varios ficheros), ni siquiera el engine C
    con dtype nativo cabe en una maquina con poca RAM (comprobado con datos reales: un solo
    fichero de 2,3 GB ya se estimaba en ~3,5 GB de DataFrame antes de optimizar, mas que la RAM
    total disponible). Cada trozo se lee con dtype=object (sin ambiguedad de tipos posible dentro
    de un trozo pequenyo, a diferencia del chunking interno del engine C con low_memory=True) y
    se parsean solo las columnas de fecha, que no dependen de nada global. El resto de columnas
    se decide (entero mas pequenyo que quepa, categorica o texto) en un paso posterior que ve el
    fichero completo (o los varios ficheros de la tabla juntos) via DuckDB, sin cargarlo en pandas.

    `chunksize=None` (por defecto) deja que `chunksize_optimo()` decida segun la RAM real del
    equipo (ver etl/recursos.py): trozos mas grandes en una maquina con RAM de sobra, menos
    vueltas y mas rapido, sin cambiar la logica.
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    if chunksize is None:
        chunksize = chunksize_optimo()

    writer = None
    total_filas = 0
    t0 = time.time()
    try:
        for i, chunk in enumerate(
            pd.read_csv(
                ruta, header=None, names=column_names, sep=";",
                dtype=object, chunksize=chunksize,
            )
        ):
            for col in columnas_fecha_completa:
                _parsear_columna_fecha(chunk, col)

            tabla = pa.Table.from_pandas(chunk, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(temp_path, tabla.schema)
            writer.write_table(tabla)

            total_filas += len(chunk)
            if i % 10 == 0:
                print(f"    trozo {i}: {total_filas:,} filas leidas ({time.time() - t0:.0f} s)")
            del chunk, tabla
    finally:
        if writer is not None:
            writer.close()

    print(f"  Total: {total_filas:,} filas en {time.time() - t0:.0f} s -> {temp_path}")
    return total_filas


def _quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def combinar_temporales(con, temp_paths, output_path):
    """A partir de N parquet temporales de una misma tabla (todas las columnas como texto
    excepto las fechas, ya parseadas por leer_csv_por_trozos_a_parquet), decide el tipo final de
    cada columna con datos del conjunto COMPLETO -via DuckDB, sin cargar nada en pandas- y
    escribe el parquet final combinado.

    Mismo criterio que optimize_dataframe_types (mismos umbrales de entero/float), pero calculado
    sobre la union de todos los ficheros en vez de sobre un DataFrame en memoria: intenta
    convertir cada columna a numerico completo; si lo consigue, elige el entero mas pequenyo que
    quepa (o float si hay decimales); si no, se deja como texto. La distincion pandas
    category/texto no se replica aqui porque no cambia ningun valor almacenado en el Parquet (ver
    conversacion con el usuario: la app lee todo con DuckDB, no con pandas, asi que esa distincion
    es irrelevante para los datos).
    """
    lista_sql = ", ".join(f"'{p}'" for p in temp_paths)
    con.execute(f"CREATE OR REPLACE VIEW _todos AS SELECT * FROM read_parquet([{lista_sql}])")

    esquema = con.execute("DESCRIBE _todos").fetchall()

    partes_select = []
    for nombre, tipo, *_ in esquema:
        col = _quote(nombre)
        if tipo.startswith("TIMESTAMP"):
            partes_select.append(col)
            continue

        limpio = f"NULLIF(TRIM({col}), '')"
        no_numericos = con.execute(
            f"SELECT COUNT(*) FROM _todos WHERE {limpio} IS NOT NULL AND TRY_CAST({limpio} AS DOUBLE) IS NULL"
        ).fetchone()[0]

        if no_numericos > 0:
            partes_select.append(f"{limpio} AS {col}")
            continue

        minimo, maximo, no_enteros = con.execute(
            f"""
            SELECT MIN(v), MAX(v), COUNT(*) FILTER (WHERE v != ROUND(v))
            FROM (SELECT TRY_CAST({limpio} AS DOUBLE) AS v FROM _todos) sub
            WHERE v IS NOT NULL
            """
        ).fetchone()

        if minimo is None:
            partes_select.append(f"CAST(NULL AS INTEGER) AS {col}")
        elif no_enteros == 0:
            if minimo >= -128 and maximo <= 127:
                tipo_final = "TINYINT"
            elif minimo >= -32768 and maximo <= 32767:
                tipo_final = "SMALLINT"
            elif minimo >= -2147483648 and maximo <= 2147483647:
                tipo_final = "INTEGER"
            else:
                tipo_final = "BIGINT"
            partes_select.append(f"TRY_CAST({limpio} AS {tipo_final}) AS {col}")
        else:
            tipo_final = "FLOAT" if (-3.4e38 <= minimo and maximo <= 3.4e38) else "DOUBLE"
            partes_select.append(f"TRY_CAST({limpio} AS {tipo_final}) AS {col}")

    select_list = ", ".join(partes_select)
    con.execute(f"COPY (SELECT {select_list} FROM _todos) TO '{output_path}' (FORMAT PARQUET)")
    return output_path


def encontrar_fichero(raw_root, nombre_esperado):
    """Busca de forma recursiva bajo raw_root un fichero .txt/.TXT cuyo nombre (sin extension)
    coincida exactamente con nombre_esperado. Devuelve la ruta encontrada o None."""
    candidatos = glob.glob(os.path.join(raw_root, "**", nombre_esperado + ".*"), recursive=True)
    candidatos = [c for c in candidatos if os.path.splitext(c)[1].lower() == ".txt"]
    if len(candidatos) == 0:
        return None
    if len(candidatos) > 1:
        print(f"  Aviso: varios candidatos para '{nombre_esperado}', se usa el primero: {candidatos}")
    return candidatos[0]


def process_table(config, year, raw_root, parquet_folder):
    """Lee los TXT brutos de una tabla, optimiza tipos y guarda el Parquet resultado.
    Devuelve el DataFrame final (llamador decide si lo libera de memoria)."""
    separador = "=" * 80
    print(f"\n{separador}\nProcesando tabla: {config['nombre']}\n{separador}")

    rutas, faltantes = [], []
    for template in config["file_templates"]:
        nombre_esperado = template.format(year=year)
        ruta = encontrar_fichero(raw_root, nombre_esperado)
        (rutas if ruta else faltantes).append(ruta or nombre_esperado)

    if faltantes:
        raise FileNotFoundError(
            f"No se han encontrado estos ficheros dentro de '{raw_root}': {faltantes}. "
            f"Revisa que la carpeta de {year} contenga todos los ficheros esperados."
        )

    tamano_total_mb = sum(os.path.getsize(r) for r in rutas) / (1024**2)
    print(f"Tamaño total: {tamano_total_mb:,.0f} MB en {len(rutas)} fichero(s)")

    column_names = config["column_names"]
    dfs = []

    for i, ruta in enumerate(rutas):
        print(f"\nProcesando archivo: {os.path.basename(ruta)} ({i+1}/{len(rutas)})")

        primera_linea = pd.read_csv(ruta, header=None, sep=";", nrows=1)
        num_columnas_real = primera_linea.shape[1]
        if num_columnas_real != len(column_names):
            raise ValueError(
                f"El fichero '{ruta}' tiene {num_columnas_real} columnas pero se esperaban "
                f"{len(column_names)} para la tabla '{config['nombre']}'. Revisa el layout de {year}."
            )

        # El cuaderno original usaba engine="pyarrow" (mas rapido), pero en una maquina con poca
        # RAM (~4 GB en este equipo) revienta con "Out of memory" al leer los ficheros grandes
        # (Afiliacion, Pensiones). El engine C por defecto de pandas es mas lento pero no falla.
        # Ver _leer_csv_seguro() para el motivo del segundo paso (releer solo las columnas con
        # tipos mixtos en vez de forzar dtype=str en todas: mucho mas rapido y con menos memoria).
        print("  Leyendo (engine=c)...")
        t0 = time.time()
        df_temp = _leer_csv_seguro(ruta, column_names)
        print(f"  Lectura completada en {time.time() - t0:.1f} s")
        memoria_antes = df_temp.memory_usage(deep=True).sum() / (1024**2)
        print(f"  Filas: {df_temp.shape[0]:,} | Memoria antes de optimizar: {memoria_antes:.2f} MB")

        df_optimizado = optimize_dataframe_types(df_temp)
        memoria_despues = df_optimizado.memory_usage(deep=True).sum() / (1024**2)
        print(f"  Memoria después de optimizar: {memoria_despues:.2f} MB (reducción: {memoria_antes - memoria_despues:.2f} MB)")

        dfs.append(df_optimizado)

    print("\nConcatenando todos los ficheros...")
    df_final = pd.concat(dfs, ignore_index=True) if len(dfs) > 1 else dfs[0]
    del dfs

    if len(rutas) > 1:
        # Cada fichero se optimizo por separado antes de concatenar: si una columna de texto se
        # convirtio a 'category' en cada trozo, las categorias de cada trozo no coinciden entre si
        # (cada uno vio un subconjunto de valores distinto), y pandas revierte la columna a texto
        # plano al concatenar categorias distintas entre si. Se reoptimiza aqui sobre el DataFrame
        # ya concatenado para que la cardinalidad se calcule sobre el total real de filas.
        print("Reoptimizando tipos tras la concatenacion (necesario cuando hay varios ficheros)...")
        df_final = optimize_dataframe_types(df_final)

    print(f"DataFrame final: {df_final.shape[0]:,} filas, {df_final.shape[1]} columnas")

    os.makedirs(parquet_folder, exist_ok=True)
    output_path = os.path.join(parquet_folder, config["output_parquet"])
    print(f"\nGuardando en: {output_path}")
    df_final.to_parquet(output_path, index=False)
    print("Guardado completado.")

    return df_final


def process_table_grande(config, year, raw_root, parquet_folder, con, chunksize=None, borrar_txt=False):
    """Version de process_table() para tablas multi-fichero que no caben enteras en RAM
    (Afiliacion, Bases de cotizacion cuenta ajena): cada fichero se lee por trozos a un
    parquet temporal (leer_csv_por_trozos_a_parquet) y los temporales se combinan con DuckDB
    (combinar_temporales), que decide el tipo final de cada columna sobre el conjunto
    completo sin cargar nada en pandas.

    borrar_txt=False por defecto: en una maquina con RAM/disco de sobra no hace falta borrar
    los TXT de origen para poder seguir. Se activa explicitamente solo si hace falta.

    chunksize=None deja que leer_csv_por_trozos_a_parquet decida segun la RAM real del equipo.
    """
    separador = "=" * 80
    print(f"\n{separador}\nProcesando tabla (por trozos): {config['nombre']}\n{separador}")

    rutas, faltantes = [], []
    for template in config["file_templates"]:
        nombre_esperado = template.format(year=year)
        ruta = encontrar_fichero(raw_root, nombre_esperado)
        (rutas if ruta else faltantes).append(ruta or nombre_esperado)
    if faltantes:
        raise FileNotFoundError(
            f"No se han encontrado estos ficheros dentro de '{raw_root}': {faltantes}. "
            f"Revisa que la carpeta de {year} contenga todos los ficheros esperados."
        )

    os.makedirs(parquet_folder, exist_ok=True)
    columnas_fecha = tuple(
        c for c in ("06 FECHA_ALTA_REAL_AFIL", "07 FECHA_BAJA_REAL_AFIL") if c in config["column_names"]
    )

    temps = []
    for i, ruta in enumerate(rutas):
        print(f"\n=== Fichero {i + 1}/{len(rutas)}: {os.path.basename(ruta)} ===")
        temp_path = os.path.join(parquet_folder, f"_tmp_{config['output_parquet']}_part{i}.parquet")
        leer_csv_por_trozos_a_parquet(ruta, config["column_names"], columnas_fecha, temp_path, chunksize=chunksize)
        temps.append(temp_path)
        if borrar_txt:
            os.remove(ruta)
            print(f"  TXT borrado: {ruta}")

    print("\nCombinando temporales con DuckDB...")
    output_path = os.path.join(parquet_folder, config["output_parquet"])
    combinar_temporales(con, temps, output_path)
    for t in temps:
        os.remove(t)
    print(f"Guardado: {output_path}")
    return output_path
