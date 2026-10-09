# Novedades por versión

## v2.3 — tres categóricas más en Análisis de personas (2026-10-09)

- **`tipo_contr_ult_baja`, `causa_ult_baja` y `act_econ_ult_baja`** (tipo de contrato, causa y actividad
  económica del episodio más reciente de cada persona) ya se pueden ver y filtrar como categóricas en
  "Análisis de personas". Antes solo estaban sus equivalentes "principales" (acumulados en toda la carrera:
  `tipo_contr_ppal_agrupado`, `causa_baja_trab_ppal`, `act_econ_ppal`); `grupo_ultimo_empleo` ya estaba, pero
  sus hermanas del mismo episodio se habían quedado fuera de `CATEGORICAS_COLECTIVOS` (`config.py`) sin
  motivo documentado. Detectado analizando edadismo (mayores de 45) sobre la muestra 2025: para estudiar qué
  contrato le ofrecen *ahora* a alguien, no el acumulado en toda su vida laboral, hace falta la del último
  episodio, no la principal.

## v2.2 — retoques de "Acerca de" y "Descripción de variables" (2026-09-28)

- **"Qué hay en la app" y "Cómo usarla" del README** (y de "Acerca de") reescritos: describen las pestañas reales
  (Análisis de personas, Trayectorias de personas, Trayectorias individuales, Cruce de variables) y guían a un
  usuario nuevo paso a paso, desde elegir el colectivo (2 o 4 filtros) hasta exportar resultados.
- **"Construcción de variables" desaparece de "Acerca de".** Las secciones del README solo para el equipo ETL
  (`## Cómo construir los datos`, `## Estructura`) ya no se muestran en la app.
- **Descripción de variables tiene una columna nueva, "Cómo se construye"**, con un resumen en lenguaje sencillo
  de cómo calcula el ETL cada una de las 62 variables derivadas de Personas y las 4 de Afiliación; las variables
  originales llevan la nota "Campo original de la MCVL, tal como viene en el fichero."

## v2.2 — varias muestras de la MCVL (2026-09-25)

- **La app maneja varias muestras, una a la vez.** Cada muestra es una carpeta de Parquet; el año sale de su
  `fecha_corte.txt` (año de corte menos 1). Menú de tres rayas en la cabecera: elegir muestra, Añadir, Editar y
  Eliminar. Al cambiar de muestra la página se recarga en su estado inicial.
- **`config.json` cambia de formato:** `{"muestras": [{"carpeta", "anio"}], "activa": ...}`. Un `config.json`
  antiguo (`parquet_dir`) se migra solo, con esa carpeta como única muestra.
- **Eliminar solo quita de la lista** y no se puede quitar la única muestra que queda.
- **Nuevas vistas:** Descripción de variables (71 de Personas y 41 de Afiliación, con búsqueda) y Acerca de (README,
  reglas de consistencia y casos raros).
- **Cabecera:** título `MCVLAnalyserSEPE` y "Observatorio de las Ocupaciones" centrados; `Muestra AAAA` junto al menú.
- Interno: la conexión DuckDB y la fecha de corte salen de la muestra activa (`app.db.estado`); el cambio de
  muestra es un intercambio atómico. `tools/generar_datos_ayuda.py` genera los JSON de las dos vistas.

## Sin versión todavía (cambios de variables tras revisar las reglas de consistencia, 2026-09-25)

- **`Grupo_cotiz_ppal`, `Grupo_cotiz_1er_Empleo_Signif` y `Grupo_Ultimo_Empleo` solo tienen una categoría de "no consta":
  `0 NO CONSTA`.** Antes las personas sin episodio salían como `NO CONSTA` (46.820, 254.015 y 55.812) y las de
  grupo 00 o fuera de rango como `0 NO CONSTA`. Hay que repetir el Grupo H (acción `grupo_h`) para verlo.
- **El Grupo H para si una columna clave sale toda a 0 o nula** (`T_total_*`, `Cotizado_anual_promedio`, edades,
  ingresos...) y no escribe el Parquet; así el fallo de numexpr no pasa desapercibido en un PC nuevo.
- **`ingresos_pension_2024` se renombra a `ingresos_pension_y_bajas_2024`.** La clave B del modelo 190
  no es solo pensión: incluye también las prestaciones por incapacidad temporal (bajas por enfermedad)
  abonadas directamente por la Seguridad Social o la mutua y las de mutualidades de funcionarios. De las
  48.780 personas con este importe y sin pensión en la tabla de pensiones, solo 1.809 tienen 65 años o más.
  **Cualquier filtro o análisis que use el nombre antiguo hay que actualizarlo.** La app reconoce
  también un Parquet anterior con el nombre antiguo.
