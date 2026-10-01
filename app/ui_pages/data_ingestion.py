"""Página de carga, procesamiento y gobierno analítico de datos."""

import json
from datetime import date
from pathlib import Path

import altair as alt
import folium
import pandas as pd

import streamlit as st
from streamlit_folium import st_folium

from backend.garmin_ingestion import (
    ACTIVITIES_DIR,
    FIT_FILES_DIR,
    extract_fit_activities,
    save_fit,
)
from backend.process import process_pending_pdfs
from backend.upload import save_pdf
from backend.activity_usage_store import (
    PROFILE_RULES,
    load_activity_policies,
    policy_for,
    save_activity_policy,
)
from backend.activity_index import load_activity_index
from athlete_state.state_builder import build_athlete_state
from ui_pages.athlete_state import render_session_feedback
from adaptive_planning.session_feedback_store import (
    feedback_tracking_start,
    load_session_feedback,
)
from backend.ui_translations import display_es


PROFILE_LABELS = {
    "complete": "Completo",
    "load_only": "Sólo carga",
    "load_compliance": "Carga + cumplimiento",
    "exclude": "Excluir completamente",
    "custom": "Personalizado",
}
REASONS = (
    "Sin motivo específico", "Actividad social o acompañando", "Transporte",
    "Competencia", "Prueba o test", "Problema técnico del dispositivo",
    "Condiciones externas excepcionales", "Lesión o molestias", "Otro",
)
RULE_LABELS = {
    "use_for_load": "Carga de entrenamiento",
    "use_for_volume": "Volumen y distancia",
    "use_for_zones": "Tiempo por zonas",
    "use_for_performance": "Métricas de rendimiento",
    "use_for_segments_routes": "Segmentos y rutas",
    "use_for_pauses": "Estadística de pausas",
    "use_for_memory": "Memoria de progresión",
    "use_for_compliance": "Cumplimiento de planificación",
}


def _activity_summary(entry):
    duration_seconds = entry.get("duration_seconds")
    return {
        "path": ACTIVITIES_DIR / entry["file"], "file": entry["file"], "date": entry.get("date") or "",
        "discipline": display_es(entry.get("discipline")) or "—",
        "distance_km": entry.get("distance_km"),
        "duration_minutes": (
            float(duration_seconds) / 60
            if isinstance(duration_seconds, (int, float)) else None
        ),
    }


def _duration_label(seconds):
    if not isinstance(seconds, (int, float)):
        return "—"
    minutes = round(float(seconds) / 60)
    return f"{minutes // 60} h {minutes % 60:02d} min" if minutes >= 60 else f"{minutes} min"


