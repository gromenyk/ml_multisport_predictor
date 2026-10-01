from collections import defaultdict
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

try:  # Imported through the ML package by the application.
    from .dataset import TrainingDataset
    from .features import encode_session, SPORT_MAP
except ImportError:  # Direct script execution from ML/.
    from dataset import TrainingDataset
    from features import encode_session, SPORT_MAP


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_ROOT / "data" / "processed_data"
MODEL_DIR = PROJECT_ROOT / "ML" / "models" / "evaluation"
OUTPUT_FILE = PROJECT_ROOT / "ML" / "results" / "pipeline_predictions.txt"

CONTEXT_WEEKS = 4
TRAIN_RATIO = 0.8

WEEKDAY_MAP = {
    0: "seg", 1: "ter", 2: "qua", 3: "qui",
    4: "sex", 5: "sáb", 6: "dom",
}

SESSION_TYPE_MAP = {
    None: 0,
    "Leve": 1,
    "Regenerativo": 2,
    "Avaliação": 3,
    "Longão": 4,
    "Simulado": 5,
    "Contínuo": 6,
    "Acelerações": 7,
    "Ritmado": 8,
    "Rest": 9,
    "Transição": 10,
    "Subidas": 11,
    "Otros": 12,
}

WEEK_TYPE_MAP = {
    "Semana Controle": 0,
    "Semana Regenerativa": 1,
    "Desenvolvimento Aeróbico": 2,
    "Desenvolvimento Anaeróbico": 3,
    "Técnica e Fortalecimento": 4,
    "Pico de Performance": 5,
    "Otros": 6,
    "None": 7,
}

TERRAIN_MAP = {"Ondulado": 0, "Plano": 1, "Montaña": 2}
ZONE_MAP = {"Z1": 0, "Z2": 1, "Z3": 2}

INV_SESSION_TYPE_MAP = {value: key for key, value in SESSION_TYPE_MAP.items()}
SPORT_NAMES = {value: key for key, value in SPORT_MAP.items()}
INV_TERRAIN_MAP = {value: key for key, value in TERRAIN_MAP.items()}
INV_ZONE_MAP = {value: key for key, value in ZONE_MAP.items()}

# This is intentionally deterministic, matching the original pipeline.
STRUCTURED_SESSION_TYPES = {
    "Acelerações", "Avaliação", "Ritmado", "Subidas"
}


def get_session_of_day(week, day_idx):
    weekday = WEEKDAY_MAP[day_idx]
    sessions = [
        session for session in week.get("sessions", [])
        if session.get("weekday") == weekday
    ]
    return sessions[0] if sessions else None


def encode_history(history, include_week_type):
    features = []
    for week in history:
        if include_week_type:
            features.append(
                WEEK_TYPE_MAP.get(week.get("week_type"), WEEK_TYPE_MAP["Otros"])
            )
        for day in range(7):
            features.extend(encode_session(get_session_of_day(week, day)))
    return features


def build_signature(session):
    parts = []
    for block in session.get("blocks", []):
        block_type = block.get("type")
        if block_type == "warmup":
            parts.append(f"WU{block.get('duration_min')}")
        elif block_type == "cooldown":
            parts.append(f"CD{block.get('duration_min')}")
        elif block_type == "steady":
            zone = block.get("zone")
            if block.get("duration_min") is not None:
                parts.append(f"{block['duration_min']}'{zone}")
            elif block.get("distance_km") is not None:
                parts.append(f"{block['distance_km']}k{zone}")
        elif block_type == "interval":
            repetitions = block.get("repetitions")
            work = (block.get("work") or [{}])[0]
            recovery = (block.get("recovery") or [{}])[0]
            work_value = (
                f"{work.get('distance_km')}k"
                if work.get("distance_km") is not None
                else f"{work.get('duration_min')}'"
            )
            recovery_value = (
                f"{recovery.get('duration_min')}'"
                if recovery.get("duration_min") is not None
                else f"{recovery.get('distance_km')}k"
            )
            parts.append(
                f"{repetitions}×({work_value}{work.get('zone')}/"
                f"{recovery_value}{recovery.get('zone')})"
            )
    return " - ".join(parts)


