"""Read and filter the historical structured-workout template library."""

from __future__ import annotations

import csv
import json
from functools import lru_cache
from pathlib import Path
import unicodedata


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEMPLATES_FILE = PROJECT_ROOT / "app" / "backend" / "data" / "templates.json"
CLASSIFICATION_FILE = (
    PROJECT_ROOT
    / "app"
    / "backend"
    / "data"
    / "structured_templates_with_objective_class_draft.csv"
)


def _plain(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def _zones(blocks):
    values = set()
    for block in blocks or []:
        if block.get("zone"):
            values.add(str(block["zone"]).upper())
        for group in ("work", "recovery"):
            for part in block.get(group, []) or []:
                if part.get("zone"):
                    values.add(str(part["zone"]).upper())
    return values


@lru_cache(maxsize=1)
def load_template_library():
    """Join block definitions and objective classes using their signature."""
    payload = json.loads(TEMPLATES_FILE.read_text(encoding="utf-8"))
    classifications = {}
    with CLASSIFICATION_FILE.open(newline="", encoding="utf-8") as file:
        for row in csv.DictReader(file):
            classifications[row["signature"]] = row
    result = []
    for item in payload.get("templates", []):
        classification = classifications.get(item.get("signature"), {})
        blocks = item.get("template", {}).get("blocks", [])
        result.append({
            "template_id": item.get("template_id"),
            "signature": item.get("signature"),
            "sport": item.get("sport") or item.get("template", {}).get("sport"),
            "blocks": blocks,
            "count": int(classification.get("count") or item.get("count") or 0),
            "objective_class": classification.get("objective_class"),
            "example_week": classification.get("example_week"),
            "zones": sorted(_zones(blocks)),
        })
    return result


def compatible_templates(operation, discipline):
    """Return real historical templates compatible with one intense recipe."""
    matches = []
    for item in load_template_library():
        if item.get("sport") != discipline:
            continue
        objective = _plain(item.get("objective_class"))
        zones = set(item.get("zones", []))
        if operation == "threshold":
            compatible = "umbral" in objective and "Z4" in zones
        elif operation == "maximal_aerobic":
            compatible = "vo2max" in objective and "Z5" in zones
        else:
            compatible = False
        if compatible:
            matches.append(item)
    return matches

