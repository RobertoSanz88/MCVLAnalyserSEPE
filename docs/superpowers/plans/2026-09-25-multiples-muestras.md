# Varias muestras de la MCVL (v2.2) — Plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** La app gestiona varias muestras de la MCVL (una carpeta de Parquet cada una), trabaja con una sola a la vez y se cambia de una a otra desde un menú de la cabecera; añade las vistas "Descripción de variables" y "Acerca de".

**Architecture:** `config.py` guarda una lista de muestras y cuál está activa (con migración del `config.json` antiguo). `app/db.py` abre una `Muestra` (conexión DuckDB + año + fecha de corte) sobre una carpeta y la sustituye de golpe en un estado compartido; `app.state.con` pasa a ser un objeto que delega en la muestra activa, así los routers de análisis no cambian. La interfaz añade una cabecera compartida con menú (`cabecera.js/css`) y dos vistas nuevas en el shell de `colectivos.html`.

**Tech Stack:** Python 3, FastAPI, DuckDB, Jinja2, JavaScript sin build, pytest (ya instalado, junto con httpx para `TestClient`).

**Spec:** `docs/superpowers/specs/2026-09-25-multiples-muestras-design.md`

## Global Constraints

- Una muestra = una carpeta de Parquet que contiene `tabla_personas_extendida.parquet`; el año sale de `fecha_corte.txt` (año de corte menos 1); si falta, se pide y se guarda.
- Al cambiar de muestra la app vuelve al estado inicial: se recarga la página, sin conservar colectivo ni filtros.
- Eliminar solo quita de la lista; nunca borra ficheros del disco.
- Arranca con la última muestra usada; sin ninguna, pide la carpeta y lee el año.
- Título `MCVLAnalyserSEPE` con "Observatorio de las Ocupaciones" debajo, centrados; a la izquierda botón de tres rayas y `Muestra AAAA`.
- Sin CDN en la app (sin internet y con Netskope): las librerías van en `app/static`.
- Los routers de análisis siguen consultando la vista `personas` con nombres limpios en snake_case y `con.cursor()` por petición.
- Los avisos usan un aviso flotante propio, nunca `alert()`/`confirm()`.
- Este PC tiene ~4 GB de RAM: avisar antes de ejecutar la generación de la muestra sintética (Task 4) y no lanzar nada en segundo plano.
- Comitear cada tarea al terminarla, sin acumular; **no subir** (push) sin que lo pida el usuario.
- Ajustes respecto al spec aprobado (a reflejar en el spec en Task 8): (1) el cambio de muestra es un intercambio atómico y no un cerrojo; (2) no se puede eliminar la **única** muestra que queda (en lugar de volver al primer arranque); (3) el estado vive en un objeto de módulo `app.db.estado`.

## Review Focus

1. `config.json` vacío o corrupto → se trata como "sin muestras", no rompe el arranque (test en Task 1).
2. Rutas con espacios y acentos (`MCVL 2024 parquet data`) y la misma carpeta escrita con barras o mayúsculas distintas → no se duplica (test en Task 1).
3. Activar una muestra cuyo Parquet está dañado → no se cambia y la anterior sigue funcionando (test en Task 4).
4. Eliminar la activa → pasa a la más reciente disponible; eliminar la única → se rechaza (tests en Tasks 1 y 4).
5. Una petición con cursor abierto durante el cambio de muestra sigue respondiendo sobre la muestra anterior (test en Task 4).

---

## File Structure

| Fichero | Responsabilidad |
|---|---|
| `config.py` (modificar) | Lista de muestras: leer/guardar/migrar `config.json`, añadir/editar/eliminar/activar, año, disponibilidad, selector de carpeta por subproceso, primer arranque |
| `run.py` (modificar) | Modo `--elegir-carpeta` (selector de carpeta en un proceso aparte, también en el `.exe`) |
| `app/db.py` (modificar) | `get_connection(parquet_dir)`, `Muestra`, `abrir_muestra`, `estado`, `ConexionActiva` |
| `app/main.py` (modificar) | Arranque con la muestra inicial; registra el router de muestras |
| `app/routers/muestras.py` (crear) | Endpoints `/muestras*`, `/variables`, `/acerca-de` |
| `app/routers/trayectorias.py` (modificar) | Fecha de corte de la muestra activa en cada petición |
| `tests/ejecutar_reglas.py` (modificar) | Carpeta de la muestra activa desde `config` |
| `tests/test_muestras.py` (crear) | Lógica de configuración con carpetas temporales |
| `tests/muestra_sintetica.py` (crear) | Construye una muestra pequeña a partir de la real |
| `tests/test_cambio_muestra.py` (crear) | Cambio de muestra de verdad con `TestClient` |
| `app/templates/_cabecera.html`, `app/static/cabecera.css`, `app/static/cabecera.js` (crear) | Cabecera compartida y menú |
| `app/static/vistas_info.js`, `app/static/vistas_info.css` (crear) | Vistas Descripción de variables y Acerca de |
| `app/static/marked.min.js` (crear) | Librería de Markdown sin CDN |
| `app/static/datos/variables.json`, `app/static/datos/casos_raros.json` (crear) | Datos de las vistas |
| `tools/generar_datos_ayuda.py` (crear) | Genera los dos JSON |
| `app/templates/colectivos.html`, `app/templates/trayectoria.html` (modificar) | Usan la cabecera; colectivos añade las dos vistas |
| `build/mcvlanalyser.spec` (modificar) | Empaqueta `README.md` |
| `README.md`, `CHANGELOG.md`, `CLAUDE.md`, spec (modificar) | Documentación |

---

### Task 1: Lista de muestras en `config.py`

**Files:**
- Modify: `config.py`
- Create: `tests/test_muestras.py`

**Interfaces:**
- Produces (todas en `config.py`):
  - `class ErrorMuestra(Exception)`, `class FaltaAnio(ErrorMuestra)`
  - `leer_config() -> dict` (`{"muestras": [{"carpeta": str, "anio": int|None}], "activa": str|None, ...}`, migra `parquet_dir`)
  - `guardar_config(cfg: dict) -> None`
  - `leer_anio(carpeta) -> int | None`
  - `anio_de(m: dict) -> int | None`
  - `carpeta_valida(carpeta) -> bool`, `disponible(m: dict) -> bool`
  - `buscar_muestra(cfg, carpeta) -> dict | None`
  - `anadir_muestra(cfg, carpeta, anio=None) -> dict`, `editar_muestra(cfg, actual, nueva, anio=None) -> dict`, `eliminar_muestra(cfg, carpeta) -> dict`, `activar_muestra(cfg, carpeta) -> dict`
  - `muestras_con_estado(cfg, activa=None) -> list[dict]` (`carpeta, anio, disponible, activa`, más recientes primero)
  - `muestra_inicial(cfg) -> tuple[dict | None, str | None]`

- [ ] **Step 1: Escribir los tests que fallan**

Crear `tests/test_muestras.py`:

```python
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402


@pytest.fixture
def cfgfile(tmp_path, monkeypatch):
    f = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", f)
    return f


def hacer_muestra(base, nombre, corte=None):
    c = Path(base) / nombre
    c.mkdir()
    (c / "tabla_personas_extendida.parquet").write_bytes(b"x")
    if corte:
        (c / "fecha_corte.txt").write_text(corte + "\n", encoding="utf-8")
    return str(c)


def test_migra_config_antiguo(cfgfile, tmp_path):
    c = hacer_muestra(tmp_path, "MCVL 2024 parquet data", "2025-05-01")
    cfgfile.write_text(json.dumps({"parquet_dir": c, "puerto": 8123}), encoding="utf-8")
    cfg = config.leer_config()
    assert cfg["muestras"] == [{"carpeta": str(Path(c)), "anio": 2024}]
    assert cfg["activa"] == str(Path(c))
    assert cfg["puerto"] == 8123 and "parquet_dir" not in cfg


@pytest.mark.parametrize("contenido", [None, "", "{no es json", "[]"])
def test_config_vacio_o_corrupto_es_sin_muestras(cfgfile, contenido):
    if contenido is not None:
        cfgfile.write_text(contenido, encoding="utf-8")
    cfg = config.leer_config()
    assert cfg["muestras"] == [] and cfg["activa"] is None


def test_leer_anio(tmp_path):
    assert config.leer_anio(hacer_muestra(tmp_path, "a", "2025-05-01")) == 2024
    assert config.leer_anio(hacer_muestra(tmp_path, "b")) is None


def test_anadir_valida_carpeta_y_lee_anio(cfgfile, tmp_path):
    cfg = config.leer_config()
    with pytest.raises(config.ErrorMuestra):
        config.anadir_muestra(cfg, str(tmp_path))  # sin tabla_personas
    c = hacer_muestra(tmp_path, "m 2023 ñ", "2024-05-01")
    config.anadir_muestra(cfg, c)
    assert cfg["muestras"][0]["anio"] == 2023 and cfg["activa"] == str(Path(c))


def test_anadir_duplicado_con_barras_y_mayusculas(cfgfile, tmp_path):
    c = hacer_muestra(tmp_path, "Muestra", "2025-05-01")
    cfg = config.leer_config()
    config.anadir_muestra(cfg, c)
    with pytest.raises(config.ErrorMuestra):
        config.anadir_muestra(cfg, c.replace("\\", "/").upper().replace("MUESTRA", "Muestra").lower())


def test_anadir_pide_anio_si_falta(cfgfile, tmp_path):
    c = hacer_muestra(tmp_path, "sin_corte")
    cfg = config.leer_config()
    with pytest.raises(config.FaltaAnio):
        config.anadir_muestra(cfg, c)
    config.anadir_muestra(cfg, c, 2022)
    assert cfg["muestras"][0]["anio"] == 2022


def test_editar_cambia_carpeta_y_mantiene_activa(cfgfile, tmp_path):
    a = hacer_muestra(tmp_path, "a", "2025-05-01")
    b = hacer_muestra(tmp_path, "b", "2024-05-01")
    cfg = config.leer_config()
    config.anadir_muestra(cfg, a)
    config.editar_muestra(cfg, a, b)
    assert [m["carpeta"] for m in cfg["muestras"]] == [str(Path(b))]
    assert cfg["activa"] == str(Path(b)) and cfg["muestras"][0]["anio"] == 2023


def test_eliminar_activa_pasa_a_la_mas_reciente_y_no_borra_ficheros(cfgfile, tmp_path):
    a = hacer_muestra(tmp_path, "a", "2025-05-01")  # 2024
    b = hacer_muestra(tmp_path, "b", "2024-05-01")  # 2023
    c = hacer_muestra(tmp_path, "c", "2023-05-01")  # 2022
    cfg = config.leer_config()
    for x in (a, b, c):
        config.anadir_muestra(cfg, x)
    config.activar_muestra(cfg, c)
    config.eliminar_muestra(cfg, c)
    assert cfg["activa"] == str(Path(a))
    assert (Path(c) / "tabla_personas_extendida.parquet").exists()


def test_eliminar_la_unica_se_rechaza(cfgfile, tmp_path):
    a = hacer_muestra(tmp_path, "a", "2025-05-01")
    cfg = config.leer_config()
    config.anadir_muestra(cfg, a)
    with pytest.raises(config.ErrorMuestra):
        config.eliminar_muestra(cfg, a)
    assert len(cfg["muestras"]) == 1


def test_muestra_inicial_usa_la_activa_o_la_mas_reciente_con_aviso(cfgfile, tmp_path):
    a = hacer_muestra(tmp_path, "a", "2025-05-01")  # 2024
    b = hacer_muestra(tmp_path, "b", "2024-05-01")  # 2023
    cfg = config.leer_config()
    config.anadir_muestra(cfg, a)
    config.anadir_muestra(cfg, b)
    config.activar_muestra(cfg, b)
    m, aviso = config.muestra_inicial(cfg)
    assert m["carpeta"] == str(Path(b)) and aviso is None
    (Path(b) / "tabla_personas_extendida.parquet").unlink()  # carpeta "movida"
    m, aviso = config.muestra_inicial(cfg)
    assert m["carpeta"] == str(Path(a)) and "2024" in aviso
    estados = config.muestras_con_estado(cfg)
    assert [e["disponible"] for e in estados] == [True, False]


def test_guardar_y_releer(cfgfile, tmp_path):
    a = hacer_muestra(tmp_path, "a", "2025-05-01")
    cfg = config.leer_config()
    config.anadir_muestra(cfg, a)
    config.guardar_config(cfg)
    assert config.leer_config() == cfg
```

