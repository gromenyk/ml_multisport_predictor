"""MTB VAM protocol."""

from metrics.vam_at_hr_zone.protocol import PROTOCOL as BASE_PROTOCOL

PROTOCOL = {
    **BASE_PROTOCOL,
    "title": "Test de VAM en MTB por zona de FC",
    "cycling_modality": "MTB",
    "minimum_slope_percent": 5.0,
    "maximum_slope_percent": 10.0,
    "slope_bands": ["5-10%"],
    "maximum_record_gap_seconds": 15,
    "minimum_moving_time_share": 0.95,
    "maximum_stopped_time_seconds": 10,
    "steps": [
        "Realiza una sesión de MTB en una subida sostenida.",
        "Mantén 5 minutos de ascenso en Z2 o Z3; se permiten hasta 10 segundos detenidos acumulados y registros separados hasta 15 segundos.",
        "Al menos el 90% del tramo debe estar en la zona evaluada, con tolerancia de ±2 bpm.",
        "La pendiente media debe quedar entre 5% y 10%, incluidos ambos límites.",
        "El sistema calcula VAM = desnivel neto positivo / duración × 3600 y separa Z2 de Z3.",
    ],
}
