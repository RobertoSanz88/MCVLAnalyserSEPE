# Esquema de datos de la MCVL

Edición documentada: **MCVL 2024** (fecha de corte 2025-05-01). Las cifras de recuento son de esa edición.
La guía oficial de la MCVL (`MCVLGuia20260707.pdf`) es la referencia de significado de los campos originales.

> Las tablas Parquet **no van nunca a git**. Este documento describe su estructura, no contiene datos.

## 1. Tablas Parquet

Una carpeta local con estos ficheros (la app solo lee; los genera el módulo `etl/`):

| Fichero | Contenido | Filas aprox. |
|---|---|---|
| `tabla_personas_extendida.parquet` | **Una fila por persona**: variables originales de Personas y todas las derivadas (71 columnas, sección 2). Es la tabla principal de la app. | 1.359.056 |
| `tabla_personas_extendida_preH.parquet` | Copia de la anterior justo antes del Grupo H (61 columnas). Solo sirve para repetir el Grupo H sin rehacer los grupos A a G. | 1.359.780 |
| `tabla_afiliacion_extendida.parquet` | Un episodio de afiliación por fila (unos 33 millones): las 37 columnas originales más `Baja_modificada`, `Empleo_publico`, `DURACION_EMPLEO` y `DURACION_DESEMPLEO`. | 33 M |
| `tabla_afiliacion_tramos.parquet` | Tramos de calendario **sin solape** por persona (`ipf`, `fecha_inicio_tramo`, `fecha_fin_tramo`, `categoria` = EMPLEO, DESEMPLEO o MIXTO). De aquí salen los tiempos totales. | 25,5 M |
| `tabla_pensiones_extendida.parquet` | Una pensión por fila y año de dato, con `Tipo_pension` e `Ingresos_pension_bruto_anual`. | |
| `tabla_datos_fiscales_optimizada.parquet` | Datos fiscales (modelo 190) por perceptor. | |
| `tabla_bases_cotizacion_CA_optimizada.parquet`, `..._CP_...` | Bases de cotización por año: cuenta ajena (CA) y cuenta propia (CP). El importe viene en **céntimos**. | |
| `fecha_corte.txt` | La fecha de corte con la que se generó (la app la lee de aquí). | |

Unión principal entre tablas: `IPF` (identificador de persona anonimizado). Hay 200 IPF duplicados en origen
con sexo y fecha de nacimiento distintos; se eliminan las 453 filas afectadas de la tabla de personas.

## 2. Tabla de personas extendida (71 columnas)

Convenciones: los tiempos y edades están en **años** (decimales) salvo que se indique otra cosa; los
importes en **euros**; "NULL" = no aplica o sin datos. Las columnas con año en el nombre (`_2024`) se detectan
por patrón para cualquier edición. Las categorías de texto llevan el código delante (p. ej. `10 MAYORES DE 18...`).

### 2.1 Identificación y datos personales

| Columna | Tipo | Descripción |
|---|---|---|
| `01 IPF` | entero | Identificador de persona anonimizado. Clave de unión. |
| `02 FECHA_NACIMIENTO` | fecha | Fecha de nacimiento (solo año y mes son fiables). |
| `03 SEXO` | texto | `Hombre`, `Mujer` o `NO CONSTA` (52 personas). |
| `04 NACIONALIDAD` | texto | Nacionalidad decodificada: `N01 Alemania`, ... |
| `05 PROV_NACIMIENTO` | texto | Provincia de nacimiento (`28 MADRID`...). `66 EXTRANJERO` para los nacidos fuera; `NO CONSTA` si no consta. |
| `06 PROV_1a_AFILIACION` | texto | Provincia de la primera afiliación. |
| `07 DOMIC_RESID_HABITUAL` | texto | Código INE de 5 dígitos del municipio de residencia (los municipios pequeños se agrupan como `PP000`). |
| `prov_resid_habitual` | texto | Provincia de residencia, derivada del anterior. |
| `09 PAIS_NACIMIENTO` | texto | País de nacimiento decodificado. |
| `Nacionalidad_agrupado` | texto | `espana`, `paises_UE_UK`, `paises_latinos`, `paises_africanos`, `paises_lejanos`. |
| `Pais_nacim_agrupado` | texto | Igual, para el país de nacimiento (NULL si no consta, 3,9 %). |
| `08 FECHA_FALLECIMIENTO` | entero | Fecha de fallecimiento `aaaamm`; `0` si no ha fallecido. |
| `edad_fallecimiento` | decimal | Edad al fallecer (precisión de mes). NULL si no ha fallecido. |
| `ha_fallecido` | texto | `SI` / `NO` (18.801 fallecidos). |
| `Edad_Actual_Fecha_Corte` | decimal | Edad a la fecha de corte. **Para los fallecidos es la edad a su fecha de fallecimiento** (coincide con `edad_fallecimiento`). |