- [ ] **Step 2: Ejecutar los tests y comprobar que fallan**

Run: `python -m pytest tests/test_muestras.py -q`
Expected: FAIL (`AttributeError: module 'config' has no attribute 'leer_config'` o similar).

- [ ] **Step 3: Implementar en `config.py`**

Añadir `import os` al bloque de imports. Después de `leer_fecha_corte` (y antes de `PUERTO_POR_DEFECTO`), insertar:

```python
# --- Varias muestras (v2.2) -------------------------------------------------------------------------------
# config.json: {"muestras": [{"carpeta": ..., "anio": ...}], "activa": <carpeta>|null, "puerto": ...}.
# Un config.json anterior ({"parquet_dir": ...}) se migra en memoria a una lista de una muestra.


class ErrorMuestra(Exception):
    """Error de validacion al gestionar la lista de muestras (mensaje apto para mostrar al usuario)."""


class FaltaAnio(ErrorMuestra):
    """La carpeta no tiene fecha_corte.txt y no se ha indicado el anio."""


def _clave(carpeta) -> str:
    return os.path.normcase(os.path.normpath(str(carpeta)))


def carpeta_valida(carpeta) -> bool:
    return (Path(carpeta) / _FICHERO_CLAVE_PARQUET).exists()


def leer_anio(carpeta):
    """Anio de la edicion (anio de corte - 1) segun fecha_corte.txt; None si no existe o no es valida."""
    try:
        texto = Path(carpeta, FICHERO_FECHA_CORTE).read_text(encoding="utf-8").strip()
        return datetime.date.fromisoformat(texto).year - 1
    except (OSError, ValueError):
        return None


def anio_de(m: dict):
    return leer_anio(m["carpeta"]) or m.get("anio")


def disponible(m: dict) -> bool:
    return carpeta_valida(m["carpeta"])


def leer_config() -> dict:
    data = {}
    if CONFIG_FILE.exists():
        try:
            leido = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(leido, dict):
                data = leido
        except (OSError, ValueError):
            data = {}
    if "muestras" not in data:
        antigua = data.pop("parquet_dir", None)
        data["muestras"] = []
        data["activa"] = None
        if antigua:
            carpeta = str(Path(antigua))
            data["muestras"] = [{"carpeta": carpeta, "anio": leer_anio(carpeta)}]
            data["activa"] = carpeta
    data.pop("parquet_dir", None)
    data.setdefault("activa", None)
    return data


def guardar_config(cfg: dict) -> None:
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def buscar_muestra(cfg: dict, carpeta):
    for m in cfg["muestras"]:
        if _clave(m["carpeta"]) == _clave(carpeta):
            return m
    return None


def _mas_reciente_disponible(cfg: dict):
    disp = [m for m in cfg["muestras"] if disponible(m)]
    return max(disp, key=lambda m: anio_de(m) or 0) if disp else None


def anadir_muestra(cfg: dict, carpeta, anio=None) -> dict:
    if not carpeta_valida(carpeta):
        raise ErrorMuestra(f"Esa carpeta no tiene {_FICHERO_CLAVE_PARQUET}.")
    if buscar_muestra(cfg, carpeta):
        raise ErrorMuestra("Esa carpeta ya está en la lista de muestras.")
    a = leer_anio(carpeta) or anio
    if a is None:
        raise FaltaAnio("La carpeta no tiene fecha_corte.txt: indica el año de la muestra.")
    cfg["muestras"].append({"carpeta": str(Path(carpeta)), "anio": int(a)})
    if not cfg.get("activa"):
        cfg["activa"] = str(Path(carpeta))
    return cfg


def editar_muestra(cfg: dict, actual, nueva, anio=None) -> dict:
    m = buscar_muestra(cfg, actual)
    if m is None:
        raise ErrorMuestra("La muestra no está en la lista.")
    if not carpeta_valida(nueva):
        raise ErrorMuestra(f"Esa carpeta no tiene {_FICHERO_CLAVE_PARQUET}.")
    otra = buscar_muestra(cfg, nueva)
    if otra is not None and otra is not m:
        raise ErrorMuestra("Esa carpeta ya está en la lista de muestras.")
    a = leer_anio(nueva) or anio
    if a is None:
        raise FaltaAnio("La carpeta no tiene fecha_corte.txt: indica el año de la muestra.")
    era_activa = cfg.get("activa") and _clave(cfg["activa"]) == _clave(m["carpeta"])
    m["carpeta"], m["anio"] = str(Path(nueva)), int(a)
    if era_activa:
        cfg["activa"] = m["carpeta"]
    return cfg


def eliminar_muestra(cfg: dict, carpeta) -> dict:
    """Quita la muestra de la lista. NUNCA borra ficheros. La unica que queda no se puede quitar."""
    m = buscar_muestra(cfg, carpeta)
    if m is None:
        raise ErrorMuestra("La muestra no está en la lista.")
    if len(cfg["muestras"]) == 1:
        raise ErrorMuestra("Es la única muestra: añade otra antes de quitarla.")
    era_activa = cfg.get("activa") and _clave(cfg["activa"]) == _clave(m["carpeta"])
    cfg["muestras"].remove(m)
    if era_activa:
        nueva = _mas_reciente_disponible(cfg)
        cfg["activa"] = nueva["carpeta"] if nueva else None
    return cfg


def activar_muestra(cfg: dict, carpeta) -> dict:
    m = buscar_muestra(cfg, carpeta)
    if m is None:
        raise ErrorMuestra("La muestra no está en la lista.")
    if not disponible(m):
        raise ErrorMuestra("La carpeta de esta muestra no está disponible.")
    cfg["activa"] = m["carpeta"]
    return cfg


def muestras_con_estado(cfg: dict, activa=None) -> list:
    activa = activa if activa is not None else cfg.get("activa")
    filas = [{"carpeta": m["carpeta"], "anio": anio_de(m), "disponible": disponible(m),
              "activa": bool(activa) and _clave(activa) == _clave(m["carpeta"])} for m in cfg["muestras"]]
    return sorted(filas, key=lambda f: f["anio"] or 0, reverse=True)


def muestra_inicial(cfg: dict):
    """(muestra, aviso): la activa si esta disponible; si no, la mas reciente disponible con un aviso."""
    if cfg.get("activa"):
        m = buscar_muestra(cfg, cfg["activa"])
        if m is not None and disponible(m):
            return m, None
    m = _mas_reciente_disponible(cfg)
    if m is None:
        return None, None
    aviso = f"La última muestra usada no está disponible; se abre la {anio_de(m)}." if cfg.get("activa") else None
    return m, aviso
```

- [ ] **Step 4: Ejecutar los tests y comprobar que pasan**

Run: `python -m pytest tests/test_muestras.py -q`
Expected: todos PASS.

- [ ] **Step 5: Commit**

```bash
git add config.py tests/test_muestras.py
git commit -m "config: lista de muestras con migracion del config.json antiguo"
```

---

### Task 2: Selector de carpeta por subproceso y primer arranque

**Files:**
- Modify: `config.py` (sustituir `get_parquet_dir`, añadir `elegir_carpeta_dialogo`, `pedir_primera_muestra`)
- Modify: `run.py`

**Interfaces:**
- Consumes: `leer_config`, `guardar_config`, `anadir_muestra`, `carpeta_valida`, `leer_anio`, `FaltaAnio` (Task 1).
- Produces: `elegir_carpeta_dialogo(titulo: str) -> str | None`; `pedir_primera_muestra(cfg: dict) -> dict` (devuelve `cfg` con una muestra añadida y activa; lanza `RuntimeError` si el usuario cancela).

- [ ] **Step 1: Sustituir `_pedir_carpeta_parquet` y `get_parquet_dir` en `config.py`**

Borrar la función `get_parquet_dir` (lo único que la importaba era `app/db.py`, que cambia en Task 3) y reemplazar `_pedir_carpeta_parquet` por:

```python
def _dialogo_carpeta(titulo: str) -> str:
    """Selector de carpeta nativo. Devuelve '' si se cancela."""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        return filedialog.askdirectory(title=titulo) or ""
    finally:
        root.destroy()


def elegir_carpeta_dialogo(titulo: str):
    """Abre el selector de carpeta en un PROCESO APARTE (el servidor es multihilo y Tk no debe usarse desde
    un hilo de peticion). En el .exe se relanza el propio ejecutable con --elegir-carpeta (ver run.py)."""
    import subprocess

    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "--elegir-carpeta", titulo]
    else:
        cmd = [sys.executable, str(Path(__file__).resolve().parent / "run.py"), "--elegir-carpeta", titulo]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=900)
    lineas = [x for x in r.stdout.splitlines() if x.strip()]
    return lineas[-1].strip() if r.returncode == 0 and lineas else None


def pedir_primera_muestra(cfg: dict) -> dict:
    """Primer arranque (sin muestras): pide la carpeta (validada) y, si no tiene fecha_corte.txt, el anio."""
    from tkinter import messagebox, simpledialog
    import tkinter as tk

    while True:
        carpeta = _dialogo_carpeta("Selecciona la carpeta con los Parquet de la MCVL")
        if not carpeta:
            raise RuntimeError("No se selecciono ninguna carpeta de Parquet.")
        if carpeta_valida(carpeta):
            break
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        messagebox.showerror("Carpeta no válida", f"Esa carpeta no tiene {_FICHERO_CLAVE_PARQUET}.\n\n"
                             "Selecciona la carpeta correcta con los Parquet de la MCVL.")
        root.destroy()
    anio = None
    if leer_anio(carpeta) is None:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        anio = simpledialog.askinteger("Año de la muestra", "Esta carpeta no indica el año de la MCVL.\nAño:",
                                       minvalue=2004, maxvalue=2100, parent=root)
        root.destroy()
        if anio is None:
            raise RuntimeError("No se indico el año de la muestra.")
    anadir_muestra(cfg, carpeta, anio)
    guardar_config(cfg)
    return cfg
```

- [ ] **Step 2: Modo `--elegir-carpeta` en `run.py`**

Justo después de `multiprocessing.freeze_support()` (dentro de `if __name__ == "__main__":`) añadir:

```python
    if "--elegir-carpeta" in sys.argv:
        # Subproceso del selector de carpeta (lo lanza config.elegir_carpeta_dialogo): sin importar app.main.
        from config import _dialogo_carpeta

        titulo = sys.argv[sys.argv.index("--elegir-carpeta") + 1] if len(sys.argv) > sys.argv.index("--elegir-carpeta") + 1 else "Selecciona una carpeta"
        sys.stdout.reconfigure(encoding="utf-8")
        print(_dialogo_carpeta(titulo))
        sys.exit(0)
```

