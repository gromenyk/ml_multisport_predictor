"""Extract whole-activity inputs for Kayak Endurance Score."""

from __future__ import annotations

from datetime import datetime, timedelta
from math import isfinite


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value)


def extract(activity, assignment, zone_profile):
    metadata = activity.get("activity", {})
    sport = str(metadata.get("sport") or "").lower()
    profile = str(metadata.get("sport_profile_name") or "").lower()
    if sport != "kayaking" and profile != "kayak":
        return {"status": "not_applicable", "reason": "discipline_not_supported", "segments": []}
    distance_meters = metadata.get("distance_meters")
    duration_seconds = metadata.get("duration_seconds")
    average_speed_mps = activity.get("summary", {}).get("average_speed_mps")
    if not _finite(average_speed_mps) and _finite(distance_meters) and _finite(duration_seconds) and duration_seconds > 0:
        average_speed_mps = distance_meters / duration_seconds
    if not _finite(distance_meters) or distance_meters <= 0:
        return {"status": "incomplete", "reason": "distance_missing", "segments": []}
    if not _finite(average_speed_mps) or average_speed_mps <= 0:
        return {"status": "incomplete", "reason": "average_speed_missing", "segments": []}
    start_time = metadata.get("start_time") or f"{metadata.get('date')}T00:00:00"
    try:
        end_time = (
            datetime.fromisoformat(start_time.replace("Z", "+00:00"))
            + timedelta(seconds=float(duration_seconds or 0))
        ).isoformat()
    except (TypeError, ValueError):
        end_time = start_time
    return {
        "status": "ready",
        "discipline": "Kayak",
        "segments": [{
            "start_time": start_time,
            "end_time": end_time,
            "duration_seconds": float(duration_seconds) if _finite(duration_seconds) else None,
            "distance_meters": float(distance_meters),
            "distance_km": float(distance_meters) / 1000.0,
            "average_speed_mps": float(average_speed_mps),
            "average_speed_kmh": float(average_speed_mps) * 3.6,
        }],
        "candidate_rejection_counts": {},
    }
