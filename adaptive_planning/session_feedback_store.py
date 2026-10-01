"""Persistence for structured post-training feedback linked to performed FIT activities."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FEEDBACK_FILE = PROJECT_ROOT / "data" / "athlete_state" / "session_feedback.json"
FEEDBACK_SETTINGS_FILE = PROJECT_ROOT / "data" / "athlete_state" / "session_feedback_settings.json"
TEMPERATURE_OPTIONS = {
    "very_hot", "hot", "pleasant", "cold", "very_cold",
}
TERRAIN_CONDITION_OPTIONS = {"normal", "muddy", "snowy"}


def planned_session_reference(plan_id, session, fallback_index=0):
    """Stable session identity; dates alone are ambiguous for double sessions."""
    session_id = session.get("session_id") or session.get("id")
    if session_id:
        return f"{plan_id}::{session_id}"
    return "::".join([
        str(plan_id), str(session.get("date") or ""), str(session.get("sport") or ""),
        str(session.get("zone") or ""), str(session.get("distance_km") or ""), str(fallback_index),
    ])


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def feedback_tracking_start():
    """First date on which post-training feedback is expected."""
    if not FEEDBACK_SETTINGS_FILE.exists():
        return None
    try:
        return json.loads(FEEDBACK_SETTINGS_FILE.read_text(encoding="utf-8")).get("tracking_start")
    except (OSError, json.JSONDecodeError):
        return None


def load_session_feedback():
    if not FEEDBACK_FILE.exists():
        return []
    try:
        values = json.loads(FEEDBACK_FILE.read_text(encoding="utf-8")).get("records", [])
    except (OSError, json.JSONDecodeError):
        return []
    return sorted(values, key=lambda item: (item.get("date", ""), item.get("activity_id", "")))


def save_session_feedback(value):
    match_type = value.get("match_type", "planned")
    planned_session = value.get("planned_session") if match_type == "planned" else None
    completion = (
        float(value.get("completion_percent", 100)) if match_type == "planned" else None
    )
    rpe = float(value["rpe"])
    if completion is not None and not 0 <= completion <= 100:
        raise ValueError("El cumplimiento debe estar entre 0 y 100%.")
    if not 1 <= rpe <= 10:
        raise ValueError("El esfuerzo percibido debe estar entre 1 y 10.")
    if match_type not in {"planned", "additional", "unmatched"}:
        raise ValueError("Tipo de correspondencia desconocido.")
    if match_type == "planned" and not planned_session:
        raise ValueError("Selecciona la sesión planificada de referencia.")
    temperature = value.get("temperature", "pleasant")
    terrain_condition = value.get("terrain_condition", "normal")
    if temperature not in TEMPERATURE_OPTIONS:
        raise ValueError("Condición de temperatura desconocida.")
    if terrain_condition not in TERRAIN_CONDITION_OPTIONS:
        raise ValueError("Condición del terreno desconocida.")
    record = {
        "activity_id": str(value["activity_id"]),
        "date": value["date"],
        "completion_percent": completion,
        "match_type": match_type,
        "planned_session": planned_session,
        "rpe": rpe,
        "finish_feeling": value["finish_feeling"],
        "energy_response": value["energy_response"],
        "discomfort": value["discomfort"],
        "temperature": temperature,
        "terrain_condition": terrain_condition,
        "notes": (value.get("notes") or "").strip(),
    }
    records = load_session_feedback()
    if planned_session:
        reference_key = planned_session.get("reference_key")
        duplicate = next(
            (
                item for item in records
                if item.get("activity_id") != record["activity_id"]
                and (item.get("planned_session") or {}).get("reference_key") == reference_key
            ),
            None,
        )
        if duplicate:
            raise ValueError(
                "Esa sesión planificada ya está asociada a otra actividad realizada."
            )
    timestamp = _now()
    existing = next((item for item in records if item.get("activity_id") == record["activity_id"]), None)
    if existing:
        created_at = existing.get("created_at", timestamp)
        existing.clear()
        existing.update({**record, "created_at": created_at, "updated_at": timestamp})
    else:
        records.append({**record, "id": f"session_feedback_{uuid4().hex[:12]}",
                        "created_at": timestamp, "updated_at": timestamp})
    FEEDBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = FEEDBACK_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps({"records": records}, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(FEEDBACK_FILE)


def delete_session_feedback(activity_id):
    records = load_session_feedback()
    retained = [item for item in records if item.get("activity_id") != str(activity_id)]
    if len(retained) == len(records):
        raise KeyError("La actividad seleccionada no tiene una evaluación guardada.")
    temporary = FEEDBACK_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps({"records": retained}, ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    temporary.replace(FEEDBACK_FILE)
