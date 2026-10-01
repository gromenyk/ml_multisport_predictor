from collections import defaultdict
from datetime import date, datetime, timedelta
import argparse
import hashlib
import json
from pathlib import Path
from statistics import median

try:  # Imported by the Streamlit app as the ML package.
    from .dataset import TrainingDataset, validate_consecutive_context
    from .historical_context_selector import (
        rank_historical_contexts, record_context_selection, select_historical_context,
    )
    from .style_score import calculate_style_score
    from .evaluate_pipeline import (
        CONTEXT_WEEKS, DATA_DIR, INV_SESSION_TYPE_MAP, INV_TERRAIN_MAP,
        INV_ZONE_MAP, SPORT_MAP, SPORT_NAMES, WEEKDAY_MAP,
        build_structured_library, load_models, predict_pipeline,
    )
except ImportError:  # Direct execution: python ML/predict_next_week.py
    from dataset import TrainingDataset, validate_consecutive_context
    from historical_context_selector import (
        rank_historical_contexts, record_context_selection, select_historical_context,
    )
    from style_score import calculate_style_score
    from evaluate_pipeline import (
        CONTEXT_WEEKS, DATA_DIR, INV_SESSION_TYPE_MAP, INV_TERRAIN_MAP,
        INV_ZONE_MAP, SPORT_MAP, SPORT_NAMES, WEEKDAY_MAP,
        build_structured_library, load_models, predict_pipeline,
    )


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_MODEL_DIR = PROJECT_ROOT / "ML" / "models" / "production"
PREDICTIONS_DIR = PROJECT_ROOT / "ML" / "results" / "predictions"
ADAPTIVE_PLANS_FILE = PROJECT_ROOT / "data" / "athlete_state" / "adaptive_published_plans.json"
MINIMUM_STYLE_SCORE = 70.0
# The performed-state profile retrieves relevant historical contexts, but it
# must not outweigh the learned human-plan style when choosing a proposal.
CONTEXT_SIMILARITY_TOLERANCE_PERCENT = 3.0


def _kayak_reference(weeks, weekday, zone):
    """Reuse a real kayak prescription; duration is the primary dose."""
    rows = []
    for week in weeks:
        for item in week.get("sessions", []):
            if item.get("sport") != "Kayak" or item.get("structured"):
                continue
            blocks = item.get("blocks") or []
            duration = sum(float(part.get("duration_min") or 0) for part in blocks if isinstance(part, dict))
            distance = sum(float(part.get("distance_km") or 0) for part in blocks if isinstance(part, dict))
            item_zone = next((part.get("zone") for part in blocks if isinstance(part, dict) and part.get("zone")), None)
            if duration > 0 and distance > 0:
                rows.append({"duration": duration, "distance": distance, "zone": item_zone,
                             "weekday": item.get("weekday")})
    if not rows:
        return {"duration": 60.0, "distance": 10.0, "source": "referencia inicial"}
    tiers = (
        [row for row in rows if row["zone"] == zone],
        rows,
    )
    pool = next((tier for tier in tiers if tier), rows)
    typical_duration = median(row["duration"] for row in pool)
    typical_distance = median(row["distance"] for row in pool)
    selected = min(pool, key=lambda row: (
        abs(row["duration"] - typical_duration) + abs(row["distance"] - typical_distance),
        row["weekday"] != weekday,
    ))
    return {**selected, "source": f"{len(pool)} sesión(es) históricas comparables"}


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
    if prediction.get("data_cutoff_date") is None:
        record_context_selection(
            prediction["context_selection"], prediction["week_start"]
        )


def _week_signature(sessions):
    """Return a compact, date-independent description of a predicted week."""
    if not sessions:
        return ()
    first_date = min(date.fromisoformat(str(session["date"])) for session in sessions)
    signature = []
    for session in sessions:
        session_date = date.fromisoformat(str(session["date"]))
        distance = session.get("distance_km")
        duration = session.get("estimated_duration_minutes")
        signature.append((
            (session_date - first_date).days,
            str(session.get("sport") or ""),
            str(session.get("zone") or ""),
            str(session.get("terrain") or ""),
            bool(session.get("structured")),
            str(session.get("template") or ""),
            None if distance is None else round(float(distance), 1),
            None if duration is None else round(float(duration), 1),
        ))
    return tuple(sorted(signature))


