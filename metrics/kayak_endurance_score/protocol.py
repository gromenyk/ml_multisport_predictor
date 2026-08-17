"""Kayak Endurance Score protocol."""

PROTOCOL = {
    "title": "Kayak Endurance Score por actividad",
    "whole_activity": True,
    "formula": "distancia_km × velocidad_media_kmh",
    "target_interval_days": 28,
    "planning_notice_days": 7,
    "grace_period_days": 7,
    "steps": [
        "Realiza una actividad de Kayak con distancia y tiempo activo registrados.",
        "El sistema utiliza la distancia total en kilómetros.",
        "Utiliza la velocidad media de Garmin en km/h; si falta, la deriva de distancia ÷ tiempo activo.",
        "Calcula Kayak Endurance Score = distancia (km) × velocidad media (km/h).",
        "Compara actividades considerando que una mayor distancia o velocidad eleva el resultado.",
    ],
}
