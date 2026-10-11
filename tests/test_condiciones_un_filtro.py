"""Modo "1 filtro" de Seleccion de colectivo (v2.3): condiciones_un_filtro da
siempre 2 subgrupos, pero a partir de una CADENA de 1 a 4 filtros (en vez de
un cruce de ejes) - decision explicita del usuario, 2026-10-11, tras ver que
el diseño original (filtro1 + filtro3 como unico secundario) no encaja con lo
que queria. Cada filtro de la cadena, salvo el ultimo, queda fijo ("cumplido"
para todo el colectivo); el ultimo es el que varia en si/no. Mismo shape
{where, params, etiqueta} que el resto de modos, para que tablas, graficos,
trayectorias, cruce y Excel no necesiten saber cuantos filtros hay en la
cadena."""
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


def _con_simple():
    con = duckdb.connect()
    df = pd.DataFrame({
        "edad_actual_fecha_corte": [20, 30, 50, 60, 70],
        "sexo": ["Hombre", "Mujer", "Hombre", "Mujer", "Hombre"],
    })
    con.register("personas", df)
    return con


# Dataset para cadenas de 3 y 4 eslabones - mismo ejemplo que puso el usuario
# al pedir el cambio: mayores de 45 -> hombres -> latinos -> autonomos.
def _con_cadena_larga():
    con = duckdb.connect()
    df = pd.DataFrame({
        "edad_actual_fecha_corte": [50, 50, 50, 50, 50, 30],
        "sexo":                    ["Hombre", "Hombre", "Hombre", "Hombre", "Mujer", "Hombre"],
        "nacionalidad":            ["Latino", "Latino", "Latino", "Otro", "Latino", "Latino"],
        "autonomo":                ["SI", "SI", "NO", "SI", "SI", "SI"],
    })
    con.register("personas", df)
    return con


def _recuentos(con, condiciones):
    return {
        nombre: con.execute(f"SELECT COUNT(*) FROM personas WHERE {c['where']}", c["params"]).fetchone()[0]
        for nombre, c in condiciones.items()
    }


def test_cadena_de_1_dial_el_shape_habitual():
    con = _con_simple()
    filtro1 = FiltroNumerico(columna="edad_actual_fecha_corte", operador=">=", valor=45)

    condiciones = condiciones_un_filtro(con, [filtro1])

    assert set(condiciones.keys()) == set(ORDEN_SUBGRUPOS_1F)
    for grupo in condiciones.values():
        assert set(grupo.keys()) == {"where", "params", "etiqueta"}


def test_cadena_de_1_reparte_las_personas_por_filtro1_y_su_complementario():
    con = _con_simple()
    filtro1 = FiltroNumerico(columna="edad_actual_fecha_corte", operador=">=", valor=45)

    recuentos = _recuentos(con, condiciones_un_filtro(con, [filtro1]))

    assert recuentos["filtro1_si"] == 3  # 50, 60, 70
    assert recuentos["filtro1_no"] == 2  # 20, 30


def test_cadena_de_2_fija_filtro1_y_reparte_por_filtro2():
    con = _con_simple()
    filtro1 = FiltroNumerico(columna="edad_actual_fecha_corte", operador=">=", valor=45)
    filtro2 = FiltroCategorico(columna="sexo", categorias=["Hombre"])

    recuentos = _recuentos(con, condiciones_un_filtro(con, [filtro1, filtro2]))

    # Filtro1 (edad>=45) fijo: 50/60/70 (3 personas). Dentro de esas, filtro2
    # (sexo=Hombre) reparte 2 "si" (50,70) y 1 "no" (60).
    assert recuentos["filtro1_si"] == 2
    assert recuentos["filtro1_no"] == 1


