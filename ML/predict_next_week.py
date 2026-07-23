from collections import defaultdict
from datetime import date, timedelta
import json
from pathlib import Path

from dataset import TrainingDataset
from evaluate_pipeline import (
    CONTEXT_WEEKS,
    DATA_DIR,
    INV_SESSION_TYPE_MAP,
    INV_TERRAIN_MAP,
    INV_ZONE_MAP,
    SPORT_MAP,
    SPORT_NAMES,
    WEEKDAY_MAP,
    build_structured_library,
    load_models,
    predict_pipeline,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_MODEL_DIR = PROJECT_ROOT / "ML" / "models" / "production"
OUTPUT_FILE = PROJECT_ROOT / "ML" / "results" / "next_week_prediction.json"


def build_prediction():
    dataset = TrainingDataset(DATA_DIR, context_weeks=CONTEXT_WEEKS)

    if len(dataset.weeks) < CONTEXT_WEEKS:
        raise ValueError("At least four historical weeks are required.")

    history = dataset.weeks[-CONTEXT_WEEKS:]
    last_week = dataset.weeks[-1]
    next_start = date.fromisoformat(last_week["week_end"]) + timedelta(days=1)
    next_end = next_start + timedelta(days=6)

    models = load_models(PRODUCTION_MODEL_DIR)
    library = build_structured_library(dataset.weeks)

    (
        sports,
        session_types,
        structured,
        objectives,
        templates,
        terrain,
        zones,
        distances,
    ) = predict_pipeline(history, models, library, defaultdict(int))

    sessions = []

    for day in range(7):
        sport = SPORT_NAMES[sports[day]]
        session_type = INV_SESSION_TYPE_MAP[session_types[day]]
        session_date = next_start + timedelta(days=day)

        if sport == "Descanso":
            session = {
                "date": session_date.isoformat(),
                "weekday": WEEKDAY_MAP[day],
                "sport": sport,
                "session_type": None,
                "structured": False,
                "objective_class": None,
                "template": None,
                "terrain": None,
                "zone": None,
                "distance_km": None,
                "blocks": [{"type": "rest"}],
            }
        elif structured[day]:
            template = templates[day]
            session = {
                "date": session_date.isoformat(),
                "weekday": WEEKDAY_MAP[day],
                "sport": sport,
                "session_type": session_type,
                "structured": True,
                "objective_class": objectives[day],
                "template": template.get("signature") if template else None,
                "terrain": None,
                "zone": None,
                "distance_km": None,
                "blocks": template.get("blocks", []) if template else [],
            }
        else:
            terrain_name = INV_TERRAIN_MAP[terrain[day]] if terrain[day] is not None else None
            zone_name = INV_ZONE_MAP[zones[day]] if zones[day] is not None else None
            block = {
                "type": "steady",
                "zone": zone_name,
                "distance_km": distances[day],
            }
            session = {
                "date": session_date.isoformat(),
                "weekday": WEEKDAY_MAP[day],
                "sport": sport,
                "session_type": session_type,
                "structured": False,
                "objective_class": None,
                "template": None,
                "terrain": terrain_name,
                "zone": zone_name,
                "distance_km": distances[day],
                "blocks": [block],
            }

        sessions.append(session)

    prediction = {
        "week_start": next_start.isoformat(),
        "week_end": next_end.isoformat(),
        "context_weeks": [week["week_start"] for week in history],
        "sessions": sessions,
    }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(prediction, file, indent=4, ensure_ascii=False)

    return prediction


if __name__ == "__main__":
    result = build_prediction()
    print(json.dumps(result, ensure_ascii=False))
