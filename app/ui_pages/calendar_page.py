"""Dedicated planning calendar page."""

from ui_pages.objectives import _render_calendar


def render():
    st_text = (
        "Visualiza ciclos, evaluaciones, entrenamiento previsto y actividad realizada "
        "en una única vista temporal."
    )
    # Import locally through Streamlit's already configured page environment.
    import streamlit as st
    st.markdown(
        """
        <style>
        /* Esta ampliación existe solamente mientras se renderiza Calendario. */
        .block-container {
            width: 100% !important;
            max-width: none !important;
            padding-left: 1.25rem !important;
            padding-right: 1.25rem !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    st.write(st_text)
    _render_calendar()
