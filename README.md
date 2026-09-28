# MCVLAnalyserSEPE

Aplicación web **local** para analizar la MCVL (Muestra Continua de Vidas Laborales) sin depender de
Excel, que no aguanta el volumen de las tablas (Afiliación, unos 33 millones de filas). La usan los
analistas del Observatorio de las Ocupaciones (SEPE), cada uno en su PC y contra su propia copia local
de los datos. **No hay servidor compartido, ni autenticación, ni nube**: los datos no salen del PC.

## Dos módulos separados

| Módulo | Quién lo usa | Qué hace |
|---|---|---|
| `etl/` | Solo quien construye los datos (uso restringido) | Convierte los ficheros crudos de la MCVL en Parquet. Son scripts de línea de comandos y una pequeña interfaz web local (`etl/webui/`), no forman parte de la app. |
| `app/` | Todo el equipo | App FastAPI que **solo lee** los Parquet ya construidos con DuckDB. Frontend HTML + Jinja2 + JavaScript sin *build* + Plotly. |

**Regla dura:** los Parquet **nunca** viajan por git ni GitHub (la MCVL es dato restringido de la
entidad). `data/`, `parquet/` y `*.parquet` están en `.gitignore`. El repositorio contiene solo código.
Los Parquet se distribuyen por el canal interno del Observatorio; el `.exe` (sin datos) por GitHub Releases.

## Qué hay en la app (v2.2)

1. **Selección de colectivo y filtro global**, con un único interruptor que se aplica a todas las pestañas.
2. **Análisis de personas:** estadísticas agregadas del colectivo — numéricas (media, mediana, desviación) y
   categóricas (recuento y porcentaje por valor) — en tabla o en gráfico, con varios tipos de gráfico
   disponibles para las categóricas (barras agrupadas, barras apiladas, tarta...).
3. **Trayectorias de personas:** evolución agregada por año de variables de tres tablas (bases de
   cotización, pensiones y afiliación) a lo largo de la vida laboral del colectivo, sobre dos ejes de tiempo
   a elegir: edad o años desde el inicio de la vida laboral.
4. **Trayectorias individuales:** buscar por IPF y ver la línea temporal de episodios laborales de una
   persona — altas y bajas, empresas, tipo de contrato, régimen.
5. **Cruce de variables:** cruzar dos variables cualesquiera del colectivo (densidad, tabla de
   contingencia, diagrama de cajas...) para explorar su relación.
6. **Varias muestras de la MCVL, una a la vez.** El botón de tres rayas de la cabecera abre el menú: una fila
   por muestra añadida (2024, 2023...), y las opciones Añadir, Editar y Eliminar muestra. Elegir otra muestra
   recarga la aplicación en su estado inicial (sin colectivo ni filtros de la muestra anterior). Eliminar solo
   quita la muestra de la lista: nunca borra ficheros.
7. **Descripción de variables** (Personas extendida y Afiliación extendida, con búsqueda y con una
   explicación de cómo se construye cada una) y **Acerca de** (este texto, con las reglas de consistencia y
   los casos raros de los datos), desde el mismo menú.

Los resultados de Análisis, Trayectorias y Cruce se pueden exportar a Excel y CSV.

## Cómo usarla

La primera vez que alguien la abre (doble clic en `iniciar.bat`), la aplicación pide la carpeta con los
Parquet de la muestra y arranca en el navegador; en los siguientes arranques abre sola la última muestra
usada. A partir de ahí, un recorrido típico:

1. **Elige tu colectivo en "Selección de colectivo".** Se define con 2 filtros (Filtro 1 y Filtro 2), cada
   uno con su contracolectivo automático activado por defecto (el "resto" de ese filtro) — así se obtienen 4
   subgrupos cruzando los dos filtros (por ejemplo, mujeres/hombres × españoles/extranjeros). Si desactivas
   el contracolectivo de un filtro, en su lugar defines un segundo filtro independiente (Filtro 3 o Filtro 4)
   para profundizar más dentro de ese mismo colectivo ya definido por los 2 primeros — por ejemplo, dentro de
   "hombres españoles", cruzar parados de larga duración/resto × mayores de 45/resto.
