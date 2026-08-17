"""Human-readable renderer for normalized Garmin activity JSON files."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


SECTION_TITLES = (
    ("GENERAL INFORMATION", "activity"),
    ("PERFORMANCE", "summary"),
    ("LOAD", "load"),
    ("PHYSIOLOGY", "physiology"),
    ("ENVIRONMENT", "environment"),
)


def is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def display(value: Any) -> str:
    if value is None:
        return "not available"
    if isinstance(value, float) and not math.isfinite(value):
        return "not available"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False, default=str)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def label(name: str) -> str:
    return name.replace("_", " ").strip().title()


def append_mapping(lines: list[str], mapping: dict, *, indent: int = 0):
    padding = " " * indent
    width = max((len(label(key)) for key in mapping), default=0)
    for key, value in mapping.items():
        if is_scalar(value):
            lines.append(f"{padding}{label(key):<{width}} : {display(value)}")
        else:
            lines.append(f"{padding}{label(key)}:")
            if isinstance(value, dict):
                append_mapping(lines, value, indent=indent + 2)
            elif len(value) > 20:
                lines.append(
                    f"{padding}  {len(value)} entries "
                    "(complete values retained in JSON)"
                )
            else:
                for item in value:
                    lines.append(f"{padding}  - {display(item)}")


def render_activity_text(activity_data: dict) -> str:
    """Render normalized activity data without including GPS or record tracks."""
    lines = ["GARMIN ACTIVITY", "=" * 88]

    for title, key in SECTION_TITLES:
        lines.extend(["", title, "-" * 88])
        section = activity_data.get(key, {})
        if section:
            append_mapping(lines, section)
        else:
            lines.append("No data available.")

    additional = {
        "gps_points": len(activity_data.get("gps", {}).get("points", [])),
        "temporal_records": len(activity_data.get("records", [])),
        "raw_message_types": len(
            activity_data.get("raw_metrics", {})
            .get("interpreted_messages", {})
        ),
        "decoder_errors": activity_data.get("raw_metrics", {}).get(
            "decoder_errors", []
        ),
    }
    lines.extend(["", "ADDITIONAL STATISTICS", "-" * 88])
    append_mapping(lines, additional)

    return "\n".join(lines) + "\n"


def write_activity_text(activity_data: dict, output_path: Path):
    output_path.write_text(
        render_activity_text(activity_data),
        encoding="utf-8",
    )
