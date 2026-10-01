"""Detect saved geographic segments inside normalized FIT activities."""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from config import PROJECT_ROOT
from backend.segment_store import (
    load_segment_attempts,
    load_segments,
    save_segment_attempts,
)
from backend.heart_rate_zone_classification import classify_observed_zone
from backend.activity_usage_store import permits


ACTIVITIES_DIR = PROJECT_ROOT / "garmin" / "activities"
START_END_TOLERANCE_M = 65.0
MEDIAN_ROUTE_TOLERANCE_M = 45.0
P90_ROUTE_TOLERANCE_M = 90.0
LENGTH_RATIO_RANGE = (0.82, 1.18)


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _haversine(lat1, lon1, lat2, lon2):
    """Vectorized great-circle distance in metres."""
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    value = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 6371000.0 * 2 * np.arctan2(np.sqrt(value), np.sqrt(np.maximum(0, 1 - value)))


def _discipline(activity: dict) -> tuple[str | None, str | None]:
    profile = activity.get("sport_profile_name")
    sport = activity.get("sport")
    discipline = {
        "Bici": "Bici", "Trail running": "Trail", "Corrida": "Corrida", "Kayak": "Kayak",
    }.get(profile) or {"cycling": "Bici", "running": "Corrida", "kayaking": "Kayak"}.get(sport)
    return discipline, activity.get("cycling_modality")


def _compatible(segment: dict, activity: dict) -> bool:
    discipline, modality = _discipline(activity)
    expected = segment.get("discipline")
    if expected not in {discipline, "Otra", None}:
        return False
    expected_modality = segment.get("cycling_modality")
    return not (expected == "Bici" and expected_modality in {"Ruta", "MTB"} and modality != expected_modality)


def _activity_arrays(payload: dict):
    rows = [
        item for item in payload.get("records", [])
        if all(_finite(item.get(key)) for key in ("latitude", "longitude", "distance_meters", "altitude_meters"))
        and item.get("timestamp")
    ]
    if len(rows) < 2:
        return None
    return {
        "rows": rows,
        "lat": np.array([row["latitude"] for row in rows], dtype=float),
        "lon": np.array([row["longitude"] for row in rows], dtype=float),
        "distance": np.array([row["distance_meters"] for row in rows], dtype=float),
    }


def _reference_arrays(segment: dict):
    points = segment.get("geometry", [])
    if len(points) < 2:
        return None
    return {
        "distance": np.array([point["distance_meters"] for point in points], dtype=float),
        "lat": np.array([point["latitude"] for point in points], dtype=float),
        "lon": np.array([point["longitude"] for point in points], dtype=float),
    }


def _cluster_minima(indices, distances):
    if not len(indices):
        return []
    clusters, current = [], [int(indices[0])]
    for value in indices[1:]:
        value = int(value)
        if value <= current[-1] + 3:
            current.append(value)
        else:
            clusters.append(current)
            current = [value]
    clusters.append(current)
    return [min(cluster, key=lambda index: distances[index]) for cluster in clusters]


def _route_quality(activity, reference, start_index, end_index):
    candidate_distance = activity["distance"][start_index:end_index + 1]
    relative = candidate_distance - candidate_distance[0]
    candidate_length = relative[-1]
    reference_length = reference["distance"][-1]
    if candidate_length <= 0 or reference_length <= 0:
        return None
    ratio = candidate_length / reference_length
    if not LENGTH_RATIO_RANGE[0] <= ratio <= LENGTH_RATIO_RANGE[1]:
        return None
    fractions = np.linspace(0, 1, 31)
    candidate_targets = fractions * candidate_length
    reference_targets = fractions * reference_length
    candidate_lat = np.interp(candidate_targets, relative, activity["lat"][start_index:end_index + 1])
    candidate_lon = np.interp(candidate_targets, relative, activity["lon"][start_index:end_index + 1])
    reference_lat = np.interp(reference_targets, reference["distance"], reference["lat"])
    reference_lon = np.interp(reference_targets, reference["distance"], reference["lon"])
    errors = _haversine(candidate_lat, candidate_lon, reference_lat, reference_lon)
    median, p90 = float(np.median(errors)), float(np.percentile(errors, 90))
    if median > MEDIAN_ROUTE_TOLERANCE_M or p90 > P90_ROUTE_TOLERANCE_M:
        return None
    return {"length_ratio": ratio, "median_error_m": median, "p90_error_m": p90}


