from pathlib import Path
from collections import Counter

import numpy as np

# ==========================================================
# Configuración
# ==========================================================

PREDICTIONS_FILE = Path(
    "results/pipeline_predictions.txt"
)

SPORTS = [
    "Descanso",
    "Trail",
    "Corrida",
    "Bici",
    "Kayak",
]

SESSION_TYPES = [
    None,
    "Leve",
    "Regenerativo",
    "Avaliação",
    "Longão",
    "Simulado",
    "Contínuo",
    "Acelerações",
    "Ritmado",
    "Rest",
    "Transição",
    "Subidas",
    "Otros",
]

TERRAINS = [
    None,
    "Ondulado",
    "Plano",
    "Montaña",
]

ZONES = [
    None,
    "Z1",
    "Z2",
    "Z3",
]


WEEKDAYS = [
    "seg",
    "ter",
    "qua",
    "qui",
    "sex",
    "sáb",
    "dom",
]

# ==========================================================
# Lectura del archivo
# ==========================================================

def load_predictions(filename):

    real_sports = []
    pred_sports = []

    real_sessions = []
    pred_sessions = []

    current_real_sport = []
    current_pred_sport = []

    current_real_session = []
    current_pred_session = []

    real_terrain = []
    pred_terrain = []

    current_real_terrain = []
    current_pred_terrain = []

    real_zone = []
    pred_zone = []

    current_real_zone = []
    current_pred_zone = []

    real_distance = []
    pred_distance = []

    current_real_distance = []
    current_pred_distance = []

    real_objective = []
    pred_objective = []

    current_real_objective = []
    current_pred_objective = []

    inside_week = False

    with open(
        filename,
        encoding="utf-8",
    ) as f:

        for line in f:

            line = line.rstrip()

            if line.startswith("Week "):

                if len(current_real_sport) == 7:

                    real_sports.append(
                        current_real_sport
                    )

                    pred_sports.append(
                        current_pred_sport
                    )

                    real_sessions.append(
                        current_real_session
                    )

                    pred_sessions.append(
                        current_pred_session
                    )

                    real_objective.append(
                        current_real_objective
                    )

                    pred_objective.append(
                        current_pred_objective
                    )

                    real_terrain.append(
                        current_real_terrain
                    )

                    pred_terrain.append(
                        current_pred_terrain
                    )

                    real_zone.append(
                        current_real_zone
                    )

                    pred_zone.append(
                        current_pred_zone
                    )

                    real_distance.append(
                        current_real_distance
                    )

                    pred_distance.append(
                        current_pred_distance
                    )

                current_real_sport = []
                current_pred_sport = []

                current_real_session = []
                current_pred_session = []

                current_real_terrain = []
                current_pred_terrain = []

                current_real_zone = []
                current_pred_zone = []

                current_real_distance = []
                current_pred_distance = []

                current_real_objective =  []
                current_pred_objective = []

                inside_week = True

                continue

            if not inside_week:
                continue

            if (
                line.startswith("Day")
                or line.startswith("---")
                or line.startswith("=")
                or line == ""
            ):
                continue

            if line.startswith("Sport accuracy"):

                inside_week = False
                continue

            tokens = line.split("\t")

            if len(tokens) < 5:
                continue

            # -------------------------------------------------
            # Día PredSport PredSession RealSport RealSession
            # -------------------------------------------------

            if len(tokens) < 5:
                continue

            pred_sport = tokens[1]
            pred_session = tokens[2]

            # tokens[3] = Struct (no se usa)

            pred_objective_value = tokens[4]

            # tokens[5] = Pred Structured Session (no se usa)

            pred_terrain_value = tokens[6]
            pred_zone_value = tokens[7]
            pred_distance_value = tokens[8]

            real_sport = tokens[9]
            real_session = tokens[10]

            real_objective_value = tokens[11]

            real_terrain_value = tokens[12]
            real_zone_value = tokens[13]
            real_distance_value = tokens[14]

            if pred_session == "None":
                pred_session = None

            if real_session == "None":
                real_session = None

            if pred_objective_value == "-":
                pred_objective_value = None

            if real_objective_value == "-":
                real_objective_value = None

            if pred_terrain_value == "-":
                pred_terrain_value = None

            if real_terrain_value == "-":
                real_terrain_value = None

            if pred_zone_value in ("-", "None"):
                pred_zone_value = None

            if real_zone_value in ("-", "None"):
                real_zone_value = None

            if pred_distance_value == "-":
                pred_distance_value = None
            else:
                pred_distance_value = float(pred_distance_value)

            if real_distance_value == "-":
                real_distance_value = None
            else:
                real_distance_value = float(real_distance_value)

            current_pred_sport.append(
                pred_sport
            )

            current_real_sport.append(
                real_sport
            )

            current_pred_session.append(
                pred_session
            )

            current_real_session.append(
                real_session
            )

            current_pred_objective.append(
                pred_objective_value
            )

            current_real_objective.append(
                real_objective_value
            )

            current_pred_terrain.append(
                pred_terrain_value
            )

            current_real_terrain.append(
                real_terrain_value
            )

            current_pred_zone.append(
                pred_zone_value
            )

            current_real_zone.append(
                real_zone_value
            )

            current_pred_distance.append(
                pred_distance_value
            )

            current_real_distance.append(
                real_distance_value
            )

    if len(current_real_sport) == 7:

        real_sports.append(
            current_real_sport
        )

        pred_sports.append(
            current_pred_sport
        )

        real_sessions.append(
            current_real_session
        )

        pred_sessions.append(
            current_pred_session
        )

        real_terrain.append(
            current_real_terrain
        )

        pred_terrain.append(
            current_pred_terrain
        )

        real_zone.append(
            current_real_zone
        )

        pred_zone.append(
            current_pred_zone
        )

        real_distance.append(
            current_real_distance   
        )

        pred_distance.append(
            current_pred_distance
        )

    return (

        real_sports,
        pred_sports,

        real_sessions,
        pred_sessions,

        real_objective,
        pred_objective,

        real_terrain,
        pred_terrain,

        real_zone,
        pred_zone,

        real_distance,
        pred_distance,

    )

