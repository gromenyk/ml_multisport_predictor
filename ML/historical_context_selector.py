"""Choose a coherent human four-week context for the base prediction.

The selector never uses adaptive plans as model input.  It compares the
athlete's recent *performed* profile with every consecutive four-week block of
human plans, then returns the closest complete block and an auditable summary.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from math import log
from pathlib import Path
import json
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = PROJECT_ROOT / "app"
SELECTION_HISTORY_FILE = PROJECT_ROOT / "data" / "athlete_state" / "historical_context_selection.json"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


SPORTS = ("Bici", "Trail", "Corrida", "Kayak")
ZONES = ("Z1", "Z2", "Z3", "Z4", "Z5", "Z6")
DEFAULT_SPEED_KMH = {"Bici": 24.0, "Trail": 8.0, "Corrida": 10.0, "Kayak": 7.0}


def _share(values: dict[str, float], keys: tuple[str, ...]) -> dict[str, float]:
    total = sum(max(0.0, float(values.get(key, 0.0) or 0.0)) for key in keys)
    if total <= 0:
        return {key: 0.0 for key in keys}
    return {key: max(0.0, float(values.get(key, 0.0) or 0.0)) / total for key in keys}


def _zone_number(zone: str | None) -> int:
    try:
        return int(str(zone or "Z2").upper().replace("Z", ""))
    except ValueError:
        return 2


def _component_minutes(component: dict[str, Any], sport: str) -> float:
    duration = component.get("duration_min")
    if duration is not None:
        try:
            return max(0.0, float(duration))
        except (TypeError, ValueError):
            pass
    distance = component.get("distance_km")
    try:
        return max(0.0, float(distance)) / DEFAULT_SPEED_KMH.get(sport, 10.0) * 60.0
    except (TypeError, ValueError):
        return 0.0


def _plan_profile(weeks: list[dict[str, Any]]) -> dict[str, Any]:
    sport_minutes: defaultdict[str, float] = defaultdict(float)
    zone_minutes: defaultdict[str, float] = defaultdict(float)
    total_minutes = 0.0
    sessions = 0
    intense_sessions = 0

    for week in weeks:
        for session in week.get("sessions", []):
            sport = session.get("sport")
            if sport not in SPORTS:
                continue
            components = session.get("blocks") or []
            session_minutes = 0.0
            session_intense = False
            for component in components:
                if not isinstance(component, dict):
                    continue
                minutes = _component_minutes(component, sport)
                if minutes <= 0:
                    continue
                zone = str(component.get("zone") or "Z2").upper()
                sport_minutes[sport] += minutes
                zone_minutes[zone] += minutes
                total_minutes += minutes
                session_minutes += minutes
                session_intense = session_intense or _zone_number(zone) >= 4
            if session_minutes > 0:
                sessions += 1
                intense_sessions += int(session_intense)

    weeks_count = max(1, len(weeks))
    return {
        "weekly_minutes": total_minutes / weeks_count,
        "weekly_sessions": sessions / weeks_count,
        "weekly_intense_sessions": intense_sessions / weeks_count,
        "sport_share": _share(sport_minutes, SPORTS),
        "zone_share": _share(zone_minutes, ZONES),
        "sport_minutes": dict(sport_minutes),
        "zone_minutes": dict(zone_minutes),
    }


def _current_profile(as_of_date: date | str) -> dict[str, Any]:
    from athlete_state.state_builder import build_athlete_state

    training = build_athlete_state(as_of_date).get("training", {})
    zone_minutes = training.get("zone_minutes_28d") or {}
    discipline_load = training.get("discipline_load_28d") or {}
    activities = training.get("session_continuity", {}).get("activities_28d") or []
    intense = sum(
        1 for activity in activities
        if str(activity.get("effective_zone") or activity.get("zone") or "").upper() in {"Z4", "Z5", "Z6"}
    )
    # FIT activity rows do not always carry an effective zone. Zone-minute share
    # remains the primary intensity signal in that case.
    return {
        "weekly_minutes": sum(float(value or 0.0) for value in zone_minutes.values()) / 4.0,
        "weekly_sessions": len(activities) / 4.0,
        "weekly_intense_sessions": intense / 4.0,
        "sport_share": _share(discipline_load, SPORTS),
        "zone_share": _share(zone_minutes, ZONES),
        "sport_minutes": discipline_load,
        "zone_minutes": zone_minutes,
    }


def _distribution_distance(left: dict[str, float], right: dict[str, float], keys: tuple[str, ...]) -> float:
    return sum(abs(float(left.get(key, 0.0)) - float(right.get(key, 0.0))) for key in keys) / 2.0


def _relative_distance(left: float, right: float) -> float:
    if left <= 0 and right <= 0:
        return 0.0
    return min(1.0, abs(log((left + 1.0) / (right + 1.0))))


def _score(candidate: dict[str, Any], current: dict[str, Any]) -> tuple[float, dict[str, float]]:
    components = {
        "volumen": _relative_distance(candidate["weekly_minutes"], current["weekly_minutes"]),
        "disciplinas": _distribution_distance(candidate["sport_share"], current["sport_share"], SPORTS),
        "intensidad": _distribution_distance(candidate["zone_share"], current["zone_share"], ZONES),
        "frecuencia": _relative_distance(candidate["weekly_sessions"], current["weekly_sessions"]),
        "sesiones_intensas": _relative_distance(
            candidate["weekly_intense_sessions"], current["weekly_intense_sessions"]
        ),
    }
    weights = {"volumen": 0.25, "disciplinas": 0.30, "intensidad": 0.25, "frecuencia": 0.12, "sesiones_intensas": 0.08}
    return sum(components[key] * weights[key] for key in weights), components


def _consecutive_blocks(weeks: list[dict[str, Any]], length: int = 4):
    for start in range(0, len(weeks) - length + 1):
        block = weeks[start:start + length]
        if all(
            date.fromisoformat(current["week_start"])
            == date.fromisoformat(previous["week_end"]) + timedelta(days=1)
            for previous, current in zip(block, block[1:])
        ):
            yield block


def _load_previous_selection(history_file: Path = SELECTION_HISTORY_FILE) -> dict[str, Any] | None:
    try:
        payload = json.loads(history_file.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def record_context_selection(selection: dict[str, Any], target_week_start: date | str) -> None:
    """Persist only the chosen historical reference for the next planning cycle."""
    target = target_week_start.isoformat() if isinstance(target_week_start, date) else str(target_week_start)
    payload = {
        "target_week_start": target,
        "selected_week_start": selection["week_start"],
        "selected_week_end": selection["week_end"],
        "similarity_percent": selection["similarity_percent"],
        "recorded_at": date.today().isoformat(),
    }
    SELECTION_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    SELECTION_HISTORY_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def select_historical_context(
    weeks: list[dict[str, Any]],
    as_of_date: date | str,
    length: int = 4,
    target_week_start: date | str | None = None,
    history_file: Path = SELECTION_HISTORY_FILE,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return the most similar consecutive block of human plans and its evidence."""
    current = _current_profile(as_of_date)
    candidates = []
    for block in _consecutive_blocks(weeks, length):
        profile = _plan_profile(block)
        score, components = _score(profile, current)
        candidates.append((score, block, profile, components))
    if not candidates:
        raise ValueError(f"No hay bloques consecutivos de {length} semanas humanas disponibles.")

    # Lower is better. For an exact tie, the more recent real block wins.
    best_score, best_block, best_profile, best_components = min(
        candidates,
        key=lambda item: (item[0], -date.fromisoformat(item[1][-1]["week_end"]).toordinal()),
    )
    previous = _load_previous_selection(history_file)
    target = target_week_start.isoformat() if isinstance(target_week_start, date) else target_week_start
    by_start = {item[1][0]["week_start"]: item for item in candidates}
    score, block, profile, components = best_score, best_block, best_profile, best_components
    decision = "perfil_más_parecido"

    if previous and target and previous.get("target_week_start") == str(target):
        # Regenerating the same operational week must remain reproducible.
        saved = by_start.get(previous.get("selected_week_start"))
        if saved:
            score, block, profile, components = saved
            decision = "misma_semana_reutiliza_contexto"
    similarity = max(0.0, min(100.0, (1.0 - score) * 100.0))
    selection = {
        "method": "historical_similarity_v1",
        "decision": decision,
        "week_start": block[0]["week_start"],
        "week_end": block[-1]["week_end"],
        "similarity_percent": round(similarity, 1),
        "score": round(score, 4),
        "criteria": {
            "volumen": round(components["volumen"], 4),
            "disciplinas": round(components["disciplinas"], 4),
            "intensidad": round(components["intensidad"], 4),
            "frecuencia": round(components["frecuencia"], 4),
            "sesiones_intensas": round(components["sesiones_intensas"], 4),
        },
        "current_profile": current,
        "selected_profile": profile,
        "candidate_count": len(candidates),
    }
    return block, selection


