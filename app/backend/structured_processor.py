"""
structured_processor.py

Enriquece las sesiones estructuradas utilizando el catálogo de
templates.json.

Responsabilidades:
- Detectar si una sesión es estructurada.
- Generar una signature canónica.
- Buscar la signature en templates.json.
- Añadir template_id y objective_class.
"""

from __future__ import annotations
import csv
from pathlib import Path


# ==========================================================
# Paths
# ==========================================================

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_FILE = (
    BASE_DIR /
    "data" /
    "structured_templates_with_objective_class_draft.csv"
)


# ==========================================================
# Build lookup dictionary
# ==========================================================

TEMPLATE_LOOKUP = {}

with open(TEMPLATES_FILE, newline="", encoding="utf-8") as f:

    reader = csv.DictReader(f)

    for row in reader:

        TEMPLATE_LOOKUP[row["signature"]] = {

            "template_id": int(row["template_id"]),

            "objective_class": row["objective_class"],

            "count": int(row["count"]),

            "sport": row["sport"],

            "example_week": row["example_week"]

        }

print("\n========== INTERVAL RUN TEMPLATES ==========\n")

for sig in sorted(TEMPLATE_LOOKUP):
    if sig.startswith("Corrida:") and "INT(" in sig:
        print(sig)

print("\n===========================================\n")


# ==========================================================
# Utility functions
# ==========================================================

def _normalize_zone(zone):
    """
    Normaliza zonas de frecuencia cardíaca.
    """

    if zone is None:
        return None

    zone = str(zone).strip().upper()
    zone = zone.replace("ZONE", "")
    zone = zone.replace(" ", "")

    if zone.startswith("Z"):
        return zone

    return f"Z{zone}"


def _block_type(block):

    if block is None:
        return None

    value = block.get("type")

    if value is None:
        return None

    return str(value).strip().lower()


def _format_measure(duration=None,
                    distance=None,
                    max_duration=None):
    """
    Convierte duración/distancia a la representación
    utilizada por el catálogo.
    """

    if duration is not None:

        value = f"{duration:g}min"

        if max_duration is not None:
            value += f", max={max_duration:g}min"

        return value

    if distance is not None:
        return f"{distance:g}km"

    return None


def _format_segment(segment):
    """
    Convierte un segmento de trabajo/recuperación
    a la forma:

        Z4@10min
        Z5@1km
    """

    zone = _normalize_zone(segment.get("zone"))

    measure = _format_measure(
        duration=segment.get("duration_min"),
        distance=segment.get("distance_km"),
        max_duration=segment.get("max_duration_min")
    )

    if measure is None:
        return zone

    return f"{zone}@{measure}"


# ==========================================================
# Structured detection
# ==========================================================

def is_structured_session(session):
    """
    Una sesión es estructurada cuando posee
    más de un bloque.
    """

    blocks = session.get("blocks", [])

    return len(blocks) > 1

# ==========================================================
# Signature builders
# ==========================================================

def _signature_warmup(block):

    zone = _normalize_zone(block.get("zone"))

    measure = _format_measure(
        duration=block.get("duration_min"),
        distance=block.get("distance_km"),
        max_duration=block.get("max_duration_min")
    )

    if measure:
        return f"WU({zone}, {measure})"

    return f"WU({zone})"


def _signature_steady(block):

    zone = _normalize_zone(block.get("zone"))

    measure = _format_measure(
        duration=block.get("duration_min"),
        distance=block.get("distance_km"),
        max_duration=block.get("max_duration_min")
    )

    if measure:
        return f"ST({zone}, {measure})"

    return f"ST({zone})"


def _signature_cooldown(block):

    zone = _normalize_zone(block.get("zone"))

    measure = _format_measure(
        duration=block.get("duration_min"),
        distance=block.get("distance_km"),
        max_duration=block.get("max_duration_min")
    )

    if measure:
        return f"CD({zone}, {measure})"

    return f"CD({zone})"


def _signature_interval(block):

    work = block.get("work", [])
    recovery = block.get("recovery", [])

    print("\nINTERVAL BLOCK")
    print("WORK:", work)
    print("RECOVERY:", recovery)

    reps = block.get("repetitions")

    if reps is None:
        reps_label = "-x"

    elif reps == 1 and len(work) > 1:
        reps_label = "-x"

    else:
        reps_label = f"{reps}x"

    work_str = "|".join(
        _format_segment(x)
        for x in work
    )

    recovery_str = "|".join(
        _format_segment(x)
        for x in recovery
    )

    return (
        f"INT("
        f"{reps_label};"
        f"W[{work_str}];"
        f"R[{recovery_str}]"
        f")"
    )


def _signature_rest(block):
    return "REST"