# ==========================================================
# Estadísticas genéricas
# ==========================================================

def distribution(
    weeks,
    categories,
):

    counter = Counter()

    total = 0

    for week in weeks:

        counter.update(week)

        total += len(week)

    stats = {}

    for category in categories:

        stats[category] = (
            counter[category] / total
            if total > 0
            else 0
        )

    return stats


def transition_matrix(
    weeks,
    categories,
):

    counts = {

        c: Counter()

        for c in categories

    }

    totals = Counter()

    for week in weeks:

        for i in range(6):

            a = week[i]
            b = week[i + 1]

            counts[a][b] += 1
            totals[a] += 1

    matrix = {}

    for c1 in categories:

        matrix[c1] = {}

        for c2 in categories:

            if totals[c1] == 0:

                matrix[c1][c2] = 0

            else:

                matrix[c1][c2] = (

                    counts[c1][c2]

                    /

                    totals[c1]

                )

    return matrix


def weekday_distribution(
    weeks,
    categories,
):

    stats = {

        day: Counter()

        for day in WEEKDAYS

    }

    totals = Counter()

    for week in weeks:

        for day, value in zip(
            WEEKDAYS,
            week,
        ):

            stats[day][value] += 1
            totals[day] += 1

    distribution = {}

    for day in WEEKDAYS:

        distribution[day] = {}

        for category in categories:

            if totals[day] == 0:

                distribution[day][category] = 0

            else:

                distribution[day][category] = (

                    stats[day][category]

                    /

                    totals[day]

                )

    return distribution


def repetition_rate(
    weeks,
):

    repetitions = 0
    total = 0

    for week in weeks:

        for i in range(6):

            total += 1

            if week[i] == week[i + 1]:

                repetitions += 1

    if total == 0:
        return 0

    return repetitions / total


# ==========================================================
# Comparaciones
# ==========================================================

def compare_distribution(
    real,
    pred,
    categories,
):

    error = 0

    for c in categories:

        error += abs(
            real[c] -
            pred[c]
        )

    return max(
        0,
        100 * (1 - error / 2)
    )


