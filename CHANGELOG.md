# Novedades por versión

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
