"""
Calculate training load for every archived model prediction.

Prediction JSON files in ``ML/results/predictions`` are immutable snapshots:
several files may target the same week because each model run is preserved.
This module calculates each snapshot independently and never modifies or
promotes predictions into the human historical dataset.

The normalized blocks are converted with the same rules used for prescribed
human sessions, then passed to the source-independent training-load engine.
The resulting derived JSON is designed for future UI comparison while keeping
the generation timestamp and source file that produced every value.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from training_load.prescribed_load import (  # noqa: E402
    plan_blocks_to_components,
)
from training_load.training_load_calculator import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    TrainingLoadError,
    calculate_session_load,
    load_config,
)


DEFAULT_PREDICTIONS_DIR = PROJECT_ROOT / "ML" / "results" / "predictions"
DEFAULT_OUTPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "derived"
    / "training_load"
    / "predicted_load.json"
)


def _sha256_file(path: Path) -> str:
    """Hash a file so a derived load can always be traced to exact input."""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _prediction_id(prediction_file: Path) -> str:
    """Use the versioned filename as the stable prediction identifier."""
    return prediction_file.stem


def _resolve_predicted_terrain(
    session: dict[str, Any],
    config: dict[str, Any],
) -> tuple[str | None, list[str]]:
    """
    Resolve only source-specific terrain defaults.

    Structured sessions are left as null here because the shared calculator
    applies its configured structured-session default. Kayak has its own
    configured default because prediction records normally omit water terrain.
    """
    terrain = session.get("terrain")
    warnings: list[str] = []
    sport = session.get("sport")
    structured = bool(session.get("structured"))
    if terrain is None and not structured and sport != "Descanso":
        defaults = config["calculation_rules"][
            "unstructured_session_default_terrain_by_sport"
        ]
        terrain = defaults.get(sport)
        if terrain is None:
            return None, warnings
        warnings.append(
            f"Terrain was not predicted; defaulted to {terrain} "
            f"for {sport}."
        )
    return terrain, warnings


def calculate_predicted_session(
    session: dict[str, Any],
    *,
    session_index: int,
    prediction_id: str,
    prediction_file: Path,
    prediction_hash: str,
    config: dict[str, Any],
    config_hash: str,
) -> dict[str, Any]:
    """Calculate one predicted session and retain its complete audit trail."""
    date = session.get("date")
    sport = session.get("sport")
    structured = bool(session.get("structured"))
    base_result = {
        "session_id": f"{prediction_id}:{session_index}",
        "date": date,
        "source": {
            "prediction_id": prediction_id,
            "prediction_json": prediction_file.name,
            "prediction_json_sha256": prediction_hash,
            "session_index": session_index,
            "configuration_sha256": config_hash,
        },
        "prediction": session,
    }

    if not date:
        raise TrainingLoadError("Predicted session date is missing.")
    if not sport:
        raise TrainingLoadError("Predicted session sport is missing.")

    # Structured Kayak is an anomaly in both prescribed and predicted data.
    # Exclusion prevents an invalid prediction from silently becoming load.
    if sport == "Kayak" and structured:
        return {
            **base_result,
            "status": "excluded",
            "load": None,
            "quality": {
                "warnings": [
                    "Structured Kayak prediction excluded according to "
                    "project policy."
                ]
            },
        }

    terrain, warnings = _resolve_predicted_terrain(session, config)
    components, component_warnings = plan_blocks_to_components(
        session.get("blocks") or []
    )
    warnings.extend(component_warnings)

    calculated = calculate_session_load(
        sport=sport,
        terrain=terrain,
        components=components,
        structured=structured,
        config=config,
    )
    warnings.extend(calculated["warnings"])

    # Restore the block locations removed by the generic calculation engine.
    component_details = []
    for source_component, calculated_component in zip(
        components,
        calculated["components"],
    ):
        component_details.append(
            {
                **calculated_component,
                "type": source_component["type"],
                "source_block_index": source_component["source_block_index"],
                "source_group": source_component["source_group"],
                "source_part_index": source_component["source_part_index"],
            }
        )

    return {
        **base_result,
        "status": "calculated",
        "resolved_terrain": calculated["terrain"],
        "load": {
            "calculated_load": calculated["total_load"],
            "base_load": calculated["base_load"],
            "global_scale": calculated["global_scale"],
            "components": component_details,
        },
        "quality": {"warnings": warnings},
    }


def aggregate_prediction_by_date(
    sessions: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """
    Sum sessions by date inside one prediction snapshot.

    If any session is excluded or failed, the final daily value is null and
    only ``partial_calculated_load`` is exposed. This prevents an incomplete
    prediction from looking like a valid low-load day.
    """
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for session in sessions:
        if session.get("date"):
            grouped[session["date"]].append(session)

    daily: dict[str, dict[str, Any]] = {}
    for date in sorted(grouped):
        items = grouped[date]
        calculated = [item for item in items if item["status"] == "calculated"]
        incomplete = [item for item in items if item["status"] != "calculated"]
        partial_load = sum(
            item["load"]["calculated_load"] for item in calculated
        )
        daily[date] = {
            "session_count": len(items),
            "calculated_session_count": len(calculated),
            "session_ids": [item["session_id"] for item in items],
            "predicted_load": partial_load if not incomplete else None,
            "partial_calculated_load": partial_load,
            "quality_status": "complete" if not incomplete else "incomplete",
            "warning_count": sum(
                len(item.get("quality", {}).get("warnings", []))
                for item in items
            ),
        }
    return daily


def calculate_prediction_snapshot(
    prediction: dict[str, Any],
    *,
    prediction_file: Path,
    prediction_hash: str,
    config: dict[str, Any],
    config_hash: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Calculate all sessions belonging to one archived model generation."""
    prediction_id = _prediction_id(prediction_file)
    source_sessions = prediction.get("sessions")
    if not isinstance(source_sessions, list):
        raise TrainingLoadError(
            "The prediction does not contain a sessions list."
        )

    sessions: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for session_index, session in enumerate(source_sessions):
        try:
            if not isinstance(session, dict):
                raise TrainingLoadError("Predicted session must be an object.")
            result = calculate_predicted_session(
                session,
                session_index=session_index,
                prediction_id=prediction_id,
                prediction_file=prediction_file,
                prediction_hash=prediction_hash,
                config=config,
                config_hash=config_hash,
            )
        except (TrainingLoadError, TypeError, ValueError) as error:
            result = {
                "session_id": f"{prediction_id}:{session_index}",
                "date": (
                    session.get("date") if isinstance(session, dict) else None
                ),
                "source": {
                    "prediction_id": prediction_id,
                    "prediction_json": prediction_file.name,
                    "prediction_json_sha256": prediction_hash,
                    "session_index": session_index,
                    "configuration_sha256": config_hash,
                },
                "prediction": session if isinstance(session, dict) else {},
                "status": "failed",
                "load": None,
                "quality": {"warnings": [], "error": str(error)},
            }
            failures.append(
                {
                    "prediction_id": prediction_id,
                    "session_index": session_index,
                    "date": result["date"],
                    "error": str(error),
                }
            )
        sessions.append(result)

    status_counts = defaultdict(int)
    for session in sessions:
        status_counts[session["status"]] += 1

    snapshot = {
        "prediction_id": prediction_id,
        "source": {
            "prediction_json": prediction_file.name,
            "prediction_json_sha256": prediction_hash,
        },
        "week_start": prediction.get("week_start"),
        "week_end": prediction.get("week_end"),
        "generated_at": prediction.get("generated_at"),
        "context_weeks": prediction.get("context_weeks") or [],
        "summary": {
            "sessions_discovered": len(sessions),
            "sessions_calculated": status_counts["calculated"],
            "sessions_excluded": status_counts["excluded"],
            "sessions_failed": status_counts["failed"],
        },
        "daily_loads": aggregate_prediction_by_date(sessions),
        "sessions": sessions,
    }
    return snapshot, failures


