"""UI for evidence-based physiological interpretation."""

from __future__ import annotations

from datetime import date
import json

import streamlit as st

from backend.physiological_analysis import (
    build_evidence,
    build_session_evidence,
    request_global_analysis,
    request_session_analysis,
)


def _show_evidence_summary(evidence: dict) -> None:
    training = evidence.get("training_state", {})
    recovery = evidence.get("recovery_state", {})
    latest = evidence.get("last_training") or {}
    columns = st.columns(5)
    columns[0].metric("Última sesión", latest.get("discipline") or "—")
    columns[1].metric("Carga 7 días", f"{training.get('acute_load_7d') or 0:.0f}")
    columns[2].metric("Referencia 42 días", f"{training.get('chronic_load_42d_equivalent') or 0:.0f}")
    columns[3].metric("Métricas con tendencia", len(evidence.get("performance_metrics", [])))
    columns[4].metric("Series geográficas comparables", len(evidence.get("geographic_speed_progress", [])))
    st.caption(
        "El modelo recibe estas fuentes estructuradas, no los archivos FIT crudos. "
        f"Última recuperación registrada: {(recovery.get('latest') or {}).get('date', 'sin registro')}"
    )


def render() -> None:
    st.title("Análisis fisiológico")
    st.write(
        "Interpretación crítica de tu preparación para resistencia multideporte y carreras de expedición. "
        "No modifica la planificación ni sustituye una evaluación médica."
    )
    as_of = st.date_input("Analizar hasta la fecha", value=date.today(), key="physiological_analysis_as_of")
    evidence = build_evidence(as_of)
    _show_evidence_summary(evidence)

    horizon = evidence.get("planning_horizon", {}).get("windows_from_current_onward", [])
    if horizon:
        active = next((item for item in horizon if item.get("status_at_analysis") == "activa"), None)
        future = next((item for item in horizon if item.get("status_at_analysis") == "futura"), None)
        st.caption(
            "Marco principal del análisis: "
            f"ventana activa «{active.get('name')}»" if active else "Marco principal del análisis: sin ventana activa configurada."
        )
        if future:
            st.caption(f"Próxima etapa considerada: «{future.get('name')}» ({future.get('start_date')} a {future.get('end_date')}).")

    st.info(
        "El análisis evalúa coherencia fisiológica, especificidad, carga, recuperación y progreso relativo a tu propia historia; "
        "no usa una norma genérica de volumen para corredores recreativos."
    )
    st.caption("La interpretaci\u00f3n prioriza el significado fisiol\u00f3gico; los datos detallados permanecen disponibles como evidencia al final.")
    actions = st.columns(2)
    with actions[0]:
        latest_requested = st.button("Analizar último entrenamiento", type="primary", use_container_width=True)
    with actions[1]:
        global_requested = st.button("Interpretar panorama fisiol\u00f3gico", use_container_width=True)

    if latest_requested:
        analysis_kind = "sesión"
        evidence_used = build_session_evidence(evidence)
        generator = request_session_analysis
    elif global_requested:
        analysis_kind = "panorama general"
        evidence_used = evidence
        generator = request_global_analysis
    else:
        analysis_kind = None
    if analysis_kind:
        with st.spinner("Contrastando los datos con principios fisiológicos..."):
            try:
                st.session_state["physiological_analysis_result"] = {
                    "as_of": as_of.isoformat(), "kind": analysis_kind,
                    "text": generator(evidence), "evidence": evidence_used,
                }
            except Exception as error:
                st.error(f"No fue posible generar el análisis: {error}")

    result = st.session_state.get("physiological_analysis_result")
    if result:
        st.divider()
        st.caption(f"Análisis de {result['kind']} generado hasta {result['as_of']}.")
        st.markdown(result["text"])
        with st.expander("Fuentes de datos utilizadas"):
            st.json(result["evidence"], expanded=False)
        st.download_button(
            "Descargar análisis en Markdown",
            data=result["text"],
            file_name=f"analisis_fisiologico_{result['as_of']}.md",
            mime="text/markdown",
            use_container_width=True,
        )
