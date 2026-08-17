"""Idempotent persistence for automatically calculated metric measurements."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any
from uuid import uuid4


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MEASUREMENTS_FILE = (
    PROJECT_ROOT / "data" / "objectives" / "metric_measurements.json"
)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_measurements() -> list[dict[str, Any]]:
    if not MEASUREMENTS_FILE.exists():
        return []
    with MEASUREMENTS_FILE.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    values = payload.get("measurements", [])
    if not isinstance(values, list):
        raise ValueError("metric_measurements.json: 'measurements' must be a list.")
    return values


def _write(values: list[dict[str, Any]]) -> None:
    MEASUREMENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = MEASUREMENTS_FILE.with_suffix(f".{uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump({"measurements": values}, file, indent=4, ensure_ascii=False)
        file.write("\n")
    try:
        temporary.replace(MEASUREMENTS_FILE)
    except PermissionError:
        # OneDrive can briefly lock the destination during synchronization.
        # A direct rewrite remains available when atomic replacement is denied.
        with MEASUREMENTS_FILE.open("w", encoding="utf-8") as file:
            json.dump(
                {"measurements": values},
                file,
                indent=4,
                ensure_ascii=False,
            )
            file.write("\n")
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def upsert_measurement(value: dict[str, Any]) -> str:
    values = load_measurements()
    identity_key = value["identity_key"]
    timestamp = _now()
    existing = next(
        (item for item in values if item.get("identity_key") == identity_key),
        None,
    )
    if existing:
        measurement_id = existing["id"]
        created_at = existing.get("created_at", timestamp)
        existing.clear()
        existing.update(
            {
                **value,
                "id": measurement_id,
                "created_at": created_at,
                "updated_at": timestamp,
            }
        )
    else:
        measurement_id = f"metric_measurement_{uuid4().hex[:12]}"
        values.append(
            {
                **value,
                "id": measurement_id,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )
    _write(values)
    return measurement_id


def replace_measurement_scope(
    activity_id: str,
    metric_id: str,
    calculation_version: int,
    replacements: list[dict[str, Any]],
) -> list[str]:
    """Atomically replace one activity/metric/version result set."""
    values = load_measurements()
    timestamp = _now()
    # Only the current calculation is active. Reprocessing with a newer
    # version supersedes prior results for the same activity and metric.
    scope = [
        item
        for item in values
        if item.get("activity_id") == activity_id
        and item.get("metric_id") == metric_id
    ]
    existing_by_identity = {
        item.get("identity_key"): item for item in scope
    }
    existing_by_segment = {
        (
            item.get("heart_rate_zone_id"),
            item.get("components", {}).get("start_time"),
            item.get("components", {}).get("slope_band"),
        ): item
        for item in scope
    }
    retained = [item for item in values if item not in scope]
    measurement_ids = []
    for value in replacements:
        existing = existing_by_identity.get(value["identity_key"]) or existing_by_segment.get(
            (
                value.get("heart_rate_zone_id"),
                value.get("components", {}).get("start_time"),
                value.get("components", {}).get("slope_band"),
            )
        )
        measurement_id = (
            existing["id"]
            if existing
            else f"metric_measurement_{uuid4().hex[:12]}"
        )
        retained.append(
            {
                **value,
                "id": measurement_id,
                "manual_note": value.get(
                    "manual_note",
                    existing.get("manual_note", "") if existing else "",
                ),
                "created_at": (
                    existing.get("created_at", timestamp)
                    if existing
                    else timestamp
                ),
                "updated_at": timestamp,
            }
        )
        measurement_ids.append(measurement_id)
    _write(retained)
    return measurement_ids


def update_measurement_note(measurement_id: str, note: str) -> None:
    """Update user-authored context without changing calculated fields."""
    values = load_measurements()
    measurement = next(
        (item for item in values if item.get("id") == measurement_id),
        None,
    )
    if measurement is None:
        raise KeyError(f"Medición desconocida: {measurement_id}")
    measurement["manual_note"] = note.strip()
    measurement["updated_at"] = _now()
    _write(values)