def _attempt_metrics(rows: list[dict], start_index: int, end_index: int, activity_date: str | None):
    selected = rows[start_index:end_index + 1]
    first, last = selected[0], selected[-1]
    start_time = datetime.fromisoformat(first["timestamp"].replace("Z", "+00:00"))
    end_time = datetime.fromisoformat(last["timestamp"].replace("Z", "+00:00"))
    duration = max(0.0, (end_time - start_time).total_seconds())
    distance = max(0.0, float(last["distance_meters"] - first["distance_meters"]))
    elevations = np.array([row["altitude_meters"] for row in selected], dtype=float)
    deltas = np.diff(elevations)
    ascent, descent = float(deltas[deltas > 0].sum()), float(-deltas[deltas < 0].sum())
    net = float(elevations[-1] - elevations[0])
    heart_rates = [float(row["heart_rate_bpm"]) for row in selected if _finite(row.get("heart_rate_bpm"))]
    return {
        "start_time": first["timestamp"], "end_time": last["timestamp"],
        "duration_seconds": round(duration, 3), "distance_km": round(distance / 1000, 4),
        "speed_kmh": round(distance / duration * 3.6, 4) if duration else None,
        "ascent_m": round(ascent, 2), "descent_m": round(descent, 2), "net_m": round(net, 2),
        "slope_percent": round(100 * net / distance, 4) if distance else None,
        "vam_mh": round(max(0, net) / duration * 3600, 3) if duration else None,
        "heart_rate_mean": round(float(np.mean(heart_rates)), 2) if heart_rates else None,
        "heart_rate_max": round(float(np.max(heart_rates)), 1) if heart_rates else None,
        "heart_rate_zone": classify_observed_zone(selected, activity_date),
    }


def match_segment(segment: dict, payload: dict, source_file: str) -> list[dict[str, Any]]:
    if not _compatible(segment, payload.get("activity", {})):
        return []
    activity, reference = _activity_arrays(payload), _reference_arrays(segment)
    if activity is None or reference is None:
        return []
    start_errors = _haversine(activity["lat"], activity["lon"], reference["lat"][0], reference["lon"][0])
    start_candidates = _cluster_minima(np.where(start_errors <= START_END_TOLERANCE_M)[0], start_errors)
    expected_length = reference["distance"][-1]
    attempts, used_until = [], -1
    for start_index in start_candidates:
        if start_index <= used_until:
            continue
        travelled = activity["distance"] - activity["distance"][start_index]
        possible = np.where(
            (np.arange(len(travelled)) > start_index)
            & (travelled >= expected_length * LENGTH_RATIO_RANGE[0])
            & (travelled <= expected_length * LENGTH_RATIO_RANGE[1])
        )[0]
        if not len(possible):
            continue
        end_errors = _haversine(
            activity["lat"][possible], activity["lon"][possible], reference["lat"][-1], reference["lon"][-1]
        )
        valid = possible[end_errors <= START_END_TOLERANCE_M]
        if not len(valid):
            continue
        end_index = min(valid, key=lambda index: float(_haversine(
            activity["lat"][index], activity["lon"][index], reference["lat"][-1], reference["lon"][-1]
        )))
        quality = _route_quality(activity, reference, start_index, int(end_index))
        if quality is None:
            continue
        metrics = _attempt_metrics(
            activity["rows"], start_index, int(end_index), payload.get("activity", {}).get("date")
        )
        identity = f"{segment['id']}|{source_file}|{metrics['start_time']}"
        attempts.append({
            "id": "attempt_" + hashlib.sha256(identity.encode()).hexdigest()[:16],
            "segment_id": segment["id"], "activity_file": source_file,
            "activity_date": payload.get("activity", {}).get("date"),
            "discipline": _discipline(payload.get("activity", {}))[0],
            "cycling_modality": _discipline(payload.get("activity", {}))[1],
            "metrics": metrics,
            "match_quality": {key: round(float(value), 4) for key, value in quality.items()},
        })
        used_until = int(end_index)
    return attempts


