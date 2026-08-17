from datetime import datetime, timedelta, timezone

from metrics.cycling_vam_mtb.extractor import extract as extract_mtb
from metrics.cycling_vam_road.extractor import extract as extract_road
from metrics.vam_at_hr_zone.calculator import calculate


ZONES = {
    "id": "zones_test",
    "zones": [
        {"id": "Z2", "lower_bpm": 120, "upper_bpm": 139},
        {"id": "Z3", "lower_bpm": 140, "upper_bpm": 159},
    ],
}


def cycling_activity(profile="Bici", heart_rate=130, slope=7.5, seconds=300):
    start = datetime(2026, 7, 1, 8, 0, tzinfo=timezone.utc)
    speed = 4.0
    records = []
    for offset in range(seconds + 1):
        distance = speed * offset
        records.append({
            "timestamp": (start + timedelta(seconds=offset)).isoformat(),
            "distance_meters": distance,
            "altitude_meters": 500 + distance * slope / 100.0,
            "heart_rate_bpm": heart_rate,
            "speed_mps": speed,
        })
    return {
        "activity": {
            "sport": "cycling", "sport_profile_name": profile,
            "sub_sport": "generic", "date": "2026-07-01",
        },
        "records": records,
    }


def test_road_and_mtb_are_strictly_separated():
    road = cycling_activity("Bici")
    mtb = cycling_activity("MTB")
    assert extract_road(road, {}, ZONES)["status"] == "ready"
    assert extract_road(mtb, {}, ZONES)["status"] == "not_applicable"
    assert extract_mtb(mtb, {}, ZONES)["status"] == "ready"
    assert extract_mtb(road, {}, ZONES)["status"] == "not_applicable"


def test_cycling_vam_uses_five_to_ten_percent_and_z2_z3():
    for heart_rate, zone in ((130, "Z2"), (150, "Z3")):
        extracted = extract_road(
            cycling_activity(heart_rate=heart_rate, slope=7.5), {}, ZONES
        )
        segment = extracted["segments"][0]
        assert segment["heart_rate_zone_id"] == zone
        assert segment["slope_band"] == "5-10%"
        assert calculate(extracted)[0]["value"] > 0
    assert extract_road(cycling_activity(slope=4.9), {}, ZONES)["status"] == "no_qualifying_segments"
    assert extract_road(cycling_activity(slope=10.1), {}, ZONES)["status"] == "no_qualifying_segments"
    assert extract_road(cycling_activity(slope=5.0), {}, ZONES)["status"] == "ready"
    assert extract_road(cycling_activity(slope=10.0), {}, ZONES)["status"] == "ready"


def test_cycling_continuity_tolerates_short_stops_and_smart_recording():
    short_stop = cycling_activity()
    anchor_distance = short_stop["records"][99]["distance_meters"]
    anchor_altitude = short_stop["records"][99]["altitude_meters"]
    for record in short_stop["records"][100:106]:
        record["distance_meters"] = anchor_distance
        record["altitude_meters"] = anchor_altitude
        record["speed_mps"] = 0.0
    extracted = extract_road(short_stop, {}, ZONES)
    assert extracted["status"] == "ready"
    assert extracted["segments"][0]["stopped_time_seconds"] <= 10

    smart_recording = cycling_activity()
    smart_recording["records"] = [
        row for index, row in enumerate(smart_recording["records"])
        if not 100 <= index <= 108
    ]
    assert extract_road(smart_recording, {}, ZONES)["status"] == "ready"

    long_gap = cycling_activity()
    long_gap["records"] = [
        row for index, row in enumerate(long_gap["records"])
        if not 100 <= index <= 120
    ]
    assert extract_road(long_gap, {}, ZONES)["status"] == "no_qualifying_segments"