def compare_transition_matrix(
    real,
    pred,
    categories,
):

    error = 0
    n = 0

    for c1 in categories:

        for c2 in categories:

            error += abs(

                real[c1][c2]

                -

                pred[c1][c2]

            )

            n += 1

    error /= n


    return max(
        0,
        100 * (1 - error)
    )


def compare_weekday_distribution(
    real,
    pred,
    categories,
):

    error = 0
    n = 0

    for day in WEEKDAYS:

        for c in categories:

            error += abs(

                real[day][c]

                -

                pred[day][c]

            )

            n += 1

    error /= n

    return max(
        0,
        100 * (1 - error)
    )


def compare_repetition(
    real,
    pred,
):

    return max(
        0,
        100 * (1 - abs(real - pred))
    )

# ==========================================================
# Firma
# ==========================================================

def build_signature(
    weeks,
    categories,
):

    return {

        "distribution":
            distribution(
                weeks,
                categories,
            ),

        "transitions":
            transition_matrix(
                weeks,
                categories,
            ),

        "weekday":
            weekday_distribution(
                weeks,
                categories,
            ),

        "repetition":
            repetition_rate(
                weeks,
            ),

    }


# ==========================================================
# Evaluación individual
# ==========================================================

def evaluate_signature(
    real_weeks,
    pred_weeks,
    categories,
):

    real = build_signature(
        real_weeks,
        categories,
    )

    pred = build_signature(
        pred_weeks,
        categories,
    )

    scores = {}

    scores["distribution"] = compare_distribution(
        real["distribution"],
        pred["distribution"],
        categories,
    )

    scores["transitions"] = compare_transition_matrix(
        real["transitions"],
        pred["transitions"],
        categories,
    )

    scores["weekday"] = compare_weekday_distribution(
        real["weekday"],
        pred["weekday"],
        categories,
    )

    scores["repetition"] = compare_repetition(
        real["repetition"],
        pred["repetition"],
    )

    scores["overall"] = np.mean([

        scores["distribution"],
        scores["transitions"],
        scores["weekday"],
        scores["repetition"],

    ])

    return scores


# ==========================================================
# Pipeline metrics
# ==========================================================

def consistency_score(
    sports,
    sessions,
):

    total = 0
    valid = 0

    for week_sport, week_session in zip(
        sports,
        sessions,
    ):

        for sport, session in zip(
            week_sport,
            week_session,
        ):

            total += 1

            if sport == "Descanso":

                if session in (None, "Rest"):

                    valid += 1

            else:

                if session not in (None, "Rest"):

                    valid += 1

    return 100 * valid / total


def joint_distribution(
    sports,
    sessions,
):

    counter = Counter()

    total = 0

    for ws, wt in zip(
        sports,
        sessions,
    ):

        for sport, session in zip(
            ws,
            wt,
        ):

            counter[
                (sport, session)
            ] += 1

            total += 1

    distribution = {}

    for key, value in counter.items():

        distribution[key] = value / total

    return distribution


def compare_joint_distribution(
    real,
    pred,
):

    keys = set(real) | set(pred)

    error = 0

    for key in keys:

        error += abs(

            real.get(key, 0)

            -

            pred.get(key, 0)

        )

    return max(
        0,
        100 * (1 - error / 2)
    )


def _week_values(week):
    """Extract the exact signature fields used by the pipeline evaluation."""
    sports, sessions, objectives, terrains, zones, distances = [], [], [], [], [], []
    by_weekday = {item.get("weekday"): item for item in week.get("sessions", [])}
    for weekday in WEEKDAYS:
        session = by_weekday.get(weekday)
        if session is None:
            sports.append("Descanso")
            sessions.append(None)
            objectives.append(None)
            terrains.append(None)
            zones.append(None)
            distances.append(None)
            continue
        sport = session.get("sport") or "Descanso"
        terrain = session.get("terrain")
        blocks = session.get("blocks") or []
        first_block = blocks[0] if blocks and isinstance(blocks[0], dict) else {}
        sports.append(sport)
        sessions.append(session.get("session_type"))
        objectives.append(session.get("objective_class"))
        terrains.append(None if terrain == "Subida" else terrain)
        zones.append(
            first_block.get("zone")
            if terrain != "Subida" and first_block.get("zone") in ZONES
            else None
        )
        distances.append(
            first_block.get("distance_km")
            if len(blocks) == 1 and first_block.get("type") == "steady"
            else None
        )
    return sports, sessions, objectives, terrains, zones, distances


