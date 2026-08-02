"""Página de carga y procesamiento de datos."""

import streamlit as st

from backend.garmin_ingestion import (
    ACTIVITIES_DIR,
    FIT_FILES_DIR,
    extract_fit_activities,
    save_fit,
)
from backend.process import process_pending_pdfs
from backend.upload import save_pdf


def _render_fit_ingestion():
    st.header("1 · Actividades Garmin FIT")
    st.write(
        "Carga primero tus actividades realizadas. Los FIT se convierten en registros "
        "normalizados utilizados por carga, métricas y Athlete State."
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
        else:
            st.error("Falló la extracción de los archivos FIT.")
        with st.expander("Registros de extracción Garmin", expanded=not success):
            st.code(logs or "El proceso no produjo registros.", language="text")


def _render_plan_ingestion():
    st.header("2 · Planes de entrenamiento")
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
    _render_plan_ingestion()
