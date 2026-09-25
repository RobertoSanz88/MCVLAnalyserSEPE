# Varias muestras de la MCVL en la app (v2.2)

Fecha: 2026-09-25. Estado: borrador para revisión del usuario.

## Objetivo

La app trabaja hoy con una única carpeta de Parquet (una muestra). Debe poder **gestionar varias muestras**
(2024, 2023...) y trabajar **con una sola a la vez**, cambiando de una a otra desde un menú de la cabecera. Además
se añaden dos vistas informativas: **Descripción de variables** y **Acerca de**.

Fuera de alcance: comparar dos muestras a la vez, cualquier cambio del ETL y cualquier cambio de los routers de
análisis (siguen usando la vista `personas`).

## Decisiones acordadas con el usuario

1. Una muestra = una **carpeta de Parquet**. El año se lee de `fecha_corte.txt` (año de corte menos 1). Si la
   carpeta no lo tiene, se pide al añadirla y se guarda.
2. Al cambiar de muestra la app **vuelve al estado inicial**: se borran colectivo, filtros globales y resultados y
   se recarga la página.
3. **Eliminar** solo quita la muestra de la lista, con confirmación. Nunca borra ficheros del disco.
4. **Descripción de variables** y **Acerca de** son vistas a pantalla completa dentro de la misma aplicación.
5. **Acerca de** muestra el `README.md` completo; al final, dos desplegables: "Tests de consistencia" (primero) y
   "Construcción de variables".
6. El desplegable de tests lista las reglas de "Casos raros (reales)" (primero) y "Baja calidad de los datos", con
   su enunciado, personas afectadas y explicación.
7. La app arranca con **la última muestra usada**. Sin ninguna muestra (primer arranque), pide la carpeta y lee el año.
8. Enfoque técnico: **recarga en caliente** (recrear las vistas de DuckDB en el mismo proceso), sin reiniciar el servidor.

## Configuración (`config.py`)

`config.json` (junto al ejecutable, como hoy) pasa a:

```json
{"muestras": [{"carpeta": "D:/mcvl/2024", "anio": 2024}, {"carpeta": "D:/mcvl/2023", "anio": 2023}],
 "activa": "D:/mcvl/2024", "puerto": 8000}
```

- **Migración:** un `config.json` antiguo (`parquet_dir`) se convierte al formato nuevo con esa carpeta como única
  muestra y activa. Se conserva `puerto`.
- **Año:** `leer_anio(carpeta)` = año de `fecha_corte.txt` menos 1. Si no existe o no es válido, se pide y se guarda
  en la lista (`anio`).
- **Validación** al añadir o editar: la carpeta tiene `tabla_personas_extendida.parquet` y no está ya en la lista.
- **Eliminar la activa:** pasa a la muestra disponible más reciente; si no queda ninguna, vuelve al primer arranque.
- **Carpeta que ya no existe:** la muestra sigue en la lista marcada como no disponible. Si la última usada no está al
  arrancar, se abre la más reciente disponible con un aviso.
- **Primer arranque sin muestras:** selector de carpeta nativo (como hoy), validación, lectura del año, muestra activa.

## Acceso a datos (`app/db.py` y routers)

- `db.py` deja de fijar `PARQUET_DIR` al importar: lo toma de la muestra activa. Nueva función que **recrea las
  conexiones y vistas** sobre otra carpeta.
- `routers/trayectorias.py` fija hoy `PARQUET_DIR` y `FECHA_CORTE_ISO` al importar: pasan a leerse de la muestra activa
  en cada petición (o de un estado compartido que se actualiza al cambiar de muestra).
- Al activar una muestra, un cerrojo bloquea las peticiones nuevas mientras se recrean las vistas; los endpoints siguen
  usando `con.cursor()` por petición.
- Si la muestra elegida no se puede abrir (falta un Parquet, está dañado o el disco no responde), **no se cambia**:
  sigue la anterior y se avisa con el motivo.

## Endpoints nuevos

