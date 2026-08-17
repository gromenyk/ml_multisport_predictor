from collections import defaultdict
from datetime import date, datetime, timedelta
import argparse
import hashlib
import json
from pathlib import Path

from dataset import TrainingDataset, validate_consecutive_context
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
PREDICTIONS_DIR = PROJECT_ROOT / "ML" / "results" / "predictions"
PLANNED_HISTORY_DIR = PROJECT_ROOT / "data" / "planned_history"


def sha256_file(path):
    """Fingerprint the exact production model used by a prediction."""
    digest = hashlib.sha256()
    with open(path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def production_model_fingerprints(model_dir=PRODUCTION_MODEL_DIR):
    """Return stable model hashes without embedding large binaries."""
    return {
        path.name: sha256_file(path)
        for path in sorted(Path(model_dir).glob("*.pkl"))
    }


def format_prediction_text(prediction):
    lines = [
        "NEXT WEEK PREDICTION",
        "=" * 80,
        f"Week       : {prediction['week_start']} to {prediction['week_end']}",
        f"Generated  : {prediction['generated_at']}",
        f"Context    : {', '.join(prediction['context_weeks'])}",
        "",
    ]

    for session in prediction["sessions"]:
        if session["sport"] == "Descanso":
            workout = "Rest"
        elif session.get("template"):
            workout = session["template"]
        elif session.get("distance_km") is not None:
            workout = f"{session['distance_km']} km"
        else:
            workout = "-"

        lines.extend(
            [
                f"{session['date']} · {session['weekday']}",
                f"  Sport       : {session['sport']}",
                f"  Session type: {session.get('session_type') or '-'}",
                f"  Structured  : {'Yes' if session.get('structured') else 'No'}",
                f"  Objective   : {session.get('objective_class') or '-'}",
                f"  Terrain     : {session.get('terrain') or '-'}",
                f"  Zone        : {session.get('zone') or '-'}",
                f"  Workout     : {workout}",
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def save_prediction_files(prediction):
    PREDICTIONS_DIR.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S_%f")
    basename = f"prediction_{prediction['week_start']}_{timestamp}"
    json_path = PREDICTIONS_DIR / f"{basename}.json"
    text_path = PREDICTIONS_DIR / f"{basename}.txt"

    with open(json_path, "x", encoding="utf-8") as file:
        json.dump(prediction, file, indent=4, ensure_ascii=False)

    with open(text_path, "x", encoding="utf-8") as file:
        file.write(format_prediction_text(prediction))


def build_prediction(*, cutoff_date=None, model_dir=PRODUCTION_MODEL_DIR, save=True):
    dataset = TrainingDataset(
        DATA_DIR, context_weeks=CONTEXT_WEEKS, cutoff_date=cutoff_date,
        planned_history_dir=PLANNED_HISTORY_DIR,
    )

    if len(dataset.weeks) < CONTEXT_WEEKS:
        raise ValueError("At least four historical weeks are required.")

    history = validate_consecutive_context(dataset.weeks, CONTEXT_WEEKS)
    last_week = dataset.weeks[-1]
    next_start = date.fromisoformat(last_week["week_end"]) + timedelta(days=1)
    next_end = next_start + timedelta(days=6)
    if cutoff_date is None:
        current_monday = date.today() - timedelta(days=date.today().weekday())
        if next_start > current_monday + timedelta(days=7):
            raise ValueError(
                "Solo se puede predecir la semana calendario inmediatamente siguiente. "
                f"La próxima habilitada comienza el {(current_monday + timedelta(days=7)).isoformat()}."
            )

    models = load_models(Path(model_dir))
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
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "data_cutoff_date": cutoff_date,
        "context_weeks": [week["week_start"] for week in history],
        "context_sources": [
            {
                "week_start": week["week_start"],
                "week_end": week["week_end"],
                "source": (week.get("metadata") or {}).get("source", "human_plan"),
            }
            for week in history
        ],
        "production_models": production_model_fingerprints(model_dir),
        "sessions": sessions,
    }

    if save:
        save_prediction_files(prediction)

    return prediction


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cutoff")
    parser.add_argument("--model-dir", type=Path, default=PRODUCTION_MODEL_DIR)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-save", action="store_true")
    args = parser.parse_args()
    result = build_prediction(cutoff_date=args.cutoff, model_dir=args.model_dir, save=not args.no_save)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        print(json.dumps(result, ensure_ascii=False))
