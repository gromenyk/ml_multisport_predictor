"""Evidence packet and OpenAI call for the physiological-review page."""

from __future__ import annotations

from datetime import date, timedelta
import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from athlete_state.state_builder import build_athlete_state
from config import PROJECT_ROOT


PERFORMED_LOAD_FILE = PROJECT_ROOT / "data" / "derived" / "training_load" / "performed_load.json"
SESSION_FEEDBACK_FILE = PROJECT_ROOT / "data" / "athlete_state" / "session_feedback.json"
ATHLETE_CONTEXT_FILE = PROJECT_ROOT / "data" / "athlete_state" / "athlete_context.json"
OBJECTIVES_DIR = PROJECT_ROOT / "data" / "objectives"
WINDOWS_FILE = OBJECTIVES_DIR / "calendar_windows.json"
EVENTS_FILE = OBJECTIVES_DIR / "events.json"
GOALS_FILE = OBJECTIVES_DIR / "development_goal_catalog.json"
METRICS_FILE = OBJECTIVES_DIR / "metric_catalog.json"
GARMIN_ACTIVITIES_DIR = PROJECT_ROOT / "garmin" / "activities"
load_dotenv(PROJECT_ROOT / ".env")


def _read(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _round(value: Any, digits: int = 2) -> Any:
    return round(value, digits) if isinstance(value, (int, float)) else value


def _feedback_for(activity_file: str | None) -> dict[str, Any] | None:
    """Link feedback to the normalized FIT name used in performed_load."""
    if not activity_file:
        return None
    records = _read(SESSION_FEEDBACK_FILE, {"records": []}).get("records", [])
    activity_id = str(activity_file)
    if activity_id.startswith("activity_"):
        activity_id = activity_id[len("activity_"):]
    if activity_id.endswith(".json"):
        activity_id = activity_id[:-len(".json")]
    matches = [
        item for item in records
        if item.get("activity_id") == activity_id or item.get("activity_file") == activity_file
    ]
    if not matches:
        return None
    latest = max(matches, key=lambda item: item.get("updated_at", ""))
    fields = (
        "completion_percent", "rpe", "finish_feeling", "energy_response",
        "discomfort", "temperature", "terrain_condition", "notes", "date",
    )
    result = {key: latest.get(key) for key in fields if latest.get(key) is not None}
    if result.get("notes"):
        result["notes"] = str(result["notes"]).strip()[:1000]
    return result


def _body_composition_context(as_of: date) -> dict[str, Any]:
    """Summarize only trends large enough to be worth discussing, not daily noise."""
    records = []
    for item in _read(ATHLETE_CONTEXT_FILE, {"records": []}).get("records", []):
        try:
            item_date = date.fromisoformat(str(item.get("date") or ""))
        except ValueError:
            continue
        if item_date <= as_of:
            records.append({**item, "_date": item_date})
    records.sort(key=lambda item: item["_date"])
    if not records:
        return {"relevance": "sin_datos"}

    recent_start, baseline_start = as_of - timedelta(days=6), as_of - timedelta(days=27)

    def values(field, start, end):
        return [float(item[field]) for item in records if start <= item["_date"] <= end
                and isinstance(item.get(field), (int, float)) and not isinstance(item.get(field), bool)]

    latest = records[-1]
    result = {
        "latest_date": latest["_date"].isoformat(),
        "latest_weight_kg": _round(latest.get("weight_kg"), 1),
        "latest_body_fat_percent": _round(latest.get("body_fat_percent"), 1),
        "relevance": "sin_cambio_relevante",
        "instruction": "No mencionar salvo que exista una relación fisiológica concreta con otra señal.",
    }
    signals = []
    for field, label, unit, threshold in (
        ("weight_kg", "peso", "kg", 1.0),
        ("body_fat_percent", "grasa corporal", "puntos porcentuales", 1.0),
    ):
        recent, baseline = values(field, recent_start, as_of), values(field, baseline_start, as_of - timedelta(days=7))
        if len(recent) < 3 or len(baseline) < 5:
            continue
        recent_average, baseline_average = sum(recent) / len(recent), sum(baseline) / len(baseline)
        change = recent_average - baseline_average
        if abs(change) >= threshold:
            signals.append({
                "variable": label,
                "recent_average": _round(recent_average, 1),
                "baseline_average": _round(baseline_average, 1),
                "change": _round(change, 1),
                "unit": unit,
            })
    if signals:
        result.update({
            "relevance": "tendencia_a_interpretar_con_cautela",
            "signals": signals,
            "instruction": (
                "Comentar solo si ayuda a explicar rendimiento, recuperación o demandas de la etapa. "
                "No atribuir causalidad; el peso diario y la grasa estimada pueden variar por hidratación y método."
            ),
        })
    return result


def _session_summary(item: dict[str, Any]) -> dict[str, Any]:
    activity, load, source = item.get("activity", {}), item.get("load", {}), item.get("source", {})
    zones = [
        {"zone": value.get("zone"), "minutes": _round(value.get("total_duration_minutes"), 1)}
        for value in load.get("components", []) if value.get("total_duration_minutes")
    ]
    return {
        "date": item.get("date"), "activity_file": source.get("activity_json"),
        "discipline": activity.get("sport"), "cycling_modality": activity.get("cycling_modality"),
        "profile": activity.get("garmin_profile"), "distance_km": _round(activity.get("distance_km"), 2),
        "duration_minutes": _round((activity.get("duration_seconds") or 0) / 60, 1),
        "elapsed_minutes": _round((activity.get("elapsed_duration_seconds") or 0) / 60, 1),
        "paused_minutes": _round((activity.get("paused_duration_seconds") or 0) / 60, 1),
        "ascent_m": _round(activity.get("ascent_meters"), 0), "descent_m": _round(activity.get("descent_meters"), 0),
        "calculated_load": _round(load.get("calculated_load"), 1),
        "garmin_training_load": _round(load.get("garmin_training_load"), 1),
        "terrain": (item.get("terrain_inference") or {}).get("terrain"),
        "heart_rate_zone_minutes": zones, "quality": item.get("quality", {}),
        "subjective_response": _feedback_for(source.get("activity_json")),
    }


def _is_strength_activity(activity: dict[str, Any]) -> bool:
    """Recognize Garmin's generic strength profile without treating it as cardio."""
    labels = " ".join(str(activity.get(key) or "") for key in (
        "sport", "sport_profile_name", "sub_sport",
    )).casefold()
    return any(token in labels for token in (
        "ejercicio", "strength", "fuerza", "gimnas", "gym",
    ))


def _strength_training_evidence(as_of: date) -> dict[str, Any]:
    """Summarize Garmin strength sessions kept outside the aerobic-load model."""
    sessions = []
    # Garmin files can be large; the user-facing strength profile is named
    # "Ejercicios" here, so avoid scanning every normalized FIT JSON.
    candidates = list(GARMIN_ACTIVITIES_DIR.glob("*ejercicios*.json"))
    candidates.extend(GARMIN_ACTIVITIES_DIR.glob("*strength*.json"))
    seen = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        payload = _read(path, {})
        activity = payload.get("activity", {})
        if not _is_strength_activity(activity):
            continue
        session_date = str(activity.get("date") or "")
        if not session_date or session_date > as_of.isoformat():
            continue
        summary, load = payload.get("summary", {}), payload.get("load", {})
        sessions.append({
            "date": session_date,
            "activity_file": path.name,
            "profile": activity.get("sport_profile_name") or "Ejercicios",
            "duration_minutes": _round((activity.get("duration_seconds") or 0) / 60, 1),
            "average_heart_rate_bpm": _round(summary.get("average_heart_rate_bpm"), 0),
            "garmin_training_load": _round(load.get("training_load"), 1),
        })
    sessions.sort(key=lambda item: (item["date"], item["activity_file"]), reverse=True)
    recent_28 = [item for item in sessions if item["date"] >= (as_of - timedelta(days=27)).isoformat()]
    recent_7 = [item for item in sessions if item["date"] >= (as_of - timedelta(days=6)).isoformat()]
    return {
        "classification": "Fuerza/gimnasio desde el perfil Garmin Ejercicios; no se interpreta como carga aeróbica.",
        "sessions_7d": len(recent_7),
        "sessions_28d": len(recent_28),
        "total_minutes_28d": _round(sum(item["duration_minutes"] or 0 for item in recent_28), 1),
        "last_session": sessions[0] if sessions else None,
        "recent_sessions": recent_28[:12],
        "data_limit": "El FIT confirma frecuencia y duración, pero no series, repeticiones, carga externa ni grupos musculares.",
    }


def _slim(items: list[dict[str, Any]], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    return [{key: _round(item.get(key), 3) for key in fields} for item in items]


def _planning_horizon(as_of: date) -> dict[str, Any]:
    """Resolve the manually defined training windows into analysis-ready context."""
    events = {item.get("id"): item for item in _read(EVENTS_FILE, {"events": []}).get("events", [])}
    goals = {item.get("id"): item for item in _read(GOALS_FILE, {"development_goals": []}).get("development_goals", [])}
    metrics = {item.get("id"): item for item in _read(METRICS_FILE, {"metrics": []}).get("metrics", [])}
    schedule = []
    for window in _read(WINDOWS_FILE, {"windows": []}).get("windows", []):
        if window.get("end_date", "") < as_of.isoformat():
            continue
        assignments = []
        for assignment in window.get("assignments", []):
            goal = goals.get(assignment.get("development_goal_id"), {})
            assignment_metrics = []
            for item in assignment.get("metrics", []):
                metric = metrics.get(item.get("metric_id"), {})
                assignment_metrics.append({
                    "id": item.get("metric_id"),
                    "name": metric.get("name_es") or metric.get("name"),
                    "discipline": item.get("discipline"),
                    "protocol": metric.get("what_it_measures"),
                })
            assignments.append({
                "development_goal": goal.get("name_es") or goal.get("name") or assignment.get("development_goal_id"),
                "priority": assignment.get("priority"), "metrics": assignment_metrics,
            })
        event = events.get(window.get("event_id"), {})
        schedule.append({
            "name": window.get("name"), "start_date": window.get("start_date"), "end_date": window.get("end_date"),
            "status_at_analysis": "activa" if window.get("start_date", "") <= as_of.isoformat() <= window.get("end_date", "") else "futura",
            "purpose": (window.get("description") or "")[:1800],
            "event": {"name": event.get("name"), "date": event.get("event_date"), "disciplines": event.get("disciplines", [])},
            "assignments": assignments, "discipline_weights": window.get("discipline_weights", {}),
        })
    return {"analysis_rule": "Las ventanas y sus objetivos definen el marco principal; las sesiones recientes solo describen la ejecución de corto plazo.", "windows_from_current_onward": schedule}


def build_evidence(as_of: date) -> dict[str, Any]:
    """Create compact, auditable evidence; raw FIT point streams are excluded."""
    state = build_athlete_state(as_of)
    activities = [item for item in _read(PERFORMED_LOAD_FILE, {"activities": []}).get("activities", []) if item.get("date", "") <= as_of.isoformat()]
    activities.sort(key=lambda item: (item.get("date", ""), item.get("activity_id", "")), reverse=True)
    training, recovery, context = state.get("training", {}), state.get("recovery", {}), state.get("context", {})
    metrics = _slim(list(state.get("progress", {}).values()), (
        "metric_id", "name", "discipline", "series_label", "unit", "latest", "latest_date",
        "days_since_measurement", "observations", "activity_summaries", "trend_per_30d", "confidence",
        "historical_trend_per_30d", "decision_trend_per_30d", "decision_trend_source",
        "decision_activity_summaries", "decision_confidence",
    ))
    geography = _slim(state.get("geographic_progress", {}).get("eligible_series", []), (
        "entity_type", "name", "discipline", "cycling_modality", "zone", "attempts", "latest_date",
        "current_speed_kmh", "primary_change_percent", "primary_status", "primary_reference",
    ))
    strength_training = _strength_training_evidence(as_of)
    return {
        "as_of_date": as_of.isoformat(),
        "athlete_context": {"sport_context": "Atleta experimentado de carreras de expedición y resistencia multideporte.", "next_event": context.get("next_event"), "active_windows": context.get("active_windows", [])},
        "planning_horizon": _planning_horizon(as_of),
        "last_training": _session_summary(activities[0]) if activities else None,
        "recent_trainings": {"role": "Evidencia de corto plazo: no define por sí sola la etapa ni los objetivos.", "sessions": [_session_summary(item) for item in activities[:14]]},
        "training_state": {key: training.get(key) for key in (
            "acute_load_7d", "previous_load_7d", "chronic_load_42d_equivalent", "load_balance", "ramp_rate_7d", "monotony_7d", "strain_7d", "zone_minutes_28d", "discipline_load_28d", "discipline_load_7d", "discipline_progression_28d", "cycling_modality_load_28d", "session_continuity", "running_mechanical_28d", "days_since_intensity",
        )},
        "recovery_state": {key: recovery.get(key) for key in ("latest", "averages_7d", "personal_baseline_60d", "latest_deviation", "fatigue")},
        "current_interpretation": state.get("interpretation", {}), "performance_metrics": metrics,
        "body_composition_context": _body_composition_context(as_of),
        "strength_training": strength_training,
        "geographic_speed_progress": geography, "measurement_freshness": state.get("measurement_freshness", []),
        "availability_next_14d": context.get("availability_next_14d", []),
        "data_limits": "No inferir causalidad de una sesión aislada; considerar calidad de datos, terreno, temperatura y actividades excluidas manualmente. La evidencia de fuerza no contiene detalle de ejercicios o series.",
    }


def build_session_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    """Small, session-centred context that cannot turn into a global review."""
    last_training = evidence.get("last_training") or {}
    discipline = last_training.get("discipline")
    horizon = evidence.get("planning_horizon", {}).get("windows_from_current_onward", [])
    relevant_metrics = [
        item for item in evidence.get("performance_metrics", [])
        if item.get("discipline") in {None, "", discipline}
    ]
    relevant_geography = [
        item for item in evidence.get("geographic_speed_progress", [])
        if item.get("discipline") == discipline
    ]
    return {
        "as_of_date": evidence.get("as_of_date"), "last_training": last_training,
        "current_window": next((item for item in horizon if item.get("status_at_analysis") == "activa"), None),
        "load_and_recovery_context": {
            "training": {key: evidence.get("training_state", {}).get(key) for key in ("acute_load_7d", "chronic_load_42d_equivalent", "load_balance", "days_since_intensity")},
            "recovery": evidence.get("recovery_state", {}),
        },
        "relevant_performance_evidence": relevant_metrics,
        "relevant_geographic_evidence": relevant_geography,
        "body_composition_context": evidence.get("body_composition_context", {}),
        "strength_training": evidence.get("strength_training", {}),
        "data_limits": evidence.get("data_limits"),
    }


GLOBAL_SYSTEM_PROMPT = """Eres un analista fisiológico de resistencia multideporte. El atleta compite en carreras de expedición, tiene una larga historia de entrenamiento y no debe compararse con un corredor recreativo de 10 km.

No repitas el dashboard ni conviertas la respuesta en un inventario de números. La persona ya puede ver carga, distancia, FC y métricas en otras páginas: usa cifras solo cuando cambien materialmente la interpretación, normalmente una o dos por sección como máximo. La jerarquía del análisis es obligatoria: (1) ventana activa y ventanas futuras, con sus objetivos y métricas; (2) demandas del evento; (3) estado actual; (4) progreso de capacidades; (5) últimas sesiones, que solo aportan contexto inmediato. Evalúa relaciones entre carga, distribución de intensidad, especificidad, progresión, desnivel/terreno, recuperación, tolerancia individual y rendimiento comparable. Lee los comentarios libres de la respuesta subjetiva como contexto cualitativo: úsalos si explican una señal (calor, nutrición, dolor, estrés, equipo, compañía o terreno), pero no los listes ni los trates como hechos fisiológicos confirmados. Considera body_composition_context únicamente si su relevancia indica una tendencia; no menciones peso ni grasa corporal por rutina, y trata cambios cortos con cautela.

Escribe como un buen fisiólogo que explica su razonamiento a un atleta curioso, sin asumir formación previa: prosa fluida, concreta y educativa. Usa lenguaje cotidiano primero. Evita jerga como «economía submáxima», «adaptación mitocondrial», «umbral anaeróbico», «deriva cardíaca» o «capacidad oxidativa» salvo que sea indispensable; si la usas, defínela inmediatamente con palabras sencillas en la misma frase y explica por qué importa aquí. Por ejemplo, en vez de «mejora de la economía submáxima», di «ayuda a mantener un ritmo cómodo usando menos esfuerzo»; en vez de «umbral anaeróbico», di «la capacidad de sostener un ritmo duro sin que el cansancio se dispare». Nunca encadenes tecnicismos ni des una definición de manual. Consulta siempre strength_training: las sesiones Garmin con perfil Ejercicios son fuerza/gimnasio ya realizado, aunque su carga aeróbica sea baja. No recomiendes añadir fuerza si existe una práctica regular; solo señala la incertidumbre sobre su dosis o progresión porque el FIT no incluye series ni cargas. La carga excéntrica aplica principalmente a carrera, trail, trekking/descenso a pie o fuerza; no la atribuyas a MTB solo por tener desnivel. Usa principios fisiológicos consolidados como marco, sin inventar citas o estudios. Distingue con naturalidad entre dato observado, inferencia razonable e incertidumbre. No diagnostiques enfermedades. Una carga alta no es alerta por sí misma: interprétala contra la historia individual, recuperación, respuesta subjetiva, objetivo y rendimiento comparable. No propongas modificaciones automáticas al plan.

Responde en español, idealmente entre 600 y 900 palabras, con estas secciones Markdown y sin tablas: ## Lectura general; ## Qué significa para esta etapa; ## Cómo está respondiendo el atleta; ## Una idea para entender mejor; ## Qué observar durante los próximos días; ## Alertas, matices e incertidumbres. Cada sección debe contener uno o dos párrafos legibles. En «Qué observar» prioriza como máximo tres señales realmente útiles, explicando qué sugeriría cada una; incluye el protocolo o la fecha de medición solo si aporta una decisión concreta. Si no hay una alerta relevante, dilo de forma breve y sigue con la interpretación."""


SESSION_SYSTEM_PROMPT = """Eres un analista fisiológico de resistencia multideporte. Analiza exclusivamente UNA sesión recién realizada dentro de la ventana activa del atleta, sin transformarla en una evaluación global de toda la preparación y sin repetir tablas de datos.

Evalúa el estímulo real, la distribución de intensidad, la continuidad/pausas, la respuesta subjetiva, la coherencia con los objetivos de la ventana y las señales inmediatas para recuperación o seguimiento. Lee el comentario libre de la sesión: úsalo cuando cambie la interpretación (por ejemplo, calor, molestias, alimentación, estrés o condiciones inusuales), sin convertirlo en diagnóstico ni repetirlo textualmente. Considera body_composition_context solo si marca una tendencia relevante y si tiene relación plausible con la sesión; no menciones peso o grasa corporal como rutina. No repitas la distancia, carga, FC o zonas que ya se ven en el detalle de la actividad: menciona solamente los datos necesarios para sostener una conclusión. Escribe para alguien sin formación de fisiología: evita tecnicismos; si alguno es realmente útil, tradúcelo al instante a una frase cotidiana y aplicada a esta sesión. Consulta strength_training como contexto: el perfil Garmin Ejercicios representa fuerza/gimnasio, no una ausencia de fuerza ni una sesión aeróbica suave. Diferencia siempre la disciplina: la carga excéntrica es relevante principalmente en carrera, trail, trekking/descenso a pie o fuerza. No la atribuyas a MTB solo por el desnivel; en MTB pueden existir demandas técnicas y musculares, pero no son equivalentes a impacto excéntrico de correr o bajar caminando. Señala errores de calidad de datos si la evidencia los sugiere. No diagnostiques, no cambies el plan y no hagas una revisión exhaustiva del estado global.

Responde en español, idealmente entre 350 y 550 palabras, en tono conversacional, con estas secciones Markdown y sin tablas: ## Lectura de la sesión; ## Qué estímulo dejó; ## Por qué importa ahora; ## Qué aprender de esta sesión; ## Próximo paso y señales a vigilar; ## Límites de la interpretación. Escribe uno o dos párrafos por sección. En «Qué aprender» explica un único concepto fisiológico pertinente aplicado a esta actividad. En «Próximo paso» no des una orden de planificación: plantea qué observar, qué confirmaría la lectura y qué haría prudente revisarla."""


def _request(prompt: str, evidence: dict[str, Any]) -> str:
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("No se encontró OPENAI_API_KEY. Configúrala en el archivo .env del proyecto.")
    from openai import OpenAI
    input_text = f"Analiza exclusivamente esta evidencia estructurada:\n```json\n{json.dumps(evidence, ensure_ascii=False, indent=2, default=str)}\n```"
    response = OpenAI().responses.create(
        model="gpt-5-mini", reasoning={"effort": "medium"},
        input=[{"role": "system", "content": prompt}, {"role": "user", "content": input_text}],
    )
    return response.output_text


def request_session_analysis(evidence: dict[str, Any]) -> str:
    return _request(SESSION_SYSTEM_PROMPT, build_session_evidence(evidence))


def request_global_analysis(evidence: dict[str, Any]) -> str:
    return _request(GLOBAL_SYSTEM_PROMPT, evidence)