- **Se elimina `causas_baja_distintas`.** Contaba las causas de baja de todos los episodios, incluidos los de
  prestación, y no era comparable con las altas de empleo. Se conserva `causa_baja_mas_comun`.
- **La edad de los fallecidos se calcula a su fecha de fallecimiento.** `edad_actual_fecha_corte` de las
  18.801 personas fallecidas coincide ahora con `edad_fallecimiento` (antes salían entre 0,25 y 1,25 años
  más viejos; 528 aparecían con más de 100 años, ahora 385). Además, el final del último empleo de un
  fallecido no supera su fecha de fallecimiento (`edad_ultimo_empleo`, 14 personas).
- **Grupo de cotización principal:** cualquier clave fuera de 00-12 (13, 14, 19, 20, 21, 23, 31, 63) cuenta
  como "00 NO CONSTA", como indica la guía, en vez de excluirse. Cambia el grupo principal de unas 1.800
  personas (0,13 %) y `t_tot_grupo_ppal` de quienes trabajaron en los grupos 21 y 23 (R.E. Agrario Cuenta
  Propia).
- **Codificación común de provincias** (`provincias_dict.csv`): 53 Isla de La Palma, 56 Mar, 66 Extranjero
  (nacidos fuera) y 98 Extranjero (residencia fiscal fuera). Antes esos valores salían como "NO CONSTA"
  (220.047 nacidos en el extranjero, 1.564 y 1.020 en provincia fiscal, 503 en provincia de trabajo).
- **Fusión de tramos de afiliación determinista.** El cálculo cambiaba entre ejecuciones idénticas
  (hasta 44 años de diferencia en 5.001 personas); ahora es estable. Cambian los tiempos `T_total_*` y sus
  porcentajes en 2.476 personas y algunos regímenes, grupos, provincias y actividades principales.

## Sin versión todavía (correcciones de las reglas de consistencia, 2026-09-22)

- **Quien cobra jubilación ya no puede aparecer como "en paro".** `situacion_paro_ocupacion`
  combinaba "Parado_LD"/"Parado_NoLD" con "_y_pensionista" para cualquier tipo de pensión,
  incluida la jubilación. Un jubilado no está buscando trabajo, así que ahora pasa directamente
  a `Pensionista`. `Ocupado_y_pensionista` no cambia: trabajar y cobrar jubilación a la vez es una
  situación real (jubilación activa). **Cambian 54.568 personas** (53.571 `Parado_LD_y_pensionista`
  + 997 `Parado_NoLD_y_pensionista` → `Pensionista`); nadie más se mueve.
- **Preparado para procesar otra edición de la MCVL (p. ej. la 2025).** (1) Las columnas con el
  año en el nombre (`ingresos_*_AAAA`, `retenciones_irpf_AAAA`, `Cobra_pension_AAAA/A`) ya no
  están escritas a mano con "2024" en el ETL ni en `app/db.py`: se detectan por patrón, para
  cualquier año. Antes, con otro año, el ETL las descartaba en silencio y la app no las veía.
  (2) La fecha de corte pasa a ser el 1 de mayo del año siguiente al de la casilla "Año" del ETL
  (2024 -> 2025-05-01, 2025 -> 2026-05-01); el ETL la deja en `fecha_corte.txt` junto a los
  Parquet y la app la lee de ahí. Un Parquet sin ese fichero usa 2025-05-01 (edición 2024), así
  que con 2024 no cambia nada. Comprobar con los datos reales de cada edición nueva que esa
  fecha encaja con las últimas altas/bajas.
- **`Parado_LD_y_pensionista` y `Parado_NoLD_y_pensionista` se renombran a
  `Parado_LD_pension_no_jubilacion` y `Parado_NoLD_pension_no_jubilacion`.** Tras el cambio
  anterior, estas dos categorías ya nunca incluyen jubilación (solo incapacidad, viudedad,
  orfandad...); el nombre lo deja explícito para no llevar a confusión. **Cualquier análisis o
  filtro que use el nombre antiguo hay que actualizarlo.**