def _recent_prediction_signatures(target_week_start, count=4):
    """Load the latest prediction for each of the preceding operational weeks."""
    target = date.fromisoformat(str(target_week_start))
    latest_by_week = {}
    for path in PREDICTIONS_DIR.glob("prediction_*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            week_start = date.fromisoformat(str(payload.get("week_start")))
            sessions = payload.get("sessions") or []
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            continue
        if week_start >= target or not sessions:
            continue
        current = latest_by_week.get(week_start)
        modified_at = path.stat().st_mtime
        if current is None or modified_at > current[0]:
            latest_by_week[week_start] = (modified_at, _week_signature(sessions))

    recent_weeks = sorted(latest_by_week, reverse=True)[:count]
    return {latest_by_week[week][1] for week in recent_weeks}


def _next_operational_week_start(current_monday):
    """Use approved plans only to locate the next calendar week, never as ML input."""
    latest_end = None
    try:
        payload = json.loads(ADAPTIVE_PLANS_FILE.read_text(encoding="utf-8"))
        active = [
            item.get("week_end") for item in payload.get("plans", [])
            if item.get("status") == "active" and item.get("week_end")
        ]
        latest_end = max(active) if active else None
    except (OSError, json.JSONDecodeError):
        pass
    next_calendar_week = current_monday + timedelta(days=7)
    if latest_end:
        return max(next_calendar_week, date.fromisoformat(latest_end) + timedelta(days=1))
    return next_calendar_week


def build_prediction(
    *,
    cutoff_date=None,
    model_dir=PRODUCTION_MODEL_DIR,
    save=True,
    history_override=None,
    context_selection_override=None,
    target_week_start=None,
):
    # The ML context is intentionally human plans only. Approved adaptive
    # weeks remain operational plans for the athlete, not feedback examples
    # for the model that generated them.
    dataset = TrainingDataset(DATA_DIR, context_weeks=CONTEXT_WEEKS, cutoff_date=cutoff_date)

    if len(dataset.weeks) < CONTEXT_WEEKS:
        raise ValueError("At least four historical weeks are required.")

    last_week = dataset.weeks[-1]
    current_monday = date.today() - timedelta(days=date.today().weekday())
    if target_week_start is not None:
        next_start = (
            target_week_start
            if isinstance(target_week_start, date)
            else date.fromisoformat(str(target_week_start))
        )
    elif cutoff_date is not None:
        # Backtests keep their historical temporal target independent from
        # the present-day operational schedule.
        next_start = date.fromisoformat(last_week["week_end"]) + timedelta(days=1)
    else:
        next_start = _next_operational_week_start(current_monday)
    next_end = next_start + timedelta(days=6)
    if cutoff_date is None:
        if next_start > current_monday + timedelta(days=7):
            raise ValueError(
                "Solo se puede predecir la semana calendario inmediatamente siguiente. "
                f"La próxima habilitada comienza el {(current_monday + timedelta(days=7)).isoformat()}."
            )
    if history_override is not None:
        history = list(history_override)
        context_selection = dict(context_selection_override or {})
    else:
        history, context_selection = select_historical_context(
            dataset.weeks,
            date.today() if cutoff_date is None else cutoff_date,
            CONTEXT_WEEKS,
            target_week_start=next_start,
        )
    validate_consecutive_context(history, CONTEXT_WEEKS)

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
            if sport == "Kayak":
                reference = _kayak_reference(dataset.weeks, WEEKDAY_MAP[day], zone_name)
                block = {
                    "type": "steady", "zone": zone_name,
                    "duration_min": reference["duration"], "distance_km": reference["distance"],
                }
                session = {
                    "date": session_date.isoformat(), "weekday": WEEKDAY_MAP[day],
                    "sport": sport, "session_type": session_type, "structured": False,
                    "objective_class": None, "template": None, "terrain": None,
                    "zone": zone_name, "duration_min": reference["duration"],
                    "distance_km": reference["distance"],
                    "prescription_basis": f"Kayak por duración · {reference['source']}",
                    "blocks": [block],
                }
                sessions.append(session)
                continue
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
        "context_selection": context_selection,
        "production_models": production_model_fingerprints(model_dir),
        "sessions": sessions,
    }

    if save:
        save_prediction_files(prediction)

    return prediction


