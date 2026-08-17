"""
Calculate prescribed training load from normalized human training plans.

The source of truth is ``data/processed_data/*.json``. This module never
modifies those plans. It converts their steady and interval blocks to the
common component format understood by ``training_load_calculator`` and writes
an auditable derived dataset for later comparison in the UI.

Important domain rules:

* Explicit distance has priority over duration.
* Time-based running, Trail, and cycling blocks use configured reference
  pace/speed values.
* Recovery without a zone defaults to Z2 in the shared calculator.
* Structured Kayak sessions are excluded and reported, as they are considered
  anomalous in this project.
* Non-structured Kayak requires explicit distance; no reference speed is
  invented.
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

from training_load.training_load_calculator import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    TrainingLoadError,
    calculate_session_load,
    load_config,
)


DEFAULT_PLANS_DIR = PROJECT_ROOT / "data" / "processed_data"
DEFAULT_OUTPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "derived"
    / "training_load"
    / "prescribed_load.json"
)


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 hash used to trace every derived value to its source."""
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _component_from_plan_part(
    part: dict[str, Any],
    *,
    block_type: str,
    repetitions: float = 1,
    is_recovery: bool = False,
) -> dict[str, Any]:
    """Translate one normalized plan part without calculating it yet."""
    component = {
        "type": "recovery" if is_recovery else block_type,
        "zone": part.get("zone"),
        "distance_km": part.get("distance_km"),
        "duration_minutes": part.get("duration_min"),
        "repetitions": repetitions,
        "is_recovery": is_recovery,
    }
    return component


