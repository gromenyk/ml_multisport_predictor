"""
==========================================================
normalizer.py

Normaliza el JSON extraído por OpenAI al formato canónico
utilizado por el pipeline de entrenamiento.

El objetivo es que cualquier planilla nueva termine con
exactamente la misma representación que los JSON históricos.

Uso:

    from backend.normalizer import normalize_json

    week = normalize_json(week)

==========================================================
"""

from copy import deepcopy
from datetime import datetime, timedelta
from collections import defaultdict

# ==========================================================
# Constantes
# ==========================================================

WEEKDAYS = [
    "seg",
    "ter",
    "qua",
    "qui",
    "sex",
    "sáb",
    "dom",
]

WEEKDAY_ORDER = {
    "seg": 0,
    "ter": 1,
    "qua": 2,
    "qui": 3,
    "sex": 4,
    "sáb": 5,
    "dom": 6,
}

# ==========================================================
# SPORT MAPPING
# ==========================================================

SPORT_MAPPING = {

    # -----------------------------
    # Descanso
    # -----------------------------

    None: "Descanso",
    "": "Descanso",
    "Descanso": "Descanso",

    # -----------------------------
    # Trail
    # -----------------------------

    "Corrida em Trilhas": "Trail",
    "Trekking": "Trail",
    "Caminhada": "Trail",

    # -----------------------------
    # Running
    # -----------------------------

    "Corrida": "Corrida",

    # -----------------------------
    # Bike
    # -----------------------------

    "Mountain Biking": "Bici",
    "Ciclismo": "Bici",
    "Ciclismo ou Mountain Biking": "Bici",
    "Ciclismo/Mountain Biking": "Bici",
    "Ciclismo / Mountain Biking": "Bici",

    # -----------------------------
    # Kayak
    # -----------------------------

    "Canoagem": "Kayak",

    # -----------------------------
    # Deportes ignorados
    # (no deberían aparecer en el
    # dataset final)
    # -----------------------------

    "Treino Funcional": None,
    "Corrida de Aventura": None,
    "Corrida de Orientação": None,
    "Ciclismo / Canoagem": None,
    "Natação": None,
    "Natação / Canoagem": None,
    "Ciclismo ou Mountain Biking; Corrida em Trilhas": None,
}

# ==========================================================
# TERRAIN MAPPING
# ==========================================================

TERRAIN_MAPPING = {

    # -----------------------------
    # Sin información
    # -----------------------------

    None: None,
    "": None,

    # -----------------------------
    # Plano
    # -----------------------------

    "Percurso com a mínima variação de altimetria.": "Plano",
    "Percurso com a mínima variação de altimetria": "Plano",
    "Percurso plano.": "Plano",
    "Percurso plano": "Plano",
    "plano": "Plano",
    "plano.": "Plano",
    "Plano": "Plano",
    "ESTRADA": "Plano",
    "Percurso with a mínima variação de altimetria.": "Plano",

    # -----------------------------
    # Ondulado
    # -----------------------------

    "Percurso com variação de altimetria.": "Ondulado",
    "Percurso com variação de altimetria": "Ondulado",
    "variação de altimetria": "Ondulado",
    "TRILHA": "Ondulado",
    "TRILHA FÁCIL": "Ondulado",
    "TRILHA TÉCNICA": "Ondulado",
    "Trilhas": "Ondulado",

    # -----------------------------
    # Montaña
    # -----------------------------

    "Percurso com a máxima variação de altimetria.": "Montaña",
    "Percurso com a máxima variação de altimetria": "Montaña",

    # -----------------------------
    # Subida
    # -----------------------------

    "SUBIDA": "Subida",
    "SUBIDA 1min": "Subida",
    "SUBIDA 2min": "Subida",
    "SUBIDA 4min": "Subida",
    "SUBIDA >10min": "Subida",
    "SUBIDA / descida, percurso com variação de altimetria.": "Subida",

    # -----------------------------
    # Información irrelevante
    # -----------------------------

    "Mantenha a cadência entre 80 e 95rpm.": None,
}

# ==========================================================
# SESSION TYPE MAPPING
# ==========================================================

SESSION_TYPE_MAPPING = {

    # -----------------------------
    # Vacíos
    # -----------------------------

    None: None,
    "": None,

    # -----------------------------
    # Duplicados
    # -----------------------------

    "SUBIDA": "Subidas",
    "rest": None,

    # -----------------------------
    # Fusionar categorías
    # -----------------------------

    "Acelerações Curtas": "Acelerações",

    # -----------------------------
    # Muy poco frecuentes
    # -----------------------------

    "Técnica": "Otros",
    "Força": "Otros",
    "Pliometria": "Otros",
    "Prova": "Otros",
}

