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


def test_arranque_salta_a_la_siguiente_si_la_activa_esta_danada(tmp_path, monkeypatch, muestras_sinteticas):
    a, b = muestras_sinteticas
    mala = tmp_path / "mala"
    shutil.copytree(b, mala)
    (mala / "tabla_afiliacion_extendida.parquet").write_bytes(b"esto no es parquet")
    f = tmp_path / "config.json"
    monkeypatch.setattr(config, "CONFIG_FILE", f)
    f.write_text(json.dumps({"muestras": [{"carpeta": str(mala), "anio": 2030}, {"carpeta": a, "anio": 2024}],
                             "activa": str(mala)}), encoding="utf-8")
    from app.main import app
    with TestClient(app) as c:
        r = c.get("/muestras").json()
        assert r["anio"] == 2024 and _n(c) == 300
        assert "2023" in r["aviso"] and "Se abre la 2024" in r["aviso"]


def test_carpeta_con_apostrofo_y_espacios(cliente, tmp_path):
    c, a, b = cliente
    ap = tmp_path / "l'año 2023 datos"
    shutil.copytree(b, ap)
    assert c.post("/muestras/anadir", json={"carpeta": str(ap)}).status_code == 200
    r = c.post("/muestras/activar", json={"carpeta": str(ap)})
    assert r.status_code == 200 and _n(c) == 200
    assert c.get("/variables").status_code == 200
