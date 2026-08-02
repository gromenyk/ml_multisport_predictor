"""Spanish display translations for reusable objective catalogs.

English names remain canonical. These deterministic translations provide a
readable UI without changing IDs or relationships.
"""

from __future__ import annotations

import re


CATEGORY_ES = {
    "Aerobic & Endurance": "Capacidad aeróbica y resistencia",
    "Durability & Fatigue Resistance": "Durabilidad y resistencia a la fatiga",
    "High Intensity & Anaerobic": "Alta intensidad y capacidad anaeróbica",
    "Strength & Neuromuscular": "Fuerza y capacidad neuromuscular",
    "Running & Trail": "Carrera y trail",
    "Cycling & MTB": "Ciclismo y MTB",
    "Kayak & Paddling": "Kayak y remada",
    "Multisport & Expedition": "Multideporte y expedición",
    "Environmental Performance": "Rendimiento ambiental",
    "Fueling & Ultra-Endurance": "Nutrición y ultraresistencia",
    "Training Tolerance & Recovery": "Tolerancia al entrenamiento y recuperación",
    "Supporting Physical Capacities": "Capacidades físicas complementarias",
    "Technical Skills": "Habilidades técnicas",
    "Pacing, Navigation & Cognitive": "Ritmo, navegación y capacidad cognitiva",
    "Body Composition & Relative Performance": "Composición corporal y rendimiento relativo",
}

EXACT_NAMES = {
    "Aerobic Endurance": "Resistencia aeróbica",
    "Aerobic Efficiency / Economy": "Eficiencia y economía aeróbica",
    "Aerobic Threshold Performance": "Rendimiento en el umbral aeróbico",
    "Threshold Performance": "Rendimiento en el umbral",
    "Maximal Aerobic Capacity": "Capacidad aeróbica máxima",
    "Durability / Fatigue Resistance": "Durabilidad y resistencia a la fatiga",
    "Muscular Fatigue Resistance": "Resistencia muscular a la fatiga",
    "Back-to-Back Training Tolerance": "Tolerancia a entrenamientos en días consecutivos",
    "Multi-Day Endurance": "Resistencia durante varios días",
    "Accumulated Load Tolerance": "Tolerancia a la carga acumulada",
    "High-Intensity Aerobic Performance": "Rendimiento aeróbico de alta intensidad",
    "Anaerobic Capacity": "Capacidad anaeróbica",
    "Anaerobic Power": "Potencia anaeróbica",
    "Repeated High-Intensity Ability": "Capacidad para repetir esfuerzos de alta intensidad",
    "Sprint Performance": "Rendimiento en sprint",
    "Maximal Strength": "Fuerza máxima",
    "Relative Strength": "Fuerza relativa al peso",
    "Muscular Endurance": "Resistencia muscular",
    "Explosive Strength": "Fuerza explosiva",
    "Neuromuscular Power": "Potencia neuromuscular",
    "Reactive Strength": "Fuerza reactiva",
    "Neuromuscular Durability": "Durabilidad neuromuscular",
    "Flat Running Performance": "Rendimiento de carrera en plano",
    "Uphill Running Performance": "Rendimiento de carrera en subida",
    "Uphill Hiking Performance": "Rendimiento caminando en subida",
    "Vertical Climbing Performance": "Rendimiento en ascenso vertical",
    "Downhill Running Performance": "Rendimiento de carrera en bajada",
    "Downhill Durability": "Durabilidad en bajada",
    "Technical Terrain Performance": "Rendimiento en terreno técnico",
    "Cycling Endurance": "Resistencia en ciclismo",
    "Cycling Efficiency": "Eficiencia en ciclismo",
    "Cycling Climbing Performance": "Rendimiento en subida en ciclismo",
    "MTB Climbing Performance": "Rendimiento en subida en MTB",
    "MTB Technical Performance": "Rendimiento técnico en MTB",
    "Kayak Endurance": "Resistencia en kayak",
    "Kayak Efficiency": "Eficiencia en kayak",
    "Kayak-Specific Muscular Endurance": "Resistencia muscular específica de kayak",
    "Kayak Technical Efficiency": "Eficiencia técnica en kayak",
    "Cross-Discipline Endurance": "Resistencia entre disciplinas",
    "Cross-Discipline Fatigue Resistance": "Resistencia a la fatiga entre disciplinas",
    "Discipline Transition Performance": "Rendimiento en transiciones entre disciplinas",
    "Discipline Balance": "Equilibrio entre disciplinas",
    "Expedition Endurance": "Resistencia en expediciones",
    "Recovery Between Days": "Recuperación entre días",
    "Heat Tolerance": "Tolerancia al calor",
    "Cold-Environment Performance": "Rendimiento en ambientes fríos",
    "Altitude Tolerance": "Tolerancia a la altitud",
    "Night Performance": "Rendimiento nocturno",
    "Fueling Tolerance During Exercise": "Tolerancia a la alimentación durante el ejercicio",
    "Gastrointestinal Tolerance": "Tolerancia gastrointestinal",
    "Hydration Management": "Gestión de la hidratación",
    "Training Load Tolerance": "Tolerancia a la carga de entrenamiento",
    "Recovery Capacity": "Capacidad de recuperación",
    "Chronic Training Tolerance": "Tolerancia crónica al entrenamiento",
    "Mobility": "Movilidad",
    "Flexibility": "Flexibilidad",
    "Postural Endurance": "Resistencia postural",
    "Trunk Endurance": "Resistencia del tronco",
    "Movement Stability": "Estabilidad del movimiento",
    "Running Technique": "Técnica de carrera",
    "Trail Technique": "Técnica de trail",
    "Downhill Technique": "Técnica de bajada",
    "MTB Technical Skill": "Habilidad técnica en MTB",
    "Kayak Technique": "Técnica de kayak",
    "Transition Efficiency": "Eficiencia en transiciones",
    "Pacing Ability": "Capacidad para dosificar el esfuerzo",
    "Effort Regulation": "Regulación del esfuerzo",
    "Navigation Performance": "Rendimiento en navegación",
    "Navigation Under Fatigue": "Navegación bajo fatiga",
    "Decision-Making Under Fatigue": "Toma de decisiones bajo fatiga",
    "Power-to-Mass / Performance-to-Mass": "Potencia y rendimiento relativos al peso",
    "Body Composition Optimization": "Optimización de la composición corporal",
    "Cardiac drift": "Deriva cardiaca",
    "Aerobic decoupling": "Desacople aeróbico",
    "MTB climbing W/kg": "Potencia relativa al peso en subida de MTB",
    "Cycling W/kg": "Potencia relativa al peso en ciclismo",
    "Climbing W/kg": "Potencia relativa al peso en subida",
}