def build_structured_library(weeks):
    library = defaultdict(list)
    for week in weeks:
        for original in week.get("sessions", []):
            if not original.get("structured", False):
                continue
            objective = original.get("objective_class")
            if objective is None:
                continue
            session = original.copy()
            session["signature"] = build_signature(session)
            library[objective].append(session)
    return library


def load_models(model_dir=MODEL_DIR):
    distance_models = {
        sport: joblib.load(model_dir / f"distance_{sport.lower()}.pkl")
        for sport in ("Trail", "Corrida", "Bici")
    }
    return {
        "sport": joblib.load(model_dir / "sport_model.pkl"),
        "session": joblib.load(model_dir / "session_type_model.pkl"),
        "terrain": joblib.load(model_dir / "terrain_model.pkl"),
        "zone": joblib.load(model_dir / "zone_model.pkl"),
        "objective": joblib.load(model_dir / "objective_class_model.pkl"),
        "objective_encoder": joblib.load(model_dir / "objective_class_encoder.pkl"),
        "objective_columns": joblib.load(model_dir / "objective_class_columns.pkl"),
        "distance": distance_models,
    }


def predict_sports(history, model):
    predicted = []
    for day in range(7):
        features = encode_history(history, include_week_type=False)
        features.extend([
            day,
            SPORT_MAP["Descanso"] if day == 0 else predicted[-1],
        ])
        predicted.append(int(model.predict(np.asarray([features]))[0]))
    return predicted


def predict_sessions(history, sports, model):
    predicted = []
    for day in range(7):
        features = encode_history(history, include_week_type=True)
        features.extend([day, sports[day]])
        predicted.append(int(model.predict(np.asarray([features]))[0]))
    return predicted


def predict_structured(sessions):
    return [
        INV_SESSION_TYPE_MAP[session] in STRUCTURED_SESSION_TYPES
        for session in sessions
    ]


def predict_objectives(sports, sessions, structured, models):
    predicted = []
    columns = ["weekday", "sport", "session_type", "terrain"]
    for day in range(7):
        if not structured[day]:
            predicted.append(None)
            continue
        row = pd.DataFrame([{
            "weekday": WEEKDAY_MAP[day],
            "sport": SPORT_NAMES[sports[day]],
            "session_type": INV_SESSION_TYPE_MAP[sessions[day]],
            "terrain": None,
        }])
        encoded = pd.get_dummies(
            row, prefix=columns, columns=columns, dummy_na=True
        ).reindex(columns=models["objective_columns"], fill_value=0)
        label = models["objective"].predict(encoded)[0]
        predicted.append(models["objective_encoder"].inverse_transform([label])[0])
    return predicted


def predict_templates(objectives, structured, library, rotation):
    predicted = []
    for day in range(7):
        if not structured[day]:
            predicted.append(None)
            continue
        templates = library.get(objectives[day])
        if not templates:
            predicted.append(None)
            continue
        index = rotation[objectives[day]]
        predicted.append(templates[index])
        rotation[objectives[day]] = (index + 1) % len(templates)
    return predicted


def predict_terrain(history, sports, structured, model):
    predicted = []
    for day in range(7):
        if structured[day]:
            predicted.append(None)
            continue
        features = encode_history(history, include_week_type=True)
        features.extend([day, sports[day]])
        predicted.append(int(model.predict(np.asarray([features]))[0]))
    return predicted


def predict_zones(history, sports, sessions, structured, terrain, model):
    predicted = []
    for day in range(7):
        if structured[day] or terrain[day] is None:
            predicted.append(None)
            continue
        features = encode_history(history, include_week_type=True)
        features.extend([day, sports[day], sessions[day], terrain[day]])
        predicted.append(int(model.predict(np.asarray([features]))[0]))
    return predicted


