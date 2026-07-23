# ==========================================================
# TRAIN OBJECTIVE CLASS MODEL
# ==========================================================

import json
import os
from pathlib import Path
import sys

ML_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ML_DIR))

import joblib
import pandas as pd

from sklearn.preprocessing import LabelEncoder

from xgboost import XGBClassifier

from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    classification_report,
    f1_score,
)
from train.reporting import print_multiclass_summary


# ==========================================================
# CONFIGURATION
# ==========================================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

DATA_FOLDER = (
    PROJECT_ROOT
    / "data"
    / "processed_data"
)

MODEL_DIR = Path(os.getenv("MODEL_OUTPUT_DIR", PROJECT_ROOT / "ML" / "models"))
MODEL_DIR.mkdir(parents=True, exist_ok=True)

MODEL_FILE = (
    MODEL_DIR
    / "objective_class_model.pkl"
)

ENCODER_FILE = (
    MODEL_DIR
    / "objective_class_encoder.pkl"
)

COLUMNS_FILE = (
    MODEL_DIR
    / "objective_class_columns.pkl"
)

RANDOM_STATE = 42
TEST_SIZE = 0.20


# ==========================================================
# LOAD DATA
# ==========================================================

print("=" * 60)
print("LOADING DATA")
print("=" * 60)

weeks = []

for file in sorted(DATA_FOLDER.glob("*.json")):

    with open(file, encoding="utf8") as f:
        weeks.append(json.load(f))

print(f"Weeks loaded : {len(weeks)}")


# ==========================================================
# BUILD DATASET
# ==========================================================

rows = []

for week_id, week in enumerate(weeks):

    for session in week["sessions"]:

        objective = session.get("objective_class")

        # Ignorar sesiones sin objetivo
        if objective is None:
            continue

        # Ignorar ejemplos no clasificados
        if objective == "Revisión manual":
            continue

        rows.append({

            "week_id": week_id,
            "date": session["date"],
            "weekday": session["weekday"],

            "week_type": week["week_type"],

            "sport": session["sport"],
            "session_type": session["session_type"],
            "terrain": session["terrain"],

            "objective_class": objective

        })

df = pd.DataFrame(rows)

print(f"Structured sessions : {len(df)}")
print()

print(df.head())


# ==========================================================
# CLASS DISTRIBUTION
# ==========================================================

print()
print("=" * 60)
print("OBJECTIVE CLASS DISTRIBUTION")
print("=" * 60)

counts = (
    df["objective_class"]
    .value_counts()
    .sort_values(ascending=False)
)

print(counts)

print()
print(f"Number of classes : {len(counts)}")


# ==========================================================
# ENCODE TARGET
# ==========================================================

label_encoder = LabelEncoder()

df["target"] = label_encoder.fit_transform(
    df["objective_class"]
)

joblib.dump(label_encoder, ENCODER_FILE)

print()
print("=" * 60)
print("OBJECTIVE CLASSES")
print("=" * 60)

for idx, name in enumerate(label_encoder.classes_):
    print(f"{idx:2d} -> {name}")

# ==========================================================
# FEATURE ENGINEERING
# ==========================================================

feature_columns = [

    "weekday",
    "sport",
    "session_type",
    "terrain",

]

X = pd.get_dummies(
    df[feature_columns],
    prefix=feature_columns,
    columns=feature_columns,
    dummy_na=True
)

joblib.dump(
    list(X.columns),
    COLUMNS_FILE
)

duplicates = X.columns[X.columns.duplicated()]

print()
print("Duplicated columns:")
print(list(duplicates))

y = df["target"]

print()
print("=" * 60)
print("FEATURE MATRIX")
print("=" * 60)

print(f"Samples  : {len(X)}")
print(f"Features : {X.shape[1]}")


# ==========================================================
# TRAIN / TEST SPLIT (BY WEEK)
# ==========================================================

if os.getenv("TRAIN_MODE", "evaluation") == "production":
    cutoff_week_id = len(weeks)
    cutoff_week = "100% history"
    train_mask = pd.Series(True, index=df.index)
    test_mask = train_mask
else:
    cutoff_week_id = int(len(weeks) * (1 - TEST_SIZE))
    cutoff_week = weeks[cutoff_week_id]["week_start"]
    train_mask = df["week_id"] < cutoff_week_id
    test_mask = ~train_mask

X_train = X.loc[train_mask]
X_test = X.loc[test_mask]

y_train = y.loc[train_mask]
y_test = y.loc[test_mask]

print()
print("=" * 60)
print("TRAIN / TEST")
print("=" * 60)

print(f"Training weeks : {cutoff_week_id}")
print(f"Test weeks     : {len(weeks) - cutoff_week_id}")
print(f"Cutoff week    : {cutoff_week}")
print()

print(f"Training sessions : {len(X_train)}")
print(f"Test sessions     : {len(X_test)}")


# ==========================================================
# TRAIN MODEL
# ==========================================================

print()
print("=" * 60)
print("TRAINING XGBOOST")
print("=" * 60)

model = XGBClassifier(

    n_estimators=300,

    max_depth=5,

    learning_rate=0.05,

    subsample=0.9,

    colsample_bytree=0.9,

    objective="multi:softprob",

    eval_metric="mlogloss",

    random_state=RANDOM_STATE,

    n_jobs=-1

)

model.fit(

    X_train,

    y_train

)

joblib.dump(model, MODEL_FILE)


# ==========================================================
# PREDICTION
# ==========================================================

pred = model.predict(X_test)
print_multiclass_summary("Objective family", y_test, pred)


# ==========================================================
# METRICS
# ==========================================================

accuracy = accuracy_score(

    y_test,

    pred

)

balanced = balanced_accuracy_score(

    y_test,

    pred

)

macro_f1 = f1_score(

    y_test,

    pred,

    average="macro"

)

print()
print("=" * 60)
print("RESULTS")
print("=" * 60)

print(f"Accuracy          : {accuracy:.3f}")
print(f"Balanced accuracy : {balanced:.3f}")
print(f"Macro F1          : {macro_f1:.3f}")

# ==========================================================
# CLASSIFICATION REPORT
# ==========================================================

print()
print("=" * 60)
print("CLASSIFICATION REPORT")
print("=" * 60)

print(

    classification_report(

        y_test,

        pred,

        target_names=label_encoder.classes_,

        digits=3,

        zero_division=0

    )

)

