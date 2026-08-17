"""Classify an activity interval by time spent in the athlete's HR zones."""

from __future__ import annotations

from datetime import datetime
import math

from backend.heart_rate_zones_store import profile_for_date


DOMINANT_SHARE = 0.60
MIN_VALID_SECONDS = 60.0


def classify_observed_zone(rows: list[dict], activity_date: str | None) -> dict:
    """Return a transparent, date-aware time-in-zone classification."""
    result = {
        "automatic_zone": None,
        "observed_zone": "Sin clasificar",
        "zone_source": "automatic",
        "zone_confidence": "Sin datos",
        "dominant_share": None,
        "valid_hr_seconds": 0.0,
        "time_in_zones_seconds": {},
        "zone_profile_id": None,
    }
    if not activity_date:
        return result
    profile = profile_for_date(activity_date)
    if not profile:
        return result
    result["zone_profile_id"] = profile.get("id")
    boundaries = {
        zone["id"]: (float(zone["lower_bpm"]), float(zone["upper_bpm"]))
        for zone in profile.get("zones", [])
    }
    durations = {zone_id: 0.0 for zone_id in boundaries}
    dated = []
    for row in rows:
        bpm, timestamp = row.get("heart_rate_bpm"), row.get("timestamp")
        if not isinstance(bpm, (int, float)) or not math.isfinite(bpm) or not timestamp:
            continue
        try:
            dated.append((datetime.fromisoformat(timestamp.replace("Z", "+00:00")), float(bpm)))
        except ValueError:
            continue
    for index, (timestamp, bpm) in enumerate(dated):
        if index + 1 < len(dated):
            seconds = max(0.0, min(10.0, (dated[index + 1][0] - timestamp).total_seconds()))
        else:
            seconds = 1.0
        for zone_id, (lower, upper) in boundaries.items():
            if lower <= bpm <= upper:
                durations[zone_id] += seconds
                break
    valid = sum(durations.values())
    result["valid_hr_seconds"] = round(valid, 1)
    result["time_in_zones_seconds"] = {
        key: round(value, 1) for key, value in durations.items() if value > 0
    }
    if valid < MIN_VALID_SECONDS or not durations:
        return result
    dominant_zone, dominant_seconds = max(durations.items(), key=lambda item: item[1])
    share = dominant_seconds / valid
    result["dominant_share"] = round(share, 4)
    if share >= DOMINANT_SHARE:
        result["automatic_zone"] = dominant_zone
        result["observed_zone"] = dominant_zone
        result["zone_confidence"] = "Alta" if share >= 0.75 else "Media"
    else:
        result["automatic_zone"] = "Mixto"
        result["observed_zone"] = "Mixto"
        result["zone_confidence"] = "Baja"
    return result
