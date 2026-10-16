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
TEXTO_ORIGINAL = "Campo original de la MCVL, tal como viene en el fichero."

# Cómo se ha construido cada variable DERIVADA de Personas extendida, en lenguaje sencillo (para la vista
# "Descripción de variables" de la app). Se ha escrito leyendo el propio etl/personas_extendida.py (Grupos A-H):
# describe la operación real hecha en el ETL, sin tecnicismos de SQL/pandas. Las variables ORIGINALES (las que
# vienen tal cual del fichero de la MCVL) llevan TEXTO_ORIGINAL en su lugar, no una entrada aquí.
CONSTRUCCION_PERSONAS = {
    "prov_resid_habitual": "Se ha tomado el código de municipio de residencia (07 DOMIC_RESID_HABITUAL) y se ha "
        "quedado con los dos primeros dígitos, que identifican la provincia.",
    "edad_fallecimiento": "Para quien consta fallecido, se ha restado su fecha de nacimiento a su fecha de "
        "fallecimiento (ambas con precisión de mes); para quien no ha fallecido queda vacía.",
    "ha_fallecido": "Se marca SI cuando la fecha de fallecimiento no está vacía, y NO en caso contrario.",
    "Nacionalidad_agrupado": "Se ha agrupado el código de nacionalidad en 5 zonas (España, Latinoamérica, "
        "África, Unión Europea y Reino Unido, y el resto de países), según la tabla de la guía MCVL.",
    "Pais_nacim_agrupado": "Igual que Nacionalidad_agrupado, pero a partir del país de nacimiento en vez de "
        "la nacionalidad.",
    "Edad_Actual_Fecha_Corte": "Se ha calculado la edad de la persona a la fecha de corte de esta muestra; "
        "para quien ha fallecido, se calcula en su lugar a su fecha de fallecimiento, para que no salga con "
        "más edad de la que llegó a tener.",
    "Edad_Primer_Empleo_Signif": "Se ha calculado la edad de la persona en la fecha de inicio de su primer "
        "empleo significativo (ver Duracion_1er_Empleo_Signif).",
    "Duracion_1er_Empleo_Signif": "Se han recorrido los episodios de empleo de cada persona en la tabla de "
        "afiliación, por orden de fecha de alta, descartando los tipos de contrato poco representativos "
        "(parciales, prácticas, formación...), y se ha elegido el primero que dura 270 días o más; se toma "
        "su duración.",
    "Grupo_cotiz_1er_Empleo_Signif": "Se ha tomado el grupo de cotización de ese mismo episodio (el del "
        "primer empleo significativo).",
    "Edad_Ultimo_Empleo": "Se ha calculado la edad de la persona en la fecha de fin de su último episodio de "
        "empleo real (sin contar los de prestación o subsidio); para quien ha fallecido con un episodio "
        "todavía abierto al morir, la fecha no supera su fallecimiento.",
    "Grupo_Ultimo_Empleo": "Se ha buscado, para cada persona, el episodio de afiliación con la fecha de baja "
        "más reciente, y se ha tomado su grupo de cotización.",
    "causa_ult_baja": "Se ha tomado la causa de baja de ese mismo episodio (el de baja más reciente), con su "
        "descripción de la guía MCVL.",
    "tipo_contr_ult_baja": "Se ha tomado el tipo de contrato de ese mismo episodio y se ha agrupado en 7 "
        "categorías (indefinido, temporal, tiempo completo/parcial, fijo discontinuo...).",
    "act_econ_ult_baja": "Se ha tomado la actividad económica (CNAE) de ese mismo episodio, con su descripción.",
    "t_contrato_mas_largo": "Se ha buscado, entre todos los episodios de empleo de cada persona, el que tiene "
        "mayor duración, y se ha tomado esa duración.",
    "Causa_Baja_Trab_Ppal": "Se ha tomado la causa de baja de ese mismo episodio (el más largo).",
    "Fecha_ini_Primer_Empleo": "Se ha buscado la fecha de alta más antigua entre los episodios de empleo real "
        "de cada persona (sin contar los de prestación o subsidio).",
    "Tiempo_total_desde_inicio_VL": "Se ha calculado el tiempo transcurrido entre el episodio más antiguo de "
        "cada persona (de cualquier tipo, empleo o prestación) y la fecha de corte de la muestra, o su "
        "fallecimiento si es anterior.",
    "duracion_promedio_EMPLEO": "Se ha calculado la duración media de los episodios de empleo real de cada "
        "persona (excluyendo los de duración cero).",
    "Cotizado_anual_promedio": "Se han sumado todas las bases de cotización anuales de la persona (cuenta "
        "ajena y cuenta propia) y se han dividido entre sus años de empleo, de prestación/subsidio y los de "
        "solape entre ambos.",
    "num_altas_anual_promedio": "Se ha contado el número de episodios de empleo de cada persona y se ha "
        "dividido entre su tiempo total desde el inicio de su vida laboral, para obtener una tasa anual.",
    "num_bajas_anual_promedio": "Se han contado los episodios de empleo con una fecha de baja real (que no "
        "siguen abiertos) y se ha dividido entre el tiempo total desde el inicio de la vida laboral, para "
        "obtener una tasa anual.",
    "T_total_EMPLEO": "Se han unido en el tiempo todos los episodios de empleo de cada persona (sin contar dos "
        "veces los periodos en que tuvo dos empleos a la vez) y se ha sumado su duración total.",
    "T_total_DESEMPLEO": "Igual que T_total_EMPLEO, pero con los episodios de prestación o subsidio de "
        "desempleo.",
    "T_total_MIXTO_EMPL_DESEMPL": "Se ha sumado el tiempo en que la persona tuvo a la vez un episodio de "
        "empleo y uno de prestación o subsidio (compatibilidad de ambos).",
    "T_total_NO_EMPLEO": "Se ha calculado como el tiempo restante: el tiempo total desde el inicio de la vida "
        "laboral menos el tiempo de empleo, de desempleo y el mixto.",
    "%T_total_EMPLEO": "Se ha calculado el porcentaje que representa T_total_EMPLEO sobre el tiempo total "
        "desde el inicio de la vida laboral.",
    "%T_total_DESEMPLEO": "Igual, para T_total_DESEMPLEO.",
    "%T_total_MIXTO_EMPL_DESEMPL": "Igual, para T_total_MIXTO_EMPL_DESEMPL.",
    "%T_total_NO_EMPLEO": "Igual, para T_total_NO_EMPLEO. Los cuatro porcentajes suman 100.",
    "Reg_cotiz_ppal_Agrupado": "Se han sumado las duraciones de los episodios de empleo de cada persona por "
        "familia de régimen de cotización (agrupando regímenes equivalentes) y se ha elegido la familia con "
        "más duración acumulada.",
    "T_tot_reg_ppal": "Se ha tomado la duración acumulada de esa familia de régimen ganadora.",
    "Grupo_cotiz_ppal": "Igual que Reg_cotiz_ppal_Agrupado, pero por grupo de cotización; cualquier clave "
        "fuera del rango documentado por la guía (00 a 12) se trata como NO CONSTA.",
    "t_tot_grupo_ppal": "Se ha tomado la duración acumulada de ese grupo de cotización ganador.",
    "Tipo_Contr_ppal_Agrupado": "Se han sumado las duraciones de los episodios de empleo por tipo de "
        "contrato, ya agrupado en 7 categorías, y se ha elegido el tipo con más duración acumulada.",
    "T_tot_tipo_contrato_ppal": "Se ha tomado la duración acumulada de ese tipo de contrato ganador.",
    "Prov_trabajo_ppal": "Se han cogido todos los episodios de empleo de la tabla de afiliación de cada "
        "persona y se han sumado las duraciones por provincia (a partir del código de cuenta de cotización), "
        "y se ha seleccionado la provincia con más duración de episodios de empleo.",
    "t_tot_prov_ppal": "Se ha tomado la duración acumulada en esa provincia ganadora.",
    "Act_econ_ppal": "Igual que Prov_trabajo_ppal, pero sumando por actividad económica (CNAE).",
    "t_tot_act_econ_ppal": "Se ha tomado la duración acumulada en esa actividad económica ganadora.",
    "ingresos_actividad_AAAA": "Se han sumado, de los datos fiscales (modelo 190) de la persona, los importes "
        "de las percepciones de actividad profesional, agraria y de estimación objetiva.",
    "ingresos_bruto_total_AAAA": "Se ha sumado el importe íntegro de todas las percepciones de la persona en "
        "los datos fiscales, de cualquier tipo.",
    "ingresos_otras_exentas_AAAA": "Se han sumado las percepciones de rentas exentas que no son pensión "
        "(dietas, indemnizaciones, prestaciones por maternidad/paternidad...).",
    "ingresos_pension_y_bajas_AAAA": "Se han sumado las percepciones de pensiones y haberes pasivos, junto "
        "con las prestaciones por incapacidad temporal (bajas por enfermedad) pagadas por la Seguridad Social "
        "o la mutua.",
    "ingresos_prestacion_AAAA": "Se han sumado las percepciones de prestación o subsidio de desempleo.",
    "ingresos_sueldo_AAAA": "Se han sumado las percepciones de trabajo por cuenta ajena (sueldo).",
    "retenciones_irpf_AAAA": "Se han sumado las retenciones de IRPF practicadas sobre todas las percepciones "
        "de la persona.",
    "clave_subclave_ppal": "Se ha sumado el importe de cada tipo de percepción (por ejemplo, sueldo o "
        "pensión) de la persona, y se ha elegido el tipo con mayor importe total.",
    "clave_subclave_todas_cobradas": "Se ha hecho la misma suma por tipo de percepción, y se han listado "
        "todos los tipos con importe mayor que cero, del de más importe al de menos.",
    "Prov_fiscal_actual": "Se ha elegido, entre los pagadores de la persona en los datos fiscales, la "
        "provincia del que le pagó más importe.",
    "Discap_fiscal": "Se marca SI cuando el campo de discapacidad de los datos fiscales de la persona es "
        "mayor que cero.",
    "Discap_afiliacion": "Se marca SI cuando algún episodio de afiliación de la persona tiene un tipo de "
        "contrato para discapacitados, o un grado de minusvalía mayor que cero.",
    "Discap_pension": "Se marca SI cuando alguna pensión de la persona tiene una clase de mínimo de gran "
        "invalidez/incapacidad, o una situación de jubilación con reducción por discapacidad.",
    "Pension_todas_cobradas": "Se ha mirado, en la tabla de pensiones, todos los tipos de pensión que la "
        "persona ha cobrado alguna vez (desde 1996), sin repetir el mismo tipo.",
    "Pension_actual": "Igual que Pension_todas_cobradas, pero solo con las pensiones cobradas en el año de "
        "referencia de esta muestra.",
    "Cobra_pension_AAAA/A": "Se marca SI cuando la persona tiene alguna fila en Pension_actual, y NO en caso "
        "contrario.",
    "fecha_ultima_baja": "Se ha buscado, entre todos los episodios de afiliación de la persona, la fecha de "
        "baja más reciente.",
    "Situacion_actual_afiliacion": "Se ha mirado el episodio de afiliación más reciente de la persona y se ha "
        "clasificado según si sigue abierto (de alta, o cobrando prestación o subsidio) o cerrado (por qué "
        "motivo: paso a pensionista, fin de una prestación/subsidio, u otra baja de actividad).",
    "Situacion_Paro_Ocupacion": "Se ha resumido Situacion_actual_afiliacion en Ocupado, Parado (con más o "
        "menos de un año sin empleo) o Pensionista, y se ha añadido si la persona cobra además una pensión al "
        "mismo tiempo.",
    "Fecha_inicio_ultima_racha_sin_empleo": "Se han mirado los últimos episodios de la persona y se ha "
        "buscado desde cuándo lleva cobrando, sin interrupción, una prestación o un subsidio (la racha en "
        "curso, o la última si ya terminó).",
    "duracion_actual_sin_empleo": "Se ha calculado, para quien está en paro, cuántos días han pasado desde el "
        "inicio de esa última racha de prestación/subsidio, o desde su última baja de afiliación si nunca ha "
        "pasado por el SEPE.",
    "causa_baja_mas_comun": "Se ha contado la causa de baja de todos los episodios de la persona (incluidos "
        "los de prestación) y se ha elegido la más frecuente.",
}

