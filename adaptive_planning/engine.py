"""Deterministic and fully traceable adaptive-planning rules."""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path
from statistics import median

from training_load.prescribed_load import plan_blocks_to_components
from training_load.training_load_calculator import calculate_session_load, load_config
from adaptive_planning.recipes import RECIPES, STATUS_URGENCY
from adaptive_planning.template_selector import compatible_templates
from config import PROJECT_ROOT


HIGH_INTENSITY_ZONES = {"Z4", "Z5", "Z6"}
EASY_ZONES = {"Z1", "Z2"}
CONFIDENCE_RANK = {"baja": 0, "media": 1, "alta": 2}
PERFORMED_LOAD_FILE = PROJECT_ROOT / "data" / "derived" / "training_load" / "performed_load.json"
PUBLISHED_PLANS_FILE = PROJECT_ROOT / "data" / "athlete_state" / "adaptive_published_plans.json"


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _snapshot(session):
    parts = [session.get("sport"), session.get("session_type"), session.get("zone")]
    return {
        "date": session.get("date"), "sport": session.get("sport"),
        "session_type": session.get("session_type"), "zone": session.get("zone"),
        "terrain": session.get("terrain"), "distance_km": session.get("distance_km"),
        "label": " · ".join(str(item) for item in parts if item),
    }


def _scale_distance(session, factor):
    """Scale explicit distance fields without inventing duration or load."""
    if _number(session.get("distance_km")):
        session["distance_km"] = round(session["distance_km"] * factor, 1)
    for block in session.get("blocks", []):
        if _number(block.get("distance_km")):
            block["distance_km"] = round(block["distance_km"] * factor, 1)


