from pathlib import Path
import os
import sys

ML_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML_DIR))

from dataset import TrainingDataset
import joblib
import numpy as np

from collections import Counter

from features import (
    encode_session,
    SPORT_MAP,
)
from train.reporting import print_binary_summary
from train.temporal_split import temporal_train_test_split

from xgboost import XGBClassifier

from sklearn.metrics import (
    accuracy_score,
    classification_report,
)

# ==========================================================
# Configuración
# ==========================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = PROJECT_ROOT / "data" / "processed_data"

MODEL_DIR = Path(os.getenv("MODEL_OUTPUT_DIR", PROJECT_ROOT / "ML" / "models"))
MODEL_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FILE = (
    PROJECT_ROOT
    / "ML"
    / "results"
    / "structured_workout_predictions.txt"
)

CONTEXT_WEEKS = 4

PREDICT_WEEKS = 30

WEEKDAY_MAP = {
    0: "seg",
    1: "ter",
    2: "qua",
    3: "qui",
    4: "sex",
    5: "sáb",
    6: "dom",
}

WEEK_TYPE_MAP = {
    None: 0,
    "": 0,
    "Semana Controle": 1,
    "Semana Regenerativa": 2,
    "Desenvolvimento Aeróbico": 3,
    "Desenvolvimento Anaeróbico": 4,
    "Técnica e Fortalecimento": 5,
    "Pico de Performance": 6,
    "Otros": 7,
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

STRUCTURED_MAP = {
    False: 0,
    True: 1,
}

INV_STRUCTURED_MAP = {
    0: False,
    1: True,
}

SPORT_NAMES = {
    v: k
    for k, v in SPORT_MAP.items()
}

# ==========================================================
# Funciones auxiliares
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


def is_structured(
    session,
):

    if session is None:

        return False

    return (
        len(
            session.get(
                "blocks",
                []
            )
        ) > 1
    )


def encode_history(
    history,
):

    features = []

    for week in history:

        features.append(

            WEEK_TYPE_MAP.get(

                week["week_type"],

                WEEK_TYPE_MAP["Otros"]

            )

        )

        for day in range(7):

            session = get_session_of_day(
                week,
                day
            )

            features.extend(
                encode_session(session)
            )

    return features

# ==========================================================
# Dataset
# ==========================================================

dataset = TrainingDataset(
    data_dir=DATA_DIR,
    context_weeks=CONTEXT_WEEKS,
)

examples = dataset.build_examples()

print(
    f"Examples: {len(examples)}"
)

# ==========================================================
# Construcción de X e y
# ==========================================================

X = []
y = []
target_week_starts = []

for example in examples:

    history = example["history"]

    target_day = example["target_day"]

    target_week = example["target_week"]

    target_session = get_session_of_day(
        target_week,
        target_day
    )

    features = encode_history(
        history
    )

    # ------------------------------------------------------
    # Día
    # ------------------------------------------------------

    features.append(
        target_day
    )

    # ------------------------------------------------------
    # Sport
    # ------------------------------------------------------

    if target_session is None:

        features.append(
            SPORT_MAP["Descanso"]
        )

        session_type = None

    else:

        features.append(

            SPORT_MAP[
                target_session["sport"]
            ]

        )

        session_type = target_session.get(
            "session_type"
        )

    # ------------------------------------------------------
    # Session type
    # ------------------------------------------------------

    features.append(

        SESSION_TYPE_MAP[
            session_type
        ]

    )

    X.append(features)
    target_week_starts.append(target_week["week_start"])

    # ------------------------------------------------------
    # Target
    # ------------------------------------------------------

    y.append(

        STRUCTURED_MAP[

            is_structured(
                target_session
            )

        ]

    )

X = np.array(X)
y = np.array(y)

print(
    "Feature shape:",
    X.shape
)

# ==========================================================
# Train / Test
# ==========================================================

X_train, X_test, y_train, y_test, cutoff_week = temporal_train_test_split(
    X, y, target_week_starts, dataset.weeks
)

print(
    f"Train: {len(X_train)}"
)

print(
    f"Test : {len(X_test)}"
)
print(f"Cutoff week: {cutoff_week}")

# ==========================================================
# Balanceo
# ==========================================================

negative = np.sum(
    y_train == 0
)

positive = np.sum(
    y_train == 1
)

scale_pos_weight = (
    negative / positive
)

print()

print(
    f"scale_pos_weight = "
    f"{scale_pos_weight:.2f}"
)

# ==========================================================
# Modelo
# ==========================================================

model = XGBClassifier(

    objective="binary:logistic",

    n_estimators=300,

    max_depth=6,

    learning_rate=0.05,

    random_state=42,

    scale_pos_weight=scale_pos_weight,

)

model.fit(
    X_train,
    y_train,
)

joblib.dump(
    model,
    MODEL_DIR / "structured_workout_model.pkl"
)

# ==========================================================
# Evaluación
# ==========================================================

pred = model.predict(
    X_test
)
print_binary_summary("Structured session", y_test, pred)

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

        zero_division=0,

    )

)

