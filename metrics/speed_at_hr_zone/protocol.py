"""Single source of truth for the Speed@FC evaluation protocol."""

SEGMENT_SECONDS = 180.0
MAX_RECORD_GAP_SECONDS = 5.0
MIN_MOVING_SPEED_MPS = 0.5
MIN_TIME_IN_EXPANDED_ZONE = 0.90
HEART_RATE_TOLERANCE_BPM = 2
MIN_SLOPE_PERCENT = -1.0
MAX_SLOPE_PERCENT = 1.0

PROTOCOL = {
    "title": "Test de velocidad en Z2 y terreno plano",
    "duration_seconds": SEGMENT_SECONDS,
    "heart_rate_zone_ids": ["Z2"],
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
        "Realiza una sesión de Corrida o Trail con un tramo continuo y prácticamente plano.",
        "Mantén 3 minutos con la FC media dentro de Z2; se permiten hasta 10 segundos detenidos acumulados y registros separados hasta 15 segundos.",
        "Al menos el 90% del tramo debe estar en Z2, con tolerancia de ±2 bpm en sus límites.",
        "La pendiente media del tramo debe quedar entre −1% y +1%.",
        "El sistema detecta automáticamente los segmentos válidos y calcula su velocidad media en km/h.",
    ],
}