def test_cadena_de_3_fija_filtro1_y_filtro2_y_reparte_por_filtro3():
    con = _con_cadena_larga()
    filtro1 = FiltroNumerico(columna="edad_actual_fecha_corte", operador=">=", valor=45)
    filtro2 = FiltroCategorico(columna="sexo", categorias=["Hombre"])
    filtro3 = FiltroCategorico(columna="nacionalidad", categorias=["Latino"])

    recuentos = _recuentos(con, condiciones_un_filtro(con, [filtro1, filtro2, filtro3]))

    # Mayores de 45 Y Hombre: 4 personas. De esas, Latino: 3 "si", 1 "no".
    assert recuentos["filtro1_si"] == 3
    assert recuentos["filtro1_no"] == 1


def test_cadena_de_4_fija_1_2_3_y_reparte_por_filtro4():
    con = _con_cadena_larga()
    filtro1 = FiltroNumerico(columna="edad_actual_fecha_corte", operador=">=", valor=45)
    filtro2 = FiltroCategorico(columna="sexo", categorias=["Hombre"])
    filtro3 = FiltroCategorico(columna="nacionalidad", categorias=["Latino"])
    filtro4 = FiltroCategorico(columna="autonomo", categorias=["SI"])

    recuentos = _recuentos(con, condiciones_un_filtro(con, [filtro1, filtro2, filtro3, filtro4]))

    # Mayores de 45, Hombre, Latino: 3 personas. De esas, autonomo=SI: 2 "si", 1 "no".
    assert recuentos["filtro1_si"] == 2
    assert recuentos["filtro1_no"] == 1


def test_condiciones_de_subgrupos_usa_cadena_de_1_si_falta_filtro2():
    con = _con_simple()
    body = SubgruposRequest(filtro1={"columna": "edad_actual_fecha_corte", "tipo": "numerica", "operador": ">=", "valor": 45})

    condiciones = condiciones_de_subgrupos(con, body)

    assert set(condiciones.keys()) == set(ORDEN_SUBGRUPOS_1F)


def test_condiciones_de_subgrupos_usa_dos_filtros_cruzados_si_filtro2_viene_informado_sin_un_filtro():
    con = _con_simple()
    body = SubgruposRequest(
        filtro1={"columna": "edad_actual_fecha_corte", "tipo": "numerica", "operador": ">=", "valor": 45},
        filtro2={"columna": "sexo", "tipo": "categorica", "categorias": ["Hombre"]},
    )

    condiciones = condiciones_de_subgrupos(con, body)

    assert set(condiciones.keys()) == set(ORDEN_SUBGRUPOS)


def test_condiciones_de_subgrupos_usa_cadena_si_un_filtro_es_true_aunque_filtro2_venga_informado():
    con = _con_simple()
    body = SubgruposRequest(
        un_filtro=True,
        filtro1={"columna": "edad_actual_fecha_corte", "tipo": "numerica", "operador": ">=", "valor": 45},
        filtro2={"columna": "sexo", "tipo": "categorica", "categorias": ["Hombre"]},
    )

    condiciones = condiciones_de_subgrupos(con, body)

    assert set(condiciones.keys()) == set(ORDEN_SUBGRUPOS_1F)
    recuentos = _recuentos(con, condiciones)
    assert recuentos["filtro1_si"] == 2
    assert recuentos["filtro1_no"] == 1


def test_condiciones_de_subgrupos_usa_cadena_larga_con_filtro3_y_filtro4():
    con = _con_cadena_larga()
    body = SubgruposRequest(
        un_filtro=True,
        filtro1={"columna": "edad_actual_fecha_corte", "tipo": "numerica", "operador": ">=", "valor": 45},
        filtro2={"columna": "sexo", "tipo": "categorica", "categorias": ["Hombre"]},
        filtro3={"columna": "nacionalidad", "tipo": "categorica", "categorias": ["Latino"]},
        filtro4={"columna": "autonomo", "tipo": "categorica", "categorias": ["SI"]},
    )

    condiciones = condiciones_de_subgrupos(con, body)

    assert set(condiciones.keys()) == set(ORDEN_SUBGRUPOS_1F)
    recuentos = _recuentos(con, condiciones)
    assert recuentos["filtro1_si"] == 2
    assert recuentos["filtro1_no"] == 1
