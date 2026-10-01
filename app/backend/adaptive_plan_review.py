"""LLM explanation layer for a deterministic adaptive-plan proposal.

This module never changes the proposal.  It packages the engine audit trail and
the four preceding weeks of *performed* FIT activity for human-readable review.
"""

from __future__ import annotations

from datetime import date, timedelta
import json
import os
from typing import Any

from dotenv import load_dotenv

from config import PROJECT_ROOT
from backend.physiological_analysis import _strength_training_evidence


PERFORMED_LOAD_FILE = PROJECT_ROOT / "data" / "derived" / "training_load" / "performed_load.json"
load_dotenv(PROJECT_ROOT / ".env")


def _read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except (OSError, json.JSONDecodeError):
        return default


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _compact_session(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": session.get("date"),
        "sport": session.get("sport"),
        "zone": session.get("zone"),
        "terrain": session.get("terrain"),
        "distance_km": round(_number(session.get("distance_km")), 1),
        "duration_minutes": round(_number(session.get("estimated_duration_minutes") or session.get("duration_min")), 0),
        "estimated_load": round(_number(session.get("estimated_load")), 1) if session.get("estimated_load") is not None else None,
        "template": session.get("template"),
    }


def _compact_change(change: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": change.get("title"),
        "signal": change.get("signal"),
        "operation": change.get("operation"),
        "expected_effect": change.get("expected_effect"),
        "before": _compact_session(change.get("before") or {}),
        "after": _compact_session(change.get("after") or {}),
    }


def _four_weeks_realized(as_of: date) -> list[dict[str, Any]]:
    """Aggregate actual FIT-derived activity in four consecutive seven-day bins."""
    activities = _read(PERFORMED_LOAD_FILE, {"activities": []}).get("activities", [])
    start = as_of - timedelta(days=27)
    buckets: list[dict[str, Any]] = []
    for offset in (0, 7, 14, 21):
        period_start = start + timedelta(days=offset)
        period_end = min(as_of, period_start + timedelta(days=6))
        selected = [
            item for item in activities
            if period_start.isoformat() <= str(item.get("date") or "") <= period_end.isoformat()
        ]
        by_discipline: dict[str, dict[str, Any]] = {}
        zone_minutes: dict[str, float] = {}
        total_load = total_distance = total_minutes = 0.0
        for item in selected:
            activity, load = item.get("activity") or {}, item.get("load") or {}
            sport = str(activity.get("sport") or "Otro")
            duration = _number(activity.get("duration_seconds")) / 60
            distance = _number(activity.get("distance_km"))
            calculated = _number(load.get("calculated_load"))
            total_load += calculated
            total_distance += distance
            total_minutes += duration
            entry = by_discipline.setdefault(sport, {"sessions": 0, "distance_km": 0.0, "minutes": 0.0, "load": 0.0})
            entry["sessions"] += 1
            entry["distance_km"] += distance
            entry["minutes"] += duration
            entry["load"] += calculated
            for component in load.get("components") or []:
                zone = str(component.get("zone") or "Sin zona")
                zone_minutes[zone] = zone_minutes.get(zone, 0.0) + _number(component.get("total_duration_minutes"))
        buckets.append({
            "period": f"{period_start.isoformat()} a {period_end.isoformat()}",
            "source": "actividades realizadas desde FIT; no incluye semanas predichas ni planes adaptados futuros",
            "sessions": len(selected),
            "load": round(total_load, 1),
            "distance_km": round(total_distance, 1),
            "active_minutes": round(total_minutes, 0),
            "by_discipline": {
                sport: {key: round(value, 1) if isinstance(value, float) else value for key, value in details.items()}
                for sport, details in sorted(by_discipline.items())
            },
            "zone_minutes": {zone: round(minutes, 1) for zone, minutes in sorted(zone_minutes.items())},
        })
    return buckets


