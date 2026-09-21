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

### Aplicación

- La etiqueta de versión pasa a **v2.2**.
- Con un Parquet anterior a esta versión la aplicación sigue arrancando, pero sin las tres
  columnas nuevas (y `domic_resid_habitual` no estará disponible en los selectores).