- [ ] **Step 3: Probar a mano el selector**

Run: `python -c "import config; print(repr(config.elegir_carpeta_dialogo('Prueba')))"`
Expected: se abre el selector de carpeta; al elegir una carpeta con espacios y acentos imprime su ruta (p. ej. `'C:/Users/USUARIO/MCVLAnalyserSEPE/parquet/MCVL 2024 parquet data'`); al cancelar imprime `None`. **Pedir al usuario que elija la carpeta y confirme.**

- [ ] **Step 4: Comprobar que los tests siguen pasando y comitear**

Run: `python -m pytest tests/test_muestras.py -q`
Expected: PASS.

```bash
git add config.py run.py
git commit -m "config: selector de carpeta en subproceso y primer arranque con año"
```

---

### Task 3: `db.py` con muestra activa y arranque de la app

**Files:**
- Modify: `app/db.py`, `app/main.py`, `app/routers/trayectorias.py:20-32,939,953`, `tests/ejecutar_reglas.py:22,82`

**Interfaces:**
- Consumes: `config.leer_config`, `config.muestra_inicial`, `config.anio_de`, `config.leer_fecha_corte`, `config.pedir_primera_muestra`, `config.guardar_config`.
- Produces (`app/db.py`):
  - `get_connection(parquet_dir) -> duckdb.DuckDBPyConnection`
  - `class Muestra` con `carpeta: Path`, `anio: int`, `con`, `fecha_corte: str`
  - `abrir_muestra(carpeta, anio) -> Muestra` (lanza excepción si no se puede leer)
  - `estado` (objeto de módulo): `estado.muestra: Muestra | None`, `estado.aviso_arranque: str | None`
  - `class ConexionActiva` con `cursor()`; `app.state.con = ConexionActiva()`

- [ ] **Step 1: Cambiar `app/db.py`**

Sustituir el import y la constante (`from config import get_parquet_dir` / `PARQUET_DIR = get_parquet_dir()`) por:

```python
from pathlib import Path

from config import leer_fecha_corte
```

Cambiar la firma de `get_connection` y la ruta:

```python
def get_connection(parquet_dir) -> duckdb.DuckDBPyConnection:
    """Abre una conexion DuckDB en memoria y registra una vista por tabla MCVL sobre 'parquet_dir',
    con columnas ya renombradas a snake_case."""
    parquet_dir = Path(parquet_dir)
    con = duckdb.connect(database=":memory:")
    for view_name, (filename, columns) in TABLES.items():
        parquet_path = parquet_dir / filename
```

(el resto del cuerpo no cambia). Añadir al final del fichero:

```python
class Muestra:
    """Una muestra abierta: carpeta de Parquet, anio de la edicion, conexion DuckDB con las vistas y fecha de corte."""

    def __init__(self, carpeta, anio, con, fecha_corte):
        self.carpeta, self.anio, self.con, self.fecha_corte = Path(carpeta), anio, con, fecha_corte


def abrir_muestra(carpeta, anio) -> Muestra:
    """Abre y valida la muestra (lanza si falta un Parquet o esta danado). No toca el estado global."""
    con = get_connection(carpeta)
    con.execute("SELECT 1 FROM personas LIMIT 1").fetchone()
    return Muestra(carpeta, anio, con, leer_fecha_corte(carpeta))


class Estado:
    muestra = None
    aviso_arranque = None


estado = Estado()


class ConexionActiva:
    """app.state.con: delega en la muestra activa en el momento de cada peticion. El cambio de muestra es un
    intercambio atomico de estado.muestra; las peticiones en curso (tambien las de streaming) siguen con el cursor
    que ya tenian, sobre la muestra anterior, cuya conexion se libera al soltarse su ultimo cursor."""

    def cursor(self):
        return estado.muestra.con.cursor()
```

- [ ] **Step 2: Cambiar `app/main.py`**

```python
"""FastAPI app: analisis de la MCVL sobre Parquet local via DuckDB."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

import config
from app.db import ConexionActiva, abrir_muestra, estado
from app.routers import colectivos, muestras, trayectorias

app = FastAPI(title="MCVL Analyser SEPE")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
app.include_router(trayectorias.router)
app.include_router(colectivos.router)
app.include_router(muestras.router)


@app.on_event("startup")
def startup():
    # Los endpoints NO usan una Connection compartida directamente (peticiones concurrentes la corrompen):
    # cada uno saca su cursor con request.app.state.con.cursor(). app.state.con delega en la muestra activa.
    cfg = config.leer_config()
    m, aviso = config.muestra_inicial(cfg)
    if m is None:
        cfg = config.pedir_primera_muestra(cfg)
        m, aviso = config.muestra_inicial(cfg)
    estado.muestra = abrir_muestra(m["carpeta"], config.anio_de(m))
    estado.aviso_arranque = aviso
    if cfg.get("activa") != m["carpeta"]:
        cfg["activa"] = m["carpeta"]
    config.guardar_config(cfg)  # persiste tambien la migracion del config.json antiguo
    app.state.con = ConexionActiva()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def raiz():
    return RedirectResponse("/colectivos")
```

- [ ] **Step 3: Router `muestras.py` provisional (solo para que importe)**

Crear `app/routers/muestras.py` con el mínimo que Task 4 completa:

```python
"""Gestion de muestras (carpetas de Parquet) y cambio de la muestra activa."""
from fastapi import APIRouter

router = APIRouter()
```

- [ ] **Step 4: `routers/trayectorias.py`: fecha de corte en cada petición**

Quitar `from app.db import PARQUET_DIR`, `from config import leer_fecha_corte` y el bloque `FECHA_CORTE_ISO = leer_fecha_corte(PARQUET_DIR)` (líneas 20-32 con su comentario). Añadir `from app.db import estado`. Sustituir las dos apariciones de `FECHA_CORTE_ISO` (líneas ~939 y ~953) por `estado.muestra.fecha_corte`.

Run: `grep -n "FECHA_CORTE_ISO\|PARQUET_DIR" app/routers/trayectorias.py`
Expected: sin resultados.

- [ ] **Step 5: `tests/ejecutar_reglas.py`**

Sustituir `from app.db import get_connection, PARQUET_DIR` por:

```python
import config
from app.db import get_connection

_m, _ = config.muestra_inicial(config.leer_config())
if _m is None:
    raise SystemExit("No hay ninguna muestra configurada (config.json).")
PARQUET_DIR = Path(_m["carpeta"])
```

Run: `grep -n "get_connection(" tests/ejecutar_reglas.py` y cambiar cada llamada `get_connection()` por `get_connection(PARQUET_DIR)`. Comprobar que `Path` está importado en el fichero (añadir `from pathlib import Path` si no).

- [ ] **Step 6: Comprobar que la app arranca con la muestra real**

Run (primer plano, con el servidor parado tras la prueba): `python -c "from fastapi.testclient import TestClient; from app.main import app; c = TestClient(app).__enter__(); print(c.get('/health').json(), c.get('/colectivos/columnas').status_code)"`
Expected: `{'status': 'ok'} 200`. (Esto migra y reescribe `config.json` local a `{"muestras": [...], "activa": ...}`.)

Run: `python -m pytest tests/test_muestras.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/db.py app/main.py app/routers/muestras.py app/routers/trayectorias.py tests/ejecutar_reglas.py
git commit -m "app: la conexion y la fecha de corte salen de la muestra activa"
```

---

### Task 4: Endpoints de muestras y prueba de cambio real

**Files:**
- Modify: `app/routers/muestras.py`
- Create: `tests/muestra_sintetica.py`, `tests/test_cambio_muestra.py`

**Interfaces:**
- Consumes: todo lo de Tasks 1-3.
- Produces (HTTP, JSON):
  - `GET /muestras` → `{"activa": str, "anio": int, "muestras": [{carpeta, anio, disponible, activa}], "aviso": str|null}`
  - `POST /muestras/anadir` `{carpeta?, anio?}` → lista actualizada, o `{"cancelado": true}`; 400 con `detail` si no valida; 409 con `detail: {"falta_anio": true, "carpeta": str, "mensaje": str}`
  - `POST /muestras/editar` `{actual, carpeta?, anio?}` → lista actualizada (mismos errores)
  - `POST /muestras/eliminar` `{carpeta}` → lista actualizada
  - `POST /muestras/activar` `{carpeta}` → lista actualizada; 400 si no se puede abrir (la anterior sigue activa)

- [ ] **Step 1: Generador de muestra sintética**

Crear `tests/muestra_sintetica.py`:

```python
"""Construye una muestra pequena (las primeras N personas de cada tabla) a partir de una carpeta de Parquet real,
para probar el cambio de muestra sin copiar 1,5 GB. Uso: python tests/muestra_sintetica.py <origen> <destino> <n> <fecha_corte>"""
import sys
from pathlib import Path

import duckdb

TABLAS = [
    "tabla_personas_extendida.parquet", "tabla_afiliacion_extendida.parquet",
    "tabla_bases_cotizacion_CA_optimizada.parquet", "tabla_bases_cotizacion_CP_optimizada.parquet",
    "tabla_pensiones_extendida.parquet",
]


def crear_muestra_sintetica(origen, destino, n_personas: int, corte: str) -> Path:
    origen, destino = Path(origen), Path(destino)
    destino.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()
    con.execute("SET threads=2")
    per = (origen / TABLAS[0]).as_posix()
    con.execute(f'CREATE TEMP TABLE ids AS SELECT "01 IPF" AS ipf FROM read_parquet(\'{per}\') ORDER BY 1 LIMIT {n_personas}')
    for f in TABLAS:
        con.execute(
            f"COPY (SELECT t.* FROM read_parquet('{(origen / f).as_posix()}') t "
            f'WHERE t."01 IPF" IN (SELECT ipf FROM ids)) TO \'{(destino / f).as_posix()}\' (FORMAT parquet)'
        )
    (destino / "fecha_corte.txt").write_text(corte + "\n", encoding="utf-8")
    return destino


if __name__ == "__main__":
    o, d, n, c = sys.argv[1:5]
    print(crear_muestra_sintetica(o, d, int(n), c))
```

- [ ] **Step 2: Escribir los tests que fallan**

Crear `tests/test_cambio_muestra.py`:

