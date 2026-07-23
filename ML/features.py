# features.py

SPORT_MAP = {
    "Descanso": 0,
    "Trail": 1,
    "Corrida": 2,
    "Bici": 3,
    "Kayak": 4,
}

TERRAIN_MAP = {
    None: 0,
    "Plano": 1,
    "Ondulado": 2,
    "Montaña": 3,
    "Subida": 4,
}

ZONE_MAP = {
    None: 0,
    "Z1": 1,
    "Z2": 2,
    "Z3": 3,
    "Z4": 4,
    "Z5": 5,
}

SESSION_TYPE_MAP = {
    None: 0,
    "Leve": 1,
    "Regenerativo": 2,
    "Avaliação": 3,
    "Longão": 4,
    "Simulado": 5,
    "Contínuo": 6,
    "Acelerações": 7,
    "Ritmado": 8,
    "Rest": 9,
    "Transição": 10,
    "Subidas": 11,
    "Otros": 12,
}


def encode_session(session):
    """
    Convierte una sesión en un vector numérico.

    Vector:
    [
        sport,
        terrain,
        zone,
        distance
    ]
    """

    # Día sin entrenamiento
    if session is None:

        return [
            SPORT_MAP["Descanso"],
            TERRAIN_MAP[None],
            ZONE_MAP[None],
            0.0,
        ]

    sport = SPORT_MAP[session["sport"]]

    terrain = TERRAIN_MAP.get(
        session.get("terrain"),
        TERRAIN_MAP[None]
    )

    blocks = session.get("blocks", [])

    # Sesión vacía
    if len(blocks) == 0:

        zone = ZONE_MAP[None]
        distance = 0.0

    else:

        # Por ahora usamos el último bloque
        # (en las sesiones simples es el único)
        block = blocks[-1]

        zone = ZONE_MAP.get(
            block.get("zone"),
            ZONE_MAP[None]
        )

        distance = block.get("distance_km")

        if distance is None:
            distance = 0.0

    return [
        sport,
        terrain,
        zone,
        float(distance),
    ]