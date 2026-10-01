"""Next-week prediction page."""

import json
import subprocess
import sys
from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from config import PROJECT_ROOT
from ML.dataset import TrainingDataset, validate_consecutive_context
from ML.historical_context_selector import select_historical_context
from ML.predict_next_week import build_prediction_candidates, save_prediction_files
from backend.ui_translations import display_es


LOAD_OUTPUTS = {
    "Prescribed": (
        PROJECT_ROOT
        / "data"
        / "derived"
        / "training_load"
        / "prescribed_load.json"
    ),
    "Predicted": (
        PROJECT_ROOT
        / "data"
        / "derived"
        / "training_load"
        / "predicted_load.json"
    ),
    "Performed": (
        PROJECT_ROOT
        / "data"
        / "derived"
        / "training_load"
        / "performed_load.json"
    ),
}

LOAD_SCRIPTS = (
    ("Prescribed", "prescribed_load.py"),
    ("Predicted", "predicted_load.py"),
    ("Performed", "performed_load.py"),
)

PREDICTIONS_DIR = PROJECT_ROOT / "ML" / "results" / "predictions"
PROCESSED_DATA_DIR = PROJECT_ROOT / "data" / "processed_data"
ADAPTIVE_PLANS_FILE = PROJECT_ROOT / "data" / "athlete_state" / "adaptive_published_plans.json"


def _next_prediction_context():
    # The base model receives human plans only. Approved adaptive plans are
    # used solely to find the next unpublished operational week.
    dataset = TrainingDataset(PROCESSED_DATA_DIR, context_weeks=4)
    current_monday = date.today() - timedelta(days=date.today().weekday())
    payload, _ = _read_json(ADAPTIVE_PLANS_FILE)
    active_ends = [
        item.get("week_end") for item in (payload or {}).get("plans", [])
        if item.get("status") == "active" and item.get("week_end")
    ]
    maximum_target = current_monday + timedelta(days=7)
    target_start = max(
        maximum_target,
        date.fromisoformat(max(active_ends)) + timedelta(days=1)
        if active_ends else maximum_target,
    )
    target_end = target_start + timedelta(days=6)
    context, selection = select_historical_context(
        dataset.weeks, date.today(), 4, target_week_start=target_start
    )
    validate_consecutive_context(context, 4)
    return {
        "weeks": context, "selection": selection,
        "target_start": target_start, "target_end": target_end,
        "allowed": target_start <= maximum_target, "maximum_target": maximum_target,
    }


def _read_json(path):
    """Read one derived dataset without hiding malformed or missing files."""
    if not path.exists():
        return None, f"File not found: {path.name}"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as error:
        return None, str(error)


