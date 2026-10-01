"""Persistence for planned sessions explicitly marked as not completed.

These records are intentionally separate from FIT-linked session feedback: a
missed session has no activity id and must not be interpreted as an
unsuccessful physiological response.
"""

from __future__ import annotations

from datetime import datetime
import json
from uuid import uuid4

from config import PROJECT_ROOT


STATUS_FILE = PROJECT_ROOT / "data" / "athlete_state" / "planned_session_status.json"
MISSED_REASONS = {
    "illness", "time_constraint", "fatigue_recovery", "personal_decision", "other",
}


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_missed_sessions():
    if not STATUS_FILE.exists():
        return []
    try:
        values = json.loads(STATUS_FILE.read_text(encoding="utf-8")).get("records", [])
    except (OSError, json.JSONDecodeError):
        return []
    return sorted(values, key=lambda item: (item.get("date", ""), item.get("updated_at", "")), reverse=True)


def _write(records):
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATUS_FILE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"records": records}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(STATUS_FILE)


def save_missed_session(planned_session, reason, notes=""):
    """Create or update an explicit not-completed status for one plan session."""
    reference_key = (planned_session or {}).get("reference_key")
    if not reference_key:
        raise ValueError("La sesión planificada no tiene una referencia válida.")
    if reason not in MISSED_REASONS:
        raise ValueError("Motivo de sesión no realizada desconocido.")
    timestamp = _now()
    record = {
        "reference_key": reference_key,
        "plan_id": planned_session.get("plan_id"),
        "session_id": planned_session.get("session_id"),
        "date": planned_session.get("date"),
        "planned_session": planned_session,
        "status": "not_completed",
        "reason": reason,
        "notes": (notes or "").strip(),
    }
    records = load_missed_sessions()
    existing = next((item for item in records if item.get("reference_key") == reference_key), None)
    if existing:
        created_at = existing.get("created_at", timestamp)
        existing.clear()
        existing.update({**record, "id": existing.get("id") or f"missed_session_{uuid4().hex[:12]}",
                         "created_at": created_at, "updated_at": timestamp})
    else:
        records.append({**record, "id": f"missed_session_{uuid4().hex[:12]}",
                        "created_at": timestamp, "updated_at": timestamp})
    _write(records)


def delete_missed_session(reference_key):
    records = load_missed_sessions()
    retained = [item for item in records if item.get("reference_key") != reference_key]
    if len(retained) == len(records):
        raise KeyError("La sesión seleccionada no está marcada como no realizada.")
    _write(retained)