PHRASES = (
    ("performance-to-mass", "rendimiento relativo al peso"),
    ("power-to-mass", "potencia relativa al peso"),
    ("early-to-late", "entre inicio y final"),
    ("high-intensity", "alta intensidad"),
    ("cross-discipline", "entre disciplinas"),
    ("back-to-back", "en días consecutivos"),
    ("heart-rate", "frecuencia cardiaca"),
    ("heart rate", "frecuencia cardiaca"),
    ("fatigue resistance", "resistencia a la fatiga"),
    ("training load", "carga de entrenamiento"),
    ("maximal aerobic", "aeróbica máxima"),
    ("aerobic threshold", "umbral aeróbico"),
    ("muscular endurance", "resistencia muscular"),
    ("muscular fatigue", "fatiga muscular"),
    ("relative strength", "fuerza relativa"),
    ("maximal strength", "fuerza máxima"),
    ("explosive strength", "fuerza explosiva"),
    ("reactive strength", "fuerza reactiva"),
    ("neuromuscular", "neuromuscular"),
    ("running economy", "economía de carrera"),
    ("cycling efficiency", "eficiencia de ciclismo"),
    ("technical terrain", "terreno técnico"),
    ("technical segment", "segmento técnico"),
    ("standardized segment", "segmento estandarizado"),
    ("standardized course", "recorrido estandarizado"),
    ("standardized", "estandarizado"),
    ("submaximal", "submáximo"),
    ("multi-day", "de varios días"),
    ("long-session", "de sesión larga"),
    ("long-ride", "de salida larga"),
    ("time to exhaustion", "tiempo hasta el agotamiento"),
    ("rate of force development", "tasa de desarrollo de fuerza"),
    ("ground contact time", "tiempo de contacto con el suelo"),
    ("vertical gain", "desnivel positivo"),
    ("body composition", "composición corporal"),
    ("body mass", "masa corporal"),
    ("fat-free mass", "masa libre de grasa"),
    ("fat mass", "masa grasa"),
    ("body-fat percentage", "porcentaje de grasa corporal"),
    ("lean-mass percentage", "porcentaje de masa magra"),
    ("skeletal muscle mass", "masa muscular esquelética"),
    ("stroke rate", "frecuencia de palada"),
    ("distance per stroke", "distancia por palada"),
    ("resting heart rate", "frecuencia cardiaca en reposo"),
    ("sleep duration", "duración del sueño"),
    ("sleep quality", "calidad del sueño"),
    ("subjective fatigue", "fatiga subjetiva"),
    ("subjective recovery", "recuperación subjetiva"),
    ("recovery capacity", "capacidad de recuperación"),
    ("recovery time", "tiempo de recuperación"),
    ("training tolerance", "tolerancia al entrenamiento"),
    ("pacing ability", "capacidad de dosificar el esfuerzo"),
    ("effort regulation", "regulación del esfuerzo"),
    ("decision-making", "toma de decisiones"),
    ("navigation", "navegación"),
    ("transition", "transición"),
    ("aerobic", "aeróbica"),
    ("anaerobic", "anaeróbica"),
    ("endurance", "resistencia"),
    ("durability", "durabilidad"),
    ("efficiency", "eficiencia"),
    ("economy", "economía"),
    ("threshold", "umbral"),
    ("performance", "rendimiento"),
    ("retention", "retención"),
    ("degradation", "deterioro"),
    ("decrement", "disminución"),
    ("tolerance", "tolerancia"),
    ("capacity", "capacidad"),
    ("power", "potencia"),
    ("speed", "velocidad"),
    ("pace", "ritmo"),
    ("distance", "distancia"),
    ("duration", "duración"),
    ("time", "tiempo"),
    ("work", "trabajo"),
    ("force", "fuerza"),
    ("strength", "fuerza"),
    ("climbing", "en subida"),
    ("uphill", "en subida"),
    ("downhill", "en bajada"),
    ("hiking", "caminata"),
    ("running", "carrera"),
    ("cycling", "ciclismo"),
    ("paddling", "remada"),
    ("kayak", "kayak"),
    ("technical", "técnico"),
    ("technique", "técnica"),
    ("recovery", "recuperación"),
    ("completion", "cumplimiento"),
    ("consistency", "consistencia"),
    ("variability", "variabilidad"),
    ("slope", "pendiente"),
    ("trend", "tendencia"),
    ("relative", "relativa"),
    ("maximal", "máxima"),
    ("peak", "máxima"),
    ("mean", "media"),
)


