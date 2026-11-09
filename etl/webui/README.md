# `etl/webui/` — interfaz web para lanzar el ETL (notas de implementación)

Herramienta auxiliar para ejecutar `optimize_types.py` y `personas_extendida.py`
con progreso en vivo en el navegador, en vez de a mano por consola. **Sigue
siendo parte del módulo restringido `etl/`** (mismo uso limitado a Roberto +
2-3 personas, ver cabecera de este documento): vive en `etl/webui/`, fuera de
`app/`, y no se distribuye al resto del equipo ni se empaqueta con PyInstaller.

- Arranque: doble clic en `etl/webui/iniciar.bat`. Instala dependencias de
  `etl/requirements.txt` (pandas, numpy, pyarrow, duckdb, fastapi, uvicorn,
  jinja2) y lanza un servidor FastAPI+uvicorn en `http://127.0.0.1:8010/`.
  El propio `.bat` prueba primero una instalación normal de pip y solo si
  falla reintenta con `--trusted-host pypi.org --trusted-host
  files.pythonhosted.org` (necesario en el PC corporativo por el proxy SSL
  de Netskope) — un único `.bat` sirve para el PC personal y el corporativo
  sin tocar nada. Escrito con `goto`/etiquetas en vez de bloques `if (...)`
  anidados: un simple paréntesis suelto dentro del texto de un `echo` rompe
  el parser de bloques de `cmd.exe` ("No se esperaba ... en este momento").
  El navegador **no** se abre con un `start` directo justo antes de lanzar
  python: en el PC corporativo las importaciones (pandas, duckdb...) tardan
  varios segundos (antivirus/EDR escaneando cada import) y el navegador no
  reintenta solo una conexión rechazada — se abrió en blanco/error una vez
  por esto. `esperar_y_abrir.bat` (lanzado en paralelo con `start`) hace
  polling con `curl` al puerto 8010 hasta que el servidor responde de
  verdad, y solo entonces abre el navegador.
- Estructura: `main.py` (rutas FastAPI, `GET /ejecutar` como SSE), `run.py`
  (lanzador uvicorn), `ejecutar_paso.py` (dispatcher que también sirve como
  target del subproceso — cada acción se ejecuta en un `subprocess.Popen`
  aparte, no en el proceso del servidor, así una caída del ETL no tumba la
  interfaz), `templates/index.html` (página única, sin build step).
