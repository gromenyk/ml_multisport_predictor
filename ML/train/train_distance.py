from pathlib import Path
import os
import sys

ML_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML_DIR))

import joblib
import numpy as np
from collections import Counter

from sklearn.utils.class_weight import compute_sample_weight
from sklearn.metrics import (
    accuracy_score,
    classification_report,
)

from xgboost import XGBClassifier

from dataset import TrainingDataset

from features import (
    SPORT_MAP,
    encode_session,
)
from train.reporting import print_multiclass_summary
from train.temporal_split import temporal_train_test_split

# ==========================================================
# CONFIGURATION
# ==========================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = (
    PROJECT_ROOT
    / "data"
    / "processed_data"
)

MODEL_DIR = Path(os.getenv("MODEL_OUTPUT_DIR", PROJECT_ROOT / "ML" / "models"))
MODEL_DIR.mkdir(parents=True, exist_ok=True)

CONTEXT_WEEKS = 4

MIN_DISTANCE_SAMPLES = 5

SPORTS = [
    "Trail",
    "Corrida",
    "Bici",
]

WEEKDAY_MAP = {
    0: "seg",
    1: "ter",
    2: "qua",
    3: "qui",
    4: "sex",
    5: "sáb",
    6: "dom",
}

TERRAIN_MAP = {
    "Ondulado": 0,
    "Plano": 1,
    "Montaña": 2,
}