def translate_name(name: str) -> str:
    """Translate catalog names compositionally, with common grammar fixes."""
    if name in EXACT_NAMES:
        return EXACT_NAMES[name]
    patterns = (
        (
            r"^(.+) at standardized heart rate$",
            lambda m: f"{translate_name(m.group(1))} a frecuencia cardiaca estandarizada",
        ),
        (
            r"^Heart rate at standardized (.+)$",
            lambda m: f"Frecuencia cardiaca a {translate_name(m.group(1)).lower()} estandarizado",
        ),
        (
            r"^Early-to-late (.+) degradation$",
            lambda m: f"Deterioro de {translate_name(m.group(1)).lower()} entre inicio y final",
        ),
        (
            r"^(.+) after accumulated (.+)$",
            lambda m: (
                f"{translate_name(m.group(1))} después de acumular "
                f"{translate_name(m.group(2)).lower()}"
            ),
        ),
        (
            r"^(.+) versus (.+)$",
            lambda m: (
                f"Relación entre {translate_name(m.group(1)).lower()} y "
                f"{translate_name(m.group(2)).lower()}"
            ),
        ),
    )
    for pattern, replacement in patterns:
        match = re.match(pattern, name, flags=re.IGNORECASE)
        if match:
            return replacement(match)

    translated = name
    for source, target in PHRASES:
        translated = re.sub(
            re.escape(source),
            target,
            translated,
            flags=re.IGNORECASE,
        )
    translated = re.sub(r"\s+", " ", translated).strip()
    return translated[:1].upper() + translated[1:] if translated else translated


