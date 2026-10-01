"""Main Streamlit application for Training Planner AI."""

from pathlib import Path
import sys

import streamlit as st

# Streamlit adds ``app`` to sys.path when this file is executed directly.
# Project-level packages such as ``metrics`` live one directory above it.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import initialize_project
from ui_pages import (
    athlete_state,
    calendar_page,
    data_ingestion,
    nutrition_planning,
    objectives,
    planning,
    physiological_analysis,
    segments,
    statistics_page,
    training_load,
)


initialize_project()

st.set_page_config(
    page_title="Training Planner AI",
    page_icon="🏃",
    layout="wide",
)

st.markdown(
    """
<style>
html, body, [class*="css"] {
    font-family: "Aptos", "Segoe UI Variable", "Segoe UI", sans-serif;
    color: #17233C;
}

.stApp {
    background: #F4F7FB;
}

.block-container {
    box-sizing: border-box;
    width: 95%;
    max-width: 1540px;
    padding-top: 2rem;
    padding-left: 2rem;
    padding-right: 2rem;
}

@media (max-width: 768px) {
    .block-container {
        width: 100%;
        padding-left: 1rem;
        padding-right: 1rem;
    }
}

h1, h2, h3 {
    color: #112B4E;
    font-family: "Bahnschrift", "Aptos Display", "Segoe UI", sans-serif;
    font-weight: 650;
    letter-spacing: -0.02em;
}

div.stButton > button,
div.stFormSubmitButton > button,
div.stDownloadButton > button {
    border-radius: 8px;
    min-height: 42px;
    font-size: 15px;
    font-family: "Bahnschrift", "Aptos", "Segoe UI", sans-serif;
    font-weight: 600;
    letter-spacing: .01em;
    box-shadow: none;
    transition: border-color .15s ease, background-color .15s ease;
}

[data-testid="stBaseButton-primary"] {
    background: #1267E5;
    color: #FFFFFF;
    border: 1px solid #1267E5;
}

[data-testid="stBaseButton-primary"]:hover {
    background: #0B50BC;
    border-color: #0B50BC;
    color: #FFFFFF;
}

[data-testid="stBaseButton-secondary"] {
    background: #FFFFFF;
    color: #17467D;
    border: 1px solid #B9CBE0;
}

[data-testid="stBaseButton-secondary"]:hover {
    background: #EAF3FF;
    border-color: #4386DA;
    color: #0B4E9D;
}

[data-baseweb="input"] > div,
[data-baseweb="select"] > div,
[data-baseweb="textarea"] {
    background: #FFFFFF;
    border-color: #CED9E8;
    border-radius: 9px;
}

[data-baseweb="input"] > div:focus-within,
[data-baseweb="select"] > div:focus-within,
[data-baseweb="textarea"]:focus-within {
    border-color: #18A6B8;
    box-shadow: 0 0 0 1px #18A6B8;
}

hr {
    border-color: #D9E2EE;
}

[data-testid="stMetric"] {
    background: #FFFFFF;
    border: 1px solid #DCE5F0;
    border-top: 3px solid #18A6B8;
    border-radius: 10px;
    padding: .85rem 1rem;
    box-shadow: 0 3px 10px rgba(17,43,78,.055);
}

[data-baseweb="tab-list"] {
    gap: .35rem;
    border-bottom: 1px solid #D9E2EE;
}

[data-baseweb="tab"] {
    border-radius: 8px 8px 0 0;
    color: #53657D;
}

[data-baseweb="tab"][aria-selected="true"] {
    color: #075ECF;
    background: #E7F1FF;
}

[data-testid="stDataFrame"], [data-testid="stTable"] {
    border: 1px solid #D9E2EE;
    border-radius: 10px;
    overflow: hidden;
}

[data-testid="stExpander"] {
    background: #FFFFFF;
    border: 1px solid #D9E2EE;
    border-radius: 10px;
}

[data-testid="stSidebar"] {
    background: linear-gradient(180deg, #102C50 0%, #0B2342 100%);
    border-right: 3px solid #18A6B8;
}

[data-testid="stSidebar"] p,
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3,
[data-testid="stSidebar"] button,
[data-testid="stSidebar"] label {
    font-family: "Aptos", "Segoe UI Variable", "Segoe UI", sans-serif;
}

/* Preserve Streamlit's icon font; overriding it exposes names such as
   keyboard_double_arrow_left as plain text. */
[data-testid="stSidebar"] .material-symbols-rounded,
[data-testid="stSidebar"] [class*="material-symbols"] {
    font-family: "Material Symbols Rounded" !important;
}

[data-testid="stSidebar"] .stMarkdown,
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] h1,
[data-testid="stSidebar"] h2,
[data-testid="stSidebar"] h3 {
    color: #FFFFFF;
}

[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {
    color: #A9BED5;
}

[data-testid="stSidebar"] hr {
    border-color: rgba(255,255,255,.16);
}

[data-testid="stSidebar"] div.stButton > button {
    width: 100%;
    min-height: 43px;
    justify-content: flex-start;
    padding-left: 1rem;
    border-radius: 8px;
    font-size: 15px;
    background: transparent;
    color: #D8E6F4;
    border: 1px solid transparent;
}

[data-testid="stSidebar"] div.stButton > button[kind="primary"] {
    background: linear-gradient(90deg, #1267E5 0%, #168FD4 100%);
    color: #FFFFFF;
    border-color: #3BA8E8;
    box-shadow: inset 4px 0 0 #FF6B4A;
}

[data-testid="stSidebar"] [data-testid="stBaseButton-primary"] {
    background: linear-gradient(90deg, #1267E5 0%, #168FD4 100%);
    color: #FFFFFF;
    border-color: #3BA8E8;
    box-shadow: inset 4px 0 0 #FF6B4A;
}

[data-testid="stSidebar"] div.stButton > button:not(:disabled):hover {
    background: rgba(255,255,255,.09);
    color: #FFFFFF;
    border-color: rgba(75,190,220,.6);
}

[data-testid="stSidebar"] div.stButton > button[kind="primary"]:hover,
[data-testid="stSidebar"] [data-testid="stBaseButton-primary"]:hover {
    background: linear-gradient(90deg, #0E5BCF 0%, #117FBF 100%);
    color: #FFFFFF;
    border-color: #52C7E0;
    box-shadow: inset 4px 0 0 #FF6B4A;
}

[data-testid="stSidebar"] h2 {
    font-family: "Bahnschrift", "Aptos Display", "Segoe UI", sans-serif;
    letter-spacing: .01em;
}

[data-testid="stSidebar"] div.stButton > button:disabled {
    background: rgba(255,255,255,.045);
    color: #7890A9;
    border: 1px dashed #49617A;
    opacity: 1;
}
</style>
""",
    unsafe_allow_html=True,
)


