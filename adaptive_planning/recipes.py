"""Explicit links between active development goals and trainable stimuli."""

from __future__ import annotations


RECIPES = {
    "goal_aerobic_efficiency": {
        "name": "Eficiencia aeróbica",
        "operation": "aerobic_efficiency",
        "metric_ids": {"metric_speed_at_standardized_heart_rate"},
        "preferred_zones": {"Z2"},
        "summary": "Mantener la sesión prevista e identificar dentro de ella una oportunidad comparable de Speed@FC.",
        "allowed_changes": ["mantener sesión", "buscar tramo continuo y plano para evaluar"],
    },
    "goal_vertical_climbing_performance": {
        "name": "Rendimiento en ascenso vertical",
        "operation": "vertical",
        "metric_ids": {
            "metric_vam_at_standardized_heart_rate",
            "metric_cycling_road_vam_at_standardized_heart_rate",
            "metric_cycling_mtb_vam_at_standardized_heart_rate",
        },
        "preferred_zones": {"Z2", "Z3"},
        "summary": "Introducir ascenso sostenido en la disciplina de la serie VAM que necesita atención.",
        "allowed_changes": ["redistribuir carga", "cambiar terreno", "bloque sostenido de subida"],
    },
    "goal_kayak_endurance": {
        "name": "Resistencia en kayak",
        "operation": "kayak_endurance",
        "metric_ids": {"metric_kayak_endurance_score"},
        "preferred_zones": {"Z2", "Z3"},
        "summary": "Aumentar exposición continua en kayak preservando una intensidad sostenible.",
        "allowed_changes": ["redistribuir carga", "extender sesión continua", "priorizar sesión larga"],
    },
    "goal_threshold_performance": {
        "name": "Rendimiento en el umbral",
        "operation": "threshold",
        "metric_ids": {
            "metric_speed_z4_at_standardized_heart_rate",
            "metric_vam_z4_trail",
            "metric_vam_z4_road",
            "metric_vam_z4_mtb",
        },
        "preferred_zones": {"Z4"},
        "summary": "Acumular bloques controlados de 10 minutos en Z4, específicos a la métrica.",
        "allowed_changes": ["crear bloques Z4", "ajustar terreno", "mover intensidad"],
    },
    "goal_maximal_aerobic_capacity": {
        "name": "Capacidad aeróbica máxima",
        "operation": "maximal_aerobic",
        "metric_ids": {"metric_speed_z5_at_standardized_heart_rate"},
        "preferred_zones": {"Z5"},
        "summary": "Introducir repeticiones de 3 minutos en Z5 con recuperación suficiente.",
        "allowed_changes": ["crear intervalos Z5", "ajustar repeticiones", "mover intensidad"],
    },
}


STATUS_URGENCY = {
    "Sin medición": 4,
    "Desactualizada": 4,
    "Evaluación pendiente": 4,
    "Próxima a vencer": 2,
    "Reciente": 0,
}