CALCULATION_ES = {
    "Split the steady session into comparable early and late halves; calculate 100 × (late output/HR ratio − early output/HR ratio) ÷ early output/HR ratio. Report the absolute deterioration.": (
        "Divide la parte estable de la sesión en dos mitades comparables, inicial y final. "
        "Calcula 100 × (relación rendimiento/FC final − relación rendimiento/FC inicial) "
        "÷ relación rendimiento/FC inicial. Informa el deterioro absoluto."
    ),
    "During a steady-output period, compare mean heart rate in the late and early portions: 100 × (late HR − early HR) ÷ early HR.": (
        "Durante un período de rendimiento estable, compara la frecuencia cardiaca media "
        "de la parte final con la inicial: 100 × (FC final − FC inicial) ÷ FC inicial."
    ),
    "Average the named external-output variable while heart rate is inside a predefined personal target band; keep protocol and conditions comparable between observations.": (
        "Calcula el promedio de la variable de rendimiento indicada mientras la frecuencia "
        "cardiaca esté dentro de una banda objetivo personal predefinida. Mantén comparables "
        "el protocolo y las condiciones entre observaciones."
    ),
    "Average heart rate while the named external-output variable is inside a predefined target band under a repeatable protocol.": (
        "Calcula la frecuencia cardiaca media mientras la variable de rendimiento indicada "
        "esté dentro de una banda objetivo predefinida, usando un protocolo repetible."
    ),
    "Measure the same variable in comparable early and late portions; calculate 100 × (late − early) ÷ early. Preserve the signed value and report deterioration according to the variable direction.": (
        "Mide la misma variable en partes inicial y final comparables. Calcula "
        "100 × (valor final − valor inicial) ÷ valor inicial. Conserva el signo e interpreta "
        "el deterioro según la dirección de mejora de la variable."
    ),
    "Calculate 100 × post-fatigue (or later) performance ÷ baseline (or early) performance using the same test or segment.": (
        "Calcula 100 × rendimiento posterior a la fatiga (o final) ÷ rendimiento basal "
        "(o inicial), utilizando la misma prueba o segmento."
    ),
    "Calculate 100 × completed amount ÷ planned amount using matching units and the defined evaluation window.": (
        "Calcula 100 × cantidad completada ÷ cantidad planificada, utilizando las mismas "
        "unidades y la ventana de análisis definida."
    ),
    "For each discipline, calculate 100 × discipline total ÷ total across all disciplines over the selected time window.": (
        "Para cada disciplina, calcula 100 × total de la disciplina ÷ total de todas las "
        "disciplinas durante la ventana seleccionada."
    ),
    "Calculate dispersion across comparable samples, normally coefficient of variation = 100 × standard deviation ÷ mean.": (
        "Calcula la dispersión entre muestras comparables, normalmente mediante el coeficiente "
        "de variación: 100 × desviación estándar ÷ media."
    ),
    "Fit a time-ordered or exposure-response trend across comparable observations and report the regression slope plus sample count.": (
        "Ajusta una tendencia temporal o de exposición-respuesta entre observaciones comparables "
        "e informa la pendiente de regresión y el número de muestras."
    ),
    "Divide the numerator measurement by the denominator measurement using values obtained at the same assessment.": (
        "Divide la medición del numerador por la del denominador utilizando valores obtenidos "
        "en la misma evaluación."
    ),
    "Measure elapsed time from the reference effort until the variable returns within the predefined tolerance around personal baseline.": (
        "Mide el tiempo transcurrido desde el esfuerzo de referencia hasta que la variable "
        "regrese al margen de tolerancia predefinido alrededor del valor basal personal."
    ),
    "Repeat the defined test, course or segment under a fixed protocol and calculate the named variable from its complete valid interval.": (
        "Repite la prueba, recorrido o segmento definido mediante un protocolo fijo y calcula "
        "la variable indicada usando todo el intervalo válido."
    ),
    "Sum record-to-record elapsed time for samples classified inside the specified intensity domain or target range.": (
        "Suma el tiempo transcurrido entre registros para las muestras clasificadas dentro "
        "del dominio de intensidad o rango objetivo especificado."
    ),
    "Sum distance increments for records classified inside the specified intensity domain or target range.": (
        "Suma los incrementos de distancia de los registros clasificados dentro del dominio "
        "de intensidad o rango objetivo especificado."
    ),
    "Extract or calculate the named variable over the explicitly defined activity, interval, test or rolling window; use the same protocol for longitudinal comparison.": (
        "Extrae o calcula la variable indicada sobre la actividad, intervalo, prueba o ventana "
        "móvil definida explícitamente. Utiliza el mismo protocolo para comparar su evolución."
    ),
}


def translate_calculation(calculation: str) -> str:
    """Return the curated Spanish calculation text for catalog formulas."""
    return CALCULATION_ES.get(calculation, calculation)


REQUIRED_DATA_ES = {
    "timestamps": "marcas de tiempo",
    "GPS track": "recorrido GPS con distancia acumulada",
    "heart-rate records": "registros de frecuencia cardiaca",
    "power records": "registros de potencia",
    "body mass": "masa corporal",
    "standardized manual or activity observation": "observación manual o de actividad estandarizada",
}


