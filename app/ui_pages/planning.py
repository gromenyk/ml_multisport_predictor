"""Unified workflow for base prediction, manual edits and adaptive planning."""

import streamlit as st

from ui_pages import adaptive_plan, manual_plan, predictions


def render():
    st.write(
        "Construye la proxima semana en tres etapas: genera una semana base desde el historial, "
        "ajustala manualmente si lo necesitas y luego aplica el contexto actual del atleta."
    )

    base_tab, manual_tab, adaptive_tab = st.tabs([
        "1 - Semana base historica",
        "2 - Ajustes manuales",
        "3 - Adaptacion y plan definitivo",
    ])

    with base_tab:
        predictions.render(embedded=True)

    with manual_tab:
        manual_plan.render()

    with adaptive_tab:
        adaptive_plan.render(embedded=True)
