Detalle e historial de las decisiones del ETL, movido desde `CLAUDE.md` para no cargarlo en cada sesión.
Las reglas que no se deducen del código siguen resumidas en `CLAUDE.md`.

# Notas de implementación del ETL (`etl/`)

Transcripción de los cuadernos originales (`1. Lectura y optimiz tipos datos y
tipo archivo.ipynb`, `2. Construccion de Tabla Personas Extendida.ipynb`) a
`etl/optimize_types.py` y `etl/personas_extendida.py`, verificada tabla a
tabla contra los Parquet de referencia que generó el cuaderno en un PC con
más RAM. El motivo de reescribir en vez de solo automatizar el `input()` de
los cuadernos: **el PC donde corre habitualmente Claude Code en este
proyecto tiene solo ~4 GB de RAM**, muy por debajo de lo que el cuaderno
original asume, así que varias cosas que en el cuaderno funcionan tal cual
revientan aquí y hay que resolverlas de otra forma. Si algún compañero monta
este ETL en un PC con más memoria, todo esto sigue funcionando igual (son
soluciones más lentas pero no más frágiles), simplemente no haría falta.

- **`engine="pyarrow"` en `pd.read_csv` revienta por memoria** en los
  ficheros grandes (Afiliación, Pensiones) con `ArrowMemoryError`. Se usa el
  engine C por defecto de pandas — más lento, pero no revienta.
- **El engine C con `low_memory=True` (su valor por defecto) pierde valores**
  en columnas con tipos mixtos (p.ej. códigos como "00"/"01" mezclados con
  algún valor no numérico como "XX"): al trocear el fichero en bloques para
  inferir el tipo, el borde entre bloques puede perder datos si un bloque
  parece numérico y el siguiente no. `_leer_csv_seguro()` en
  `optimize_types.py` hace una lectura normal, detecta por el propio aviso
  de pandas (`DtypeWarning`) qué columnas concretas tuvieron el problema (casi
  siempre 0-2 de ~40), y solo esas se releen aparte forzando `dtype=str` —
  mucho más barato que forzarlo en todas las columnas.
- **Ni siquiera con eso cabe un fichero de Afiliación entero en RAM**
  (2-3 GB de texto por fichero, 4 ficheros). `leer_csv_por_trozos_a_parquet()`
  lee en trozos de 100.000 filas con `pd.read_csv(..., chunksize=...)` y
  cada trozo se escribe directo a un Parquet temporal (nunca más de un trozo
  en memoria a la vez). Los 4 temporales se combinan después con
  `combinar_temporales()`, que decide el tipo final de cada columna
  (entero más pequeño que quepa, o texto) con consultas DuckDB sobre el
  conjunto completo — sin cargar nada en pandas. La distinción pandas
  `category`/texto plano del cuaderno no se replica: no cambia ningún valor
  almacenado en el Parquet, y la app lee todo con DuckDB, no con pandas, así
  que es irrelevante para los datos.
