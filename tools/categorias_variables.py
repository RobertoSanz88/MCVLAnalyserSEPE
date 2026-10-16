"""Tablas "cómo se asigna cada categoría" de la vista Descripción de variables (v2.3).

Para las variables de Personas extendida cuyas categorías se CALCULAN con reglas (no son una simple
traducción de un diccionario), una lista de (categoría, regla) escrita en lenguaje sencillo. Las
constantes que tiene el propio ETL (grupos de países, etiquetas de grupo de cotización, de tipo de
contrato y de régimen) se importan de `etl/` para que la documentación no se desincronice de lo que de
verdad calcula; las reglas que solo viven dentro de una consulta SQL (p. ej. la de
`Situacion_actual_afiliacion`) se han escrito a mano leyendo `etl/personas_extendida.py` y las protege
`tests/test_variables.py`, que compara las categorías documentadas con los valores reales de la muestra.

Las claves del diccionario son los nombres de columna normalizados (año -> AAAA), igual que el resto de
`variables.json`.
"""
import csv
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))


def _filas(*pares):
    return [{"categoria": c, "regla": r} for c, r in pares]


def _nombres_paises() -> dict:
    ruta = RAIZ / "etl" / "metadata" / "paises_dict.csv"
    with open(ruta, encoding="utf-8") as f:
        return {fila["codigo"]: fila["nombre"] for fila in csv.DictReader(f, delimiter=";")}


def _situacion_actual_afiliacion():
    filas = _filas(
        ("Alta_ocupado", "Su episodio más reciente sigue abierto (sin fecha de baja real) y no es de prestación, "
                         "subsidio ni de clave TRL 980/400."),
        ("Baja_de_actividad", "Su episodio más reciente ya tiene baja, de causa distinta de 58, y no es de prestación, "
                              "subsidio ni de clave TRL 980/400."),
        ("Baja_para_pension", "Su episodio más reciente tiene baja con causa 58 (paso a pensionista)."),
        ("Baja_de_prestacion", "Episodio más reciente con baja de causa distinta de 58 y clave TRL 751 o 752 "
                               "(prestación por desempleo)."),
        ("Baja_de_subsidio", "Episodio más reciente con baja de causa distinta de 58 y clave TRL 753, 754, 755 o 756 "
                             "(subsidio por desempleo)."),
        ("Asimilado_al_alta_prest_subs", "Episodio más reciente abierto y clave TRL 751, 752, 755 o 756: cobrando "
                                         "prestación o subsidio de desempleo."),
        ("Asimilado_al_alta_subs52", "Episodio más reciente abierto y clave TRL 753 o 754: subsidio de desempleo de "
                                     "mayores de 52/55 años o de fijos discontinuos."),
        ("Asimilado_otros", "Episodio más reciente de clave TRL 980 o 400 (peculiaridades que no se consideran alta "
                            "laboral), abierto o con baja de causa distinta de 58."),
        ("Nunca_ha_cotizado", "No tiene ningún episodio de afiliación."),
    )
    nota = ("Se mira un único episodio por persona, el más reciente: el de fecha de baja más reciente y, si empatan, el "
            "de fecha de alta más reciente (si aún empatan, gana el de prestación o subsidio). \"Abierto\" significa que "
            "el origen le da fecha de baja en el año 2099. Ojo con Asimilado_al_alta_subs52: sale solo de la clave TRL "
            "del episodio, no de la edad de la persona, y la guía MCVL indica que las claves 753/754 también las usan "
            "los trabajadores fijos discontinuos que cobran el subsidio entre campañas, aunque no hayan cumplido "
            "esa edad (la guía reconoce que no se pueden separar con seguridad solo con los datos).")
    return filas, nota