def rank_historical_contexts(
    weeks: list[dict[str, Any]],
    as_of_date: date | str,
    length: int = 4,
    limit: int = 5,
    target_week_start: date | str | None = None,
    history_file: Path = SELECTION_HISTORY_FILE,
) -> list[tuple[list[dict[str, Any]], dict[str, Any]]]:
    """Return contexts ordered strictly by similarity to performed state.

    Repetition is evaluated after every block has generated a concrete week.
    This keeps the historical context as a library of styles, rather than an
    artificial sequence which the athlete must follow.
    """
    current = _current_profile(as_of_date)
    candidates = []
    for block in _consecutive_blocks(weeks, length):
        profile = _plan_profile(block)
        score, components = _score(profile, current)
        candidates.append((score, block, profile, components))
    if not candidates:
        raise ValueError(f"No hay bloques consecutivos de {length} semanas humanas disponibles.")

    def rank_key(item):
        score, block, _profile, _components = item
        return (score, -date.fromisoformat(block[-1]["week_end"]).toordinal())

    ranked = sorted(candidates, key=rank_key)[: max(1, int(limit))]
    results = []
    for rank, (score, block, profile, components) in enumerate(ranked, start=1):
        similarity = max(0.0, min(100.0, (1.0 - score) * 100.0))
        decision = "alternativa_contexto_historico_similar"
        results.append((block, {
            "method": "historical_similarity_v1",
            "decision": decision,
            "rank": rank,
            "week_start": block[0]["week_start"],
            "week_end": block[-1]["week_end"],
            "similarity_percent": round(similarity, 1),
            "score": round(score, 4),
            "criteria": {key: round(value, 4) for key, value in components.items()},
            "current_profile": current,
            "selected_profile": profile,
            "candidate_count": len(candidates),
        }))
    return results
