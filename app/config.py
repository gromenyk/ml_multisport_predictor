"""
config.py

Central configuration file for the Training Planner AI project.
Defines project paths and initializes the required folder structure.
"""

from pathlib import Path

# ==========================================================
# PROJECT ROOT
# ==========================================================

PROJECT_ROOT = Path(__file__).resolve().parents[1]

PROJECT_DIR = Path(__file__).resolve().parent.parent

# ==========================================================
# DATA
# ==========================================================

DATA_DIR = PROJECT_ROOT / "data"

RAW_DATA_DIR = DATA_DIR / "raw_data"

PROCESSED_DATA_DIR = DATA_DIR / "processed_data"

# Temporary files generated during processing
TEMP_DIR = DATA_DIR / "temp"

# ==========================================================
# MACHINE LEARNING
# ==========================================================

ML_DIR = PROJECT_ROOT / "ML"

MODELS_DIR = ML_DIR / "models"

RESULTS_DIR = ML_DIR / "results"

TRAIN_DIR = ML_DIR / "train"

# ==========================================================
# PROMPTS
# ==========================================================

PROMPTS_DIR = PROJECT_ROOT / "prompts"

# ==========================================================
# INITIALIZATION
# ==========================================================

def initialize_project():
    """
    Creates the required project folders if they do not already exist.
    Safe to call every time the application starts.
    """

    folders = [
        DATA_DIR,
        RAW_DATA_DIR,
        PROCESSED_DATA_DIR,
        TEMP_DIR,
        ML_DIR,
        MODELS_DIR,
        RESULTS_DIR,
        TRAIN_DIR,
        PROMPTS_DIR,
    ]

    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)