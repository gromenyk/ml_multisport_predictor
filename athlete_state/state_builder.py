"""Assemble training, recovery, progress and planning state for one date."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
from statistics import mean, pstdev
from typing import Any
from uuid import uuid4

from metrics.freshness import metric_freshness
from metrics.aggregation import activity_medians
from adaptive_planning.progression_memory import build_progression_memory
from backend.activity_usage_store import permits
from backend.geographic_progress import build_geographic_progress


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOAD_FILE = PROJECT_ROOT / "data" / "derived" / "training_load" / "performed_load.json"
WELLNESS_FILE = PROJECT_ROOT / "data" / "athlete_state" / "daily_wellness.json"
CONTEXT_FILE = PROJECT_ROOT / "data" / "athlete_state" / "athlete_context.json"
AVAILABILITY_FILE = PROJECT_ROOT / "data" / "athlete_state" / "availability_calendar.json"
SESSION_FEEDBACK_FILE = PROJECT_ROOT / "data" / "athlete_state" / "session_feedback.json"
MEASUREMENTS_FILE = PROJECT_ROOT / "data" / "objectives" / "metric_measurements.json"
METRICS_FILE = PROJECT_ROOT / "data" / "objectives" / "metric_catalog.json"
EVENTS_FILE = PROJECT_ROOT / "data" / "objectives" / "events.json"
WINDOWS_FILE = PROJECT_ROOT / "data" / "objectives" / "calendar_windows.json"
SNAPSHOT_DIR = PROJECT_ROOT / "data" / "athlete_state" / "snapshots"
SEGMENT_CATALOG_FILE = PROJECT_ROOT / "data" / "segments" / "segment_catalog.json"
SEGMENT_ATTEMPTS_FILE = PROJECT_ROOT / "data" / "segments" / "segment_attempts.json"
ROUTE_FAMILIES_FILE = PROJECT_ROOT / "data" / "segments" / "route_families.json"
RECENT_METRIC_TREND_DAYS = 90
MIN_RECENT_METRIC_ACTIVITIES = 4


def _json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _average(values):
    valid = [float(value) for value in values if _finite(value)]
    return mean(valid) if valid else None


def _latest_on_or_before(records, as_of):
    applicable = [item for item in records if item.get("date", "") <= as_of.isoformat()]
    return max(applicable, key=lambda item: item["date"]) if applicable else None


def _linear_trend_per_30d(rows):
    """Fit a time trend to one already-summarized observation per activity."""
    if len(rows) < 2:
        return None
    origin = date.fromisoformat(rows[0]["date"])
    x_values = [(date.fromisoformat(item["date"]) - origin).days for item in rows]
    y_values = [float(item["value"]) for item in rows]
    x_mean, y_mean = mean(x_values), mean(y_values)
    denominator = sum((value - x_mean) ** 2 for value in x_values)
    if denominator <= 0:
        return None
    return 30 * sum(
        (x_value - x_mean) * (y_value - y_mean)
        for x_value, y_value in zip(x_values, y_values)
    ) / denominator


def _active_window(as_of):
    windows = _json(WINDOWS_FILE, {"windows": []}).get("windows", [])
    active = [
        item for item in windows
        if item.get("start_date", "") <= as_of.isoformat() <= item.get("end_date", "")
    ]
    return max(active, key=lambda item: item.get("start_date", "")) if active else None


def _date_range(as_of: date, days: int):
    return [as_of - timedelta(days=offset) for offset in reversed(range(days))]


def _training_state(as_of: date) -> dict[str, Any]:
    payload = _json(LOAD_FILE, {"daily_loads": {}, "activities": []})
    daily = payload.get("daily_loads", {})

    def load_on(day):
        return float(daily.get(day.isoformat(), {}).get("calculated_load") or 0)

    loads_7 = [load_on(day) for day in _date_range(as_of, 7)]
    previous_7 = [load_on(day) for day in _date_range(as_of - timedelta(days=7), 7)]
    loads_42 = [load_on(day) for day in _date_range(as_of, 42)]
    load_timeline = []
    for timeline_day in _date_range(as_of, 90):
        acute_at_day = sum(
            load_on(day) for day in _date_range(timeline_day, 7)
        )
        chronic_at_day = sum(
            load_on(day) for day in _date_range(timeline_day, 42)
        ) / 6.0
        load_timeline.append(
            {
                "date": timeline_day.isoformat(),
                "daily_load": load_on(timeline_day),
                "acute_load_7d": acute_at_day,
                "chronic_load_42d_equivalent": chronic_at_day,
            }
        )
    acute = sum(loads_7)
    previous = sum(previous_7)
    chronic_equivalent = sum(loads_42) / 6.0
    monotony = mean(loads_7) / pstdev(loads_7) if pstdev(loads_7) > 0 else None

    zone_minutes = defaultdict(float)
    discipline_load = defaultdict(float)
    discipline_load_7d = defaultdict(float)
    discipline_activity_records = defaultdict(list)
    cycling_modality_load = defaultdict(float)
    pause_records = []
    running_distance = running_ascent = running_descent = 0.0
    intensity_dates = []
    activity_dates = []
    cutoff_28 = as_of - timedelta(days=27)
    for activity in payload.get("activities", []):
        activity_date_text = activity.get("date")
        if not activity_date_text:
            continue
        activity_date = date.fromisoformat(activity_date_text)
        if activity_date > as_of:
            continue
        activity_dates.append(activity_date)
        activity_details = activity.get("activity", {})
        activity_file = activity.get("source", {}).get("activity_json")
        paused_seconds = activity_details.get("paused_duration_seconds")
        elapsed_seconds = activity_details.get("elapsed_duration_seconds")
        if (
            permits(activity_file, "pauses")
            and _finite(paused_seconds) and _finite(elapsed_seconds) and elapsed_seconds > 0
        ):
            pause_records.append({
                "date": activity_date.isoformat(),
                "activity_id": activity.get("activity_id"),
                "discipline": activity_details.get("sport", "Desconocida"),
                "cycling_modality": activity_details.get("cycling_modality"),
                "elapsed_duration_seconds": float(elapsed_seconds),
                "timer_duration_seconds": float(activity_details.get("duration_seconds") or 0),
                "paused_duration_seconds": float(paused_seconds),
                "paused_time_share": float(paused_seconds) / float(elapsed_seconds),
            })
        if activity_date < cutoff_28:
            continue
        sport = activity_details.get("sport", "Desconocida")
        activity_load = float(activity.get("load", {}).get("calculated_load") or 0)
        if permits(activity_file, "volume"):
            discipline_activity_records[sport].append({
                "date": activity_date,
                "distance_km": float(activity_details.get("distance_km") or 0),
                "load": activity_load,
            })
        discipline_load[sport] += activity_load
        if activity_date >= as_of - timedelta(days=6):
            discipline_load_7d[sport] += activity_load
        if sport == "Bici":
            modality = activity_details.get("cycling_modality") or "Desconocida"
            cycling_modality_load[modality] += activity_load
        high_intensity = 0.0
        for component in activity.get("load", {}).get("components", []):
            zone = component.get("zone")
            minutes = float(component.get("total_duration_minutes") or 0)
            if permits(activity_file, "zones"):
                zone_minutes[zone] += minutes
            if zone in {"Z3", "Z4", "Z5", "Z6"}:
                high_intensity += minutes
        if high_intensity > 0:
            intensity_dates.append(activity_date)
        if sport in {"Corrida", "Trail"} and permits(activity_file, "volume"):
            running_distance += float(activity_details.get("distance_km") or 0)
            running_ascent += float(activity_details.get("ascent_meters") or 0)
            running_descent += float(activity_details.get("descent_meters") or 0)

    def pause_summary(records):
        elapsed = sum(item["elapsed_duration_seconds"] for item in records)
        paused = sum(item["paused_duration_seconds"] for item in records)
        return {
            "activities": len(records),
            "elapsed_hours": elapsed / 3600.0,
            "paused_minutes": paused / 60.0,
            "paused_time_share": paused / elapsed if elapsed > 0 else None,
        }

    recent_pause = [
        item for item in pause_records
        if date.fromisoformat(item["date"]) >= as_of - timedelta(days=6)
    ]
    baseline_pause = [
        item for item in pause_records
        if as_of - timedelta(days=48) <= date.fromisoformat(item["date"]) <= as_of - timedelta(days=7)
    ]
    pause_by_discipline = {}
    for discipline in sorted({item["discipline"] for item in pause_records}):
        applicable = [
            item for item in pause_records
            if item["discipline"] == discipline
            and date.fromisoformat(item["date"]) >= as_of - timedelta(days=27)
        ]
        if applicable:
            pause_by_discipline[discipline] = pause_summary(applicable)
    recent_pause_summary = pause_summary(recent_pause)
    baseline_pause_summary = pause_summary(baseline_pause)
    recent_share = recent_pause_summary["paused_time_share"]
    baseline_share = baseline_pause_summary["paused_time_share"]

    discipline_progression = {}
    for discipline, records in discipline_activity_records.items():
        recent_records = [
            item for item in records if item["date"] >= cutoff_28
        ]
        if not recent_records:
            continue
        weekly = []
        for block in range(4):
            block_end = as_of - timedelta(days=7 * block)
            block_start = block_end - timedelta(days=6)
            block_records = [
                item for item in recent_records
                if block_start <= item["date"] <= block_end
            ]
            weekly.append({
                "end_date": block_end,
                "distance_km": sum(item["distance_km"] for item in block_records),
                "longest_km": max(
                    (item["distance_km"] for item in block_records), default=0
                ),
            })
        last_progression = None
        for index in range(3):
            newer, older = weekly[index], weekly[index + 1]
            volume_progressed = (
                older["distance_km"] > 0
                and newer["distance_km"] > older["distance_km"] * 1.05
            )
            long_progressed = (
                older["longest_km"] > 0
                and newer["longest_km"] > older["longest_km"] * 1.05
            )
            if volume_progressed or long_progressed:
                last_progression = newer["end_date"]
                break
        longest = max(recent_records, key=lambda item: item["distance_km"])
        discipline_progression[discipline] = {
            "distance_28d_km": sum(
                item["distance_km"] for item in recent_records
            ),
            "load_28d": sum(item["load"] for item in recent_records),
            "longest_session_28d_km": longest["distance_km"],
            "longest_session_date": longest["date"].isoformat(),
            "last_activity_date": max(
                item["date"] for item in recent_records
            ).isoformat(),
            "last_progression_date": (
                last_progression.isoformat() if last_progression else None
            ),
            "days_since_progression": (
                (as_of - last_progression).days if last_progression else None
            ),
        }

    return {
        "acute_load_7d": acute,
        "previous_load_7d": previous,
        "chronic_load_42d_equivalent": chronic_equivalent,
        "load_balance": chronic_equivalent - acute,
        "ramp_rate_7d": ((acute - previous) / previous if previous > 0 else None),
        "monotony_7d": monotony,
        "strain_7d": acute * monotony if monotony is not None else None,
        "daily_load_7d": [
            {"date": day.isoformat(), "load": load_on(day)}
            for day in _date_range(as_of, 7)
        ],
        "load_timeline_90d": load_timeline,
        "zone_minutes_28d": dict(sorted(zone_minutes.items())),
        "discipline_load_28d": dict(sorted(discipline_load.items())),
        "discipline_load_7d": dict(sorted(discipline_load_7d.items())),
        "discipline_progression_28d": dict(sorted(discipline_progression.items())),
        "cycling_modality_load_28d": dict(sorted(cycling_modality_load.items())),
        "session_continuity": {
            "recent_7d": recent_pause_summary,
            "baseline_previous_42d": baseline_pause_summary,
            "change_percentage_points": (
                100.0 * (recent_share - baseline_share)
                if recent_share is not None and baseline_share is not None else None
            ),
            "by_discipline_28d": pause_by_discipline,
            "activities_28d": [
                item for item in pause_records
                if date.fromisoformat(item["date"]) >= as_of - timedelta(days=27)
            ],
        },
        "running_mechanical_28d": {
            "distance_km": running_distance,
            "ascent_m": running_ascent,
            "descent_m": running_descent,
        },
        "days_since_activity": ((as_of - max(activity_dates)).days if activity_dates else None),
        "days_since_intensity": ((as_of - max(intensity_dates)).days if intensity_dates else None),
        "source_generated_at": payload.get("generated_at"),
    }


def _recovery_state(as_of: date) -> dict[str, Any]:
    records = [
        item for item in _json(WELLNESS_FILE, {"records": []}).get("records", [])
        if item.get("date", "") <= as_of.isoformat()
    ]
    latest = _latest_on_or_before(records, as_of)
    if latest:
        latest = dict(latest)
        # Compatibilidad: los registros antiguos llamaban "dolor muscular" a
        # esta percepción. No se modifica el archivo histórico.
        if latest.get("muscle_fatigue") is None:
            latest["muscle_fatigue"] = latest.get("muscle_soreness")
    last_7_start = (as_of - timedelta(days=6)).isoformat()
    last_7 = [item for item in records if item["date"] >= last_7_start]
    baseline_start = (as_of - timedelta(days=60)).isoformat()
    baseline_end = (as_of - timedelta(days=1)).isoformat()
    baseline = [item for item in records if baseline_start <= item["date"] <= baseline_end]
    keys = (
        "sleep_duration_hours", "sleep_score", "hrv_ms", "resting_hr_bpm",
        "subjective_fatigue", "muscle_fatigue",
    )
    normalized_last_7 = []
    for item in last_7:
        normalized = dict(item)
        if normalized.get("muscle_fatigue") is None:
            normalized["muscle_fatigue"] = normalized.get("muscle_soreness")
        normalized_last_7.append(normalized)
    averages_7 = {key: _average(item.get(key) for item in normalized_last_7) for key in keys}
    baselines = {key: _average(item.get(key) for item in baseline) for key in keys}
    deviations = {
        key: (
            latest.get(key) - baselines[key]
            if latest and _finite(latest.get(key)) and _finite(baselines[key])
            else None
        )
        for key in keys
    }
    weekly_deviations = {
        key: (
            averages_7[key] - baselines[key]
            if _finite(averages_7.get(key)) and _finite(baselines.get(key))
            else None
        )
        for key in keys
    }
    return {
        "latest": latest,
        "averages_7d": averages_7,
        "personal_baseline_60d": baselines,
        "latest_deviation": deviations,
        # The latest night remains available for daily review. Weekly planning
        # uses a seven-day trend so a single observation cannot trigger a
        # whole-week reduction.
        "weekly_deviation": weekly_deviations,
        "records_7d": len(last_7),
        "records_total": len(records),
        "fatigue": {
            "general": {
                "latest": latest.get("subjective_fatigue") if latest else None,
                "average_7d": averages_7.get("subjective_fatigue"),
            },
            "muscular": {
                "latest": latest.get("muscle_fatigue") if latest else None,
                "average_7d": averages_7.get("muscle_fatigue"),
            },
        },
    }


def _progress_state(as_of: date) -> dict[str, Any]:
    measurements = [
        item for item in _json(MEASUREMENTS_FILE, {"measurements": []}).get("measurements", [])
        if item.get("date", "") <= as_of.isoformat()
    ]
    catalog = {
        item["id"]: item
        for item in _json(METRICS_FILE, {"metrics": []}).get("metrics", [])
    }
    groups = defaultdict(list)
    for item in measurements:
        metric_definition = catalog.get(item.get("metric_id"), {})
        series_dimensions = metric_definition.get("series_dimensions", [])
        if (
            "heart_rate_zone_id" in series_dimensions
            and item.get("heart_rate_zone_id")
            not in metric_definition.get("heart_rate_zone_ids", [])
        ):
            continue
        if (
            "slope_band" in series_dimensions
            and item.get("components", {}).get("slope_band")
            not in metric_definition.get("slope_bands", [])
        ):
            continue
        groups[
            (
                item.get("metric_id"),
                (
                    item.get("discipline")
                    if metric_definition.get("separate_by_discipline", True)
                    else None
                ),
                (
                    item.get("heart_rate_zone_id")
                    if "heart_rate_zone_id" in series_dimensions else None
                ),
                (
                    item.get("components", {}).get("slope_band")
                    if "slope_band" in series_dimensions else None
                ),
            )
        ].append(item)
    active_window = _active_window(as_of)
    result = {}
    for (metric_id, discipline, heart_rate_zone_id, slope_band), rows in groups.items():
        rows.sort(key=lambda item: (item["date"], item.get("components", {}).get("start_time", "")))
        summarized = activity_medians(rows)
        historical_trend = _linear_trend_per_30d(summarized)
        recent_cutoff = as_of - timedelta(days=RECENT_METRIC_TREND_DAYS)
        decision_rows = [
            item for item in summarized
            if date.fromisoformat(item["date"]) >= recent_cutoff
        ]
        decision_source = f"últimos {RECENT_METRIC_TREND_DAYS} días"
        if len(decision_rows) < MIN_RECENT_METRIC_ACTIVITIES and active_window:
            window_rows = [
                item for item in summarized
                if active_window["start_date"] <= item["date"] <= active_window["end_date"]
            ]
            if len(window_rows) >= MIN_RECENT_METRIC_ACTIVITIES:
                decision_rows = window_rows
                decision_source = f"ventana activa: {active_window.get('name', 'sin nombre')}"
        decision_has_evidence = len(decision_rows) >= MIN_RECENT_METRIC_ACTIVITIES
        decision_trend = _linear_trend_per_30d(decision_rows) if decision_has_evidence else None
        latest = summarized[-1]
        series_label = " · ".join(
            value for value in (heart_rate_zone_id, slope_band) if value
        )
        key = f"{metric_id}:{discipline or 'Todas'}:{heart_rate_zone_id or ''}:{slope_band or ''}"
        result[key] = {
            "metric_id": metric_id,
            "name": catalog.get(metric_id, {}).get("name_es") or catalog.get(metric_id, {}).get("name") or metric_id,
            "discipline": (
                discipline
                or " + ".join(catalog.get(metric_id, {}).get("disciplines", []))
                or "Todas"
            ),
            "heart_rate_zone_id": heart_rate_zone_id,
            "slope_band": slope_band,
            "series_label": series_label,
            "unit": rows[-1].get("unit"),
            "latest": float(latest["value"]),
            "latest_date": latest["date"],
            "days_since_measurement": (as_of - date.fromisoformat(latest["date"])).days,
            "observations": len(rows),
            "activity_summaries": len(summarized),
            "mean": mean(float(item["value"]) for item in summarized),
            "variability": (
                pstdev(float(item["value"]) for item in summarized)
                if len(summarized) > 1 else None
            ),
            # Historical trend remains a reference. The adaptive engine uses
            # decision_trend_per_30d only when recent evidence is sufficient.
            "trend_per_30d": historical_trend,
            "historical_trend_per_30d": historical_trend,
            "decision_trend_per_30d": decision_trend,
            "decision_trend_source": (
                decision_source if decision_has_evidence else "evidencia reciente insuficiente"
            ),
            "decision_activity_summaries": len(decision_rows),
            "decision_confidence": (
                "alta" if len(decision_rows) >= 6 else "media"
                if decision_has_evidence else "insuficiente"
            ),
            "confidence": "alta" if len(summarized) >= 10 else "media" if len(summarized) >= 5 else "baja",
            "observations_data": summarized,
        }
    return result


def _context_state(as_of: date) -> dict[str, Any]:
    context_records = _json(CONTEXT_FILE, {"records": []}).get("records", [])
    latest = _latest_on_or_before(context_records, as_of)
    explicit_availability = {
        item["date"]: item
        for item in _json(AVAILABILITY_FILE, {"records": []}).get("records", [])
    }
    availability_next_14d = []
    for offset in range(14):
        calendar_day = as_of + timedelta(days=offset)
        stored = explicit_availability.get(calendar_day.isoformat())
        availability_next_14d.append(
            {
                "date": calendar_day.isoformat(),
                "status": stored.get("status") if stored else "free",
                "max_minutes": stored.get("max_minutes") if stored else None,
            }
        )
    events = _json(EVENTS_FILE, {"events": []}).get("events", [])
    future_events = [
        item
        for item in events
        if item.get("event_date", "") >= as_of.isoformat()
    ]
    next_event = (
        min(future_events, key=lambda item: item["event_date"])
        if future_events
        else None
    )
    windows = _json(WINDOWS_FILE, {"windows": []}).get("windows", [])
    active_windows = [
        item for item in windows
        if item.get("start_date", "") <= as_of.isoformat() <= item.get("end_date", "")
    ]
    events_by_id = {item.get("id"): item for item in events}
    for window in active_windows:
        if window.get("discipline_weights"):
            continue
        legacy_priorities = window.get("discipline_priorities", {})
        if legacy_priorities:
            legacy_values = {"High": 60.0, "Medium": 30.0, "Low": 10.0, "None": 0.0}
            converted = {
                discipline: legacy_values.get(priority, 30.0)
                for discipline, priority in legacy_priorities.items()
            }
            converted_total = sum(converted.values())
            if converted_total > 0:
                window["discipline_weights"] = {
                    discipline: 100.0 * value / converted_total
                    for discipline, value in converted.items()
                }
                continue
        disciplines = events_by_id.get(window.get("event_id"), {}).get("disciplines", [])
        if disciplines:
            equal_weight = 100.0 / len(disciplines)
            window["discipline_weights"] = {
                discipline: equal_weight for discipline in disciplines
            }
    return {
        "latest_manual_context": (
            {
                "date": latest.get("date"),
                "weight_kg": latest.get("weight_kg"),
                "body_fat_percent": latest.get("body_fat_percent"),
            }
            if latest else None
        ),
        "availability_next_14d": availability_next_14d,
        "next_event": next_event,
        "days_to_next_event": (
            (date.fromisoformat(next_event["event_date"]) - as_of).days
            if next_event
            else None
        ),
        "active_windows": active_windows,
    }


def _interpret(training, recovery) -> dict[str, Any]:
    """Build weekly planning readiness from a 7-day recovery trend."""
    reasons_positive = []
    reasons_limiting = []
    score = 50.0
    evidence = 0
    averages = recovery.get("averages_7d") or {}
    deviation = recovery.get("weekly_deviation", {})
    sleep_delta = deviation.get("sleep_duration_hours")
    if _finite(sleep_delta):
        evidence += 1
        score += max(-15, min(15, sleep_delta * 10))
        (reasons_positive if sleep_delta >= 0 else reasons_limiting).append(
            f"Sueño {abs(sleep_delta):.1f} h {'sobre' if sleep_delta >= 0 else 'bajo'} la línea base."
        )
    hrv_delta = deviation.get("hrv_ms")
    if _finite(hrv_delta):
        evidence += 1
        score += max(-12, min(12, hrv_delta * 0.8))
        (reasons_positive if hrv_delta >= 0 else reasons_limiting).append(
            f"HRV {abs(hrv_delta):.1f} ms {'sobre' if hrv_delta >= 0 else 'bajo'} la línea base."
        )
    resting_delta = deviation.get("resting_hr_bpm")
    if _finite(resting_delta):
        evidence += 1
        score -= max(-10, min(10, resting_delta * 2))
        (reasons_limiting if resting_delta > 0 else reasons_positive).append(
            f"FC en reposo {abs(resting_delta):.1f} bpm {'sobre' if resting_delta > 0 else 'bajo'} la línea base."
        )
    fatigue = averages.get("subjective_fatigue")
    if _finite(fatigue):
        evidence += 1
        score += (5 - fatigue) * 4
        (reasons_limiting if fatigue >= 7 else reasons_positive if fatigue <= 3 else []).append(
            f"Fatiga subjetiva media de 7 días {fatigue:.1f}/10."
        )
    muscular_fatigue = averages.get("muscle_fatigue")
    if _finite(muscular_fatigue):
        evidence += 1
        score += (4 - muscular_fatigue) * 3
        if muscular_fatigue >= 7:
            reasons_limiting.append(f"Fatiga muscular media de 7 días {muscular_fatigue:.1f}/10.")
    latest = recovery.get("latest") or {}
    if latest.get("has_discomfort"):
        evidence += 1
        score -= 20
        reasons_limiting.append("Existe una molestia o lesión informada.")
    load_balance = training.get("load_balance")
    chronic = training.get("chronic_load_42d_equivalent")
    if _finite(load_balance) and _finite(chronic) and chronic > 0:
        evidence += 1
        relative = load_balance / chronic
        if relative < -0.25:
            score -= 8
            reasons_limiting.append("La carga aguda está claramente sobre la carga habitual.")
        elif relative > 0.20:
            reasons_positive.append("La carga aguda está por debajo de la carga habitual.")
    score = max(0, min(100, score))
    label = "insuficiente" if evidence < 2 else "baja" if score < 40 else "normal" if score < 70 else "alta"
    return {
        "readiness_score": score if evidence >= 2 else None,
        "readiness_label": label,
        "evidence_count": evidence,
        "confidence": "alta" if evidence >= 5 else "media" if evidence >= 3 else "baja",
        "positive_factors": reasons_positive,
        "limiting_factors": reasons_limiting,
        "note": "Indicador semanal basado en promedios de bienestar de 7 días frente a tu línea base personal; la última noche se muestra como contexto. No es un diagnóstico médico.",
    }


def _hash(path: Path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def build_athlete_state(as_of_date: date | str) -> dict[str, Any]:
    as_of = date.fromisoformat(as_of_date) if isinstance(as_of_date, str) else as_of_date
    training = _training_state(as_of)
    recovery = _recovery_state(as_of)
    progress = _progress_state(as_of)
    metric_catalog = _json(METRICS_FILE, {"metrics": []}).get("metrics", [])
    measurements = _json(MEASUREMENTS_FILE, {"measurements": []}).get("measurements", [])
    measurement_freshness = metric_freshness(metric_catalog, measurements, as_of)
    context = _context_state(as_of)
    progression_memory = build_progression_memory(as_of)
    geographic_progress = build_geographic_progress(as_of)
    return {
        "snapshot_id": None,
        "as_of_date": as_of.isoformat(),
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "state_version": 4,
        "training": training,
        "recovery": recovery,
        "progress": progress,
        "measurement_freshness": measurement_freshness,
        "context": context,
        "progression_memory": progression_memory,
        "geographic_progress": geographic_progress,
        "interpretation": _interpret(training, recovery),
        "source_hashes": {
            path.name: _hash(path)
            for path in (
                LOAD_FILE, WELLNESS_FILE, CONTEXT_FILE, AVAILABILITY_FILE,
                SESSION_FEEDBACK_FILE, MEASUREMENTS_FILE, EVENTS_FILE, WINDOWS_FILE,
                SEGMENT_CATALOG_FILE, SEGMENT_ATTEMPTS_FILE, ROUTE_FAMILIES_FILE,
            )
        },
    }


def save_snapshot(state: dict[str, Any]) -> Path:
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_id = f"athlete_state_{state['as_of_date']}_{uuid4().hex[:8]}"
    value = {**state, "snapshot_id": snapshot_id}
    path = SNAPSHOT_DIR / f"{snapshot_id}.json"
    with path.open("w", encoding="utf-8") as file:
        json.dump(value, file, indent=4, ensure_ascii=False)
        file.write("\n")
    return path