```python
import json
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tests"))
import config  # noqa: E402
from muestra_sintetica import crear_muestra_sintetica  # noqa: E402

REAL = None
try:
    _m, _ = config.muestra_inicial(config.leer_config())
    REAL = Path(_m["carpeta"]) if _m else None
except Exception:
    REAL = None

pytestmark = pytest.mark.skipif(REAL is None, reason="no hay muestra real configurada")
BASE = RAIZ / "parquet" / "_sinteticas"


@pytest.fixture(scope="session")
def muestras_sinteticas():
    a, b = BASE / "s2024", BASE / "s2023"
    if not (a / "tabla_pensiones_extendida.parquet").exists():
        crear_muestra_sintetica(REAL, a, 300, "2025-05-01")
    if not (b / "tabla_pensiones_extendida.parquet").exists():
        crear_muestra_sintetica(REAL, b, 200, "2024-05-01")
    return str(a), str(b)


@pytest.fixture
def cliente(tmp_path, monkeypatch, muestras_sinteticas):
    a, b = muestras_sinteticas
    f = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", f)
    f.write_text(json.dumps({"muestras": [{"carpeta": a, "anio": 2024}, {"carpeta": b, "anio": 2023}], "activa": a}),
                 encoding="utf-8")
    from app.main import app
    with TestClient(app) as c:
        yield c, a, b


def _n(c):
    return c.app.state.con.cursor().execute("SELECT count(*) FROM personas").fetchone()[0]


def test_activar_cambia_datos_anio_y_fecha_de_corte(cliente):
    c, a, b = cliente
    r = c.get("/muestras").json()
    assert r["anio"] == 2024 and _n(c) == 300
    r = c.post("/muestras/activar", json={"carpeta": b}).json()
    assert r["anio"] == 2023 and _n(c) == 200
    from app.db import estado
    assert estado.muestra.fecha_corte == "2024-05-01"
    assert json.loads(config.CONFIG_FILE.read_text(encoding="utf-8"))["activa"] == str(Path(b))


def test_activar_muestra_danada_no_cambia(cliente, tmp_path):
    c, a, b = cliente
    mala = tmp_path / "mala"
    shutil.copytree(b, mala)
    (mala / "tabla_afiliacion_extendida.parquet").write_bytes(b"esto no es parquet")
    cfg = config.leer_config()
    config.anadir_muestra(cfg, str(mala), 2020)
    config.guardar_config(cfg)
    r = c.post("/muestras/activar", json={"carpeta": str(mala)})
    assert r.status_code == 400 and "No se pudo abrir" in r.json()["detail"]
    assert c.get("/muestras").json()["anio"] == 2024 and _n(c) == 300


def test_peticion_en_curso_sigue_en_la_muestra_anterior(cliente):
    c, a, b = cliente
    cursor = c.app.state.con.cursor()  # peticion "en curso" (p. ej. un streaming)
    c.post("/muestras/activar", json={"carpeta": b})
    assert cursor.execute("SELECT count(*) FROM personas").fetchone()[0] == 300
    assert _n(c) == 200


def test_eliminar_activa_pasa_a_la_otra_y_la_unica_se_rechaza(cliente):
    c, a, b = cliente
    r = c.post("/muestras/eliminar", json={"carpeta": a}).json()
    assert r["anio"] == 2023 and _n(c) == 200 and len(r["muestras"]) == 1
    assert Path(a, "tabla_personas_extendida.parquet").exists()  # no se borra nada
    r = c.post("/muestras/eliminar", json={"carpeta": b})
    assert r.status_code == 400


def test_anadir_con_carpeta_sin_anio_devuelve_409_y_luego_funciona(cliente, tmp_path, muestras_sinteticas):
    c, a, b = cliente
    sin = tmp_path / "sin_corte"
    shutil.copytree(b, sin)
    (sin / "fecha_corte.txt").unlink()
    r = c.post("/muestras/anadir", json={"carpeta": str(sin)})
    assert r.status_code == 409 and r.json()["detail"]["falta_anio"] is True
    r = c.post("/muestras/anadir", json={"carpeta": str(sin), "anio": 2019})
    assert r.status_code == 200 and {m["anio"] for m in r.json()["muestras"]} == {2024, 2023, 2019}


def test_editar_muestra_activa_reabre_la_conexion(cliente, tmp_path):
    c, a, b = cliente
    movida = tmp_path / "movida"
    shutil.copytree(a, movida)
    r = c.post("/muestras/editar", json={"actual": a, "carpeta": str(movida)})
    assert r.status_code == 200 and _n(c) == 300
    from app.db import estado
    assert estado.muestra.carpeta == movida
```

- [ ] **Step 3: Generar las muestras sintéticas (avisar antes al usuario)**

Avisar: "Voy a generar dos muestras pequeñas a partir de la real; lee Afiliación entera y tarda 1-2 minutos. Usa 2 hilos." Con el visto bueno:

Run: `python -c "import sys; sys.path.insert(0,'tests'); from muestra_sintetica import crear_muestra_sintetica as c; from pathlib import Path; import config; o=Path(config.muestra_inicial(config.leer_config())[0]['carpeta']); c(o,'parquet/_sinteticas/s2024',300,'2025-05-01'); c(o,'parquet/_sinteticas/s2023',200,'2024-05-01')"`
Expected: se crean ambas carpetas con 5 Parquet y `fecha_corte.txt` (`parquet/` está en `.gitignore`).

- [ ] **Step 4: Ejecutar los tests y comprobar que fallan**

Run: `python -m pytest tests/test_cambio_muestra.py -q -x`
Expected: FAIL (404 en `/muestras...`).

- [ ] **Step 5: Implementar `app/routers/muestras.py`**

```python
"""Gestion de muestras (carpetas de Parquet) y cambio de la muestra activa."""
import copy
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import config
from app.db import abrir_muestra, estado

router = APIRouter()
_bloqueo = threading.Lock()  # serializa las operaciones sobre config.json y el cambio de muestra


class MuestraBody(BaseModel):
    carpeta: str | None = None
    actual: str | None = None
    anio: int | None = None


def _respuesta(cfg, **extra):
    m = estado.muestra
    return {"activa": str(m.carpeta), "anio": m.anio,
            "muestras": config.muestras_con_estado(cfg, activa=str(m.carpeta)), **extra}


def _error(e):
    if isinstance(e, config.FaltaAnio):
        return HTTPException(409, detail={"falta_anio": True, "mensaje": str(e)})
    return HTTPException(400, detail=str(e))


def _abrir_o_400(m):
    try:
        return abrir_muestra(m["carpeta"], config.anio_de(m))
    except Exception as e:  # falta un Parquet, esta danado, disco no disponible...
        raise HTTPException(400, detail=f"No se pudo abrir la muestra: {e}")


@router.get("/muestras")
def listar():
    aviso, estado.aviso_arranque = estado.aviso_arranque, None
    return _respuesta(config.leer_config(), aviso=aviso)


@router.post("/muestras/anadir")
def anadir(body: MuestraBody):
    carpeta = body.carpeta or config.elegir_carpeta_dialogo("Selecciona la carpeta con los Parquet de la muestra")
    if not carpeta:
        return {"cancelado": True}
    with _bloqueo:
        cfg = config.leer_config()
        try:
            config.anadir_muestra(cfg, carpeta, body.anio)
        except config.FaltaAnio as e:
            raise HTTPException(409, detail={"falta_anio": True, "carpeta": carpeta, "mensaje": str(e)})
        except config.ErrorMuestra as e:
            raise _error(e)
        cfg["activa"] = str(estado.muestra.carpeta)  # anadir nunca cambia la muestra activa
        config.guardar_config(cfg)
        return _respuesta(cfg)


@router.post("/muestras/editar")
def editar(body: MuestraBody):
    if not body.actual:
        raise HTTPException(400, detail="Falta la muestra a editar.")
    nueva = body.carpeta or config.elegir_carpeta_dialogo("Selecciona la nueva carpeta de la muestra")
    if not nueva:
        return {"cancelado": True}
    with _bloqueo:
        cfg = config.leer_config()
        era_activa = config._clave(estado.muestra.carpeta) == config._clave(body.actual)
        try:
            config.editar_muestra(cfg, body.actual, nueva, body.anio)
        except config.ErrorMuestra as e:
            if isinstance(e, config.FaltaAnio):
                raise HTTPException(409, detail={"falta_anio": True, "carpeta": nueva, "mensaje": str(e)})
            raise _error(e)
        if era_activa:
            m = config.buscar_muestra(cfg, nueva)
            estado.muestra = _abrir_o_400(m)
        cfg["activa"] = str(estado.muestra.carpeta)
        config.guardar_config(cfg)
        return _respuesta(cfg)


@router.post("/muestras/eliminar")
def eliminar(body: MuestraBody):
    with _bloqueo:
        cfg = config.leer_config()
        nuevo = copy.deepcopy(cfg)
        try:
            config.eliminar_muestra(nuevo, body.carpeta)
        except config.ErrorMuestra as e:
            raise _error(e)
        if config._clave(estado.muestra.carpeta) == config._clave(body.carpeta):
            siguiente, _ = config.muestra_inicial(nuevo)
            if siguiente is None:
                raise HTTPException(400, detail="No queda ninguna otra muestra disponible.")
            estado.muestra = _abrir_o_400(siguiente)
            nuevo["activa"] = str(estado.muestra.carpeta)
        else:
            nuevo["activa"] = str(estado.muestra.carpeta)
        config.guardar_config(nuevo)
        return _respuesta(nuevo)


@router.post("/muestras/activar")
def activar(body: MuestraBody):
    with _bloqueo:
        cfg = config.leer_config()
        try:
            config.activar_muestra(cfg, body.carpeta)
        except config.ErrorMuestra as e:
            raise _error(e)
        m = config.buscar_muestra(cfg, body.carpeta)
        estado.muestra = _abrir_o_400(m)  # solo se sustituye si se abrio bien
        config.guardar_config(cfg)
        return _respuesta(cfg)
```

- [ ] **Step 6: Ejecutar los tests y comprobar que pasan**

Run: `python -m pytest tests/test_cambio_muestra.py tests/test_muestras.py -q`
Expected: todos PASS.

- [ ] **Step 7: Commit**

```bash
git add app/routers/muestras.py tests/muestra_sintetica.py tests/test_cambio_muestra.py
git commit -m "app: endpoints de muestras con intercambio atomico y pruebas de cambio real"
```

---

### Task 5: Cabecera compartida y menú de muestras

**Files:**
- Create: `app/templates/_cabecera.html`, `app/static/cabecera.css`, `app/static/cabecera.js`
- Modify: `app/templates/colectivos.html:498-507`, `app/templates/trayectoria.html:74-84`

**Interfaces:**
- Consumes: endpoints de Task 4.
- Produces (JS global en `cabecera.js`): `cabAvisar(mensaje, tipo)`, `cabAbrirInfo(vista)` (`'variables'` o `'acerca'`; si la página no tiene `contenido-<vista>`, navega a `/colectivos#<vista>`).

- [ ] **Step 1: Crear `app/templates/_cabecera.html`**

```html
<header class="cabecera">
    <div class="cab-izq">
        <button id="cab-boton-menu" type="button" aria-label="Menú de muestras" aria-expanded="false"
                onclick="cabAlternarMenu(event)">&#9776;</button>
        <span id="cab-muestra">Muestra …</span>
        <div id="cab-menu" class="cab-menu" hidden></div>
    </div>
    <div class="cab-centro">
        <div class="logo"><img src="/static/icon.ico" alt=""></div>
        <div class="header-text">
            <h1>MCVLAnalyserSEPE</h1>
            <p>Observatorio de las Ocupaciones</p>
        </div>
    </div>
    <div class="header-badges">
        <span class="badge">v2.2</span>
        {% if insignia_extra %}<span class="badge">{{ insignia_extra }}</span>{% endif %}
    </div>
</header>
<div id="cab-aviso" class="cab-aviso" hidden></div>
<link rel="stylesheet" href="/static/cabecera.css">
<script src="/static/cabecera.js"></script>
```

- [ ] **Step 2: Crear `app/static/cabecera.css`**

