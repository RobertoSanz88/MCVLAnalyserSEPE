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