### 2.2 Primer y último empleo

El "empleo" excluye los episodios de prestación o subsidio de desempleo (TRL 700-799) y los de TRL 400
(sin alta laboral según la guía). Un primer empleo **significativo** es el primer episodio de empleo con
duración de **270 días o más**.

| Columna | Tipo | Descripción |
|---|---|---|
| `Fecha_ini_Primer_Empleo` | fecha | Inicio del primer episodio de empleo. NULL si nunca ha tenido empleo real. |
| `Edad_Primer_Empleo_Signif` | decimal | Edad al inicio del primer empleo significativo. |
| `Duracion_1er_Empleo_Signif` | años | Duración de ese episodio. |
| `Grupo_cotiz_1er_Empleo_Signif` | texto | Grupo de cotización de ese episodio. |
| `Edad_Ultimo_Empleo` | decimal | Edad al final del último episodio de empleo. **Para un fallecido no supera su edad al fallecer.** |
| `Grupo_Ultimo_Empleo` | texto | Grupo de cotización del último empleo. |
| `fecha_ultima_baja` | fecha | Fecha de la última baja de afiliación (`2099` = episodio aún abierto). |
| `causa_ult_baja` | texto | Causa de baja del último episodio (`51 Voluntaria`...). |
| `tipo_contr_ult_baja` | texto | Tipo de contrato del último episodio. |
| `act_econ_ult_baja` | texto | CNAE del último episodio (`código descripción`). |

Si dos episodios empatan como "último", gana el de prestación o subsidio y después un criterio fijo
(TRL y causa), para que el resultado no dependa del orden del fichero.

### 2.3 Tiempo de vida laboral (tramos sin solape)

Se calculan sobre `tabla_afiliacion_tramos.parquet`: cada día cuenta una sola vez. **Empleo** y **desempleo**
se suman por separado; un día con ambos cuenta como **mixto**. `T_total_NO_EMPLEO` es el **residuo**
(`Tiempo_total_desde_inicio_VL` menos los otros tres), nunca una suma de huecos.

| Columna | Tipo | Descripción |
|---|---|---|
| `Tiempo_total_desde_inicio_VL` | años | Desde el primer episodio de **cualquier tipo** hasta la fecha de corte (o hasta el fallecimiento). |
| `T_total_EMPLEO` | años | Tiempo solo con empleo. |
| `T_total_DESEMPLEO` | años | Tiempo solo con prestación o subsidio. |
| `T_total_MIXTO_EMPL_DESEMPL` | años | Tiempo con empleo y prestación a la vez. También es trabajo. |
| `T_total_NO_EMPLEO` | años | Residuo: tiempo sin ningún episodio. |
| `%T_total_EMPLEO`, `%T_total_DESEMPLEO`, `%T_total_MIXTO_EMPL_DESEMPL`, `%T_total_NO_EMPLEO` | 0-100 | Porcentaje de cada uno sobre `Tiempo_total_desde_inicio_VL`. Suman 100. |
| `duracion_promedio_EMPLEO` | años | Duración media de los episodios de empleo. |
| `t_contrato_mas_largo` | años | Duración del episodio de empleo más largo. |
| `Causa_Baja_Trab_Ppal` | texto | Causa de baja de ese mismo episodio (el más largo). |
| `num_altas_anual_promedio`, `num_bajas_anual_promedio` | por año | Altas y bajas **de empleo** por año de vida laboral. Una baja cuenta solo si el episodio de empleo tiene fecha de baja real (no 2099). |
| `Cotizado_anual_promedio` | €/año | Bases de cotización totales (cuenta ajena **y propia**, en euros) divididas por `T_total_EMPLEO + DESEMPLEO + MIXTO`. |
| `causa_baja_mas_comun` | texto | Causa de baja más frecuente entre **todos** los episodios. |

