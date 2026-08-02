"""Import the project's development-goal catalog from its YAML-like source.

The source format is intentionally simple and does not require PyYAML. Original
applicability tags are preserved while a canonical discipline list is derived
for compatibility with the rest of the application.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from catalog_translations import (
    CATEGORY_ES,
    goal_description_es,
    translate_name,
)

CANONICAL_DISCIPLINES = ("Bici", "Corrida", "Trail", "Kayak")
TAG_TO_DISCIPLINES = {
    "running": ("Corrida",),
    "trail": ("Trail",),
    "trekking": ("Trail",),
    "orienteering": ("Trail",),
    "cycling": ("Bici",),
    "mtb": ("Bici",),
    "kayak": ("Kayak",),
    "general": CANONICAL_DISCIPLINES,
    "multisport": CANONICAL_DISCIPLINES,
}


def parse_catalog(text: str) -> list[dict]:
    goals = []
    current = None
    mode = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        is_goal_key = (
            line.startswith("  ")
            and not line.startswith("    ")
            and line.strip().endswith(":")
            and not line.lstrip().startswith("#")
        )
        if is_goal_key:
            if current:
                goals.append(current)
            catalog_key = line.strip()[:-1]
            current = {
                "id": f"goal_{catalog_key}",
                "catalog_key": catalog_key,
            }
            mode = None
        elif current and line.startswith("    name:"):
            current["name"] = line.split(":", 1)[1].strip().strip('"')
        elif current and line.startswith("    category:"):
            current["category"] = line.split(":", 1)[1].strip().strip('"')
        elif current and line.startswith("    description:"):
            current["description"] = ""
            mode = "description"
        elif current and line.startswith("    applicable_disciplines:"):
            current["applicability_tags"] = []
            mode = "tags"
        elif (
            current
            and mode == "description"
            and line.startswith("      ")
            and not line.strip().startswith("-")
        ):
            current["description"] = (
                current["description"] + " " + line.strip()
            ).strip()
        elif current and mode == "tags" and line.strip().startswith("- "):
            current["applicability_tags"].append(line.strip()[2:])

    if current:
        goals.append(current)

    required = {
        "id",
        "catalog_key",
        "name",
        "category",
        "description",
        "applicability_tags",
    }
    seen_ids = set()
    for goal in goals:
        missing = required - goal.keys()
        if missing:
            raise ValueError(
                f"{goal.get('catalog_key', 'unknown')}: missing {sorted(missing)}"
            )
        if goal["id"] in seen_ids:
            raise ValueError(f"Duplicate goal ID: {goal['id']}")
        seen_ids.add(goal["id"])
        unknown_tags = [
            tag
            for tag in goal["applicability_tags"]
            if tag not in TAG_TO_DISCIPLINES
        ]
        if unknown_tags:
            raise ValueError(
                f"{goal['catalog_key']}: unknown tags {unknown_tags}"
            )
        goal["disciplines"] = [
            discipline
            for discipline in CANONICAL_DISCIPLINES
            if any(
                discipline in TAG_TO_DISCIPLINES[tag]
                for tag in goal["applicability_tags"]
            )
        ]
        goal["name_es"] = translate_name(goal["name"])
        goal["category_es"] = CATEGORY_ES.get(
            goal["category"], translate_name(goal["category"])
        )
        goal["description_es"] = goal_description_es(
            goal["name_es"],
            goal["category_es"],
            goal["disciplines"],
        )
    return goals


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace an existing destination catalog.",
    )
    args = parser.parse_args()

    goals = parse_catalog(args.source.read_text(encoding="utf-8"))
    if args.destination.exists() and not args.replace:
        raise FileExistsError(
            f"{args.destination} exists; pass --replace deliberately."
        )

    args.destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.destination.with_suffix(args.destination.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            {"development_goals": goals},
            indent=4,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.destination)
    print(f"Imported {len(goals)} development goals.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