def predict_distances(history, sports, structured, terrain, zones, models):
    predicted = []
    for day in range(7):
        sport = SPORT_NAMES[sports[day]]
        if (
            structured[day]
            or sport == "Descanso"
            or terrain[day] is None
            or zones[day] is None
            or sport not in models
        ):
            predicted.append(None)
            continue
        bundle = models[sport]
        features = encode_history(history, include_week_type=True)
        features.extend([day, terrain[day], zones[day]])
        label = int(bundle["model"].predict(np.asarray([features]))[0])
        predicted.append(bundle["inverse_distance_map"][label])
    return predicted


def predict_pipeline(history, models, library, rotation):
    sports = predict_sports(history, models["sport"])
    sessions = predict_sessions(history, sports, models["session"])
    structured = predict_structured(sessions)
    objectives = predict_objectives(sports, sessions, structured, models)
    templates = predict_templates(objectives, structured, library, rotation)
    terrain = predict_terrain(history, sports, structured, models["terrain"])
    zones = predict_zones(
        history, sports, sessions, structured, terrain, models["zone"]
    )
    distances = predict_distances(
        history, sports, structured, terrain, zones, models["distance"]
    )
    return sports, sessions, structured, objectives, templates, terrain, zones, distances


def real_values(week):
    sports, sessions, objectives, terrains, zones, distances = [], [], [], [], [], []
    for day in range(7):
        session = get_session_of_day(week, day)
        if session is None:
            sports.append(SPORT_MAP["Descanso"])
            sessions.append(None)
            objectives.append(None)
            terrains.append(None)
            zones.append(None)
            distances.append(None)
            continue
        sports.append(SPORT_MAP[session["sport"]])
        sessions.append(session.get("session_type"))
        objectives.append(session.get("objective_class"))
        terrain = session.get("terrain")
        terrains.append(None if terrain == "Subida" else terrain)
        blocks = session.get("blocks", [])
        zones.append(
            blocks[0].get("zone")
            if blocks and terrain != "Subida" and blocks[0].get("zone") in ZONE_MAP
            else None
        )
        distances.append(
            blocks[0].get("distance_km")
            if len(blocks) == 1 and blocks[0].get("type") == "steady"
            else None
        )
    return sports, sessions, objectives, terrains, zones, distances


def display(value):
    return "-" if value is None else str(value)


def comparison(predicted, real):
    if predicted is None or real is None:
        return "-"
    return "✓" if predicted == real else "✗"


