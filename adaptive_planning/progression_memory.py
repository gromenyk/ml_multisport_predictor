"""Build an explainable longitudinal progression memory from FIT and feedback."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
import json
from pathlib import Path

from adaptive_planning.session_feedback_store import feedback_tracking_start, load_session_feedback
from backend.activity_usage_store import permits

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOAD_FILE = PROJECT_ROOT / "data" / "derived" / "training_load" / "performed_load.json"
WELLNESS_FILE = PROJECT_ROOT / "data" / "athlete_state" / "daily_wellness.json"


def _read(path, key):
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get(key, [])
    except (OSError, json.JSONDecodeError):
        return []


def _classification(feedback, recovery):
    if not feedback:
        return "not_evaluable", ["No existe percepción postentrenamiento."]
    reasons = []
    completion = feedback.get("completion_percent")
    completion = float(completion) if completion is not None else None
    rpe = float(feedback.get("rpe", 10))
    finish = feedback.get("finish_feeling")
    energy = feedback.get("energy_response")
    discomfort = feedback.get("discomfort")
    recovery_alarm = any(
        item.get("has_discomfort")
        or (item.get("subjective_fatigue") is not None and float(item["subjective_fatigue"]) >= 8)
        or (
            item.get("muscle_fatigue", item.get("muscle_soreness")) is not None
            and float(item.get("muscle_fatigue", item.get("muscle_soreness"))) >= 8
        )
        or (item.get("sleep_duration_hours") is not None and float(item["sleep_duration_hours"]) < 5.5)
        for item in recovery
    )
    if (completion is not None and completion < 75) or finish == "excessive" or discomfort == "relevant":
        reasons.append("La sesión fue incompleta, excesiva o produjo molestias relevantes.")
        return "not_tolerated", reasons
    if recovery_alarm:
        reasons.append("La recuperación de las 48 horas posteriores mostró una señal limitante.")
        return "difficult", reasons
    if (completion is not None and completion < 90) or rpe >= 8 or finish == "very_demanding" or energy == "strong_drop" or discomfort == "mild":
        reasons.append("Se completó con dificultad o con una señal subjetiva que aconseja consolidar.")
        return "difficult", reasons
    reasons.append(
        "Se completó al menos 90%, sin señales relevantes y con esfuerzo controlado."
        if completion is not None
        else "Actividad adicional bien tolerada, sin señales subjetivas relevantes."
    )
    return "well_tolerated", reasons


def build_progression_memory(as_of_date):
    as_of = date.fromisoformat(str(as_of_date)) if not isinstance(as_of_date, date) else as_of_date
    activities = _read(LOAD_FILE, "activities")
    wellness = _read(WELLNESS_FILE, "records")
    feedback_by_activity = {item["activity_id"]: item for item in load_session_feedback()}
    tracking_start = feedback_tracking_start()
    wellness_by_date = {item.get("date"): item for item in wellness}
    evaluated = []
    for item in activities:
        activity_file = item.get("source", {}).get("activity_json")
        use_for_memory = permits(activity_file, "memory")
        use_for_compliance = permits(activity_file, "compliance")
        if not use_for_memory and not use_for_compliance:
            continue
        activity_date = date.fromisoformat(item["date"])
        if activity_date > as_of:
            continue
        activity = item.get("activity", {})
        discipline = activity.get("sport")
        if not discipline or discipline == "Descanso":
            continue
        recovery = [
            wellness_by_date.get((activity_date + timedelta(days=offset)).isoformat(), {})
            for offset in (1, 2)
        ]
        feedback = feedback_by_activity.get(str(item.get("activity_id")))
        if tracking_start and item["date"] < tracking_start and not feedback:
            # Historical FITs remain objective context, but are not treated as
            # missing subjective evaluations before feedback tracking began.
            continue
        status, reasons = _classification(feedback, recovery)
        zone_minutes = item.get("zone_distribution", {}).get("duration_by_load_zone_minutes", {})
        total_zone_minutes = sum(float(value or 0) for value in zone_minutes.values())
        easy_zone_minutes = sum(float(zone_minutes.get(zone) or 0) for zone in ("Z1", "Z2"))
        stimulus_type = (
            "endurance" if total_zone_minutes and easy_zone_minutes / total_zone_minutes >= .60
            else "intensity"
        )
        evaluated.append({
            "activity_id": str(item.get("activity_id")), "date": item["date"],
            "discipline": discipline, "cycling_modality": activity.get("cycling_modality"),
            "distance_km": float(activity.get("distance_km") or 0),
            "ascent_m": float(activity.get("ascent_meters") or 0),
            "duration_minutes": float(activity.get("duration_seconds") or 0) / 60,
            "load": float(item.get("load", {}).get("calculated_load") or 0),
            "paused_time_share": activity.get("paused_time_share"),
            "use_for_memory": use_for_memory,
            "use_for_compliance": use_for_compliance,
            "stimulus_type": stimulus_type,
            "zone_minutes": {
                key: round(float(value or 0), 1) for key, value in zone_minutes.items()
            },
            "status": status, "reasons": reasons, "feedback": feedback,
        })
    by_discipline = defaultdict(list)
    for item in evaluated:
        if item["use_for_memory"]:
            by_discipline[item["discipline"]].append(item)
    memory = {}
    for discipline, records in by_discipline.items():
        records.sort(key=lambda item: item["date"])
        endurance_records = [item for item in records if item["stimulus_type"] == "endurance"]
        assessable = [item for item in endurance_records if item["status"] != "not_evaluable"]
        tolerated = [item for item in assessable if item["status"] == "well_tolerated"]
        streak = 0
        for item in reversed(assessable):
            if item["status"] != "well_tolerated":
                break
            streak += 1
        latest = assessable[-1] if assessable else None
        longest = max(tolerated, key=lambda item: item["distance_km"], default=None)
        action = (
            "progress" if latest and latest["status"] == "well_tolerated" else
            "consolidate" if latest and latest["status"] == "difficult" else
            "regress" if latest and latest["status"] == "not_tolerated" else
            "insufficient_evidence"
        )
        memory[discipline] = {
            "action": action, "well_tolerated_streak": streak,
            "latest_evaluated": latest, "longest_well_tolerated": longest,
            "assessable_sessions": len(assessable),
            "sessions_without_feedback": sum(item["status"] == "not_evaluable" for item in endurance_records),
            "stimulus_type": "endurance",
        }
    compliance_sessions = [item for item in evaluated if item["use_for_compliance"]]
    return {"as_of_date": as_of.isoformat(), "disciplines": dict(memory),
            "recent_sessions": sorted(compliance_sessions, key=lambda item: item["date"], reverse=True)[:20]}