- **`cotizado_anual_promedio` ahora incluye las bases de cotización de cuenta propia (autónomos,
  agrario cuenta propia...), no solo las de cuenta ajena.** Antes solo se leía la tabla de cuenta
  ajena; quien cotizó alguna vez como autónomo se veía afectado de dos formas: si toda su carrera
  fue por cuenta propia, `cotizado_anual_promedio` salía vacío (79.383 personas con historia
  laboral sin este dato, la mayoría exactamente por esto); si tuvo una carrera mixta (cuenta
  ajena y propia en distintos años, 485.783 personas), el valor salía con un número, pero
  **infravalorado**: mediana +33% al corregirlo, y en el 43% de esas personas sube más de un 50%.
- **`cotizado_anual_promedio` divide entre tiempo de empleo + desempleo + mixto, no entre todo el
  tiempo desde el inicio de la vida laboral.** Antes se diluía con los huecos sin ningún episodio
  registrado, donde por definición no puede haber cotización. El desempleo sí se mantiene en el
  denominador (decisión explícita del usuario, verificada con datos reales): la prestación
  contributiva y el subsidio para mayores de 52/55 años cotizan a efectos de jubilación, y de
  hecho llevan aparejada una base de cotización real en un 54% de los casos (99% en el subsidio
  de 52/55). Junto con el cambio anterior (cuenta propia), la mediana sube un +44%
  (9.008 → 13.673 €/año).
- **`t_tot_reg_ppal`, `t_tot_grupo_ppal`, `t_tot_tipo_contrato_ppal`, `t_tot_prov_ppal` y
  `t_tot_act_econ_ppal` ya no cuentan dos veces el pluriempleo simultáneo.** Antes sumaban la
  duración de cada episodio de afiliación tal cual, así que dos altas a la vez en el mismo
  régimen/grupo/tipo de contrato/provincia/actividad se sumaban por separado en vez de una sola
  vez. Ahora se fusionan primero los episodios solapados o contiguos de la misma categoría
  (igual criterio que ya se usa para el tiempo total de empleo), y se suma la duración fusionada.
  **Los valores cambian**: antes, hasta 49.420 personas tenían `t_tot_prov_ppal` mayor que su
  tiempo total de vida laboral (6,8 años de más de media); ahora, 0.
- **`Fecha_inicio_ultima_racha_sin_empleo` ya se rellena para `Asimilado_otros`.** El cálculo
  solo miraba episodios de prestación o subsidio del SEPE (TRL 751-756); una persona cuya
  situación actual es `Asimilado_otros` (TRL 400/980) casi nunca tenía ninguno entre sus 5
  episodios más recientes, así que la racha quedaba vacía aunque constara como parada.
- **200 IPF (453 filas) que venían duplicados en el origen se eliminan.** Cada pareja de filas
  con el mismo IPF tenía sexo y fecha de nacimiento distintos: son dos personas físicas
  distintas que comparten identificador, no una fila repetida por el ETL. Se eliminan las dos
  filas de cada IPF afectado (no se puede saber cuál es la correcta).
- **`clave_subclave_ppal` es NO CONSTA para quien no tiene ningún importe positivo.** 278 personas
  tienen datos fiscales pero todos sus importes son 0 o negativos; antes recibían igualmente un
  tipo principal arbitrario (el de menor código entre los empatados).
- **`pension_actual` ya no repite el mismo tipo de pensión dentro de la propia lista** (por
  ejemplo `[Jubilación, Jubilación]` cuando había una fila por año cobrado). Afectaba a 3.823
  personas; `pension_todas_cobradas` ya deduplicaba, a `pension_actual` se le había quedado sin
  aplicar el mismo criterio.

## v2.2

### Datos (hay que descargar los Parquet nuevos, generados con el ETL de esta versión)

- **`domic_resid_habitual` pasa de numérica a categórica.** Es el código INE del municipio de
  residencia, de 5 dígitos (2 de provincia + 3 de municipio). Solo los municipios de más de
  40.000 habitantes llevan su propio código; el resto de municipios de cada provincia se agrupa
  bajo `PP000` (por ejemplo `28000` = resto de la provincia de Madrid). Los valores sin dato
  (`00000`, personas no residentes en España) o con una provincia fuera de 01-52 (dato erróneo
  según la guía) aparecen como `NO CONSTA`.
- **Nueva columna `prov_resid_habitual`**: la provincia de residencia (los dos primeros dígitos
  de `domic_resid_habitual`), con el mismo formato que las demás provincias (`28 MADRID`).
- **Nueva columna `edad_fallecimiento`**: edad en años al fallecer (con precisión de mes);
  vacía si la persona no ha fallecido.
