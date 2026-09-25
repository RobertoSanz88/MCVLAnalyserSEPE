"""Genera los JSON de ayuda de la app (app/static/datos/). Solo lo usa quien mantiene el proyecto.

  python tools/generar_datos_ayuda.py variables <carpeta_parquet>
  python tools/generar_datos_ayuda.py casos
"""
import json
import re
import sys
from pathlib import Path

import duckdb

RAIZ = Path(__file__).resolve().parent.parent
DATOS = RAIZ / "app" / "static" / "datos"

# Titulos de las 37 variables originales de Afiliacion (guia MCVL, 2.001 a 2.037), en el orden de las columnas 01-37.
AFILIACION_ORIGINALES = [
    "IDENTIFICADOR DE LA PERSONA FÍSICA (IPF)", "RÉGIMEN DE COTIZACIÓN", "GRUPO DE COTIZACIÓN",
    "TIPO DE CONTRATO DE TRABAJO", "COEFICIENTE DE TIEMPO PARCIAL", "FECHA REAL DEL ALTA EN AFILIACIÓN",
    "FECHA REAL DE LA BAJA EN AFILIACIÓN", "CAUSA DE BAJA EN AFILIACIÓN", "MINUSVALÍA SEGÚN ALTA EN AFILIACIÓN",
    "CÓDIGO DE CUENTA DE COTIZACIÓN (CCC)", "DOMICILIO DE ACTIVIDAD DE LA CUENTA DE COTIZACIÓN",
    "ACTIVIDAD ECONÓMICA DE LA CUENTA DE COTIZACIÓN (CNAE 2009)", "NÚMERO DE TRABAJADORES EN LA CUENTA DE COTIZACIÓN",
    "FECHA DE ALTA DEL PRIMER TRABAJADOR DE LA CUENTA DE COTIZACIÓN",
    "TIPO DE RELACIÓN LABORAL (TRL) DE LA CUENTA DE COTIZACIÓN", "COLECTIVO ESPECIAL DE LA CUENTA DE COTIZACIÓN",
    "EMPLEADOR (TIPO DE IDENTIFICADOR)", "EMPLEADOR (FORMA JURÍDICA) – LETRA NIF DE LA ENTIDAD PAGADORA",
    "IDENTIFICADOR (NIF/CIF) ANONIMADO DE LA ENTIDAD PAGADORA", "CÓDIGO DE CUENTA DE COTIZACIÓN PRINCIPAL (CCCP)",
    "PROVINCIA DEL DOMICILIO DE LA CUENTA DE COTIZACIÓN PRINCIPAL",
    "FECHA MODIFIC. DEL TIPO CONTRATO INICIAL O COEF. TIEMPO PARCIAL INICIAL", "TIPO DE CONTRATO INICIAL",
    "COEFICIENTE DE TIEMPO PARCIAL INICIAL", "FECHA MODIFIC. TIPO CONTRATO SEGUNDO O COEF. TIEMPO PARCIAL SEGUNDO",
    "TIPO DE CONTRATO SEGUNDO", "COEFICIENTE DE TIEMPO PARCIAL SEGUNDO",
    "FECHA DE MODIFICACIÓN DEL GRUPO DE COTIZACIÓN INICIAL", "GRUPO DE COTIZACIÓN INICIAL",
    "ACTIVIDAD ECONÓMICA DE LA CUENTA DE COTIZACIÓN (CNAE 93)", "SISTEMA ESPECIAL DE TRABAJADORES AGRARIOS (SETA)",
    "TIPO DE RELACIÓN CON OTRAS ENTIDADES O AUTÓNOMOS", "FECHA DE EFECTO DEL ALTA EN AFILIACIÓN",
    "FECHA DE EFECTO DE LA BAJA EN AFILIACIÓN", "CONVENIO COLECTIVO 1", "CONVENIO COLECTIVO 2",
    "NÚMERO DE TRABAJADORES EN LA CUENTA DE COTIZACIÓN PRINCIPAL",
]
AFILIACION_DERIVADAS = {
    "Baja_modificada": "Fecha de baja de afiliación con los episodios abiertos (año 2099) cortados en la fecha de corte.",
    "Empleo_publico": "SI si la letra del NIF del empleador es P (corporación local), Q (organismo público) o S (órgano de la "
                      "Administración); NO en otro caso.",
    "DURACION_EMPLEO": "Baja_modificada - fecha de alta + 1 día, en microsegundos. 0 en los episodios de prestación o subsidio "
                       "(TRL 700-799) y TRL 400; NULL si sale negativa.",
    "DURACION_DESEMPLEO": "Igual, pero solo con valor en los episodios de prestación o subsidio de desempleo (TRL 700-799); 0 en "
                          "el resto; NULL si sale negativa.",
}
PERSONAS_ORIGINALES = {"01 IPF", "02 FECHA_NACIMIENTO", "03 SEXO", "04 NACIONALIDAD", "05 PROV_NACIMIENTO",
                       "06 PROV_1a_AFILIACION", "07 DOMIC_RESID_HABITUAL", "08 FECHA_FALLECIMIENTO", "09 PAIS_NACIMIENTO"}
