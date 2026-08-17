"""Incremental, lightweight index for normalized FIT activities.

Activity JSON files contain every recorded sample and can be large. UI selectors
only need a few summary fields, so this module persists those fields and reads a
full activity file only when it is new or has changed.
"""

from __future__ import annotations

import json
from pathlib import Path

from config import PROJECT_ROOT


ACTIVITIES_DIR = PROJECT_ROOT / "garmin" / "activities"
INDEX_FILE = PROJECT_ROOT / "data" / "derived" / "activity_index.json"
SCHEMA_VERSION = 1


def _read_index() -> dict:
    if not INDEX_FILE.exists():
        return {"schema_version": SCHEMA_VERSION, "activities": {}}
    try:
        payload = json.loads(INDEX_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"schema_version": SCHEMA_VERSION, "activities": {}}
    if (
        payload.get("schema_version") != SCHEMA_VERSION
        or not isinstance(payload.get("activities"), dict)
    ):
        return {"schema_version": SCHEMA_VERSION, "activities": {}}
    return payload


def _write_index(payload: dict) -> None:
    INDEX_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = INDEX_FILE.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(INDEX_FILE)


def _summary(path: Path, stat_result) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    activity = payload.get("activity", {})
    return {
        "file": path.name,
        "modified_ns": stat_result.st_mtime_ns,
        "size_bytes": stat_result.st_size,
        "date": activity.get("date") or "",
        "discipline": activity.get("sport_profile_name") or activity.get("sport") or "",
        "distance_km": activity.get("distance_km"),
        "duration_seconds": activity.get("duration_seconds"),
    }


def load_activity_index() -> list[dict]:
    """Return summaries, refreshing only activity files changed since last call."""
    payload = _read_index()
    known = payload["activities"]
    paths = sorted(ACTIVITIES_DIR.glob("activity_*.json")) if ACTIVITIES_DIR.exists() else []
    current_names = {path.name for path in paths}
    changed = False

    for name in list(known):
        if name not in current_names:
            known.pop(name)
            changed = True

    for path in paths:
        stat_result = path.stat()
        current = known.get(path.name, {})
        if (
            current.get("modified_ns") == stat_result.st_mtime_ns
            and current.get("size_bytes") == stat_result.st_size
        ):
            continue
        try:
            known[path.name] = _summary(path, stat_result)
        except (OSError, json.JSONDecodeError):
            # Leave a previous valid index entry intact if a write is in progress.
            continue
        changed = True

    if changed:
        _write_index(payload)
    return list(known.values())