def calculate_style_score(real_weeks, predicted_weeks):
    """Calculate the current pipeline Style Score for arbitrary plan weeks.

    This is deliberately the same signature-based score shown during model
    evaluation.  In planning it expresses adherence to the human-plan corpus,
    not future predictive accuracy.
    """
    real_values = [_week_values(week) for week in real_weeks]
    predicted_values = [_week_values(week) for week in predicted_weeks]
    real_sports, real_sessions, real_objectives, real_terrain, real_zone, real_distance = map(list, zip(*real_values))
    pred_sports, pred_sessions, pred_objectives, pred_terrain, pred_zone, pred_distance = map(list, zip(*predicted_values))

    distance_categories = sorted({
        value for week in (real_distance + pred_distance) for value in week if value is not None
    })
    if any(value is None for week in (real_distance + pred_distance) for value in week):
        distance_categories = [None] + distance_categories
    objective_categories = sorted({
        value for week in (real_objectives + pred_objectives) for value in week if value is not None
    })
    if any(value is None for week in (real_objectives + pred_objectives) for value in week):
        objective_categories = [None] + objective_categories

    sport_scores = evaluate_signature(real_sports, pred_sports, SPORTS)
    session_scores = evaluate_signature(real_sessions, pred_sessions, SESSION_TYPES)
    objective_scores = evaluate_signature(real_objectives, pred_objectives, objective_categories)
    terrain_scores = evaluate_signature(real_terrain, pred_terrain, TERRAINS)
    zone_scores = evaluate_signature(real_zone, pred_zone, ZONES)
    distance_scores = evaluate_signature(real_distance, pred_distance, distance_categories)
    consistency = consistency_score(pred_sports, pred_sessions)
    joint = compare_joint_distribution(
        joint_distribution(real_sports, real_sessions),
        joint_distribution(pred_sports, pred_sessions),
    )
    components = {
        "deporte": sport_scores["overall"],
        "tipo_sesion": session_scores["overall"],
        "objetivo": objective_scores["overall"],
        "terreno": terrain_scores["overall"],
        "zona": zone_scores["overall"],
        "distancia": distance_scores["overall"],
        "consistencia": consistency,
        "combinacion_deporte_sesion": joint,
    }
    return {
        "global_score": round(float(np.mean(list(components.values()))), 1),
        "components": {key: round(float(value), 1) for key, value in components.items()},
        "reference_weeks": len(real_weeks),
    }


# ==========================================================
# Main
# ==========================================================

