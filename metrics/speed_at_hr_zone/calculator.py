"""Calculate mean speed for extracted qualifying segments."""

from __future__ import annotations

from typing import Any


CALCULATION_VERSION = 3


def calculate(extracted: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for segment in extracted.get("segments", []):
        hours = segment["duration_seconds"] / 3600.0
        value = (segment["distance_meters"] / 1000.0) / hours
        results.append(
            {
                "status": "complete",
                "value": value,
                "unit": "km/h",
                "calculation_version": CALCULATION_VERSION,
                "segment": segment,
            }
        )
    return results