def build_prediction_candidates(*, cutoff_date=None, model_dir=PRODUCTION_MODEL_DIR, limit=5):
    """Create context-relevant alternatives, ordered primarily by plan style.

    The performed state retrieves a close historical neighbourhood; within
    that neighbourhood the Style Score selects the weekly structure.
    """
    dataset = TrainingDataset(DATA_DIR, context_weeks=CONTEXT_WEEKS, cutoff_date=cutoff_date)
    if len(dataset.weeks) < CONTEXT_WEEKS:
        raise ValueError("At least four historical weeks are required.")
    if cutoff_date is not None:
        next_start = date.fromisoformat(dataset.weeks[-1]["week_end"]) + timedelta(days=1)
    else:
        current_monday = date.today() - timedelta(days=date.today().weekday())
        next_start = _next_operational_week_start(current_monday)
        if next_start > current_monday + timedelta(days=7):
            raise ValueError(
                "Solo se puede predecir la semana calendario inmediatamente siguiente. "
                f"La próxima habilitada comienza el {(current_monday + timedelta(days=7)).isoformat()}."
            )

    candidates = []
    recent_signatures = _recent_prediction_signatures(next_start)
    discarded_duplicates = 0
    references = dataset.weeks[-30:]
    ranked_contexts = rank_historical_contexts(
        dataset.weeks,
        date.today() if cutoff_date is None else cutoff_date,
        CONTEXT_WEEKS,
        max(20, int(limit) * 6),
        target_week_start=next_start,
    )
    best_similarity = ranked_contexts[0][1]["similarity_percent"]
    similarity_floor = max(0.0, best_similarity - CONTEXT_SIMILARITY_TOLERANCE_PERCENT)
    close_contexts = [
        (history, selection)
        for history, selection in ranked_contexts
        if selection["similarity_percent"] >= similarity_floor
    ]

    for history, selection in close_contexts:
        candidate = build_prediction(
            cutoff_date=cutoff_date,
            model_dir=model_dir,
            save=False,
            history_override=history,
            context_selection_override=selection,
            target_week_start=next_start,
        )
        candidate["style_score"] = calculate_style_score(references, [candidate])
        candidate["selection_policy"] = {
            "name": "estilo_primero_contexto_cercano_v1",
            "best_context_similarity_percent": best_similarity,
            "context_similarity_floor_percent": round(similarity_floor, 1),
            "contexts_retrieved": len(ranked_contexts),
            "contexts_close": len(close_contexts),
        }
        if _week_signature(candidate["sessions"]) in recent_signatures:
            discarded_duplicates += 1
            continue
        candidate["novelty"] = {
            "compared_recent_weeks": len(recent_signatures),
            "discarded_exact_duplicates": 0,
        }
        candidates.append(candidate)

    candidates = [
        candidate for candidate in candidates
        if candidate["style_score"]["global_score"] >= MINIMUM_STYLE_SCORE
    ]
    candidates.sort(
        key=lambda item: (
            -item["style_score"]["global_score"],
            -item["context_selection"]["similarity_percent"],
        )
    )
    candidates = candidates[:max(1, int(limit))]
    for rank, candidate in enumerate(candidates, start=1):
        candidate["alternative_rank"] = rank
        candidate["novelty"]["discarded_exact_duplicates"] = discarded_duplicates
    return candidates


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