def _rebuild_load(script_name, force=False):
    """Run one source-specific load adapter with the current Python runtime."""
    command = [
            sys.executable,
            "-X",
            "utf8",
            str(PROJECT_ROOT / "training_load" / script_name),
        ]
    if force and script_name == "performed_load.py":
        command.append("--force")
    return subprocess.run(
        command,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _prediction_load_snapshots():
    """Index calculated prediction loads by immutable prediction ID."""
    data, _ = _read_json(LOAD_OUTPUTS["Predicted"])
    return {
        prediction["prediction_id"]: prediction
        for prediction in (data or {}).get("predictions", [])
        if prediction.get("prediction_id")
    }


def _load_prediction_archive():
    """Load every archived prediction JSON and its optional derived load."""
    load_snapshots = _prediction_load_snapshots()
    entries = []
    errors = []

    for path in sorted(PREDICTIONS_DIR.glob("prediction_*.json")):
        prediction, error = _read_json(path)
        if error:
            errors.append(f"{path.name}: {error}")
            continue

        try:
            week_start = date.fromisoformat(prediction["week_start"])
            week_end = date.fromisoformat(prediction["week_end"])
            generated_at = pd.to_datetime(prediction["generated_at"])
        except (KeyError, TypeError, ValueError) as error:
            errors.append(f"{path.name}: invalid metadata ({error})")
            continue

        prediction_id = path.stem
        load_snapshot = load_snapshots.get(prediction_id)
        daily_loads = (
            load_snapshot.get("daily_loads", {}) if load_snapshot else {}
        )
        load_values = [
            day.get("predicted_load")
            for day in daily_loads.values()
            if day.get("predicted_load") is not None
        ]
        entries.append(
            {
                "prediction_id": prediction_id,
                "path": path,
                "prediction": prediction,
                "load_snapshot": load_snapshot,
                "week_start": week_start,
                "week_end": week_end,
                "generated_at": generated_at,
                "weekly_load": sum(load_values) if load_values else None,
            }
        )

    entries.sort(key=lambda item: item["generated_at"], reverse=True)
    return entries, errors


def _prediction_workout(session):
    """Create the compact workout description used in weekly tables."""
    if session.get("sport") == "Descanso":
        return "Descanso"
    if session.get("template"):
        return session["template"]
    if session.get("distance_km") is not None:
        return f"{_compact_number(session['distance_km'])} km"
    return "-"


def _compact_number(value, decimals=1):
    """Format UI values without exposing calculation-only float noise."""
    if not isinstance(value, (int, float)):
        return "—" if value is None else str(value)
    rounded = round(float(value), decimals)
    return str(int(rounded)) if rounded.is_integer() else f"{rounded:.{decimals}f}"


def _render_prediction_archive_detail(entry):
    """Render the selected archived generation as a seven-day table."""
    prediction = entry["prediction"]
    load_snapshot = entry["load_snapshot"] or {}
    daily_loads = load_snapshot.get("daily_loads", {})

    header_columns = st.columns(3)
    with header_columns[0]:
        st.metric(
            "Semana predicha",
            f"{entry['week_start'].strftime('%d/%m/%Y')} – "
            f"{entry['week_end'].strftime('%d/%m/%Y')}",
        )
    with header_columns[1]:
        st.metric(
            "Generada",
            entry["generated_at"].strftime("%d/%m/%Y %H:%M"),
        )
    with header_columns[2]:
        weekly_load = entry["weekly_load"]
        st.metric(
            "Carga predicha",
            f"{weekly_load:.2f}" if weekly_load is not None else "No disponible",
        )

    rows = []
    for session in prediction.get("sessions", []):
        daily_load = daily_loads.get(session.get("date"), {})
        rows.append(
            {
                "Fecha": date.fromisoformat(session["date"]),
                "Día": display_es(session.get("weekday")),
                "Deporte": display_es(session.get("sport")),
                "Objetivo": display_es(session.get("objective_class")),
                "Terreno": display_es(session.get("terrain")),
                "Zona": session.get("zone"),
                "Entrenamiento": display_es(_prediction_workout(session)),
                "Carga": daily_load.get("predicted_load"),
            }
        )

    st.dataframe(
        rows,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Fecha": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Carga": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    st.caption(
        "Semanas de contexto: "
        + ", ".join(prediction.get("context_weeks") or [])
    )
    st.caption(f"Identificador de predicción: {entry['prediction_id']}")

    model_fingerprints = prediction.get("production_models")
    if model_fingerprints:
        with st.expander("Huellas de los modelos de producción"):
            st.dataframe(
                [
                    {"Modelo": name, "SHA-256": fingerprint}
                    for name, fingerprint in model_fingerprints.items()
                ],
                hide_index=True,
                use_container_width=True,
            )
    else:
        st.caption("Huellas de los modelos de producción: no disponibles")

    warnings = []
    for session in load_snapshot.get("sessions", []):
        warnings.extend(session.get("quality", {}).get("warnings", []))
    if warnings:
        with st.expander(f"Advertencias de cálculo ({len(warnings)})"):
            for warning in warnings:
                st.warning(warning)

    with st.expander("JSON de la predicción"):
        st.code(
            json.dumps(prediction, indent=4, ensure_ascii=False),
            language="json",
        )


def _render_prediction_history():
    """Render filters, archive index and the selected weekly generation."""
    st.divider()
    st.header("Historial de predicciones")
    st.write(
        "Revisa cada semana archivada sin mezclar distintas fechas de simulación "
        "ni ejecuciones del modelo."
    )

    entries, errors = _load_prediction_archive()
    if errors:
        with st.expander(f"Advertencias del archivo ({len(errors)})"):
            for error in errors:
                st.warning(error)
    if not entries:
        st.info("Todavía no hay predicciones archivadas.")
        return

    target_min = min(entry["week_start"] for entry in entries)
    target_max = max(entry["week_start"] for entry in entries)
    generation_min = min(
        entry["generated_at"].date() for entry in entries
    )
    generation_max = max(
        entry["generated_at"].date() for entry in entries
    )

    if st.button(
        "Mostrar todas las predicciones",
        use_container_width=True,
        key="prediction_history_show_all",
    ):
        st.session_state["prediction_history_target_range"] = (
            target_min,
            target_max,
        )
        st.session_state["prediction_history_generation_range"] = (
            generation_min,
            generation_max,
        )
        st.session_state["prediction_history_latest_only"] = False

    target_col, generation_col = st.columns(2)
    with target_col:
        target_range = st.date_input(
            "Inicio de la semana predicha entre",
            value=(target_min, target_max),
            min_value=target_min,
            max_value=target_max,
            key="prediction_history_target_range",
            help=(
                "Fecha histórica de la semana producida por el modelo. Es independiente "
                "de la fecha real en que ejecutaste la predicción."
            ),
        )
    with generation_col:
        generation_range = st.date_input(
            "Archivo generado entre",
            value=(generation_min, generation_max),
            min_value=generation_min,
            max_value=generation_max,
            key="prediction_history_generation_range",
            help=(
                "Fecha real en que se pulsó Generar próxima semana y se creó el JSON archivado."
            ),
        )

    if len(target_range) != 2 or len(generation_range) != 2:
        st.info("Selecciona ambos extremos de cada intervalo de fechas.")
        return

    latest_only = st.checkbox(
        "Mostrar sólo la generación más reciente de cada semana predicha",
        value=False,
        key="prediction_history_latest_only",
    )
    date_filtered = [
        entry
        for entry in entries
        if target_range[0] <= entry["week_start"] <= target_range[1]
        and generation_range[0]
        <= entry["generated_at"].date()
        <= generation_range[1]
    ]
    hidden_by_date = len(entries) - len(date_filtered)
    filtered = date_filtered

    if latest_only:
        latest = {}
        for entry in filtered:
            latest.setdefault(entry["week_start"], entry)
        filtered = list(latest.values())

    hidden_versions = len(date_filtered) - len(filtered)
    if hidden_by_date or hidden_versions:
        messages = []
        if hidden_by_date:
            messages.append(
                f"{hidden_by_date} fuera de los intervalos seleccionados"
            )
        if hidden_versions:
            messages.append(
                f"{hidden_versions} generaciones anteriores ocultas"
            )
        st.info(
            "No se muestran todas las predicciones archivadas: "
            + "; ".join(messages)
            + "."
        )

    if not filtered:
        st.info("Ninguna predicción coincide con los filtros seleccionados.")
        return

    index_rows = []
    for entry in filtered:
        context = entry["prediction"].get("context_weeks") or []
        index_rows.append(
            {
                "Generada": entry["generated_at"],
                "Inicio semana predicha": entry["week_start"],
                "Fin semana predicha": entry["week_end"],
                "Última semana de contexto": context[-1] if context else None,
                "Sesiones": len(entry["prediction"].get("sessions", [])),
                "Carga predicha": entry["weekly_load"],
            }
        )
    st.dataframe(
        index_rows,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Generada": st.column_config.DatetimeColumn(
                format="DD/MM/YYYY HH:mm"
            ),
            "Inicio semana predicha": st.column_config.DateColumn(
                format="DD/MM/YYYY"
            ),
            "Fin semana predicha": st.column_config.DateColumn(
                format="DD/MM/YYYY"
            ),
            "Carga predicha": st.column_config.NumberColumn(format="%.2f"),
        },
    )

    entry_by_id = {
        entry["prediction_id"]: entry for entry in filtered
    }
    selected_id = st.selectbox(
        "Predicción a consultar",
        options=list(entry_by_id),
        format_func=lambda prediction_id: (
            f"{entry_by_id[prediction_id]['week_start'].strftime('%d/%m/%Y')}"
            " – "
            f"{entry_by_id[prediction_id]['week_end'].strftime('%d/%m/%Y')}"
            " · generada "
            f"{entry_by_id[prediction_id]['generated_at'].strftime('%d/%m/%Y %H:%M')}"
        ),
        key="prediction_history_selected_id",
    )
    _render_prediction_archive_detail(entry_by_id[selected_id])


