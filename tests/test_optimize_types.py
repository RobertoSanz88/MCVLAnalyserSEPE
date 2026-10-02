import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from etl.optimize_types import ColaCorruptaError, _leer_csv_seguro, _separar_cola_corrupta

COLUMNAS = ["a", "b", "c"]


def _fila(a, b, c):
    # Ancho fijo (9 bytes + CRLF), igual que las filas reales de la MCVL: numero de columnas fijo
    # dentro de un ancho total fijo, para que el ancho "normal" sea detectable.
    return f"{a:04d};{b:02d};{c}".encode() + b"\r\n"


def _escribir(tmp_path, filas, nombre="datos.txt"):
    ruta = tmp_path / nombre
    with open(ruta, "wb") as f:
        f.writelines(filas)
    return str(ruta)


def test_separar_cola_corrupta_detecta_la_cola_truncada(tmp_path):
    buenas = [_fila(i, i % 10, i % 3) for i in range(50)]
    # Cola corrupta: dos lineas finales con un ancho distinto al habitual (visto en el fichero
    # real de la MCVL 2025, Pensiones: 2.037 bytes mal formados al final de 1,4 GB).
    corruptas = [b"0050;9\r\n", b"005\r\n"]
    ruta = _escribir(tmp_path, buenas + corruptas)

    bytes_buenos, resumen = _separar_cola_corrupta(ruta)

    assert bytes_buenos == b"".join(buenas)
    assert "2 lineas" in resumen  # las 2 corruptas descartadas
    assert "50 de 52" in resumen  # lineas buenas de un total de 52


def test_separar_cola_corrupta_no_adivina_si_la_anomalia_no_esta_al_final(tmp_path):
    buenas = [_fila(i, i % 10, i % 3) for i in range(50)]
    filas = buenas[:25] + [b"rota;\r\n"] + buenas[25:]  # anomalia en MEDIO del fichero, no al final
    ruta = _escribir(tmp_path, filas)

    with pytest.raises(ColaCorruptaError):
        _separar_cola_corrupta(ruta)


def test_leer_csv_seguro_recupera_las_filas_buenas_pese_a_la_cola_corrupta(tmp_path):
    # Mas campos de los esperados (como en el caso real: 97 campos en vez de 46) es lo que de
    # verdad hace que pandas reviente con ParserError - con MENOS campos, pandas solo rellena con
    # NaN y no llega a fallar, no sirve para probar esta ruta.
    buenas = [_fila(i, i % 10, i % 3) for i in range(200)]
    corruptas = [b"9999;99;9;9;9;9\r\n", b"8888;88;8;8\r\n"]
    ruta = _escribir(tmp_path, buenas + corruptas)

    df = _leer_csv_seguro(ruta, COLUMNAS)

    assert len(df) == 200
    assert df["a"].tolist() == list(range(200))


def test_leer_csv_seguro_sin_corrupcion_no_cambia_nada(tmp_path):
    buenas = [_fila(i, i % 10, i % 3) for i in range(30)]
    ruta = _escribir(tmp_path, buenas)

    df = _leer_csv_seguro(ruta, COLUMNAS)

    assert len(df) == 30