def _json_file(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")) if Path(path).exists() else default
    except (OSError, json.JSONDecodeError):
        return default


def _effective_activity_zone(components):
    """Return the highest meaningful FIT-derived zone for spacing purposes."""
    durations = {}
    for component in components or []:
        zone = component.get("zone")
        minutes = component.get("total_duration_minutes")
        if zone and _number(minutes):
            durations[zone] = durations.get(zone, 0.0) + float(minutes)
    # Brief incidental time in a high zone should not turn an easy activity
    # into an intensity day.  Sustained Z3 needs 20 min; Z4+ needs 8 min.
    thresholds = {"Z3": 20.0, "Z4": 8.0, "Z5": 8.0, "Z6": 8.0}
    for zone in ("Z6", "Z5", "Z4", "Z3"):
        if durations.get(zone, 0.0) >= thresholds[zone]:
            return zone
    return "Z2"


def _carry_in_intensity_sessions(week_start, days=2):
    """Build the last calendar days before a proposed week for rule S1.

    Actual FIT-derived load components are authoritative.  If a date has no
    FIT yet, the active approved plan is a clearly marked provisional fallback.
    The returned easy days deliberately remain in the sequence because they
    break a run of demanding days.
    """
    start = date.fromisoformat(week_start)
    dates = [(start - timedelta(days=offset)).isoformat() for offset in range(days, 0, -1)]
    activities = _json_file(PERFORMED_LOAD_FILE, {"activities": []}).get("activities", [])
    by_date = {}
    for activity in activities:
        day = activity.get("date")
        if day not in dates:
            continue
        details = activity.get("activity", {})
        sport = details.get("sport")
        if not sport:
            continue
        zone = _effective_activity_zone(activity.get("load", {}).get("components", []))
        candidate = {
            "date": day, "sport": sport, "zone": zone,
            "source": "FIT realizado", "activity_id": activity.get("activity_id"),
        }
        current = by_date.get(day)
        rank = {"Z2": 0, "Z3": 1, "Z4": 2, "Z5": 3, "Z6": 4}
        if current is None or rank.get(zone, 0) > rank.get(current.get("zone"), 0):
            by_date[day] = candidate

    plans = _json_file(PUBLISHED_PLANS_FILE, {"plans": []}).get("plans", [])
    active_sessions = {
        session.get("date"): session
        for plan in plans if plan.get("status") == "active"
        for session in plan.get("sessions", [])
        if session.get("date") in dates
    }
    for day in dates:
        if day in by_date:
            continue
        session = active_sessions.get(day)
        if session and session.get("sport") and session.get("sport") != "Descanso":
            by_date[day] = {
                "date": day, "sport": session.get("sport"),
                "zone": session.get("zone") or "Z2",
                "source": "Plan aprobado provisional", "activity_id": None,
            }
    return [by_date[day] for day in dates if day in by_date]


def _priorities_and_metrics(context, goal_names, metric_catalog):
    priorities, assigned_ids, primary_ids = [], set(), set()
    discipline_weight_totals = {}
    for window in context.get("active_windows", []):
        weights = window.get("discipline_weights", {})
        if not weights and window.get("discipline_priorities"):
            legacy = {"High": 60.0, "Medium": 30.0, "Low": 10.0, "None": 0.0}
            weights = {
                key: legacy.get(value, 30.0)
                for key, value in window["discipline_priorities"].items()
            }
        for discipline, weight in weights.items():
            discipline_weight_totals[discipline] = (
                discipline_weight_totals.get(discipline, 0.0) + float(weight)
            )
        for assignment in window.get("assignments", []):
            priority = assignment.get("priority")
            goal_id = assignment.get("development_goal_id")
            priorities.append({"goal_id": goal_id, "goal": goal_names.get(goal_id, goal_id), "priority": priority})
            ids = {item.get("metric_id") for item in assignment.get("metrics", []) if item.get("metric_id")}
            assigned_ids.update(ids)
            if priority == "Primary":
                primary_ids.update(ids)
    priorities.sort(key=lambda item: {"Primary": 0, "Secondary": 1, "Supporting": 2}.get(item["priority"], 9))
    metrics = {item.get("id"): item for item in metric_catalog}
    primary_disciplines = set()
    for metric_id in primary_ids:
        primary_disciplines.update(metrics.get(metric_id, {}).get("disciplines", []))
    total = sum(discipline_weight_totals.values())
    discipline_weights = (
        {key: 100.0 * value / total for key, value in discipline_weight_totals.items()}
        if total > 0 else {}
    )
    return priorities, assigned_ids, primary_ids, primary_disciplines, discipline_weights


def _normalized_discipline(value):
    if not value:
        return None
    return "Bici" if str(value).lower().startswith("bici") else value


def _geographic_signals(geographic_progress):
    """Summarize comparable route/segment series without mixing HR zones.

    Only the Athlete State's already-qualified series are admitted: each has
    five or more attempts and a classified observed HR zone.  A median across
    the eligible series avoids letting a single route dominate a discipline.
    """
    grouped = {}
    for item in (geographic_progress or {}).get("eligible_series", []):
        discipline = _normalized_discipline(item.get("discipline"))
        change = item.get("primary_change_percent")
        if not discipline or not _number(change):
            continue
        grouped.setdefault(discipline, []).append({
            "series_id": item.get("series_id"),
            "name": item.get("name"),
            "type": item.get("entity_type"),
            "zone": item.get("zone"),
            "change_percent": float(change),
            "reference": item.get("primary_reference"),
            "attempts": item.get("attempts"),
        })
    signals = {}
    for discipline, series in grouped.items():
        change = round(float(median(item["change_percent"] for item in series)), 2)
        status = "pérdida clara" if change <= -3 else (
            "mejora clara" if change >= 3 else "estable"
        )
        signals[discipline] = {
            "discipline": discipline,
            "status": status,
            "change_percent": change,
            "series_count": len(series),
            "declining": change <= -3,
            "improving": change >= 3,
            "has_clear_decline": any(item["change_percent"] <= -3 for item in series),
            "has_clear_improvement": any(item["change_percent"] >= 3 for item in series),
            "series": series,
        }
    return signals


def _choose_focus_discipline(
    sessions, availability, discipline_weights, evaluations, progress, training,
    geographic_signals=None,
):
    """Choose one discipline using explicit emphasis, then transparent fallbacks."""
    candidate_disciplines = sorted({
        session.get("sport") for session in sessions
        if session.get("sport") != "Descanso"
        and session.get("zone") in EASY_ZONES
        and _number(session.get("distance_km"))
        and availability.get(session.get("date"), {"status": "free"}).get("status") != "unavailable"
    })
    if not candidate_disciplines:
        return {
            "discipline": None, "source": "Sin candidata", "explanation": (
                "No existe una sesión aeróbica con distancia y disponibilidad compatible."
            ), "evidence": []
        }

    if discipline_weights:
        priorities = {
            discipline: float(discipline_weights.get(discipline, 0.0))
            for discipline in candidate_disciplines
        }
    else:
        priorities = {
            discipline: 100.0 / len(candidate_disciplines)
            for discipline in candidate_disciplines
        }
    highest_rank = max(priorities.values())
    priority_pool = [
        discipline for discipline in candidate_disciplines
        if priorities[discipline] == highest_rank and priorities[discipline] > 0
    ]
    differentiated = len(set(priorities.values())) > 1
    pool = priority_pool if differentiated and priority_pool else candidate_disciplines

    due_counts = {discipline: 0 for discipline in candidate_disciplines}
    for item in evaluations:
        discipline = _normalized_discipline(item.get("discipline"))
        if discipline in due_counts:
            due_counts[discipline] += 1
    stalled = {discipline: False for discipline in candidate_disciplines}
    for item in progress.values():
        discipline = _normalized_discipline(item.get("discipline"))
        trend = item.get("trend_per_30d")
        if discipline in stalled and _number(trend) and trend <= 0:
            stalled[discipline] = True
    geographic_signals = geographic_signals or {}
    geographic_decline = {
        discipline: bool(geographic_signals.get(discipline, {}).get("has_clear_decline"))
        for discipline in candidate_disciplines
    }

    history = training.get("discipline_progression_28d", {})
    load = training.get("discipline_load_28d", {})
    evidence = []
    for discipline in candidate_disciplines:
        details = history.get(discipline, {})
        evidence.append({
            "discipline": discipline,
            "priority": f"{priorities[discipline]:.0f}%",
            "evaluations_due": due_counts[discipline],
            "stalled_metric": stalled[discipline],
            "geographic_progress": geographic_signals.get(discipline, {}).get("status", "sin evidencia suficiente"),
            "geographic_change_percent": geographic_signals.get(discipline, {}).get("change_percent"),
            "days_since_progression": details.get("days_since_progression"),
            "load_28d": round(float(load.get(discipline, 0)), 1),
            "eligible": discipline in pool,
        })

    if differentiated and len(pool) == 1:
        selected = pool[0]
        return {
            "discipline": selected,
            "source": "Énfasis explícito de la ventana",
            "explanation": (
                f"{selected} tiene la mayor importancia declarada "
                f"({priorities[selected]:.0f}%)."
            ),
            "evidence": evidence,
        }

    metric_pool = [
        item for item in pool
        if due_counts[item] or stalled[item] or geographic_decline[item]
    ]
    selection_pool = metric_pool or pool

    def fallback_key(discipline):
        days = history.get(discipline, {}).get("days_since_progression")
        return (
            due_counts[discipline],
            int(stalled[discipline]),
            int(geographic_decline[discipline]),
            days if _number(days) else 9999,
            -float(load.get(discipline, 0)),
            discipline,
        )

    selected = max(selection_pool, key=fallback_key)
    if metric_pool:
        reason = "tiene una evaluación pendiente, una métrica sin progreso o una pérdida clara en una ruta/segmento comparable"
        source = "Necesidad de medición o progreso"
    elif history.get(selected, {}).get("days_since_progression") is not None:
        reason = "lleva más tiempo sin una progresión reciente"
        source = "Rotación por progresión reciente"
    else:
        reason = "presenta la menor exposición reciente entre las opciones compatibles"
        source = "Exposición reciente y semana base"
    prefix = "Entre las disciplinas con mayor énfasis, " if differentiated else "Sin una prioridad única, "
    return {
        "discipline": selected,
        "source": source,
        "explanation": f"{prefix}se elige {selected} porque {reason}.",
        "evidence": evidence,
    }


def _active_recipe_signals(context, metric_catalog, freshness, progress, weights, sessions, geographic_signals=None):
    """Rank active goal recipes using assignment, freshness and metric trend."""
    metric_by_id = {item.get("id"): item for item in metric_catalog}
    session_sports = {item.get("sport") for item in sessions if item.get("sport") != "Descanso"}
    eligible_disciplines = session_sports | {
        key for key, value in weights.items() if value > 0
    }
    assigned = {}
    priority_points = {"Primary": 30, "Secondary": 20, "Supporting": 10}
    for window in context.get("active_windows", []):
        for assignment in window.get("assignments", []):
            goal_id = assignment.get("development_goal_id")
            if goal_id not in RECIPES:
                continue
            assigned_metrics = {
                item.get("metric_id") for item in assignment.get("metrics", [])
                if item.get("metric_id")
            }
            current = assigned.setdefault(goal_id, {
                "goal_id": goal_id,
                "recipe": RECIPES[goal_id],
                "priority": assignment.get("priority"),
                "metric_ids": set(),
            })
            current["metric_ids"].update(assigned_metrics or RECIPES[goal_id]["metric_ids"])
            if priority_points.get(assignment.get("priority"), 0) > priority_points.get(current.get("priority"), 0):
                current["priority"] = assignment.get("priority")

    geographic_signals = geographic_signals or {}
    signals = []
    for goal_id, item in assigned.items():
        relevant_freshness = [
            row for row in freshness
            if row.get("metric_id") in item["metric_ids"]
            and (
                not _normalized_discipline(row.get("discipline"))
                or _normalized_discipline(row.get("discipline")) in eligible_disciplines
            )
        ]
        urgency = max((STATUS_URGENCY.get(row.get("status"), 0) for row in relevant_freshness), default=0)
        relevant_progress = [
            row for row in progress.values()
            if row.get("metric_id") in item["metric_ids"]
            and (
                not _normalized_discipline(row.get("discipline"))
                or _normalized_discipline(row.get("discipline")) in eligible_disciplines
            )
        ]
        stalled_rows = [
            row for row in relevant_progress
            if _number(row.get("trend_per_30d")) and row["trend_per_30d"] <= 0
            and row.get("confidence") in {"media", "alta"}
        ]
        discipline_candidates = []
        ranked_measurements = sorted(
            relevant_freshness,
            key=lambda row: STATUS_URGENCY.get(row.get("status"), 0),
            reverse=True,
        )
        selected_measurement = ranked_measurements[0] if ranked_measurements else None
        for row in ranked_measurements:
            discipline = _normalized_discipline(row.get("discipline"))
            if discipline:
                discipline_candidates.append(discipline)
        for metric_id in item["metric_ids"]:
            discipline_candidates.extend(
                _normalized_discipline(value)
                for value in metric_by_id.get(metric_id, {}).get("disciplines", [])
            )
        discipline_candidates = [
            value for value in discipline_candidates if value in eligible_disciplines
        ] or list(session_sports)
        discipline = max(
            set(discipline_candidates),
            key=lambda value: (float(weights.get(value, 0)), -discipline_candidates.index(value)),
            default=None,
        )
        discipline_influence = float(weights.get(discipline, 0)) if discipline else 0.0
        geographic_signal = geographic_signals.get(discipline, {})
        geographic_decline = bool(geographic_signal.get("declining"))
        geographic_attention = bool(geographic_signal.get("has_clear_decline"))
        score = (
            priority_points.get(item.get("priority"), 0)
            + urgency * 10
            + (8 if stalled_rows else 0)
            + (6 if geographic_decline else 0)
            + (3 if geographic_attention and not geographic_decline else 0)
            + discipline_influence * .3
        )
        signals.append({
            "goal_id": goal_id,
            "goal": item["recipe"]["name"],
            "operation": item["recipe"]["operation"],
            "summary": item["recipe"]["summary"],
            "allowed_changes": item["recipe"]["allowed_changes"],
            "assignment_priority": item.get("priority"),
            "urgency": urgency,
            "stalled": bool(stalled_rows),
            "geographic_signal": geographic_signal or None,
            "discipline": discipline,
            "metric_id": selected_measurement.get("metric_id") if selected_measurement else None,
            "metric_name": selected_measurement.get("metric_name") if selected_measurement else None,
            "score": score,
            "reason": (
                "medición pendiente" if urgency >= 4 else
                "medición próxima a vencer" if urgency >= 2 else
                "pérdida clara en ruta o segmento comparable" if geographic_attention else
                "tendencia estancada o descendente" if stalled_rows else
                "continuidad del estímulo"
            ),
        })
    return sorted(signals, key=lambda item: item["score"], reverse=True)


def _rest_session_like(session):
    return {
        "date": session.get("date"), "weekday": session.get("weekday"),
        "sport": "Descanso", "session_type": None, "structured": False,
        "objective_class": None, "template": None, "terrain": None,
        "zone": None, "distance_km": None, "blocks": [{"type": "rest"}],
    }


def _create_aerobic_session(session, discipline, training):
    defaults = {"Bici": 30.0, "Corrida": 6.0, "Trail": 6.0, "Kayak": 5.0}
    recent = training.get("discipline_progression_28d", {}).get(discipline, {})
    longest = recent.get("longest_session_28d_km")
    distance = round(max(defaults.get(discipline, 6.0), float(longest or 0)), 1)
    terrain = "Plano" if discipline == "Kayak" else "Montaña" if discipline == "Trail" else "Ondulado"
    return {
        "date": session.get("date"), "weekday": session.get("weekday"),
        "sport": discipline, "session_type": "Aeróbico continuo", "structured": False,
        "objective_class": "adaptive_emphasis", "template": "Sesión creada por énfasis de ventana",
        "terrain": terrain, "zone": "Z2", "distance_km": distance,
        "blocks": [{"type": "steady", "zone": "Z2", "distance_km": distance}],
    }


def _apply_progression_memory(
    sessions, discipline, progression_memory, capacity_mode, availability,
    training, changes, strength,
):
    """Progress, consolidate or regress from the last subjectively evaluated stimulus."""
    memory = (progression_memory or {}).get("disciplines", {}).get(discipline, {})
    action = memory.get("action", "insufficient_evidence")
    reference = memory.get("longest_well_tolerated") or memory.get("latest_evaluated") or {}
    evidence = {
        "discipline": discipline, "action": action,
        "latest_evaluated": memory.get("latest_evaluated"),
        "longest_well_tolerated": memory.get("longest_well_tolerated"),
        "well_tolerated_streak": memory.get("well_tolerated_streak", 0),
        "result": "Sin cambio",
    }
    candidates = [
        item for item in sessions
        if item.get("sport") == discipline and not item.get("structured")
        and _number(item.get("distance_km"))
    ]
    if not candidates or not _number(reference.get("distance_km")):
        evidence["reason"] = "No existe una sesión comparable o una referencia evaluada."
        return evidence
    session = max(candidates, key=lambda item: float(item["distance_km"]))
    current = float(session["distance_km"])
    reference_distance = float(reference["distance_km"])
    minimum = 5.0 if discipline == "Bici" else 1.0

    if action == "progress" and capacity_mode == "build":
        step = .10 if discipline in {"Bici", "Kayak"} else .07
        full_target = reference_distance * (1 + step)
        target = current + strength * (full_target - current)
        if target > current and target - current >= minimum:
            before = _snapshot(session)
            _scale_distance(session, target / current)
            changes.append({
                "change_id": f"memory_progress_{discipline}_{session['date']}",
                "type": "scale_distance", "rule_id": "M1",
                "title": f"Progresar la sesión de referencia de {discipline}",
                "before": before, "after": _snapshot(session),
                "signal": (
                    f"Última respuesta evaluable bien tolerada; referencia {reference_distance:.1f} km; "
                    f"racha {memory.get('well_tolerated_streak', 0)}."
                ),
                "operation": f"Siguiente escalón completo {full_target:.1f} km; alternativa aplicada al {strength:.0%}.",
                "limit": "Se progresa una sola variable: distancia. La carga semanal sigue limitada por Athlete State.",
                "expected_effect": "Construir sobre una dosis demostrablemente tolerada, no sobre el rango del modelo.",
            })
            evidence["result"] = f"Progresión {current:.1f} → {target:.1f} km"
    elif action == "regress":
        target = min(current, max(reference_distance * .90, current * .90))
        if current - target >= minimum:
            before = _snapshot(session)
            _scale_distance(session, target / current)
            changes.append({
                "change_id": f"memory_regress_{discipline}_{session['date']}",
                "type": "scale_distance", "rule_id": "M1",
                "title": f"Retroceder el estímulo de {discipline}",
                "before": before, "after": _snapshot(session),
                "signal": "La última sesión evaluable fue clasificada como no tolerada.",
                "operation": "Reducir 10% la sesión comparable y reevaluar la respuesta.",
                "limit": "No se añade intensidad ni se progresa otra variable de esta sesión.",
                "expected_effect": "Volver a una dosis tolerable antes de intentar un nuevo escalón.",
            })
            evidence["result"] = f"Retroceso {current:.1f} → {target:.1f} km"
    else:
        evidence["reason"] = (
            "La respuesta exige consolidar o aún no existe evidencia subjetiva suficiente."
            if action != "progress" else "Athlete State no habilita construcción esta semana."
        )

    # Frequency progression is deliberately harder to unlock than distance progression.
    active = [item for item in sessions if item.get("sport") != "Descanso"]
    discipline_sessions = [item for item in active if item.get("sport") == discipline]
    if (
        action == "progress" and capacity_mode == "build" and strength >= .65
        and int(memory.get("well_tolerated_streak", 0)) >= 2
        and len(discipline_sessions) <= 1
    ):
        rest_candidates = [
            item for index, item in enumerate(sessions)
            if item.get("sport") == "Descanso"
            and availability.get(item.get("date"), {"status": "free"}).get("status") == "free"
            and all(
                abs(index - other_index) > 1
                for other_index, other in enumerate(sessions)
                if other.get("zone") in HIGH_INTENSITY_ZONES
            )
        ]
        if rest_candidates:
            rest = rest_candidates[0]
            before = _snapshot(rest)
            created = _create_aerobic_session(rest, discipline, training)
            support_distance = max(minimum, round(reference_distance * .25, 1))
            created["distance_km"] = support_distance
            created["session_type"] = "Aeróbico de apoyo"
            created["template"] = "Sesión añadida por progresión de frecuencia"
            created["blocks"] = [{"type": "steady", "zone": "Z2", "distance_km": support_distance}]
            rest.clear(); rest.update(created)
            changes.append({
                "change_id": f"memory_add_{discipline}_{rest['date']}",
                "type": "replace_session", "rule_id": "M2",
                "title": f"Agregar una sesión de apoyo de {discipline}",
                "before": before, "after": _snapshot(rest),
                "signal": f"Dos o más sesiones evaluables consecutivas fueron bien toleradas ({memory.get('well_tolerated_streak')}).",
                "operation": f"Usar un día libre para una sesión Z2 equivalente al 25% de la referencia ({support_distance:.1f} km).",
                "limit": "Sólo se agrega si hay descanso libre; no añade intensidad y permanece dentro del presupuesto semanal.",
                "expected_effect": "Progresar frecuencia sin concentrar todo el estímulo en una única sesión.",
            })
            evidence["added_session"] = f"{rest['date']} · {support_distance:.1f} km Z2"
    return evidence


def _ensure_recipe_discipline(sessions, recipe_signal, weights, availability, training, changes, strength):
    """Create an absent discipline only when the selected recipe requires it."""
    if strength < .60:
        return
    present = {item.get("sport") for item in sessions if item.get("sport") != "Descanso"}
    discipline = (recipe_signal or {}).get("discipline")
    if not discipline or discipline in present or float(weights.get(discipline, 0)) <= 0:
        return
    rest = next((item for item in sessions if item.get("sport") == "Descanso"
                 and availability.get(item.get("date"), {"status": "free"}).get("status") == "free"), None)
    if rest is None:
        return
    before = _snapshot(rest)
    replacement = _create_aerobic_session(rest, discipline, training)
    rest.clear(); rest.update(replacement)
    changes.append({
        "change_id": f"create_{discipline}_{rest['date']}", "type": "replace_session", "rule_id": "O1",
        "title": f"Crear una sesión aeróbica de {discipline}", "before": before, "after": _snapshot(rest),
        "signal": f"La receta seleccionada requiere {discipline} y la semana base no la contiene.",
        "operation": "Usar un día de descanso libre con una dosis inicial basada en las últimas cuatro semanas.",
        "limit": "La nueva sesión es Z2 y no añade intensidad.",
        "expected_effect": "Atender una receta o medición concreta, no completar una cuota porcentual.",
    })


def _select_historical_template(operation, discipline, session, availability):
    """Select the compatible real template closest to the session's load envelope."""
    try:
        base_details = _estimated_session_details(session)
    except (KeyError, TypeError, ValueError):
        base_details = {"total_load": 0.0, "duration_minutes": 0.0}
    day = availability.get(session.get("date"), {"status": "free"})
    maximum_minutes = day.get("max_minutes") if day.get("status") == "limited" else None
    evaluated = []
    for template in compatible_templates(operation, discipline):
        trial = deepcopy(session)
        trial.update({
            "structured": True,
            "blocks": deepcopy(template["blocks"]),
            "terrain": trial.get("terrain") or "Plano",
        })
        try:
            details = _estimated_session_details(trial)
        except (KeyError, TypeError, ValueError):
            continue
        base_load = base_details["total_load"]
        load_difference = (
            abs(details["total_load"] - base_load) / base_load * 100
            if base_load > 0 else 0
        )
        time_excess = (
            max(0.0, details["duration_minutes"] - maximum_minutes)
            if _number(maximum_minutes) else 0.0
        )
        score = (
            100
            - .55 * load_difference
            - 2.0 * time_excess
            + min(10, template.get("count", 0))
        )
        evaluated.append({
            **template,
            "estimated_load": round(details["total_load"], 1),
            "duration_minutes": round(details["duration_minutes"], 1),
            "load_difference_percent": round(load_difference, 1),
            "score": round(score, 1),
        })
    if not evaluated:
        return None, []
    evaluated.sort(key=lambda item: (item["score"], item.get("count", 0)), reverse=True)
    return evaluated[0], evaluated[:5]


def _apply_recipe_to_week(sessions, signal, allow_intensity, availability, changes):
    if not signal or not signal.get("discipline"):
        return False, "No existe una receta activa compatible con la semana.", None
    discipline, operation = signal["discipline"], signal["operation"]
    candidates = [item for item in sessions if item.get("sport") == discipline and item.get("sport") != "Descanso"]
    if not candidates:
        return False, f"No existe una sesión de {discipline} para aplicar la receta.", None
    if operation in {"threshold", "maximal_aerobic"} and not allow_intensity:
        return False, "La receta intensa queda en espera porque Athlete State no habilita construcción esta semana.", None
    before_session = max(candidates, key=lambda item: float(item.get("distance_km") or 0))
    before = _snapshot(before_session)
    if operation == "aerobic_efficiency":
        before_session["zone"] = "Z2"
        before_session["session_type"] = "Aeróbico continuo"
        if discipline in {"Corrida", "Trail"}:
            before_session["terrain"] = "Plano"
        for block in before_session.get("blocks", []):
            if block.get("type") != "rest": block["zone"] = "Z2"
    elif operation == "vertical":
        before_session["zone"] = before_session.get("zone") if before_session.get("zone") in {"Z2", "Z3"} else "Z3"
        before_session["terrain"] = "Montaña" if discipline == "Trail" else "Subida"
        before_session["session_type"] = "Ascenso sostenido"
    elif operation == "kayak_endurance":
        before_session["zone"] = "Z2"
        before_session["session_type"] = "Resistencia continua"
        for block in before_session.get("blocks", []):
            if block.get("type") != "rest": block["zone"] = "Z2"
    else:
        selected_template, template_candidates = _select_historical_template(
            operation, discipline, before_session, availability
        )
        if selected_template is None:
            return False, "No existe una plantilla histórica compatible y calculable.", None
        work_zone = "Z4" if operation == "threshold" else "Z5"
        label = selected_template["signature"]
        before_session.update({
            "structured": True,
            "session_type": selected_template.get("objective_class") or signal["goal"],
            "zone": work_zone,
            "objective_class": selected_template.get("objective_class") or signal["goal_id"],
            "template_id": selected_template.get("template_id"),
            "template": label,
            "blocks": deepcopy(selected_template["blocks"]),
        })
        metric_id = signal.get("metric_id") or ""
        if "vam" in metric_id:
            before_session["terrain"] = "Montaña" if discipline == "Trail" else "Subida"
        elif "speed" in metric_id:
            before_session["terrain"] = "Plano"
        template_selection = {
            "selected": {
                key: selected_template.get(key)
                for key in (
                    "template_id", "signature", "objective_class", "count",
                    "example_week", "estimated_load", "duration_minutes", "score"
                )
            },
            "alternatives": [
                {
                    key: item.get(key)
                    for key in (
                        "template_id", "signature", "objective_class", "count",
                        "estimated_load", "duration_minutes", "score"
                    )
                }
                for item in template_candidates
            ],
        }
    if operation not in {"threshold", "maximal_aerobic"}:
        template_selection = None
    changes.append({
        "change_id": f"recipe_{signal['goal_id']}_{before_session['date']}",
        "type": "replace_session", "rule_id": "O2",
        "title": f"Aplicar receta de {signal['goal']}", "before": before, "after": _snapshot(before_session),
        "signal": f"{signal['reason'].capitalize()}; receta activa: {signal['summary']}",
        "operation": (
            f"Usar plantilla histórica: {before_session.get('template')}."
            if template_selection else ", ".join(signal["allowed_changes"])
        ),
        "limit": "Se aplica una receta principal por semana; el resto de los objetivos se mantiene.",
        "expected_effect": f"Dar un estímulo trazable al objetivo {signal['goal']}.",
    })
    note = (
        f"Plantilla histórica seleccionada: {before_session.get('template')}"
        if template_selection else signal["summary"]
    )
    return True, note, template_selection


def _week_distribution(sessions):
    by_sport, total = {}, 0.0
    for session in sessions:
        if session.get("sport") == "Descanso": continue
        try: load = _estimated_session_details(session)["total_load"]
        except (KeyError, TypeError, ValueError): continue
        by_sport[session.get("sport")] = by_sport.get(session.get("sport"), 0.0) + load
        total += load
    return total, {key: 100 * value / total for key, value in by_sport.items()} if total else {}


def _align_candidate_load(sessions, target_load, weights, recipe_signal, changes):
    """Concentrate a meaningful load adjustment in one or two sessions."""
    adjustable, fixed = [], 0.0
    for session in sessions:
        if session.get("sport") == "Descanso":
            continue
        try:
            load = _estimated_session_details(session)["total_load"]
        except (KeyError, TypeError, ValueError):
            continue
        if not session.get("structured") and _number(session.get("distance_km")):
            adjustable.append((session, load))
        else:
            fixed += load
    adjustable_load = sum(item[1] for item in adjustable)
    current_total = fixed + adjustable_load
    if not adjustable_load or target_load <= fixed or abs(target_load - current_total) / current_total < .02:
        return
    delta = target_load - current_total
    recipe_discipline = (recipe_signal or {}).get("discipline")
    if delta >= 0:
        adjustable.sort(key=lambda item: (
            item[0].get("sport") == recipe_discipline,
            float(weights.get(item[0].get("sport"), 0)),
            float(item[0].get("distance_km") or 0),
        ), reverse=True)
    else:
        adjustable.sort(key=lambda item: (
            item[0].get("sport") == recipe_discipline,
            -float(weights.get(item[0].get("sport"), 0)),
            -float(item[0].get("distance_km") or 0),
        ))

    selected = adjustable[:2]
    first_session, first_load = selected[0]
    first_distance = float(first_session["distance_km"])
    first_load_per_km = first_load / first_distance
    required_first_km = abs(delta) / first_load_per_km
    first_minimum = 5.0 if first_session.get("sport") == "Bici" else 1.0
    if required_first_km < first_minimum:
        return

    allocations = [(first_session, first_load, delta)]
    split_threshold = max(first_minimum * 2, first_distance * .20)
    if len(selected) > 1 and required_first_km >= split_threshold:
        second_session, second_load = selected[1]
        second_distance = float(second_session["distance_km"])
        second_load_per_km = second_load / second_distance
        second_delta = delta * .30
        second_minimum = 5.0 if second_session.get("sport") == "Bici" else 1.0
        if abs(second_delta) / second_load_per_km >= second_minimum:
            allocations = [
                (first_session, first_load, delta * .70),
                (second_session, second_load, second_delta),
            ]

    for session, session_load, load_delta in allocations:
        distance = float(session["distance_km"])
        load_per_km = session_load / distance
        distance_delta = load_delta / load_per_km
        minimum = 5.0 if session.get("sport") == "Bici" else 1.0
        if abs(distance_delta) < minimum:
            continue
        new_distance = max(distance * .25, distance + distance_delta)
        factor = new_distance / distance
        before = _snapshot(session)
        _scale_distance(session, factor)
        changes.append({
            "change_id": f"load_alignment_{session['date']}", "type": "scale_distance", "rule_id": "O3",
            "title": f"Alinear carga de la sesión del {session['date']}",
            "before": before, "after": _snapshot(session),
            "signal": f"Carga total objetivo de la alternativa: {target_load:.1f}.",
            "operation": (
                f"Concentrar {load_delta:+.1f} puntos de carga en esta sesión "
                f"({distance:+.1f} → {new_distance:.1f} km), priorizada por receta e influencia."
            ),
            "limit": (
                "Sólo se muestran cambios de al menos 5 km en Bici o 1 km en "
                "Corrida, Trail y Kayak; máximo dos sesiones por ajuste de carga."
            ),
            "expected_effect": "Crear un estímulo perceptible sin repartir microajustes por toda la semana.",
        })


def _optimize_week(base_sessions, weights, availability, training, recipe_signal,
                   capacity_mode, progression_memory, focus_discipline):
    base_load = _estimated_week_load(base_sessions) or 0.0
    chronic = training.get("chronic_load_42d_equivalent")
    focus_memory = (progression_memory or {}).get("disciplines", {}).get(focus_discipline, {})
    tolerated_streak = int(focus_memory.get("well_tolerated_streak", 0))
    if capacity_mode == "build":
        growth = 1.10 if tolerated_streak >= 2 else 1.08 if tolerated_streak >= 1 else 1.05
        target_load = max(base_load, float(chronic or 0), base_load * growth)
    elif capacity_mode == "absorb": target_load = min(base_load * .85, float(chronic or base_load) * .85)
    else: target_load = base_load
    variants = [("Conservadora", .35), ("Equilibrada", .65), ("Directa", 1.0)]
    candidates = []
    for name, strength in variants:
        sessions, changes = deepcopy(base_sessions), []
        _ensure_recipe_discipline(
            sessions, recipe_signal, weights, availability, training, changes, strength
        )
        candidate_target = base_load + strength * (target_load - base_load)
        memory_evidence = _apply_progression_memory(
            sessions, focus_discipline, progression_memory, capacity_mode,
            availability, training, changes, strength,
        ) if focus_discipline else {"result": "Sin disciplina de foco"}
        recipe_applied, recipe_note, template_selection = _apply_recipe_to_week(
            sessions, recipe_signal, capacity_mode == "build", availability, changes
        )
        _align_candidate_load(sessions, candidate_target, weights, recipe_signal, changes)
        total, distribution = _week_distribution(sessions)
        load_error = abs(total - candidate_target) / candidate_target * 100 if candidate_target else 0
        demanding_dates = sorted(item.get("date") for item in sessions if item.get("zone") in {"Z4", "Z5", "Z6"})
        consecutive = sum(
            (date.fromisoformat(demanding_dates[i]) - date.fromisoformat(demanding_dates[i - 1])).days == 1
            for i in range(1, len(demanding_dates))
        )
        preferred_strength = .65 if capacity_mode == "build" else .35
        strength_penalty = 15 * abs(strength - preferred_strength)
        recipe_discipline = (recipe_signal or {}).get("discipline")
        maximum_weight = max(weights.values(), default=0) if weights else 0
        influence_bonus = (
            5 * float(weights.get(recipe_discipline, 0)) / maximum_weight
            if recipe_discipline and maximum_weight > 0 else 0
        )
        score = min(
            100,
            85 - .75 * load_error - 20 * consecutive - strength_penalty
            + (8 if recipe_applied else 0) + influence_bonus,
        )
        candidates.append({
            "name": name, "strength": strength, "score": round(score, 1),
            "estimated_load": round(total, 1), "target_load": round(candidate_target, 1),
            "distribution": {key: round(value, 1) for key, value in distribution.items()},
            "recipe_applied": recipe_applied, "recipe_note": recipe_note,
            "template_selection": template_selection,
            "progression_memory": memory_evidence,
            "changes_count": len({
                item.get("after", {}).get("date") for item in changes
                if item.get("after", {}).get("date")
            }),
            "sessions": sessions, "changes": changes,
            "selected": False,
        })
    selected = max(candidates, key=lambda item: item["score"])
    selected["selected"] = True
    return candidates, selected


def _coalesce_optimizer_changes(base_sessions, adapted_sessions, changes):
    """Expose one independently selectable final transformation per day."""
    base_by_date = {item.get("date"): item for item in base_sessions}
    adapted_by_date = {item.get("date"): item for item in adapted_sessions}
    result = []
    for day in sorted(adapted_by_date):
        before_session, after_session = base_by_date.get(day), adapted_by_date[day]
        if before_session == after_session:
            continue
        related = [item for item in changes if item.get("after", {}).get("date") == day]
        if not related:
            continue
        result.append({
            "change_id": f"optimize_{day}", "type": "replace_session", "rule_id": "O1/O2",
            "title": f"Optimizar la sesión del {day}",
            "before": _snapshot(before_session), "after": _snapshot(after_session),
            "signal": " ".join(dict.fromkeys(item["signal"] for item in related)),
            "operation": " ".join(dict.fromkeys(item["operation"] for item in related)),
            "limit": "Cambio diario consolidado; puede aprobarse o excluirse de forma independiente.",
            "expected_effect": " ".join(dict.fromkeys(item["expected_effect"] for item in related)),
        })
    return result


def _estimated_week_load(sessions):
    """Calculate comparable load in memory with the existing load engine."""
    config = load_config()
    total = 0.0
    for session in sessions:
        if session.get("sport") == "Descanso":
            continue
        try:
            calculated = _estimated_session_details(session, config)
            total += calculated["total_load"]
        except (KeyError, TypeError, ValueError):
            return None
    return total


def _estimated_session_details(session, config=None):
    if session.get("sport") == "Descanso":
        return {"total_load": 0.0, "duration_minutes": 0.0}
    config = config or load_config()
    components, _ = plan_blocks_to_components(session.get("blocks") or [])
    calculated = calculate_session_load(
        sport=session.get("sport"), terrain=session.get("terrain"), components=components,
        structured=bool(session.get("structured")), config=config,
    )
    return {"total_load": calculated["total_load"],
            "duration_minutes": sum(item.get("total_duration_minutes", 0) for item in calculated["components"])}


def _apply_time_limits(sessions, availability, changes):
    violations, unresolved = [], []
    for session in sessions:
        day = availability.get(session.get("date"), {})
        maximum = day.get("max_minutes") if day.get("status") == "limited" else None
        if not _number(maximum) or session.get("sport") == "Descanso":
            continue
        try:
            estimated = _estimated_session_details(session)["duration_minutes"]
        except (KeyError, TypeError, ValueError):
            unresolved.append({"date": session.get("date"), "reason": "No fue posible estimar la duración."})
            continue
        if estimated <= maximum:
            continue
        violations.append({"date": session.get("date"), "estimated_minutes": round(estimated, 1), "maximum_minutes": maximum})
        if session.get("structured") or not _number(session.get("distance_km")):
            unresolved.append({"date": session.get("date"), "reason": "La sesión estructurada excede el tiempo y no puede recortarse sólo por distancia."})
            continue
        before = _snapshot(session)
        factor = maximum / estimated
        _scale_distance(session, factor)
        changes.append({
            "change_id": f"time_limit_{session.get('date')}", "type": "scale_distance", "rule_id": "A2",
            "title": f"Ajustar la sesión al máximo de {maximum:.0f} minutos", "before": before, "after": _snapshot(session),
            "signal": f"Duración estimada {estimated:.1f} min; disponibilidad máxima {maximum:.0f} min.",
            "operation": f"Distancia base × ({maximum:.0f} / {estimated:.1f}).",
            "limit": "No se cambia deporte, zona ni terreno.",
            "expected_effect": "Hacer que la sesión quepa en el tiempo declarado.",
        })
    return violations, unresolved


def _redistribute_consecutive_intensity(
    sessions, availability, changes, allow_cross_family_z3=True, carry_in_sessions=None,
):
    """Separate conflicting intensity with the least disruptive valid change.

    Z4-Z6 always require an easy/rest day before another Z3+ session. Two Z3
    days may remain together when they use different mechanical families, but
    Corrida and Trail share one impact family and three consecutive Z3 days are
    never retained. The resolution hierarchy is: relocate, soften Z3 to Z2,
    then convert one session to rest only when no softer solution is feasible.
    """
    sessions.sort(key=lambda item: item.get("date", ""))
    carry_in_sessions = sorted(
        deepcopy(carry_in_sessions or []), key=lambda item: item.get("date", "")
    )

    def with_carry(candidate_sessions):
        return carry_in_sessions + list(candidate_sessions)

    def mechanical_family(session):
        sport = session.get("sport")
        if sport in {"Corrida", "Trail"}:
            return "impacto_corrida"
        return sport

    def conflict_index(candidate_sessions):
        demanding = {"Z3", "Z4", "Z5", "Z6"}
        high = {"Z4", "Z5", "Z6"}
        for index in range(1, len(candidate_sessions)):
            previous = candidate_sessions[index - 1]
            current = candidate_sessions[index]
            previous_zone, current_zone = previous.get("zone"), current.get("zone")
            if previous_zone not in demanding or current_zone not in demanding:
                continue
            if previous_zone in high or current_zone in high:
                return index
            if not allow_cross_family_z3:
                return index
            if mechanical_family(previous) == mechanical_family(current):
                return index
        for index in range(2, len(candidate_sessions)):
            if all(
                candidate_sessions[position].get("zone") == "Z3"
                for position in (index - 2, index - 1, index)
            ):
                return index
        return None

    def conflict_count(candidate_sessions):
        demanding = {"Z3", "Z4", "Z5", "Z6"}
        high = {"Z4", "Z5", "Z6"}
        count = 0
        for index in range(1, len(candidate_sessions)):
            previous, current = candidate_sessions[index - 1], candidate_sessions[index]
            previous_zone, current_zone = previous.get("zone"), current.get("zone")
            if previous_zone not in demanding or current_zone not in demanding:
                continue
            if previous_zone in high or current_zone in high:
                count += 1
            elif not allow_cross_family_z3 or mechanical_family(previous) == mechanical_family(current):
                count += 1
        for index in range(2, len(candidate_sessions)):
            if all(candidate_sessions[position].get("zone") == "Z3" for position in (index - 2, index - 1, index)):
                count += 1
        return count

    conflict = conflict_index(with_carry(sessions))
    if conflict is None or conflict < len(carry_in_sessions):
        return False
    session_conflict = conflict - len(carry_in_sessions)
    source = sessions[session_conflict]
    initial_conflict_count = conflict_count(with_carry(sessions))
    candidates = []
    for index, target in enumerate(sessions):
        if target.get("zone") not in EASY_ZONES and target.get("sport") != "Descanso":
            continue
        if availability.get(target.get("date"), {"status": "free"}).get("status") == "unavailable":
            continue
        trial_sessions = list(sessions)
        trial_sessions[index], trial_sessions[session_conflict] = (
            trial_sessions[session_conflict], trial_sessions[index]
        )
        if conflict_count(with_carry(trial_sessions)) < initial_conflict_count:
            candidates.append((abs(index - session_conflict), index))
    if candidates:
        target = sessions[min(candidates)[1]]
        before = _snapshot(source)
        source_date, source_weekday = source["date"], source.get("weekday")
        target_date, target_weekday = target["date"], target.get("weekday")
        source["date"], source["weekday"] = target_date, target_weekday
        target["date"], target["weekday"] = source_date, source_weekday
        sessions.sort(key=lambda item: item.get("date", ""))
        changes.append({
            "change_id": f"intensity_spacing_{source_date}", "type": "relocate_session", "rule_id": "S1",
            "title": f"Separar sesiones exigentes moviendo {source_date} a {target_date}",
            "before": before, "after": _snapshot(source),
            "signal": (
                "La semana contiene intensidad consecutiva incompatible: Z4–Z6 "
                "junto a otro día Z3+, dos Z3 de la misma familia mecánica o tres "
                "días Z3 seguidos."
            ),
            "operation": "Intercambiar con el día suave o de descanso compatible más cercano.",
            "limit": "La sesión conserva disciplina, distancia, intensidad y terreno.",
            "expected_effect": "Crear al menos un día de separación entre estímulos exigentes.",
        })
        return True

    def load_proxy(session):
        """Prefer the least costly session when intensity must be reduced."""
        try:
            return float(_estimated_session_details(session).get("total_load", float("inf")))
        except (KeyError, TypeError, ValueError):
            return float(session.get("distance_km") or float("inf"))

    def soften_z3(session):
        """Turn a simple Z3 session into an equivalent Z2 session."""
        session["zone"] = "Z2"
        session["session_type"] = "Leve"
        for block in session.get("blocks", []):
            if block.get("zone") == "Z3":
                block["zone"] = "Z2"

    # A reordering cannot solve the conflict. Preserve frequency and volume by
    # reducing the least costly simple Z3 session to Z2 whenever that resolves it.
    softening_candidates = []
    for index, candidate in enumerate(sessions):
        if (
            candidate.get("zone") != "Z3"
            or candidate.get("sport") == "Descanso"
            or candidate.get("structured")
        ):
            continue
        trial_sessions = deepcopy(sessions)
        soften_z3(trial_sessions[index])
        if conflict_count(with_carry(trial_sessions)) < initial_conflict_count:
            softening_candidates.append((load_proxy(candidate), index))
    if softening_candidates:
        source = sessions[min(softening_candidates)[1]]
        before = _snapshot(source)
        soften_z3(source)
        changes.append({
            "change_id": f"intensity_soften_{before['date']}", "type": "replace_session", "rule_id": "S1",
            "title": f"Suavizar la sesión del {before['date']} de Z3 a Z2",
            "before": before, "after": _snapshot(source),
            "signal": "La semana contiene intensidad consecutiva incompatible y no existe un día suave o de descanso al cual reubicarla.",
            "operation": "Sustituir el estímulo Z3 menos costoso que resuelve el conflicto por trabajo continuo Z2.",
            "limit": "Se conservan disciplina, distancia y terreno; solo se reduce la intensidad.",
            "expected_effect": "Romper la secuencia de intensidad manteniendo frecuencia y volumen semanal.",
        })
        return True

    def convert_to_rest(session):
        """Use rest only after relocation and softening have both failed."""
        session.update({
            "sport": "Descanso", "session_type": None, "structured": False,
            "objective_class": None, "template": None, "terrain": None,
            "zone": None, "distance_km": None, "blocks": [{"type": "rest"}],
        })

    rest_candidates = []
    for index, candidate in enumerate(sessions):
        if candidate.get("sport") == "Descanso":
            continue
        trial_sessions = deepcopy(sessions)
        convert_to_rest(trial_sessions[index])
        if conflict_count(with_carry(trial_sessions)) < initial_conflict_count:
            rest_candidates.append((load_proxy(candidate), index))
    if rest_candidates:
        source = sessions[min(rest_candidates)[1]]
        before = _snapshot(source)
        convert_to_rest(source)
        changes.append({
            "change_id": f"intensity_rest_{before['date']}", "type": "replace_session", "rule_id": "S1",
            "title": f"Convertir la sesión del {before['date']} en descanso",
            "before": before, "after": _snapshot(source),
            "signal": "La semana contiene intensidad consecutiva incompatible sin una reubicación o suavización segura disponible.",
            "operation": "Retirar la sesión menos costosa para crear un día real de recuperación.",
            "limit": "Último recurso: solo se aplica cuando no existe una solución que conserve la sesión.",
            "expected_effect": "Restablecer una separación recuperativa entre estímulos exigentes.",
        })
    return True


def _rule(rule_id, name, observed, threshold, triggered, consequence, evidence):
    return {"rule_id": rule_id, "name": name, "observed": observed, "threshold": threshold,
            "triggered": triggered, "consequence": consequence, "evidence": evidence}


def _relocate_unavailable(sessions, availability, changes):
    unavailable, unresolved = [], []
    for index, session in enumerate(sessions):
        day = availability.get(session.get("date"), {"status": "free"})
        if session.get("sport") != "Descanso" and day.get("status") == "unavailable":
            unavailable.append(index)
    for source_index in unavailable:
        source = sessions[source_index]
        candidates = []
        for target_index, target in enumerate(sessions):
            day = availability.get(target.get("date"), {"status": "free"})
            if target.get("sport") == "Descanso" and day.get("status") == "free":
                candidates.append((abs(target_index - source_index), target_index))
        if not candidates:
            unresolved.append({"date": source.get("date"), "sport": source.get("sport"),
                               "reason": "No existe un día de descanso libre al cual moverla."})
            continue
        target = sessions[min(candidates)[1]]
        before = _snapshot(source)
        source_date, source_weekday = source.get("date"), source.get("weekday")
        target_date, target_weekday = target.get("date"), target.get("weekday")
        source["date"], source["weekday"] = target_date, target_weekday
        target["date"], target["weekday"] = source_date, source_weekday
        changes.append({
            "change_id": f"availability_{source_date}", "type": "relocate_session", "rule_id": "A1",
            "title": f"Mover sesión de {source_date} a {target_date}", "before": before, "after": _snapshot(source),
            "signal": f"El {source_date} está marcado como no disponible (0 minutos).",
            "operation": "Intercambiar la sesión con el día de descanso libre más cercano.",
            "limit": "No se modifica deporte, zona, terreno ni distancia.",
            "expected_effect": "Hacer ejecutable la semana sin alterar su carga prescrita.",
        })
    sessions.sort(key=lambda item: item.get("date", ""))
    return unavailable, unresolved


def _apply_reduction(sessions, easy_factor, hard_factor, rule_id, reason, changes):
    for session in sessions:
        if session.get("sport") == "Descanso" or not _number(session.get("distance_km")):
            continue
        factor = hard_factor if session.get("zone") in HIGH_INTENSITY_ZONES else easy_factor
        before = _snapshot(session)
        _scale_distance(session, factor)
        after = _snapshot(session)
        if before["distance_km"] == after["distance_km"]:
            continue
        percentage = round((1 - factor) * 100)
        changes.append({
            "change_id": f"{rule_id}_{session.get('date')}", "type": "scale_distance", "rule_id": rule_id,
            "title": f"Reducir {percentage}% la distancia del {session.get('date')}", "before": before, "after": after,
            "signal": reason, "operation": f"Distancia adaptada = distancia base × {factor:.2f}.",
            "limit": "Se conserva la disciplina, el tipo de sesión, la zona y el terreno.",
            "expected_effect": "Reducir la demanda semanal sin eliminar por completo el estímulo planificado.",
        })


def _apply_concentrated_overload_reduction(
    sessions, relative_balance, training, reason, changes,
):
    """Remove a proportional weekly dose from at most two meaningful sessions."""
    overload_ratio = max(0.0, -float(relative_balance))
    # At the activation threshold (25%) remove 5% of weekly load. Above it,
    # half of the additional excess is translated into more unloading, capped
    # at 15% so one signal cannot erase the week's structure.
    target_week_fraction = min(
        .15, max(.05, .05 + .5 * (overload_ratio - .25))
    )
    base_week_load = _estimated_week_load(sessions) or 0.0
    target_load_reduction = base_week_load * target_week_fraction
    recent_load = (
        training.get("discipline_load_7d")
        or training.get("discipline_load_28d")
        or {}
    )
    candidates = []
    for session in sessions:
        if session.get("sport") == "Descanso" or not _number(session.get("distance_km")):
            continue
        try:
            planned_load = _estimated_session_details(session)["total_load"]
        except (KeyError, TypeError, ValueError):
            continue
        candidates.append({
            "session": session,
            "planned_load": float(planned_load),
            "recent_discipline_load": float(recent_load.get(session.get("sport"), 0)),
        })
    if not candidates or target_load_reduction <= 0:
        return {
            "target_fraction": target_week_fraction,
            "target_load_reduction": target_load_reduction,
            "applied_load_reduction": 0.0,
            "sessions_changed": 0,
        }

    # First choose the most costly future session of each recently loaded
    # discipline. This prevents several tiny cuts while reflecting where the
    # preceding seven-day load was accumulated.
    best_by_discipline = {}
    for candidate in candidates:
        discipline = candidate["session"].get("sport")
        current = best_by_discipline.get(discipline)
        if current is None or candidate["planned_load"] > current["planned_load"]:
            best_by_discipline[discipline] = candidate
    selected = sorted(
        best_by_discipline.values(),
        key=lambda item: (
            item["recent_discipline_load"], item["planned_load"]
        ),
        reverse=True,
    )[:2]
    if len(selected) < 2:
        remaining = sorted(
            (item for item in candidates if item not in selected),
            key=lambda item: (
                item["recent_discipline_load"], item["planned_load"]
            ),
            reverse=True,
        )
        selected.extend(remaining[:2 - len(selected)])

    selected_load = sum(item["planned_load"] for item in selected)
    # A single overload signal may reduce at most 35% of either selected
    # session. If that is insufficient, the unapplied remainder stays visible
    # in the audit instead of spreading microadjustments over more days.
    applied_load_reduction = min(target_load_reduction, selected_load * .35)
    factor = 1.0 - applied_load_reduction / selected_load
    percentage = round((1.0 - factor) * 100, 1)
    for item in selected:
        session = item["session"]
        before = _snapshot(session)
        _scale_distance(session, factor)
        after = _snapshot(session)
        if before["distance_km"] == after["distance_km"]:
            continue
        changes.append({
            "change_id": f"L1_{session.get('date')}",
            "type": "scale_distance", "rule_id": "L1",
            "title": f"Concentrar la descarga en la sesión del {session.get('date')}",
            "before": before, "after": after,
            "signal": reason,
            "operation": (
                f"Reducir {percentage:.1f}% esta sesión para concentrar una descarga "
                f"semanal objetivo de {100 * target_week_fraction:.1f}%."
            ),
            "limit": (
                "Máximo dos sesiones; se conserva disciplina, tipo, zona y terreno. "
                "Ninguna sesión se reduce más de 35%."
            ),
            "expected_effect": (
                "Crear espacio de recuperación perceptible sin repartir microajustes "
                "por toda la semana."
            ),
        })
    return {
        "target_fraction": target_week_fraction,
        "target_load_reduction": target_load_reduction,
        "applied_load_reduction": applied_load_reduction,
        "sessions_changed": len(selected),
    }


def build_adaptive_proposal(prediction, athlete_state, goal_names=None, metric_catalog=None):
    """Return an adapted week plus a complete audit trail for every decision."""
    goal_names, metric_catalog = goal_names or {}, metric_catalog or []
    interpretation = athlete_state.get("interpretation", {})
    training, context = athlete_state.get("training", {}), athlete_state.get("context", {})
    base_sessions = deepcopy(prediction.get("sessions", []))
    sessions, changes, rules = deepcopy(base_sessions), [], []
    availability = {item["date"]: item for item in context.get("availability_next_14d", [])}
    (
        priorities, assigned_ids, primary_ids, primary_disciplines,
        discipline_weights,
    ) = _priorities_and_metrics(context, goal_names, metric_catalog)
    freshness = [item for item in athlete_state.get("measurement_freshness", [])
                 if not assigned_ids or item.get("metric_id") in assigned_ids]
    evaluations = [item for item in freshness if item.get("status") in
                   {"Sin medición", "Próxima a vencer", "Evaluación pendiente", "Desactualizada"}]
    score = interpretation.get("readiness_score")
    confidence = interpretation.get("confidence", "baja")
    recovery_latest = (athlete_state.get("recovery", {}).get("latest") or {})
    discomfort = bool(recovery_latest.get("has_discomfort"))
    fatigue = recovery_latest.get("subjective_fatigue")
    muscular_fatigue = recovery_latest.get(
        "muscle_fatigue", recovery_latest.get("muscle_soreness")
    )
    allow_cross_family_z3 = (
        not discomfort
        and (not _number(score) or score >= 40)
        and (not _number(fatigue) or fatigue < 7)
        and (not _number(muscular_fatigue) or muscular_fatigue < 7)
    )

    unavailable, unresolved = _relocate_unavailable(sessions, availability, changes)
    rules.append(_rule("A1", "Disponibilidad obligatoria", f"{len(unavailable)} sesión(es) en días con 0 minutos",
                       "Ninguna sesión puede quedar en un día no disponible", bool(unavailable),
                       "Reubicar en el descanso libre más cercano" if unavailable else "Sin cambios",
                       "Disponibilidad calendarizada y fechas de la semana base"))
    time_violations, time_unresolved = _apply_time_limits(sessions, availability, changes)
    unresolved.extend(time_unresolved)
    rules.append(_rule("A2", "Límite horario diario",
                       f"{len(time_violations)} sesión(es) exceden el máximo diario",
                       "Duración de la sesión ≤ máximo disponible", False,
                       "Recortar proporcionalmente la distancia o declarar conflicto", "Disponibilidad horaria y duración estimada"))
    rules[-1]["triggered"] = bool(time_violations)
    spacing_change_start = len(changes)
    carry_in_intensity = _carry_in_intensity_sessions(prediction.get("week_start"))
    intensity_conflict = False
    # One adjustment can expose the next conflict (especially across the
    # weekend boundary), so resolve sequentially until no further safe change
    # is available. A seven-day plan bounds this loop naturally.
    for _ in range(len(sessions)):
        before_changes = len(changes)
        found_conflict = _redistribute_consecutive_intensity(
            sessions,
            availability,
            changes,
            allow_cross_family_z3=allow_cross_family_z3,
            carry_in_sessions=carry_in_intensity,
        )
        intensity_conflict = intensity_conflict or found_conflict
        if not found_conflict or len(changes) == before_changes:
            break
    spacing_applied = len(changes) > spacing_change_start
    rules.append(_rule("S1", "Separación de intensidad",
                       "Hay intensidad consecutiva incompatible" if intensity_conflict else "La distribución de intensidad es compatible",
                       "Separar Z4–Z6; permitir hasta dos Z3 consecutivas sólo entre familias mecánicas diferentes y con recuperación compatible",
                       intensity_conflict, "Reubicar, suavizar o convertir en descanso según la alternativa menos invasiva" if spacing_applied else "Sin solución automática disponible",
                       (
                           "Secuencia semanal más "
                           + (
                               ", ".join(
                                   f"{item['date']} {item['sport']} {item['zone']} ({item['source']})"
                                   for item in carry_in_intensity
                               )
                               if carry_in_intensity else "sin actividades previas disponibles"
                           )
                       )))

    geographic_signals = _geographic_signals(athlete_state.get("geographic_progress", {}))
    discipline_focus = _choose_focus_discipline(
        sessions,
        availability,
        discipline_weights,
        evaluations,
        athlete_state.get("progress", {}),
        training,
        geographic_signals,
    )

    acute, chronic, balance = (training.get("acute_load_7d"), training.get("chronic_load_42d_equivalent"),
                                training.get("load_balance"))
    relative = balance / chronic if _number(balance) and _number(chronic) and chronic > 0 else None
    low_recovery = bool(_number(score) and score < 40 and CONFIDENCE_RANK.get(confidence, 0) >= 1)
    overloaded = bool(_number(relative) and relative <= -0.25)
    progression = bool(
        _number(score) and score >= 55
        and CONFIDENCE_RANK.get(confidence, 0) >= 1
        and _number(relative) and relative >= 0.20
        and not discomfort
    )
    primary_progress = [item for item in athlete_state.get("progress", {}).values()
                        if item.get("metric_id") in primary_ids]
    stalled_primary = any(
        _number(item.get("trend_per_30d")) and item["trend_per_30d"] <= 0
        and item.get("confidence") in {"media", "alta"}
        for item in primary_progress
    )
    recipe_signals = _active_recipe_signals(
        context,
        metric_catalog,
        freshness,
        athlete_state.get("progress", {}),
        discipline_weights,
        sessions,
        geographic_signals,
    )
    selected_recipe = recipe_signals[0] if recipe_signals else None
    relative_text = f"{relative:.1%}" if relative is not None else "sin base suficiente"
    rules.extend([
        _rule("R1", "Recuperación limitada", f"Preparación {score:.1f}/100; confianza {confidence}" if _number(score) else f"Preparación sin puntaje; confianza {confidence}",
              "Puntaje < 40 y confianza media/alta", low_recovery, "Reducir 10% Z1–Z3 y 15% Z4–Z6",
              "Sueño, VFC, FC reposo, fatiga, molestias y carga"),
        _rule("L1", "Sobrecarga aguda", f"Balance relativo {relative_text}", "(crónica − aguda) / crónica ≤ −25%",
              overloaded, "Concentrar una reducción proporcional al exceso en un máximo de dos sesiones", "Carga aguda de 7 días frente a carga crónica equivalente"),
        _rule("P1", "Progresión aeróbica dirigida", f"Preparación {score:.1f}/100; balance relativo {relative_text}; foco {discipline_focus.get('discipline') or 'sin candidata'}" if _number(score) else f"Preparación sin puntaje; balance relativo {relative_text}",
              "Preparación ≥55, confianza media/alta, carga aguda ≥20% bajo la habitual y sin molestia",
              progression, "Progresar una sesión de la disciplina seleccionada desde la referencia realizada",
              "Preparación, balance de carga, molestias y exposición reciente; rutas/segmentos orientan el estímulo, no limitan la carga"),
        _rule("O1", "Optimización semanal por objetivos",
              f"Receta principal: {selected_recipe['goal'] if selected_recipe else 'ninguna'}; "
              f"énfasis: {', '.join(f'{key} {value:.0f}%' for key, value in discipline_weights.items()) or 'equilibrado'}",
              "Generar alternativas completas compatibles con Athlete State y seleccionar la de mayor puntaje",
              bool(selected_recipe or discipline_weights),
              "Redistribuir carga y aplicar como máximo una receta principal",
              "Ventana activa, métricas, carga, recuperación, disponibilidad y semana base"),
    ])

    optimization_candidates = []
    progression_result = None
    applied_safety_rules = []
    safety_actions = []

    # Availability and intensity are constraints, not exclusive modes.  Once
    # they make the plan executable, every applicable recovery/load safeguard
    # must still be evaluated against the resulting week.
    if unavailable or time_violations:
        applied_safety_rules.append("A1" if unavailable else "A2")
        safety_actions.append("se resolvió la disponibilidad")
    if intensity_conflict:
        applied_safety_rules.append("S1")
        safety_actions.append("se separó la intensidad incompatible")
    if low_recovery:
        _apply_reduction(sessions, .90, .85, "R1", f"Preparación {score:.1f}/100 bajo el umbral de 40.", changes)
        applied_safety_rules.append("R1")
        safety_actions.append("se aplicó una reducción preventiva")
    if overloaded:
        reduction = _apply_concentrated_overload_reduction(
            sessions, relative, training,
            f"Carga aguda {acute:.1f}; carga crónica equivalente {chronic:.1f}.",
            changes,
        )
        applied_safety_rules.append("L1")
        safety_actions.append(
            f"se concentró una descarga objetivo de {100 * reduction['target_fraction']:.1f}% "
            f"en {reduction['sessions_changed']} sesión(es)"
        )

    if applied_safety_rules:
        decision = "AJUSTES DE SEGURIDAD ACUMULADOS"
        action = (
            " · ".join(safety_actions).capitalize()
            + ". Las recetas de objetivos no aumentan la carga mientras haya una salvaguarda activa."
        )
    else:
        capacity_mode = "build" if progression else "maintain"
        optimizer_candidates, selected = _optimize_week(
            sessions,
            discipline_weights,
            availability,
            training,
            selected_recipe,
            capacity_mode,
            athlete_state.get("progression_memory", {}),
            discipline_focus.get("discipline"),
        )
        optimization_candidates = optimizer_candidates
        sessions = selected["sessions"]
        changes.extend(selected["changes"])
        memory_result = selected.get("progression_memory", {})
        memory_triggered = memory_result.get("result", "").startswith(("Progresión", "Retroceso"))
        rules.append(_rule(
            "M1", "Memoria de progresión longitudinal",
            (
                f"{memory_result.get('discipline') or 'Sin disciplina'} · "
                f"{memory_result.get('action', 'sin evidencia')} · "
                f"{memory_result.get('result', 'Sin cambio')}"
            ),
            "Progresar sólo tras una respuesta de resistencia bien tolerada",
            memory_triggered,
            memory_result.get("result", "Mantener la referencia actual"),
            "FIT realizado, percepción postentrenamiento y recuperación posterior de 48 h",
        ))
        if memory_result.get("added_session"):
            rules.append(_rule(
                "M2", "Progresión de frecuencia",
                memory_result["added_session"],
                "Al menos dos respuestas bien toleradas consecutivas y un día libre",
                True, "Agregar una sesión aeróbica Z2 de apoyo",
                "Memoria longitudinal, disponibilidad y presupuesto semanal de carga",
            ))
        decision = "CONSTRUIR Y OPTIMIZAR" if progression else "OPTIMIZAR MANTENIENDO CARGA"
        action = (
            f"Alternativa {selected['name'].lower()}: carga estimada {selected['estimated_load']:.1f}; "
            f"{selected['recipe_note']}"
        )

    if optimization_candidates:
        changes = _coalesce_optimizer_changes(base_sessions, sessions, changes)

    for collection in (base_sessions, sessions):
        for session in collection:
            try:
                details = _estimated_session_details(session)
                session["estimated_duration_minutes"] = round(details["duration_minutes"], 1)
                session["estimated_load"] = round(details["total_load"], 1)
            except (KeyError, TypeError, ValueError):
                session["estimated_duration_minutes"] = None
                session["estimated_load"] = None

    return {
        "mode": "adaptive_v15", "engine_version": "adaptive_engine_v15", "decision": decision,
        "recommended_action": action, "confidence": confidence,
        "positive_factors": interpretation.get("positive_factors", []),
        "limiting_factors": interpretation.get("limiting_factors", []),
        "availability_conflicts": unresolved, "goal_priorities": priorities,
        "intensity_carry_in": carry_in_intensity,
        "primary_disciplines": sorted(primary_disciplines),
        "discipline_focus": discipline_focus,
        "progression_memory": athlete_state.get("progression_memory", {}),
        "discipline_weights": discipline_weights,
        "geographic_signals": geographic_signals,
        "recipe_signals": recipe_signals,
        "selected_recipe": selected_recipe,
        "progression_result": progression_result,
        "evaluations_due": evaluations,
        "base_sessions": base_sessions, "adapted_sessions": sessions, "changes": changes,
        "optimization_candidates": [{key: value for key, value in item.items() if key not in {"sessions", "changes"}}
                                    for item in optimization_candidates],
        "rule_evaluations": rules,
        "guardrails": ["Las restricciones de disponibilidad, intensidad, recuperación y carga se aplican de forma acumulativa.",
                       "La disponibilidad impide una sesión en un día bloqueado, pero no anula las reducciones de carga necesarias.",
                       "Se aplica una receta fisiológica principal por semana; las demás capacidades se mantienen.",
                       "Los porcentajes son pesos de decisión; no son cuotas de carga ni de kilómetros.",
                       "Una receta intensa sólo se aplica cuando Athlete State habilita construcción.",
                       "La progresión longitudinal sólo se habilita después de una sesión evaluada como bien tolerada.",
                       "Dos respuestas bien toleradas consecutivas pueden habilitar una sesión Z2 adicional si existe un día libre.",
                       "El margen de carga puede distribuirse entre varias sesiones; se descartan microajustes menores a 5 km en Bici o 1 km en las demás disciplinas.",
                       "Las alternativas se comparan por carga, énfasis, objetivo atendido y separación de intensidad.",
                       "Las métricas, rutas y segmentos priorizan el estímulo; no bloquean por sí solos una progresión de carga.",
                       "La aprobación humana es obligatoria antes de publicar."],
        "note": "Propuesta determinista y reversible: cada cambio conserva regla y valores antes/después.",
    }