# ==========================================================
# WEEK TYPE MAPPING
# ==========================================================

WEEK_TYPE_MAPPING = {

    # Desarrollo aeróbico

    "1 - DESENVOLVIMENTO AERÓBICO":
        "Desenvolvimento Aeróbico",

    "DESENVOLVIMENTO AERÓBICO":
        "Desenvolvimento Aeróbico",

    # Desarrollo anaeróbico

    "3 - DESENVOLVIMENTO ANAERÓBICO":
        "Desenvolvimento Anaeróbico",

    "DESENVOLVIMENTO ANAERÓBICO":
        "Desenvolvimento Anaeróbico",

    "3 - DESENVOLVIMENTO ANAÓBICO":
        "Desenvolvimento Anaeróbico",

    # Técnica

    "2 - TÉCNICA E FORTALECIMENTO":
        "Técnica e Fortalecimento",

    # Pico

    "4 - PICO DE PERFORMANCE":
        "Pico de Performance",

    # Especiales

    "SEMANA CONTROLE":
        "Semana Controle",

    "SEMANA REGENERATIVA":
        "Semana Regenerativa",

    # Otros

    "POLIMENTO":
        "Otros",

    "AREX 500km. RUN 140km + BIK 280km + KYK 80km":
        "Otros",

    # Vacíos

    None: None,
    "": None,
}

# ==========================================================
# Funciones auxiliares
# ==========================================================

def _clean_string(value):
    """
    Limpia strings provenientes del extractor.
    """

    if value is None:
        return None

    if not isinstance(value, str):
        return value

    value = value.strip()

    if value == "":
        return None

    return value


# ==========================================================
# Week type
# ==========================================================

def _normalize_week_type(week):

    week_type = _clean_string(
        week.get("week_type")
    )

    if week_type not in WEEK_TYPE_MAPPING:
        raise ValueError(
            f"Unknown week type: '{week_type}'"
        )

    week["week_type"] = WEEK_TYPE_MAPPING[
        week_type
    ]

# ==========================================================
# Dates
# ==========================================================

def _normalize_dates(week):
    """
    Convierte las fechas al formato ISO (YYYY-MM-DD).
    """

    # -----------------------------
    # Week start / end
    # -----------------------------

    for field in ["week_start", "week_end"]:

        value = week.get(field)

        if value is None:
            continue

        value = value.strip()

        # Ya está en formato ISO
        try:
            datetime.strptime(value, "%Y-%m-%d")
            continue
        except ValueError:
            pass

        # Formato DD/MM/YYYY
        try:
            dt = datetime.strptime(value, "%d/%m/%Y")
            week[field] = dt.strftime("%Y-%m-%d")
            continue
        except ValueError:
            pass

        raise ValueError(
            f"Unknown date format: '{value}'"
        )

    # -----------------------------
    # Session dates
    # -----------------------------

    for session in week["sessions"]:

        value = session.get("date")

        if value is None:
            continue

        value = value.strip()

        try:
            datetime.strptime(value, "%Y-%m-%d")
            continue
        except ValueError:
            pass

        try:
            dt = datetime.strptime(value, "%d/%m/%Y")
            session["date"] = dt.strftime("%Y-%m-%d")
            continue
        except ValueError:
            pass

        raise ValueError(
            f"Unknown session date: '{value}'"
        )


# ==========================================================
# Sports
# ==========================================================

def _normalize_sports(week):

    normalized_sessions = []

    for session in week["sessions"]:

        sport = _clean_string(
            session.get("sport")
        )

        if sport not in SPORT_MAPPING:
            raise ValueError(
                f"Unknown sport: '{sport}'"
            )

        sport = SPORT_MAPPING[sport]

        # Deportes descartados
        if sport is None:
            continue

        session["sport"] = sport

        normalized_sessions.append(
            session
        )

    week["sessions"] = normalized_sessions


# ==========================================================
# Terrain
# ==========================================================

def _normalize_terrain(week):

    for session in week["sessions"]:

        terrain = _clean_string(
            session.get("terrain")
        )

        if terrain not in TERRAIN_MAPPING:
            raise ValueError(
                f"Unknown terrain: '{terrain}'"
            )

        session["terrain"] = TERRAIN_MAPPING[
            terrain
        ]


