"""Local persistence for user-defined geographic training segments."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from config import PROJECT_ROOT


SEGMENTS_FILE = PROJECT_ROOT / "data" / "segments" / "segment_catalog.json"
ATTEMPTS_FILE = PROJECT_ROOT / "data" / "segments" / "segment_attempts.json"


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_segments() -> list[dict[str, Any]]:
    if not SEGMENTS_FILE.exists():
        return []
    payload = json.loads(SEGMENTS_FILE.read_text(encoding="utf-8"))
    segments = payload.get("segments", [])
    if not isinstance(segments, list):
        raise ValueError("segment_catalog.json: 'segments' debe ser una lista.")
    return segments


def _write(segments: list[dict[str, Any]]) -> None:
    SEGMENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = SEGMENTS_FILE.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"segments": segments}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(SEGMENTS_FILE)


def find_duplicate(value: dict[str, Any], tolerance_km: float = 0.05):
    """Find the same interval previously saved from the same source activity."""
    source = value.get("source", {})
    for existing in load_segments():
        existing_source = existing.get("source", {})
        if existing_source.get("activity_file") != source.get("activity_file"):
            continue
        if (
            abs(float(existing_source.get("start_km", -999)) - float(source.get("start_km", 999))) <= tolerance_km
            and abs(float(existing_source.get("end_km", -999)) - float(source.get("end_km", 999))) <= tolerance_km
        ):
            return existing
    return None


def save_segment(value: dict[str, Any]) -> str:
    segments = load_segments()
    segment_id = value.get("id") or f"segment_{uuid4().hex[:12]}"
    timestamp = _now()
    replacement = {**value, "id": segment_id, "updated_at": timestamp}
    for index, existing in enumerate(segments):
        if existing.get("id") == segment_id:
            replacement["created_at"] = existing.get("created_at", timestamp)
            segments[index] = replacement
            break
    else:
        replacement["created_at"] = timestamp
        segments.append(replacement)
    _write(segments)
    return segment_id


def delete_segment(segment_id: str) -> None:
    segments = load_segments()
    retained = [item for item in segments if item.get("id") != segment_id]
    if len(retained) == len(segments):
        raise KeyError(f"Segmento desconocido: {segment_id}")
    _write(retained)
    attempts = load_segment_attempts()
    save_segment_attempts(
        [item for item in attempts if item.get("segment_id") != segment_id]
    )


def load_segment_attempts() -> list[dict[str, Any]]:
    if not ATTEMPTS_FILE.exists():
        return []
    payload = json.loads(ATTEMPTS_FILE.read_text(encoding="utf-8"))
    attempts = payload.get("attempts", [])
    if not isinstance(attempts, list):
        raise ValueError("segment_attempts.json: 'attempts' debe ser una lista.")
    return attempts


def save_segment_attempts(attempts: list[dict[str, Any]]) -> None:
    ATTEMPTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = ATTEMPTS_FILE.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"attempts": attempts}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(ATTEMPTS_FILE)
