"""Model training and evaluation page."""

import json
import os
import re
import subprocess
import sys
from datetime import datetime

import streamlit as st

from backend.normalizer import normalize_json
from backend.structured_processor import process_structured_sessions
from config import PROCESSED_DATA_DIR, PROJECT_ROOT


def render():
    # STEP 3 - RETRAIN MODELS
    # ==========================================================

    st.header("Paso 3 · Entrenar modelos de evaluación")

    st.write(
        """
    Entrena todos los modelos predictivos usando una división cronológica 80/20.

    Estos modelos se utilizan sólo para métricas y evaluación del estilo del flujo.
    """
    )

    training_scripts = [
        ("Deporte", "train_sport.py"),
        ("Tipo de sesión", "train_session_type.py"),
        ("Sesión estructurada", "train_structured.py"),
        ("Familia de objetivo", "train_objective_family.py"),
        ("Terreno", "train_terrain.py"),
        ("Zonas", "train_zones.py"),
        ("Distancia", "train_distance.py"),
    ]

    processed_files = sorted(PROCESSED_DATA_DIR.glob("*.json"))

    dataset_dates = []

    for file in processed_files:
        try:
            dataset_dates.append(datetime.strptime(file.stem, "%Y-%m-%d").date())
        except ValueError:
            continue

    dataset_dates.sort()

    if dataset_dates:
        first_date = dataset_dates[0]
        last_date = dataset_dates[-1]
        total_months = (
            (last_date.year - first_date.year) * 12
            + last_date.month
            - first_date.month
        )
        dataset_years, dataset_months = divmod(total_months, 12)
        dataset_span = f"{dataset_years} años, {dataset_months} meses"
    else:
        first_date = None
        last_date = None
        dataset_span = "Sin datos"

    col1, col2 = st.columns(2)

    with col1:
        st.metric("Semanas procesadas", len(processed_files))

    with col2:
        st.metric("Etapas de entrenamiento", len(training_scripts))

    if dataset_dates:
        st.markdown(
            f"### Intervalo del conjunto de datos: {first_date.isoformat()} a "
            f"{last_date.isoformat()}"
        )
        st.markdown(f"### Extensión histórica: {dataset_span}")
    else:
        st.warning("No hay archivos JSON procesados disponibles para entrenar.")

    if st.button(
        "Entrenar modelos de evaluación · 80/20",
        disabled=not processed_files,
        use_container_width=True,
    ):
        progress = st.progress(0)
        current_step = st.empty()
        logs = []
        training_failed = False

        valid_sports = {"Descanso", "Trail", "Corrida", "Bici", "Kayak"}
        normalized_files = []

        current_step.info("Validando el conjunto de datos procesado...")

        try:
            for json_file in processed_files:
                with open(json_file, "r", encoding="utf-8") as file:
                    week = json.load(file)

                sessions = week.get("sessions", [])

                if any(session.get("sport") not in valid_sports for session in sessions):
                    week = normalize_json(week)
                    week = process_structured_sessions(week)

                    with open(json_file, "w", encoding="utf-8") as file:
                        json.dump(week, file, indent=4, ensure_ascii=False)

                    normalized_files.append(json_file.name)

            if normalized_files:
                logs.append(
                    "===== Dataset normalization =====\n"
                    + "Normalized: "
                    + ", ".join(normalized_files)
                )

        except Exception as error:
            training_failed = True
            logs.append(f"===== Dataset validation =====\n{error}")
            current_step.error("Falló la validación del conjunto de datos.")

        for index, (model_name, script_name) in enumerate(training_scripts):
            if training_failed:
                break

            current_step.info(
                f"Entrenando {model_name} ({index + 1}/{len(training_scripts)})..."
            )

            script_path = PROJECT_ROOT / "ML" / "train" / script_name

            result = subprocess.run(
                [sys.executable, "-X", "utf8", str(script_path)],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={
                    **os.environ,
                    "TRAIN_MODE": "evaluation",
                    "MODEL_OUTPUT_DIR": str(PROJECT_ROOT / "ML" / "models" / "evaluation"),
                },
            )

            output = result.stdout
            if result.stderr:
                output += "\n" + result.stderr

            if result.returncode != 0:
                logs.append(f"\n===== {model_name} =====\n{output.strip()}")
                training_failed = True
                current_step.error(f"Falló el entrenamiento al procesar {model_name}.")
                break

            summaries = re.findall(
                r"MODEL_SUMMARY_START\s*(.*?)\s*MODEL_SUMMARY_END",
                output,
                flags=re.DOTALL,
            )

            if summaries:
                logs.append(
                    f"\n===== {model_name} =====\n"
                    + "\n\n".join(summary.strip() for summary in summaries)
                )
            else:
                logs.append(
                    f"\n===== {model_name} =====\n"
                    "Training completed, but no metrics summary was produced."
                )

            progress.progress((index + 1) / len(training_scripts))

        with st.expander("Registros de entrenamiento", expanded=training_failed):
            st.code("\n".join(logs), language="text")

        if training_failed:
            st.error("La secuencia de entrenamiento se detuvo. Revisa los registros anteriores.")
        else:
            current_step.success("Todos los modelos se entrenaron correctamente.")
            st.success("Los archivos de los modelos fueron actualizados.")

    st.divider()

    # ==========================================================
    # STEP 4 - PIPELINE STYLE SCORE
    # ==========================================================

    st.header("Paso 4 · Evaluar el estilo del flujo")

    st.write(
        """
    Evalúa el flujo completo de predicción sobre las últimas 30 semanas históricas
    y compara su estilo estadístico con los planes de entrenamiento reales.
    """
    )

    if st.button(
        "Ejecutar evaluación del estilo del flujo",
        use_container_width=True,
    ):
        evaluation_status = st.empty()
        evaluation_status.info("Generando predicciones del flujo...")

        pipeline_result = subprocess.run(
            [sys.executable, "-X", "utf8", str(PROJECT_ROOT / "ML" / "evaluate_pipeline.py")],
            cwd=PROJECT_ROOT / "ML",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )

        if pipeline_result.returncode != 0:
            evaluation_status.error("Falló la evaluación del flujo.")
            with st.expander("Error de evaluación", expanded=True):
                st.code(
                    (pipeline_result.stdout + "\n" + pipeline_result.stderr).strip(),
                    language="text",
                )
        else:
            evaluation_status.info("Calculando puntuación de estilo...")

            score_result = subprocess.run(
                [sys.executable, "-X", "utf8", str(PROJECT_ROOT / "ML" / "style_score.py")],
                cwd=PROJECT_ROOT / "ML",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

            if score_result.returncode != 0:
                evaluation_status.error("Falló el cálculo de la puntuación de estilo.")
                with st.expander("Error de puntuación de estilo", expanded=True):
                    st.code(
                        (score_result.stdout + "\n" + score_result.stderr).strip(),
                        language="text",
                    )
            else:
                score_output = score_result.stdout
                global_match = re.search(
                    r"GLOBAL STYLE SCORE\s*:\s*([0-9.]+)%",
                    score_output,
                )
                consistency_match = re.search(
                    r"Sport-Session consistency\s*:\s*([0-9.]+)",
                    score_output,
                )
                joint_match = re.search(
                    r"Joint distribution\s*:\s*([0-9.]+)",
                    score_output,
                )

                component_scores = []
                for component in (
                    "SPORT",
                    "SESSION TYPE",
                    "OBJECTIVE FAMILY",
                    "TERRAIN",
                    "ZONE",
                    "DISTANCE",
                ):
                    component_match = re.search(
                        rf"{re.escape(component)}\s*\n-+.*?Overall\s*:\s*([0-9.]+)",
                        score_output,
                        flags=re.DOTALL,
                    )
                    if component_match:
                        component_scores.append(
                            {
                                "Component": component.title(),
                                "Style score": f"{float(component_match.group(1)):.1f}%",
                            }
                        )

                evaluation_status.success("Evaluación del estilo del flujo completada.")

                if global_match:
                    st.metric(
                        "Puntuación global de estilo",
                        f"{float(global_match.group(1)):.1f}%",
                    )

                if component_scores:
                    st.table(component_scores)

                if consistency_match and joint_match:
                    col1, col2 = st.columns(2)
                    with col1:
                        st.metric(
                            "Coherencia deporte-sesión",
                            f"{float(consistency_match.group(1)):.1f}%",
                        )
                    with col2:
                        st.metric(
                            "Distribución conjunta",
                            f"{float(joint_match.group(1)):.1f}%",
                        )

                with st.expander("Informe completo de estilo"):
                    st.code(score_output.strip(), language="text")

    st.divider()

    # ==========================================================
    # STEP 5 - PRODUCTION MODELS
    # ==========================================================

    st.header("Paso 5 · Entrenar modelos de producción")

    st.write(
        """
    Entrena los modelos definitivos con el 100% del conjunto de datos histórico.

    Estos modelos se utilizarán exclusivamente para predecir la próxima semana desconocida.
    """
    )

    if st.button(
        "Entrenar modelos de producción · 100%",
        disabled=not processed_files,
        use_container_width=True,
    ):
        production_progress = st.progress(0)
        production_status = st.empty()
        production_failed = False

        for index, (model_name, script_name) in enumerate(training_scripts):
            production_status.info(
                f"Entrenando {model_name} con el 100% de los datos "
                f"({index + 1}/{len(training_scripts)})..."
            )

            script_path = PROJECT_ROOT / "ML" / "train" / script_name
            result = subprocess.run(
                [sys.executable, "-X", "utf8", str(script_path)],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env={
                    **os.environ,
                    "TRAIN_MODE": "production",
                    "MODEL_OUTPUT_DIR": str(PROJECT_ROOT / "ML" / "models" / "production"),
                },
            )

            if result.returncode != 0:
                production_failed = True
                production_status.error(f"Falló el entrenamiento de producción en {model_name}.")
                with st.expander("Error de entrenamiento de producción", expanded=True):
                    st.code((result.stdout + "\n" + result.stderr).strip(), language="text")
                break

            production_progress.progress((index + 1) / len(training_scripts))

        if not production_failed:
            production_status.success("Todos los modelos de producción se entrenaron correctamente.")
            st.success("Los modelos de producción ahora incluyen el 100% del historial.")

    st.divider()

    # ==========================================================