- **La Fase 1 (afiliación extendida) tampoco cabe en pandas** para
  Afiliación (33M filas): revienta igual al cargar el Parquet ya optimizado
  entero. `fase1_afiliacion_extendida_duckdb()` calcula todo con DuckDB
  (funciones de ventana) en vez de pandas. Ojo con dos cosas si se toca:
  - Un `ORDER BY`/`PARTITION BY` que arrastre todas las columnas originales
    a través del cálculo agota el disco (comprobado: varios intentos con
    6-7 GB libres fallaron por "Espacio en disco insuficiente" antes de
    reescribirlo para que solo las columnas estrictamente necesarias pasen
    por el sort).
  - **Diferencia deliberada frente al cuaderno**, acordada explícitamente
    con el usuario: el cuaderno calcula `Alta_desplazada` con un
    `shift(-1)` GLOBAL (no por persona) sobre el DataFrame ordenado por
    `(01 IPF, 06 FECHA_ALTA_REAL_AFIL)`, así que en la última fila de cada
    persona "roba" la fecha de alta de la primera fila de la persona
    siguiente en el orden — y de ahí sale, en el resultado real del
    cuaderno, algún `DURACION_NO_EMPLEO` negativo sin sentido para esa fila.
    Replicar ese `shift` global exige un `ORDER BY` sobre las 33M filas
    completas, que agota el disco disponible en esta máquina. Aquí se usa
    `LEAD` particionado por `01 IPF` (mucho más barato, y da `NULL` en vez
    de un dato prestado de otra persona). Verificado el alcance exacto de
    la diferencia: afecta a `Alta_desplazada` en ~1,31M filas (una por
    persona, su último episodio) pero a `DURACION_NO_EMPLEO` solo en
    ~156.000 (el resto se sobrescribe igual en ambas versiones, vía la fila
    de `Baja_modificada` máxima). De las 50 columnas finales de
    `tabla_personas_extendida`, la única que podría arrastrar esta
    diferencia es `num_bajas_anual_promedio` (cuenta episodios de "no
    empleo" a partir de `DURACION_NO_EMPLEO`) — el resto de columnas no
    dependen de `Alta_desplazada` ni `DURACION_NO_EMPLEO`. Pendiente de
    revisar ese cálculo de todos modos (el usuario lo señaló como
    sospechoso incluso en la versión del cuaderno).
    **ACTUALIZACIÓN 2026-09-21 — todo lo anterior de este punto queda superado:**
    `Alta_desplazada` y `DURACION_NO_EMPLEO` ya NO se generan en Afiliación
    (decisión explícita del usuario). `DURACION_NO_EMPLEO` era el hueco hasta el
    alta siguiente por orden de fecha de alta, ignorando solapes de pluriempleo:
    el 11 % de lo que contaba como "baja" eran solapes y el 57 % huecos de un mes
    o menos. `num_episodios_baja` (Grupo C) pasa a contar bajas reales: mismos
    episodios de empleo que cuentan como alta (`DURACION_EMPLEO` distinta de 0 y
    no nula) con fecha de baja real distinta de 2099. Efecto medido sobre
    Afiliación completa: media de bajas por persona 13,5 -> 18,8; cambia el
    recuento en el 78 % de las personas; personas con bajas > altas 12.382 -> 0.
    Sin las ventanas `LEAD`/`ROW_NUMBER`, el Fase 1 también es más ligero.
- **pandas 3.0 usa microsegundos como resolución de fecha por defecto**
  (`datetime64[us]`), no nanosegundos como versiones anteriores. Los
  `Timedelta` (duraciones) que vienen de esas fechas se serializan a Parquet
  como enteros en microsegundos, no nanosegundos. Da un error silencioso de
  factor 1000 si se asume nanosegundos al reconstruir esos cálculos fuera de
  pandas (p.ej. en SQL) — pasó una vez aquí, detectado comparando sumas
  contra el Parquet de referencia.
- **Verificación de tablas grandes**: comparar dos Parquet de 30M+ filas con
  un `JOIN` (por `row_number()` o por lo que sea) satura el disco igual que
  el cálculo — un `JOIN` de dos relaciones de ese tamaño en una máquina así
  necesita volcar a disco. Para tablas grandes, comparar por agregados
  (`COUNT`, `MIN`, `MAX`, `SUM`) columna a columna es una verificación
  mucho más barata y en la práctica igual de fiable (si algo difiere en
  valores, casi siempre difiere en algún agregado). Para tablas pequeñas
  (Personas, Pensiones, Datos fiscales) sí es viable comparar fila a fila.
- **Estado real (2026-09-25)**: construidas y verificadas todas las tablas, incluidas Bases de cotización (CA/CP)
  y la Fase 3 completa (Grupos A-H) en `tabla_personas_extendida.parquet` (1.359.056 × 71). La Fase 3 completa la
  ejecuta el PC corporativo (15 GB); en el PC de desarrollo (~4 GB) solo se comparan y verifican los resultados.
  Al copiar un Parquet nuevo, renombrar antes el actual como copia y compararlo después columna a columna.
- **numexpr desactivado a propósito** (`pd.set_option("compute.use_numexpr", False)` en `personas_extendida.py` y
  `optimize_types.py`): con numexpr instalado (PC corporativo: numexpr 2.14.1 + numpy 1.26.4) pandas lo usa en
  operaciones de más de 1 millón de filas y fallaba de forma intermitente (2 de cada 200 repeticiones), dejando
  columnas enteras a 0 (`%T_total_DESEMPLEO`, `T_total_EMPLEO`, `Cotizado_anual_promedio` en regeneraciones
  distintas). Si vuelve a salir una columna a 0 en un PC nuevo, mirar `pandas.show_versions()` antes que el código.
- **Las ventanas de fusión tienen ORDEN TOTAL** (`ORDER BY fecha_alta, fecha_baja, rn` con `rn = file_row_number`)
  en `fase1b_tramos_fusionados_duckdb` y `_sql_categoria_ppal_fusionada`. Con `ORDER BY fecha_alta` a secas, los
  episodios que empiezan el mismo día quedaban en un orden no fijado y la fusión cambiaba entre ejecuciones
  idénticas (5.001 personas, hasta 44 años de diferencia). Cualquier ventana nueva sobre fechas de episodios
  necesita desempate total, y después hay que lanzar `etl/verificar_tramos.py`.
- **Acciones del ETL útiles** (`python etl\webui\ejecutar_paso.py --accion <x> --year 2024 --raw x --parquet <carpeta>`):
  `tramos` repite solo la fusión de tramos; `grupo_h` repite solo el Grupo H sobre `tabla_personas_extendida_preH.parquet`
  (unos 75 s); el H renombra por compatibilidad `ingresos_pension_AAAA` a `ingresos_pension_y_bajas_AAAA` si el
  parcial trae el nombre antiguo. Cambios que tocan los Grupos A-G exigen rehacer Personas y el botón final.
- **Criterios de datos vigentes** (detalle y cifras en `docs/esquema_datos_mcvl.md` y `CHANGELOG.md`): fecha de
  corte 1 de mayo del año siguiente; los fallecidos cortan tiempo de vida laboral, edad actual y último empleo en
  su fecha de fallecimiento; codificación común de provincias (53 La Palma, 56 Mar, 66 y 98 Extranjero); cualquier
  grupo de cotización fuera de 00-12 es `0 NO CONSTA`; edad mínima para trabajar 14 años hasta la cohorte 1965 y 16
  después, con excepción de menores autorizados (art. 6.4 ET); `causas_baja_distintas` eliminada;
  `ingresos_pension_y_bajas_AAAA` incluye bajas por incapacidad temporal (clave B del modelo 190).
- **Cola truncada en un TXT crudo (MCVL 2025, Pensiones, 2026-10-02)**: `MCVL2025PRESTAC_CDF.TXT` (1,4 GB) dio
  `pandas.errors.ParserError: Expected 46 fields ... saw 97` al construir el Parquet. Diagnosticado con el fichero
  real (descargado aparte, sin tocar el original): de 5.752.827 líneas, las 5.752.822 primeras eran regulares (256
  bytes cada una) y solo las **últimas 5 (2.037 bytes) tenían un ancho distinto** — un corte en la descarga o
  exportación del origen, no corrupción repartida por el fichero (ninguna comilla suelta ni anomalía en medio).
  `_leer_csv_seguro()` ahora detecta esto (`_separar_cola_corrupta()` en `etl/optimize_types.py`) y, si el
  `ParserError` viene de una cola corta y corrupta al final (como mucho 1.000 líneas o el 0,1% del fichero), avisa
  por consola y sigue con las líneas buenas, **sin tocar nunca el fichero de origen**. Si la anomalía está en medio
  o es más grande, no adivina: lanza `ColaCorruptaError` para que se revise a mano. Afecta a las 4 tablas que usan
  `process_table()` (Personas, Datos fiscales, Bases CP y Pensiones); Afiliación y Bases CA van por el camino
  troceado y no pasan por aquí. Probado con ficheros sintéticos (`tests/test_optimize_types.py`); la lectura
  completa del fichero real de 1,4 GB se valida en el PC corporativo (15 GB), no en el de desarrollo (~4 GB).
- **`.tmp/guia_mcvl.txt`** (texto extraído de la guía, no versionado): la tabla de clases de prestación (4.004) sale
  con las descripciones desplazadas una fila respecto a su código; en la guía original está bien (30 viudedad
  provisional, 31 viudedad general, 32 MUNPAL, 40 orfandad provisional, 41 orfandad simple, 42 orfandad absoluta,
  51-54 a favor de familiares). No usar ese texto para esa tabla; leer la guía no requiere pedir permiso.
