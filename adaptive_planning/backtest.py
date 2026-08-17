"""Strict walk-forward backtest with weekly model retraining."""

from __future__ import annotations

from datetime import date, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys

from adaptive_planning.engine import _estimated_week_load, build_adaptive_proposal
from adaptive_planning.workflow_store import save_backtest
from athlete_state.state_builder import build_athlete_state
from config import PROJECT_ROOT


ML_DIR = PROJECT_ROOT / "ML"
PROCESSED_DIR = PROJECT_ROOT / "data" / "processed_data"
TRAINING_SCRIPTS = (
    "train_sport.py", "train_session_type.py", "train_structured.py",
    "train_objective_family.py", "train_terrain.py", "train_zones.py", "train_distance.py",
)


def _model_python():
    explicit = os.getenv("ADAPTIVE_MODEL_PYTHON")
    if explicit and Path(explicit).exists():
        return Path(explicit)
    candidate = Path(sys.executable).resolve().parent / "envs" / "multisport" / "python.exe"
    return candidate if candidate.exists() else Path(sys.executable)


def _json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except (OSError, json.JSONDecodeError):
        return default


def _catalogs():
    goals = _json(PROJECT_ROOT / "data" / "objectives" / "development_goal_catalog.json", {"development_goals": []})
    metrics = _json(PROJECT_ROOT / "data" / "objectives" / "metric_catalog.json", {"metrics": []})
    return ({item["id"]: item.get("name_es") or item.get("name") for item in goals["development_goals"]}, metrics["metrics"])


def _human_week(week_start):
    for path in PROCESSED_DIR.glob("*.json"):
        value = _json(path, {})
        if value.get("week_start") == week_start.isoformat():
            return value
    return None


def _performed_load(week_start):
    payload = _json(PROJECT_ROOT / "data" / "derived" / "training_load" / "performed_load.json", {})
    end = week_start + timedelta(days=6)
    return sum(float(item.get("calculated_load") or 0) for day, item in payload.get("daily_loads", {}).items()
               if week_start.isoformat() <= day <= end.isoformat())


def _rounded(value):
    return round(value, 2) if isinstance(value, (int, float)) else None


def _train_models(cutoff, model_dir):
    model_dir.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, "TRAIN_MODE": "production", "MODEL_OUTPUT_DIR": str(model_dir),
                   "TRAINING_CUTOFF_DATE": cutoff.isoformat(), "PYTHONIOENCODING": "utf-8"}
    logs = []
    for script in TRAINING_SCRIPTS:
        result = subprocess.run([str(_model_python()), "-X", "utf8", str(ML_DIR / "train" / script)],
                                cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8",
                                errors="replace", env=environment)
        logs.append({"script": script, "returncode": result.returncode,
                     "tail": (result.stdout + "\n" + result.stderr)[-1500:]})
        if result.returncode:
            raise RuntimeError(f"Falló {script} para corte {cutoff}: {logs[-1]['tail']}")
    return logs


def _predict(cutoff, model_dir):
    output = model_dir / "prediction.json"
    result = subprocess.run([
        str(_model_python()), "-X", "utf8", str(ML_DIR / "predict_next_week.py"),
        "--cutoff", cutoff.isoformat(), "--model-dir", str(model_dir),
        "--output", str(output), "--no-save",
    ], cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError(f"Falló la predicción para corte {cutoff}: {(result.stdout + result.stderr)[-2000:]}")
    return _json(output, {})


def run_walk_forward(start_week, end_week, progress_callback=None):
    """Retrain and predict each Monday without exposing later human weeks."""
    start = date.fromisoformat(start_week) if isinstance(start_week, str) else start_week
    end = date.fromisoformat(end_week) if isinstance(end_week, str) else end_week
    goals, metrics = _catalogs()
    mondays, cursor = [], start
    while cursor <= end:
        mondays.append(cursor)
        cursor += timedelta(days=7)
    results = []
    for index, week_start in enumerate(mondays):
        cutoff = week_start - timedelta(days=1)
        if progress_callback:
            progress_callback(index, len(mondays), f"Reentrenando con información hasta {cutoff}")
        model_dir = ML_DIR / "results" / "adaptive_backtests" / "models" / cutoff.isoformat()
        logs = _train_models(cutoff, model_dir)
        prediction = _predict(cutoff, model_dir)
        if prediction["week_start"] != week_start.isoformat():
            raise RuntimeError(f"El modelo produjo {prediction['week_start']} en vez de {week_start}.")
        state = build_athlete_state(cutoff)
        proposal = build_adaptive_proposal(prediction, state, goals, metrics)
        human = _human_week(week_start)
        base_load = _estimated_week_load(prediction["sessions"])
        adapted_load = _estimated_week_load(proposal["adapted_sessions"])
        human_load = _estimated_week_load(human.get("sessions", [])) if human else None
        results.append({
            "week_start": week_start.isoformat(), "cutoff_date": cutoff.isoformat(),
            "model_dir": str(model_dir.relative_to(PROJECT_ROOT)), "training_logs": logs,
            "prediction": prediction, "athlete_state_snapshot_id": state.get("snapshot_id"),
            "athlete_state": state, "proposal": proposal,
            "comparison": {"base_load": _rounded(base_load), "adapted_load": _rounded(adapted_load),
                           "human_load": _rounded(human_load),
                           "performed_load": _rounded(_performed_load(week_start))},
        })
    run = save_backtest({"engine_version": "adaptive_engine_v15", "start_week": start.isoformat(),
                         "end_week": end.isoformat(), "strict_temporal_cutoff": True, "weeks": results})
    if progress_callback:
        progress_callback(len(mondays), len(mondays), "Backtest completado")
    return run