def evaluate_activity_segments(activity_path: Path) -> dict[str, Any]:
    """Incrementally refresh attempts for one normalized FIT activity.

    Only attempts of active segments for this activity are replaced. This makes
    FIT ingestion fast while preserving attempts from every other activity and
    any inactive/deleted segment kept for catalog history.
    """
    path = Path(activity_path)
    segments = [item for item in load_segments() if item.get("active", True)]
    active_ids = {item["id"] for item in segments}
    previous = load_segment_attempts()
    retained = [
        item for item in previous
        if not (
            item.get("activity_file") == path.name
            and item.get("segment_id") in active_ids
        )
    ]
    previous_by_id = {item["id"]: item for item in previous}
    detected: list[dict[str, Any]] = []
    failure = None
    evaluated = 0
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        evaluated = 1
        if permits(path.name, "segments_routes"):
            for segment in segments:
                detected.extend(match_segment(segment, payload, path.name))
    except Exception as error:
        # Do not overwrite prior results if the newly extracted payload cannot
        # be read or matched correctly.
        return {
            "activity_file": path.name,
            "activities_evaluated": 0,
            "segments_evaluated": len(segments),
            "attempts_detected": 0,
            "failure": str(error),
        }

    # A manual zone classification belongs to the athlete, so keep it when a
    # deterministic refresh recreates the same attempt identity.
    for item in detected:
        old_zone = previous_by_id.get(item["id"], {}).get("metrics", {}).get("heart_rate_zone", {})
        if old_zone.get("zone_source") == "manual":
            item["metrics"]["heart_rate_zone"].update({
                "observed_zone": old_zone.get("observed_zone"),
                "target_zone": old_zone.get("target_zone"),
                "zone_source": "manual",
            })
    save_segment_attempts(retained + detected)
    return {
        "activity_file": path.name,
        "activities_evaluated": evaluated,
        "segments_evaluated": len(segments),
        "attempts_detected": len(detected),
        "failure": failure,
    }


def evaluate_historical_segments() -> dict[str, Any]:
    segments = [item for item in load_segments() if item.get("active", True)]
    paths = sorted(ACTIVITIES_DIR.glob("activity_*.json")) if ACTIVITIES_DIR.exists() else []
    previous = {item["id"]: item for item in load_segment_attempts()}
    evaluated, failures, detected = 0, [], []
    for path in paths:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            evaluated += 1
            if not permits(path.name, "segments_routes"):
                continue
            for segment in segments:
                detected.extend(match_segment(segment, payload, path.name))
        except Exception as error:
            failures.append({"activity_file": path.name, "error": str(error)})
    # Active segments are recalculated deterministically; attempts belonging to
    # inactive/deleted segments are retained until the catalog explicitly removes them.
    active_ids = {item["id"] for item in segments}
    retained = [item for item in previous.values() if item.get("segment_id") not in active_ids]
    # Preserve manual classification when deterministic detection recreates an attempt.
    for item in detected:
        old_zone = previous.get(item["id"], {}).get("metrics", {}).get("heart_rate_zone", {})
        if old_zone.get("zone_source") == "manual":
            item["metrics"]["heart_rate_zone"].update({
                "observed_zone": old_zone.get("observed_zone"),
                "target_zone": old_zone.get("target_zone"),
                "zone_source": "manual",
            })
    combined = retained + detected
    save_segment_attempts(combined)
    return {
        "segments_evaluated": len(segments), "activities_evaluated": evaluated,
        "attempts_detected": len(detected), "failures": failures,
    }