```css
header.cabecera { display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; gap: 0.75rem; }
.cab-izq { display: flex; align-items: center; gap: 0.6rem; position: relative; justify-self: start; }
#cab-boton-menu { width: 38px; height: 38px; border: 1px solid rgba(255,255,255,0.45); border-radius: 6px;
                  background: transparent; color: #fff; font-size: 1.3rem; cursor: pointer; line-height: 1; }
#cab-boton-menu:hover, #cab-boton-menu[aria-expanded="true"] { background: rgba(255,255,255,0.16); }
#cab-muestra { color: #fff; font-weight: 700; font-size: 1rem; white-space: nowrap; }
.cab-centro { display: flex; align-items: center; gap: 0.75rem; justify-self: center; text-align: center; }
.cab-centro .header-text { text-align: center; }
header.cabecera .header-badges { margin-left: 0; justify-self: end; }
.cab-menu { position: absolute; top: 46px; left: 0; z-index: 1000; min-width: 260px; background: #fff; color: #1A2E26;
            border: 1px solid #b9cfc6; border-radius: 6px; box-shadow: 0 6px 20px rgba(0,0,0,0.25); padding: 0.3rem 0; }
.cab-menu[hidden] { display: none; }
.cab-item { display: flex; align-items: center; gap: 0.5rem; padding: 0.5rem 0.9rem; cursor: pointer; font-size: 0.92rem; }
.cab-item:hover { background: #e8f1ed; }
.cab-item.activa { font-weight: 800; }
.cab-item .marca { width: 1rem; color: #2f7d5b; }
.cab-item.no-disponible { color: #8a8a8a; cursor: not-allowed; }
.cab-item.no-disponible:hover { background: transparent; }
.cab-item small { color: #8a8a8a; margin-left: auto; }
.cab-sep { height: 1px; background: #b9cfc6; margin: 0.3rem 0; }
.cab-aviso { position: fixed; top: 74px; left: 50%; transform: translateX(-50%); z-index: 2000; max-width: 70vw;
             background: #fff; color: #1A2E26; border: 1px solid #2f7d5b; border-radius: 6px; padding: 0.6rem 1rem;
             box-shadow: 0 4px 14px rgba(0,0,0,0.25); font-size: 0.9rem; }
.cab-aviso.error { border-color: #b3261e; color: #b3261e; }
.cab-aviso[hidden] { display: none; }
.cab-velo { position: fixed; inset: 0; background: rgba(0,0,0,0.35); z-index: 3000; display: flex;
            align-items: center; justify-content: center; }
.cab-dialogo { background: #fff; color: #1A2E26; border-radius: 8px; padding: 1.1rem 1.3rem; min-width: 340px;
               max-width: 90vw; box-shadow: 0 10px 30px rgba(0,0,0,0.35); }
.cab-dialogo h3 { margin: 0 0 0.7rem; font-size: 1.05rem; }
.cab-dialogo label { display: flex; gap: 0.5rem; padding: 0.25rem 0; cursor: pointer; }
.cab-dialogo input[type=number] { width: 7rem; padding: 0.3rem; }
.cab-dialogo .botones { display: flex; justify-content: flex-end; gap: 0.5rem; margin-top: 1rem; }
.cab-dialogo button { padding: 0.4rem 1rem; border-radius: 5px; border: 1px solid #2f7d5b; cursor: pointer;
                      background: #fff; color: #1A2E26; }
.cab-dialogo button.primario { background: #2f7d5b; color: #fff; }
```

- [ ] **Step 3: Crear `app/static/cabecera.js`**

```javascript
// Cabecera compartida: menu de muestras (v2.2). Al cambiar de muestra se recarga la pagina (estado inicial).
let cabDatos = null;
let cabTemporizador = null;

function cabAvisar(mensaje, tipo) {
    const d = document.getElementById('cab-aviso');
    d.textContent = mensaje;
    d.classList.toggle('error', tipo === 'error');
    d.hidden = false;
    clearTimeout(cabTemporizador);
    cabTemporizador = setTimeout(() => { d.hidden = true; }, tipo === 'error' ? 9000 : 5000);
}

async function cabLlamar(url, cuerpo) {
    const r = await fetch(url, cuerpo === undefined ? {} : {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(cuerpo)
    });
    let json = null;
    try { json = await r.json(); } catch (e) { /* sin cuerpo */ }
    if (!r.ok) {
        const det = json && json.detail;
        const err = new Error(typeof det === 'string' ? det : (det && det.mensaje) || 'Error ' + r.status);
        err.detalle = det;
        err.status = r.status;
        throw err;
    }
    return json;
}

function cabNombre(m) { return m.anio ? String(m.anio) : m.carpeta; }

async function cabCargar() {
    try {
        cabDatos = await cabLlamar('/muestras');
        document.getElementById('cab-muestra').textContent = 'Muestra ' + cabDatos.anio;
        if (cabDatos.aviso) cabAvisar(cabDatos.aviso);
    } catch (e) {
        cabAvisar('No se pudo leer la lista de muestras: ' + e.message, 'error');
    }
}

function cabCerrarMenu() {
    const m = document.getElementById('cab-menu');
    m.hidden = true;
    document.getElementById('cab-boton-menu').setAttribute('aria-expanded', 'false');
}

function cabAlternarMenu(ev) {
    ev.stopPropagation();
    const m = document.getElementById('cab-menu');
    if (!m.hidden) { cabCerrarMenu(); return; }
    m.innerHTML = '';
    for (const x of (cabDatos ? cabDatos.muestras : [])) {
        const it = document.createElement('div');
        it.className = 'cab-item' + (x.activa ? ' activa' : '') + (x.disponible ? '' : ' no-disponible');
        it.innerHTML = '<span class="marca">' + (x.activa ? '&#10003;' : '') + '</span><span></span>' +
                       (x.disponible ? '' : '<small>carpeta no encontrada</small>');
        it.children[1].textContent = cabNombre(x);
        if (x.disponible && !x.activa) it.onclick = () => { cabCerrarMenu(); cabActivar(x.carpeta); };
        m.appendChild(it);
    }
    const acciones = [['Añadir muestra', cabAnadir], ['Editar muestra', cabEditar], ['Eliminar muestra', cabEliminar]];
    for (const [t, f] of acciones) m.appendChild(cabItemAccion(t, f));
    const sep = document.createElement('div'); sep.className = 'cab-sep'; m.appendChild(sep);
    m.appendChild(cabItemAccion('Descripción de variables', () => cabAbrirInfo('variables')));
    m.appendChild(cabItemAccion('Acerca de', () => cabAbrirInfo('acerca')));
    m.hidden = false;
    document.getElementById('cab-boton-menu').setAttribute('aria-expanded', 'true');
}

function cabItemAccion(texto, fn) {
    const it = document.createElement('div');
    it.className = 'cab-item';
    it.innerHTML = '<span class="marca"></span><span></span>';
    it.children[1].textContent = texto;
    it.onclick = () => { cabCerrarMenu(); fn(); };
    return it;
}

function cabAbrirInfo(vista) {
    if (typeof cambiarVista === 'function' && document.getElementById('contenido-' + vista)) {
        cambiarVista(vista);
        history.replaceState(null, '', '#' + vista);
    } else {
        location.href = '/colectivos#' + vista;
    }
}

function cabDialogo(titulo, cuerpo, botones) {
    // botones: [{texto, valor, primario}] -> Promise con el valor pulsado (o null al cerrar con Escape)
    return new Promise(resolve => {
        const velo = document.createElement('div');
        velo.className = 'cab-velo';
        const d = document.createElement('div');
        d.className = 'cab-dialogo';
        const h = document.createElement('h3'); h.textContent = titulo; d.appendChild(h);
        d.appendChild(cuerpo);
        const fila = document.createElement('div'); fila.className = 'botones';
        const cerrar = v => { document.removeEventListener('keydown', alTecla); velo.remove(); resolve(v); };
        const alTecla = ev => { if (ev.key === 'Escape') cerrar(null); };
        document.addEventListener('keydown', alTecla);
        for (const b of botones) {
            const el = document.createElement('button');
            el.textContent = b.texto;
            if (b.primario) el.className = 'primario';
            el.onclick = () => cerrar(b.valor);
            fila.appendChild(el);
        }
        d.appendChild(fila); velo.appendChild(d); document.body.appendChild(velo);
    });
}

async function cabElegirMuestra(titulo, textoBoton) {
    const cuerpo = document.createElement('div');
    (cabDatos.muestras).forEach((x, i) => {
        const l = document.createElement('label');
        const r = document.createElement('input');
        r.type = 'radio'; r.name = 'cab-muestra-elegida'; r.value = x.carpeta; r.checked = x.activa;
        const s = document.createElement('span');
        s.textContent = cabNombre(x) + '  (' + x.carpeta + ')';
        l.appendChild(r); l.appendChild(s); cuerpo.appendChild(l);
    });
    const v = await cabDialogo(titulo, cuerpo, [{ texto: 'Cancelar', valor: null }, { texto: textoBoton, valor: 'ok', primario: true }]);
    if (v !== 'ok') return null;
    const marcada = cuerpo.querySelector('input:checked');
    return marcada ? marcada.value : null;
}

async function cabPedirAnio(mensaje) {
    const cuerpo = document.createElement('div');
    const p = document.createElement('p'); p.textContent = mensaje; cuerpo.appendChild(p);
    const inp = document.createElement('input'); inp.type = 'number'; inp.min = 2004; inp.max = 2100; cuerpo.appendChild(inp);
    const v = await cabDialogo('Año de la muestra', cuerpo, [{ texto: 'Cancelar', valor: null }, { texto: 'Aceptar', valor: 'ok', primario: true }]);
    return v === 'ok' && inp.value ? parseInt(inp.value, 10) : null;
}

async function cabActivar(carpeta) {
    try {
        await cabLlamar('/muestras/activar', { carpeta });
        location.href = '/colectivos';  // estado inicial con la muestra nueva
    } catch (e) {
        cabAvisar(e.message, 'error');
    }
}

async function cabAnadirOEditar(url, base) {
    try {
        const r = await cabLlamar(url, base);
        if (r.cancelado) return;
        cabDatos = r;
        cabAvisar('Lista de muestras actualizada.');
        if (url.endsWith('editar')) location.href = '/colectivos';
    } catch (e) {
        if (e.status === 409 && e.detalle && e.detalle.falta_anio) {
            const anio = await cabPedirAnio(e.detalle.mensaje);
            if (anio) return cabAnadirOEditar(url, Object.assign({}, base, { carpeta: e.detalle.carpeta, anio }));
            return;
        }
        cabAvisar(e.message, 'error');
    }
}

function cabAnadir() { cabAnadirOEditar('/muestras/anadir', {}); }

async function cabEditar() {
    const carpeta = await cabElegirMuestra('Editar muestra: elige cuál', 'Elegir nueva carpeta');
    if (carpeta) cabAnadirOEditar('/muestras/editar', { actual: carpeta });
}

async function cabEliminar() {
    const carpeta = await cabElegirMuestra('Eliminar muestra: elige cuál', 'Continuar');
    if (!carpeta) return;
    const m = cabDatos.muestras.find(x => x.carpeta === carpeta);
    const cuerpo = document.createElement('p');
    cuerpo.textContent = 'Se quita la muestra ' + cabNombre(m) + ' de la lista. Los ficheros no se borran.';
    const v = await cabDialogo('Quitar muestra', cuerpo, [{ texto: 'Cancelar', valor: null }, { texto: 'Quitar de la lista', valor: 'ok', primario: true }]);
    if (v !== 'ok') return;
    try {
        const r = await cabLlamar('/muestras/eliminar', { carpeta });
        if (m.activa) { location.href = '/colectivos'; return; }
        cabDatos = r;
        cabAvisar('Muestra quitada de la lista.');
    } catch (e) {
        cabAvisar(e.message, 'error');
    }
}

document.addEventListener('click', ev => {
    const m = document.getElementById('cab-menu');
    if (m && !m.hidden && !m.contains(ev.target)) cabCerrarMenu();
});
document.addEventListener('keydown', ev => { if (ev.key === 'Escape') cabCerrarMenu(); });
document.addEventListener('DOMContentLoaded', cabCargar);
```

- [ ] **Step 4: Usar la cabecera en las dos plantillas**