`GET /muestras` (lista, activa, disponibilidad), `POST /muestras/añadir`, `POST /muestras/editar`,
`POST /muestras/eliminar`, `POST /muestras/activar`, `GET /variables` (JSON de descripciones cruzado con las columnas
de la muestra activa), `GET /acerca-de` (README y casos raros). Añadir y editar abren el selector de carpeta de
Windows en el servidor (la app es local), igual que el primer arranque.

## Interfaz

**Cabecera**, una plantilla compartida por `colectivos.html` y `trayectoria.html`:
- Izquierda: botón de tres rayas y `Muestra AAAA` (año de la muestra activa), donde hoy está "Observatorio de las
  Ocupaciones · SEPE".
- Centro: título `MCVLAnalyserSEPE` y debajo "Observatorio de las Ocupaciones", centrados; el logo, a la izquierda del título.
- Derecha: insignia de versión (y la de "Trayectorias" en la página de trayectoria individual).

**Menú** (se cierra con clic fuera o Escape):
1. Una fila por muestra, la activa con una marca; las no disponibles en gris con "carpeta no encontrada".
2. Añadir muestra, Editar muestra, Eliminar muestra.
3. Raya divisoria: Descripción de variables y Acerca de.

**Acciones:** elegir una muestra la activa y recarga; Añadir abre el selector, valida y añade sin cambiar la activa; Editar
y Eliminar abren un cuadro con la lista para elegir la muestra (la activa por defecto); Eliminar pide confirmación
("Quitar la muestra 2023 de la lista. Los ficheros no se borran"). Los avisos usan el aviso flotante de la app, sin `alert()`.

**Descripción de variables:** selector Personas / Afiliación, caja de búsqueda (nombre y descripción) y tabla con
nombre, tipo, origen (original o derivada) y descripción; las columnas con año se muestran con el año de la muestra
activa.

**Acerca de:** README completo con los dos desplegables finales. "Construcción de variables" agrupa las secciones
"Cómo construir los datos" y "Estructura"; "Tests de consistencia" agrupa la sección de reglas más la lista de casos
raros y de baja calidad. Desde la página de trayectoria individual, las dos vistas se abren en la pestaña principal.

## Datos de las vistas y empaquetado

- `app/static/datos/variables.json`: una entrada por variable de Personas y Afiliación, con el año sustituido por
  `AAAA`. Lo genera un script a partir de `docs/esquema_datos_mcvl.md` y la guía, y se versiona. La app lo cruza con las
  columnas reales; una columna sin descripción aparece como "sin descripción".
- `app/static/datos/casos_raros.json`: las 102 reglas de casos raros (31) y baja calidad (71) de
  `tests/reglas_consistencia_resultados_sin_ipf.xlsx`, con enunciado, personas afectadas y explicación. Se regenera cuando se
  repasan las reglas (se anota en `CLAUDE.md`).
- Librería de Markdown pequeña guardada en `app/static` (sin CDN: la app funciona sin internet y con Netskope).
- `build/mcvlanalyser.spec` incorpora `README.md` y `app/static/datos`. El `.exe` se reconstruye al final con
  `build\construir.bat`.

## Pruebas

- `tests/test_muestras.py`: lógica de la lista con carpetas temporales (migración, añadir, editar, eliminar,
  validación, año, pasar a la más reciente al eliminar la activa, carpeta no disponible).
- Muestra sintética pequeña (primeras filas de cada tabla) para probar el cambio real de muestra sin copiar 1,5 GB
  (solo existe la 2024). Bloque `muestras` en `tests/smoke.py`: activa la sintética y vuelve a la 2024.
- Revisión visual del usuario de la cabecera, el menú y las dos vistas con la 2024 real.
- Documentación: `README.md`, `CHANGELOG.md` y `CLAUDE.md` al terminar.

## Riesgos

- Cualquier módulo que guarde la ruta de Parquet o la fecha de corte al importarse quedaría con la muestra anterior tras
  cambiar (hoy: `routers/trayectorias.py`). Habrá que revisar todos y cubrirlo con una prueba de cambio real.
- Peticiones largas en curso al cambiar de muestra: el cerrojo las espera; una consulta de 40-110 s puede retrasar el cambio.