Para los fallecidos, los tiempos se cortan en la fecha de fallecimiento aunque tuvieran un episodio aún abierto.

### 2.4 Categorías principales ("ganadoras")

Para cada persona se elige la categoría con **más tiempo acumulado**, fusionando episodios solapados de la
misma categoría; el desempate es determinista. Las columnas `T_tot_*` / `t_tot_*` son ese tiempo en años.

| Columna | Descripción |
|---|---|
| `Reg_cotiz_ppal_Agrupado`, `T_tot_reg_ppal` | Régimen principal (`01 Régimen General`, `05 R.E. Autónomos`, `06 R.E. Agrario Cuenta Ajena`, `08 R.E. Mar`, `12 R.E. Empleados de Hogar`...). |
| `Grupo_cotiz_ppal`, `t_tot_grupo_ppal` | Grupo de cotización principal. **Cualquier clave fuera de 00-12 cuenta como `0 NO CONSTA`**, como indica la guía. |
| `Tipo_Contr_ppal_Agrupado`, `T_tot_tipo_contrato_ppal` | Tipo de contrato agrupado: `Indefinido_t_completo`, `Indefinido_t_parcial`, `Indefinido_fijo_discontinuo`, `Temporal_t_completo`, `Temporal_t_parcial`, `Otros`, `No consta`. |
| `Prov_trabajo_ppal`, `t_tot_prov_ppal` | Provincia de trabajo principal, a partir de los dígitos 5-6 del CCC (`56 MAR` para los códigos 53 y 56 del Régimen del Mar). |
| `Act_econ_ppal`, `t_tot_act_econ_ppal` | Actividad económica principal (CNAE 2009, 3 dígitos). El CNAE 0 se ignora. |

La categoría principal de cada variable es independiente de las demás: el régimen principal y la actividad
principal no tienen por qué corresponderse (por eso hay reglas de consistencia entre ellos como casos raros).

### 2.5 Ingresos fiscales (modelo 190, ejercicio 2024)

Importes en euros. `NULL` si la persona no aparece en la tabla fiscal (11 %); `0` si aparece pero no cobra de ese tipo.
Las cinco partes **suman exactamente** `ingresos_bruto_total_2024`.

| Columna | Descripción |
|---|---|
| `ingresos_sueldo_2024` | Trabajo por cuenta ajena (clave A). |
| `ingresos_pension_y_bajas_2024` | Clave B: pensiones y haberes pasivos **y también bajas por incapacidad temporal** abonadas por la Seguridad Social o la mutua (subclave B01) y prestaciones de mutualidades de funcionarios (B03); más las pensiones exentas L06, L07 y L08. *Antes se llamaba `ingresos_pension_2024`; el nombre antiguo no describía este contenido.* |
| `ingresos_prestacion_2024` | Prestaciones y subsidios de desempleo (clave C). |
| `ingresos_actividad_2024` | Actividades profesionales, agrarias y otras (claves F, G, H). |
| `ingresos_otras_exentas_2024` | Rentas exentas que no son pensión (clave L, resto). |
| `ingresos_bruto_total_2024` | Total. |
| `retenciones_irpf_2024` | Retenciones. |
| `clave_subclave_ppal` | Tipo de percepción con **mayor importe**: `A00 Sueldo`, `B01 Pensión SS / Clases Pasivas`, `C00 Prestación o subsidio de desempleo`, `L06 IPA / gran invalidez exentas`, `G01 Actividad profesional (general)`... `NO CONSTA` si no hay datos fiscales o el importe es 0. |
| `clave_subclave_todas_cobradas` | Lista de todas las percepciones cobradas. |
| `Prov_fiscal_actual` | Provincia fiscal del pagador con mayor ingreso. Códigos: 1-52 provincias, `53 ISLA DE LA PALMA`, `98 EXTRANJERO`. |

Territorios forales: Álava, Guipúzcoa, Navarra y Vizcaya **no tienen datos fiscales** en la MCVL (guía).

### 2.6 Pensiones y discapacidad

