"""Local food catalogue used by the nutrition-planning UI."""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from config import PROJECT_ROOT


FOOD_CATALOG_FILE = PROJECT_ROOT / "data" / "nutrition" / "food_catalog.json"


# Values are per 100 g.  Missing values in the original list remain unknown;
# they are intentionally not converted to zero.
DEFAULT_FOODS = (
    ("Gel Powerbar", "Geles", 252, 63, None, None, 499, 1.58),
    ("Haribo Goldbären DieSauren", "Gomitas dulces", 338, 77, 5.4, None, None, 2.99),
    ("GU Stroopwafel Salty's Caramel", "Geles", 469, 69, 3, 19, None, 2.37),
    ("Mentos", "Dulce", 388, 92, None, 1.9, None, 0.60),
    ("Cashew Erdnuss Mix Honig & Salz - Kaufland", "Salado", 541, 44, 15, 33, None, 3.09),
    ("Plátano", "Fruta", 89, 22.8, 1.1, 0.3, 1, 0.50),
    ("SchlangensBande", "Dulces", 343, 84, 0.3, 0.1, None, None),
    ("Haselnuss Schnitte", "Obleas avellana", 527, 56, 6.4, 30, None, None),
    ("Waffel Schnitten", "Obleas limón", 526, 66.7, 3.3, 26.9, None, None),
    ("Feine Butter Waffeln", "Galletas", 483, 70, 6, 19, None, None),
    ("Haribo Berries", "Dulce", 350, 86, 1.1, None, None, 1.19),
    ("Grissini", "Salado", 440, 73, 9.2, 2.6, None, 1.25),
    ("Maoam Kracher Two Fruits", "Dulces", 409, 87, 0.9, 6.1, None, 1.39),
    ("Azúcar", "Dulce", 400, 100, None, None, None, 0.99),
)


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _seed_foods() -> list[dict[str, Any]]:
    created_at = _now()
    return [
        {
            "id": f"food_{index:03d}",
            "name": name,
            "category": category,
            "calories_per_100g": calories,
            "carbs_per_100g": carbs,
            "protein_per_100g": protein,
            "fat_per_100g": fat,
            "sodium_mg_per_100g": sodium,
            "package_cost": cost,
            "created_at": created_at,
            "updated_at": created_at,
        }
        for index, (name, category, calories, carbs, protein, fat, sodium, cost) in enumerate(DEFAULT_FOODS, start=1)
    ]


def _write(foods: list[dict[str, Any]]) -> None:
    FOOD_CATALOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = FOOD_CATALOG_FILE.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"foods": foods}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(FOOD_CATALOG_FILE)


def load_foods() -> list[dict[str, Any]]:
    """Load the catalogue, creating the provided starter list on first use."""
    if not FOOD_CATALOG_FILE.exists():
        foods = _seed_foods()
        _write(foods)
        return foods
    payload = json.loads(FOOD_CATALOG_FILE.read_text(encoding="utf-8"))
    foods = payload.get("foods", [])
    if not isinstance(foods, list):
        raise ValueError("food_catalog.json: 'foods' debe ser una lista.")
    return foods


def save_food(value: dict[str, Any]) -> str:
    foods = load_foods()
    food_id = value.get("id") or f"food_{uuid4().hex[:12]}"
    timestamp = _now()
    replacement = {**value, "id": food_id, "updated_at": timestamp}
    for index, existing in enumerate(foods):
        if existing.get("id") == food_id:
            replacement["created_at"] = existing.get("created_at", timestamp)
            foods[index] = replacement
            break
    else:
        replacement["created_at"] = timestamp
        foods.append(replacement)
    _write(foods)
    return food_id


def delete_food(food_id: str) -> None:
    foods = load_foods()
    retained = [item for item in foods if item.get("id") != food_id]
    if len(retained) == len(foods):
        raise KeyError("Alimento desconocido.")
    _write(retained)
