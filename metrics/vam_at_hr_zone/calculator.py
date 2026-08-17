"""Calculate VAM for qualifying standardized uphill segments."""

from __future__ import annotations

from typing import Any


CALCULATION_VERSION = 3


def calculate(extracted: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for segment in extracted.get("segments", []):
        duration_hours = segment["duration_seconds"] / 3600.0
        vam = segment["net_elevation_gain_meters"] / duration_hours
        enriched = {**segment, "vam_m_per_hour": vam}
        results.append(
            {
                "status": "complete",
                "value": vam,
                "unit": "m/h",
                "calculation_version": CALCULATION_VERSION,
                "segment": enriched,
            }
        )
    return results