| Columna | Tipo | Descripción |
|---|---|---|
| `Pension_todas_cobradas` | lista | Tipos de pensión cobrados alguna vez (desde 1996). |
| `Pension_actual` | lista | Tipos de pensión en el año de referencia: `Jubilación`, `Viudedad`, `Orfandad`, `Incapacidad`, `Incapacidad>65`, `Pension_a_favor_familiares`. |
| `Cobra_pension_2024/5` | texto | `SI` / `NO`: cobra alguna pensión en la edición. |
| `Discap_fiscal`, `Discap_afiliacion`, `Discap_pension` | booleano | Discapacidad según datos fiscales, alta de afiliación y tabla de pensiones respectivamente (la definición exacta de `Discap_pension` está pendiente de confirmar). |

Clases de prestación (guía 4.004): 30 viudedad con resolución provisional, 31 viudedad general, 32 viudedad
MUNPAL, 40 orfandad con resolución provisional, 41 orfandad simple, 42 **orfandad absoluta**, 51-54 a favor de
familiares. La orfandad de adultos es posible: **no tiene límite de edad si el beneficiario tiene incapacidad
permanente** (art. 224 LGSS; guía 4.030, prestación vitalicia).
Los regímenes de pensión 31, 32, 35, 38, 39 y 68 son **prestaciones complementarias** (mutualidades), no
pensiones básicas de la Seguridad Social; se cuentan como pensión sin distinguirlas.

### 2.7 Situación actual

`Situacion_actual_afiliacion` (a la fecha de corte, según el último episodio):

| Valor | Personas | Significado |
|---|---|---|
| `Alta_ocupado` | 712.320 | Episodio de empleo abierto. |
| `Baja_de_actividad` | 274.368 | Baja de empleo sin pasar por el SEPE. |
| `Baja_para_pension` | 201.583 | Baja por paso a pensionista (causa 58). |
| `Baja_de_prestacion` / `Baja_de_subsidio` | 52.426 / 31.215 | De baja de una prestación o subsidio. |
| `Asimilado_al_alta_prest_subs` / `Asimilado_al_alta_subs52` | 25.560 / 15.711 | Cobrando prestación o subsidio (el segundo, subsidio de mayores, TRL 753/754). |
| `Asimilado_otros` | 242 | TRL 980 o 400. |
| `Nunca_ha_cotizado` | 45.631 | Sin ningún episodio de afiliación. |

**`Asimilado_al_alta_subs52` no implica necesariamente tener 52/55 años** (verificado con datos reales,
MCVL 2025: 203 personas de 20 a 45 años en este valor). La guía MCVL explica que las claves TRL 753/754 se
usan cuando la entidad gestora tiene obligación de cotizar por el beneficiario del subsidio, lo que ocurre
"en general" a partir de cierta edad — pero los **trabajadores fijos discontinuos** pueden recibir el mismo
TRL cobrando el subsidio **entre campañas**, sin haber cumplido esa edad, si acreditan un período mínimo de
ocupación cotizada. La propia guía reconoce que no se puede distinguir un caso del otro con seguridad solo
con los datos (sugiere mirar fecha de nacimiento, contrato previo y duración del subsidio).

`Situacion_Paro_Ocupacion`: `Ocupado` (alta), `Pensionista` (baja para pensión, nunca cotizó, **o parado que
cobra jubilación**), `Parado_LD` / `Parado_NoLD` (parado con más / con menos de 365 días sin empleo), y las
combinaciones `Ocupado_y_pensionista`, `Parado_LD_pension_no_jubilacion`, `Parado_NoLD_pension_no_jubilacion`
(con una pensión que no es jubilación: viudedad, incapacidad, orfandad). La jubilación **nunca** deja a alguien
como "en paro". `Ocupado_y_pensionista` con jubilación es la jubilación activa, que sí es una situación real.
La etiqueta `Parado_*` no mira la edad: a edades muy avanzadas puede aparecer con una viudedad.

| Columna | Tipo | Descripción |
|---|---|---|
| `Fecha_inicio_ultima_racha_sin_empleo` | fecha | Inicio de la última racha de prestación o subsidio. |
| `duracion_actual_sin_empleo` | **días** | Días sin empleo hasta la fecha de corte (desde el inicio de la racha si cobra del SEPE; desde la última baja en otro caso). |

## 3. Criterios y convenciones

- **Fecha de corte:** 1 de mayo del año siguiente al de la edición (2024 → 2025-05-01). Se guarda en
  `fecha_corte.txt`; nunca se usa la fecha del sistema.
