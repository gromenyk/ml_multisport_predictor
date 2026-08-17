"""Persist user decisions made in the adaptive-plan workflow."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from uuid import uuid4

from config import PROJECT_ROOT


FILE = PROJECT_ROOT / "data" / "athlete_state" / "adaptive_plan_decisions.json"


def save_decision(prediction_id, week_start, choice, proposal, comment=""):
    payload = {"decisions": []}
    if FILE.exists():
        try:
            payload = json.loads(FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    value = {
        "id": f"adaptive_decision_{uuid4().hex[:12]}",
        "prediction_id": prediction_id,
        "week_start": week_start,
        "choice": choice,
        "comment": comment.strip(),
        "proposal": proposal,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    payload.setdefault("decisions", []).append(value)
    FILE.parent.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return value
