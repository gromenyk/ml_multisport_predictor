from datetime import datetime, timedelta, timezone

from metrics.speed_z4_at_hr_zone.extractor import extract as extract_speed_z4
from metrics.vam_z4_trail.extractor import extract as extract_vam_trail
from metrics.vam_z4_road.extractor import extract as extract_vam_road
from metrics.vam_z4_mtb.extractor import extract as extract_vam_mtb
from metrics.speed_z5_at_hr_zone.extractor import extract as extract_speed_z5


ZONES = {
    "id": "zones_threshold",
    "zones": [{"id": "Z4", "lower_bpm": 160, "upper_bpm": 174}],
}
Z5_ZONES = {
    "id": "zones_maximal_aerobic",
    "zones": [{"id": "Z5", "lower_bpm": 175, "upper_bpm": 190}],
}


def activity(profile, sport, seconds, heart_rate=167, slope=7.5, speed=4.0):
    start = datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc)
    records = []
    for offset in range(seconds + 1):
        distance = speed * offset
        records.append({
            "timestamp": (start + timedelta(seconds=offset)).isoformat(),
            "distance_meters": distance,
            "altitude_meters": 500 + distance * slope / 100,
            "heart_rate_bpm": heart_rate,
            "speed_mps": speed,
        })
    return {
        "activity": {
            "sport": sport, "sport_profile_name": profile,
            "sub_sport": "generic", "date": "2026-07-01",
        },
        "records": records,
    }


def test_speed_z4_uses_ten_minutes_and_flat_slope():
    value = activity("Carrera", "running", 600, slope=0.0, speed=3.0)
    extracted = extract_speed_z4(value, {}, ZONES)
    assert extracted["status"] == "ready"
    assert extracted["segments"][0]["duration_seconds"] == 600
    assert extracted["zone_id"] == "Z4"


def test_vam_z4_uses_ten_minutes_and_correct_modalities():
    trail = activity("Trail running", "running", 600, slope=12.0, speed=2.0)
    road = activity("Bici", "cycling", 600, slope=7.5)
    mtb = activity("MTB", "cycling", 600, slope=7.5)
    trail_result = extract_vam_trail(trail, {}, ZONES)
    road_result = extract_vam_road(road, {}, ZONES)
    mtb_result = extract_vam_mtb(mtb, {}, ZONES)
    assert trail_result["segments"][0]["duration_seconds"] == 600
    assert road_result["segments"][0]["duration_seconds"] == 600
    assert mtb_result["segments"][0]["duration_seconds"] == 600
    assert all(
        result["segments"][0]["heart_rate_zone_id"] == "Z4"
        for result in (trail_result, road_result, mtb_result)
    )
    assert extract_vam_trail(activity("Carrera", "running", 600, slope=12), {}, ZONES)["status"] == "not_applicable"
    assert extract_vam_road(mtb, {}, ZONES)["status"] == "not_applicable"
    assert extract_vam_mtb(road, {}, ZONES)["status"] == "not_applicable"


def test_speed_z5_uses_three_minutes_and_flat_slope():
    value = activity(
        "Trail running", "running", 180,
        heart_rate=182, slope=0.0, speed=3.0,
    )
    extracted = extract_speed_z5(value, {}, Z5_ZONES)
    assert extracted["status"] == "ready"
    assert extracted["zone_id"] == "Z5"
    assert extracted["segments"][0]["duration_seconds"] == 180
