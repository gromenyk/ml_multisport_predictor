"""Build the reusable metric catalog from the supplied metric list.

Repeated metric names are deduplicated and linked to every applicable
development goal. Calculation, data and equipment fields are generated from
auditable rules and remain editable in the UI.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import re
import unicodedata

from catalog_translations import CATEGORY_ES, translate_calculation, translate_name

def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "_", value).strip("_")


def parse_assignments(text: str):
    section = None
    goal = None
    assignments = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if raw == raw.upper() and not stripped.startswith("-"):
            section = stripped
        elif not raw.startswith((" ", "\t", "-")):
            goal = stripped
        elif stripped.startswith("- "):
            if not section or not goal:
                raise ValueError(f"Metric outside section/goal: {stripped}")
            assignments.append((section, goal, stripped[2:].strip()))
    return assignments


def infer_unit(name: str) -> str:
    value = name.casefold()
    if " at standardized heart rate" in value:
        return infer_unit(name[: value.index(" at standardized heart rate")])
    if value.startswith("heart rate at standardized"):
        return "bpm"
    rules = (
        (("body-fat percentage", "lean-mass percentage", "adherence",
          "completion rate", "retention", "degradation", "share",
          "mass change", "decoupling", "cardiac drift"), "%"),
        (("w/kg", "relative power"), "W/kg"),
        (("heart rate", "resting heart-rate"), "bpm"),
        (("hrv",), "ms"),
        (("vam", "vertical gain per hour"), "m/h"),
        (("pace",), "min/km"),
        (("speed",), "km/h"),
        (("power",), "W"),
        (("distance per stroke",), "m/stroke"),
        (("stroke rate",), "strokes/min"),
        (("cadence",), "steps/min or rpm"),
        (("distance", "vertical gain"), "km or m"),
        (("time", "duration", "ground contact", "flight time"), "time"),
        (("vo2max", "vo2 max"), "ml/kg/min"),
        (("temperature",), "°C"),
        (("spo2",), "%"),
        (("body mass", "fat mass", "fat-free mass",
          "skeletal muscle mass", "lean mass"), "kg"),
        (("fluid intake rate",), "ml/h"),
        (("carbohydrate intake rate",), "g/h"),
        (("energy intake rate",), "kcal/h"),
        (("sweat rate",), "L/h"),
        (("range of motion",), "degrees"),
        (("force",), "N"),
        (("torque",), "N·m"),
        (("jump height",), "cm"),
        (("acceleration",), "m/s²"),
        (("count", "error count"), "count"),
        (("score", "index", "ratio", "efficiency", "slope",
          "variability", "consistency", "quality"), "index"),
    )
    for terms, unit in rules:
        if any(term in value for term in terms):
            return unit
    return "protocol-specific"


def infer_direction(name: str) -> str:
    value = name.casefold()
    minimize = (
        "time", "pace", "heart rate at", "degradation", "decrement",
        "error", "physiological cost", "energy cost", "symptom",
        "nausea", "bloating", "discomfort", "fatigue", "recovery time",
        "ground contact", "postural sway", "variability", "overshoot",
    )
    range_terms = (
        "body mass", "fat mass", "fat-free mass", "body-fat",
        "lean-mass", "skeletal muscle", "hydration", "temperature",
        "training-time share", "training-distance share",
        "training-load share", "session-frequency share",
    )
    if any(term in value for term in range_terms):
        return "range"
    if any(term in value for term in minimize):
        return "minimize"
    return "maximize"


def infer_calculation(name: str) -> str:
    value = name.casefold()
    if "aerobic decoupling" in value:
        return (
            "Split the steady session into comparable early and late halves; "
            "calculate 100 × (late output/HR ratio − early output/HR ratio) "
            "÷ early output/HR ratio. Report the absolute deterioration."
        )
    if "cardiac drift" in value:
        return (
            "During a steady-output period, compare mean heart rate in the "
            "late and early portions: 100 × (late HR − early HR) ÷ early HR."
        )
    if " at standardized heart rate" in value:
        return (
            "Average the named external-output variable while heart rate is "
            "inside a predefined personal target band; keep protocol and "
            "conditions comparable between observations."
        )
    if "heart rate at standardized" in value:
        return (
            "Average heart rate while the named external-output variable is "
            "inside a predefined target band under a repeatable protocol."
        )
    if "early-to-late" in value or " degradation" in value:
        return (
            "Measure the same variable in comparable early and late portions; "
            "calculate 100 × (late − early) ÷ early. Preserve the signed value "
            "and report deterioration according to the variable direction."
        )
    if " retention" in value:
        return (
            "Calculate 100 × post-fatigue (or later) performance ÷ baseline "
            "(or early) performance using the same test or segment."
        )
    if "completion" in value or "adherence" in value:
        return (
            "Calculate 100 × completed amount ÷ planned amount using matching "
            "units and the defined evaluation window."
        )
    if " share by discipline" in value:
        return (
            "For each discipline, calculate 100 × discipline total ÷ total "
            "across all disciplines over the selected time window."
        )
    if "variability" in value or "consistency" in value:
        return (
            "Calculate dispersion across comparable samples, normally "
            "coefficient of variation = 100 × standard deviation ÷ mean."
        )
    if " slope" in value or " trend" in value or " versus " in value:
        return (
            "Fit a time-ordered or exposure-response trend across comparable "
            "observations and report the regression slope plus sample count."
        )
    if "/" in name or " relative to " in value or "-to-mass" in value:
        return (
            "Divide the numerator measurement by the denominator measurement "
            "using values obtained at the same assessment."
        )
    if "return to baseline" in value or "recovery time" in value:
        return (
            "Measure elapsed time from the reference effort until the variable "
            "returns within the predefined tolerance around personal baseline."
        )
    if "standardized" in value or "test" in value or "segment" in value:
        return (
            "Repeat the defined test, course or segment under a fixed protocol "
            "and calculate the named variable from its complete valid interval."
        )
    if "time in" in value:
        return (
            "Sum record-to-record elapsed time for samples classified inside "
            "the specified intensity domain or target range."
        )
    if "distance in" in value:
        return (
            "Sum distance increments for records classified inside the "
            "specified intensity domain or target range."
        )
    return (
        "Extract or calculate the named variable over the explicitly defined "
        "activity, interval, test or rolling window; use the same protocol for "
        "longitudinal comparison."
    )


def infer_requirements(name: str):
    value = name.casefold()
    data = set()
    equipment = set()

    if any(term in value for term in (
        "speed", "pace", "distance", "segment", "route", "vam",
        "vertical", "climb", "descent", "elevation", "navigation",
    )):
        data.update(("timestamps", "GPS track"))
        equipment.add("GPS-capable activity device")
    if "heart rate" in value or "cardiac" in value:
        data.add("heart-rate records")
        equipment.add("Heart-rate chest strap recommended")
    if "hrv" in value:
        data.add("HRV observations")
        equipment.add("HRV-compatible heart-rate sensor")
    if "power" in value or "work" in value or "w-prime" in value:
        data.add("power records")
        equipment.add("Sport-compatible power meter or validated power estimate")
    if any(term in value for term in ("vo2", "oxygen cost", "gross efficiency",
                                      "delta efficiency", "energy cost")):
        data.add("respiratory gas measurements")
        equipment.add("Metabolic cart")
    if any(term in value for term in ("lt1", "lt2", "lactate threshold")):
        data.add("stage-test threshold observations")
        equipment.add("Blood-lactate analyzer or respiratory gas system")
    if "body mass" in value or "w/kg" in value or "to-mass" in value:
        data.add("body mass")
        equipment.add("Calibrated body-mass scale")
    if any(term in value for term in (
        "fat mass", "fat-free mass", "body-fat", "lean-mass",
        "skeletal muscle", "body-composition",
    )):
        data.add("body-composition assessment")
        equipment.add("Validated body-composition device")
    if any(term in value for term in (
        "force", "torque", "rate of force", "jump", "reactive strength",
    )):
        data.add("force or jump-test records")
        equipment.add("Force plate, jump mat or validated dynamometer")
    if "range of motion" in value or "mobility" in value or "flexibility" in value:
        data.add("joint-angle assessment")
        equipment.add("Goniometer or validated mobility test setup")
    if "spo2" in value:
        data.add("oxygen-saturation observations")
        equipment.add("Pulse oximeter")
    if "core temperature" in value:
        data.add("core-temperature observations")
        equipment.add("Validated core-temperature sensor")
    if "skin temperature" in value or "peripheral temperature" in value:
        data.add("skin-temperature observations")
        equipment.add("Skin-temperature sensor")
    if "sweat rate" in value:
        data.update(("pre-exercise body mass", "post-exercise body mass",
                     "fluid intake", "urine output"))
        equipment.add("Calibrated body-mass scale")
    if any(term in value for term in (
        "intake", "fueling", "hydration", "carbohydrate", "fluid",
    )):
        data.add("manual nutrition/hydration log")
    if "sleep" in value or "hours awake" in value:
        data.add("sleep/wake observations")
        equipment.add("Sleep-capable wearable optional")
    if any(term in value for term in (
        "subjective", "rpe", "symptom", "nausea", "bloating",
        "discomfort", "thirst", "alertness",
    )):
        data.add("standardized self-report")
    if "stroke" in value and "heart" not in value:
        data.add("stroke-rate or stroke-count records")
        equipment.add("Paddling stroke sensor or compatible wearable")
    if "cadence" in value or "running dynamics" in value or "ground contact" in value:
        data.add("cadence/running-dynamics records")
        equipment.add("Running-dynamics sensor or compatible watch")

    if not data:
        data.add("standardized manual or activity observation")
    if not equipment:
        equipment.add("No special equipment beyond the defined test setup")
    return sorted(data), sorted(equipment)


def infer_extraction_type(name: str, equipment):
    value = name.casefold()
    if any(term in value for term in (
        "subjective", "symptom", "nausea", "bloating", "discomfort",
        "intake", "1rm", "range of motion", "decision", "error count",
    )):
        return "Manual"
    if any(term in value for term in (
        "segment", "climb", "descent", "route", "navigation",
    )):
        return "GPS segment"
    if any(item in " ".join(equipment).casefold() for item in (
        "metabolic cart", "lactate", "force plate", "dynamometer",
        "goniometer",
    )):
        return "Calculated"
    return "Calculated"


WHY_BY_CATEGORY = {
    "Aerobic & Endurance": (
        "Sirve para seguir tu capacidad de sostener esfuerzos prolongados y "
        "comprobar si puedes producir más rendimiento con un coste "
        "fisiológico similar o menor."
    ),
    "Durability & Fatigue Resistance": (
        "Sirve para saber cuánto rendimiento conservas cuando acumulas horas, "
        "distancia, desnivel o carga. Es especialmente relevante en pruebas "
        "largas, donde el rendimiento fresco no cuenta toda la historia."
    ),
    "High Intensity & Anaerobic": (
        "Sirve para evaluar tu capacidad de producir y repetir esfuerzos de "
        "alta intensidad, y para comprobar si ese rendimiento mejora con el "
        "entrenamiento."
    ),
    "Strength & Neuromuscular": (
        "Sirve para seguir la capacidad de producir fuerza o potencia y "
        "detectar si esa capacidad se mantiene bajo fatiga."
    ),
    "Running & Trail": (
        "Sirve para controlar una capacidad específica de carrera o trail y "
        "comparar tu evolución en terrenos y esfuerzos similares."
    ),
    "Cycling & Mtb": (
        "Sirve para controlar una capacidad específica de ciclismo o MTB y "
        "relacionarla con el rendimiento que puedes sostener sobre la bicicleta."
    ),
    "Kayak & Paddling": (
        "Sirve para seguir tu rendimiento de kayak, separando en lo posible "
        "la condición física, la resistencia muscular y la eficiencia técnica."
    ),
    "Multisport & Expedition": (
        "Sirve para evaluar cómo mantienes el rendimiento al acumular varias "
        "disciplinas, transiciones o jornadas consecutivas."
    ),
    "Environmental Performance": (
        "Sirve para conocer cuánto cambia tu rendimiento bajo condiciones "
        "ambientales exigentes y si la adaptación a esas condiciones mejora."
    ),
    "Fueling & Ultra-Endurance": (
        "Sirve para comprobar si tu estrategia de alimentación e hidratación "
        "permite sostener esfuerzos largos sin problemas gastrointestinales "
        "ni una caída innecesaria del rendimiento."
    ),
    "Training Tolerance & Recovery": (
        "Sirve para relacionar la carga que realizas con tu recuperación, "
        "continuidad y respuesta posterior al entrenamiento."
    ),
    "Supporting Physical Capacities": (
        "Sirve para vigilar una capacidad física de apoyo que puede limitar "
        "la técnica, la comodidad o la tolerancia al entrenamiento."
    ),
    "Technical Skills": (
        "Sirve para seguir la calidad y consistencia técnica, especialmente "
        "cuando aumenta la velocidad, la dificultad o la fatiga."
    ),
    "Pacing, Navigation & Cognitive": (
        "Sirve para evaluar cómo regulas el esfuerzo y mantienes decisiones o "
        "navegación eficaces cuando cambian el terreno y la fatiga."
    ),
    "Body Composition & Relative Performance": (
        "Sirve para interpretar el rendimiento en relación con el peso y la "
        "composición corporal, sin asumir que reducir peso siempre sea mejor."
    ),
}


def plain_metric_name(name: str) -> str:
    """Translate common catalog terminology for explanatory prose."""
    exact = {
        "MTB climbing W/kg": "potencia relativa al peso en subida de MTB",
        "Cycling W/kg": "potencia relativa al peso en ciclismo",
        "Climbing W/kg": "potencia relativa al peso en subida",
        "W-prime": "capacidad de trabajo W-prime",
        "1RM": "fuerza máxima de una repetición (1RM)",
        "Estimated 1RM": "fuerza máxima estimada de una repetición",
    }
    if name in exact:
        return exact[name]
    replacements = (
        ("heart rate", "frecuencia cardiaca"),
        ("Heart rate", "frecuencia cardiaca"),
        ("speed", "velocidad"),
        ("Speed", "velocidad"),
        ("pace", "ritmo"),
        ("Pace", "ritmo"),
        ("power", "potencia"),
        ("Power", "potencia"),
        ("duration", "duración"),
        ("Duration", "duración"),
        ("distance", "distancia"),
        ("Distance", "distancia"),
        ("performance", "rendimiento"),
        ("Performance", "rendimiento"),
        ("efficiency", "eficiencia"),
        ("Efficiency", "eficiencia"),
        ("retention", "retención"),
        ("Retention", "retención"),
        ("degradation", "deterioro"),
        ("Degradation", "deterioro"),
        ("climbing", "en subida"),
        ("Climbing", "en subida"),
        ("running", "de carrera"),
        ("Running", "de carrera"),
        ("cycling", "de ciclismo"),
        ("Cycling", "de ciclismo"),
        ("stroke rate", "frecuencia de palada"),
        ("Stroke rate", "frecuencia de palada"),
        ("body mass", "masa corporal"),
        ("Body mass", "masa corporal"),
    )
    translated = name
    for source, target in replacements:
        translated = translated.replace(source, target)
    return translated


def infer_plain_explanation(name: str, categories, direction: str):
    """Return accessible Spanish explanations without replacing the protocol."""
    value = name.casefold()

    if "aerobic decoupling" in value:
        what = (
            "Mide si la relación entre tu rendimiento externo —ritmo, "
            "velocidad o potencia— y tu frecuencia cardiaca empeora durante "
            "una sesión continua. En términos simples, muestra si tu cuerpo "
            "necesita cada vez más esfuerzo cardiaco para mantener el mismo "
            "rendimiento."
        )
        interpretation = (
            "Una desacoplación menor suele indicar mejor resistencia aeróbica "
            "y durabilidad. Compárala solo en sesiones similares: el calor, "
            "la deshidratación, el desnivel y la fatiga pueden aumentarla."
        )
    elif "cardiac drift" in value:
        what = (
            "Mide cuánto aumenta tu frecuencia cardiaca durante un esfuerzo "
            "continuo aunque mantengas aproximadamente el mismo ritmo o potencia."
        )
        interpretation = (
            "Una deriva menor suele ser favorable. Una deriva alta también "
            "puede deberse a calor, deshidratación, estrés o recuperación "
            "insuficiente, no solamente a una peor condición física."
        )
    elif " at standardized heart rate" in value:
        variable = plain_metric_name(name.split(" at standardized", 1)[0])
        what = (
            f"Mide {variable.lower()} cuando trabajas dentro de una misma banda "
            "de frecuencia cardiaca personal. Permite comparar el rendimiento "
            "a un esfuerzo cardiovascular parecido."
        )
        interpretation = (
            "En general, producir más velocidad, potencia o desnivel con la "
            "misma frecuencia cardiaca indica mejora. Compara recorridos, "
            "duraciones y condiciones semejantes."
        )
    elif "heart rate at standardized" in value:
        what = (
            "Mide la frecuencia cardiaca necesaria para sostener una velocidad, "
            "ritmo, potencia u otra producción externa previamente definida."
        )
        interpretation = (
            "Una frecuencia cardiaca menor para el mismo trabajo suele indicar "
            "mejor eficiencia, siempre que no exista fatiga extrema o una "
            "respuesta cardiaca anormalmente deprimida."
        )
    elif "degradation" in value or "decrement" in value:
        what = (
            "Mide cuánto disminuye la variable indicada entre una parte inicial "
            "y otra posterior del esfuerzo, o entre repeticiones comparables."
        )
        interpretation = (
            "Una degradación menor significa que conservas mejor el rendimiento "
            "bajo fatiga. La comparación requiere esfuerzos iniciales y finales "
            "de dificultad equivalente."
        )
    elif "retention" in value:
        what = (
            "Mide qué porcentaje de tu rendimiento inicial o de referencia "
            "puedes conservar después de acumular ejercicio, carga o fatiga."
        )
        interpretation = (
            "Un porcentaje de retención mayor suele ser mejor. Debe compararse "
            "con el mismo protocolo y una exposición previa semejante."
        )
    elif "recovery" in value or "return to baseline" in value:
        what = (
            "Mide cuánto y con qué rapidez una variable de rendimiento, "
            "frecuencia cardiaca, HRV o sensación personal vuelve a su nivel "
            "habitual después del esfuerzo."
        )
        interpretation = (
            "Una recuperación más rápida suele ser favorable, pero debe "
            "interpretarse respecto de tu línea base personal y de la magnitud "
            "del esfuerzo realizado."
        )
    elif "completion" in value or "adherence" in value:
        what = (
            "Mide qué proporción del entrenamiento, estrategia o cantidad "
            "planificada lograste completar realmente."
        )
        interpretation = (
            "Un valor mayor indica mayor cumplimiento. No implica por sí solo "
            "que el plan fuera adecuado: debe analizarse junto con recuperación "
            "y rendimiento."
        )
    elif "variability" in value or "consistency" in value:
        what = (
            "Mide cuánto fluctúa la variable indicada dentro de una sesión, "
            "entre repeticiones o a través de varias observaciones."
        )
        interpretation = (
            "Menor variabilidad suele significar ejecución más estable, aunque "
            "la variabilidad deseable depende del terreno y del objetivo de la "
            "sesión."
        )
    elif "slope" in value or "trend" in value or " versus " in value:
        what = (
            "Mide la dirección y magnitud de la relación entre la variable "
            "indicada y el tiempo, la carga o la exposición acumulada."
        )
        interpretation = (
            "La pendiente muestra si la respuesta mejora, empeora o permanece "
            "estable. Necesita varias observaciones y no demuestra por sí sola "
            "que una variable sea la causa de la otra."
        )
    elif any(term in value for term in ("time", "duration")):
        what = (
            "Mide el tiempo que tardas en completar el esfuerzo definido o "
            "cuánto tiempo puedes mantenerlo bajo el protocolo establecido."
        )
        interpretation = (
            "En una prueba de rendimiento, menos tiempo suele ser mejor; en una "
            "prueba de tolerancia o duración sostenible, más tiempo suele ser "
            "mejor. Usa siempre el mismo protocolo."
        )
    elif "distance" in value:
        what = (
            "Mide la distancia completada dentro de la actividad, intensidad, "
            "duración o condición definida por el protocolo."
        )
        interpretation = (
            "Una distancia mayor suele ser favorable cuando el tiempo y el "
            "esfuerzo son comparables. El terreno y el GPS pueden afectar la "
            "medición."
        )
    elif "pace" in value or "speed" in value:
        what = (
            "Mide la rapidez con la que te desplazas durante el esfuerzo, "
            "segmento o condición definida."
        )
        interpretation = (
            "Mayor velocidad o menor ritmo en min/km suele indicar mejora si "
            "el esfuerzo fisiológico, el terreno y las condiciones son similares."
        )
    elif "power" in value or "work" in value:
        what = (
            "Mide la potencia o el trabajo mecánico producido durante el "
            "intervalo, prueba o actividad definida."
        )
        interpretation = (
            "Más potencia o trabajo suele indicar mejora cuando la duración y "
            "las condiciones son comparables. Comprueba que el medidor y su "
            "calibración sean consistentes."
        )
    elif "heart rate" in value or "hrv" in value:
        what = (
            "Mide una respuesta cardiovascular o autonómica asociada al "
            "entrenamiento y la recuperación."
        )
        interpretation = (
            "Debe compararse con tu línea base y su tendencia, no con valores "
            "universales. Sueño, estrés, enfermedad, temperatura e hidratación "
            "pueden modificarla."
        )
    elif any(term in value for term in (
        "intake", "fueling", "hydration", "gastrointestinal", "nausea",
        "bloating", "thirst",
    )):
        what = (
            "Mide cuánto consumes, cuánto cumples la estrategia o qué tan bien "
            "toleras alimentación e hidratación durante el ejercicio."
        )
        interpretation = (
            "El objetivo es sostener una estrategia suficiente y tolerable, no "
            "simplemente maximizar la ingesta. Interprétala junto con síntomas, "
            "duración, intensidad y temperatura."
        )
    elif any(term in value for term in (
        "body mass", "fat mass", "fat-free mass", "body-fat",
        "lean-mass", "skeletal muscle",
    )):
        what = (
            "Mide un componente del peso o de la composición corporal mediante "
            "el método indicado."
        )
        interpretation = (
            "Interesa la tendencia y su relación con salud, recuperación y "
            "rendimiento. Cambios rápidos pueden reflejar hidratación y los "
            "dispositivos domésticos tienen error considerable."
        )
    elif any(term in value for term in (
        "technique", "technical", "navigation", "decision", "transition",
    )):
        what = (
            "Mide la calidad, rapidez o consistencia con que ejecutas la "
            "habilidad técnica o cognitiva indicada."
        )
        interpretation = (
            "La mejora se observa al cometer menos errores o mantener una "
            "ejecución más rápida y estable, especialmente bajo fatiga y en "
            "condiciones comparables."
        )
    else:
        readable_name = plain_metric_name(name)
        what = (
            f"Mide {readable_name.lower()} mediante un protocolo definido y "
            "repetible, "
            "para poder comparar su evolución a lo largo del tiempo."
        )
        if direction == "minimize":
            interpretation = (
                "Un valor menor suele considerarse favorable. Confirma que las "
                "condiciones y el método de medición sean comparables."
            )
        elif direction == "range":
            interpretation = (
                "No se busca necesariamente maximizar o minimizar el valor, "
                "sino mantenerlo dentro del rango asociado a buena respuesta y "
                "rendimiento."
            )
        else:
            interpretation = (
                "Un valor mayor suele considerarse favorable cuando el "
                "protocolo, la carga previa y las condiciones son comparables."
            )

    why = next(
        (
            WHY_BY_CATEGORY[category]
            for category in categories
            if category in WHY_BY_CATEGORY
        ),
        (
            "Sirve para seguir una dimensión concreta de tu preparación y "
            "relacionarla posteriormente con el entrenamiento realizado."
        ),
    )
    return what, why, interpretation


def build_catalog(assignments, development_goals):
    goals_by_name = {goal["name"]: goal for goal in development_goals}
    grouped = defaultdict(list)
    display_names = {}
    sections = {}
    for section, goal_name, metric_name in assignments:
        if goal_name not in goals_by_name:
            raise ValueError(f"Unknown development goal: {goal_name}")
        # Hyphenation and punctuation variants describe the same metric.
        key = slugify(metric_name)
        grouped[key].append(goals_by_name[goal_name]["id"])
        display_names.setdefault(key, metric_name)
        sections.setdefault(key, set()).add(section.title())

    metrics = []
    used_ids = set()
    for key, goal_ids in grouped.items():
        name = display_names[key]
        metric_id = f"metric_{slugify(name)}"
        if metric_id in used_ids:
            raise ValueError(f"Metric ID collision: {metric_id}")
        used_ids.add(metric_id)
        unique_goal_ids = list(dict.fromkeys(goal_ids))
        related_goals = [
            goal for goal in development_goals if goal["id"] in unique_goal_ids
        ]
        disciplines = [
            discipline
            for discipline in ("Bici", "Corrida", "Trail", "Kayak")
            if any(
                discipline in goal.get("disciplines", [])
                for goal in related_goals
            )
        ]
        requirements, equipment = infer_requirements(name)
        goal_names = ", ".join(goal["name"] for goal in related_goals)
        categories = sorted(sections[key])
        direction = infer_direction(name)
        what, why, interpretation = infer_plain_explanation(
            name, categories, direction
        )
        metrics.append(
            {
                "id": metric_id,
                "catalog_key": slugify(name),
                "name": name,
                "name_es": translate_name(name),
                "description": what,
                "what_it_measures": what,
                "why_it_matters": why,
                "how_to_interpret": interpretation,
                "related_goal_summary": goal_names,
                "categories": categories,
                "categories_es": [
                    CATEGORY_ES.get(category, translate_name(category))
                    for category in categories
                ],
                "disciplines": disciplines,
                "modality": "",
                "unit": infer_unit(name),
                "direction": direction,
                "extraction_type": infer_extraction_type(name, equipment),
                "development_goal_ids": unique_goal_ids,
                "calculation": infer_calculation(name),
                "calculation_es": translate_calculation(infer_calculation(name)),
                "measurement_method": infer_calculation(name),
                "required_data": requirements,
                "special_equipment": equipment,
                "metadata_origin": "rule_enriched_catalog_import",
            }
        )
    return metrics


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("goal_catalog", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    goals = json.loads(
        args.goal_catalog.read_text(encoding="utf-8")
    )["development_goals"]
    assignments = parse_assignments(args.source.read_text(encoding="utf-8"))
    metrics = build_catalog(assignments, goals)
    if args.destination.exists() and not args.replace:
        raise FileExistsError(
            f"{args.destination} exists; pass --replace deliberately."
        )
    temporary = args.destination.with_suffix(args.destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"metrics": metrics}, indent=4, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.destination)
    print(
        f"Imported {len(metrics)} unique metrics from "
        f"{len(assignments)} goal assignments."
    )


if __name__ == "__main__":
    main()