# ==========================================================
# Session type
# ==========================================================

def _normalize_session_type(week):

    for session in week["sessions"]:

        session_type = _clean_string(
            session.get("session_type")
        )

        if session_type in SESSION_TYPE_MAPPING:
            session_type = SESSION_TYPE_MAPPING[
                session_type
            ]

        session["session_type"] = session_type

# ==========================================================
# Seleccionar sesión principal
# ==========================================================

def _session_score(session):
    """
    Devuelve un score para elegir la sesión principal.

    Por ahora usa la distancia total.
    En el futuro podrá reemplazarse por duración o carga.
    """

    distance = 0

    for block in session.get("blocks", []):

        km = block.get("distance_km")

        if km is not None:
            distance += km

    return distance


def _select_primary_sessions(week):

    grouped = defaultdict(list)

    for session in week["sessions"]:
        grouped[session["weekday"]].append(session)

    selected_sessions = []

    for weekday in WEEKDAYS:

        sessions = grouped.get(weekday, [])

        if len(sessions) == 0:
            continue

        if len(sessions) == 1:
            selected_sessions.append(sessions[0])
            continue

        best = max(
            sessions,
            key=_session_score
        )

        selected_sessions.append(best)

    week["sessions"] = selected_sessions

    _sort_sessions(week)


# ==========================================================
# Ordenar semana
# ==========================================================

def _sort_sessions(week):

    week["sessions"].sort(
        key=lambda s: WEEKDAY_ORDER[
            s["weekday"]
        ]
    )


# ==========================================================
# Insertar días de descanso faltantes
# ==========================================================

def _ensure_rest_days(week):

    existing_days = {

        session["weekday"]

        for session in week["sessions"]

    }

    week_start = datetime.strptime(

        week["week_start"],

        "%Y-%m-%d"

    )

    for i, weekday in enumerate(WEEKDAYS):

        if weekday in existing_days:
            continue

        date = (

            week_start + timedelta(days=i)

        ).strftime(

            "%Y-%m-%d"

        )

        week["sessions"].append({

            "date": date,

            "weekday": weekday,

            "sport": "Descanso",

            "session_type": None,

            "terrain": None,

            "blocks": []

        })

    _sort_sessions(
        week
    )

# ==========================================================
# Validación
# ==========================================================

VALID_SPORTS = {
    "Descanso",
    "Bici",
    "Corrida",
    "Trail",
    "Kayak",
}

VALID_TERRAINS = {
    None,
    "Plano",
    "Ondulado",
    "Montaña",
    "Subida",
}


def _validate_json(week):
    """
    Comprueba que el JSON resultante tenga un formato válido.
    Lanza una excepción si detecta algún problema.
    """

    if "sessions" not in week:
        raise ValueError("JSON does not contain 'sessions'")

    if len(week["sessions"]) != 7:
        raise ValueError(
            f"Week must contain exactly 7 sessions "
            f"(found {len(week['sessions'])})"
        )

    weekdays = []

    for session in week["sessions"]:

        weekday = session.get("weekday")

        if weekday not in WEEKDAY_ORDER:
            raise ValueError(
                f"Unknown weekday '{weekday}'"
            )

        weekdays.append(weekday)

        sport = session.get("sport")

        if sport not in VALID_SPORTS:
            raise ValueError(
                f"Unknown normalized sport '{sport}'"
            )

        terrain = session.get("terrain")

        if terrain not in VALID_TERRAINS:
            raise ValueError(
                f"Unknown normalized terrain '{terrain}'"
            )

        if "blocks" not in session:
            raise ValueError(
                f"Session {weekday} has no blocks"
            )

        if not isinstance(session["blocks"], list):
            raise ValueError(
                f"Blocks must be a list ({weekday})"
            )

    # No repetir días

    if len(set(weekdays)) != 7:
        raise ValueError(
            "Duplicated weekdays detected"
        )


# ==========================================================
# Función pública
# ==========================================================

def normalize_json(week):
    """
    Normaliza un JSON extraído desde una planilla.

    Parameters
    ----------
    week : dict

    Returns
    -------
    dict
        JSON normalizado.
    """

    week = deepcopy(week)

    _normalize_week_type(
        week
    )

    _normalize_dates(
        week
    )

    _normalize_sports(
        week
    )

    _normalize_terrain(
        week
    )

    _normalize_session_type(
        week
    )

    _select_primary_sessions(
        week
    )

    _ensure_rest_days(
        week
    )

    _validate_json(
        week
    )

    return week