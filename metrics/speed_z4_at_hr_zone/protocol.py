"""Speed@FC Z4 protocol."""

from metrics.speed_at_hr_zone.protocol import PROTOCOL as BASE_PROTOCOL

PROTOCOL = {
    **BASE_PROTOCOL,
    "title": "Test de velocidad en Z4 y terreno plano",
    "duration_seconds": 600.0,
    "heart_rate_zone_ids": ["Z4"],
    "steps": [
        step.replace("Z2", "Z4").replace("3 minutos", "10 minutos")
        for step in BASE_PROTOCOL["steps"]
    ],
}