def _latest_prediction_snapshots(predicted_data):
    """
    Select the latest generation for each predicted week.

    Older snapshots stay in predicted_load.json for auditing; only the latest
    one per week is used in the default comparison table.
    """
    latest = {}
    for prediction in (predicted_data or {}).get("predictions", []):
        week_start = prediction.get("week_start")
        if not week_start:
            continue
        generated_at = prediction.get("generated_at") or ""
        current = latest.get(week_start)
        if current is None or generated_at > (current.get("generated_at") or ""):
            latest[week_start] = prediction
    return latest


def _adaptive_approved_loads():
    """Return daily load of the currently active approved adaptive plans."""
    payload, _ = _read_json(ADAPTIVE_PLANS_FILE)
    daily = {}
    for plan in (payload or {}).get("plans", []):
        if plan.get("status") != "active":
            continue
        for session in plan.get("sessions") or []:
            day = session.get("date")
            if not day:
                continue
            try:
                value = float(session.get("estimated_load") or 0)
            except (TypeError, ValueError):
                value = 0.0
            daily[day] = daily.get(day, 0.0) + value
    return daily


def _build_load_rows(prescribed, predicted, performed, adaptive):
    """Join base, approved-adaptive and performed load sources by date."""
    prescribed_days = (prescribed or {}).get("daily_loads", {})
    performed_days = (performed or {}).get("daily_loads", {})
    performed_start = min(performed_days) if performed_days else None
    today = date.today()
    predictions = _latest_prediction_snapshots(predicted)

    predicted_days = {}
    predicted_ids = {}
    for prediction in predictions.values():
        for day, values in prediction.get("daily_loads", {}).items():
            predicted_days[day] = values
            predicted_ids[day] = prediction.get("prediction_id")

    dates = sorted(
        set(prescribed_days) | set(predicted_days) | set(performed_days) | set(adaptive)
    )
    rows = []
    for day in dates:
        calendar_date = date.fromisoformat(day)
        prescribed_day = prescribed_days.get(day, {})
        predicted_day = predicted_days.get(day, {})
        performed_day = performed_days.get(day, {})
        performed_load = performed_day.get("calculated_load")
        performed_status = performed_day.get("quality_status")

        # Once Garmin coverage begins, an elapsed calendar day without an
        # activity represents zero performed load. Future days and dates before
        # the first available Garmin activity remain unknown rather than zero.
        if (
            not performed_day
            and performed_start is not None
            and day >= performed_start
            and calendar_date <= today
        ):
            performed_load = 0.0
            performed_status = "no_activity"

        rows.append(
            {
                "Date": calendar_date,
                "Prescribed": prescribed_day.get("prescribed_load"),
                "Predicted": predicted_day.get("predicted_load"),
                "Adaptive": adaptive.get(day),
                "Performed": performed_load,
                "Prescribed status": prescribed_day.get("quality_status"),
                "Predicted status": predicted_day.get("quality_status"),
                "Performed status": performed_status,
                "Prediction ID": predicted_ids.get(day),
            }
        )
    return rows, predictions


def _build_load_balance_timeline(performed, start_date, end_date):
    """Build the same rolling load balance used by Athlete State."""
    performed_days = (performed or {}).get("daily_loads", {})
    if not performed_days:
        return pd.DataFrame()

    first_date = date.fromisoformat(min(performed_days))
    last_date = min(date.today(), end_date)
    if last_date < first_date:
        return pd.DataFrame()

    # The extra 41 zero-filled days make the 42-day rolling calculation
    # identical to state_builder.load_on(), including the beginning of history.
    calculation_start = first_date - timedelta(days=41)
    dates = pd.date_range(calculation_start, last_date, freq="D")
    daily_load = pd.Series(
        [
            float(
                performed_days.get(day.date().isoformat(), {}).get(
                    "calculated_load"
                )
                or 0
            )
            for day in dates
        ],
        index=dates,
        dtype=float,
    )
    timeline = pd.DataFrame({"Fecha": dates, "Carga diaria": daily_load.values})
    timeline["Carga aguda 7 d"] = daily_load.rolling(7, min_periods=7).sum().values
    timeline["Carga crónica equivalente 42 d"] = (
        daily_load.rolling(42, min_periods=42).sum().values / 6.0
    )
    timeline["Balance de carga"] = (
        timeline["Carga crónica equivalente 42 d"]
        - timeline["Carga aguda 7 d"]
    )
    return timeline[
        (timeline["Fecha"].dt.date >= max(start_date, first_date))
        & (timeline["Fecha"].dt.date <= last_date)
    ].copy()


