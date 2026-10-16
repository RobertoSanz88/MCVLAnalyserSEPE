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


# --- Tablas "cómo se asigna cada categoría" (v2.3) ---------------------------------------------------
VARIABLES_CON_CATEGORIAS = [
    "Situacion_actual_afiliacion", "Situacion_Paro_Ocupacion", "Nacionalidad_agrupado", "Pais_nacim_agrupado",
    "Tipo_Contr_ppal_Agrupado", "tipo_contr_ult_baja", "Reg_cotiz_ppal_Agrupado", "Pension_actual",
    "Pension_todas_cobradas", "Grupo_cotiz_ppal", "Grupo_cotiz_1er_Empleo_Signif", "Grupo_Ultimo_Empleo",
    "Discap_fiscal", "Discap_afiliacion", "Discap_pension",
]


def _personas_json():
    return json.loads((RAIZ / "app" / "static" / "datos" / "variables.json").read_text(encoding="utf-8"))["personas"]


def test_categorias_solo_en_las_variables_acordadas():
    p = _personas_json()
    for nombre, v in p.items():
        if nombre in VARIABLES_CON_CATEGORIAS:
            assert v["categorias"], nombre
            assert all(c["categoria"] and c["regla"] for c in v["categorias"]), nombre
        else:
            assert v.get("categorias", []) == [], nombre


def test_situacion_actual_afiliacion_tiene_sus_9_categorias_y_la_nota_de_fijos_discontinuos():
    v = _personas_json()["Situacion_actual_afiliacion"]
    assert [c["categoria"] for c in v["categorias"]] == [
        "Alta_ocupado", "Baja_de_actividad", "Baja_para_pension", "Baja_de_prestacion", "Baja_de_subsidio",
        "Asimilado_al_alta_prest_subs", "Asimilado_al_alta_subs52", "Asimilado_otros", "Nunca_ha_cotizado"]
    assert "fijos discontinuos" in v["categorias_nota"]


def test_nacionalidad_agrupado_lista_los_28_codigos_en_su_grupo_segun_el_etl():
    from etl import personas_extendida as pe
    p = _personas_json()
    grupos = {"paises_latinos": pe.PAISES_LATINOS, "paises_africanos": pe.PAISES_AFRICANOS,
              "paises_UE_UK": pe.PAISES_EU_UK, "paises_lejanos": pe.PAISES_LEJANOS, "espana": pe.ESPANA}
    for variable in ("Nacionalidad_agrupado", "Pais_nacim_agrupado"):
        reglas = {c["categoria"]: c["regla"] for c in p[variable]["categorias"]}
        assert set(reglas) == set(grupos), variable
        for grupo, codigos in grupos.items():
            assert all(cod in reglas[grupo] for cod in codigos), (variable, grupo)
    assert "N24" in {c["categoria"]: c["regla"] for c in p["Nacionalidad_agrupado"]["categorias"]}["paises_lejanos"]


def test_grupos_de_cotizacion_tienen_las_13_etiquetas_del_etl_y_la_regla_de_no_consta():
    from etl import personas_extendida as pe
    for variable in ("Grupo_cotiz_ppal", "Grupo_cotiz_1er_Empleo_Signif", "Grupo_Ultimo_Empleo"):
        v = _personas_json()[variable]
        assert [c["categoria"] for c in v["categorias"]] == list(pe.GRUPOS_COTIZ_DICT.values()), variable
        assert "fuera de 00-12" in v["categorias"][0]["regla"]


def test_endpoint_variables_incluye_categorias():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        r = c.get("/variables").json()
        por_nombre = {f["nombre"]: f for f in r["personas"]}
        assert len(por_nombre["Situacion_actual_afiliacion"]["categorias"]) == 9
        assert por_nombre["Situacion_actual_afiliacion"]["categorias_nota"]
        assert por_nombre["sexo" if "sexo" in por_nombre else "03 SEXO"]["categorias"] == []


def test_las_categorias_documentadas_cubren_los_valores_reales_de_la_muestra_activa():
    """Si el ETL cambia las etiquetas y se olvida la documentación, esto lo detecta con datos reales."""
    import duckdb
    from fastapi.testclient import TestClient
    from app.main import app
    from app.db import estado
    columnas_texto = ["Situacion_actual_afiliacion", "Situacion_Paro_Ocupacion", "Nacionalidad_agrupado",
                      "Pais_nacim_agrupado", "Tipo_Contr_ppal_Agrupado", "tipo_contr_ult_baja",
                      "Reg_cotiz_ppal_Agrupado", "Grupo_cotiz_ppal", "Grupo_cotiz_1er_Empleo_Signif",
                      "Grupo_Ultimo_Empleo"]
    p = _personas_json()
    with TestClient(app):
        fichero = (estado.muestra.carpeta / "tabla_personas_extendida.parquet").as_posix().replace("'", "''")
        con = duckdb.connect()
        for col in columnas_texto:
            reales = {r[0] for r in con.execute(
                f"SELECT DISTINCT \"{col}\" FROM read_parquet('{fichero}') WHERE \"{col}\" IS NOT NULL").fetchall()}
            documentadas = {c["categoria"] for c in p[col]["categorias"]}
            assert reales <= documentadas, (col, reales - documentadas)
