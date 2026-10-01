"""Canonical model-context representation of approved adaptive weeks."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json

from config import PROJECT_ROOT


HISTORY_DIR = PROJECT_ROOT / "data" / "planned_history"


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _model_session(session):
    """Keep the stable planning schema while discarding derived UI fields."""
    value = deepcopy(session)
    for field in ("estimated_duration_minutes", "estimated_load"):
        value.pop(field, None)
    if value.get("sport") == "Descanso":
        value.update({
            "session_type": None, "structured": False,
            "objective_class": None, "template": None,
            "terrain": None, "zone": None, "distance_km": None,
            "blocks": [{"type": "rest"}],
        })
    return value


def _session_priority(session):
    """Choose a stable primary session when a day contains two sessions."""
    for field in ("estimated_load", "duration_min", "estimated_duration_minutes", "distance_km"):
        try:
            value = float(session.get(field) or 0)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
    return 0.0


def _weekly_model_context(sessions, week_start, week_end):
    """Reduce a multi-session week to the seven daily slots expected by ML."""
    grouped = {}
    for session in sessions:
        day = session.get("date")
        if day:
            grouped.setdefault(day, []).append(session)
    start = datetime.fromisoformat(week_start).date()
    expected_days = {(start.fromordinal(start.toordinal() + offset)).isoformat() for offset in range(7)}
    if set(grouped) != expected_days or len(grouped) != 7:
        raise ValueError("La semana aprobada debe cubrir exactamente siete fechas consecutivas.")
    primary, additional = [], {}
    for day in sorted(grouped):
        day_sessions = grouped[day]
        selected = max(day_sessions, key=_session_priority)
        primary.append(_model_session(selected))
        extras = [_model_session(item) for item in day_sessions if item is not selected]
        if extras:
            additional[day] = extras
    if primary[0]["date"] != week_start or primary[-1]["date"] != week_end:
        raise ValueError("Las fechas de la semana aprobada no coinciden con sus sesiones.")
    return primary, additional


def export_approved_plan(plan):
    """Atomically export one active publication for next-week inference."""
    week_start, week_end = plan.get("week_start"), plan.get("week_end")
    sessions, additional_sessions = _weekly_model_context(
        plan.get("sessions", []), week_start, week_end
    )
    if len(sessions) != 7:
        raise ValueError("La semana aprobada debe contener exactamente siete días.")
    week_start, week_end = plan.get("week_start"), plan.get("week_end")
    if sessions[0]["date"] != week_start or sessions[-1]["date"] != week_end:
        raise ValueError("Las fechas de la semana aprobada no coinciden con sus sesiones.")
    value = {
        "week_start": week_start,
        "week_end": week_end,
        "week_type": "Otros",
        "sessions": sessions,
        "metadata": {
            "source": "adaptive_approved",
            "adaptive_plan_id": plan.get("id"),
            "adaptive_plan_version": plan.get("version"),
            "prediction_id": plan.get("prediction_id"),
            "decision_id": plan.get("decision_id"),
            "engine_version": plan.get("engine_version"),
            "approved_at": plan.get("published_at"),
            "exported_at": _now(),
            "additional_sessions_by_date": additional_sessions,
        },
    }
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    destination = HISTORY_DIR / f"{week_start}.json"
    temporary = destination.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(destination)
    return destination


def remove_exported_week(week_start):
    path = HISTORY_DIR / f"{week_start}.json"
    if path.exists():
        path.unlink()


def sync_active_plans(plans):
    """Make the inference history reflect the current active publication set."""
    active = {item["week_start"]: item for item in plans if item.get("status") == "active"}
    for plan in active.values():
        export_approved_plan(plan)
    if HISTORY_DIR.exists():
        for path in HISTORY_DIR.glob("*.json"):
            if path.stem not in active:
                path.unlink()
    return sorted(active)