En `app/templates/colectivos.html` sustituir todo el bloque `<header> ... </header>` (líneas ~498-507) por:

```html
    {% include "_cabecera.html" %}
```

En `app/templates/trayectoria.html` sustituir el bloque `<header> ... </header>` (líneas ~74-84) por:

```html
    {% set insignia_extra = "Trayectorias" %}
    {% include "_cabecera.html" %}
```

- [ ] **Step 5: Probar en el navegador (revisión del usuario)**

Run (primer plano, en otra ventana del usuario): `python run.py`
Comprobar, y **pedir al usuario que lo revise con la 2024 real**: título y subtítulo centrados; `Muestra 2024` junto a las tres rayas; el menú lista la muestra con su marca, las tres acciones, la raya y las dos opciones finales; se cierra con clic fuera y con Escape; la página `/trayectoria/<ipf>` muestra la misma cabecera con la insignia "Trayectorias". Probar Añadir (cancelar el selector no debe dar error) y Eliminar con una sola muestra (debe avisar que es la única).

- [ ] **Step 6: Commit**

```bash
git add app/templates app/static/cabecera.css app/static/cabecera.js
git commit -m "app: cabecera compartida con menu de muestras"
```

---

### Task 6: Vista "Descripción de variables"

**Files:**
- Create: `tools/generar_datos_ayuda.py`, `app/static/datos/variables.json`, `app/static/vistas_info.js`, `app/static/vistas_info.css`, `tests/test_variables.py`
- Modify: `app/routers/muestras.py` (endpoint `/variables`), `app/templates/colectivos.html` (contenedores y `cambiarVista`)

**Interfaces:**
- Produces:
  - `tools/generar_datos_ayuda.py` funciones `normalizar_nombre(col: str) -> str` (año → `AAAA`, `2024/5` → `AAAA/A`), `generar_variables(carpeta_parquet: Path) -> dict`; ejecución `python tools/generar_datos_ayuda.py variables <carpeta>`.
  - `GET /variables` → `{"anio": int, "personas": [{"nombre","tipo","origen","descripcion"}], "afiliacion": [...]}`
  - JS: `iniciarVistaVariables()` y `vistas_info.js` carga.

- [ ] **Step 1: Test de la normalización y del endpoint (falla)**

Crear `tests/test_variables.py`:

```python
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(RAIZ / "tools"))
from generar_datos_ayuda import normalizar_nombre  # noqa: E402


def test_normalizar_nombre():
    assert normalizar_nombre("ingresos_sueldo_2024") == "ingresos_sueldo_AAAA"
    assert normalizar_nombre("Cobra_pension_2024/5") == "Cobra_pension_AAAA/A"
    assert normalizar_nombre("01 IPF") == "01 IPF"
    assert normalizar_nombre("%T_total_EMPLEO") == "%T_total_EMPLEO"


def test_variables_json_cubre_las_71_y_41_columnas():
    datos = json.loads((RAIZ / "app" / "static" / "datos" / "variables.json").read_text(encoding="utf-8"))
    for tabla in ("personas", "afiliacion"):
        assert all(v["descripcion"] for v in datos[tabla].values()), tabla
    assert len(datos["personas"]) == 71 and len(datos["afiliacion"]) == 41
```

Run: `python -m pytest tests/test_variables.py -q`
Expected: FAIL (no existe el módulo).

- [ ] **Step 2: Crear `tools/generar_datos_ayuda.py`**