def _situacion_paro_ocupacion():
    filas = _filas(
        ("Ocupado", "Situacion_actual_afiliacion = Alta_ocupado y no cobra ninguna pensión."),
        ("Ocupado_y_pensionista", "Alta_ocupado y cobra alguna pensión (incluida la jubilación: es la jubilación activa)."),
        ("Pensionista", "Baja_para_pension, Nunca_ha_cotizado, o un parado que cobra una pensión de jubilación."),
        ("Parado_LD", "Cualquier otra situación (baja de actividad, prestación, subsidio, asimilado...) con más de 365 "
                      "días sin empleo y sin pensión."),
        ("Parado_NoLD", "Igual, con 365 días sin empleo o menos (o sin dato de duración) y sin pensión."),
        ("Parado_LD_pension_no_jubilacion", "Parado de larga duración que cobra una pensión que no es de jubilación "
                                            "(viudedad, incapacidad, orfandad...)."),
        ("Parado_NoLD_pension_no_jubilacion", "Igual, para parado de corta duración."),
    )
    nota = ("Los días sin empleo (duracion_actual_sin_empleo) se cuentan desde que empezó la última racha de "
            "prestación o subsidio, si la persona la cobra ahora o la ha cobrado en su último periodo, y desde la "
            "fecha de su última baja en cualquier otro caso. \"Cobra pensión\" es Cobra_pension_AAAA/A = SI. "
            "Se parte siempre de Situacion_actual_afiliacion.")
    return filas, nota


def _agrupado_paises(texto_variable):
    from etl import personas_extendida as pe
    nombres = _nombres_paises()
    grupos = [
        ("espana", pe.ESPANA), ("paises_UE_UK", pe.PAISES_EU_UK), ("paises_latinos", pe.PAISES_LATINOS),
        ("paises_africanos", pe.PAISES_AFRICANOS), ("paises_lejanos", pe.PAISES_LEJANOS),
    ]
    filas = _filas(*[
        (grupo, f"{texto_variable} con código: " + "; ".join(f"{c} {nombres.get(c, '?')}" for c in codigos) + ".")
        for grupo, codigos in grupos])
    nota = ("Los 28 códigos de la guía MCVL (1.004) están repartidos entre estos cinco grupos, ninguno queda fuera. "
            "Ojo con paises_lejanos: no es solo Asia, incluye Ucrania, el resto de países europeos, otros países y "
            "territorios y el código 99 (\"No consta\"). El Reino Unido (N16) sigue agrupado con la UE, aunque ya no "
            "es miembro desde 2020.")
    return filas, nota


def _tipo_contrato(texto_episodio):
    filas = _filas(
        ("No consta", "Clave de contrato 000."),
        ("Indefinido_t_completo", "Claves 100 a 199."),
        ("Indefinido_t_parcial", "Claves 200 a 299."),
        ("Indefinido_fijo_discontinuo", "Claves 300 a 399."),
        ("Temporal_t_completo", "Claves 400 a 499."),
        ("Temporal_t_parcial", "Claves 500 a 599."),
        ("Otros", "Cualquier otra clave (p. ej. 990) y las claves antiguas sin sucesora."),
    )
    nota = ("Antes de agrupar, cada clave antigua (deprecada) se sustituye por su clave sucesora de la guía MCVL "
            "(si la antigua se bifurca en completo y parcial, se elige según el coeficiente de tiempo parcial del "
            "episodio). " + texto_episodio)
    return filas, nota


def _regimen():
    from etl.reg_cotizacion_agrupado import ETIQUETAS_AGRUPADO
    reglas = [
        "Régimen con código 01xx (011x a 016x), excepto el 0138.",
        "Código 0138 (Régimen General, sistema especial de empleados de hogar).",
        "Código 05xx.", "Código 06xx.", "Código 07xx.", "Código 08xx.", "Código 09xx.", "Código 12xx.",
    ]
    filas = _filas(*[(ETIQUETAS_AGRUPADO[i], reglas[i]) for i in range(8)],
                   ("No consta", "Cualquier otro código, o sin código."))
    nota = ("La familia son las dos primeras cifras del código de régimen de 4 dígitos, como indica la guía MCVL "
            "para sus estadísticas. El régimen principal es el que acumula más tiempo de empleo (los episodios "
            "solapados de la misma familia se cuentan una sola vez).")
    return filas, nota