def _render_processed_activity(path):
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        st.warning("No se pudo abrir la actividad recién procesada.")
        return
    activity, summary = payload.get("activity", {}), payload.get("summary", {})
    records = pd.DataFrame(payload.get("records") or [])
    st.subheader("Actividad recién procesada")
    columns = st.columns(4)
    columns[0].metric("Disciplina", activity.get("sport_profile_name") or activity.get("sport") or "—")
    columns[1].metric("Distancia", f"{float(activity.get('distance_meters') or 0) / 1000:.1f} km")
    columns[2].metric("Duración activa", _duration_label(activity.get("duration_seconds")))
    columns[3].metric("Desnivel +", f"{float(activity.get('ascent_meters') or 0):.0f} m")
    columns = st.columns(4)
    columns[0].metric("Velocidad media", f"{float(summary.get('average_speed_mps') or 0) * 3.6:.1f} km/h")
    columns[1].metric("Velocidad máxima", f"{float(summary.get('maximum_speed_mps') or 0) * 3.6:.1f} km/h")
    columns[2].metric("FC media", f"{summary.get('average_heart_rate_bpm') or '—'} bpm")
    columns[3].metric("FC máxima", f"{summary.get('maximum_heart_rate_bpm') or '—'} bpm")
    if records.empty or not {"latitude", "longitude"}.issubset(records):
        st.info("Esta actividad no contiene un recorrido GPS utilizable.")
        return
    route = records.dropna(subset=["latitude", "longitude"]).copy()
    if len(route) < 2:
        st.info("Esta actividad no contiene suficientes puntos GPS para dibujar la ruta.")
        return
    route = route.iloc[::max(1, len(route) // 3000)]
    center = route.iloc[len(route) // 2]
    map_view = folium.Map(
        location=[float(center.latitude), float(center.longitude)], zoom_start=12,
        tiles="https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        attr="© OpenTopoMap · © OpenStreetMap contributors", control_scale=True,
    )
    folium.PolyLine(route[["latitude", "longitude"]].values.tolist(), color="#1267E5", weight=5).add_to(map_view)
    st_folium(map_view, key=f"ingestion_map_{path.name}", height=460, use_container_width=True, returned_objects=[])
    if {"distance_meters", "altitude_meters"}.issubset(records) and records["altitude_meters"].notna().any():
        profile = records.dropna(subset=["distance_meters", "altitude_meters"]).copy()
        profile["distance_km"] = profile["distance_meters"] / 1000
        chart = alt.Chart(profile).mark_area(line={"color": "#1267E5", "strokeWidth": 2}, color="#1267E5", opacity=.18).encode(
            x=alt.X("distance_km:Q", title="Distancia (km)"),
            y=alt.Y("altitude_meters:Q", title="Altitud (m)", scale=alt.Scale(zero=False)),
            tooltip=[alt.Tooltip("distance_km:Q", title="Distancia", format=".2f"), alt.Tooltip("altitude_meters:Q", title="Altitud", format=".0f")],
        ).properties(height=280, title="Perfil altimétrico")
        st.altair_chart(chart, use_container_width=True)


def _render_activity_browser():
    entries = load_activity_index()
    if not entries:
        return
    rows = sorted((_activity_summary(item) for item in entries), key=lambda item: (item["date"], item["file"]), reverse=True)
    query = st.text_input("Buscar actividad", key="ingestion_activity_search", placeholder="Fecha, disciplina o nombre del archivo")
    if query.strip():
        needle = query.strip().lower()
        rows = [item for item in rows if needle in " ".join(str(item.get(key, "")).lower() for key in ("date", "discipline", "file"))]
    st.subheader("Explorar actividades anteriores")
    if not rows:
        st.info("No hay actividades que coincidan con la búsqueda.")
        return
    page_size = 10
    page_count = (len(rows) + page_size - 1) // page_size
    page = st.selectbox(
        "Página", tuple(range(page_count)),
        format_func=lambda value: f"{value + 1} de {page_count}",
        key="ingestion_activity_page",
    )
    visible = rows[page * page_size:(page + 1) * page_size]
    st.dataframe(pd.DataFrame([{
        "Fecha": item["date"], "Disciplina": item["discipline"],
        "Distancia (km)": round(float(item["distance_km"] or 0), 1),
        "Duración (min)": round(float(item["duration_minutes"] or 0)),
    } for item in visible]), hide_index=True, use_container_width=True)
    by_file = {item["file"]: item for item in visible}
    selected = st.selectbox(
        "Actividad para visualizar", tuple(by_file),
        format_func=lambda value: f"{by_file[value]['date']} · {by_file[value]['discipline']} · {float(by_file[value]['distance_km'] or 0):.1f} km",
        key="ingestion_activity_viewer",
    )
    if st.button("Visualizar actividad", use_container_width=True, key="view_previous_activity"):
        st.session_state["last_processed_activity_path"] = str(by_file[selected]["path"])
        st.rerun()


def _render_activity_management():
    st.header("3 · Administrar uso analítico")
    st.write(
        "Decide qué análisis puede utilizar cada actividad. Cambiar esta configuración "
        "no elimina el FIT y no altera sus datos originales."
    )
    entries = load_activity_index()
    if not entries:
        st.info("Todavía no hay actividades procesadas.")
        return
    policies = load_activity_policies()
    activities = sorted(
        (_activity_summary(entry) for entry in entries),
        key=lambda item: (item["date"], item["file"]),
        reverse=True,
    )
    table = pd.DataFrame([{
        "Fecha": item["date"], "Disciplina": item["discipline"],
        "Distancia (km)": item["distance_km"],
        "Duración (min)": round(item["duration_minutes"], 1) if item["duration_minutes"] else None,
        "Uso analítico": PROFILE_LABELS.get(
            policies.get(item["file"], {}).get("profile", "complete"), "Completo"
        ),
        "Motivo": policies.get(item["file"], {}).get("reason", ""),
    } for item in activities[:30]])
    st.caption("Últimas 30 actividades procesadas")
    st.dataframe(table, hide_index=True, use_container_width=True)

    activity_map = {item["file"]: item for item in activities}
    selected_file = st.selectbox(
        "Actividad a configurar",
        tuple(activity_map),
        format_func=lambda name: (
            f"{activity_map[name]['date']} · {activity_map[name]['discipline']} · "
            f"{activity_map[name]['distance_km'] or 0:.1f} km"
        ),
        key="activity_usage_selector",
    )
    current = policy_for(selected_file)
    profile_keys = tuple(PROFILE_LABELS)
    current_profile = current.get("profile", "complete")
    with st.form(f"activity_usage_{selected_file}"):
        profile = st.selectbox(
            "Uso analítico",
            profile_keys,
            index=profile_keys.index(current_profile) if current_profile in profile_keys else 0,
            format_func=lambda value: PROFILE_LABELS[value],
        )
        reason = st.selectbox(
            "Motivo",
            REASONS,
            index=REASONS.index(current.get("reason")) if current.get("reason") in REASONS else 0,
        )
        custom_rules = {}
        if profile == "custom":
            st.caption("Selecciona individualmente dónde puede utilizarse esta actividad.")
            columns = st.columns(2)
            for index, (rule, label) in enumerate(RULE_LABELS.items()):
                with columns[index % 2]:
                    custom_rules[rule] = st.checkbox(label, value=bool(current.get(rule, True)))
        else:
            rules = PROFILE_RULES[profile]
            included = [RULE_LABELS[key] for key, value in rules.items() if value]
            excluded = [RULE_LABELS[key] for key, value in rules.items() if not value]
            st.info(
                "Se conserva: " + (", ".join(included) if included else "ningún análisis")
                + ".\n\nSe excluye: " + (", ".join(excluded) if excluded else "ninguno") + "."
            )
        notes = st.text_area("Comentario opcional", value=current.get("notes", ""))
        submitted = st.form_submit_button("Guardar uso analítico", use_container_width=True)
    if submitted:
        old_load_permission = bool(current.get("use_for_load", True))
        save_activity_policy(selected_file, {
            "profile": profile,
            "reason": "" if reason == "Sin motivo específico" else reason,
            "notes": notes,
            **custom_rules,
        })
        # Re-evaluate just this activity so performance measurements disappear
        # or return immediately, without rebuilding the entire FIT history.
        from metrics.orchestrator import evaluate_activity_file
        evaluate_activity_file(activity_map[selected_file]["path"])
        new_policy = policy_for(selected_file)
        if old_load_permission != bool(new_policy.get("use_for_load", True)):
            from training_load.performed_load import calculate_all_performed_loads
            calculate_all_performed_loads()
        st.success("Uso analítico actualizado. La carga original se conserva según el perfil elegido.")
        st.rerun()


def _render_post_fit_feedback():
    st.header("2 · Evaluar actividades realizadas")
    st.write(
        "Después de procesar un FIT, revisa sus datos objetivos y registra cómo "
        "respondió tu cuerpo. Esta evaluación alimenta la memoria longitudinal."
    )
    entries = load_activity_index()
    if not entries:
        st.info("Procesa una actividad FIT para habilitar su evaluación.")
        return
    activity_dates = []
    for entry in entries:
        try:
            value = entry.get("date")
            if value:
                activity_dates.append(date.fromisoformat(value))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
    as_of = max([date.today(), *activity_dates])
    state = build_athlete_state(as_of)
    sessions = state.get("progression_memory", {}).get("recent_sessions", [])
    tracking_start = feedback_tracking_start()
    if tracking_start:
        sessions = [item for item in sessions if item.get("date", "") >= tracking_start]
        state["progression_memory"]["recent_sessions"] = sessions
        st.caption(
            f"La evaluación subjetiva se solicita desde el {date.fromisoformat(tracking_start).strftime('%d/%m/%Y')}. "
            "Las actividades anteriores conservan todos sus datos objetivos."
        )
    evaluated = {item["activity_id"] for item in load_session_feedback()}
    pending = sum(item["activity_id"] not in evaluated for item in sessions)
    columns = st.columns(2)
    columns[0].metric("Actividades recientes", len(sessions))
    columns[1].metric("Pendientes de evaluación", pending)
    render_session_feedback(
        state, show_memory=False, prefer_pending=True, widget_prefix="ingestion_"
    )


def _render_fit_ingestion():
    st.header("1 · Actividades Garmin FIT")
    st.write(
        "Carga primero tus actividades realizadas. Los FIT se convierten en registros "
        "normalizados utilizados por carga, métricas y Estado del atleta."
    )
    fit_files = sorted(FIT_FILES_DIR.glob("*.fit")) if FIT_FILES_DIR.exists() else []
    activities = sorted(ACTIVITIES_DIR.glob("activity_*.json")) if ACTIVITIES_DIR.exists() else []
    columns = st.columns(2)
    columns[0].metric("Archivos FIT", len(fit_files))
    columns[1].metric("Actividades normalizadas", len(activities))
    uploaded_fit = st.file_uploader(
        "Seleccionar archivos Garmin FIT",
        type=["fit"],
        accept_multiple_files=True,
        key="garmin_fit_upload",
    )
    if uploaded_fit:
        messages, failed = [], False
        for fit_file in uploaded_fit:
            success, message, _ = save_fit(fit_file)
            messages.append(f"{fit_file.name}: {message}")
            failed = failed or not success
        (st.warning if failed else st.success)("\n".join(messages))
    if st.button(
        "Procesar archivos FIT",
        disabled=not FIT_FILES_DIR.exists() or not any(FIT_FILES_DIR.glob("*.fit")),
        use_container_width=True,
    ):
        with st.spinner("Extrayendo actividades Garmin..."):
            success, logs = extract_fit_activities()
        if success:
            st.success("Actividades Garmin procesadas correctamente.")
            processed = sorted(
                ACTIVITIES_DIR.glob("activity_*.json"),
                key=lambda item: item.stat().st_mtime_ns,
                reverse=True,
            )
            if processed:
                st.session_state["last_processed_activity_path"] = str(processed[0])
        else:
            st.error("Falló la extracción de los archivos FIT.")
        with st.expander("Registros de extracción Garmin", expanded=not success):
            st.code(logs or "El proceso no produjo registros.", language="text")
    latest_path = st.session_state.get("last_processed_activity_path")
    if latest_path:
        path = ACTIVITIES_DIR / Path(latest_path).name
        if path.exists():
            _render_processed_activity(path)
    _render_activity_browser()


def _render_plan_ingestion():
    st.header("4 · Planes de entrenamiento")
    st.write(
        "Carga una planificación semanal en PDF. Se conservará en `raw_data` y podrá "
        "convertirse al formato estructurado utilizado por el modelo."
    )
    uploaded_file = st.file_uploader("Seleccionar un PDF", type=["pdf"], key="training_plan_pdf_upload")
    pdf_uploaded = False
    if uploaded_file is not None:
        success, message, saved_path = save_pdf(uploaded_file)
        if success:
            pdf_uploaded = True
            st.success(message)
            columns = st.columns(2)
            columns[0].metric("Archivo", uploaded_file.name)
            columns[1].metric("Tamaño", f"{uploaded_file.size / 1024:.1f} KB")
            st.info(f"Guardado en:\n\n{saved_path}")
        else:
            st.error(message)
    if st.button(
        "Procesar plan y agregarlo al dataset",
        disabled=not pdf_uploaded,
        use_container_width=True,
    ):
        with st.spinner("Procesando plan de entrenamiento..."):
            success, message = process_pending_pdfs()
        (st.success if success else st.error)(message)


def render():
    _render_fit_ingestion()
    st.divider()
    _render_post_fit_feedback()
    st.divider()
    _render_activity_management()
    st.divider()
    _render_plan_ingestion()
