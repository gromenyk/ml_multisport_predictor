"""Dedicated planning calendar page."""

from ui_pages.objectives import _render_calendar


def render():
    st_text = (
        "Visualiza ciclos, evaluaciones, entrenamiento previsto y actividad realizada "
        "en una única vista temporal."
    )
    # Import locally through Streamlit's already configured page environment.
    import streamlit as st
    st.write(st_text)
    _render_calendar()
