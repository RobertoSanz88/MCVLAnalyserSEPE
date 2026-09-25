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

## Qué hay en la app (v1)

1. **Selección de colectivo y filtro global**, con un único interruptor que se aplica a todas las pestañas.
2. **Explorador de trayectorias individuales:** buscar por IPF y ver la línea temporal de episodios laborales.
3. **Constructor de colectivos y estadísticas agregadas:** filtros (sexo, edad, provincia, CNAE, tipo de
   contrato, fechas...), recuentos, distribuciones, cruces de variables y trayectorias de colectivo por
   años de vida laboral o por edad. Exportable a Excel y CSV.

## Cómo usarla

- **Arranque:** doble clic en `iniciar.bat` (instala las dependencias de `requirements.txt` si faltan y
  abre el navegador cuando el servidor responde). El puerto preferido es el 8000; si está ocupado usa el
  siguiente libre. La primera vez pide la carpeta de Parquet con un selector; la configuración se guarda
  en `%APPDATA%`.
- **Ejecutable:** la distribución prevista es un `.exe` autónomo (PyInstaller, modo `--onedir`) generado
  siempre con `build\construir.bat`. Consulta `CLAUDE.md` para el detalle y la advertencia de SmartScreen.

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