- **Fallecidos:** el tiempo de vida laboral, la edad actual y el final del último empleo se cortan en su
  fecha de fallecimiento. Sus episodios abiertos al morir no cuentan más allá.
- **Tramos sin solape:** los tiempos totales salen de la fusión de episodios en tramos de calendario
  (`tabla_afiliacion_tramos.parquet`); el orden de las ventanas de cálculo es **total y determinista**
  (por fecha de alta, fecha de baja y número de fila).
- **Empleo:** no incluye los episodios de prestación o subsidio (TRL 700-799) ni el TRL 400.
- **Codificación de provincias común** (`etl/metadata/provincias_dict.csv`, todas las variables de provincia):
  01-52 provincias INE, `53 ISLA DE LA PALMA` (provincia fiscal), `56 MAR` (cuentas del Régimen del Mar de
  Ferrol y Vilagarcía de Arousa; el 53 del CCC también es del Mar y se convierte en 56), `66 EXTRANJERO`
  (nacidos fuera) y `98 EXTRANJERO` (residencia fiscal fuera). Cualquier otro código es `NO CONSTA`.
- **Grupos de cotización:** válidos 00-12; el resto (13, 14, 19, 20, 21, 23, 31, 63) equivale a `NO CONSTA`.
  Los grupos 21 y 23 pertenecen al R.E. Agrario Cuenta Propia (extinguido en 2008).
- **Edad mínima legal para trabajar:** 14 años hasta la cohorte 1965, 16 después (Ley 8/1980, Estatuto de los
  Trabajadores). Los **menores autorizados** (art. 6.4 ET, espectáculos públicos) trabajan por debajo de esa edad:
  régimen 0112 (artistas) y sectores CNAE 59, 60, 90, 93, 73, 781, 742 y 855.
- **Datos anteriores a 1967:** la afiliación empieza en 1967 y las bases de cotización hacia 1976-1980. Las carreras
  anteriores (pensiones SOVI, `NORMA_SOVI` = 1) no aparecen en la tabla de afiliación.
- **Variables eliminadas:** `causas_baja_distintas` (mezclaba episodios de prestación con los de empleo).
- **Escalas:** las bases de cotización y los importes fiscales vienen en céntimos en origen y se dividen entre 100.

## 4. Reglas de consistencia

Las reglas están en `tests/reglas_consistencia_v4.xlsx`. Cada una es una condición SQL sobre la tabla de
personas (vista `personas` de `app/db.py`) que selecciona las filas que la **incumplen**: la regla pasa si no
devuelve ninguna. El runner (`tests/ejecutar_reglas.py`) crea tablas auxiliares según lo que use cada regla:

| Tabla auxiliar | Personas que incluye |
|---|---|
| `solo_prestaciones` | Todos sus episodios son de prestación o subsidio (TRL 700-799 o 400), sin empleo real. |
| `menor_autorizado` | Episodio que empieza antes de la edad mínima en el régimen 0112 o en los CNAE de espectáculos. |
| `orfandad_vitalicia` | Alguna pensión de orfandad vitalicia (incapacidad permanente). |
| `sin_190_esperable` | Episodio abierto fuera del régimen general o empleador sin NIF/CIF: no tienen modelo 190 de salarios. |

`tests/reglas_consistencia_resultados_sin_ipf.xlsx` (sin identificadores) tiene tres hojas:
**Hallazgos** (una fila por grupo de causa), **Resultados** (una fila por regla) y **Resumen**. Cada regla tiene un **estado**:

| Estado | Significado |
|---|---|
| Pasan | Cumplen: 0 incumplimientos. |
| Corregido | Se corrigió el ETL o la regla, y ya cumple o queda un residuo mínimo. |
| **Casos raros (reales)** | Personas reales con situaciones poco habituales pero posibles. No son errores. |
| **Baja calidad de los datos** | El dato de origen es incompleto o incoherente; no hay un cálculo propio que corregir. |
| Pendiente | Sin revisar. |

La última columna de **Resultados**, "Explicación (casos raros / baja calidad)", explica cada regla de las dos
categorías con las cifras que la sostienen. Estado de la edición 2024: 538 reglas; 391 pasan, 45 corregidas,
71 de baja calidad, 31 casos raros, 0 pendientes.
