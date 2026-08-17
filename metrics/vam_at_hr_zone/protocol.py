"""Single source of truth for the VAM@FC evaluation protocol."""

SEGMENT_SECONDS = 300.0
MAX_RECORD_GAP_SECONDS = 5.0
MIN_TIME_IN_EXPANDED_ZONE = 0.90
HEART_RATE_TOLERANCE_BPM = 2
MIN_SLOPE_PERCENT = 10.0
MAX_SLOPE_PERCENT = 15.0
SLOPE_EPSILON = 1e-9
ZONE_IDS = ("Z2", "Z3")
SLOPE_BANDS = ("10-15%",)

PROTOCOL = {
    "title": "Test de VAM por zona de FC y banda de pendiente",
    "duration_seconds": SEGMENT_SECONDS,
    "heart_rate_zone_ids": list(ZONE_IDS),
    "slope_bands": list(SLOPE_BANDS),
    "minimum_time_in_zone_share": MIN_TIME_IN_EXPANDED_ZONE,
    "heart_rate_tolerance_bpm": HEART_RATE_TOLERANCE_BPM,
    "minimum_slope_percent": MIN_SLOPE_PERCENT,
    "maximum_slope_percent": MAX_SLOPE_PERCENT,
    "overlap_allowed": False,
    "maximum_record_gap_seconds": 15,
    "minimum_moving_time_share": 0.95,
    "maximum_stopped_time_seconds": 10,
    "target_interval_days": 28,
    "planning_notice_days": 7,
    "grace_period_days": 7,
    "steps": [
        "Realiza una sesión de Corrida o Trail en una subida sostenida.",
        "Mantén 5 minutos en Z2 o Z3; se permiten hasta 10 segundos detenidos acumulados y registros separados hasta 15 segundos.",
        "Al menos el 90% del tramo debe estar en la zona evaluada, con tolerancia de ±2 bpm.",
        "La pendiente media debe quedar entre 10% y 15%, incluidos ambos límites.",
        "El sistema calcula VAM = desnivel neto positivo / duración × 3600 y conserva separadas las series Z2 y Z3.",
    ],
}
