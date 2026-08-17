"""Convert an archived model prediction into a normalized context week.

This experiment deliberately stays outside the production pipeline. It never
modifies the source prediction and writes only inside this experiment's output
directory (unless another output directory is explicitly supplied).
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import date, datetime, timedelta
import json
from pathlib import Path
from typing import Any


EXPERIMENT_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT_DIR = EXPERIMENT_DIR / "input"
DEFAULT_OUTPUT_DIR = EXPERIMENT_DIR / "output"

WEEKDAYS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")
VALID_SPORTS = {"Descanso", "Trail", "Corrida", "Bici", "Kayak"}


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        value = json.load(file)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name}: the JSON root must be an object.")
    return value


def parse_iso_date(value: Any, field: str, source: Path) -> date:
    if not isinstance(value, str):
        raise ValueError(f"{source.name}: {field} must be an ISO date string.")
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(
            f"{source.name}: invalid {field} date {value!r}."
        ) from error


def normalize_session(
    source_session: dict[str, Any],
    expected_date: date,
    source: Path,
) -> dict[str, Any]:
    session_date = parse_iso_date(source_session.get("date"), "session date", source)
    if session_date != expected_date:
        raise ValueError(
            f"{source.name}: expected a session for {expected_date.isoformat()}, "
            f"found {session_date.isoformat()}."
        )

    sport = source_session.get("sport")
    if sport not in VALID_SPORTS:
        raise ValueError(
            f"{source.name}: unknown sport {sport!r} on {session_date.isoformat()}."
        )

    blocks = source_session.get("blocks")
    if not isinstance(blocks, list):
        raise ValueError(
            f"{source.name}: blocks must be a list on {session_date.isoformat()}."
        )
    if any(not isinstance(block, dict) for block in blocks):
        raise ValueError(
            f"{source.name}: every block must be an object on "
            f"{session_date.isoformat()}."
        )

    structured = bool(source_session.get("structured", False))
    if sport == "Descanso":
        structured = False
        blocks = [{"type": "rest"}]

    # Keep the complete block representation. Future feature engineering may
    # need fields that the current models do not yet consume.
    return {
        "date": session_date.isoformat(),
        "weekday": WEEKDAYS[session_date.weekday()],
        "sport": sport,
        "session_type": (
            None if sport == "Descanso" else source_session.get("session_type")
        ),
        "terrain": None if sport == "Descanso" else source_session.get("terrain"),
        "blocks": deepcopy(blocks),
        "structured": structured,
        "template_id": (
            source_session.get("template") if structured else None
        ),
        "objective_class": (
            source_session.get("objective_class") if structured else None
        ),
    }


def adapt_prediction(prediction: dict[str, Any], source: Path) -> dict[str, Any]:
    """Return a prediction represented as one normalized seven-day week."""
    week_start = parse_iso_date(prediction.get("week_start"), "week_start", source)
    week_end = parse_iso_date(prediction.get("week_end"), "week_end", source)

    if week_end != week_start + timedelta(days=6):
        raise ValueError(f"{source.name}: the predicted week must contain 7 days.")
    if week_start.weekday() != 0:
        raise ValueError(f"{source.name}: week_start must be a Monday.")

    source_sessions = prediction.get("sessions")
    if not isinstance(source_sessions, list) or len(source_sessions) != 7:
        raise ValueError(f"{source.name}: exactly 7 sessions are required.")

    sessions_by_date: dict[date, dict[str, Any]] = {}
    for session in source_sessions:
        if not isinstance(session, dict):
            raise ValueError(f"{source.name}: every session must be an object.")
        session_date = parse_iso_date(session.get("date"), "session date", source)
        if session_date in sessions_by_date:
            raise ValueError(
                f"{source.name}: duplicate session date {session_date.isoformat()}."
            )
        sessions_by_date[session_date] = session

    sessions = []
    for offset in range(7):
        expected_date = week_start + timedelta(days=offset)
        if expected_date not in sessions_by_date:
            raise ValueError(
                f"{source.name}: missing session for {expected_date.isoformat()}."
            )
        sessions.append(
            normalize_session(sessions_by_date[expected_date], expected_date, source)
        )

    # Current downstream models expect week_type in each context week, but the
    # predictor does not generate it. "Otros" is the established neutral
    # fallback used by the prediction pipeline; this is recorded explicitly so
    # the experimental assumption remains auditable.
    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "week_type": "Otros",
        "macro": None,
        "micro": None,
        "sessions": sessions,
        "metadata": {
            "source_type": "model_prediction",
            "source_file": source.name,
            "adapted_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "experiment": "autoregressive_context",
            "week_type_assumption": "Otros",
            "original_generated_at": prediction.get("generated_at"),
            "original_context_weeks": prediction.get("context_weeks", []),
            "production_models": prediction.get("production_models", {}),
        },
    }


def find_input_files(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    if input_path.is_dir():
        return sorted(input_path.glob("*.json"))
    raise FileNotFoundError(f"Input path does not exist: {input_path}")


def convert_file(source: Path, output_dir: Path) -> Path:
    adapted = adapt_prediction(load_json(source), source)
    destination = output_dir / f"{adapted['week_start']}.json"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Refuse accidental replacement: each rollout step should be deliberate.
    with destination.open("x", encoding="utf-8") as file:
        json.dump(adapted, file, indent=4, ensure_ascii=False)
        file.write("\n")

    return destination


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Convert prediction JSON files into normalized weekly context files."
        )
    )
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=DEFAULT_INPUT_DIR,
        help="Prediction JSON file or directory (default: experiment input/).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Destination directory (default: experiment output/).",
    )
    args = parser.parse_args()

    files = find_input_files(args.input)
    if not files:
        print(f"No JSON predictions found in {args.input.resolve()}")
        return 0

    for source in files:
        destination = convert_file(source.resolve(), args.output_dir.resolve())
        print(f"Created: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
