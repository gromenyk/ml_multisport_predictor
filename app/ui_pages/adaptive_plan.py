"""Five-step, explainable adaptive planning workflow."""

from __future__ import annotations

from datetime import date, timedelta
from copy import deepcopy
import json

import pandas as pd
import streamlit as st

from adaptive_planning.engine import build_adaptive_proposal, _estimated_session_details
from adaptive_planning.manual_plan_store import load_manual_plan
from adaptive_planning.store import save_decision
from adaptive_planning.workflow_store import (
    load_active_plans,
    load_published_plans,
    publish_plan,
    revert_plan,
)
from athlete_state.state_builder import build_athlete_state
from backend.adaptive_plan_review import (
    build_adaptive_review_evidence,
    request_adaptive_review,
)
from config import PROJECT_ROOT
from backend.ui_translations import display_es


PREDICTIONS_DIR = PROJECT_ROOT / "ML" / "results" / "predictions"
PREDICTED_LOAD_FILE = PROJECT_ROOT / "data" / "derived" / "training_load" / "predicted_load.json"
GOALS_FILE = PROJECT_ROOT / "data" / "objectives" / "development_goal_catalog.json"
METRICS_FILE = PROJECT_ROOT / "data" / "objectives" / "metric_catalog.json"


def _json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except (OSError, json.JSONDecodeError):
        return default


def _prediction_entries():
    loads = {
        item.get("prediction_id"): item
        for item in _json(PREDICTED_LOAD_FILE, {"predictions": []}).get("predictions", [])
    }
    entries = []
    for path in PREDICTIONS_DIR.glob("prediction_*.json"):
        prediction = _json(path, {})
        if not prediction.get("week_start"):
            continue
        entries.append({
            "id": path.stem,
            "prediction": prediction,
            "load": loads.get(path.stem, {}),
        })
    return sorted(entries, key=lambda item: (item["prediction"].get("week_start", ""), item["prediction"].get("generated_at", "")), reverse=True)


def _compact_number(value, decimals=1):
    if not isinstance(value, (int, float)):
        return "—" if value is None else str(value)
    rounded = round(float(value), decimals)
    return str(int(rounded)) if rounded.is_integer() else f"{rounded:.{decimals}f}"


def _append_manual_additional_sessions(proposal, additional_sessions):
    """Add protected manual sessions after the engine adapts model-owned sessions."""
    extras = deepcopy(additional_sessions)
    for session in extras:
        try:
            details = _estimated_session_details(session)
            session["estimated_duration_minutes"] = round(details["duration_minutes"], 1)
            session["estimated_load"] = round(details["total_load"], 1)
        except (KeyError, TypeError, ValueError):
            session["estimated_duration_minutes"] = None
            session["estimated_load"] = None
    for key in ("base_sessions", "adapted_sessions"):
        proposal[key] = sorted(proposal.get(key, []) + deepcopy(extras), key=lambda item: (item.get("date", ""), item.get("session_id", "")))
    return proposal


def _session_rows(sessions):
    return [{
        "Fecha": item.get("date"),
        "Día": display_es(item.get("weekday")),
        "Deporte": display_es(item.get("sport")),
        "Zona": item.get("zone") or "—",
        "Terreno": display_es(item.get("terrain")) or "—",
        "Distancia (km)": _compact_number(item.get("distance_km")),
        "Duración est. (min)": _compact_number(item.get("estimated_duration_minutes") or item.get("duration_min"), 0),
        "Carga est.": item.get("estimated_load"),
        "Entrenamiento": display_es(item.get("template")) or "—",
    } for item in sessions]


def _week_summary(entry):
    prediction = entry["prediction"]
    sessions = prediction.get("sessions", [])
    active = [item for item in sessions if item.get("sport") != "Descanso"]
    distances = {}
    for item in active:
        sport = item.get("sport", "Desconocido")
        distances[sport] = distances.get(sport, 0.0) + float(item.get("distance_km") or 0)
    daily = entry.get("load", {}).get("daily_loads", {})
    load = sum(float(item.get("predicted_load") or 0) for item in daily.values())
    intensity = sum(item.get("zone") in {"Z4", "Z5", "Z6"} for item in active)
    return {"sessions": len(active), "load": load, "distances": distances, "intensity_sessions": intensity}


def _session_summary(sessions):
    active = [item for item in sessions if item.get("sport") != "Descanso"]
    return {
        "sessions": len(active),
        "distance": sum(float(item.get("distance_km") or 0) for item in active),
        "load": sum(
            float(item.get("estimated_load") or 0) for item in active
            if item.get("estimated_load") is not None
        ),
        "intensity_sessions": sum(item.get("zone") in {"Z4", "Z5", "Z6"} for item in active),
    }