def _pensiones(texto):
    filas = _filas(
        ("Incapacidad", "Clase de prestación 10 a 19."),
        ("Jubilación", "Clase de prestación 20 a 26, o la clave 2E."),
        ("Viudedad", "Clase de prestación 30 a 32."),
        ("Orfandad", "Clase de prestación 40 a 42."),
        ("Pension_a_favor_familiares", "Clase de prestación 51 a 54."),
        ("Incapacidad>65", "Clave formada por la letra J y un dígito (incapacidad de mayores de 65 años)."),
        ("Otros", "Cualquier otra clave."),
    )
    return filas, "Se obtiene de la clase de prestación (campo 4.004 de la guía MCVL), sin repetir tipos. " + texto


def _grupos_cotizacion(texto_episodio):
    from etl.personas_extendida import GRUPOS_COTIZ_DICT
    filas = []
    for clave, etiqueta in GRUPOS_COTIZ_DICT.items():
        if clave == 0:
            regla = ("Clave 00, cualquier clave fuera de 00-12 (se asimila a \"no consta\", como indica la guía), "
                     "o persona sin episodio válido para esta variable.")
        else:
            regla = f"Clave {clave:02d} del grupo de cotización (guía MCVL, 2.003)."
        filas.append({"categoria": etiqueta, "regla": regla})
    return filas, texto_episodio


def _discapacidad(true_regla, false_regla, nota=""):
    return _filas(("true", true_regla), ("false", false_regla)), nota


def categorias_personas() -> dict:
    """{nombre normalizado de la variable: {"filas": [{categoria, regla}], "nota": str}}"""
    entradas = {
        "Situacion_actual_afiliacion": _situacion_actual_afiliacion(),
        "Situacion_Paro_Ocupacion": _situacion_paro_ocupacion(),
        "Nacionalidad_agrupado": _agrupado_paises("Nacionalidad"),
        "Pais_nacim_agrupado": _agrupado_paises("País de nacimiento"),
        "Tipo_Contr_ppal_Agrupado": _tipo_contrato(
            "Para cada persona se suman las duraciones de sus episodios de empleo por categoría y se elige la que "
            "más tiempo acumula."),
        "tipo_contr_ult_baja": _tipo_contrato("Se aplica solo al contrato del episodio más reciente de la persona."),
        "Reg_cotiz_ppal_Agrupado": _regimen(),
        "Pension_actual": _pensiones("Solo cuenta lo cobrado en el año de referencia de la muestra."),
        "Pension_todas_cobradas": _pensiones("Cuenta lo cobrado alguna vez (desde 1996)."),
        "Grupo_cotiz_ppal": _grupos_cotizacion("Es el grupo con más tiempo acumulado en toda su vida laboral."),
        "Grupo_cotiz_1er_Empleo_Signif": _grupos_cotizacion(
            "Es el grupo de cotización de su primer empleo significativo (primer episodio de empleo de 270 días o más)."),
        "Grupo_Ultimo_Empleo": _grupos_cotizacion("Es el grupo de cotización del episodio con la baja más reciente."),
        "Discap_fiscal": _discapacidad(
            "Alguna fila suya en los datos fiscales (modelo 190) tiene el campo de discapacidad mayor que cero.",
            "No aparece en los datos fiscales, o ninguna de sus filas indica discapacidad."),
        "Discap_afiliacion": _discapacidad(
            "Algún episodio de afiliación con tipo de contrato para discapacitados (130, 139, 230, 239, 330, 339, 430 "
            "o 530) o con minusvalía en el alta mayor que cero.",
            "Ningún episodio cumple lo anterior."),
        "Discap_pension": _discapacidad(
            "Alguna pensión con clase de mínimo 5 o 6, o con tipo de situación de jubilación 17.",
            "Ninguna pensión cumple lo anterior.",
            "La definición exacta de esta variable está pendiente de confirmar (ver docs/esquema_datos_mcvl.md)."),
    }
    return {nombre: {"filas": filas, "nota": nota} for nombre, (filas, nota) in entradas.items()}