def _component_table(components):
    """Keep the drill-down compact while retaining the useful calculation."""
    rows = []
    for component in components or []:
        rows.append(
            {
                "Bloque": component.get("source_block_index"),
                "Tipo": display_es(component.get("type")),
                "Zona": component.get("zone"),
                "Duración (min)": component.get("total_duration_minutes"),
                "Fuente de duración": display_es(component.get("duration_source")),
                "Distancia (km)": component.get("total_distance_km"),
                "Factor de deporte": component.get("sport_factor"),
                "Factor de zona": component.get("zone_factor"),
                "Factor de terreno": component.get("terrain_factor"),
                "Carga": component.get("scaled_load"),
            }
        )
    return rows


def _format_duration(seconds):
    """Format Garmin duration seconds for display without changing source data."""
    if not isinstance(seconds, (int, float)):
        return seconds
    total_seconds = max(0, round(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _render_session_detail(title, metadata, load, quality):
    """Render one session/activity without exposing filesystem paths."""
    st.markdown(f"**{title}**")
    field_labels = {
        "sport": "Deporte", "cycling_modality": "Modalidad de ciclismo",
        "garmin_sport": "Deporte Garmin", "garmin_profile": "Perfil Garmin",
        "distance_km": "Distancia (km)", "elapsed_duration_seconds": "Duración total (s)",
        "paused_duration_seconds": "Tiempo pausado (s)", "paused_time_share": "Proporción pausada",
        "ascent_meters": "Desnivel positivo (m)", "descent_meters": "Desnivel negativo (m)",
        "duration": "Duración", "terrain": "Terreno",
        "garmin_training_load": "Carga de entrenamiento Garmin",
        "objective_class": "Objetivo",
        "zone": "Zona", "template": "Entrenamiento",
    }
    metadata_rows = [
        {"Campo": field_labels.get(key, key.replace("_", " ").title()), "Valor": display_es(value)}
        for key, value in metadata.items()
        if value is not None and key != "blocks"
    ]
    if metadata_rows:
        st.dataframe(metadata_rows, hide_index=True, use_container_width=True)

    if load:
        st.metric("Carga calculada", f"{load['calculated_load']:.2f}")
        components = _component_table(load.get("components"))
        if components:
            st.dataframe(
                components,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Distancia (km)": st.column_config.NumberColumn(format="%.2f"),
                    "Carga": st.column_config.NumberColumn(format="%.2f"),
                },
            )

    warnings = (quality or {}).get("warnings", [])
    error = (quality or {}).get("error")
    if warnings:
        for warning in warnings:
            st.warning(warning)
    if error:
        st.error(error)


def _render_load_detail(
    selected_date,
    prescribed,
    predictions,
    performed,
):
    """Show source-specific sessions for the row selected in the table."""
    day = selected_date.isoformat()
    prescribed_sessions = [
        session
        for session in (prescribed or {}).get("sessions", [])
        if session.get("date") == day
    ]
    performed_activities = [
        activity
        for activity in (performed or {}).get("activities", [])
        if activity.get("date") == day
    ]
    predicted_sessions = []
    for prediction in predictions.values():
        if day in prediction.get("daily_loads", {}):
            predicted_sessions.extend(
                session
                for session in prediction.get("sessions", [])
                if session.get("date") == day
            )

    st.subheader(f"Detalle de carga · {day}")
    prescribed_tab, predicted_tab, performed_tab = st.tabs(
        ["Prescrita", "Predicha", "Realizada"]
    )

    with prescribed_tab:
        if not prescribed_sessions:
            st.info("No hay una sesión prescrita para esta fecha.")
        for session in prescribed_sessions:
            _render_session_detail(
                session["session_id"],
                session.get("prescription", {}),
                session.get("load"),
                session.get("quality"),
            )

    with predicted_tab:
        if not predicted_sessions:
            st.info("No hay una predicción del modelo para esta fecha.")
        for session in predicted_sessions:
            _render_session_detail(
                session["session_id"],
                session.get("prediction", {}),
                session.get("load"),
                session.get("quality"),
            )

    with performed_tab:
        if not performed_activities:
            st.info("No hay una actividad Garmin realizada para esta fecha.")
        for activity in performed_activities:
            activity_metadata = activity.get("activity", {})
            metadata = {
                **activity_metadata,
                "duration": _format_duration(
                    activity_metadata.get("duration_seconds")
                ),
                "terrain": activity.get("terrain_inference", {}).get("terrain"),
                "garmin_training_load": activity.get("load", {}).get(
                    "garmin_training_load"
                ),
            }
            metadata.pop("duration_seconds", None)
            _render_session_detail(
                activity["activity_id"],
                metadata,
                activity.get("load"),
                activity.get("quality"),
            )


def render_training_load():
    """Render the daily comparison table and its session drill-down."""
    st.divider()
    st.header("Carga de entrenamiento")
    st.write(
        "Compara la prescripción humana, la predicción del modelo y las actividades "
        "Garmin usando la misma fórmula de carga."
    )

    run_update = st.button("Actualizar carga", use_container_width=True)
    with st.expander("Opciones avanzadas"):
        st.caption(
            "La reconstrucción completa ignora la memoria incremental. Úsala sólo "
            "para auditoría o si sospechas que un dato derivado está desactualizado."
        )
        run_full_rebuild = st.button(
            "Reconstruir toda la carga",
            use_container_width=True,
            key="force_training_load_rebuild",
        )
    if run_update or run_full_rebuild:
        progress = st.progress(0)
        messages = []
        for index, (label, script_name) in enumerate(LOAD_SCRIPTS, start=1):
            result = _rebuild_load(script_name, force=run_full_rebuild)
            output_exists = LOAD_OUTPUTS[label].exists()
            recalculated = reused = None
            if label == "Performed" and output_exists:
                performed_payload, _ = _read_json(LOAD_OUTPUTS[label])
                performed_summary = (performed_payload or {}).get("summary", {})
                recalculated = performed_summary.get("activities_recalculated")
                reused = performed_summary.get("activities_reused")
            messages.append(
                {
                    "Fuente": label,
                    "Resultado generado": output_exists,
                    "Actividades recalculadas": recalculated,
                    "Actividades reutilizadas": reused,
                    "Código": result.returncode,
                }
            )
            progress.progress(index / len(LOAD_SCRIPTS))
        st.dataframe(messages, hide_index=True, use_container_width=True)
        st.caption(
            "En el modo normal, las actividades sin cambios se recuperan de la memoria local."
        )

    datasets = {}
    errors = []
    for label, path in LOAD_OUTPUTS.items():
        datasets[label], error = _read_json(path)
        if error:
            errors.append(f"{label}: {error}")

    if errors:
        st.warning(
            "Algunas fuentes de carga no están disponibles. Usa Actualizar carga "
            "después de procesar sus datos de origen."
        )
        with st.expander("Fuentes no disponibles"):
            for error in errors:
                st.write(error)

    adaptive_loads = _adaptive_approved_loads()
    rows, predictions = _build_load_rows(
        datasets["Prescribed"],
        datasets["Predicted"],
        datasets["Performed"],
        adaptive_loads,
    )
    if not rows:
        st.info("Todavía no hay datos de carga de entrenamiento.")
        return

    minimum_date = rows[0]["Date"]
    maximum_date = rows[-1]["Date"]
    default_start = max(minimum_date, maximum_date - timedelta(days=42))
    start_col, end_col = st.columns(2)
    with start_col:
        start_date = st.date_input(
            "Desde",
            value=default_start,
            min_value=minimum_date,
            max_value=maximum_date,
            key="load_start_date",
        )
    with end_col:
        end_date = st.date_input(
            "Hasta",
            value=maximum_date,
            min_value=minimum_date,
            max_value=maximum_date,
            key="load_end_date",
        )

    if start_date > end_date:
        st.error("La fecha inicial no puede ser posterior a la fecha final.")
        return

    filtered_rows = [
        row for row in rows if start_date <= row["Date"] <= end_date
    ]
    visible_columns = ["Date", "Prescribed", "Predicted", "Adaptive", "Performed"]
    table = pd.DataFrame(filtered_rows)
    complete_table = pd.DataFrame(rows)

    metric_columns = st.columns(4)
    for column, metric_column in zip(
        ("Prescribed", "Predicted", "Adaptive", "Performed"),
        metric_columns,
    ):
        total = table[column].dropna().sum()
        available_days = int(table[column].notna().sum())
        with metric_column:
            source_label = {
                "Prescribed": "Prescrita", "Predicted": "Predicha",
                "Adaptive": "Adaptada aprobada", "Performed": "Realizada",
            }[column]
            st.metric(f"Carga {source_label.lower()} total", f"{total:.1f}")
            st.caption(f"{available_days} días con datos")

    chart_columns = ["Prescribed", "Predicted", "Adaptive", "Performed"]
    chart_color_map = {
        "Prescribed": "#2563EB",
        "Predicted": "#F59E0B",
        "Adaptive": "#8B5CF6",
        "Performed": "#16A34A",
    }
    chart_data = table[["Date", *chart_columns]].copy()
    chart_data["Date"] = pd.to_datetime(chart_data["Date"])

    # Weekly totals always represent complete Monday-Sunday calendar weeks.
    # The date filter selects intersecting weeks but does not truncate their
    # values when the selected range begins or ends in the middle of a week.
    complete_table["Date"] = pd.to_datetime(complete_table["Date"])
    complete_table["Week start"] = (
        complete_table["Date"]
        - pd.to_timedelta(complete_table["Date"].dt.weekday, unit="D")
    )
    weekly_table = (
        complete_table.groupby("Week start", as_index=False)[chart_columns]
        .sum(min_count=1)
    )
    weekly_table["Week end"] = weekly_table["Week start"] + pd.Timedelta(days=6)
    weekly_table = weekly_table[
        (weekly_table["Week end"].dt.date >= start_date)
        & (weekly_table["Week start"].dt.date <= end_date)
    ]

    selected_sources = st.multiselect(
        "Fuentes mostradas en los gráficos",
        options=chart_columns,
        default=chart_columns,
        format_func=lambda value: {
            "Prescribed": "Prescrita", "Predicted": "Predicha",
            "Adaptive": "Adaptada aprobada", "Performed": "Realizada",
        }[value],
        key="training_load_chart_sources",
    )

    balance_timeline = _build_load_balance_timeline(
        datasets["Performed"], start_date, end_date
    )

    if selected_sources:
        source_names_es = {
            "Prescribed": "Prescrita", "Predicted": "Predicha",
            "Adaptive": "Adaptada aprobada", "Performed": "Realizada",
        }
        selected_sources_es = [source_names_es[value] for value in selected_sources]
        chart_data_es = chart_data.rename(columns={"Date": "Fecha", **source_names_es})
        weekly_table_es = weekly_table.rename(columns={
            "Week start": "Inicio de semana", "Week end": "Fin de semana", **source_names_es,
        })
        selected_colors = [
            chart_color_map[source] for source in selected_sources
        ]

        st.subheader("Evolución de la carga")
        st.line_chart(
            chart_data_es,
            x="Fecha",
            y=selected_sources_es,
            x_label="Fecha",
            y_label="Carga de entrenamiento",
            color=selected_colors,
            height=360,
        )

        if not balance_timeline.empty:
            st.subheader("Balance de carga")
            latest_balance = balance_timeline.iloc[-1]
            st.metric(
                "Balance actual",
                f"{latest_balance['Balance de carga']:+.1f}",
                help=(
                    "Carga crónica equivalente de 42 días menos carga aguda "
                    "de 7 días."
                ),
            )
            chart_base = alt.Chart(balance_timeline).encode(
                x=alt.X(
                    "Fecha:T",
                    title="Fecha",
                    axis=alt.Axis(format="%d/%m", labelAngle=0),
                ),
                tooltip=[
                    alt.Tooltip("Fecha:T", title="Fecha", format="%d/%m/%Y"),
                    alt.Tooltip(
                        "Carga aguda 7 d:Q", title="Carga aguda 7 d", format=".1f"
                    ),
                    alt.Tooltip(
                        "Carga crónica equivalente 42 d:Q",
                        title="Carga crónica 42 d",
                        format=".1f",
                    ),
                    alt.Tooltip(
                        "Balance de carga:Q", title="Balance", format="+.1f"
                    ),
                ],
            )
            balance_line = chart_base.mark_line(
                color="#1267E5", strokeWidth=3
            ).encode(
                y=alt.Y("Balance de carga:Q", title="Balance de carga")
            )
            balance_points = chart_base.mark_circle(size=48).encode(
                y=alt.Y("Balance de carga:Q"),
                color=alt.condition(
                    alt.datum["Balance de carga"] >= 0,
                    alt.value("#18A6B8"),
                    alt.value("#FF6B4A"),
                ),
            )
            zero_line = alt.Chart(pd.DataFrame({"zero": [0]})).mark_rule(
                color="#6B7D93", strokeDash=[5, 5], strokeWidth=1
            ).encode(y="zero:Q")
            st.altair_chart(
                (zero_line + balance_line + balance_points).properties(height=300),
                use_container_width=True,
            )
            st.caption(
                "Turquesa: la carga reciente está por debajo de la carga habitual. "
                "Coral: la carga de los últimos 7 días supera la referencia habitual. "
                "El balance es una señal de contexto, no un límite aislado."
            )

        st.subheader("Comparación diaria por fuente")
        st.bar_chart(
            chart_data_es,
            x="Fecha",
            y=selected_sources_es,
            x_label="Fecha",
            y_label="Carga de entrenamiento",
            color=selected_colors,
            stack=False,
            height=420,
        )

        st.subheader("Totales semanales de carga")
        st.line_chart(
            weekly_table_es,
            x="Inicio de semana",
            y=selected_sources_es,
            x_label="Inicio de semana",
            y_label="Carga semanal",
            color=selected_colors,
            height=420,
        )
    else:
        st.info("Selecciona al menos una fuente para mostrar los gráficos.")

    st.subheader("Valores semanales")
    weekly_values = weekly_table[
        ["Week start", "Week end", *chart_columns]
    ].copy()
    weekly_values = weekly_values.rename(columns={
        "Week start": "Inicio de semana", "Week end": "Fin de semana",
        "Prescribed": "Prescrita", "Predicted": "Predicha",
        "Adaptive": "Adaptada aprobada", "Performed": "Realizada",
    })
    st.dataframe(
        weekly_values,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Inicio de semana": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Fin de semana": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Prescrita": st.column_config.NumberColumn(format="%.2f"),
            "Predicha": st.column_config.NumberColumn(format="%.2f"),
            "Realizada": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    st.caption(
        "Las semanas van de lunes a domingo. Los totales realizados incluyen las "
        "actividades Garmin importadas hasta ahora, por lo que la semana actual puede estar incompleta."
    )

    st.subheader("Valores diarios")
    event = st.dataframe(
        table[visible_columns].rename(columns={
            "Date": "Fecha", "Prescribed": "Prescrita",
            "Predicted": "Predicha", "Adaptive": "Adaptada aprobada",
            "Performed": "Realizada",
        }),
        hide_index=True,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        key="training_load_comparison",
        column_config={
            "Fecha": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Prescrita": st.column_config.NumberColumn(format="%.2f"),
            "Predicha": st.column_config.NumberColumn(format="%.2f"),
            "Realizada": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    st.caption("Selecciona una fila para consultar sus sesiones y componentes de carga.")

    selected_rows = event.selection.rows
    if selected_rows:
        selected_date = filtered_rows[selected_rows[0]]["Date"]
        _render_load_detail(
            selected_date,
            datasets["Prescribed"],
            predictions,
            datasets["Performed"],
        )


def _render_legacy(embedded=False):
    # STEP 6 - NEXT WEEK PREDICTION
    # ==========================================================

    st.header("1 · Generar la semana base" if embedded else "Predecir la próxima semana")

    st.write(
        "Genera exclusivamente la semana inmediatamente posterior. La semana base selecciona un bloque "
        "humano similar al estado actual; el contexto actual se incorpora después, en la capa adaptativa."
    )

    try:
        planning = _next_prediction_context()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        planning = None
        st.error(f"No se puede construir un contexto de planificación válido: {error}")
    if planning:
        st.success(
            f"Próxima semana disponible: {planning['target_start'].strftime('%d/%m/%Y')}–"
            f"{planning['target_end'].strftime('%d/%m/%Y')}."
        )
        selection = planning["selection"]
        st.info(
            "Contexto histórico seleccionado: "
            f"{date.fromisoformat(selection['week_start']).strftime('%d/%m/%Y')}–"
            f"{date.fromisoformat(selection['week_end']).strftime('%d/%m/%Y')} "
            f"({selection['similarity_percent']:.1f}% de similitud). "
            "Se compara volumen, distribución por disciplina, zonas, frecuencia y sesiones intensas."
        )
        if selection["decision"] == "progresión_histórica_compatible":
            st.caption("Se avanzó al bloque histórico consecutivo para evitar repetir la misma referencia.")
        elif selection["decision"] == "misma_semana_reutiliza_contexto":
            st.caption("Se conserva el mismo bloque para que regenerar esta semana sea reproducible.")
        if not planning["allowed"]:
            st.info(
                "Ya existe planificación aprobada hasta la próxima semana. Para evitar encadenar "
                "predicciones futuras, la siguiente generación se habilitará cuando corresponda."
            )

    production_model_dir = PROJECT_ROOT / "ML" / "models" / "production"
    required_production_models = [
        "sport_model.pkl",
        "session_type_model.pkl",
        "terrain_model.pkl",
        "zone_model.pkl",
        "objective_class_model.pkl",
        "objective_class_encoder.pkl",
        "objective_class_columns.pkl",
        "distance_trail.pkl",
        "distance_corrida.pkl",
        "distance_bici.pkl",
    ]
    production_ready = all(
        (production_model_dir / filename).exists()
        for filename in required_production_models
    )

    if not production_ready:
        st.warning("Los modelos de producción deben estar disponibles antes de generar la próxima semana.")

    if st.button(
        "Generar próxima semana",
        disabled=not production_ready or planning is None or not planning["allowed"],
        use_container_width=True,
    ):
        with st.spinner("Generando la predicción de la próxima semana..."):
            prediction_result = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "utf8",
                    str(PROJECT_ROOT / "ML" / "predict_next_week.py"),
                ],
                cwd=PROJECT_ROOT / "ML",
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )

        if prediction_result.returncode != 0:
            st.error("Falló la predicción de la próxima semana.")
            with st.expander("Error de predicción", expanded=True):
                st.code(
                    (prediction_result.stdout + "\n" + prediction_result.stderr).strip(),
                    language="text",
                )
        else:
            prediction = json.loads(prediction_result.stdout)
            # The adaptive step lives in the same planning flow. A newly
            # generated base must replace any transient, previously fixed base
            # rather than leaving the selector disabled on an older archive.
            created = sorted(
                PREDICTIONS_DIR.glob(f"prediction_{prediction['week_start']}_*.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            if created:
                st.session_state["adaptive_base_prediction_id"] = created[0].stem
                st.session_state.pop("adaptive_base_fixed", None)
                st.session_state.pop("adaptive_prediction_selector", None)
                st.session_state.pop("adaptive_proposal", None)
                st.session_state.pop("adaptive_decision", None)
            st.success(
                f"Predicción generada para el período {prediction['week_start']} "
                f"a {prediction['week_end']}."
            )

            rows = []
            for session in prediction["sessions"]:
                workout = session.get("template")
                if workout is None and session.get("distance_km") is not None:
                    workout = f"{_compact_number(session['distance_km'])} km"
                if workout is None and session["sport"] == "Descanso":
                    workout = "Descanso"

                rows.append(
                    {
                        "Fecha": session["date"],
                        "Día": display_es(session["weekday"]),
                        "Deporte": display_es(session["sport"]),
                        "Objetivo": display_es(session.get("objective_class")),
                        "Terreno": display_es(session.get("terrain")),
                        "Zona": session.get("zone"),
                        "Duración (min)": _compact_number(session.get("duration_min"), 0),
                        "Distancia (km)": _compact_number(session.get("distance_km")),
                        "Entrenamiento": display_es(workout),
                    }
                )

            st.table(rows)
            st.caption(
                "Semanas de contexto: " + ", ".join(prediction["context_weeks"])
            )

            prediction_json = json.dumps(prediction, indent=4, ensure_ascii=False)

            with st.expander("JSON de la predicción"):
                st.code(prediction_json, language="json")

            # Keep the derived prediction-load collection synchronized with
            # the newly archived prediction before rendering the comparison.
            _rebuild_load("predicted_load.py")

            # Streamlit preserves widget values between reruns. Clear history
            # filters so the new target week is immediately included instead
            # of remaining hidden behind the previous date range.
            for key in (
                "prediction_history_target_range",
                "prediction_history_generation_range",
                "prediction_history_selected_id",
            ):
                st.session_state.pop(key, None)

    _render_prediction_history()


def _candidate_rows(candidate):
    """Compact and comparable description of a base-week alternative."""
    sessions = candidate.get("sessions", [])
    active = [session for session in sessions if session.get("sport") != "Descanso"]
    sports = {}
    intense = 0
    for session in active:
        sport = display_es(session.get("sport"))
        sports[sport] = sports.get(sport, 0) + 1
        blocks = session.get("blocks") or []
        if any(str(block.get("zone") or "").upper() in {"Z4", "Z5", "Z6"} for block in blocks if isinstance(block, dict)):
            intense += 1
    style = candidate.get("style_score") or {}
    selection = candidate.get("context_selection") or {}
    return {
        "Alternativa": candidate.get("alternative_rank"),
        "Estilo histórico": style.get("global_score"),
        "Similitud de contexto": selection.get("similarity_percent"),
        "Bloque humano de referencia": (
            f"{selection.get('week_start', '—')} a {selection.get('week_end', '—')}"
        ),
        "Sesiones": len(active),
        "Deportes": " · ".join(f"{sport}: {count}" for sport, count in sports.items()) or "Descanso",
        "Sesiones Z4–Z6": intense,
    }


def _render_candidate_week(candidate):
    rows = []
    for session in candidate.get("sessions", []):
        workout = session.get("template") or _prediction_workout(session)
        rows.append({
            "Fecha": session.get("date"),
            "Día": display_es(session.get("weekday")),
            "Deporte": display_es(session.get("sport")),
            "Terreno": display_es(session.get("terrain")),
            "Zona": session.get("zone") or "—",
            "Entrenamiento": display_es(workout),
        })
    st.dataframe(rows, hide_index=True, use_container_width=True)
    components = (candidate.get("style_score") or {}).get("components") or {}
    if components:
        with st.expander("Detalle del score de estilo"):
            st.dataframe(
                [{"Dimensión": display_es(name), "Score": value} for name, value in components.items()],
                hide_index=True,
                use_container_width=True,
                column_config={"Score": st.column_config.NumberColumn(format="%.1f")},
            )


def render(embedded=False):
    st.header("1 · Generar la semana base" if embedded else "Predecir la próxima semana")
    st.write(
        "El modelo propone alternativas desde bloques de cuatro semanas de planillas humanas "
        "similares a lo que realmente has realizado. Elige una semana base antes de editarla "
        "o aplicar la capa adaptativa."
    )
    try:
        planning = _next_prediction_context()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        planning = None
        st.error(f"No se puede construir un contexto de planificación válido: {error}")

    if planning:
        st.success(
            f"Próxima semana disponible: {planning['target_start'].strftime('%d/%m/%Y')}–"
            f"{planning['target_end'].strftime('%d/%m/%Y')}."
        )
        st.caption(
            "Las alternativas usan únicamente semanas realmente realizadas como contexto. "
            "Las semanas predichas o adaptadas no retroalimentan el modelo."
        )
        stored = st.session_state.get("base_week_candidates")
        if stored and stored[0].get("week_start") != planning["target_start"].isoformat():
            st.session_state.pop("base_week_candidates", None)

    production_model_dir = PROJECT_ROOT / "ML" / "models" / "production"
    required_models = [
        "sport_model.pkl", "session_type_model.pkl", "terrain_model.pkl", "zone_model.pkl",
        "objective_class_model.pkl", "objective_class_encoder.pkl", "objective_class_columns.pkl",
        "distance_trail.pkl", "distance_corrida.pkl", "distance_bici.pkl",
    ]
    ready = all((production_model_dir / filename).exists() for filename in required_models)
    if not ready:
        st.warning("Los modelos de producción deben estar disponibles antes de generar alternativas.")

    if st.button(
        "Generar alternativas de semana base",
        disabled=not ready or planning is None or not planning["allowed"],
        use_container_width=True,
        key="generate_base_week_alternatives",
    ):
        with st.spinner("Generando y evaluando alternativas contra el estilo histórico..."):
            try:
                st.session_state["base_week_candidates"] = build_prediction_candidates(limit=5)
            except Exception as error:
                st.error(f"No se pudieron generar las alternativas: {error}")

    candidates = st.session_state.get("base_week_candidates") or []
    if candidates:
        st.subheader("Alternativas de semana base")
        policy = candidates[0].get("selection_policy") or {}
        if policy:
            st.info(
                "Selección: el contexto recupera bloques cercanos y el estilo humano decide el orden final. "
                f"Se evaluaron {policy.get('contexts_close', 0)} bloques cercanos de "
                f"{policy.get('contexts_retrieved', 0)} recuperados "
                f"(similitud mínima: {policy.get('context_similarity_floor_percent', 0):.1f}%)."
            )
        st.caption(
            "El score de estilo reutiliza el cálculo de evaluación de modelos y compara cada "
            "alternativa con las últimas 30 semanas humanas disponibles. Mide adherencia al estilo, "
            "no una garantía de resultado fisiológico. Se excluyen alternativas bajo 70%. "
            "Además, se descarta una alternativa si reproduce exactamente alguna de las cuatro "
            "últimas semanas base predichas."
        )
        discarded_duplicates = max(
            (candidate.get("novelty") or {}).get("discarded_exact_duplicates", 0)
            for candidate in candidates
        )
        if discarded_duplicates:
            st.info(
                f"Se descartaron {discarded_duplicates} alternativa(s) idéntica(s) a una semana "
                "base reciente antes de mostrar estas opciones."
            )
        rows = [_candidate_rows(candidate) for candidate in candidates]
        st.dataframe(
            rows,
            hide_index=True,
            use_container_width=True,
            column_config={
                "Estilo histórico": st.column_config.NumberColumn(format="%.1f%%"),
                "Similitud de contexto": st.column_config.NumberColumn(format="%.1f%%"),
            },
        )
        index_by_rank = {candidate["alternative_rank"]: candidate for candidate in candidates}
        selected_rank = st.radio(
            "Semana base a revisar",
            options=list(index_by_rank),
            horizontal=True,
            format_func=lambda rank: f"Alternativa {rank} · estilo {index_by_rank[rank]['style_score']['global_score']:.1f}%",
            key="base_week_candidate_choice",
        )
        selected = index_by_rank[selected_rank]
        st.markdown(f"#### Alternativa {selected_rank}")
        _render_candidate_week(selected)

        if st.button(
            "Usar esta alternativa como semana base",
            type="primary",
            use_container_width=True,
            key="save_selected_base_week_alternative",
        ):
            save_prediction_files(selected)
            created = sorted(
                PREDICTIONS_DIR.glob(f"prediction_{selected['week_start']}_*.json"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            if created:
                st.session_state["adaptive_base_prediction_id"] = created[0].stem
                st.session_state.pop("adaptive_base_fixed", None)
                st.session_state.pop("adaptive_prediction_selector", None)
                st.session_state.pop("adaptive_proposal", None)
                st.session_state.pop("adaptive_decision", None)
            st.session_state.pop("base_week_candidates", None)
            _rebuild_load("predicted_load.py")
            st.success("Semana base guardada. Continúa con Ajustes manuales o Adaptación y plan definitivo.")

    _render_prediction_history()
