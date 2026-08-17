from metrics.cycling_vam_road.protocol import PROTOCOL as BASE_PROTOCOL

PROTOCOL = {
    **BASE_PROTOCOL, "title": "Test de VAM Ruta en Z4",
    "duration_seconds": 600.0, "heart_rate_zone_ids": ["Z4"],
    "steps": [
        "Realiza una sesión de ciclismo de ruta en una subida sostenida con pendiente media de 5–10%.",
        "Mantén 10 minutos en Z4; se permiten hasta 10 segundos detenidos acumulados y registros separados hasta 15 segundos.",
        "Al menos el 90% del tramo debe estar en Z4, con tolerancia de ±2 bpm.",
        "El sistema calcula VAM = desnivel neto positivo / duración × 3600.",
    ],
}
