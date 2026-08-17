"""Speed@FC Z5 protocol."""

from metrics.speed_at_hr_zone.protocol import PROTOCOL as BASE_PROTOCOL

PROTOCOL = {
    **BASE_PROTOCOL,
    "title": "Test de velocidad en Z5 y terreno plano",
    "duration_seconds": 180.0,
    "heart_rate_zone_ids": ["Z5"],
    "steps": [step.replace("Z2", "Z5") for step in BASE_PROTOCOL["steps"]],
}