def _planned_week_summary(sessions):
    active = [item for item in sessions if item.get("sport") != "Descanso"]
    load = 0.0
    for item in active:
        try:
            load += _estimated_session_details(item)["total_load"]
        except (KeyError, TypeError, ValueError):
            pass
    distances = {}
    for item in active:
        sport = item.get("sport", "Desconocido")
        distances[sport] = distances.get(sport, 0.0) + float(item.get("distance_km") or 0)
    return {"sessions": len(active), "load": load, "distances": distances,
            "intensity_sessions": sum(item.get("zone") in {"Z4", "Z5", "Z6"} for item in active)}


def _clip(value, lower, upper):
    return max(lower, min(upper, value))


def _recovery_detail_rows(recovery):
    latest = recovery.get("latest") or {}
    average = recovery.get("averages_7d") or {}
    baseline = recovery.get("personal_baseline_60d") or {}
    deviation = recovery.get("weekly_deviation") or {}
    definitions = [
        ("Duración del sueño", "sleep_duration_hours", "h", "desviación × 10; límite ±15", lambda value: _clip(value * 10, -15, 15)),
        ("VFC", "hrv_ms", "ms", "desviación × 0,8; límite ±12", lambda value: _clip(value * .8, -12, 12)),
        ("FC en reposo", "resting_hr_bpm", "bpm", "−desviación × 2; límite ±10", lambda value: -_clip(value * 2, -10, 10)),
    ]
    rows = []
    for label, key, unit, formula, contribution in definitions:
        delta = deviation.get(key)
        rows.append({
            "Indicador": label,
            "Último": f"{latest.get(key):.1f} {unit}" if isinstance(latest.get(key), (int, float)) else "—",
            "Promedio 7 d": f"{average.get(key):.1f} {unit}" if isinstance(average.get(key), (int, float)) else "—",
            "Base 60 d": f"{baseline.get(key):.1f} {unit}" if isinstance(baseline.get(key), (int, float)) else "—",
            "Desviación usada (7 d)": f"{delta:+.1f} {unit}" if isinstance(delta, (int, float)) else "—",
            "Fórmula": formula,
            "Aporte": f"{contribution(delta):+.1f} puntos" if isinstance(delta, (int, float)) else "No disponible",
        })
    fatigue = latest.get("subjective_fatigue")
    muscular_fatigue = latest.get("muscle_fatigue", latest.get("muscle_soreness"))
    planning_fatigue = average.get("subjective_fatigue")
    planning_muscular_fatigue = average.get("muscle_fatigue")
    rows.extend([
        {"Indicador": "Fatiga general", "Último": f"{fatigue:.0f}/10" if isinstance(fatigue, (int, float)) else "—",
         "Promedio 7 d": f"{average.get('subjective_fatigue'):.1f}/10" if isinstance(average.get("subjective_fatigue"), (int, float)) else "—", "Base 60 d": "No utilizada", "Desviación usada (7 d)": "Promedio 7 d",
         "Fórmula": "(5 − fatiga media) × 4", "Aporte": f"{(5-planning_fatigue)*4:+.1f} puntos" if isinstance(planning_fatigue, (int, float)) else "No disponible"},
        {"Indicador": "Fatiga muscular", "Último": f"{muscular_fatigue:.0f}/10" if isinstance(muscular_fatigue, (int, float)) else "—",
         "Promedio 7 d": f"{average.get('muscle_fatigue'):.1f}/10" if isinstance(average.get("muscle_fatigue"), (int, float)) else "—", "Base 60 d": "No utilizada", "Desviación usada (7 d)": "Promedio 7 d",
         "Fórmula": "(4 − fatiga muscular media) × 3", "Aporte": f"{(4-planning_muscular_fatigue)*3:+.1f} puntos" if isinstance(planning_muscular_fatigue, (int, float)) else "No disponible"},
        {"Indicador": "Molestia declarada", "Último": "Sí" if latest.get("has_discomfort") else "No",
         "Promedio 7 d": "No aplica", "Base 60 d": "No aplica", "Desviación usada": "Estado actual",
         "Fórmula": "−20 si existe", "Aporte": "−20,0 puntos" if latest.get("has_discomfort") else "+0,0 puntos"},
    ])
    return rows


def _step_header(number, title, status):
    status_label = {
        "complete": "Completo",
        "active": "En curso",
        "locked": "Bloqueado",
        "pending": "Pendiente",
    }.get(status, "Pendiente")
    st.markdown(f"## {number}. {title}")
    st.caption(status_label.upper())


def _rerun():
    """Support both recent and older Streamlit releases."""
    rerun = getattr(st, "rerun", None) or getattr(st, "experimental_rerun")
    rerun()


