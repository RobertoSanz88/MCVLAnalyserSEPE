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
