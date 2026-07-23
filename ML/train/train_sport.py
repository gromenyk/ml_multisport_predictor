from pathlib import Path
import os
import sys
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dataset import TrainingDataset

from features import encode_session, SPORT_MAP
from train.reporting import print_multiclass_summary
from train.temporal_split import temporal_train_test_split

from xgboost import XGBClassifier
from sklearn.metrics import accuracy_score, classification_report

import numpy as np
import joblib

# ==========================================================
# Configuración
# ==========================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = PROJECT_ROOT / "data" / "processed_data"

MODEL_DIR = Path(os.getenv("MODEL_OUTPUT_DIR", PROJECT_ROOT / "ML" / "models"))
MODEL_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_FILE = PROJECT_ROOT / "ML" / "results" / "sport_predictions.txt"

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

SPORT_NAMES = {
    v: k
    for k, v in SPORT_MAP.items()
}

# ==========================================================
# Funciones auxiliares
# ==========================================================

def get_session_of_day(week, day_idx):

    weekday = WEEKDAY_MAP[day_idx]

    sessions = [
        s
        for s in week["sessions"]
        if s["weekday"] == weekday
    ]

    if len(sessions) == 0:
        return None

    return sessions[0]


def get_previous_sport(target_week, target_day):

    if target_day == 0:
        return SPORT_MAP["Descanso"]

    previous = get_session_of_day(
        target_week,
        target_day - 1
    )

    if previous is None:
        return SPORT_MAP["Descanso"]

    return SPORT_MAP[
        previous["sport"]
    ]


def encode_history(history):

    features = []

    for week in history:

        for day in range(7):

            session = get_session_of_day(
                week,
                day
            )

            features.extend(
                encode_session(session)
            )

    return features


def predict_week(model, history, target_week):

    predicted = []

    for day in range(7):

        features = encode_history(history)

        features.append(day)

        if day == 0:
            previous = SPORT_MAP["Descanso"]
        else:
            previous = predicted[-1]

        features.append(previous)

        X = np.array(features).reshape(1, -1)

        sport = int(
            model.predict(X)[0]
        )

        predicted.append(sport)

    real = []

    for day in range(7):

        session = get_session_of_day(
            target_week,
            day
        )

        if session is None:
            real.append(
                SPORT_MAP["Descanso"]
            )
        else:
            real.append(
                SPORT_MAP[
                    session["sport"]
                ]
            )

    return predicted, real


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

    features = encode_history(history)

    features.append(target_day)

    features.append(
        get_previous_sport(
            target_week,
            target_day
        )
    )

    X.append(features)
    target_week_starts.append(target_week["week_start"])

    if target_session is None:
        sport = SPORT_MAP["Descanso"]
    else:
        sport = SPORT_MAP[
            target_session["sport"]
        ]

    y.append(sport)

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

print(f"Train: {len(X_train)}")
print(f"Test : {len(X_test)}")
print(f"Cutoff week: {cutoff_week}")

# ==========================================================
# Modelo
# ==========================================================

model = XGBClassifier(
    objective="multi:softmax",
    num_class=len(SPORT_MAP),
    n_estimators=300,
    max_depth=6,
    learning_rate=0.05,
    random_state=42,
)

model.fit(
    X_train,
    y_train
)

joblib.dump(
    model,
    MODEL_DIR / "sport_model.pkl"
)

# ==========================================================
# Evaluación
# ==========================================================

pred = model.predict(X_test)
print_multiclass_summary("Sport", y_test, pred)

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
        zero_division=0
    )
)

# ==========================================================
# Predicción autoregresiva
# ==========================================================

print()
print("=" * 60)
print("AUTOREGRESSIVE EVALUATION")
print("=" * 60)

week_scores = []

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:

    f.write("=" * 70 + "\n")
    f.write("SPORT MODEL AUTOREGRESSIVE EVALUATION\n")
    f.write("=" * 70 + "\n\n")

    f.write(f"Weeks evaluated : {PREDICT_WEEKS}\n\n")

    first_week = len(dataset.weeks) - PREDICT_WEEKS

    for week_idx in range(first_week, len(dataset.weeks)):

        # Necesitamos suficientes semanas de historia
        if week_idx < CONTEXT_WEEKS:
            continue

        history = dataset.weeks[
            week_idx - CONTEXT_WEEKS:week_idx
        ]

        target_week = dataset.weeks[
            week_idx
        ]

        predicted, real = predict_week(
            model,
            history,
            target_week
        )

        correct = sum(
            p == r
            for p, r in zip(predicted, real)
        )

        week_scores.append(correct)

        f.write("=" * 70 + "\n")
        f.write(
            f"Week {target_week['week_start']}   "
            f"({correct}/7)\n"
        )
        f.write("=" * 70 + "\n\n")

        f.write("Real\n")

        for sport in real:
            f.write(
                f"  {SPORT_NAMES[sport]}\n"
            )

        f.write("\n")

        f.write("Predicted\n")

        for sport in predicted:
            f.write(
                f"  {SPORT_NAMES[sport]}\n"
            )

        f.write("\n")

        f.write("Comparison\n")

        for day in range(7):

            symbol = (
                "✓"
                if predicted[day] == real[day]
                else "✗"
            )

            f.write(
                f"{WEEKDAY_MAP[day]:>3} : "
                f"{SPORT_NAMES[predicted[day]]:<10}"
                f" | "
                f"{SPORT_NAMES[real[day]]:<10}"
                f" {symbol}\n"
            )

        f.write("\n\n")

    # ------------------------------------------------------
    # Resumen
    # ------------------------------------------------------

    mean_score = np.mean(week_scores)

    f.write("=" * 70 + "\n")
    f.write("SUMMARY\n")
    f.write("=" * 70 + "\n\n")

    f.write(
        f"Weeks evaluated : {len(week_scores)}\n"
    )

    f.write(
        f"Mean accuracy : "
        f"{mean_score:.2f}/7 "
        f"({100*mean_score/7:.1f}%)\n\n"
    )

    for score in range(7, -1, -1):

        count = week_scores.count(score)

        f.write(
            f"{score}/7 : {count}\n"
        )

print()
print(
    f"Predictions written to "
    f"{OUTPUT_FILE}"
)
