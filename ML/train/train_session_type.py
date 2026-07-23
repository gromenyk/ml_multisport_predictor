from pathlib import Path
import os
import sys

ML_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML_DIR))

from dataset import TrainingDataset
import joblib
import numpy as np
from sklearn.utils.class_weight import compute_sample_weight

from collections import Counter

from features import (
    encode_session,
    SPORT_MAP,
)
from train.reporting import print_multiclass_summary
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

OUTPUT_FILE = PROJECT_ROOT / "ML" / "results" / "session_type_predictions.txt"

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

INV_SESSION_TYPE_MAP = {
    v: k
    for k, v in SESSION_TYPE_MAP.items()
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


def encode_history(history):

    features = []

    for week in history:

        # -----------------------------
        # Contexto de la semana
        # -----------------------------

        features.append(
            WEEK_TYPE_MAP[
                week["week_type"]
            ]
        )

        # -----------------------------
        # Sesiones
        # -----------------------------

        for day in range(7):

            session = get_session_of_day(
                week,
                day
            )

            features.extend(
                encode_session(session)
            )

    return features


def predict_week(
    model,
    history,
    target_week,
):

    predicted = []

    for day in range(7):

        features = encode_history(
            history
        )

        features.append(day)

        target_session = get_session_of_day(
            target_week,
            day
        )

        # Por ahora usamos el deporte real.
        # En el pipeline final será el deporte
        # predicho por sport_model.pkl

        if target_session is None:

            sport = SPORT_MAP[
                "Descanso"
            ]

        else:

            sport = SPORT_MAP[
                target_session["sport"]
            ]

        features.append(sport)

        pred = model.predict(

            np.array([features])

        )[0]

        predicted.append(pred)

    real = []

    for day in range(7):

        session = get_session_of_day(
            target_week,
            day
        )

        if session is None:

            real.append(None)

        else:

            real.append(
                session.get(
                    "session_type"
                )
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

    features = encode_history(
        history
    )

    features.append(
        target_day
    )

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

    X.append(features)
    target_week_starts.append(target_week["week_start"])

    if session_type not in SESSION_TYPE_MAP:

        raise ValueError(
            f"Unknown session type: "
            f"{session_type}"
        )

    y.append(
        SESSION_TYPE_MAP[
            session_type
        ]
    )

for r, row in enumerate(X):

    for c, value in enumerate(row):

        if isinstance(value, str):

            print(
                f"Row {r}, Col {c}:",
                repr(value),
                type(value)
            )

            raise SystemExit

print("No strings found.")

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

sample_weight = compute_sample_weight(
    class_weight="balanced",
    y=y_train
)

# ==========================================================
# Modelo
# ==========================================================

model = XGBClassifier(
    objective="multi:softmax",
    num_class=len(SESSION_TYPE_MAP),
    n_estimators=300,
    max_depth=6,
    learning_rate=0.05,
    random_state=42,
)

model.fit(
    X_train,
    y_train,
    sample_weight=sample_weight
)

joblib.dump(
    model,
    MODEL_DIR / "session_type_model.pkl"
)

# ==========================================================
# Evaluación
# ==========================================================

pred = model.predict(X_test)
print_multiclass_summary("Session type", y_test, pred)

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

# ==========================================================
# Distribución de clases
# ==========================================================

print()
print("=" * 60)
print("TRAIN DISTRIBUTION")
print("=" * 60)

for cls, count in sorted(Counter(y_train).items()):

    print(f"{cls:2d}: {count}")

print()

print("=" * 60)
print("TEST DISTRIBUTION")
print("=" * 60)

for cls, count in sorted(Counter(y_test).items()):

    print(f"{cls:2d}: {count}")

print()

print("=" * 60)
print("PREDICTED DISTRIBUTION")
print("=" * 60)

for cls, count in sorted(Counter(pred).items()):

    print(f"{cls:2d}: {count}")

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
    f.write("SESSION TYPE MODEL AUTOREGRESSIVE EVALUATION\n")
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
            INV_SESSION_TYPE_MAP[p] == r
            for p, r in zip(predicted, real)
        )

        week_scores.append(correct)

        f.write("=" * 70 + "\n")
        f.write(
            f"Week {target_week['week_start']}   "
            f"({correct}/7)\n"
        )
        f.write("=" * 70 + "\n\n")

        # --------------------------------------------------
        # Real
        # --------------------------------------------------

        f.write("Real\n")

        for session_type in real:

            f.write(
                f"  {session_type}\n"
            )

        f.write("\n")

        # --------------------------------------------------
        # Predicted
        # --------------------------------------------------

        f.write("Predicted\n")

        for session_type in predicted:

            f.write(
                f"  {INV_SESSION_TYPE_MAP[session_type]}\n"
            )

        f.write("\n")

        # --------------------------------------------------
        # Comparison
        # --------------------------------------------------

        f.write("Comparison\n")

        for day in range(7):

            pred_name = INV_SESSION_TYPE_MAP[
                predicted[day]
            ]

            real_name = real[day]

            symbol = (
                "✓"
                if pred_name == real_name
                else "✗"
            )

            f.write(
                f"{WEEKDAY_MAP[day]:>3} : "
                f"{str(pred_name):<18}"
                f" | "
                f"{str(real_name):<18}"
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
