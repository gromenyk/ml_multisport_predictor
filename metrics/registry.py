"""Technical registry connecting stable metric IDs to implementations."""

from metrics.speed_at_hr_zone.calculator import (
    CALCULATION_VERSION as SPEED_CALCULATION_VERSION,
    calculate as calculate_speed,
)
from metrics.speed_at_hr_zone.extractor import extract as extract_speed
from metrics.speed_at_hr_zone.protocol import PROTOCOL as SPEED_PROTOCOL
from metrics.vam_at_hr_zone.calculator import (
    CALCULATION_VERSION as VAM_CALCULATION_VERSION,
    calculate as calculate_vam,
)
from metrics.vam_at_hr_zone.extractor import extract as extract_vam
from metrics.vam_at_hr_zone.protocol import PROTOCOL as VAM_PROTOCOL
from metrics.cycling_vam_road.calculator import (
    CALCULATION_VERSION as ROAD_VAM_CALCULATION_VERSION,
    calculate as calculate_road_vam,
)
from metrics.cycling_vam_road.extractor import extract as extract_road_vam
from metrics.cycling_vam_road.protocol import PROTOCOL as ROAD_VAM_PROTOCOL
from metrics.cycling_vam_mtb.calculator import (
    CALCULATION_VERSION as MTB_VAM_CALCULATION_VERSION,
    calculate as calculate_mtb_vam,
)
from metrics.cycling_vam_mtb.extractor import extract as extract_mtb_vam
from metrics.cycling_vam_mtb.protocol import PROTOCOL as MTB_VAM_PROTOCOL
from metrics.kayak_endurance_score.calculator import (
    CALCULATION_VERSION as KAYAK_ENDURANCE_CALCULATION_VERSION,
    calculate as calculate_kayak_endurance,
)
from metrics.kayak_endurance_score.extractor import extract as extract_kayak_endurance
from metrics.kayak_endurance_score.protocol import PROTOCOL as KAYAK_ENDURANCE_PROTOCOL
from metrics.speed_z4_at_hr_zone.calculator import CALCULATION_VERSION as SPEED_Z4_VERSION, calculate as calculate_speed_z4
from metrics.speed_z4_at_hr_zone.extractor import extract as extract_speed_z4
from metrics.speed_z4_at_hr_zone.protocol import PROTOCOL as SPEED_Z4_PROTOCOL
from metrics.vam_z4_trail.calculator import CALCULATION_VERSION as VAM_Z4_TRAIL_VERSION, calculate as calculate_vam_z4_trail
from metrics.vam_z4_trail.extractor import extract as extract_vam_z4_trail
from metrics.vam_z4_trail.protocol import PROTOCOL as VAM_Z4_TRAIL_PROTOCOL
from metrics.vam_z4_road.calculator import CALCULATION_VERSION as VAM_Z4_ROAD_VERSION, calculate as calculate_vam_z4_road
from metrics.vam_z4_road.extractor import extract as extract_vam_z4_road
from metrics.vam_z4_road.protocol import PROTOCOL as VAM_Z4_ROAD_PROTOCOL
from metrics.vam_z4_mtb.calculator import CALCULATION_VERSION as VAM_Z4_MTB_VERSION, calculate as calculate_vam_z4_mtb
from metrics.vam_z4_mtb.extractor import extract as extract_vam_z4_mtb
from metrics.vam_z4_mtb.protocol import PROTOCOL as VAM_Z4_MTB_PROTOCOL
from metrics.speed_z5_at_hr_zone.calculator import CALCULATION_VERSION as SPEED_Z5_VERSION, calculate as calculate_speed_z5
from metrics.speed_z5_at_hr_zone.extractor import extract as extract_speed_z5
from metrics.speed_z5_at_hr_zone.protocol import PROTOCOL as SPEED_Z5_PROTOCOL


METRIC_REGISTRY = {
    "metric_speed_at_standardized_heart_rate": {
        "extract": extract_speed,
        "calculate": calculate_speed,
        "calculation_version": SPEED_CALCULATION_VERSION,
        "protocol": SPEED_PROTOCOL,
    },
    "metric_vam_at_standardized_heart_rate": {
        "extract": extract_vam,
        "calculate": calculate_vam,
        "calculation_version": VAM_CALCULATION_VERSION,
        "protocol": VAM_PROTOCOL,
    },
    "metric_cycling_road_vam_at_standardized_heart_rate": {
        "extract": extract_road_vam,
        "calculate": calculate_road_vam,
        "calculation_version": ROAD_VAM_CALCULATION_VERSION,
        "protocol": ROAD_VAM_PROTOCOL,
    },
    "metric_cycling_mtb_vam_at_standardized_heart_rate": {
        "extract": extract_mtb_vam,
        "calculate": calculate_mtb_vam,
        "calculation_version": MTB_VAM_CALCULATION_VERSION,
        "protocol": MTB_VAM_PROTOCOL,
    },
    "metric_kayak_endurance_score": {
        "extract": extract_kayak_endurance,
        "calculate": calculate_kayak_endurance,
        "calculation_version": KAYAK_ENDURANCE_CALCULATION_VERSION,
        "protocol": KAYAK_ENDURANCE_PROTOCOL,
    },
    "metric_speed_z4_at_standardized_heart_rate": {
        "extract": extract_speed_z4, "calculate": calculate_speed_z4,
        "calculation_version": SPEED_Z4_VERSION, "protocol": SPEED_Z4_PROTOCOL,
    },
    "metric_vam_z4_trail": {
        "extract": extract_vam_z4_trail, "calculate": calculate_vam_z4_trail,
        "calculation_version": VAM_Z4_TRAIL_VERSION, "protocol": VAM_Z4_TRAIL_PROTOCOL,
    },
    "metric_vam_z4_road": {
        "extract": extract_vam_z4_road, "calculate": calculate_vam_z4_road,
        "calculation_version": VAM_Z4_ROAD_VERSION, "protocol": VAM_Z4_ROAD_PROTOCOL,
    },
    "metric_vam_z4_mtb": {
        "extract": extract_vam_z4_mtb, "calculate": calculate_vam_z4_mtb,
        "calculation_version": VAM_Z4_MTB_VERSION, "protocol": VAM_Z4_MTB_PROTOCOL,
    },
    "metric_speed_z5_at_standardized_heart_rate": {
        "extract": extract_speed_z5, "calculate": calculate_speed_z5,
        "calculation_version": SPEED_Z5_VERSION, "protocol": SPEED_Z5_PROTOCOL,
    },
}
