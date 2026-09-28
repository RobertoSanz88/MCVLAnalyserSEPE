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


def test_variables_json_tiene_como_se_construye_en_las_71_y_41():
    datos = json.loads((RAIZ / "app" / "static" / "datos" / "variables.json").read_text(encoding="utf-8"))
    for tabla in ("personas", "afiliacion"):
        assert all(v["construccion"] for v in datos[tabla].values()), tabla
    # Las originales llevan la nota fija; las derivadas, una explicacion propia (no la nota fija).
    ORIGINALES_PERSONAS = 9
    ORIGINALES_AFILIACION = 37
    NOTA = "Campo original de la MCVL, tal como viene en el fichero."
    personas = list(datos["personas"].values())
    afiliacion = list(datos["afiliacion"].values())
    assert sum(v["construccion"] == NOTA for v in personas) == ORIGINALES_PERSONAS
    assert sum(v["construccion"] == NOTA for v in afiliacion) == ORIGINALES_AFILIACION
    assert all(v["construccion"] != NOTA for v in personas if v["origen"] == "derivada")
    assert all(v["construccion"] != NOTA for v in afiliacion if v["origen"] == "derivada")


def test_endpoint_variables_incluye_construccion():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        r = c.get("/variables").json()
        assert all(f["construccion"] for f in r["personas"])
        assert all(f["construccion"] for f in r["afiliacion"])