def _render_approved_weeks(entries):
    plans = load_published_plans()
    st.markdown("### Semanas adaptadas y aprobadas")
    st.caption("Consulta el plan definitivo publicado en calendario y sus versiones anteriores.")
    if not plans:
        st.info("Todavía no hay semanas aprobadas y publicadas.")
        return
    by_week = {}
    for plan in plans:
        by_week.setdefault(plan["week_start"], []).append(plan)
    weeks = sorted(by_week, reverse=True)
    selected_week = st.selectbox(
        "Semana aprobada",
        tuple(weeks),
        format_func=lambda value: (
            f"{date.fromisoformat(value).strftime('%d/%m/%Y')}–"
            f"{date.fromisoformat(by_week[value][0]['week_end']).strftime('%d/%m/%Y')}"
        ),
        key="approved_week_history_selector",
    )
    versions = by_week[selected_week]
    active = next((item for item in versions if item.get("status") == "active"), versions[0])
    status_labels = {"active": "Activa", "superseded": "Reemplazada", "reverted": "Revertida"}
    columns = st.columns(4)
    columns[0].metric("Versión vigente", active.get("version", "—"))
    columns[1].metric("Estado", status_labels.get(active.get("status"), active.get("status", "—")))
    columns[2].metric("Carga estimada", f"{float(active.get('estimated_load') or 0):.1f}")
    columns[3].metric("Sesiones", sum(item.get("sport") != "Descanso" for item in active.get("sessions", [])))
    st.dataframe(
        pd.DataFrame(_session_rows(active.get("sessions", []))),
        use_container_width=True, hide_index=True,
    )

    base_entry = next((item for item in entries if item["id"] == active.get("prediction_id")), None)
    if base_entry:
        base_by_date = {item["date"]: item for item in base_entry["prediction"].get("sessions", [])}
        fields = ("sport", "terrain", "zone", "distance_km", "template")
        changed = sum(
            any(item.get(field) != base_by_date.get(item["date"], {}).get(field) for field in fields)
            for item in active.get("sessions", [])
        )
        st.caption(
            f"Origen: predicción base {active.get('prediction_id')} · "
            f"{changed} día(s) modificados respecto de la base."
        )
    else:
        st.caption(f"Origen: predicción base {active.get('prediction_id', '—')}.")

    with st.expander(f"Historial de versiones · {len(versions)}"):
        st.dataframe(pd.DataFrame([{
            "Versión": item.get("version"),
            "Estado": status_labels.get(item.get("status"), item.get("status")),
            "Publicada": item.get("published_at"),
            "Carga": item.get("estimated_load"),
            "Motor": item.get("engine_version"),
            "Comentario": item.get("comment") or "—",
        } for item in versions]), use_container_width=True, hide_index=True)


