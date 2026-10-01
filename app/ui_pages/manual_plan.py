"""Manual weekly draft between the historical model and adaptive engine."""

from __future__ import annotations

from copy import deepcopy
import json

import pandas as pd
import streamlit as st

from adaptive_planning.engine import _estimated_session_details
from adaptive_planning.manual_plan_store import delete_manual_plan, load_manual_plan, save_manual_plan
from config import PROJECT_ROOT


PREDICTIONS_DIR = PROJECT_ROOT / "ML" / "results" / "predictions"
SPORTS = ("Bici", "Trail", "Corrida", "Kayak")
ZONES = ("Z1", "Z2", "Z3", "Z4", "Z5")
TERRAINS = ("Plano", "Ondulado", "Montaña", "Subida", "Sin especificar")


def _read(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except (OSError, json.JSONDecodeError):
        return default


def _rerun():
    (getattr(st, "rerun", None) or getattr(st, "experimental_rerun"))()


def _entries():
    rows = []
    for path in PREDICTIONS_DIR.glob("prediction_*.json"):
        prediction = _read(path, {})
        if prediction.get("week_start") and prediction.get("week_end"):
            rows.append({"id": path.stem, "prediction": prediction})
    return sorted(rows, key=lambda item: item["prediction"].get("week_start", ""), reverse=True)


def _ensure_ids(sessions):
    for position, session in enumerate(sessions):
        session.setdefault("session_id", f"base_{session.get('date')}_{position}")
    return sessions


def _default_terrain(sport, terrain):
    if terrain:
        return terrain
    return "Ondulado" if sport == "Trail" else "Plano" if sport in {"Bici", "Corrida"} else None


def _session_label(session):
    parts = [session.get("sport") or "Descanso"]
    if session.get("sport") != "Descanso":
        parts.extend([session.get("zone") or "—", session.get("terrain") or "Sin terreno"])
        if session.get("distance_km") is not None:
            parts.append(f"{float(session['distance_km']):.1f} km")
        elif session.get("duration_min") is not None:
            parts.append(f"{float(session['duration_min']):.0f} min")
    if session.get("manual_additional"):
        parts.append("adicional")
    if session.get("manual_protected"):
        parts.append("protegida")
    return " · ".join(parts)


def _update_session(session, prefix):
    is_rest = st.checkbox("Descanso", value=session.get("sport") == "Descanso", key=f"{prefix}_rest")
    if is_rest:
        return {**session, "sport": "Descanso", "zone": None, "terrain": None, "distance_km": None,
                "duration_min": None, "blocks": [], "structured": False, "template": None}
    sport = st.selectbox("Deporte", SPORTS, index=SPORTS.index(session.get("sport")) if session.get("sport") in SPORTS else 0, key=f"{prefix}_sport")
    zone = st.selectbox("Zona", ZONES, index=ZONES.index(session.get("zone")) if session.get("zone") in ZONES else 1, key=f"{prefix}_zone")
    existing_terrain = session.get("terrain") if session.get("terrain") in TERRAINS else "Sin especificar"
    terrain_choice = st.selectbox("Terreno", TERRAINS, index=TERRAINS.index(existing_terrain), key=f"{prefix}_terrain")
    distance = st.number_input("Distancia (km; 0 para usar duración)", min_value=0.0, value=float(session.get("distance_km") or 0), step=0.5, key=f"{prefix}_distance")
    duration = st.number_input("Duración (min)", min_value=1.0, value=float(session.get("duration_min") or 60), step=5.0, key=f"{prefix}_duration")
    template = st.text_input("Descripción", value=session.get("template") or "", key=f"{prefix}_template")
    protected = st.checkbox("Proteger frente a la capa adaptativa", value=bool(session.get("manual_protected")), key=f"{prefix}_protected")
    terrain = _default_terrain(sport, None if terrain_choice == "Sin especificar" else terrain_choice)
    component = {"type": "steady", "zone": zone}
    if distance > 0:
        component["distance_km"] = float(distance)
    else:
        component["duration_min"] = float(duration)
    return {**session, "sport": sport, "zone": zone, "terrain": terrain,
            "distance_km": float(distance) if distance > 0 else None,
            "duration_min": float(duration) if distance <= 0 else None,
            "blocks": [component], "structured": False, "template": template.strip() or None,
            "manual_protected": protected, "manual_origin": True}


def _summary_rows(sessions):
    rows = []
    for session in sessions:
        try:
            details = _estimated_session_details(session)
            duration, load = details["duration_minutes"], details["total_load"]
        except (KeyError, TypeError, ValueError):
            duration, load = None, None
        rows.append({"Fecha": session.get("date"), "Sesión": _session_label(session),
                     "Carga est.": load, "Duración est. (min)": duration})
    return rows


def render():
    st.header("2 · Ajustes manuales")
    st.write("Edita un borrador semanal. Los cambios no pasan a la capa adaptativa hasta guardar la semana completa.")
    entries = _entries()
    if not entries:
        st.info("Primero genera una semana base en la pestaña anterior.")
        return
    by_id = {item["id"]: item for item in entries}
    preferred = st.session_state.get("adaptive_base_prediction_id")
    selected = st.selectbox("Semana a ajustar", tuple(by_id),
        index=tuple(by_id).index(preferred) if preferred in by_id else 0,
        format_func=lambda key: f"{by_id[key]['prediction']['week_start']} a {by_id[key]['prediction']['week_end']}")
    prediction = by_id[selected]["prediction"]
    draft_key = f"manual_week_draft_{selected}"
    if draft_key not in st.session_state:
        saved = load_manual_plan(selected)
        st.session_state[draft_key] = _ensure_ids(deepcopy(saved.get("sessions")) if saved else deepcopy(prediction.get("sessions", [])))
        st.session_state[f"{draft_key}_note"] = saved.get("note", "") if saved else ""
    sessions = st.session_state[draft_key]
    by_day = {}
    for session in sessions:
        by_day.setdefault(session["date"], []).append(session)

    for day, day_sessions in sorted(by_day.items()):
        with st.container(border=True):
            st.markdown(f"### {day}")
            for session in day_sessions:
                with st.expander(_session_label(session), expanded=False):
                    candidate = _update_session(session, session["session_id"])
                    left, right = st.columns(2)
                    if left.button("Aplicar edición al borrador", key=f"apply_{session['session_id']}"):
                        st.session_state[draft_key] = [candidate if item["session_id"] == session["session_id"] else item for item in sessions]
                        _rerun()
                    if session.get("manual_additional") and right.button("Eliminar sesión adicional", key=f"delete_{session['session_id']}"):
                        st.session_state[draft_key] = [item for item in sessions if item["session_id"] != session["session_id"]]
                        _rerun()
            with st.popover("Agregar sesión adicional"):
                extra = {"date": day, "weekday": day_sessions[0].get("weekday"), "sport": "Bici", "zone": "Z2",
                         "terrain": "Plano", "distance_km": None, "duration_min": 60.0, "blocks": [],
                         "manual_additional": True, "manual_protected": True, "manual_origin": True,
                         "session_id": f"extra_{day}_{len(day_sessions)}"}
                candidate = _update_session(extra, f"new_{day}")
                candidate["manual_additional"] = True
                candidate["manual_protected"] = True
                if st.button("Añadir al borrador", key=f"add_{day}"):
                    st.session_state[draft_key] = sessions + [candidate]
                    _rerun()

    st.divider()
    st.markdown("### Guardar semana manual")
    st.dataframe(pd.DataFrame(_summary_rows(st.session_state[draft_key])), hide_index=True, use_container_width=True)
    note = st.text_area("Nota general de los ajustes", value=st.session_state.get(f"{draft_key}_note", ""), key=f"{draft_key}_note")
    if st.button("Guardar ajustes manuales y actualizar semana base", type="primary", use_container_width=True):
        save_manual_plan(selected, prediction["week_start"], prediction["week_end"], st.session_state[draft_key], note)
        st.session_state["adaptive_base_prediction_id"] = selected
        st.session_state["adaptive_base_fixed"] = selected
        for key in ("adaptive_proposal", "adaptive_decision", "adaptive_plan_review"):
            st.session_state.pop(key, None)
        st.success("Semana manual guardada. Ve a Adaptación: esa versión ya es la semana base.")
    if load_manual_plan(selected) and st.button("Descartar ajustes manuales guardados", use_container_width=True):
        delete_manual_plan(selected)
        st.session_state.pop(draft_key, None)
        st.session_state.pop("adaptive_proposal", None)
        _rerun()