def _select_main_page(page):
    """Update navigation before Streamlit renders the new page."""
    st.session_state["main_navigation"] = page


with st.sidebar:
    st.markdown("## Planificador Multideporte")
    st.caption("Entrenamiento, rendimiento y adaptación")
    st.divider()
    pages = (
        "Ingestión de datos",
        "Planificación nutricional",
        "Estadísticas",
        "Entrenamiento del modelo",
        "Planificación",
        "Calendario",
        "Carga de entrenamiento",
        "Estado del atleta",
        "Objetivos",
        "Segmentos",
        "Análisis fisiológico",
    )
    legacy_pages = {
        "Data ingestion": "Ingestión de datos", "Model training": "Ingestión de datos",
        "Predictions": "Planificación", "Predicciones": "Planificación",
        "Plan adaptativo": "Planificación", "Training Load": "Carga de entrenamiento",
    }
    selected_page = st.session_state.get("main_navigation", "Ingestión de datos")
    selected_page = legacy_pages.get(selected_page, selected_page)
    st.session_state["main_navigation"] = selected_page
    for page in pages:
        frozen = page == "Entrenamiento del modelo"
        label = page + (" · congelado" if frozen else "")
        st.button(
            label,
            key=f"nav_{page}",
            disabled=frozen,
            type="primary" if page == selected_page and not frozen else "secondary",
            use_container_width=True,
            on_click=_select_main_page,
            args=(page,),
        )
    st.divider()
    st.caption("Los modelos están congelados para evitar reentrenamientos accidentales.")

st.title(selected_page)

if selected_page == "Ingestión de datos":
    st.caption("Carga y procesa actividades Garmin y planes de entrenamiento.")
    data_ingestion.render()
elif selected_page == "Planificación":
    st.caption("Genera la semana base y transfórmala en el plan definitivo mediante la adaptación contextual.")
    planning.render()
elif selected_page == "Planificación nutricional":
    st.caption("Define los alimentos y, posteriormente, genera estrategias nutricionales para cada sesión.")
    nutrition_planning.render()
elif selected_page == "Estadísticas":
    st.caption("Consulta el resumen histórico y comparativo de todas las actividades procesadas.")
    statistics_page.render()
elif selected_page == "Análisis fisiológico":
    st.caption("Interpreta la preparación y los entrenamientos desde un marco fisiológico individual.")
    physiological_analysis.render()
elif selected_page == "Calendario":
    st.caption("Revisa ciclos, sesiones previstas, actividades realizadas y carga diaria.")
    calendar_page.render()
elif selected_page == "Carga de entrenamiento":
    st.caption("Compara la carga prescrita, predicha y realizada.")
    training_load.render()
elif selected_page == "Estado del atleta":
    st.caption("Integra carga, recuperación, progreso y contexto personal.")
    athlete_state.render()
elif selected_page == "Segmentos":
    st.caption("Explora recorridos FIT y analiza tramos sobre el mapa y la altimetría.")
    segments.render()
else:
    st.caption("Define objetivos, métricas de rendimiento y sus mediciones.")
    objectives.render()
