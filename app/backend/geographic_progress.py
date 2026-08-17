"""Speed progress for repeated geographic segments and complete routes."""

from __future__ import annotations

from datetime import date, timedelta
from statistics import median
from typing import Any

from backend.activity_usage_store import permits
from backend.route_matching import load_route_families
from backend.segment_store import load_segment_attempts, load_segments


MIN_ATHLETE_STATE_ATTEMPTS = 5
REFERENCE_WINDOW_DAYS = 30


def _change(current, reference):
    return round(100.0 * (current / reference - 1.0), 2) if reference else None


def _status(value):
    if value is None:
        return "Sin referencia"
    if value >= 3:
        return "Mejora clara"
    if value >= 1:
        return "Mejora leve"
    if value > -1:
        return "Estable"
    if value > -3:
        return "Pérdida leve"
    return "Pérdida clara"


def _reference(records, target_date):
    candidates = [
        item for item in records
        if abs((item["date"] - target_date).days) <= REFERENCE_WINDOW_DAYS
    ]
    return round(median(item["speed_kmh"] for item in candidates), 3) if candidates else None


def _series(entity_type, entity, zone, records):
    records = sorted(records, key=lambda item: (item["date"], item.get("attempt_id", "")))
    latest = records[-1]
    recent = records[-3:]
    current_level = round(median(item["speed_kmh"] for item in recent), 3)
    previous = records[-2]["speed_kmh"] if len(records) >= 2 else None
    start_records = records[:min(3, len(records))]
    start_reference = round(median(item["speed_kmh"] for item in start_records), 3)
    comparisons = {
        "previous": {
            "label": "Intento anterior", "reference": previous,
            "change_percent": _change(latest["speed_kmh"], previous),
        },
        "three_months": {
            "label": "Hace 3 meses", "reference": _reference(records[:-1], latest["date"] - timedelta(days=90)),
        },
        "six_months": {
            "label": "Hace 6 meses", "reference": _reference(records[:-1], latest["date"] - timedelta(days=180)),
        },
        "one_year": {
            "label": "Hace 1 año", "reference": _reference(records[:-1], latest["date"] - timedelta(days=365)),
        },
        "start": {"label": "Desde el inicio", "reference": start_reference},
    }
    for key, item in comparisons.items():
        if key != "previous":
            item["change_percent"] = _change(current_level, item["reference"])
        item["status"] = _status(item.get("change_percent"))
    primary = comparisons["three_months"]
    if primary["change_percent"] is None:
        primary = comparisons["start"]
    return {
        "series_id": f"{entity_type}:{entity['id']}:{zone}",
        "entity_type": entity_type,
        "entity_id": entity["id"],
        "name": entity.get("name", "Sin nombre"),
        "discipline": entity.get("discipline"),
        "cycling_modality": entity.get("cycling_modality"),
        "zone": zone,
        "attempts": len(records),
        "eligible_for_athlete_state": len(records) >= MIN_ATHLETE_STATE_ATTEMPTS,
        "latest_date": latest["date"].isoformat(),
        "latest_speed_kmh": round(latest["speed_kmh"], 3),
        "current_speed_kmh": current_level,
        "primary_change_percent": primary.get("change_percent"),
        "primary_status": primary.get("status"),
        "primary_reference": primary.get("label"),
        "comparisons": comparisons,
        "observations": [
            {"date": item["date"].isoformat(), "speed_kmh": item["speed_kmh"]}
            for item in records
        ],
    }


def build_geographic_progress(as_of_date: date | str | None = None) -> dict[str, Any]:
    cutoff = date.fromisoformat(as_of_date) if isinstance(as_of_date, str) else as_of_date
    grouped: dict[tuple[str, str, str], list[dict]] = {}
    entities = {}
    segments = {item["id"]: item for item in load_segments() if item.get("active", True)}
    for attempt in load_segment_attempts():
        entity = segments.get(attempt.get("segment_id"))
        speed = attempt.get("metrics", {}).get("speed_kmh")
        date_text = attempt.get("activity_date")
        zone = attempt.get("metrics", {}).get("heart_rate_zone", {}).get("observed_zone") or "Sin clasificar"
        if not entity or not isinstance(speed, (int, float)) or not date_text:
            continue
        if not permits(attempt.get("activity_file"), "segments_routes"):
            continue
        attempt_date = date.fromisoformat(date_text)
        if cutoff and attempt_date > cutoff:
            continue
        key = ("segment", entity["id"], zone)
        entities[key] = entity
        grouped.setdefault(key, []).append({
            "date": attempt_date, "speed_kmh": float(speed), "attempt_id": attempt.get("id", ""),
        })
    for route in load_route_families():
        if not route.get("active", True):
            continue
        for attempt in route.get("attempts", []):
            speed, date_text = attempt.get("speed_kmh"), attempt.get("date")
            zone = attempt.get("heart_rate_zone", {}).get("observed_zone") or "Sin clasificar"
            if not isinstance(speed, (int, float)) or not date_text:
                continue
            if not permits(attempt.get("activity_file"), "segments_routes"):
                continue
            attempt_date = date.fromisoformat(date_text)
            if cutoff and attempt_date > cutoff:
                continue
            key = ("route", route["id"], zone)
            entities[key] = route
            grouped.setdefault(key, []).append({
                "date": attempt_date, "speed_kmh": float(speed),
                "attempt_id": attempt.get("activity_file", ""),
            })
    series = [
        _series(entity_type, entities[key], zone, records)
        for key, records in grouped.items()
        for entity_type, _, zone in [key]
        if records
    ]
    series.sort(key=lambda item: (not item["eligible_for_athlete_state"], item["entity_type"], item["name"], item["zone"]))
    eligible = [
        item for item in series
        if item["eligible_for_athlete_state"] and item["zone"] not in {"Mixto", "Sin clasificar"}
    ]
    return {
        "minimum_attempts": MIN_ATHLETE_STATE_ATTEMPTS,
        "series": series,
        "eligible_series": eligible,
        "summary": {
            "eligible": len(eligible),
            "improving": sum((item.get("primary_change_percent") or 0) >= 1 for item in eligible),
            "stable": sum(item.get("primary_change_percent") is not None and -1 < item["primary_change_percent"] < 1 for item in eligible),
            "declining": sum((item.get("primary_change_percent") or 0) <= -1 for item in eligible),
        },
    }