2. **Pulsa "Calcular" y ve a "Análisis de personas"** para ver las estadísticas agregadas de los 4
   subgrupos: numéricas y categóricas, en tabla o en gráfico.
3. **En "Trayectorias de personas"**, sigue la evolución de variables de bases de cotización, pensiones y
   afiliación a lo largo de la vida laboral del colectivo, agregada por año, sobre el eje de tiempo que
   elijas (edad o años desde el inicio de la vida laboral).
4. **En "Trayectorias individuales"**, busca una persona por su IPF (o una al azar del colectivo) y ve su
   línea temporal de episodios laborales.
5. **En "Cruce de variables"**, cruza dos variables cualesquiera del colectivo para explorar su relación.

El menú de la cabecera (icono de tres rayas) permite cambiar de muestra y consultar la Descripción de
variables y este mismo texto (Acerca de).

## Cómo construir los datos (solo equipo ETL)

1. Doble clic en `etl/webui/iniciar.bat`: abre una interfaz local con un botón por tabla y otro para la
   tabla de personas extendida, con progreso en vivo.
2. También por línea de comandos:

```
python etl\webui\ejecutar_paso.py --accion <accion> --year 2024 --raw <carpeta TXT> --parquet <carpeta Parquet>
```

| Acción | Qué hace |
|---|---|
| `personas`, `fiscal`, `bases_cp`, `bases_ca` | Genera el Parquet optimizado de esa tabla |
| `pensiones`, `afiliacion` | Genera la tabla extendida (Afiliación incluye la fusión de tramos) |
| `tramos` | Repite solo la fusión de tramos de afiliación |
| `personas_extendida` | Grupos A a H: la tabla final de personas (necesita `personas` antes) |
| `grupo_h` | Repite solo el Grupo H sobre la copia `_preH` |
| `todas` | Todas las tablas, en orden |

`etl/verificar_tramos.py --parquet <carpeta>` comprueba que la fusión de tramos es determinista y no
tiene incoherencias (no modifica ningún fichero).

## Estructura

```
config.py, config.json      configuración y fecha de corte
app/                        app FastAPI (main.py, db.py, routers/, templates/, static/)
etl/                        procesamiento (optimize_types.py, personas_extendida.py, webui/, metadata/)
tests/                      reglas de consistencia y prueba de humo
docs/                       documentación de datos (esquema_datos_mcvl.md)
build/                      empaquetado PyInstaller (construir.bat)
CHANGELOG.md                cambios de variables y criterios
CLAUDE.md                   notas de diseño y decisiones del proyecto
```

## Reglas de consistencia

`tests/ejecutar_reglas.py` ejecuta las reglas de `tests/reglas_consistencia_v4.xlsx` (una condición SQL
por regla que selecciona las filas que la **incumplen**) sobre la tabla de personas y escribe los
resultados en `tests/resultados/`. `tests/reglas_consistencia_resultados_sin_ipf.xlsx` recoge el estado de
cada regla y su explicación (sin identificadores de persona, versionado). Ver
[`docs/esquema_datos_mcvl.md`](docs/esquema_datos_mcvl.md), sección "Reglas de consistencia".

```
python tests/ejecutar_reglas.py A        # bloque A, B o C
python tests/smoke.py                    # prueba de humo de la app
```

## Documentación

- [`docs/esquema_datos_mcvl.md`](docs/esquema_datos_mcvl.md): tablas Parquet, las 71 columnas de la tabla
  de personas, categorías, criterios de cálculo y convenciones.
- [`CHANGELOG.md`](CHANGELOG.md): qué ha cambiado y qué análisis hay que revisar tras cada cambio.
- `CLAUDE.md`: decisiones de diseño y notas de implementación del ETL.
