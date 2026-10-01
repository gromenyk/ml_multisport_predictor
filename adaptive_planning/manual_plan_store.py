"""Versioned manual intermediate plans between model prediction and adaptation."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json

from config import PROJECT_ROOT


FILE = PROJECT_ROOT / "data" / "athlete_state" / "manual_week_plans.json"


def _read() -> dict:
    if not FILE.exists():
        return {"plans": []}
    try:
        payload = json.loads(FILE.read_text(encoding="utf-8"))
        return payload if isinstance(payload.get("plans"), list) else {"plans": []}
    except (OSError, json.JSONDecodeError):
        return {"plans": []}


def _write(payload: dict) -> None:
    FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(FILE)


def load_manual_plan(prediction_id: str) -> dict | None:
    plans = [item for item in _read()["plans"] if item.get("prediction_id") == prediction_id]
    return deepcopy(max(plans, key=lambda item: item.get("updated_at", ""))) if plans else None


def save_manual_plan(prediction_id: str, week_start: str, week_end: str, sessions: list[dict], note: str = "") -> dict:
    payload = _read()
    previous = load_manual_plan(prediction_id) or {}
    value = {
        "prediction_id": prediction_id,
        "week_start": week_start,
        "week_end": week_end,
        "sessions": deepcopy(sessions),
        "note": note.strip(),
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    payload["plans"] = [item for item in payload["plans"] if item.get("prediction_id") != prediction_id]
    if previous:
        value["created_at"] = previous.get("created_at", value["updated_at"])
    else:
        value["created_at"] = value["updated_at"]
    payload["plans"].append(value)
    _write(payload)
    return value


def delete_manual_plan(prediction_id: str) -> None:
    payload = _read()
    payload["plans"] = [item for item in payload["plans"] if item.get("prediction_id") != prediction_id]
    _write(payload)
