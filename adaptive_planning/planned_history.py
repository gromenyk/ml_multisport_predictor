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


def export_approved_plan(plan):
    """Atomically export one active publication for next-week inference."""
    sessions = sorted((_model_session(item) for item in plan.get("sessions", [])), key=lambda item: item["date"])
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
