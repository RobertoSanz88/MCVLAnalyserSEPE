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


def test_partir_readme_quita_las_secciones_del_etl_y_no_las_devuelve():
    p = partir_readme(TEXTO)
    assert set(p) == {"principal", "tests"}
    assert "Dos módulos" in p["principal"] and "Documentación" in p["principal"]
    assert "Reglas de consistencia" in p["tests"]
    # Las secciones de construccion no aparecen en ningun sitio del resultado (se quitan enteras).
    assert "Cómo construir los datos" not in p["principal"] and "Cómo construir los datos" not in p["tests"]
    assert "Estructura" not in p["principal"] and "Estructura" not in p["tests"]


def test_readme_real_no_lleva_las_secciones_del_etl():
    texto = (Path(__file__).resolve().parent.parent / "README.md").read_text(encoding="utf-8")
    p = partir_readme(texto)
    assert p["principal"] and p["tests"]
    junto = p["principal"] + p["tests"]
    assert "## Cómo construir los datos" not in junto and "## Estructura" not in junto
