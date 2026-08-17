from datetime import datetime, timedelta, timezone

import pytest

from metrics.speed_at_hr_zone.calculator import calculate
from metrics.speed_at_hr_zone.extractor import extract


ZONE_PROFILE = {
    "id": "test_profile",
    "zones": [{"id": "Z2", "lower_bpm": 130, "upper_bpm": 144}],
}
ASSIGNMENT = {"heart_rate_zone_id": "Z2"}


def _activity(
    seconds=360,
    speed_mps=3.0,
    heart_rate=137,
    altitude_gain=0.0,
    discipline="Carrera",
):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    records = []
    for second in range(seconds + 1):
        records.append(
            {
                "timestamp": (start + timedelta(seconds=second)).isoformat(),
                "distance_meters": speed_mps * second,
                "heart_rate_bpm": heart_rate,
                "altitude_meters": altitude_gain * second / seconds,
                "speed_mps": speed_mps,
            }
        )
    return {
        "activity": {"sport_profile_name": discipline},
        "records": records,
    }


def test_returns_two_exact_non_overlapping_segments():
    extracted = extract(_activity(), ASSIGNMENT, ZONE_PROFILE)
    results = calculate(extracted)

    assert extracted["status"] == "ready"
    assert len(results) == 2
    assert all(
        result["segment"]["duration_seconds"] == 180 for result in results
    )
    assert results[0]["segment"]["end_time"] == results[1]["segment"]["start_time"]
    assert results[0]["value"] == pytest.approx(10.8)


def test_rejects_mean_hr_outside_strict_z2_even_with_tolerance():
    extracted = extract(
        _activity(heart_rate=129), ASSIGNMENT, ZONE_PROFILE
    )

    assert extracted["segments"] == []
    assert extracted["candidate_rejection_counts"]["mean_hr_outside_zone"] > 0


def test_rejects_slope_greater_than_one_percent():
    extracted = extract(
        _activity(altitude_gain=12), ASSIGNMENT, ZONE_PROFILE
    )

    assert extracted["segments"] == []
    assert (
        extracted["candidate_rejection_counts"][
            "mean_slope_outside_range"
        ]
        > 0
    )


def test_accepts_trail_running_name_from_fit():
    extracted = extract(
        _activity(seconds=180, discipline="Trail running"),
        ASSIGNMENT,
        ZONE_PROFILE,
    )

    assert extracted["discipline"] == "Trail"
    assert len(extracted["segments"]) == 1


def test_tolerates_a_brief_pause_inside_the_segment():
    activity = _activity(seconds=181)
    activity["records"][90]["distance_meters"] = (
        activity["records"][89]["distance_meters"]
    )
    extracted = extract(activity, ASSIGNMENT, ZONE_PROFILE)

    assert len(extracted["segments"]) == 1
    assert extracted["segments"][0]["stopped_time_seconds"] <= 10


def test_rejects_a_long_recording_gap():
    activity = _activity(seconds=200)
    activity["records"] = [
        row for index, row in enumerate(activity["records"])
        if not 80 <= index <= 100
    ]
    extracted = extract(activity, ASSIGNMENT, ZONE_PROFILE)
    assert extracted["segments"] == []
    assert extracted["candidate_rejection_counts"]["movement_not_continuous"] > 0