- Botones: uno por tabla origen (Personas, Datos fiscales, Bases CA, Bases
  CP → generan solo el `_optimizada.parquet`; Pensiones y Afiliación →
  generan directamente el `_extendida.parquet`, Fase 1/2 incluidas sin
  exponerlas por separado) + "Construir todas" (equivalente al "procesar
  todas" del cuaderno 1) + un botón final aparte, visualmente distinto,
  "Construir tabla de personas extendida".
- Progreso vía SSE (`Cache-Control: no-cache`, `X-Accel-Buffering: no`,
  igual que el patrón ya usado en `BusinessValuationPSD`): cada línea de
  stdout del subproceso se reenvía como evento al navegador. La barra es
  siempre un porcentaje real 0-100% (ya no hay barra indeterminada salvo
  como red de seguridad para una acción futura sin duración conocida), con
  interpolación suave por tiempo transcurrido dentro de cada etapa (tope al
  97% del tramo para no darla nunca por acabada antes de que llegue la
  siguiente marca real del log o el evento final). Un único mecanismo
  genérico (`calcularPorcentajes()`, `etapasActivas`, `tick()` en
  `index.html`) sirve para los tres casos:
  - **"personas_extendida"**: ponderada por la duración medida de cada
    grupo (A-H) en una ejecución real completa.
  - **"Construir todas"**: ponderada por la duración medida de cada tabla
    en una ejecución real completa (28 min en el PC corporativo de 15GB),
    con las marcas de etapa reutilizando las mismas líneas `# NOMBRE` que
    ya se usan para resaltar el botón en curso. **Acoplamiento importante**:
    el orden de `ORDEN_TODAS` en `index.html` debe coincidir exactamente
    con `TODAS_LAS_TABLAS` en `ejecutar_paso.py` (ahí Bases CP va antes que
    Bases CA, al revés que en la fila de botones) — si se desincronizan, los
    porcentajes acumulados quedan mal calculados y la barra puede retroceder
    al pasar de una tabla a la siguiente.
  - **Las 6 acciones de tabla individual**: una sola etapa 0-100% con la
    duración medida de esa misma ejecución de referencia, arrancando a
    interpolar en cuanto se pulsa el botón (no hay marca de log que
    esperar). Decisión explícita del usuario: aunque la duración real varíe
    con el tamaño de los TXT de turno y no coincida exacto, prefiere esto a
    la barra indeterminada — si la tabla real termina antes de los segundos
    estimados, la barra salta de repente al 100% en el evento final; si
    tarda más, se queda esperando cerca del 97%. Los números están en
    `DURACION_TABLA_SEG` (`index.html`), medidos una sola vez en un PC de
    15GB con la MCVL 2024 completa — no válidos para otro volumen de datos
    o máquina muy distinta, pero suficientes como referencia.
- Checkbox "Borrar TXT tras procesar", **desactivado por defecto**: el
  borrado automático de TXT fue una necesidad de la máquina de desarrollo
  (~4GB RAM, poco disco), no una norma general — en un PC con disco de
  sobra (como el corporativo) no debe borrar nada salvo que el usuario lo
  pida explícitamente.
- Configuración (rutas de carpeta TXT/Parquet, año) persistida en
  `localStorage` del navegador, no en fichero — es una herramienta de un
  único usuario en su propia máquina, no hace falta más.
- **Un solo fichero por tabla, no dos**: para Personas, Pensiones y
  Afiliación, en cuanto la `_extendida.parquet` está construida se borra
  la `_optimizada.parquet` correspondiente (`_borrar_optimizada()` en
  `ejecutar_paso.py`) — decisión explícita del usuario, no queda como
  fichero intermedio útil porque la extendida ya la contiene entera (mismas
  columnas originales sin tocar, solo se le añaden columnas nuevas; ver
  `bootstrap_personas_extendida`, `fase2_pensiones_extendida`,
  `fase1_afiliacion_extendida_duckdb`). **Importante para Personas
  concretamente**: a diferencia de Pensiones/Afiliación (donde optimizada y
  extendida se generan en el mismo clic de botón), Personas optimizada se
  genera con el botón "Personas" y Personas extendida con el botón final
  de Fase 3, en clics separados — el borrado ocurre en el paso de Fase 3
  (justo tras el bootstrap). Si se repite Fase 3 sin haber vuelto a pulsar
  "Personas" antes, fallará porque su fichero de entrada ya no existe; hay
  que volver a generar Personas optimizada primero (es rápida, tabla
  pequeña).
- Probada de extremo a extremo con navegador real (no solo `curl`) en la
  máquina de desarrollo de ~4GB: una primera ejecución del botón final se
  cortó durante el Grupo H (el proceso de uvicorn murió sin dejar
  traceback legible porque la ventana de consola se cerró antes de poder
  leerlo); una segunda ejecución idéntica, con la misma máquina y los
  mismos datos, completó sin problemas y produjo el mismo resultado ya
  verificado `(1359664, 50)`. Al no ser reproducible, se interpreta como un
  pico puntual de memoria (navegador + servidor + subproceso ETL a la vez
  en una máquina tan limitada), no un fallo del pipeline. Si vuelve a pasar,
  capturar el log completo con `etl\webui\iniciar.bat > log_etl.txt 2>&1`
  para tener el traceback aunque se cierre la ventana.
- **`etl/recursos.py`**: detecta la RAM total del equipo (`GlobalMemoryStatusEx`
  vía `ctypes`, sin depender de `psutil`) y adapta automáticamente dos
  parámetros — mismo código, mismos resultados, solo cambia la velocidad —
  cuando hay ≥10GB (`UMBRAL_RAM_HOLGADA_GB`): el tamaño de trozo de
  `leer_csv_por_trozos_a_parquet` pasa de 100.000 a 500.000 filas, y los
  hilos de DuckDB (`_nueva_conexion` en `ejecutar_paso.py`) de 2 a
  `min(cpu_count(), 8)`. Ambos valores conservadores (100.000 filas, 2
  hilos) son justo los que ya llevan toda la sesión verificados en la
  máquina de desarrollo de ~4GB, así que ahí el comportamiento no cambia
  nada — confirmado (`ram_total_gb()` devuelve 3.94 en esta máquina, sigue
  eligiendo el modo conservador). Confirmado con datos reales en el PC
  corporativo (15,3GB detectados -> 8 hilos, "modo rápido"): la Fase 3
  completa corrió de principio a fin sin problemas de memoria. Si algún día
  compensa apretar más (o resulta demasiado agresivo), ajustar los números
  en `etl/recursos.py`, no la lógica que los usa.
- **`provincias_dict.csv`/`cnae_3d_dict.csv` movidos a `etl/metadata/`**
  (antes vivían sueltos en la carpeta de datos de cada máquina): son tablas
  de código público (provincia INE, CNAE a 3 dígitos), no datos MCVL
  restringidos, así que sí pueden ir en el repo. Antes `ejecutar_paso.py`
  los buscaba por defecto en la carpeta justo encima de la de salida de
  Parquet — convención fragil que rompió en el primer PC donde la carpeta
  de Parquet no colgaba directamente de la raíz del repo (`FileNotFoundError`
  en el último paso de Fase 3, con toda la Fase 3 ya calculada). Ahora el
  valor por defecto apunta siempre a `etl/metadata/`, sea cual sea la
  carpeta de Parquet elegida.
