from metrics.kayak_endurance_score.calculator import calculate
from metrics.kayak_endurance_score.extractor import extract


def test_kayak_endurance_score_uses_km_and_kmh():
    activity = {
        "activity": {
            "sport": "kayaking", "sport_profile_name": "Kayak",
            "date": "2026-07-01", "start_time": "2026-07-01T08:00:00+00:00",
            "distance_meters": 10000.0, "duration_seconds": 3600.0,
        },
        "summary": {"average_speed_mps": 2.5},
    }
    extracted = extract(activity, {}, None)
    result = calculate(extracted)[0]
    assert result["value"] == 90.0
    assert result["unit"] == "km²/h"
    assert result["segment"]["distance_km"] == 10.0
    assert result["segment"]["average_speed_kmh"] == 9.0


def test_non_kayak_is_not_applicable_and_speed_can_fallback():
    assert extract({"activity": {"sport": "running"}}, {}, None)["status"] == "not_applicable"
    kayak = {
        "activity": {
            "sport": "kayaking", "date": "2026-07-01",
            "distance_meters": 5000.0, "duration_seconds": 2500.0,
        },
        "summary": {},
    }
    segment = extract(kayak, {}, None)["segments"][0]
    assert segment["average_speed_mps"] == 2.0