def calculate_all_predicted_loads(
    *,
    predictions_dir: Path | str = DEFAULT_PREDICTIONS_DIR,
    output_file: Path | str = DEFAULT_OUTPUT_FILE,
    config_path: Path | str = DEFAULT_CONFIG_PATH,
) -> dict[str, Any]:
    """Calculate every archived prediction and write one derived collection."""
    predictions_path = Path(predictions_dir)
    output_path = Path(output_file)
    configuration_path = Path(config_path)
    config = load_config(configuration_path)
    config_hash = _sha256_file(configuration_path)
    prediction_files = sorted(predictions_path.glob("prediction_*.json"))

    snapshots: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for prediction_file in prediction_files:
        prediction_hash = _sha256_file(prediction_file)
        try:
            prediction = json.loads(
                prediction_file.read_text(encoding="utf-8")
            )
            snapshot, snapshot_failures = calculate_prediction_snapshot(
                prediction,
                prediction_file=prediction_file,
                prediction_hash=prediction_hash,
                config=config,
                config_hash=config_hash,
            )
            snapshots.append(snapshot)
            failures.extend(snapshot_failures)
        except (
            OSError,
            json.JSONDecodeError,
            TrainingLoadError,
            TypeError,
            ValueError,
        ) as error:
            failures.append(
                {
                    "prediction_id": _prediction_id(prediction_file),
                    "prediction_json": prediction_file.name,
                    "error": str(error),
                }
            )

    session_counts = defaultdict(int)
    for snapshot in snapshots:
        summary = snapshot["summary"]
        for status in ("calculated", "excluded", "failed"):
            session_counts[status] += summary[f"sessions_{status}"]

    output = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "configuration": {
            "file": str(configuration_path.resolve()),
            "sha256": config_hash,
        },
        "summary": {
            "prediction_files_discovered": len(prediction_files),
            "predictions_calculated": len(snapshots),
            "sessions_calculated": session_counts["calculated"],
            "sessions_excluded": session_counts["excluded"],
            "sessions_failed": session_counts["failed"],
        },
        "predictions": snapshots,
        "failures": failures,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output


def main() -> int:
    """Command-line entry point for rebuilding predicted load."""
    parser = argparse.ArgumentParser(
        description="Calculate load for all archived model predictions."
    )
    parser.add_argument(
        "--predictions-dir",
        type=Path,
        default=DEFAULT_PREDICTIONS_DIR,
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_FILE)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args()

    result = calculate_all_predicted_loads(
        predictions_dir=args.predictions_dir,
        output_file=args.output,
        config_path=args.config,
    )
    summary = result["summary"]
    print("PREDICTED TRAINING LOAD")
    print("=" * 60)
    print(
        "Prediction files     : "
        f"{summary['prediction_files_discovered']}"
    )
    print(f"Predictions calculated: {summary['predictions_calculated']}")
    print(f"Sessions calculated   : {summary['sessions_calculated']}")
    print(f"Sessions excluded     : {summary['sessions_excluded']}")
    print(f"Sessions failed       : {summary['sessions_failed']}")
    print(f"Output                : {args.output.resolve()}")
    return 1 if result["failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