```python
"""Genera los JSON de ayuda de la app (app/static/datos/). Solo lo usa quien mantiene el proyecto.

  python tools/generar_datos_ayuda.py variables <carpeta_parquet>
  python tools/generar_datos_ayuda.py casos
"""
import json
import re
import sys
from pathlib import Path

import duckdb

RAIZ = Path(__file__).resolve().parent.parent
DATOS = RAIZ / "app" / "static" / "datos"

# Titulos de las 37 variables originales de Afiliacion (guia MCVL, 2.001 a 2.037), en el orden de las columnas 01-37.
AFILIACION_ORIGINALES = [
    "IDENTIFICADOR DE LA PERSONA FÍSICA (IPF)", "RÉGIMEN DE COTIZACIÓN", "GRUPO DE COTIZACIÓN",
    "TIPO DE CONTRATO DE TRABAJO", "COEFICIENTE DE TIEMPO PARCIAL", "FECHA REAL DEL ALTA EN AFILIACIÓN",
    "FECHA REAL DE LA BAJA EN AFILIACIÓN", "CAUSA DE BAJA EN AFILIACIÓN", "MINUSVALÍA SEGÚN ALTA EN AFILIACIÓN",
    "CÓDIGO DE CUENTA DE COTIZACIÓN (CCC)", "DOMICILIO DE ACTIVIDAD DE LA CUENTA DE COTIZACIÓN",
    "ACTIVIDAD ECONÓMICA DE LA CUENTA DE COTIZACIÓN (CNAE 2009)", "NÚMERO DE TRABAJADORES EN LA CUENTA DE COTIZACIÓN",
    "FECHA DE ALTA DEL PRIMER TRABAJADOR DE LA CUENTA DE COTIZACIÓN",
    "TIPO DE RELACIÓN LABORAL (TRL) DE LA CUENTA DE COTIZACIÓN", "COLECTIVO ESPECIAL DE LA CUENTA DE COTIZACIÓN",
    "EMPLEADOR (TIPO DE IDENTIFICADOR)", "EMPLEADOR (FORMA JURÍDICA) – LETRA NIF DE LA ENTIDAD PAGADORA",
    "IDENTIFICADOR (NIF/CIF) ANONIMADO DE LA ENTIDAD PAGADORA", "CÓDIGO DE CUENTA DE COTIZACIÓN PRINCIPAL (CCCP)",
    "PROVINCIA DEL DOMICILIO DE LA CUENTA DE COTIZACIÓN PRINCIPAL",
    "FECHA MODIFIC. DEL TIPO CONTRATO INICIAL O COEF. TIEMPO PARCIAL INICIAL", "TIPO DE CONTRATO INICIAL",
    "COEFICIENTE DE TIEMPO PARCIAL INICIAL", "FECHA MODIFIC. TIPO CONTRATO SEGUNDO O COEF. TIEMPO PARCIAL SEGUNDO",
    "TIPO DE CONTRATO SEGUNDO", "COEFICIENTE DE TIEMPO PARCIAL SEGUNDO",
    "FECHA DE MODIFICACIÓN DEL GRUPO DE COTIZACIÓN INICIAL", "GRUPO DE COTIZACIÓN INICIAL",
    "ACTIVIDAD ECONÓMICA DE LA CUENTA DE COTIZACIÓN (CNAE 93)", "SISTEMA ESPECIAL DE TRABAJADORES AGRARIOS (SETA)",
    "TIPO DE RELACIÓN CON OTRAS ENTIDADES O AUTÓNOMOS", "FECHA DE EFECTO DEL ALTA EN AFILIACIÓN",
    "FECHA DE EFECTO DE LA BAJA EN AFILIACIÓN", "CONVENIO COLECTIVO 1", "CONVENIO COLECTIVO 2",
    "NÚMERO DE TRABAJADORES EN LA CUENTA DE COTIZACIÓN PRINCIPAL",
]
AFILIACION_DERIVADAS = {
    "Baja_modificada": "Fecha de baja de afiliación con los episodios abiertos (año 2099) cortados en la fecha de corte.",
    "Empleo_publico": "SI si la letra del NIF del empleador es P (corporación local), Q (organismo público) o S (órgano de la "
                      "Administración); NO en otro caso.",
    "DURACION_EMPLEO": "Baja_modificada - fecha de alta + 1 día, en microsegundos. 0 en los episodios de prestación o subsidio "
                       "(TRL 700-799) y TRL 400; NULL si sale negativa.",
    "DURACION_DESEMPLEO": "Igual, pero solo con valor en los episodios de prestación o subsidio de desempleo (TRL 700-799); 0 en "
                          "el resto; NULL si sale negativa.",
}
PERSONAS_ORIGINALES = {"01 IPF", "02 FECHA_NACIMIENTO", "03 SEXO", "04 NACIONALIDAD", "05 PROV_NACIMIENTO",
                       "06 PROV_1a_AFILIACION", "07 DOMIC_RESID_HABITUAL", "08 FECHA_FALLECIMIENTO", "09 PAIS_NACIMIENTO"}
DESCRIPCIONES_EXTRA = {
    "Situacion_actual_afiliacion": "Situación a la fecha de corte según el último episodio: Alta_ocupado, Baja_de_actividad, "
                                   "Baja_para_pension, Baja_de_prestacion, Baja_de_subsidio, Asimilado_al_alta_prest_subs, "
                                   "Asimilado_al_alta_subs52, Asimilado_otros, Nunca_ha_cotizado.",
    "Situacion_Paro_Ocupacion": "Ocupado, Pensionista, Parado_LD (más de 365 días sin empleo), Parado_NoLD, "
                                "Ocupado_y_pensionista, Parado_LD_pension_no_jubilacion, Parado_NoLD_pension_no_jubilacion.",
}


def normalizar_nombre(col: str) -> str:
    """Sustituye el anio de las columnas fiscales/de pension por AAAA (y '2024/5' por 'AAAA/A')."""
    col = re.sub(r"(\d{4})/\d$", "AAAA/A", col)
    return re.sub(r"_(\d{4})$", "_AAAA", col)


def _descripciones_md() -> dict:
    d = {}
    for linea in (RAIZ / "docs" / "esquema_datos_mcvl.md").read_text(encoding="utf-8").splitlines():
        if linea.startswith("|"):
            celdas = [c.strip() for c in linea.strip().strip("|").split("|")]
            if len(celdas) >= 2:
                for n in re.findall(r"`([^`]+)`", celdas[0]):
                    d[normalizar_nombre(n)] = celdas[-1].replace("`", "")
    return d


def _esquema(carpeta: Path, fichero: str):
    con = duckdb.connect()
    return [(x[0], x[1]) for x in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{(carpeta / fichero).as_posix()}')").fetchall()]


def generar_variables(carpeta: Path) -> dict:
    md = _descripciones_md()
    md.update({normalizar_nombre(k): v for k, v in DESCRIPCIONES_EXTRA.items()})
    personas = {}
    for i, (n, t) in enumerate(_esquema(carpeta, "tabla_personas_extendida.parquet")):
        clave = normalizar_nombre(n)
        personas[clave] = {"orden": i + 1, "tipo": t, "origen": "original" if n in PERSONAS_ORIGINALES else "derivada",
                           "descripcion": md.get(clave, "")}
    afiliacion = {}
    for i, (n, t) in enumerate(_esquema(carpeta, "tabla_afiliacion_extendida.parquet")):
        if i < len(AFILIACION_ORIGINALES):
            origen, desc = "original", AFILIACION_ORIGINALES[i]
        else:
            origen, desc = "derivada", AFILIACION_DERIVADAS.get(n, "")
        afiliacion[n] = {"orden": i + 1, "tipo": t, "origen": origen, "descripcion": desc}
    return {"personas": personas, "afiliacion": afiliacion}


if __name__ == "__main__":
    modo = sys.argv[1]
    if modo == "variables":
        datos = generar_variables(Path(sys.argv[2]))
        vacias = [n for n, v in datos["personas"].items() if not v["descripcion"]]
        DATOS.mkdir(parents=True, exist_ok=True)
        (DATOS / "variables.json").write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
        print("personas:", len(datos["personas"]), "afiliacion:", len(datos["afiliacion"]), "sin descripcion:", vacias)
```


- [ ] **Step 3: Generar el JSON y comprobar los tests**

Run: `python tools/generar_datos_ayuda.py variables "parquet/MCVL 2024 parquet data"`
Expected: `personas: 71 afiliacion: 41 sin descripcion: []`. Si sale alguna sin descripción, añadirla a `DESCRIPCIONES_EXTRA` (texto tomado de `docs/esquema_datos_mcvl.md`) y regenerar.

Run: `python -m pytest tests/test_variables.py -q` → PASS.

- [ ] **Step 4: Endpoint `/variables`**

Añadir a `app/routers/muestras.py`:

```python
import json
import re
import sys
from pathlib import Path

import duckdb


def _ruta_recurso(relativa: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent))
    return base / relativa


def _normalizar(col: str) -> str:
    col = re.sub(r"(\d{4})/\d$", "AAAA/A", col)
    return re.sub(r"_(\d{4})$", "_AAAA", col)


@router.get("/variables")
def variables():
    """Descripcion de las variables de Personas y Afiliacion, cruzada con las columnas reales de la muestra activa."""
    catalogo = json.loads(_ruta_recurso("app/static/datos/variables.json").read_text(encoding="utf-8"))
    m = estado.muestra
    con = duckdb.connect()
    salida = {"anio": m.anio}
    for tabla, fichero in (("personas", "tabla_personas_extendida.parquet"), ("afiliacion", "tabla_afiliacion_extendida.parquet")):
        columnas = [(x[0], x[1]) for x in con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{(m.carpeta / fichero).as_posix()}')").fetchall()]
        filas = []
        for n, t in columnas:
            info = catalogo[tabla].get(_normalizar(n) if tabla == "personas" else n, {})
            filas.append({"nombre": n, "tipo": t, "origen": info.get("origen", ""),
                          "descripcion": info.get("descripcion") or "sin descripción"})
        salida[tabla] = filas
    return salida
```

Comprobación: `python -c "from fastapi.testclient import TestClient; from app.main import app; c=TestClient(app).__enter__(); r=c.get('/variables').json(); print(len(r['personas']), len(r['afiliacion']), [x['nombre'] for x in r['personas'] if x['descripcion']=='sin descripción'])"` → `71 41 []`.

- [ ] **Step 5: Vista en `colectivos.html`**

Después del contenedor `contenido-cruce` (cierre de su `</div>`, antes del cierre de `#contenido`) añadir:

```html
            <div id="contenido-variables" class="contenido-vista" style="display:none">
                <div class="panel panel-info">
                    <div class="info-barra">
                        <button type="button" id="var-boton-personas" class="info-pestana activa" onclick="varMostrar('personas')">Personas extendida</button>
                        <button type="button" id="var-boton-afiliacion" class="info-pestana" onclick="varMostrar('afiliacion')">Afiliación extendida</button>
                        <input type="search" id="var-buscar" placeholder="Buscar por nombre o descripción" oninput="varFiltrar()">
                        <span id="var-contador"></span>
                    </div>
                    <div class="info-tabla-envoltorio"><table id="var-tabla"><thead><tr>
                        <th>#</th><th>Variable</th><th>Tipo</th><th>Origen</th><th>Descripción</th></tr></thead><tbody></tbody></table></div>
                </div>
            </div>
            <div id="contenido-acerca" class="contenido-vista" style="display:none">
                <div class="panel panel-info" id="acerca-cuerpo"></div>
            </div>
```

En `<head>` de `colectivos.html` (junto a los otros `<link>`/`<style>`) añadir `<link rel="stylesheet" href="/static/vistas_info.css">` y, antes del `<script>` principal, `<script src="/static/marked.min.js"></script><script src="/static/vistas_info.js"></script>`.

En `cambiarVista` añadir tras la línea de `contenido-cruce`:

```javascript
    document.getElementById('contenido-variables').style.display = vista === 'variables' ? 'flex' : 'none';
    document.getElementById('contenido-acerca').style.display = vista === 'acerca' ? 'flex' : 'none';
```

Cambiar las dos condiciones de `bloque-acciones-sidebar` y `boton-exportar` de `(vista === 'individuales' || vista === 'seleccion')` a `(vista === 'individuales' || vista === 'seleccion' || vista === 'variables' || vista === 'acerca')`. Al final de `cambiarVista`, antes de su llave de cierre, añadir:

```javascript
    if (vista === 'variables') iniciarVistaVariables();
    if (vista === 'acerca') iniciarVistaAcerca();
```

`actualizarBotonExportar()` ya cae en su `else` para vistas desconocidas y el botón queda oculto: no requiere cambios.

- [ ] **Step 6: Crear `app/static/vistas_info.css` y `app/static/vistas_info.js`**

`vistas_info.css`:

```css
.panel-info { flex: 1; min-height: 0; display: flex; flex-direction: column; overflow: hidden; }
.info-barra { display: flex; align-items: center; gap: 0.6rem; padding: 0.4rem 0 0.7rem; flex-wrap: wrap; }
.info-pestana { padding: 0.4rem 0.9rem; border: 1px solid #2f7d5b; border-radius: 5px; background: #fff; cursor: pointer; }
.info-pestana.activa { background: #2f7d5b; color: #fff; }
#var-buscar { flex: 1; min-width: 220px; padding: 0.4rem 0.6rem; }
.info-tabla-envoltorio { flex: 1; min-height: 0; overflow: auto; }
#var-tabla { border-collapse: collapse; width: 100%; font-size: 0.86rem; }
#var-tabla th { position: sticky; top: 0; background: #e8f1ed; text-align: left; padding: 0.4rem 0.6rem; }
#var-tabla td { padding: 0.35rem 0.6rem; border-top: 1px solid #dfe9e4; vertical-align: top; }
#var-tabla td:nth-child(2) { font-family: var(--mono, monospace); white-space: nowrap; }
#acerca-cuerpo { overflow: auto; padding: 1rem 1.4rem; }
#acerca-cuerpo h1, #acerca-cuerpo h2 { margin: 1rem 0 0.5rem; }
#acerca-cuerpo table { border-collapse: collapse; margin: 0.6rem 0; }
#acerca-cuerpo th, #acerca-cuerpo td { border: 1px solid #b9cfc6; padding: 0.3rem 0.6rem; font-size: 0.86rem; }
#acerca-cuerpo pre { background: #f2f6f4; padding: 0.6rem; overflow: auto; }
#acerca-cuerpo details { border: 1px solid #b9cfc6; border-radius: 6px; margin: 0.8rem 0; padding: 0.4rem 0.9rem; }
#acerca-cuerpo summary { cursor: pointer; font-weight: 800; padding: 0.3rem 0; }
```

`vistas_info.js` (la parte de variables; la de "Acerca de" se añade en Task 7):

```javascript
// Vistas informativas del shell (v2.2): Descripcion de variables y Acerca de.
let varDatos = null;
let varTabla = 'personas';

async function iniciarVistaVariables() {
    if (varDatos) { varPintar(); return; }
    try {
        const r = await fetch('/variables');
        if (!r.ok) throw new Error('Error ' + r.status);
        varDatos = await r.json();
        varPintar();
    } catch (e) {
        cabAvisar('No se pudo cargar la descripción de variables: ' + e.message, 'error');
    }
}

function varMostrar(tabla) {
    varTabla = tabla;
    document.getElementById('var-boton-personas').classList.toggle('activa', tabla === 'personas');
    document.getElementById('var-boton-afiliacion').classList.toggle('activa', tabla === 'afiliacion');
    varPintar();
}

function varFiltrar() { varPintar(); }

function varPintar() {
    if (!varDatos) return;
    const q = document.getElementById('var-buscar').value.trim().toLowerCase();
    const cuerpo = document.querySelector('#var-tabla tbody');
    cuerpo.innerHTML = '';
    let n = 0;
    const filas = varDatos[varTabla];
    filas.forEach((f, i) => {
        const nombre = f.nombre.replace('AAAA', String(varDatos.anio));
        if (q && !(nombre.toLowerCase().includes(q) || f.descripcion.toLowerCase().includes(q))) return;
        const tr = document.createElement('tr');
        [String(i + 1), nombre, f.tipo, f.origen, f.descripcion].forEach(t => {
            const td = document.createElement('td'); td.textContent = t; tr.appendChild(td);
        });
        cuerpo.appendChild(tr);
        n++;
    });
    document.getElementById('var-contador').textContent = n + ' de ' + filas.length;
}

document.addEventListener('DOMContentLoaded', () => {
    if (['#variables', '#acerca'].includes(location.hash) && typeof cambiarVista === 'function') {
        cambiarVista(location.hash.slice(1));
    }
});
```

- [ ] **Step 7: Probar en el navegador (revisión del usuario) y comitear**

Run: `python run.py`; menú → "Descripción de variables". Comprobar que salen 71 filas en Personas y 41 en Afiliación, que la búsqueda filtra por nombre y por descripción, que las columnas con año muestran `2024`, y que desde `/trayectoria/<ipf>` el mismo menú abre `/colectivos#variables`. **Pedir al usuario que lo revise.**

```bash
git add tools app/static app/templates app/routers/muestras.py tests/test_variables.py
git commit -m "app: vista Descripcion de variables"
```

---

### Task 7: Vista "Acerca de" con README y casos raros

**Files:**
- Modify: `tools/generar_datos_ayuda.py` (modo `casos`), `app/routers/muestras.py` (`/acerca-de`), `app/static/vistas_info.js`
- Create: `app/static/marked.min.js`, `app/static/datos/casos_raros.json`, `tests/test_acerca.py`

**Interfaces:**
- Produces: `partir_readme(texto: str) -> dict` en `app/routers/muestras.py` (claves `principal`, `construccion`, `tests`); `GET /acerca-de` → `{"principal": md, "construccion": md, "tests": md, "casos": [{id, regla, estado, n, pct, explicacion}]}` (casos raros primero); `iniciarVistaAcerca()`.

- [ ] **Step 1: Test de `partir_readme` (falla)**

Crear `tests/test_acerca.py`:

```python
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.routers.muestras import partir_readme  # noqa: E402

TEXTO = """# Titulo
intro

## Dos módulos separados
a

## Cómo construir los datos (solo equipo ETL)
b

## Estructura
c

## Reglas de consistencia
d

## Documentación
e
"""


def test_partir_readme_agrupa_secciones():
    p = partir_readme(TEXTO)
    assert "Dos módulos" in p["principal"] and "Documentación" in p["principal"]
    assert "Cómo construir los datos" in p["construccion"] and "## Estructura" in p["construccion"]
    assert "Reglas de consistencia" in p["tests"]
    assert "Estructura" not in p["principal"] and "Reglas de consistencia" not in p["principal"]


def test_readme_real_se_parte_en_tres():
    texto = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    p = partir_readme(texto)
    assert p["construccion"] and p["tests"] and p["principal"]
```

Run: `python -m pytest tests/test_acerca.py -q` → FAIL (falta `partir_readme`).

- [ ] **Step 2: Implementar `partir_readme` y `/acerca-de`**

Añadir a `app/routers/muestras.py`:

```python
_SECCIONES_CONSTRUCCION = ("## Cómo construir los datos", "## Estructura")
_SECCIONES_TESTS = ("## Reglas de consistencia",)


def partir_readme(texto: str) -> dict:
    """Separa el README en el texto principal y los dos desplegables finales (construccion de variables y tests)."""
    trozos, actual = [], []
    for linea in texto.splitlines():
        if linea.startswith("## ") and actual:
            trozos.append("\n".join(actual))
            actual = []
        actual.append(linea)
    trozos.append("\n".join(actual))
    salida = {"principal": [], "construccion": [], "tests": []}
    for t in trozos:
        if t.startswith(_SECCIONES_CONSTRUCCION):
            salida["construccion"].append(t)
        elif t.startswith(_SECCIONES_TESTS):
            salida["tests"].append(t)
        else:
            salida["principal"].append(t)
    return {k: "\n\n".join(v) for k, v in salida.items()}


@router.get("/acerca-de")
def acerca_de():
    texto = _ruta_recurso("README.md").read_text(encoding="utf-8")
    casos = json.loads(_ruta_recurso("app/static/datos/casos_raros.json").read_text(encoding="utf-8"))
    return {**partir_readme(texto), "casos": casos}
```

Run: `python -m pytest tests/test_acerca.py -q` → PASS.

- [ ] **Step 3: Generar `casos_raros.json`**

Añadir a `tools/generar_datos_ayuda.py` (antes del `if __name__`) y al bloque principal:

```python
def generar_casos() -> list:
    import openpyxl

    wb = openpyxl.load_workbook(RAIZ / "tests" / "reglas_consistencia_resultados_sin_ipf.xlsx", read_only=True)
    filas = list(wb["Resultados"].iter_rows(values_only=True))
    cab = list(filas[0])
    ix = {n: cab.index(n) for n in ("ID", "Regla (debe cumplirse)", "Estado (seguimiento)", "Filas que la incumplen",
                                    "% de personas")}
    ix_expl = next(i for i, n in enumerate(cab) if str(n).startswith("Explicaci"))
    casos = []
    for f in filas[1:]:
        estado = f[ix["Estado (seguimiento)"]]
        if estado in ("Casos raros (reales)", "Baja calidad de los datos"):
            casos.append({"id": f[ix["ID"]], "regla": f[ix["Regla (debe cumplirse)"]], "estado": estado,
                          "n": f[ix["Filas que la incumplen"]] or 0, "pct": f[ix["% de personas"]] or 0,
                          "explicacion": f[ix_expl] or ""})
    casos.sort(key=lambda c: (c["estado"] != "Casos raros (reales)", c["id"]))
    return casos
```

En el bloque `if __name__ == "__main__":` añadir:

```python
    elif modo == "casos":
        casos = generar_casos()
        DATOS.mkdir(parents=True, exist_ok=True)
        (DATOS / "casos_raros.json").write_text(json.dumps(casos, ensure_ascii=False, indent=1), encoding="utf-8")
        print("casos raros:", sum(c["estado"].startswith("Casos") for c in casos),
              "baja calidad:", sum(c["estado"].startswith("Baja") for c in casos))
```

Run: `python tools/generar_datos_ayuda.py casos`
Expected: `casos raros: 31 baja calidad: 71`.

- [ ] **Step 4: Librería Markdown sin CDN**

Run: `curl -L -o app/static/marked.min.js https://cdn.jsdelivr.net/npm/marked@12.0.2/marked.min.js`
Expected: fichero de ~40 KB que empieza por un comentario `marked v12`. Si la red corporativa bloquea la descarga, pedirle al usuario que guarde ese fichero en `app/static/marked.min.js`.

- [ ] **Step 5: Vista en `vistas_info.js`**

Añadir a `app/static/vistas_info.js`:

```javascript
let acercaCargada = false;

async function iniciarVistaAcerca() {
    if (acercaCargada) return;
    const cont = document.getElementById('acerca-cuerpo');
    try {
        const r = await fetch('/acerca-de');
        if (!r.ok) throw new Error('Error ' + r.status);
        const d = await r.json();
        cont.innerHTML = marked.parse(d.principal);
        cont.appendChild(acercaDesplegable('Tests de consistencia', marked.parse(d.tests) + acercaCasos(d.casos)));
        cont.appendChild(acercaDesplegable('Construcción de variables', marked.parse(d.construccion)));
        acercaCargada = true;
    } catch (e) {
        cabAvisar('No se pudo cargar "Acerca de": ' + e.message, 'error');
    }
}

function acercaDesplegable(titulo, html) {
    const det = document.createElement('details');
    const sum = document.createElement('summary');
    sum.textContent = titulo;
    const cuerpo = document.createElement('div');
    cuerpo.innerHTML = html;
    det.appendChild(sum);
    det.appendChild(cuerpo);
    return det;
}

function acercaEscapar(t) {
    return String(t).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

function acercaCasos(casos) {
    let html = '';
    for (const [estado, titulo] of [['Casos raros (reales)', 'Casos raros (personas reales con situaciones poco habituales)'],
                                    ['Baja calidad de los datos', 'Baja calidad de los datos (dato de origen incompleto o incoherente)']]) {
        const lista = casos.filter(c => c.estado === estado);
        html += '<h3>' + acercaEscapar(titulo) + ' — ' + lista.length + ' reglas</h3>' +
                '<table><thead><tr><th>Regla</th><th>Enunciado</th><th>Personas</th><th>Explicación</th></tr></thead><tbody>' +
                lista.map(c => '<tr><td>' + acercaEscapar(c.id) + '</td><td>' + acercaEscapar(c.regla) + '</td><td>' +
                              Number(c.n).toLocaleString('es-ES') + ' (' + Number(c.pct).toFixed(3) + ' %)</td><td>' +
                              acercaEscapar(c.explicacion) + '</td></tr>').join('') +
                '</tbody></table>';
    }
    return html;
}
```

- [ ] **Step 6: Probar en el navegador (revisión del usuario) y comitear**

Run: `python run.py`; menú → "Acerca de". Comprobar: el README se ve con sus tablas y bloques de código, al final hay dos desplegables (primero "Tests de consistencia", con 31 casos raros y después 71 de baja calidad, cada uno con su explicación; luego "Construcción de variables"), y las secciones de ETL y reglas ya no aparecen en el texto principal. **Pedir al usuario que lo revise.**

```bash
git add tools app/static app/routers/muestras.py tests/test_acerca.py
git commit -m "app: vista Acerca de con README, tests de consistencia y casos raros"
```

---

### Task 8: Empaquetado, prueba de humo, documentación y cierre

**Files:**
- Modify: `build/mcvlanalyser.spec`, `tests/smoke.py`, `README.md`, `CHANGELOG.md`, `CLAUDE.md`, `docs/superpowers/specs/2026-09-25-multiples-muestras-design.md`

- [ ] **Step 1: Empaquetar el README**

En `build/mcvlanalyser.spec`, dentro de `datas=[...]` de `a_main`, añadir `('..\\README.md', '.'),`. (`app/static`, con `datos/`, `marked.min.js`, `cabecera.*` y `vistas_info.*`, ya se copia entera.) No reconstruir el `.exe` aquí: lo hará el usuario al final con `build\construir.bat`.

- [ ] **Step 2: Bloque `muestras` en `tests/smoke.py`**

Añadir a la docstring de `tests/smoke.py` el bloque `muestras` y, junto a los demás bloques (mismo estilo que los existentes, con `call(...)`), uno que use dos carpetas dadas por variables de entorno `MCVL_MUESTRA_A` y `MCVL_MUESTRA_B`:

```python
    if bloque == "muestras":
        import os
        a, b = os.environ.get("MCVL_MUESTRA_A"), os.environ.get("MCVL_MUESTRA_B")
        lista = call("muestras", "GET", "/muestras").json()
        original = lista["activa"]
        if a and b:
            for destino in (b, a, b, original):
                r = call(f"activar {destino[-20:]}", "POST", "/muestras/activar", json={"carpeta": destino})
                cols = call("columnas tras cambio", "GET", "/colectivos/columnas")
        call("variables", "GET", "/variables")
        call("acerca-de", "GET", "/acerca-de")
```

Run (con el servidor arrancado y las dos sintéticas): `set MCVL_MUESTRA_A=parquet\_sinteticas\s2024` y `set MCVL_MUESTRA_B=parquet\_sinteticas\s2023` y `python tests/smoke.py muestras`
Expected: `0 fallos`. Después, con la muestra real de nuevo activa, `python tests/smoke.py colectivos` → `0 fallos`.

- [ ] **Step 3: Toda la batería**

Run: `python -m pytest tests -q`
Expected: todos PASS (los de `test_cambio_muestra.py` se saltan si no hay muestra real).

- [ ] **Step 4: Documentación**

- `README.md`: en "Qué hay en la app" añadir la cabecera y el menú de muestras (cambiar, añadir, editar, eliminar) y las vistas Descripción de variables y Acerca de; en "Cómo usarla", explicar que el primer arranque pide la carpeta y que `config.json` guarda la lista de muestras.
- `CHANGELOG.md`: nueva entrada "v2.2: varias muestras" con el formato nuevo de `config.json` (migración automática), el intercambio atómico de muestra y que Eliminar solo quita de la lista y no permite quitar la única.
- `CLAUDE.md`: sección "Varias muestras (v2.2)" con: formato de `config.json`; `app.db.estado` y `ConexionActiva`; el selector por subproceso (`--elegir-carpeta`); que `tools/generar_datos_ayuda.py casos` hay que volver a ejecutarlo tras repasar las reglas y `variables <carpeta>` si cambian las columnas; y actualizar en "Estructura de carpetas" `tools/`, `app/routers/muestras.py`, `tests/muestra_sintetica.py`.
- Spec: sustituir en `docs/superpowers/specs/2026-09-25-multiples-muestras-design.md` la frase de "Eliminar la activa" que dice "si no queda ninguna, vuelve al primer arranque" por "la única muestra que queda no se puede eliminar".

- [ ] **Step 5: Commit**

```bash
git add build/mcvlanalyser.spec tests/smoke.py README.md CHANGELOG.md CLAUDE.md docs
git commit -m "v2.2: empaquetado, prueba de humo y documentacion de varias muestras"
```

- [ ] **Step 6: Revisión final con el usuario**

Recorrer con el usuario: arranque con la muestra real; añadir una sintética desde el menú (selector nativo) y cambiar a ella (la página se recarga en estado inicial con `Muestra 2023`); volver a la real; editar (cambiar carpeta) y eliminar la sintética; Descripción de variables; Acerca de. La reconstrucción del `.exe` (`build\construir.bat`) queda pendiente para cuando el usuario lo pida.

---

## Self-Review

**Cobertura del spec.** Configuración con migración, año, validación, eliminar la activa, carpeta no disponible y primer arranque → Tasks 1-2. Acceso a datos y trayectorias con fecha de corte dinámica → Task 3. Endpoints → Task 4. Cabecera, menú, diálogos y avisos → Task 5. Descripción de variables → Task 6. Acerca de con README, desplegables y casos raros → Task 7. Empaquetado, pruebas y documentación → Task 8. La muestra sintética y el bloque `muestras` de `smoke.py` están en Tasks 4 y 8.

**Placeholders.** Ninguno; cada paso lleva el código o el comando exacto. Las "revisiones del usuario" son pasos explícitos, no huecos.

**Consistencia de tipos.** `anio_de`, `muestra_inicial`, `anadir_muestra`, `editar_muestra`, `eliminar_muestra`, `activar_muestra`, `muestras_con_estado`, `abrir_muestra`, `estado.muestra` y `_ruta_recurso` mantienen el mismo nombre y firma en todas las tareas. `_clave` (de `config`) se usa desde el router como `config._clave`; se deja con guion bajo por ser interna, aceptado dentro del proyecto.

**Review Focus.** Los cinco casos tienen prueba: config corrupto y rutas con acentos/duplicados (Task 1), Parquet dañado, cursor en curso y eliminar la activa/única (Task 4).