def evaluate():
    dataset = TrainingDataset(DATA_DIR, context_weeks=CONTEXT_WEEKS)
    models = load_models()
    library = build_structured_library(dataset.weeks)
    rotation = defaultdict(int)
    score_lists = {key: [] for key in ("sport", "session", "terrain", "zone", "distance")}

    with open(OUTPUT_FILE, "w", encoding="utf-8") as output:
        output.write("=" * 70 + "\nSPORT + SESSION TYPE PIPELINE\n" + "=" * 70 + "\n\n")
        cutoff_index = int(len(dataset.weeks) * TRAIN_RATIO)
        predict_weeks = len(dataset.weeks) - cutoff_index
        output.write(f"Weeks evaluated : {predict_weeks}\n\n")

        first_week = cutoff_index
        for week_index in range(first_week, len(dataset.weeks)):
            history = dataset.weeks[week_index - CONTEXT_WEEKS:week_index]
            target = dataset.weeks[week_index]
            predicted = predict_pipeline(history, models, library, rotation)
            real = real_values(target)
            ps, pst, pstr, pobj, ptemp, pter, pzone, pdist = predicted
            rs, rst, robj, rter, rzone, rdist = real

            pred_terrain_names = [INV_TERRAIN_MAP[x] if x is not None else None for x in pter]
            pred_zone_names = [INV_ZONE_MAP[x] if x is not None else None for x in pzone]
            pred_sport_names = [SPORT_NAMES[x] for x in ps]
            pred_session_names = [INV_SESSION_TYPE_MAP[x] for x in pst]
            real_sport_names = [SPORT_NAMES[x] for x in rs]

            score_lists["sport"].append(sum(a == b for a, b in zip(pred_sport_names, real_sport_names)))
            score_lists["session"].append(sum(a == b for a, b in zip(pred_session_names, rst)))
            valid_terrain = [i for i, value in enumerate(rter) if value is not None]
            valid_zone = [i for i, value in enumerate(rzone) if value is not None]
            valid_distance = [i for i, value in enumerate(rdist) if value is not None]
            terrain_correct = sum(pred_terrain_names[i] == rter[i] for i in valid_terrain)
            zone_correct = sum(pred_zone_names[i] == rzone[i] for i in valid_zone)
            distance_correct = sum(pdist[i] == rdist[i] for i in valid_distance)
            score_lists["terrain"].append(terrain_correct)
            score_lists["zone"].append(zone_correct)
            score_lists["distance"].append(distance_correct)

            output.write("=" * 70 + f"\nWeek {target['week_start']}\n" + "=" * 70 + "\n\n")
            output.write(
                "Day\tPred Sport\tPred Session\tStruct\tPred Objective\t"
                "Pred Structured Session\tPred Terrain\tPred Zone\tPred Dist\t"
                "Real Sport\tReal Session\tReal Objective\tReal Terrain\tReal Zone\t"
                "Real Dist\tResult\n" + "-" * 70 + "\n"
            )

            for day in range(7):
                template = ptemp[day]["signature"] if ptemp[day] else None
                result = "".join((
                    comparison(pred_sport_names[day], real_sport_names[day]),
                    comparison(pred_session_names[day], rst[day]),
                    comparison(pred_terrain_names[day], rter[day]),
                    comparison(pred_zone_names[day], rzone[day]),
                    comparison(pdist[day], rdist[day]),
                ))
                fields = (
                    WEEKDAY_MAP[day], pred_sport_names[day], pred_session_names[day],
                    "Yes" if pstr[day] else "No", display(pobj[day]), display(template),
                    display(pred_terrain_names[day]), display(pred_zone_names[day]), display(pdist[day]),
                    real_sport_names[day], rst[day], display(robj[day]), display(rter[day]),
                    display(rzone[day]), display(rdist[day]), result,
                )
                output.write("\t".join(str(value) for value in fields) + "\n")

            output.write("\n")
            output.write(f"Sport accuracy   : {score_lists['sport'][-1]}/7\n")
            output.write(f"Session accuracy : {score_lists['session'][-1]}/7\n\n")
            output.write(f"Terrain accuracy : {terrain_correct}/{len(valid_terrain)}\n\n")
            output.write(f"Zone accuracy : {zone_correct}/{len(valid_zone)}\n\n")
            output.write(f"Distance accuracy : {distance_correct}/{len(valid_distance)}\n\n")

        output.write("=" * 70 + "\nSUMMARY\n" + "=" * 70 + "\n\n")
        output.write(f"Weeks evaluated : {len(score_lists['sport'])}\n\n")
        output.write(f"Mean sport accuracy : {np.mean(score_lists['sport']):.2f}/7\n")
        output.write(f"Mean session accuracy : {np.mean(score_lists['session']):.2f}/7\n")
        output.write(f"Mean terrain accuracy : {np.mean(score_lists['terrain']):.2f}\n")
        output.write(f"Mean zone accuracy : {np.mean(score_lists['zone']):.2f}\n")
        output.write(f"Mean distance accuracy : {np.mean(score_lists['distance']):.2f}\n")

    print(f"Weeks evaluated: {predict_weeks}")
    print(f"Predictions written to {OUTPUT_FILE}")


if __name__ == "__main__":
    evaluate()
