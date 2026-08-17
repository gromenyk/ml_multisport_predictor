"""Versioned persistence for approved plans, feedback and backtests."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from uuid import uuid4

from config import PROJECT_ROOT
from adaptive_planning.engine import _estimated_week_load
from adaptive_planning.planned_history import export_approved_plan, remove_exported_week


DATA_DIR = PROJECT_ROOT / "data" / "athlete_state"
PLANS_FILE = DATA_DIR / "adaptive_published_plans.json"
FEEDBACK_FILE = DATA_DIR / "adaptive_week_feedback.json"
BACKTEST_FILE = DATA_DIR / "adaptive_backtests.json"


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read(path, key):
    if not path.exists():
        return {key: []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {key: []}
    return payload if isinstance(payload.get(key), list) else {key: []}


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _sessions_for_selected_changes(proposal, selected_change_ids):
    selected = set(selected_change_ids)
    if selected == {item["change_id"] for item in proposal.get("changes", [])}:
        return deepcopy(proposal["adapted_sessions"])
    result = deepcopy(proposal["base_sessions"])
    by_date = {item["date"]: item for item in result}
    adapted = {item["date"]: item for item in proposal["adapted_sessions"]}
    changes = {item["change_id"]: item for item in proposal.get("changes", [])}
    for change_id in selected:
        change = changes[change_id]
        if change["type"] == "scale_distance":
            day = change["after"]["date"]
            by_date[day] = deepcopy(adapted[day])
        elif change["type"] == "relocate_session":
            for day in (change["before"]["date"], change["after"]["date"]):
                by_date[day] = deepcopy(adapted[day])
        elif change["type"] == "replace_session":
            day = change["after"]["date"]
            by_date[day] = deepcopy(adapted[day])
    return [by_date[day] for day in sorted(by_date)]


def publish_plan(prediction_id, proposal, selected_change_ids, comment="", decision_id=None):
    sessions = _sessions_for_selected_changes(proposal, selected_change_ids)
    payload = _read(PLANS_FILE, "plans")
    version = 1 + max((item.get("version", 0) for item in payload["plans"]
                       if item.get("week_start") == sessions[0]["date"]), default=0)
    week_start, week_end = sessions[0]["date"], sessions[-1]["date"]
    for existing in payload["plans"]:
        if existing.get("week_start") == week_start and existing.get("status") == "active":
            existing["status"] = "superseded"
            existing["superseded_at"] = _now()
    value = {
        "id": f"adaptive_plan_{uuid4().hex[:12]}", "version": version, "status": "active",
        "prediction_id": prediction_id, "decision_id": decision_id,
        "engine_version": proposal.get("engine_version"), "week_start": week_start, "week_end": week_end,
        "selected_change_ids": list(selected_change_ids), "comment": comment.strip(),
        "sessions": sessions, "estimated_load": round(_estimated_week_load(sessions), 2),
        "daily_loads": {item["date"]: round(_estimated_week_load([item]), 2) for item in sessions},
        "published_at": _now(),
    }
    payload["plans"].append(value)
    _write(PLANS_FILE, payload)
    export_approved_plan(value)
    return value


def load_active_plans():
    return [item for item in _read(PLANS_FILE, "plans")["plans"] if item.get("status") == "active"]


def load_published_plans():
    """Return every published version for read-only history views."""
    return sorted(
        _read(PLANS_FILE, "plans")["plans"],
        key=lambda item: (item.get("week_start", ""), item.get("version", 0)),
        reverse=True,
    )


def revert_plan(plan_id):
    payload = _read(PLANS_FILE, "plans")
    target = next(item for item in payload["plans"] if item["id"] == plan_id)
    target["status"], target["reverted_at"] = "reverted", _now()
    candidates = [item for item in payload["plans"] if item.get("week_start") == target["week_start"]
                  and item.get("status") == "superseded"]
    if candidates:
        previous = max(candidates, key=lambda item: item.get("version", 0))
        previous["status"] = "active"
        previous.pop("superseded_at", None)
    _write(PLANS_FILE, payload)
    if candidates:
        export_approved_plan(previous)
    else:
        remove_exported_week(target["week_start"])
    return target


def save_feedback(value):
    payload = _read(FEEDBACK_FILE, "feedback")
    timestamp = _now()
    item = {**value, "id": value.get("id") or f"adaptive_feedback_{uuid4().hex[:12]}", "updated_at": timestamp}
    for index, existing in enumerate(payload["feedback"]):
        if existing.get("week_start") == item.get("week_start"):
            item["id"] = existing["id"]
            item["created_at"] = existing.get("created_at", timestamp)
            payload["feedback"][index] = item
            break
    else:
        item["created_at"] = timestamp
        payload["feedback"].append(item)
    _write(FEEDBACK_FILE, payload)
    return item


def load_feedback():
    return _read(FEEDBACK_FILE, "feedback")["feedback"]


def save_backtest(run):
    payload = _read(BACKTEST_FILE, "runs")
    value = {**run, "id": run.get("id") or f"adaptive_backtest_{uuid4().hex[:12]}", "created_at": _now()}
    payload["runs"].append(value)
    _write(BACKTEST_FILE, payload)
    return value


def load_backtests():
    return _read(BACKTEST_FILE, "runs")["runs"]