if __name__ == "__main__":

    (
        real_sports,
        pred_sports,
        real_sessions,
        pred_sessions,
        real_objective,
        pred_objective,
        real_terrain,
        pred_terrain,
        real_zone,
        pred_zone,
        real_distance,
        pred_distance,
    ) = load_predictions(
        PREDICTIONS_FILE
    )

    distance_categories = sorted(
        {
            d
            for week in (real_distance + pred_distance)
            for d in week
            if d is not None
        }
    )

    if any(
        d is None
        for week in (real_distance + pred_distance)
        for d in week
    ):
        distance_categories = [None] + distance_categories


    objective_categories = sorted(
        {
            o
            for week in (real_objective + pred_objective)
            for o in week
            if o is not None
        }
    )

    if any(
        o is None
        for week in (real_objective + pred_objective)
        for o in week
    ):
        objective_categories = [None] + objective_categories

    print(
        f"Loaded {len(real_sports)} weeks."
    )

    sport_scores = evaluate_signature(
        real_sports,
        pred_sports,
        SPORTS,
    )

    session_scores = evaluate_signature(
        real_sessions,
        pred_sessions,
        SESSION_TYPES,
    )

    terrain_scores = evaluate_signature(
        real_terrain,
        pred_terrain,
        TERRAINS,
    )

    objective_scores = evaluate_signature(

        real_objective,

        pred_objective,

        objective_categories,

    )

    zone_scores = evaluate_signature(
        real_zone,
        pred_zone,
        ZONES,
    )

    distance_scores = evaluate_signature(
        real_distance,
        pred_distance,
        distance_categories,
    )

    consistency = consistency_score(
        pred_sports,
        pred_sessions,
    )

    joint = compare_joint_distribution(

        joint_distribution(
            real_sports,
            real_sessions,
        ),

        joint_distribution(
            pred_sports,
            pred_sessions,
        ),

    )

    global_score = np.mean([

        sport_scores["overall"],

        session_scores["overall"],

        objective_scores["overall"],

        terrain_scores["overall"],

        zone_scores["overall"],

        distance_scores["overall"],

        consistency,

        joint,

    ])

    print()
    print("=" * 60)
    print("PIPELINE STYLE SCORE")
    print("=" * 60)

    print("\nSPORT")
    print("-" * 30)

    print(
        f"Distribution     : {sport_scores['distribution']:.1f}"
    )

    print(
        f"Transitions      : {sport_scores['transitions']:.1f}"
    )

    print(
        f"Weekday profile  : {sport_scores['weekday']:.1f}"
    )

    print(
        f"Repetition       : {sport_scores['repetition']:.1f}"
    )

    print(
        f"Overall          : {sport_scores['overall']:.1f}"
    )

    print("\nSESSION TYPE")
    print("-" * 30)

    print(
        f"Distribution     : {session_scores['distribution']:.1f}"
    )

    print(
        f"Transitions      : {session_scores['transitions']:.1f}"
    )

    print(
        f"Weekday profile  : {session_scores['weekday']:.1f}"
    )

    print(
        f"Repetition       : {session_scores['repetition']:.1f}"
    )

    print(
        f"Overall          : {session_scores['overall']:.1f}"
    )

    print("\nOBJECTIVE FAMILY")
    print("-" * 30)

    print(
        f"Distribution     : {objective_scores['distribution']:.1f}"
    )

    print(
        f"Transitions      : {objective_scores['transitions']:.1f}"
    )

    print(
        f"Weekday profile  : {objective_scores['weekday']:.1f}"
    )

    print(
        f"Repetition       : {objective_scores['repetition']:.1f}"
    )

    print(
        f"Overall          : {objective_scores['overall']:.1f}"
    )

    print("\nTERRAIN")
    print("-" * 30)

    print(
        f"Distribution     : {terrain_scores['distribution']:.1f}"
    )

    print(
        f"Transitions      : {terrain_scores['transitions']:.1f}"
    )

    print(
        f"Weekday profile  : {terrain_scores['weekday']:.1f}"
    )

    print(
        f"Repetition       : {terrain_scores['repetition']:.1f}"
    )

    print(
        f"Overall          : {terrain_scores['overall']:.1f}"
    )

    print("\nZONE")
    print("-" * 30)

    print(
        f"Distribution     : {zone_scores['distribution']:.1f}"
    )

    print(
        f"Transitions      : {zone_scores['transitions']:.1f}"
    )

    print(
        f"Weekday profile  : {zone_scores['weekday']:.1f}"
    )

    print(
        f"Repetition       : {zone_scores['repetition']:.1f}"
    )

    print(
        f"Overall          : {zone_scores['overall']:.1f}"
    )

    print("\nDISTANCE")
    print("-" * 30)

    print(
        f"Distribution     : {distance_scores['distribution']:.1f}"
    )

    print(
        f"Transitions      : {distance_scores['transitions']:.1f}"
    )

    print(
        f"Weekday profile  : {distance_scores['weekday']:.1f}"
    )

    print(
        f"Repetition       : {distance_scores['repetition']:.1f}"
    )

    print(
        f"Overall          : {distance_scores['overall']:.1f}"
    )

    print("\nPIPELINE")
    print("-" * 30)

    print(
        f"Sport-Session consistency : {consistency:.1f}"
    )

    print(
        f"Joint distribution        : {joint:.1f}"
    )

    print()
    print("=" * 60)

    print(
        f"GLOBAL STYLE SCORE : {global_score:.1f}%"
    )

    print("=" * 60)