- **Nueva columna `ha_fallecido`**: `SI` / `NO`.
- **`fecha_fallecimiento` deja de mostrarse en la aplicación.** Era un número `aaaamm` con el
  que no se podía filtrar con `>` o `<`. Sigue existiendo en el fichero Parquet, pero se usan
  `edad_fallecimiento` y `ha_fallecido` en su lugar.
- **La fecha de corte de la muestra pasa del 1-6-2025 al 1-5-2025.** La última baja real de la
  muestra es del 30-4-2025, así que se extrajo el 1-5. Cambian las columnas calculadas a esa fecha:
  `edad_actual_fecha_corte` (un mes menos), `tiempo_total_desde_inicio_vl`,
  `duracion_actual_sin_empleo` y los episodios de afiliación todavía abiertos.
- **`Ingresos_bruto_anual_2024` pasa a llamarse `ingresos_bruto_total_2024`.** El valor es el
  mismo (suma de todas las percepciones dinerarias de los datos fiscales, incluidas pensiones,
  prestaciones y rentas exentas); solo cambia el nombre. **Cualquier análisis que use el nombre
  antiguo hay que actualizarlo.**
- **`num_bajas_anual_promedio` se calcula con bajas reales.** Antes contaba huecos entre
  episodios sin tener en cuenta los solapes (pluriempleo), con lo que un 11 % de lo contado
  no eran bajas y no se contaban las altas encadenadas al día siguiente. Ahora cuenta los
  episodios de empleo con fecha de baja (los mismos que cuentan en `num_altas_anual_promedio`,
  pero ya terminados), así que nunca hay más bajas que altas. **Los valores cambian**: la
  media de bajas por persona pasa de 13,5 a 18,8.
- **El último episodio de afiliación ya no depende del orden del fichero.** Cuando una persona
  tiene dos episodios con las mismas fechas de alta y baja al final de su vida laboral (unas
  9.900 personas), la situación de afiliación, la racha sin empleo y la causa, el grupo, el
  contrato y la actividad del último episodio salían de una fila elegida al azar. Ahora gana el
  episodio de prestación o subsidio (TRL 751-756) y, después, un criterio fijo, así que el
  resultado es siempre el mismo. Cambian algunos valores de esas columnas en esas personas.
- **Ingresos por tipo de percepción** (nuevas columnas, en euros; suman exactamente
  `ingresos_bruto_total_2024`). Valen 0 si la persona tiene datos fiscales pero no cobra de ese
  tipo, y quedan vacías si no aparece en la tabla fiscal:
  - `ingresos_sueldo_2024`: rendimientos del trabajo por cuenta ajena (clave A del modelo 190).
  - `ingresos_pension_2024`: pensiones y haberes pasivos (clave B) **más** las pensiones exentas
    de IRPF que el modelo 190 declara en la clave L: incapacidad permanente absoluta y gran
    invalidez (L06), incapacidad de Clases Pasivas (L07) y pensiones de orfandad y prestaciones
    familiares exentas (L08).
  - `ingresos_prestacion_2024`: prestaciones y subsidios por desempleo (clave C).
  - `ingresos_actividad_2024`: actividades económicas y profesionales (autónomos), cursos y obras
    (claves F, G y H).
  - `ingresos_otras_exentas_2024`: el resto de rentas exentas (dietas, indemnizaciones por
    despido, maternidad y paternidad, sepelio...).
- **Nueva columna `retenciones_irpf_2024`**: retenciones de IRPF practicadas sobre esas
  percepciones (en euros).
- **Nuevas columnas `clave_subclave_ppal` y `clave_subclave_todas_cobradas`**: el tipo de
  percepción del modelo 190 (clave + subclave, por ejemplo `B01`). La principal es el tipo con
  **mayor importe total sumado** de la persona y sale con etiqueta (`B01 Pensión SS / Clases
  Pasivas`); `NO CONSTA` si no tiene datos fiscales. `clave_subclave_todas_cobradas` es la lista
  de los códigos que cobra con importe positivo, de mayor a menor importe (solo se ve en Excel o
  en el Parquet: las columnas de tipo lista no se pueden usar como filtro). Las etiquetas están
  en `etl/metadata/clave_percepcion_dict.csv`.

### Aplicación

- La etiqueta de versión pasa a **v2.2**.
- Con un Parquet anterior a esta versión la aplicación sigue arrancando, pero sin las columnas
  nuevas (y `domic_resid_habitual` no estará disponible en los selectores). El total de ingresos
  sí aparece, con su nombre nuevo, aunque el Parquet traiga todavía el nombre antiguo.
