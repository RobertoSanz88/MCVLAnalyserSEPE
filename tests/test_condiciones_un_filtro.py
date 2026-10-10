"""Modo "1 filtro" de Seleccion de colectivo (v2.3): condiciones_un_filtro da 2
subgrupos en vez de los 4 de calcular_condiciones_subgrupos, reutilizando _eje()
tal cual - mismo shape {where, params, etiqueta} que ya consumen tablas,
graficos, trayectorias, cruce y Excel."""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
import duckdb  # noqa: E402
import pandas as pd  # noqa: E402
from app.routers.colectivos import (  # noqa: E402
    condiciones_un_filtro,
    condiciones_de_subgrupos,
    FiltroNumerico,
    FiltroCategorico,
    SubgruposRequest,
    ORDEN_SUBGRUPOS_1F,
    ORDEN_SUBGRUPOS,
)


def _con_personas():
    con = duckdb.connect()
    df = pd.DataFrame({
        "edad_actual_fecha_corte": [20, 30, 50, 60, 70],
        "sexo": ["Hombre", "Mujer", "Hombre", "Mujer", "Hombre"],
    })
    con.register("personas", df)
    return con


def test_condiciones_un_filtro_da_2_subgrupos_con_el_shape_habitual():
    con = _con_personas()
    filtro1 = FiltroNumerico(columna="edad_actual_fecha_corte", operador=">=", valor=45)

    condiciones = condiciones_un_filtro(con, filtro1)

    assert set(condiciones.keys()) == set(ORDEN_SUBGRUPOS_1F)
    for grupo in condiciones.values():
        assert set(grupo.keys()) == {"where", "params", "etiqueta"}


def test_condiciones_un_filtro_reparte_las_personas_correctamente():
    con = _con_personas()
    filtro1 = FiltroNumerico(columna="edad_actual_fecha_corte", operador=">=", valor=45)

    condiciones = condiciones_un_filtro(con, filtro1)

    recuentos = {}
    for nombre, c in condiciones.items():
        recuentos[nombre] = con.execute(f"SELECT COUNT(*) FROM personas WHERE {c['where']}", c["params"]).fetchone()[0]

    assert recuentos["filtro1_si"] == 3  # 50, 60, 70
    assert recuentos["filtro1_no"] == 2  # 20, 30


def test_condiciones_un_filtro_con_filtro3_fija_filtro1_y_usa_el_secundario():
    con = _con_personas()
    filtro1 = FiltroNumerico(columna="edad_actual_fecha_corte", operador=">=", valor=45)
    filtro3 = FiltroCategorico(columna="sexo", categorias=["Hombre"])

    condiciones = condiciones_un_filtro(con, filtro1, filtro3)

    recuentos = {}
    for nombre, c in condiciones.items():
        recuentos[nombre] = con.execute(f"SELECT COUNT(*) FROM personas WHERE {c['where']}", c["params"]).fetchone()[0]

    # Filtro1 (edad>=45) queda fijo: solo 50/60/70 (3 personas) pasan ese filtro de base.
    # Dentro de esas, filtro3 (sexo=Hombre) reparte 2 "si" (50,70) y 1 "no" (60).
    assert recuentos["filtro1_si"] == 2
    assert recuentos["filtro1_no"] == 1


def test_condiciones_de_subgrupos_usa_un_filtro_si_falta_filtro2():
    con = _con_personas()
    body = SubgruposRequest(filtro1={"columna": "edad_actual_fecha_corte", "tipo": "numerica", "operador": ">=", "valor": 45})

    condiciones = condiciones_de_subgrupos(con, body)

    assert set(condiciones.keys()) == set(ORDEN_SUBGRUPOS_1F)


def test_condiciones_de_subgrupos_usa_dos_filtros_si_filtro2_viene_informado():
    con = _con_personas()
    body = SubgruposRequest(
        filtro1={"columna": "edad_actual_fecha_corte", "tipo": "numerica", "operador": ">=", "valor": 45},
        filtro2={"columna": "sexo", "tipo": "categorica", "categorias": ["Hombre"]},
    )

    condiciones = condiciones_de_subgrupos(con, body)

    assert set(condiciones.keys()) == set(ORDEN_SUBGRUPOS)
