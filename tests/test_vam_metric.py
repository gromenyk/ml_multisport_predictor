from datetime import datetime, timedelta, timezone

from metrics.vam_at_hr_zone.calculator import calculate
from metrics.vam_at_hr_zone.extractor import extract


ZONE_PROFILE = {
    "id": "zones_test",
    "zones": [
        {"id": "Z2", "lower_bpm": 120, "upper_bpm": 139},
        {"id": "Z3", "lower_bpm": 140, "upper_bpm": 159},
    ],
}


def activity(seconds=300, heart_rate=130, slope=10.0, speed=2.0):
    start = datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc)
    records = []
    for offset in range(seconds + 1):
        distance = speed * offset
        records.append(
            {
                "timestamp": (start + timedelta(seconds=offset)).isoformat(),
                "distance_meters": distance,
                "altitude_meters": 500 + distance * slope / 100.0,
                "heart_rate_bpm": heart_rate,
                "speed_mps": speed,
            }
        )
    return {
        "activity": {"sport_profile_name": "Trail running", "date": "2026-07-01"},
        "records": records,
    }


def test_two_independent_heart_rate_zone_series():
    found = set()
    for zone_id, heart_rate in (("Z2", 130), ("Z3", 150)):
        extracted = extract(activity(heart_rate=heart_rate, slope=12.5), {}, ZONE_PROFILE)
        assert extracted["status"] == "ready"
        segment = extracted["segments"][0]
        assert segment["heart_rate_zone_id"] == zone_id
        assert segment["slope_band"] == "10-15%"
        found.add((zone_id, segment["slope_band"]))
    assert len(found) == 2


def test_rejects_slopes_outside_ten_to_fifteen_percent():
    assert extract(activity(slope=9.9), {}, ZONE_PROFILE)["status"] == "no_qualifying_segments"
    assert extract(activity(slope=15.1), {}, ZONE_PROFILE)["status"] == "no_qualifying_segments"
    assert extract(activity(slope=10.0), {}, ZONE_PROFILE)["status"] == "ready"
    assert extract(activity(slope=15.0), {}, ZONE_PROFILE)["status"] == "ready"


def test_vam_formula_and_non_overlapping_segments():
    extracted = extract(activity(seconds=600, slope=10.0, speed=2.0), {}, ZONE_PROFILE)
    assert len(extracted["segments"]) == 2
    first, second = extracted["segments"]
    assert first["end_time"] <= second["start_time"]
    assert abs(first["net_elevation_gain_meters"] - 60.0) < 1e-9
    results = calculate(extracted)
    assert abs(results[0]["value"] - 720.0) < 1e-9
    assert results[0]["unit"] == "m/h"


def test_walking_speed_is_not_classified_separately():
    extracted = extract(activity(speed=0.4, slope=12.0), {}, ZONE_PROFILE)
    assert extracted["status"] == "ready"
    assert extracted["segments"][0]["slope_band"] == "10-15%"


def test_requires_ninety_percent_in_zone_with_two_bpm_tolerance():
    accepted_activity = activity()
    for index, record in enumerate(accepted_activity["records"]):
        record["heart_rate_bpm"] = 144 if index < 30 else 130
    accepted = extract(accepted_activity, {}, ZONE_PROFILE)
    assert accepted["status"] == "ready"
    assert accepted["segments"][0]["time_in_zone_share"] >= 0.90

    rejected_activity = activity()
    for index, record in enumerate(rejected_activity["records"]):
        record["heart_rate_bpm"] = 144 if index < 32 else 130
    rejected = extract(rejected_activity, {}, ZONE_PROFILE)
    assert rejected["status"] == "no_qualifying_segments"
    assert rejected["candidate_rejection_counts"]["insufficient_time_in_zone"] == 1


def test_rejects_a_pause_or_recording_gap():
    value = activity(seconds=330)
    value["records"] = [
        record for index, record in enumerate(value["records"])
        if not 150 <= index <= 170
    ]
    extracted = extract(value, {}, ZONE_PROFILE)
    assert extracted["status"] == "no_qualifying_segments"
    assert extracted["candidate_rejection_counts"]["movement_not_continuous"] > 0