def render(embedded=False):

    if embedded:
        st.header("2 · Adaptar y definir el plan definitivo")
    st.write(
        "Flujo explicable para convertir una predicción base en una semana contextualizada. "
        "El motor propone cambios reversibles, pero no publica nada sin aprobación humana."
    )
    entries = _prediction_entries()
    _render_approved_weeks(entries)
    st.divider()
    st.markdown("### Crear o revisar una propuesta adaptativa")
    if not entries:
        st.info("Primero genera una semana base en el paso 1 de Planificación.")
        return
    by_id = {item["id"]: item for item in entries}
    selected_id = st.session_state.get("adaptive_base_prediction_id")
    if selected_id not in by_id:
        selected_id = entries[0]["id"]

    base_fixed = st.session_state.get("adaptive_base_fixed") == selected_id
    proposal = st.session_state.get("adaptive_proposal") if base_fixed else None
    if proposal and proposal.get("engine_version") != "adaptive_engine_v23":
        st.session_state.pop("adaptive_proposal", None)
        st.session_state.pop("adaptive_decision", None)
        proposal = None

    status_columns = st.columns(5)
    labels = [
        ("1", "Semana base", base_fixed),
        ("2", "Contexto", base_fixed),
        ("3", "Propuesta", proposal is not None),
        ("4", "Comparación", proposal is not None),
        ("5", "Aprobación", st.session_state.get("adaptive_decision") is not None),
    ]
    for column, (number, label, done) in zip(status_columns, labels):
        column.markdown(f"**{number} · {label}**  \n{'Completo' if done else 'Pendiente'}")
    st.divider()

    _step_header(1, "Semana base", "complete" if base_fixed else "active")
    chosen = st.selectbox(
        "Predicción archivada",
        tuple(by_id),
        index=tuple(by_id).index(selected_id),
        format_func=lambda item: (
            f"{by_id[item]['prediction']['week_start']} a {by_id[item]['prediction']['week_end']} · "
            f"generada {by_id[item]['prediction'].get('generated_at', '—')}"
        ),
        key="adaptive_prediction_selector",
        disabled=base_fixed,
    )
    entry = by_id[chosen]
    prediction = entry["prediction"]
    manual_plan = load_manual_plan(chosen)
    manual_sessions = manual_plan.get("sessions", prediction.get("sessions", [])) if manual_plan else prediction.get("sessions", [])
    for item in manual_sessions:
        if item.get("manual_origin") and not item.get("terrain"):
            item["terrain"] = "Ondulado" if item.get("sport") == "Trail" else "Plano" if item.get("sport") in {"Corrida", "Bici"} else None
    manual_additional_sessions = [item for item in manual_sessions if item.get("manual_additional")]
    fixed_manual_load = 0.0
    for item in manual_additional_sessions:
        try:
            fixed_manual_load += _estimated_session_details(item)["total_load"]
        except (KeyError, TypeError, ValueError):
            pass
    adaptation_prediction = {
        **prediction,
        "sessions": [item for item in manual_sessions if not item.get("manual_additional")],
        "fixed_manual_load": fixed_manual_load,
    }
    summary = _planned_week_summary(manual_sessions)
    base_columns = st.columns(4)
    base_columns[0].metric("Sesiones", summary["sessions"])
    base_columns[1].metric("Carga prevista", f"{summary['load']:.1f}")
    base_columns[2].metric("Sesiones Z4–Z6", summary["intensity_sessions"])
    base_columns[3].metric("Distancia", " · ".join(f"{key} {value:.0f} km" for key, value in summary["distances"].items()) or "—")
    st.dataframe(pd.DataFrame(_session_rows(manual_sessions)), use_container_width=True, hide_index=True)
    if manual_plan:
        protected_count = sum(bool(item.get("manual_protected")) for item in manual_sessions)
        st.info(f"La adaptación partirá de tu versión manual guardada · sesiones protegidas: {protected_count}.")
    action_columns = st.columns([2, 2, 5])
    if not base_fixed and action_columns[0].button("Fijar como semana base", use_container_width=True):
        st.session_state["adaptive_base_prediction_id"] = chosen
        st.session_state["adaptive_base_fixed"] = chosen
        st.session_state.pop("adaptive_proposal", None)
        st.session_state.pop("adaptive_decision", None)
        st.session_state.pop("adaptive_plan_review", None)
        _rerun()
    if base_fixed and action_columns[0].button("Cambiar semana base", use_container_width=True):
        st.session_state.pop("adaptive_base_fixed", None)
        st.session_state.pop("adaptive_proposal", None)
        st.session_state.pop("adaptive_decision", None)
        st.session_state.pop("adaptive_plan_review", None)
        _rerun()
    if not base_fixed:
        st.info("Fija una semana base para habilitar los pasos siguientes.")
        return

    week_start = date.fromisoformat(prediction["week_start"])
    context_date = min(date.today(), week_start - timedelta(days=1))
    state = build_athlete_state(context_date)
    interpretation = state["interpretation"]
    training = state["training"]
    recovery = state["recovery"]
    continuity = training.get("session_continuity", {})

    st.divider()
    _step_header(2, "Contexto utilizado", "complete")
    st.caption(f"Estado del atleta a fecha de {state['as_of_date']} · generado automáticamente con las fuentes disponibles.")
    final_cutoff = week_start - timedelta(days=1)
    if context_date < final_cutoff:
        st.warning(
            f"Análisis preliminar: faltan los datos entre {context_date + timedelta(days=1)} y {final_cutoff}. "
            "Vuelve a analizar al cerrar la semana actual antes de publicar."
        )
    else:
        st.success(f"Análisis final: el contexto alcanza el cierre previo del {final_cutoff}.")
    context_columns = st.columns(4)
    context_columns[0].metric("Preparación", interpretation.get("readiness_label", "—").capitalize())
    context_columns[1].metric("Confianza", interpretation.get("confidence", "—").capitalize())
    context_columns[2].metric("Balance de carga", f"{training.get('load_balance', 0):+.1f}")
    recent_pause = continuity.get("recent_7d", {}).get("paused_time_share")
    context_columns[3].metric("Tiempo pausado · 7 d", f"{100 * recent_pause:.1f}%" if recent_pause is not None else "—")
    context_tabs = st.tabs(["Carga", "Recuperación", "Progreso", "Disponibilidad"])
    with context_tabs[0]:
        st.write(
            f"Carga aguda: **{training.get('acute_load_7d', 0):.1f}** · "
            f"Carga crónica equivalente: **{training.get('chronic_load_42d_equivalent', 0):.1f}** · "
            f"Días desde intensidad: **{training.get('days_since_intensity', '—')}**"
        )
    with context_tabs[1]:
        latest = recovery.get("latest") or {}
        st.write(
            f"Sueño: **{latest.get('sleep_duration_hours', '—')} h** · "
            f"VFC: **{latest.get('hrv_ms', '—')} ms** · "
            f"FC reposo: **{latest.get('resting_hr_bpm', '—')} bpm** · "
            f"Fatiga general: **{latest.get('subjective_fatigue', '—')}** · "
            f"Fatiga muscular: **{latest.get('muscle_fatigue', '—')}**"
        )
        st.markdown("**Cómo se construye la señal de recuperación**")
        st.dataframe(pd.DataFrame(_recovery_detail_rows(recovery)), use_container_width=True, hide_index=True)
        st.caption(
            "El puntaje semanal usa los promedios de sueño, VFC y FC en reposo de los últimos 7 días "
            "comparados con tu línea base personal de 60 días. La última noche se muestra como contexto diario "
            "y no activa por sí sola una reducción de la semana."
        )
        sleep_score = latest.get("sleep_score")
        sleep_score_7d = recovery.get("averages_7d", {}).get("sleep_score")
        sleep_score_60d = recovery.get("personal_baseline_60d", {}).get("sleep_score")
        st.info(
            "Puntuación de sueño Garmin: "
            f"última {sleep_score if sleep_score is not None else '—'} · "
            f"promedio 7 d {f'{sleep_score_7d:.1f}' if isinstance(sleep_score_7d, (int, float)) else '—'} · "
            f"base 60 d {f'{sleep_score_60d:.1f}' if isinstance(sleep_score_60d, (int, float)) else '—'}. "
            "Actualmente es informativa y no aporta puntos al indicador."
        )
    with context_tabs[2]:
        due = state.get("measurement_freshness", [])
        st.dataframe(pd.DataFrame([{
            "Métrica": item["metric_name"], "Serie": item["series_label"],
            "Estado": f"{item['status_icon']} {item['status']}",
            "Última": item["latest_date"] or "—", "Objetivo": item["target_date"] or "Evaluación inicial",
        } for item in due]), use_container_width=True, hide_index=True)
    with context_tabs[3]:
        availability = state["context"].get("availability_next_14d", [])
        st.dataframe(pd.DataFrame([{
            "Fecha": item["date"],
            "Disponibilidad": "Libre" if item["status"] == "free" else "No disponible" if item["status"] == "unavailable" else "Limitada",
            "Máximo": item.get("max_minutes") if item.get("max_minutes") is not None else "—",
        } for item in availability if prediction["week_start"] <= item["date"] <= prediction["week_end"]]), use_container_width=True, hide_index=True)
    with st.expander("Evidencia completa utilizada"):
        for item in interpretation.get("positive_factors", []):
            st.success(item)
        for item in interpretation.get("limiting_factors", []):
            st.warning(item)

    st.divider()
    _step_header(3, "Decisión adaptativa", "complete" if proposal else "active")
    goal_payload = _json(GOALS_FILE, {"development_goals": []})
    goal_names = {item["id"]: item.get("name_es") or item.get("name") for item in goal_payload.get("development_goals", [])}
    metric_catalog = _json(METRICS_FILE, {"metrics": []}).get("metrics", [])
    if st.button("Analizar Estado del atleta y proponer ajustes", use_container_width=True):
        proposal_value = build_adaptive_proposal(adaptation_prediction, state, goal_names, metric_catalog)
        st.session_state["adaptive_proposal"] = _append_manual_additional_sessions(
            proposal_value, manual_additional_sessions
        )
        st.session_state.pop("adaptive_decision", None)
        st.session_state.pop("adaptive_plan_review", None)
        _rerun()
    proposal = st.session_state.get("adaptive_proposal")
    if not proposal:
        st.info("El motor evaluará todas las reglas y mostrará tanto las activadas como las descartadas.")
        return
    decision_columns = st.columns(3)
    decision_columns[0].metric("Decisión", proposal["decision"])
    decision_columns[1].metric("Confianza", proposal["confidence"].capitalize())
    decision_columns[2].metric("Cambios automáticos", len(proposal["changes"]))
    st.info(proposal["recommended_action"])
    st.caption(f"Motor: {proposal['engine_version']} · {proposal['note']}")

    st.markdown("### Revisión fisiológica asistida")
    st.caption(
        "Explica la propuesta ya calculada, sus reglas y su consistencia con las cuatro semanas realizadas. "
        "No modifica ni aprueba el plan."
    )
    review_key = f"{chosen}:{proposal.get('engine_version')}:{state.get('as_of_date')}"
    if st.button("Revisar fundamento fisiológico de la propuesta", use_container_width=True):
        evidence = build_adaptive_review_evidence(prediction, proposal, state)
        with st.spinner("Revisando las reglas y la coherencia fisiológica de la semana..."):
            try:
                st.session_state["adaptive_plan_review"] = {
                    "key": review_key,
                    "text": request_adaptive_review(evidence),
                    "evidence": evidence,
                }
            except Exception as error:
                st.error(f"No fue posible generar la revisión: {error}")
    review = st.session_state.get("adaptive_plan_review")
    if review and review.get("key") == review_key:
        st.markdown(review["text"])
        with st.expander("Ver evidencia utilizada en la revisión"):
            st.json(review["evidence"], expanded=False)

    carry_in = proposal.get("intensity_carry_in") or []
    if carry_in:
        st.caption(
            "Intensidad previa considerada: "
            + " · ".join(
                f"{item['date']}: {item['sport']} {item['zone']} ({item['source']})"
                for item in carry_in
            )
        )
    geographic_signals = proposal.get("geographic_signals") or {}
    if geographic_signals:
        summary = " · ".join(
            f"{discipline}: {signal['status']} ({signal['change_percent']:+.1f}%)"
            + ("; hay una serie con pérdida clara" if signal.get("has_clear_decline") else "")
            for discipline, signal in geographic_signals.items()
        )
        st.caption(f"Rutas y segmentos comparables: {summary}.")
        with st.expander("Evidencia de rutas y segmentos utilizada"):
            rows = []
            for discipline, signal in geographic_signals.items():
                for item in signal.get("series", []):
                    rows.append({
                        "Disciplina": discipline,
                        "Tipo": "Ruta" if item.get("type") == "route" else "Segmento",
                        "Nombre": item.get("name") or "—",
                        "Zona FC": item.get("zone") or "—",
                        "Cambio": f"{item.get('change_percent', 0):+.1f}%",
                        "Referencia": item.get("reference") or "—",
                        "Intentos": item.get("attempts") or "—",
                    })
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    weights = proposal.get("discipline_weights") or {}
    selected_recipe = proposal.get("selected_recipe") or {}
    if weights or selected_recipe:
        direction_columns = st.columns([1, 2])
        direction_columns[0].markdown("**Énfasis de la ventana**")
        direction_columns[0].write(
            " · ".join(f"{key} {value:.0f}%" for key, value in weights.items())
            or "Distribución equilibrada"
        )
        direction_columns[1].markdown("**Receta principal**")
        direction_columns[1].write(selected_recipe.get("goal", "Sin receta aplicable"))
        direction_columns[1].caption(
            f"{selected_recipe.get('reason', '—').capitalize()} · "
            f"{selected_recipe.get('metric_name') or 'métricas asignadas'} · "
            f"{selected_recipe.get('summary', '')}"
        )
        with st.expander("Por qué se eligió esta receta"):
            signals = proposal.get("recipe_signals", [])
            if signals:
                st.dataframe(pd.DataFrame([{
                    "Objetivo": item["goal"],
                    "Disciplina": item.get("discipline") or "—",
                    "Métrica guía": item.get("metric_name") or "—",
                    "Motivo": item["reason"],
                    "Prioridad del objetivo": item.get("assignment_priority") or "—",
                    "Puntaje": item["score"],
                    "Seleccionada": "Sí" if item is signals[0] else "No",
                } for item in signals]), use_container_width=True, hide_index=True)
    if proposal.get("optimization_candidates"):
        st.markdown("**Alternativas semanales evaluadas**")
        st.dataframe(pd.DataFrame([{
            "Alternativa": item["name"],
            "Carga estimada": item["estimated_load"],
            "Carga objetivo": item["target_load"],
            "Carga resultante": " · ".join(f"{key} {value:.0f}%" for key, value in item["distribution"].items()),
            "Receta": "Aplicada" if item["recipe_applied"] else "En espera",
            "Plantilla": (
                display_es(item.get("template_selection", {}).get("selected", {}).get("signature", "—"))
                if item.get("template_selection") else "—"
            ),
            "Cambios": item["changes_count"],
            "Puntaje": item["score"],
            "Selección": "Seleccionada" if item["selected"] else "Descartada",
        } for item in proposal["optimization_candidates"]]), use_container_width=True, hide_index=True)
        st.caption(
            "El puntaje compara cercanía al énfasis declarado, carga compatible, "
            "objetivo atendido, separación de intensidad y magnitud de los cambios. "
            "La carga resultante es informativa: no intenta igualarse a los pesos de influencia."
        )
        selected_alternative = next(
            (item for item in proposal["optimization_candidates"] if item["selected"]),
            None,
        )
        memory_evidence = (
            selected_alternative.get("progression_memory", {})
            if selected_alternative else {}
        )
        if memory_evidence:
            st.markdown("**Memoria longitudinal utilizada**")
            latest = memory_evidence.get("latest_evaluated") or {}
            reference = memory_evidence.get("longest_well_tolerated") or {}
            memory_columns = st.columns(4)
            memory_columns[0].metric("Disciplina", display_es(memory_evidence.get("discipline")) or "—")
            memory_columns[1].metric("Decisión", display_es(memory_evidence.get("action", "—")))
            memory_columns[2].metric(
                "Referencia tolerada",
                "—" if reference.get("distance_km") is None else f"{reference['distance_km']:.1f} km",
            )
            memory_columns[3].metric("Racha positiva", memory_evidence.get("well_tolerated_streak", 0))
            st.write(memory_evidence.get("result", "Sin cambio longitudinal."))
            if memory_evidence.get("added_session"):
                st.success(f"Sesión adicional: {memory_evidence['added_session']}")
            st.caption(
                f"Última sesión evaluable: {latest.get('date', '—')} · "
                f"respuesta: {display_es(latest.get('status', '—'))}."
            )
        template_selection = (
            selected_alternative.get("template_selection")
            if selected_alternative else None
        )
        if template_selection:
            selected_template = template_selection["selected"]
            st.markdown("**Plantilla histórica seleccionada**")
            st.write(display_es(selected_template["signature"]))
            st.caption(
                f"Clase: {selected_template.get('objective_class') or '—'} · "
                f"Duración estimada: {selected_template.get('duration_minutes', 0):.0f} min · "
                f"Carga estimada: {selected_template.get('estimated_load', 0):.1f} · "
                f"Apariciones históricas: {selected_template.get('count', 0)}"
            )
            with st.expander("Otras plantillas compatibles evaluadas"):
                st.dataframe(pd.DataFrame([{
                    "Plantilla": display_es(item["signature"]),
                    "Clase": item.get("objective_class") or "—",
                    "Duración (min)": item.get("duration_minutes"),
                    "Carga": item.get("estimated_load"),
                    "Usos históricos": item.get("count"),
                    "Puntaje": item.get("score"),
                } for item in template_selection.get("alternatives", [])]),
                    use_container_width=True, hide_index=True)
    st.markdown("**Auditoría de reglas**")
    st.dataframe(pd.DataFrame([{
        "Regla": f"{item['rule_id']} · {item['name']}",
        "Dato observado": item["observed"],
        "Umbral": item["threshold"],
        "Resultado": "Activada" if item["triggered"] else "No activada",
        "Consecuencia": item["consequence"],
    } for item in proposal["rule_evaluations"]]), use_container_width=True, hide_index=True)
    with st.expander("Límites de seguridad aplicados"):
        for guardrail in proposal["guardrails"]:
            st.write(f"• {guardrail}")
    if proposal["goal_priorities"]:
        st.markdown("**Prioridades de la ventana activa**")
        st.dataframe(pd.DataFrame(proposal["goal_priorities"]), use_container_width=True, hide_index=True)
    if proposal["availability_conflicts"]:
        st.error("Existen sesiones en días marcados como no disponibles.")
        st.dataframe(pd.DataFrame(proposal["availability_conflicts"]), use_container_width=True, hide_index=True)
    if proposal["evaluations_due"]:
        with st.expander(f"Evaluaciones que requieren atención · {len(proposal['evaluations_due'])}"):
            st.dataframe(pd.DataFrame([{
                "Métrica": item["metric_name"], "Serie": item["series_label"],
                "Estado": item["status"], "Fecha objetivo": item["target_date"] or "Inicial",
            } for item in proposal["evaluations_due"]]), use_container_width=True, hide_index=True)

    opportunities = proposal.get("evaluation_opportunities") or []
    if opportunities:
        st.markdown("**Oportunidades de evaluación dentro de la semana**")
        for item in opportunities:
            st.info(
                f"{item['date']} · {item['discipline']} · {item['metric_name']}: "
                f"{item['instruction']}"
            )
        st.caption("Estas indicaciones no modifican la sesión base ni su carga estimada.")

    if proposal["changes"]:
        st.markdown("**Cambios propuestos y justificación**")
        for number, change in enumerate(proposal["changes"], start=1):
            with st.expander(f"{number}. {change['title']}", expanded=True):
                before, after = change["before"], change["after"]
                columns = st.columns(2)
                columns[0].markdown(
                    f"**Antes**  \n{before['date']} · {display_es(before['label'])}  \n"
                    f"Distancia: {_compact_number(before.get('distance_km'))} km"
                )
                columns[1].markdown(
                    f"**Después**  \n{after['date']} · {display_es(after['label'])}  \n"
                    f"Distancia: {_compact_number(after.get('distance_km'))} km"
                )
                st.write(f"**Señal:** {change['signal']}")
                st.write(f"**Operación:** {change['operation']}")
                st.write(f"**Límite:** {change['limit']}")
                st.write(f"**Efecto esperado:** {change['expected_effect']}")
                st.checkbox(
                    "Incluir este cambio en la semana aprobada",
                    value=True,
                    key=f"adaptive_include_{chosen}_{change['change_id']}",
                )
    else:
        st.success("No se modificó la semana. La tabla de reglas muestra exactamente qué umbrales no se alcanzaron.")

    st.divider()
    _step_header(4, "Comparación base versus adaptada", "complete")
    base_result = _session_summary(proposal["base_sessions"])
    adapted_result = _session_summary(proposal["adapted_sessions"])
    comparison = pd.DataFrame([
        {"Variable": "Carga estimada", "Base": round(base_result["load"], 1), "Adaptada": round(adapted_result["load"], 1), "Cambio": round(adapted_result["load"] - base_result["load"], 1)},
        {"Variable": "Distancia total (km)", "Base": round(base_result["distance"], 1), "Adaptada": round(adapted_result["distance"], 1), "Cambio": round(adapted_result["distance"] - base_result["distance"], 1)},
        {"Variable": "Sesiones", "Base": base_result["sessions"], "Adaptada": adapted_result["sessions"], "Cambio": adapted_result["sessions"] - base_result["sessions"]},
        {"Variable": "Sesiones Z4–Z6", "Base": base_result["intensity_sessions"], "Adaptada": adapted_result["intensity_sessions"], "Cambio": adapted_result["intensity_sessions"] - base_result["intensity_sessions"]},
    ])
    st.dataframe(comparison, use_container_width=True, hide_index=True)
    st.caption("La carga comparativa se calcula en memoria con el mismo motor de carga; al publicar deberá persistirse como una nueva versión.")
    st.markdown("**Comparación diaria**")
    daily = _session_rows(proposal["base_sessions"])
    adapted = _session_rows(proposal["adapted_sessions"])
    def session_label(row):
        return " · ".join(
            str(value) for value in (row.get("Deporte"), row.get("Zona"), row.get("Terreno"))
            if value and value != "—"
        )
    st.dataframe(pd.DataFrame([{
        "Fecha": base["Fecha"],
        "Base": session_label(base),
        "Adaptada": session_label(other),
        "Distancia base": base.get("Distancia (km)"),
        "Distancia adaptada": other.get("Distancia (km)"),
        "Estado": "Modificada" if base != other else "Sin cambio",
    } for base, other in zip(daily, adapted)]), use_container_width=True, hide_index=True)

    st.divider()
    _step_header(5, "Revisión y aprobación", "active")
    comment = st.text_area("Comentario de la decisión", key="adaptive_decision_comment")
    selected_change_ids = [
        item["change_id"] for item in proposal.get("changes", [])
        if st.session_state.get(f"adaptive_include_{chosen}_{item['change_id']}", True)
    ]
    st.write(f"Cambios seleccionados: **{len(selected_change_ids)} de {len(proposal.get('changes', []))}**")
    approval_columns = st.columns(2)
    if approval_columns[0].button("Mantener semana base", use_container_width=True):
        value = save_decision(chosen, prediction["week_start"], "keep_base", proposal, comment)
        published = publish_plan(chosen, proposal, [], comment, value["id"])
        st.session_state["adaptive_decision"] = value
        st.session_state["adaptive_published_plan"] = published
        _rerun()
    if approval_columns[1].button("Aprobar y publicar en calendario", use_container_width=True):
        value = save_decision(chosen, prediction["week_start"], "accept_adaptive_proposal", proposal, comment)
        published = publish_plan(chosen, proposal, selected_change_ids, comment, value["id"])
        st.session_state["adaptive_decision"] = value
        st.session_state["adaptive_published_plan"] = published
        _rerun()
    decision = st.session_state.get("adaptive_decision")
    if decision:
        label = "Semana base conservada" if decision["choice"] == "keep_base" else "Propuesta adaptada aceptada"
        st.success(f"{label} · decisión registrada {decision['created_at']}")
        if st.session_state.get("adaptive_published_plan"):
            plan = st.session_state["adaptive_published_plan"]
            st.info(f"Publicada en calendario como versión {plan['version']} · carga estimada {plan['estimated_load']:.1f}.")

    active = next((item for item in load_active_plans() if item.get("week_start") == prediction["week_start"]), None)
    if active:
        with st.expander(f"Plan publicado · versión {active['version']}"):
            st.write(f"Publicado: {active['published_at']} · Motor: {active['engine_version']}")
            st.dataframe(pd.DataFrame(_session_rows(active["sessions"])), use_container_width=True, hide_index=True)
            if st.button("Revertir esta publicación", key=f"revert_{active['id']}"):
                revert_plan(active["id"])
                st.session_state.pop("adaptive_published_plan", None)
                _rerun()