# Igual que CONSTRUCCION_PERSONAS, para las 4 variables derivadas de Afiliacion extendida.
CONSTRUCCION_AFILIACION = {
    "Baja_modificada": "Se ha copiado la fecha de baja real de cada episodio, sustituyendo el valor 2099 (que "
        "marca un episodio todavía abierto) por la fecha de corte de la muestra.",
    "Empleo_publico": "Se ha marcado SI cuando la letra del NIF del empleador del episodio corresponde a una "
        "corporación local, un organismo público o un órgano de la Administración, y NO en el resto.",
    "DURACION_EMPLEO": "Se ha calculado la duración de cada episodio (de la fecha de alta a Baja_modificada), "
        "poniéndola a cero en los episodios de prestación, subsidio u otras peculiaridades sin alta laboral.",
    "DURACION_DESEMPLEO": "Igual que DURACION_EMPLEO, pero solo con valor en los episodios de prestación o "
        "subsidio de desempleo; cero en el resto.",
}

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
    from categorias_variables import categorias_personas  # tablas "como se asigna cada categoria" (v2.3)
    categorias = categorias_personas()
    personas = {}
    for i, (n, t) in enumerate(_esquema(carpeta, "tabla_personas_extendida.parquet")):
        clave = normalizar_nombre(n)
        es_original = n in PERSONAS_ORIGINALES
        cat = categorias.get(clave, {"filas": [], "nota": ""})
        personas[clave] = {"orden": i + 1, "tipo": t, "origen": "original" if es_original else "derivada",
                           "descripcion": md.get(clave, ""),
                           "construccion": TEXTO_ORIGINAL if es_original else CONSTRUCCION_PERSONAS.get(clave, ""),
                           "categorias": cat["filas"], "categorias_nota": cat["nota"]}
    afiliacion = {}
    for i, (n, t) in enumerate(_esquema(carpeta, "tabla_afiliacion_extendida.parquet")):
        if i < len(AFILIACION_ORIGINALES):
            origen, desc, construccion = "original", AFILIACION_ORIGINALES[i], TEXTO_ORIGINAL
        else:
            origen, desc = "derivada", AFILIACION_DERIVADAS.get(n, "")
            construccion = CONSTRUCCION_AFILIACION.get(n, "")
        afiliacion[n] = {"orden": i + 1, "tipo": t, "origen": origen, "descripcion": desc,
                         "construccion": construccion}
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
        sin_construccion = [n for tabla in ("personas", "afiliacion") for n, v in datos[tabla].items()
                           if not v["construccion"]]
        DATOS.mkdir(parents=True, exist_ok=True)
        (DATOS / "variables.json").write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")
        print("personas:", len(datos["personas"]), "afiliacion:", len(datos["afiliacion"]), "sin descripcion:", vacias,
              "sin como-se-construye:", sin_construccion)
