"""Unified workflow for base prediction and adaptive planning."""

import streamlit as st

from ui_pages import adaptive_plan, predictions


def render():
    st.write(
        "Construye la próxima semana en dos etapas: primero genera una semana base "
        "a partir del historial y luego conviértela en el plan definitivo utilizando "
        "el estado actual del atleta."
    )

    base_tab, adaptive_tab = st.tabs([
        "1 · Semana base histórica",
        "2 · Adaptación y plan definitivo",
    ])

    with base_tab:
        predictions.render(embedded=True)

    with adaptive_tab:
        adaptive_plan.render(embedded=True)
