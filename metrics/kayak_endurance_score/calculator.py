"""Calculate Kayak Endurance Score from distance and average speed."""

from __future__ import annotations


CALCULATION_VERSION = 1


def calculate(extracted):
    results = []
    for segment in extracted.get("segments", []):
        score = segment["distance_km"] * segment["average_speed_kmh"]
        results.append({
            "status": "complete",
            "value": score,
            "unit": "km²/h",
            "calculation_version": CALCULATION_VERSION,
            "segment": {**segment, "kayak_endurance_score": score},
        })
    return results