DESCRIPCIONES_EXTRA = {
    "Situacion_actual_afiliacion": "Situación a la fecha de corte según el último episodio: Alta_ocupado, Baja_de_actividad, "
                                   "Baja_para_pension, Baja_de_prestacion, Baja_de_subsidio, Asimilado_al_alta_prest_subs, "
                                   "Asimilado_al_alta_subs52, Asimilado_otros, Nunca_ha_cotizado.",
    "Situacion_Paro_Ocupacion": "Ocupado, Pensionista, Parado_LD (más de 365 días sin empleo), Parado_NoLD, "
                                "Ocupado_y_pensionista, Parado_LD_pension_no_jubilacion, Parado_NoLD_pension_no_jubilacion.",
}


def normalizar_nombre(col: str) -> str:
    """Sustituye el anio de las columnas fiscales/de pension por AAAA (y '2024/5' por 'AAAA/A')."""
    col = re.sub(r"(\d{4})/\d$", "AAAA/A", col)
    return re.sub(r"_(\d{4})$", "_AAAA", col)


def _descripciones_md() -> dict:
    d = {}
    for linea in (RAIZ / "docs" / "esquema_datos_mcvl.md").read_text(encoding="utf-8").splitlines():
        if linea.startswith("|"):
            celdas = [c.strip() for c in linea.strip().strip("|").split("|")]
            if len(celdas) >= 2:
                for n in re.findall(r"`([^`]+)`", celdas[0]):
                    d[normalizar_nombre(n)] = celdas[-1].replace("`", "")
    return d


def _esquema(carpeta: Path, fichero: str):
    con = duckdb.connect()
    return [(x[0], x[1]) for x in con.execute(
        f"DESCRIBE SELECT * FROM read_parquet('{(carpeta / fichero).as_posix()}')").fetchall()]


def generar_variables(carpeta: Path) -> dict:
    md = _descripciones_md()
    md.update({normalizar_nombre(k): v for k, v in DESCRIPCIONES_EXTRA.items()})
    personas = {}
    for i, (n, t) in enumerate(_esquema(carpeta, "tabla_personas_extendida.parquet")):
        clave = normalizar_nombre(n)
        personas[clave] = {"orden": i + 1, "tipo": t, "origen": "original" if n in PERSONAS_ORIGINALES else "derivada",
                           "descripcion": md.get(clave, "")}
    afiliacion = {}
    for i, (n, t) in enumerate(_esquema(carpeta, "tabla_afiliacion_extendida.parquet")):
        if i < len(AFILIACION_ORIGINALES):
            origen, desc = "original", AFILIACION_ORIGINALES[i]
        else:
            origen, desc = "derivada", AFILIACION_DERIVADAS.get(n, "")
        afiliacion[n] = {"orden": i + 1, "tipo": t, "origen": origen, "descripcion": desc}
    return {"personas": personas, "afiliacion": afiliacion}


def generar_casos() -> list:
    import openpyxl

    wb = openpyxl.load_workbook(RAIZ / "tests" / "reglas_consistencia_resultados_sin_ipf.xlsx", read_only=True)
    filas = list(wb["Resultados"].iter_rows(values_only=True))
    cab = list(filas[0])
    ix = {n: cab.index(n) for n in ("ID", "Regla (debe cumplirse)", "Estado (seguimiento)", "Filas que la incumplen",
                                    "% de personas")}
    ix_expl = next(i for i, n in enumerate(cab) if str(n).startswith("Explicaci"))
    casos = []
    for f in filas[1:]:
        estado = f[ix["Estado (seguimiento)"]]
        if estado in ("Casos raros (reales)", "Baja calidad de los datos"):
            casos.append({"id": f[ix["ID"]], "regla": f[ix["Regla (debe cumplirse)"]], "estado": estado,
                          "n": f[ix["Filas que la incumplen"]] or 0, "pct": f[ix["% de personas"]] or 0,
                          "explicacion": f[ix_expl] or ""})
    casos.sort(key=lambda c: (c["estado"] != "Casos raros (reales)", c["id"]))
    return casos


if __name__ == "__main__":
    modo = sys.argv[1]
    if modo == "casos":
        casos = generar_casos()
        DATOS.mkdir(parents=True, exist_ok=True)
        (DATOS / "casos_raros.json").write_text(json.dumps(casos, ensure_ascii=False, indent=1), encoding="utf-8")
        print("casos raros:", sum(c["estado"].startswith("Casos") for c in casos),
              "baja calidad:", sum(c["estado"].startswith("Baja") for c in casos))
    if modo == "variables":
        datos = generar_variables(Path(sys.argv[2]))
        vacias = [n for n, v in datos["personas"].items() if not v["descripcion"]]
        DATOS.mkdir(parents=True, exist_ok=True)
        (DATOS / "variables.json").write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
        print("personas:", len(datos["personas"]), "afiliacion:", len(datos["afiliacion"]), "sin descripcion:", vacias)
