"""Automatic grouping and comparison of repeated complete FIT routes."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from config import PROJECT_ROOT
from garmin.cycling_modality import classify_cycling_modality
from backend.heart_rate_zone_classification import classify_observed_zone
from backend.activity_usage_store import permits


ACTIVITIES_DIR = PROJECT_ROOT / "garmin" / "activities"
ROUTES_FILE = PROJECT_ROOT / "data" / "segments" / "route_families.json"
DISTANCE_RATIO_RANGE = (0.90, 1.10)
ENDPOINT_TOLERANCE_M = 250.0
# Complete routes may follow the same practical course while their GPS traces
# diverge around corners, tree cover or a slightly different line. Endpoint,
# distance-ratio and p90 checks remain in force; this wider median threshold
# avoids splitting those repeated routes into artificial families.
ORDERED_MEDIAN_TOLERANCE_M = 200.0
ORDERED_P90_TOLERANCE_M = 220.0


def _haversine(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    value = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 6371000.0 * 2 * np.arctan2(np.sqrt(value), np.sqrt(np.maximum(0, 1 - value)))


def _discipline(activity: dict) -> tuple[str | None, str | None]:
    profile, sport = activity.get("sport_profile_name"), activity.get("sport")
    discipline = {
        "Bici": "Bici", "Trail running": "Trail", "Corrida": "Corrida", "Kayak": "Kayak",
    }.get(profile) or {"cycling": "Bici", "running": "Corrida", "kayaking": "Kayak"}.get(sport)
    modality = activity.get("cycling_modality")
    if discipline == "Bici" and not modality:
        modality = classify_cycling_modality(activity)
    return discipline, modality


def load_route_families() -> list[dict[str, Any]]:
    if not ROUTES_FILE.exists():
        return []
    payload = json.loads(ROUTES_FILE.read_text(encoding="utf-8"))
    values = payload.get("routes", [])
    if not isinstance(values, list):
        raise ValueError("route_families.json: 'routes' debe ser una lista.")
    return values


def _write(values: list[dict[str, Any]]) -> None:
    ROUTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = ROUTES_FILE.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"routes": values}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(ROUTES_FILE)


def save_route_family(value: dict[str, Any]) -> None:
    values = load_route_families()
    for index, existing in enumerate(values):
        if existing.get("id") == value.get("id"):
            values[index] = value
            _write(values)
            return
    raise KeyError(f"Familia de ruta desconocida: {value.get('id')}")


def _signature(payload: dict, source_file: str):
    activity = payload.get("activity", {})
    rows = [
        row for row in payload.get("records", [])
        if all(isinstance(row.get(key), (int, float)) for key in ("latitude", "longitude", "distance_meters"))
    ]
    if len(rows) < 10:
        return None
    distances = np.array([row["distance_meters"] for row in rows], dtype=float)
    valid = np.r_[True, np.diff(distances) >= 0]
    distances = distances[valid]
    lat = np.array([row["latitude"] for row in rows], dtype=float)[valid]
    lon = np.array([row["longitude"] for row in rows], dtype=float)[valid]
    altitude = np.array([
        row.get("altitude_meters") if isinstance(row.get("altitude_meters"), (int, float)) else np.nan
        for row in rows
    ], dtype=float)[valid]
    if np.isnan(altitude).all():
        altitude = np.zeros_like(distances)
    else:
        known = np.where(~np.isnan(altitude))[0]
        altitude = np.interp(np.arange(len(altitude)), known, altitude[known])
    total = float(distances[-1] - distances[0])
    if total < 500:
        return None
    relative = distances - distances[0]
    targets = np.linspace(0, total, min(151, max(31, int(total / 250))))
    sampled_lat = np.interp(targets, relative, lat)
    sampled_lon = np.interp(targets, relative, lon)
    sampled_altitude = np.interp(targets, relative, altitude)
    discipline, modality = _discipline(activity)
    if discipline is None:
        return None
    return {
        "source_file": source_file,
        "date": activity.get("date"),
        "discipline": discipline,
        "cycling_modality": modality,
        "distance_m": total,
        "lat": sampled_lat,
        "lon": sampled_lon,
        "geometry": [
            {"distance_meters": round(float(distance), 1), "latitude": round(float(latitude), 7),
             "longitude": round(float(longitude), 7), "altitude_meters": round(float(elevation), 1)}
            for distance, latitude, longitude, elevation in zip(
                targets, sampled_lat, sampled_lon, sampled_altitude
            )
        ],
    }


def _reference_signature(family: dict):
    geometry = family.get("geometry", [])
    return {
        "discipline": family.get("discipline"),
        "cycling_modality": family.get("cycling_modality"),
        "distance_m": float(family.get("distance_km", 0)) * 1000,
        "lat": np.array([point["latitude"] for point in geometry], dtype=float),
        "lon": np.array([point["longitude"] for point in geometry], dtype=float),
    }


def _similarity(reference, candidate):
    if reference["discipline"] != candidate["discipline"]:
        return None
    if reference["discipline"] == "Bici" and reference.get("cycling_modality") != candidate.get("cycling_modality"):
        return None
    ratio = candidate["distance_m"] / reference["distance_m"] if reference["distance_m"] else 0
    if not DISTANCE_RATIO_RANGE[0] <= ratio <= DISTANCE_RATIO_RANGE[1]:
        return None
    start_error = float(_haversine(reference["lat"][0], reference["lon"][0], candidate["lat"][0], candidate["lon"][0]))
    end_error = float(_haversine(reference["lat"][-1], reference["lon"][-1], candidate["lat"][-1], candidate["lon"][-1]))
    if max(start_error, end_error) > ENDPOINT_TOLERANCE_M:
        return None
    fractions = np.linspace(0, 1, 61)
    ref_axis = np.linspace(0, 1, len(reference["lat"]))
    can_axis = np.linspace(0, 1, len(candidate["lat"]))
    errors = _haversine(
        np.interp(fractions, ref_axis, reference["lat"]),
        np.interp(fractions, ref_axis, reference["lon"]),
        np.interp(fractions, can_axis, candidate["lat"]),
        np.interp(fractions, can_axis, candidate["lon"]),
    )
    median, p90 = float(np.median(errors)), float(np.percentile(errors, 90))
    if median > ORDERED_MEDIAN_TOLERANCE_M or p90 > ORDERED_P90_TOLERANCE_M:
        return None
    score = median + 0.35 * p90 + 0.1 * (start_error + end_error)
    return {
        "score": score, "distance_ratio": ratio, "median_error_m": median,
        "p90_error_m": p90, "start_error_m": start_error, "end_error_m": end_error,
    }


def _attempt(payload: dict, signature: dict, quality: dict | None):
    activity, summary = payload.get("activity", {}), payload.get("summary", {})
    duration = activity.get("duration_seconds")
    elapsed = activity.get("elapsed_duration_seconds")
    distance_km = signature["distance_m"] / 1000
    return {
        "activity_file": signature["source_file"], "date": signature.get("date"),
        "distance_km": round(distance_km, 3), "duration_seconds": duration,
        "elapsed_seconds": elapsed,
        "paused_seconds": round(max(0, elapsed - duration), 2)
        if isinstance(elapsed, (int, float)) and isinstance(duration, (int, float)) else None,
        "paused_share": round(max(0, elapsed - duration) / elapsed, 4)
        if isinstance(elapsed, (int, float)) and elapsed > 0 and isinstance(duration, (int, float)) else None,
        "speed_kmh": round(distance_km / (duration / 3600), 3)
        if isinstance(duration, (int, float)) and duration > 0 else None,
        "ascent_m": activity.get("ascent_meters"), "descent_m": activity.get("descent_meters"),
        "heart_rate_mean": summary.get("average_heart_rate_bpm"),
        "heart_rate_max": summary.get("maximum_heart_rate_bpm"),
        "heart_rate_zone": classify_observed_zone(
            payload.get("records", []), signature.get("date")
        ),
        "match_quality": {key: round(float(value), 4) for key, value in (quality or {}).items()},
    }


def _new_family(payload: dict, signature: dict):
    identity = payload.get("activity", {}).get("source_sha256") or signature["source_file"]
    route_id = "route_" + hashlib.sha256(identity.encode()).hexdigest()[:14]
    modality = signature.get("cycling_modality")
    category = "Bici de ruta" if modality == "Ruta" else "Mountain Bike" if modality == "MTB" else signature["discipline"]
    return {
        "id": route_id,
        "name": f"{category} · {signature['distance_m'] / 1000:.1f} km",
        "discipline": signature["discipline"], "cycling_modality": modality,
        "active": True, "representative_activity": signature["source_file"],
        "distance_km": round(signature["distance_m"] / 1000, 3),
        "geometry": signature["geometry"], "attempts": [_attempt(payload, signature, None)],
    }


def rebuild_route_families() -> dict[str, Any]:
    previous_families = load_route_families()
    old_names = {item.get("representative_activity"): item.get("name") for item in previous_families}
    manual_zones = {
        attempt.get("activity_file"): attempt.get("heart_rate_zone")
        for family in previous_families for attempt in family.get("attempts", [])
        if attempt.get("heart_rate_zone", {}).get("zone_source") == "manual"
    }
    families, failures, evaluated = [], [], 0
    paths = sorted(ACTIVITIES_DIR.glob("activity_*.json")) if ACTIVITIES_DIR.exists() else []
    for path in paths:
        if not permits(path.name, "segments_routes"):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            signature = _signature(payload, path.name)
            if signature is None:
                continue
            evaluated += 1
            candidates = []
            for index, family in enumerate(families):
                quality = _similarity(_reference_signature(family), signature)
                if quality is not None:
                    candidates.append((quality["score"], index, quality))
            if candidates:
                _, index, quality = min(candidates)
                families[index]["attempts"].append(_attempt(payload, signature, quality))
            else:
                family = _new_family(payload, signature)
                if signature["source_file"] in old_names:
                    family["name"] = old_names[signature["source_file"]]
                families.append(family)
        except Exception as error:
            failures.append({"activity_file": path.name, "error": str(error)})
    for family in families:
        for attempt in family.get("attempts", []):
            manual = manual_zones.get(attempt.get("activity_file"))
            if manual:
                attempt["heart_rate_zone"].update({
                    "observed_zone": manual.get("observed_zone"),
                    "target_zone": manual.get("target_zone"),
                    "zone_source": "manual",
                })
    _write(families)
    repeated = sum(len(item.get("attempts", [])) >= 2 for item in families)
    return {"activities_evaluated": evaluated, "families": len(families), "repeated_families": repeated, "failures": failures}


def update_route_families() -> dict[str, Any]:
    """Assign only activities not already present; rebuild when no catalog exists."""
    families = load_route_families()
    if not families:
        return rebuild_route_families()
    known = {attempt.get("activity_file") for family in families for attempt in family.get("attempts", [])}
    new_count, failures = 0, []
    for path in sorted(ACTIVITIES_DIR.glob("activity_*.json")):
        if path.name in known:
            continue
        if not permits(path.name, "segments_routes"):
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            signature = _signature(payload, path.name)
            if signature is None:
                continue
            candidates = []
            for index, family in enumerate(families):
                quality = _similarity(_reference_signature(family), signature)
                if quality is not None:
                    candidates.append((quality["score"], index, quality))
            if candidates:
                _, index, quality = min(candidates)
                families[index]["attempts"].append(_attempt(payload, signature, quality))
            else:
                families.append(_new_family(payload, signature))
            new_count += 1
        except Exception as error:
            failures.append({"activity_file": path.name, "error": str(error)})
    _write(families)
    return {"new_activities": new_count, "families": len(families), "failures": failures}