def plan_blocks_to_components(
    blocks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Flatten plan blocks into independent load components.

    Interval repetitions apply independently to every work and recovery part.
    The original block and part indices remain available for UI drill-downs.
    """
    components: list[dict[str, Any]] = []
    warnings: list[str] = []

    for block_index, block in enumerate(blocks):
        block_type = block.get("type")
        if block_type == "rest":
            continue

        if block_type == "interval":
            repetitions = block.get("repetitions")
            if repetitions is None:
                # The extractor uses null for a non-repeating sequence whose
                # work/recovery parts are already listed individually.
                repetitions = 1
                warnings.append(
                    f"Interval block {block_index} has no repetition count; "
                    "its listed parts were applied once."
                )
            for group_name, is_recovery in (
                ("work", False),
                ("recovery", True),
            ):
                parts = block.get(group_name) or []
                if not isinstance(parts, list):
                    raise TrainingLoadError(
                        f"Block {block_index} field '{group_name}' must be a list."
                    )
                for part_index, part in enumerate(parts):
                    if not isinstance(part, dict):
                        raise TrainingLoadError(
                            f"Block {block_index} {group_name} part "
                            f"{part_index} must be an object."
                        )
                    component = _component_from_plan_part(
                        part,
                        block_type=block_type,
                        repetitions=repetitions,
                        is_recovery=is_recovery,
                    )
                    component["source_block_index"] = block_index
                    component["source_group"] = group_name
                    component["source_part_index"] = part_index
                    if (
                        component["distance_km"] is None
                        and component["duration_minutes"] is None
                    ):
                        warnings.append(
                            f"Empty {group_name} part {part_index} in interval "
                            f"block {block_index} was ignored."
                        )
                    else:
                        components.append(component)
            continue

        if block_type not in {"steady", "warmup", "cooldown"}:
            raise TrainingLoadError(
                f"Unsupported plan block type '{block_type}' at index "
                f"{block_index}."
            )

        component = _component_from_plan_part(
            block,
            block_type=block_type,
        )
        component["source_block_index"] = block_index
        component["source_group"] = None
        component["source_part_index"] = None
        if (
            component["distance_km"] is None
            and component["duration_minutes"] is None
        ):
            warnings.append(
                f"Empty {block_type} block {block_index} was ignored."
            )
        else:
            components.append(component)

    return components, warnings


def _resolve_prescribed_terrain(
    session: dict[str, Any],
    config: dict[str, Any],
) -> tuple[str | None, list[str]]:
    """Apply only the explicitly configured terrain defaults."""
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
            f"Terrain was not prescribed; defaulted to {terrain} "
            f"for {sport}."
        )

    return terrain, warnings


def calculate_prescribed_session(
    session: dict[str, Any],
    *,
    source_file: Path,
    source_hash: str,
    session_index: int,
    config: dict[str, Any],
    config_hash: str,
) -> dict[str, Any]:
    """Calculate one prescribed session and retain its complete audit context."""
    sport = session.get("sport")
    date = session.get("date")
    structured = bool(session.get("structured"))
    base_result = {
        "session_id": f"{source_file.stem}:{session_index}",
        "date": date,
        "source": {
            "plan_json": source_file.name,
            "plan_json_sha256": source_hash,
            "session_index": session_index,
            "configuration_sha256": config_hash,
        },
        "prescription": {
            "weekday": session.get("weekday"),
            "sport": sport,
            "session_type": session.get("session_type"),
            "terrain": session.get("terrain"),
            "structured": structured,
            "template_id": session.get("template_id"),
            "objective_class": session.get("objective_class"),
            "blocks": session.get("blocks") or [],
        },
    }

    if not date:
        raise TrainingLoadError("Session date is missing.")
    if not sport:
        raise TrainingLoadError("Session sport is missing.")

    # This policy is deliberately checked before flattening. No part of an
    # anomalous structured Kayak session contributes to prescribed load.
    if sport == "Kayak" and structured:
        return {
            **base_result,
            "status": "excluded",
            "load": None,
            "quality": {
                "warnings": [
                    "Structured Kayak session excluded according to project policy."
                ],
            },
        }

    terrain, warnings = _resolve_prescribed_terrain(session, config)
    components, component_warnings = plan_blocks_to_components(
        session.get("blocks") or []
    )
    warnings.extend(component_warnings)

    result = calculate_session_load(
        sport=sport,
        terrain=terrain,
        components=components,
        structured=structured,
        config=config,
    )
    warnings.extend(result["warnings"])

    # Pair shared calculation details with their exact source location. The
    # calculation engine intentionally returns only numeric/domain information.
    component_details = []
    for source_component, calculated_component in zip(
        components,
        result["components"],
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
        "resolved_terrain": result["terrain"],
        "load": {
            "calculated_load": result["total_load"],
            "base_load": result["base_load"],
            "global_scale": result["global_scale"],
            "components": component_details,
        },
        "quality": {"warnings": warnings},
    }


def aggregate_by_date(
    sessions: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """
    Aggregate calculated sessions by date.

    Excluded and failed sessions remain visible and make the day incomplete,
    but never silently contribute a zero to the prescribed total.
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
        warnings = sum(
            len(item.get("quality", {}).get("warnings", []))
            for item in items
        )
        daily[date] = {
            "session_count": len(items),
            "calculated_session_count": len(calculated),
            "session_ids": [item["session_id"] for item in items],
            "prescribed_load": (
                sum(item["load"]["calculated_load"] for item in calculated)
                if not incomplete
                else None
            ),
            "partial_calculated_load": sum(
                item["load"]["calculated_load"] for item in calculated
            ),
            "quality_status": "complete" if not incomplete else "incomplete",
            "warning_count": warnings,
        }
    return daily


def calculate_all_prescribed_loads(
    *,
    plans_dir: Path | str = DEFAULT_PLANS_DIR,
    output_file: Path | str = DEFAULT_OUTPUT_FILE,
    config_path: Path | str = DEFAULT_CONFIG_PATH,
) -> dict[str, Any]:
    """Process every normalized weekly plan and write the derived JSON output."""
    plans_path = Path(plans_dir)
    output_path = Path(output_file)
    configuration_path = Path(config_path)

    config = load_config(configuration_path)
    config_hash = _sha256_file(configuration_path)
    plan_files = sorted(plans_path.glob("*.json"))
    sessions: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for plan_file in plan_files:
        source_hash = _sha256_file(plan_file)
        try:
            plan = json.loads(plan_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            failures.append(
                {"plan_json": plan_file.name, "error": str(error)}
            )
            continue

        plan_sessions = plan.get("sessions")
        if not isinstance(plan_sessions, list):
            failures.append(
                {
                    "plan_json": plan_file.name,
                    "error": "The plan does not contain a sessions list.",
                }
            )
            continue

        for session_index, session in enumerate(plan_sessions):
            try:
                result = calculate_prescribed_session(
                    session,
                    source_file=plan_file,
                    source_hash=source_hash,
                    session_index=session_index,
                    config=config,
                    config_hash=config_hash,
                )
            except (TrainingLoadError, TypeError, ValueError) as error:
                result = {
                    "session_id": f"{plan_file.stem}:{session_index}",
                    "date": (
                        session.get("date")
                        if isinstance(session, dict)
                        else None
                    ),
                    "source": {
                        "plan_json": plan_file.name,
                        "plan_json_sha256": source_hash,
                        "session_index": session_index,
                        "configuration_sha256": config_hash,
                    },
                    "prescription": session if isinstance(session, dict) else {},
                    "status": "failed",
                    "load": None,
                    "quality": {"warnings": [], "error": str(error)},
                }
                failures.append(
                    {
                        "plan_json": plan_file.name,
                        "session_index": session_index,
                        "date": result["date"],
                        "error": str(error),
                    }
                )
            sessions.append(result)

    status_counts = defaultdict(int)
    for session in sessions:
        status_counts[session["status"]] += 1

    output = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "configuration": {
            "file": str(configuration_path.resolve()),
            "sha256": config_hash,
        },
        "summary": {
            "plans_discovered": len(plan_files),
            "sessions_discovered": len(sessions),
            "sessions_calculated": status_counts["calculated"],
            "sessions_excluded": status_counts["excluded"],
            "sessions_failed": status_counts["failed"],
            "dates_discovered": len(
                {session["date"] for session in sessions if session.get("date")}
            ),
        },
        "daily_loads": aggregate_by_date(sessions),
        "sessions": sessions,
        "failures": failures,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output


def main() -> int:
    """Command-line entry point used for local rebuilding and inspection."""
    parser = argparse.ArgumentParser(
        description="Calculate prescribed load from normalized training plans."
    )
    parser.add_argument("--plans-dir", type=Path, default=DEFAULT_PLANS_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_FILE)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args()

    result = calculate_all_prescribed_loads(
        plans_dir=args.plans_dir,
        output_file=args.output,
        config_path=args.config,
    )
    summary = result["summary"]
    print("PRESCRIBED TRAINING LOAD")
    print("=" * 60)
    print(f"Plans discovered     : {summary['plans_discovered']}")
    print(f"Sessions discovered  : {summary['sessions_discovered']}")
    print(f"Sessions calculated  : {summary['sessions_calculated']}")
    print(f"Sessions excluded    : {summary['sessions_excluded']}")
    print(f"Sessions failed      : {summary['sessions_failed']}")
    print(f"Dates discovered     : {summary['dates_discovered']}")
    print(f"Output               : {args.output.resolve()}")
    return 1 if summary["sessions_failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