ZONE_MAP = {
    "Z1": 0,
    "Z2": 1,
    "Z3": 2,
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

# ==========================================================
# HELPERS
# ==========================================================

def get_session_of_day(
    week,
    day_idx,
):

    weekday = WEEKDAY_MAP[day_idx]

    sessions = [

        s

        for s in week["sessions"]

        if s["weekday"] == weekday

    ]

    if len(sessions) == 0:

        return None

    return sessions[0]


def encode_history(history):

    features = []

    for week in history:

        features.append(

            WEEK_TYPE_MAP.get(

                week["week_type"],

                WEEK_TYPE_MAP["Otros"],

            )

        )

        for day in range(7):

            session = get_session_of_day(
                week,
                day,
            )

            features.extend(
                encode_session(session)
            )

    return features


def is_structured(session):

    if session is None:
        return False

    blocks = session.get(
        "blocks",
        [],
    )

    return not (

        len(blocks) == 1

        and

        blocks[0].get("type") == "steady"

    )


def get_distance(session):

    if session is None:
        return None

    blocks = session.get(
        "blocks",
        [],
    )

    if len(blocks) == 0:
        return None

    return blocks[0].get(
        "distance_km"
    )


def get_zone(session):

    if session is None:
        return None

    blocks = session.get(
        "blocks",
        [],
    )

    if len(blocks) == 0:
        return None

    return blocks[0].get(
        "zone"
    )


# ==========================================================
# LOAD DATASET
# ==========================================================

dataset = TrainingDataset(
    data_dir=DATA_DIR,
    context_weeks=CONTEXT_WEEKS,
)

examples = dataset.build_examples()

print()

print(
    f"Examples: {len(examples)}"
)

# ==========================================================
# TRAIN ONE SPORT
# ==========================================================

def train_distance_model(sport_name):

    print()
    print("=" * 60)
    print(f"TRAINING {sport_name.upper()}")
    print("=" * 60)

    #
    # ----------------------------------------------
    # Collect all valid examples
    # ----------------------------------------------
    #

    filtered_examples = []

    for example in examples:

        history = example["history"]

        target_day = example["target_day"]

        target_week = example["target_week"]

        session = get_session_of_day(
            target_week,
            target_day,
        )

        if session is None:
            continue

        #
        # Sport
        #

        if session["sport"] != sport_name:
            continue

        #
        # Structured
        #

        if is_structured(session):
            continue

        #
        # Distance
        #

        distance = get_distance(session)

        if distance is None:
            continue

        #
        # Terrain
        #

        terrain = session.get("terrain")

        if terrain not in TERRAIN_MAP:
            continue

        #
        # Zone
        #

        zone = get_zone(session)

        if zone not in ZONE_MAP:
            continue

        #
        # Store example
        #

        filtered_examples.append({

            "history": history,

            "target_day": target_day,

            "target_week": target_week,

            "terrain": terrain,

            "zone": zone,

            "distance": distance,

        })

    print()

    print(
        f"Valid examples: "
        f"{len(filtered_examples)}"
    )

        #
    # ------------------------------------------------------
    # Build DISTANCE_MAP
    # ------------------------------------------------------
    #

    counts = Counter(

        example["distance"]

        for example in filtered_examples

    )

    valid_distances = sorted(

        distance

        for distance, count in counts.items()

        if count >= MIN_DISTANCE_SAMPLES

    )

    DISTANCE_MAP = {

        distance: idx

        for idx, distance in enumerate(valid_distances)

    }

    INV_DISTANCE_MAP = {

        idx: distance

        for distance, idx in DISTANCE_MAP.items()

    }

    print()

    print("Distances")

    print("-" * 40)

    for distance in valid_distances:

        print(

            f"{distance:>6} km : "

            f"{counts[distance]}"

        )

    #
    # ------------------------------------------------------
    # Build X / y
    # ------------------------------------------------------
    #

    X = []

    y = []

    target_week_starts = []

    for example in filtered_examples:

        distance = example["distance"]

        if distance not in DISTANCE_MAP:
            continue

        history = example["history"]

        target_day = example["target_day"]

        target_week = example["target_week"]

        terrain = example["terrain"]

        zone = example["zone"]

        features = encode_history(
            history
        )

        #
        # Current day
        #

        features.append(
            target_day
        )

        #
        # Terrain
        #

        features.append(
            TERRAIN_MAP[terrain]
        )

        #
        # Zone
        #

        features.append(
            ZONE_MAP[zone]
        )

        X.append(features)

        target_week_starts.append(target_week["week_start"])

        y.append(
            DISTANCE_MAP[distance]
        )

    X = np.array(X)

    y = np.array(y)

    print()

    print(
        "Feature shape:",
        X.shape,
    )

    print(
        "Classes:",
        len(DISTANCE_MAP),
    )

    #
    # ------------------------------------------------------
    # Train / Test split
    # ------------------------------------------------------
    #

    

    X_train, X_test, y_train, y_test, cutoff_week = temporal_train_test_split(
        X, y, target_week_starts, dataset.weeks
    )

    print(f"Cutoff week: {cutoff_week}")

    # XGBoost requires the labels used for fitting to be consecutive and
    # zero-based. A chronological split can leave a distance category only
    # in the test period, so remap train labels locally and translate model
    # predictions back to the original category ids for evaluation.
    train_class_ids = sorted(int(value) for value in np.unique(y_train))
    original_to_model_class = {
        original: model_class
        for model_class, original in enumerate(train_class_ids)
    }
    model_to_original_class = {
        model_class: original
        for original, model_class in original_to_model_class.items()
    }
    y_train_model = np.array([
        original_to_model_class[int(value)]
        for value in y_train
    ])

    sample_weight = compute_sample_weight(

        class_weight="balanced",

        y=y_train_model,

    )

    #
    # ------------------------------------------------------
    # Train model
    # ------------------------------------------------------
    #

    model = XGBClassifier(

        objective="multi:softmax",

        num_class=len(train_class_ids),

        n_estimators=300,

        max_depth=6,

        learning_rate=0.05,

        random_state=42,

    )

    print()
    print("Train classes:", np.unique(y_train))
    print("Test classes :", np.unique(y_test))

    model.fit(

        X_train,

        y_train_model,

        sample_weight=sample_weight,

    )

    #
    # ------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------
    #

    pred_model = model.predict(X_test)
    pred = np.array([
        model_to_original_class[int(value)]
        for value in pred_model
    ])
    print_multiclass_summary(f"Distance - {sport_name}", y_test, pred)

    print()

    print("=" * 60)
    print("RESULTS")
    print("=" * 60)

    print(

        f"Accuracy: "

        f"{accuracy_score(y_test, pred):.4f}"

    )

    print()

    print(

        classification_report(

            y_test,

            pred,

            labels=list(range(len(DISTANCE_MAP))),

            target_names=[

                str(INV_DISTANCE_MAP[i])

                for i in range(len(DISTANCE_MAP))

            ],

            zero_division=0,

        )

    )

    #
    # ------------------------------------------------------
    # Prediction distribution
    # ------------------------------------------------------
    #

    print()

    print("=" * 60)
    print("PREDICTED DISTRIBUTION")
    print("=" * 60)

    pred_counter = Counter(pred)

    for cls in sorted(pred_counter):

        print(

            f"{INV_DISTANCE_MAP[cls]:>6} km : "

            f"{pred_counter[cls]}"

        )

    #
    # ------------------------------------------------------
    # Save bundle
    # ------------------------------------------------------
    #

    model_distance_map = {
        distance: original_to_model_class[class_id]
        for distance, class_id in DISTANCE_MAP.items()
        if class_id in original_to_model_class
    }

    model_inverse_distance_map = {
        model_class: INV_DISTANCE_MAP[original_class]
        for model_class, original_class in model_to_original_class.items()
    }

    bundle = {

        "model": model,

        "distance_map": model_distance_map,

        "inverse_distance_map": model_inverse_distance_map,

        "sport": sport_name,

    }

    filename = (
        MODEL_DIR
        / f"distance_{sport_name.lower()}.pkl"
    )

    joblib.dump(

        bundle,

        filename,

    )

    print()

    print(

        f"Saved "

        f"{filename}"

    )

    return bundle

# ==========================================================
# TRAIN ALL SPORTS
# ==========================================================

distance_models = {}

for sport in SPORTS:

    bundle = train_distance_model(
        sport
    )

    distance_models[
        sport
    ] = bundle

# ==========================================================
# SUMMARY
# ==========================================================

print()

print("=" * 60)
print("SUMMARY")
print("=" * 60)

for sport in SPORTS:

    bundle = distance_models[sport]

    print()

    print(f"{sport}")

    print("-" * 30)

    print(
        f"Classes : "
        f"{len(bundle['distance_map'])}"
    )

    print()

    print("Distances")

    for distance in bundle["distance_map"]:

        print(
            f"  {distance} km"
        )

print()

print("=" * 60)
print("GENERATED FILES")
print("=" * 60)

for sport in SPORTS:

    print(
        f"distance_{sport.lower()}.pkl"
    )

print()

print("Done.")