def translate_required_data(value: str) -> str:
    return REQUIRED_DATA_ES.get(value, translate_name(value).lower())


def explain_metric_calculation(metric: dict) -> str:
    """Build a concrete Spanish calculation explanation using metric context."""
    name = metric.get("name", "")
    value = name.casefold()
    name_es = metric.get("name_es") or translate_name(name)
    required = [
        translate_required_data(item)
        for item in metric.get("required_data", [])
    ]
    if metric.get("calculation_es"):
        data_text = (
            ", ".join(required)
            if required
            else "los datos definidos por el protocolo"
        )
        return (
            f"**Qué calcula:** {name_es}.  \n"
            f"**Datos utilizados:** {data_text}.  \n"
            f"**Procedimiento y fórmula:** {metric['calculation_es']}  \n"
            f"**Resultado:** {metric.get('unit', 'la unidad definida')}."
        )

    if " at standardized heart rate" in value:
        variable = name[: value.index(" at standardized heart rate")]
        variable_es = translate_name(variable).lower()
        if "speed" in variable.casefold():
            operation = (
                "1. Define una banda personal de FC, por ejemplo 135–145 bpm.  \n"
                "2. Conserva solamente los tramos válidos cuyos registros de FC estén "
                "dentro de esa banda.  \n"
                "3. Suma la distancia recorrida en esos tramos y el tiempo transcurrido "
                "en ellos.  \n"
                "4. Calcula: **velocidad = distancia válida (km) ÷ tiempo válido (h)**."
            )
        elif "pace" in variable.casefold():
            operation = (
                "Filtra los tramos cuya FC esté dentro de la banda personal definida y "
                "calcula: **ritmo = tiempo válido (min) ÷ distancia válida (km)**."
            )
        else:
            operation = (
                f"Filtra los registros cuya FC esté dentro de la banda personal definida "
                f"y calcula la media ponderada por tiempo de {variable_es}: "
                f"**Σ({variable_es} × duración del registro) ÷ Σ(duración)**."
            )
        return (
            f"**Qué calcula:** {name_es}, únicamente durante un esfuerzo estable.  \n"
            f"**Datos utilizados:** {', '.join(required)}.  \n"
            f"**Procedimiento:**  \n{operation}  \n"
            f"**Resultado:** se expresa en {metric.get('unit', 'la unidad definida')}. "
            "La banda de FC debe guardarse junto al resultado y mantenerse igual para "
            "comparar la progresión."
        )

    data_text = ", ".join(required) if required else "los datos definidos por el protocolo"
    unit = metric.get("unit", "la unidad definida")

    if "aerobic decoupling" in value:
        operation = (
            "Selecciona el tramo continuo y estable, divídelo en dos mitades de igual "
            "duración y calcula en cada mitad rendimiento/FC. Luego aplica: "
            "**desacople (%) = 100 × [(rendimiento/FC final) − "
            "(rendimiento/FC inicial)] ÷ (rendimiento/FC inicial)**. "
            "Se informa el valor absoluto del deterioro."
        )
    elif "cardiac drift" in value:
        operation = (
            "Selecciona un tramo con velocidad o potencia estable y divídelo en dos partes "
            "comparables. Calcula la FC media ponderada por tiempo en cada parte y aplica: "
            "**deriva (%) = 100 × (FC final − FC inicial) ÷ FC inicial**."
        )
    elif value.startswith("heart rate at standardized"):
        operation = (
            "Define una banda objetivo para la variable externa indicada (velocidad, potencia "
            "o ritmo), conserva solamente los registros dentro de esa banda y calcula: "
            "**FC media = Σ(FC × duración del registro) ÷ Σ(duración)**."
        )
    elif "early-to-late" in value or " degradation" in value:
        operation = (
            "Divide el esfuerzo válido en partes inicial y final comparables. Calcula la "
            "métrica en cada parte y aplica: **cambio (%) = 100 × "
            "(valor final − valor inicial) ÷ valor inicial**. Conserva el signo; la dirección "
            "de mejora de la métrica determina si el cambio representa deterioro."
        )
    elif " retention" in value:
        operation = (
            "Mide el rendimiento basal y repite la misma medición después de la fatiga. "
            "Calcula: **retención (%) = 100 × rendimiento posterior ÷ rendimiento basal**."
        )
    elif "completion" in value or "adherence" in value:
        operation = (
            "Suma la cantidad realizada y la planificada usando la misma unidad. Calcula: "
            "**cumplimiento (%) = 100 × cantidad realizada ÷ cantidad planificada**."
        )
    elif " share by discipline" in value:
        operation = (
            "Suma el valor de la variable para cada disciplina y para el conjunto completo. "
            "Para cada disciplina calcula: **participación (%) = 100 × total de la disciplina "
            "÷ total de todas las disciplinas**."
        )
    elif "variability" in value or "consistency" in value:
        operation = (
            "Reúne observaciones comparables, calcula su media y desviación estándar y aplica: "
            "**coeficiente de variación (%) = 100 × desviación estándar ÷ media**. "
            "También se guarda el número de observaciones."
        )
    elif " slope" in value or " trend" in value or " versus " in value:
        operation = (
            "Ordena observaciones comparables por tiempo o por la variable de exposición y "
            "ajusta una regresión lineal **y = a + b·x**. El resultado principal es la "
            "pendiente **b = Σ[(x−x̄)(y−ȳ)] ÷ Σ[(x−x̄)²]**, junto al número de muestras."
        )
    elif "/" in name or " relative to " in value or "-to-mass" in value:
        operation = (
            "Obtén numerador y denominador en la misma evaluación y calcula: "
            "**resultado = numerador ÷ denominador**. No combines datos de fechas diferentes."
        )
    elif "return to baseline" in value or "recovery time" in value:
        operation = (
            "Define el valor basal y su margen de tolerancia antes del esfuerzo. Después, "
            "identifica la primera medición que vuelve a ese margen y calcula: "
            "**tiempo de recuperación = hora de retorno − hora de finalización del esfuerzo**."
        )
    elif "standardized" in value or "test" in value or "segment" in value:
        operation = (
            "Delimita el intervalo completo de la prueba o segmento y descarta registros "
            "inválidos o pausas ajenas al protocolo. Calcula la variable indicada usando "
            "todo ese intervalo y repite siempre el mismo recorrido, duración y condiciones."
        )
    elif "time in" in value:
        operation = (
            "Clasifica cada intervalo entre registros dentro o fuera del rango indicado y "
            "calcula: **tiempo total = Σ duración de los intervalos dentro del rango**."
        )
    elif "distance in" in value:
        operation = (
            "Clasifica cada tramo entre registros dentro o fuera del rango indicado y calcula: "
            "**distancia total = Σ incrementos de distancia dentro del rango**."
        )
    elif unit == "km/h":
        operation = (
            "En el intervalo válido calcula: **velocidad media = distancia total (km) ÷ "
            "tiempo en movimiento (h)**. Se excluyen pausas y puntos GPS inválidos."
        )
    elif unit == "min/km":
        operation = (
            "En el intervalo válido calcula: **ritmo medio = tiempo en movimiento (min) ÷ "
            "distancia total (km)**. Se excluyen pausas y puntos GPS inválidos."
        )
    elif unit in ("bpm", "W", "m/h", "steps/min or rpm", "strokes/min"):
        operation = (
            "Conserva los registros válidos del intervalo y calcula la media ponderada por "
            "duración: **media = Σ(valor × duración del registro) ÷ Σ(duración)**."
        )
    elif unit == "%":
        operation = (
            "Identifica el valor parcial y el total definidos por la métrica y calcula: "
            "**porcentaje = 100 × valor parcial ÷ valor total**. Ambos valores deben usar "
            "el mismo protocolo y unidad."
        )
    elif unit == "time":
        operation = (
            "Delimita el inicio y final válidos del esfuerzo y calcula: "
            "**duración = hora final − hora inicial − pausas excluidas**."
        )
    else:
        operation = (
            "Usa únicamente el intervalo o evaluación definidos por el protocolo. Extrae la "
            "variable si viene medida por el dispositivo; si requiere datos manuales, valida "
            "que pertenezcan a la misma fecha y actividad. El catálogo debe definir la fórmula "
            "específica antes de habilitar un cálculo automático."
        )

    return (
        f"**Qué calcula:** {name_es}.  \n"
        f"**Datos utilizados:** {data_text}.  \n"
        f"**Operación:** {operation}  \n"
        f"**Resultado:** {unit}."
    )


def goal_description_es(name_es: str, category_es: str, disciplines) -> str:
    scope = ", ".join(disciplines) if disciplines else "las disciplinas aplicables"
    return (
        f"Capacidad relacionada con {name_es.lower()}. Sirve para orientar y "
        f"evaluar el desarrollo dentro de «{category_es}» en {scope}. "
        "Su evolución se controla mediante las métricas que se le asignen."
    )