def build_adaptive_review_evidence(
    prediction: dict[str, Any], proposal: dict[str, Any], athlete_state: dict[str, Any]
) -> dict[str, Any]:
    """Return a compact, auditable packet for the plan-review prompt."""
    as_of = date.fromisoformat(str(athlete_state.get("as_of_date") or date.today().isoformat()))
    training = athlete_state.get("training") or {}
    recovery = athlete_state.get("recovery") or {}
    return {
        "review_scope": "Explicacion fisiologica de una propuesta ya calculada. El LLM no puede cambiar, aprobar ni rechazar el plan.",
        "as_of_date": as_of.isoformat(),
        "week_under_review": {
            "start": prediction.get("week_start"), "end": prediction.get("week_end"),
            "base_sessions": [_compact_session(item) for item in proposal.get("base_sessions") or prediction.get("sessions") or []],
            "adapted_sessions": [_compact_session(item) for item in proposal.get("adapted_sessions") or []],
            "changes": [_compact_change(item) for item in proposal.get("changes") or []],
        },
        "engine_audit": {
            "engine_version": proposal.get("engine_version"), "decision": proposal.get("decision"),
            "confidence": proposal.get("confidence"), "recommended_action": proposal.get("recommended_action"),
            "active_recipe": proposal.get("selected_recipe"), "recipe_signals": proposal.get("recipe_signals") or [],
            "rule_evaluations": proposal.get("rule_evaluations") or [],
            "guardrails": proposal.get("guardrails") or [],
            "discipline_weights": proposal.get("discipline_weights") or {},
            "goal_priorities": proposal.get("goal_priorities") or [],
            "evaluation_opportunities": proposal.get("evaluation_opportunities") or [],
        },
        "athlete_state_used": {
            "interpretation": athlete_state.get("interpretation") or {},
            "training": {key: training.get(key) for key in (
                "acute_load_7d", "previous_load_7d", "chronic_load_42d_equivalent", "load_balance",
                "ramp_rate_7d", "monotony_7d", "strain_7d", "days_since_intensity",
                "discipline_load_28d", "zone_minutes_28d",
            )},
            "recovery": {key: recovery.get(key) for key in ("latest", "averages_7d", "fatigue")},
            "availability_next_14d": (athlete_state.get("context") or {}).get("availability_next_14d") or [],
            "measurement_freshness": athlete_state.get("measurement_freshness") or [],
        },
        "last_four_weeks_realized": _four_weeks_realized(as_of),
        "strength_training_realized": _strength_training_evidence(as_of),
        "important_limits": [
            "Las cuatro semanas son actividad realizada desde FIT, no predicciones ni planes futuros.",
            "La fuerza Garmin perfil Ejercicios se entrega aparte porque no forma parte de la carga aerobica calculada.",
            "El motor adaptativo es determinista; esta revision solo interpreta su evidencia.",
            "No inferir causalidad ni diagnosticar a partir de una semana o de una sola sesion.",
        ],
    }


REVIEW_SYSTEM_PROMPT = """Eres un revisor fisiologico y pedagogico de una propuesta semanal para un atleta experimentado de resistencia multideporte y carreras de expedicion.

Tu papel es explicar criticamente una propuesta que YA genero un motor determinista. No debes modificarla, aprobarla, rechazarla ni inventar reglas. Diferencia siempre: dato observado, regla del motor e interpretacion fisiologica. Explica para una persona sin formacion previa: evita jerga; si usas un termino tecnico, traducelo de inmediato a palabras cotidianas y aplicadas a este caso. No repitas tablas, distancias ni cargas que ya aparecen en la UI salvo que un numero sea indispensable para sostener una conclusion.

La consistencia con las cuatro semanas anteriores es obligatoria. Esas semanas son SOLO entrenamientos realizados desde FIT: compara la semana propuesta con su volumen, frecuencia, disciplinas y distribucion de zonas recientes; no las confundas con predicciones anteriores. Considera tambien strength_training_realized como fuerza/gimnasio realizado, aun cuando no aporte carga aerobica. Ten presente que una semana distinta puede ser razonable si el estado actual, la recuperacion o la ventana de objetivos lo justifican. No uses una norma generica para corredores recreativos, no diagnostiques y no hagas recomendaciones medicas.

Responde en espanol, de forma fluida, entre 600 y 900 palabras y sin tablas, con estas secciones Markdown:
## Lectura de la semana propuesta
## Por que se activaron las reglas relevantes
## Consistencia con las ultimas cuatro semanas realizadas
## Sentido fisiologico dentro de la ventana actual
## Lo que esta bien resuelto y lo que conviene cuestionar
## Que observar al ejecutar la semana
## Incertidumbres y limites

En la seccion de reglas, explica solo las activadas o las que tuvieron efecto real. En la seccion de consistencia, indica explicitamente si hay continuidad, progresion, descarga o cambio de especificidad, y por que eso puede o no tener sentido. En la seccion final, plantea como maximo tres senales practicas para observar; no ordenes cambiar el plan."""


def request_adaptive_review(evidence: dict[str, Any]) -> str:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("No se encontro OPENAI_API_KEY. Configurala en el archivo .env del proyecto.")
    from openai import OpenAI

    input_text = "Revisa exclusivamente esta evidencia estructurada:\n```json\n" + json.dumps(
        evidence, ensure_ascii=False, indent=2, default=str
    ) + "\n```"
    response = OpenAI().responses.create(
        model="gpt-5-mini", reasoning={"effort": "medium"},
        input=[
            {"role": "system", "content": REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": input_text},
        ],
    )
    return response.output_text
