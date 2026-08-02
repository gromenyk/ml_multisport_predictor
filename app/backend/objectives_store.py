"""Persistence for the objective-planning module.

The module separates reusable definitions from their use in time:
events, development-goal catalog, metric catalog, calendar windows and
measurements are independent JSON documents joined by stable IDs.
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from config import OBJECTIVES_DATA_DIR


EVENTS_FILE = OBJECTIVES_DATA_DIR / "events.json"
GOALS_FILE = OBJECTIVES_DATA_DIR / "development_goal_catalog.json"
METRICS_FILE = OBJECTIVES_DATA_DIR / "metric_catalog.json"
WINDOWS_FILE = OBJECTIVES_DATA_DIR / "calendar_windows.json"
MEASUREMENTS_FILE = OBJECTIVES_DATA_DIR / "measurements.json"

FILES = {
    "events": EVENTS_FILE,
    "development_goals": GOALS_FILE,
    "metrics": METRICS_FILE,
    "windows": WINDOWS_FILE,
    "measurements": MEASUREMENTS_FILE,
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _read(kind: str) -> list[dict[str, Any]]:
    path = FILES[kind]
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    values = payload.get(kind, [])
    if not isinstance(values, list):
        raise ValueError(f"{path.name}: {kind!r} must be a list.")
    return values


def _write(kind: str, values: list[dict[str, Any]]) -> None:
    path = FILES[kind]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump({kind: values}, file, indent=4, ensure_ascii=False)
        file.write("\n")
    temporary.replace(path)


def _upsert(kind: str, value: dict[str, Any], prefix: str) -> str:
    values = _read(kind)
    item_id = value.get("id") or f"{prefix}_{uuid4().hex[:12]}"
    timestamp = _now()
    replacement = {**value, "id": item_id, "updated_at": timestamp}
    for index, existing in enumerate(values):
        if existing.get("id") == item_id:
            replacement["created_at"] = existing.get("created_at", timestamp)
            values[index] = replacement
            break
    else:
        replacement["created_at"] = timestamp
        values.append(replacement)
    _write(kind, values)
    return item_id


def _delete(kind: str, item_id: str) -> None:
    values = _read(kind)
    retained = [item for item in values if item.get("id") != item_id]
    if len(values) == len(retained):
        raise KeyError(f"Unknown {kind} item: {item_id}")
    _write(kind, retained)


def load_events():
    return _read("events")


def load_development_goals():
    return _read("development_goals")


def load_metrics():
    return _read("metrics")


def load_windows():
    return _read("windows")


def load_measurements():
    return _read("measurements")


def save_event(value):
    return _upsert("events", value, "event")


def save_development_goal(value):
    return _upsert("development_goals", value, "goal")


def save_metric(value):
    return _upsert("metrics", value, "metric")


def save_window(value):
    return _upsert("windows", value, "window")


def save_discipline_priorities(window_id: str, priorities: dict[str, str]) -> None:
    """Persist the training emphasis declared for one calendar window."""
    windows = load_windows()
    window = next(
        (item for item in windows if item.get("id") == window_id), None
    )
    if window is None:
        raise KeyError(f"Unknown window: {window_id}")
    window["discipline_priorities"] = priorities
    window["updated_at"] = _now()
    _write("windows", windows)


def save_discipline_weights(window_id: str, weights: dict[str, float]) -> None:
    """Persist discipline emphasis percentages for one calendar window."""
    windows = load_windows()
    window = next(
        (item for item in windows if item.get("id") == window_id), None
    )
    if window is None:
        raise KeyError(f"Unknown window: {window_id}")
    window["discipline_weights"] = {
        key: round(float(value), 1) for key, value in weights.items()
    }
    window.pop("discipline_priorities", None)
    window["updated_at"] = _now()
    _write("windows", windows)


def add_measurement(value):
    return _upsert("measurements", value, "measurement")


def delete_event(event_id: str) -> int:
    """Delete an event and its calendar windows."""
    _delete("events", event_id)
    windows = load_windows()
    retained = [item for item in windows if item.get("event_id") != event_id]
    removed = len(windows) - len(retained)
    _write("windows", retained)
    return removed


def delete_development_goal(goal_id: str) -> None:
    """Delete a goal and remove its catalog/window references."""
    _delete("development_goals", goal_id)
    metrics = load_metrics()
    for metric in metrics:
        metric["development_goal_ids"] = [
            item
            for item in metric.get("development_goal_ids", [])
            if item != goal_id
        ]
    _write("metrics", metrics)

    windows = load_windows()
    for window in windows:
        window["assignments"] = [
            assignment
            for assignment in window.get("assignments", [])
            if assignment.get("development_goal_id") != goal_id
        ]
    _write("windows", windows)


def delete_metric(metric_id: str) -> int:
    """Delete a metric, its measurements and window references."""
    _delete("metrics", metric_id)
    measurements = load_measurements()
    retained = [
        item for item in measurements if item.get("metric_id") != metric_id
    ]
    removed = len(measurements) - len(retained)
    _write("measurements", retained)

    windows = load_windows()
    for window in windows:
        for assignment in window.get("assignments", []):
            assignment["metrics"] = [
                item
                for item in assignment.get("metrics", [])
                if item.get("metric_id") != metric_id
            ]
    _write("windows", windows)
    return removed


def delete_window(window_id: str) -> None:
    _delete("windows", window_id)


def delete_measurement(measurement_id: str) -> None:
    _delete("measurements", measurement_id)


def save_goal_assignment(
    window_id: str, goal_id: str, priority: str
) -> None:
    windows = load_windows()
    window = next(
        (item for item in windows if item.get("id") == window_id), None
    )
    if window is None:
        raise KeyError(f"Unknown window: {window_id}")
    assignments = window.setdefault("assignments", [])
    assignment = next(
        (
            item
            for item in assignments
            if item.get("development_goal_id") == goal_id
        ),
        None,
    )
    if assignment:
        assignment["priority"] = priority
    else:
        assignments.append(
            {
                "development_goal_id": goal_id,
                "priority": priority,
                "metrics": [],
            }
        )
    window["updated_at"] = _now()
    _write("windows", windows)


def remove_goal_assignment(window_id: str, goal_id: str) -> None:
    windows = load_windows()
    window = next(
        (item for item in windows if item.get("id") == window_id), None
    )
    if window is None:
        raise KeyError(f"Unknown window: {window_id}")
    window["assignments"] = [
        item
        for item in window.get("assignments", [])
        if item.get("development_goal_id") != goal_id
    ]
    window["updated_at"] = _now()
    _write("windows", windows)


def save_metric_assignment(
    window_id: str,
    goal_id: str,
    metric_id: str,
    heart_rate_zone_id: str = None,
    discipline: str = None,
) -> None:
    windows = load_windows()
    window = next(
        (item for item in windows if item.get("id") == window_id), None
    )
    if window is None:
        raise KeyError(f"Unknown window: {window_id}")
    assignment = next(
        (
            item
            for item in window.get("assignments", [])
            if item.get("development_goal_id") == goal_id
        ),
        None,
    )
    if assignment is None:
        raise KeyError("Assign the development goal to the window first.")
    metrics = assignment.setdefault("metrics", [])
    existing = next(
        (item for item in metrics if item.get("metric_id") == metric_id), None
    )
    value = {"metric_id": metric_id}
    if heart_rate_zone_id:
        if heart_rate_zone_id not in ("Z1", "Z2", "Z3", "Z4", "Z5", "Z6"):
            raise ValueError("Zona de frecuencia cardiaca desconocida.")
        value["heart_rate_zone_id"] = heart_rate_zone_id
    if discipline:
        if discipline not in ("Bici", "Corrida", "Trail", "Kayak"):
            raise ValueError("Disciplina desconocida.")
        value["discipline"] = discipline
    if existing:
        existing.clear()
        existing.update(value)
    else:
        metrics.append(value)
    window["updated_at"] = _now()
    _write("windows", windows)


def remove_metric_assignment(
    window_id: str, goal_id: str, metric_id: str
) -> None:
    windows = load_windows()
    window = next(
        (item for item in windows if item.get("id") == window_id), None
    )
    if window is None:
        raise KeyError(f"Unknown window: {window_id}")
    assignment = next(
        (
            item
            for item in window.get("assignments", [])
            if item.get("development_goal_id") == goal_id
        ),
        None,
    )
    if assignment is None:
        raise KeyError("Unknown development-goal assignment.")
    assignment["metrics"] = [
        item
        for item in assignment.get("metrics", [])
        if item.get("metric_id") != metric_id
    ]
    window["updated_at"] = _now()
    _write("windows", windows)
