"""El Excel exportado de "Cruce de variables" solo decia el subcolectivo y
las personas, pero no que variables se habian cruzado - el usuario no podia
saber, al volver al fichero mas tarde, si esa tabla era "edad x sexo" o
"actividad economica x grupo de cotizacion". Estas pruebas fijan que ahora
cada hoja indica tambien las variables."""
import sys
from pathlib import Path
from unittest.mock import patch

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from openpyxl import Workbook, load_workbook  # noqa: E402
from openpyxl.styles import Font  # noqa: E402
from app.routers.colectivos import (  # noqa: E402
    _escribir_hoja_cruce_subgrupo,
    _construir_excel_cruce,
    CruceExportRequest,
    ORDEN_SUBGRUPOS,
)


def _subgrupo_categorica_categorica():
    return {
        "etiqueta": "sexo Hombre + nacionalidad Espana",
        "recuento": 123,
        "x": ["Hombre", "Mujer"],
        "y": ["Espana", "Extranjero"],
        "z": [[10, 20], [30, 40]],
    }


def test_hoja_subgrupo_indica_variable_eje_x_e_y():
    wb = Workbook()
    ws = wb.active
    _escribir_hoja_cruce_subgrupo(
        ws, _subgrupo_categorica_categorica(), "sexo", "nacionalidad_agrupado",
        "categorica_categorica", "contingencia", Font(bold=True),
    )
    etiquetas = {ws.cell(row=r, column=1).value: ws.cell(row=r, column=2).value for r in range(1, 5)}
    assert etiquetas["Variable eje X"] == "sexo"
    assert etiquetas["Variable eje Y"] == "nacionalidad_agrupado"


def test_hoja_subgrupo_tabla_de_datos_sigue_intacta_tras_la_cabecera_nueva():
    wb = Workbook()
    ws = wb.active
    _escribir_hoja_cruce_subgrupo(
        ws, _subgrupo_categorica_categorica(), "sexo", "nacionalidad_agrupado",
        "categorica_categorica", "contingencia", Font(bold=True),
    )
    # La rejilla debe seguir siendo legible aunque se mueva de fila: buscamos
    # la celda "Hombre" y comprobamos que su columna tiene los valores de z.
    valores = [c.value for fila in ws.iter_rows() for c in fila]
    assert "Hombre" in valores and "Mujer" in valores
    assert 10 in valores and 40 in valores


def test_excel_numerica_numerica_indica_variables_en_cada_subgrupo():
    datos = {
        nombre: {"etiqueta": nombre, "recuento": 1, "x": [0, 1], "y": [0, 1], "z": [[1, 2], [3, 4]]}
        for nombre in ORDEN_SUBGRUPOS
    }
    payload = CruceExportRequest(
        tipo="numerica_numerica", variable1="edad_actual_fecha_corte", variable2="cotizado_anual_promedio",
        tipo_grafico="dispersion", datos=datos,
    )
    contenido = _construir_excel_cruce(payload, con=None)
    wb = load_workbook(__import__("io").BytesIO(contenido))
    for hoja in wb.worksheets:
        etiquetas = {hoja.cell(row=r, column=1).value: hoja.cell(row=r, column=2).value for r in range(1, 5)}
        assert etiquetas["Variable eje X"] == "edad_actual_fecha_corte"
        assert etiquetas["Variable eje Y"] == "cotizado_anual_promedio"


def test_excel_modo_3_variables_indica_las_3_variables():
    resultado_falso = {
        "x": ["A", "B"], "y": ["C", "D"],
        "z_media": [[1, 2], [3, 4]], "z_mediana": [[1, 2], [3, 4]], "n": [[5, 6], [7, 8]],
    }
    payload = CruceExportRequest(
        tipo="categorica_categorica_central", variable1="act_econ_ult_baja", variable2="grupo_cotiz_ppal",
        variable_central="ingresos_bruto_total_2025", tipo_grafico="mapacalor",
    )
    with patch(
        "app.routers.colectivos._cruce_categorica_categorica_central",
        return_value=("act_econ_ult_baja", "grupo_cotiz_ppal", resultado_falso),
    ):
        contenido = _construir_excel_cruce(payload, con=None)
    wb = load_workbook(__import__("io").BytesIO(contenido))
    for hoja in wb.worksheets:
        etiquetas = {hoja.cell(row=r, column=1).value: hoja.cell(row=r, column=2).value for r in range(1, 6)}
        assert etiquetas["Variable eje X"] == "act_econ_ult_baja"
        assert etiquetas["Variable eje Y"] == "grupo_cotiz_ppal"
        assert etiquetas["Variable numérica"] == "ingresos_bruto_total_2025"