def _signature_block(block):

    block_type = _block_type(block)

    if block_type == "warmup":
        return _signature_warmup(block)

    if block_type == "steady":
        return _signature_steady(block)

    if block_type == "cooldown":
        return _signature_cooldown(block)

    if block_type == "interval":
        return _signature_interval(block)

    if block_type == "rest":
        return _signature_rest(block)

    # ------------------------------------------------------
    # Fallback
    # ------------------------------------------------------

    zone = _normalize_zone(block.get("zone"))

    measure = _format_measure(
        duration=block.get("duration_min"),
        distance=block.get("distance_km"),
        max_duration=block.get("max_duration_min")
    )

    label = block_type.upper()

    if measure:
        return f"{label}({zone}, {measure})"

    return f"{label}({zone})"

# ==========================================================
# Signature generation
# ==========================================================

def generate_signature(session):
    """
    Genera la signature canónica de una sesión.
    """

    sport = session.get("sport", "").strip()

    blocks = session.get("blocks", [])

    signatures = []

    for block in blocks:
        signatures.append(
            _signature_block(block)
        )

    return f"{sport}: " + " -> ".join(signatures)

# ==========================================================
# Template lookup
# ==========================================================

def lookup_template(signature):
    """
    Busca un template mediante su signature.
    """

    return TEMPLATE_LOOKUP.get(signature)

# ==========================================================
# Session enrichment
# ==========================================================

def enrich_structured_session(session):
    """
    Enriquece una sesión estructurada utilizando
    el catálogo de templates.
    """

    # ------------------------------------------------------
    # Detect structured
    # ------------------------------------------------------

    structured = is_structured_session(session)

    session["structured"] = structured

    if not structured:

        session["template_id"] = None
        session["objective_class"] = None

        return session

    # ------------------------------------------------------
    # Generate signature
    # ------------------------------------------------------

    signature = generate_signature(session)

    print("\nSignature:")
    print(signature)

    # ------------------------------------------------------
    # Lookup template
    # ------------------------------------------------------

    template = lookup_template(signature)

    print("Template:", template)

    if template is None:

        session["template_id"] = None
        session["objective_class"] = None

        return session

    # ------------------------------------------------------
    # Template found
    # ------------------------------------------------------

    session["template_id"] = template.get("template_id")

    session["objective_class"] = template.get(
        "objective_class"
    )

    return session


# ==========================================================
# Day processing
# ==========================================================

def process_day(day):
    """
    Procesa todas las sesiones de un día.
    """

    if not day:
        return day

    processed = []

    for session in day:

        processed.append(
            enrich_structured_session(session)
        )

    return processed


# ==========================================================
# Week processing
# ==========================================================

def process_week(days):
    """
    Procesa todos los días de una semana.
    """

    processed_days = []

    for day in days:

        processed_days.append(
            process_day(day)
        )

    return processed_days

# ==========================================================
# Plan processing
# ==========================================================

def process_structured_sessions(plan):
    """
    Enriquece todas las sesiones del plan.

    Modifica el diccionario recibido y lo devuelve.
    """

    # ------------------------------------------------------
    # Formato con semanas
    # ------------------------------------------------------

    if "weeks" in plan:

        for week in plan["weeks"]:

            if "days" not in week:
                continue

            week["days"] = process_week(
                week["days"]
            )

        return plan

    # ------------------------------------------------------
    # Formato simple
    # ------------------------------------------------------

    if "days" in plan:

        plan["days"] = process_week(
            plan["days"]
        )

        return plan

    # ------------------------------------------------------
    # Formato con sesiones
    # ------------------------------------------------------

    if "sessions" in plan:

        processed = []

        for session in plan["sessions"]:

            processed.append(
                enrich_structured_session(session)
            )

        plan["sessions"] = processed

        return plan

    return plan


# ==========================================================
# Statistics
# ==========================================================

def catalog_statistics():
    """
    Muestra información básica del catálogo.
    """

    print("=" * 60)
    print("Template catalog")
    print("=" * 60)
    print(f"Templates loaded : {len(TEMPLATE_LOOKUP)}")
    print()


# ==========================================================
# Debug
# ==========================================================

def debug_session(session):
    """
    Imprime una sesión enriquecida.
    """

    print("-" * 60)
    print("Sport            :", session.get("sport"))
    print("Structured       :", session.get("structured"))
    print("Template ID      :", session.get("template_id"))
    print("Objective class  :", session.get("objective_class"))
    print("-" * 60)


# ==========================================================
# Main
# ==========================================================

if __name__ == "__main__":

    catalog_statistics()

    example = {

        "sport": "Corrida",

        "blocks": [

            {
                "type": "warmup",
                "zone": "Z1",
                "duration_min": 10
            },

            {
                "type": "steady",
                "zone": "Z2",
                "duration_min": 10
            },

            {
                "type": "steady",
                "zone": "Z4",
                "duration_min": 40
            },

            {
                "type": "steady",
                "zone": "Z2",
                "duration_min": 10
            },

            {
                "type": "cooldown",
                "zone": "Z1",
                "duration_min": 10
            }

        ]
    }

    enrich_structured_session(example)

    debug_session(example)