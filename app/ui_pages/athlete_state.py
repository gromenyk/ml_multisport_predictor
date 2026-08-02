"""Athlete-state dashboard and manual wellness entry."""

from __future__ import annotations

from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from athlete_state.state_builder import build_athlete_state, save_snapshot
from adaptive_planning.session_feedback_store import (
    delete_session_feedback,
    load_session_feedback,
    save_session_feedback,
)
from adaptive_planning.workflow_store import load_active_plans
from backend.wellness_store import (
    delete_wellness,
    load_availability,
    load_context,
    load_wellness,
    merge_garmin_sleep_rows,
    merge_garmin_weight_rows,
    parse_garmin_sleep_csv,
    parse_garmin_weight_csv,
    save_context,
    save_availability_calendar,
    save_wellness,
)


GARMIN_SLEEP_QUALITY_LABELS = {
    None: "No disponible",
    "poor": "Deficiente",
    "fair": "Aceptable",
    "good": "Bueno",
    "excellent": "Excelente",
}

# Shared time-series convention: observations/reference in light blue and the
# short-term seven-record/day signal in strong blue.
OBSERVATION_COLOR = "#7DD3FC"
SEVEN_DAY_COLOR = "#1D4ED8"


def _rerun():
    if hasattr(st, "rerun"):
        st.rerun()
    st.experimental_rerun()


def _text_number(value):
    return "" if value is None else str(value)


def _parse_optional(value):
    text = (value or "").strip().replace(",", ".")
    return None if not text else float(text)


def _fmt(value, digits=1, suffix=""):
    return "—" if value is None else f"{value:.{digits}f}{suffix}"


def _wellness_charts(records):
    if not records:
        st.info("Agrega registros para visualizar tendencias de recuperación.")
        return
    frame = pd.DataFrame(records).copy()
    frame["Fecha"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values("Fecha").set_index("Fecha")
    columns = (
        "sleep_duration_hours", "sleep_score", "hrv_ms", "resting_hr_bpm",
        "subjective_fatigue", "muscle_soreness", "motivation", "sleep_quality",
    )
    for column in columns:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")

    st.markdown("#### Sueño")
    left, right = st.columns(2)
    if "sleep_duration_hours" in frame and frame["sleep_duration_hours"].notna().any():
        left.line_chart(
            pd.DataFrame(
                {
                    "Horas": frame["sleep_duration_hours"],
                    "Promedio 7 registros": frame["sleep_duration_hours"].rolling(7, min_periods=2).mean(),
                }
            ),
            color=[OBSERVATION_COLOR, SEVEN_DAY_COLOR],
        )
    else:
        left.info("Sin duración de sueño disponible.")
    if "sleep_score" in frame and frame["sleep_score"].notna().any():
        right.line_chart(
            pd.DataFrame(
                {
                    "Sleep Score": frame["sleep_score"],
                    "Promedio 7 registros": frame["sleep_score"].rolling(7, min_periods=2).mean(),
                }
            ),
            color=[OBSERVATION_COLOR, SEVEN_DAY_COLOR],
        )
    else:
        right.info("Sin Sleep Score numérico disponible.")

    st.markdown("#### Señales cardiovasculares de recuperación")
    left, right = st.columns(2)
    if "hrv_ms" in frame and frame["hrv_ms"].notna().any():
        left.line_chart(
            pd.DataFrame(
                {
                    "VFC / HRV (ms)": frame["hrv_ms"],
                    "Promedio 7 registros": frame["hrv_ms"].rolling(7, min_periods=2).mean(),
                }
            ),
            color=[OBSERVATION_COLOR, SEVEN_DAY_COLOR],
        )
    else:
        left.info("Sin VFC disponible.")
    if "resting_hr_bpm" in frame and frame["resting_hr_bpm"].notna().any():
        right.line_chart(
            pd.DataFrame(
                {
                    "FC reposo (bpm)": frame["resting_hr_bpm"],
                    "Promedio 7 registros": frame["resting_hr_bpm"].rolling(7, min_periods=2).mean(),
                }
            ),
            color=[OBSERVATION_COLOR, SEVEN_DAY_COLOR],
        )
    else:
        right.info("Sin FC en reposo disponible.")

    subjective = {
        "subjective_fatigue": "Fatiga",
        "muscle_soreness": "Dolor muscular",
        "motivation": "Motivación",
        "sleep_quality": "Calidad subjetiva del sueño",
    }
    available = [key for key in subjective if key in frame and frame[key].notna().any()]
    st.markdown("#### Percepción subjetiva")
    if available:
        st.line_chart(frame[available].rename(columns=subjective))
        st.caption("Escala 1–10. En fatiga y dolor, valores altos son menos favorables.")
    else:
        st.info("Sin registros subjetivos disponibles.")


def _physical_chart(frame, field, title, unit, digits=1):
    values = frame[["Fecha", field]].dropna().copy()
    if values.empty:
        st.info(f"Sin datos de {title.lower()} disponibles.")
        return
    values["Promedio 7 registros"] = values[field].rolling(7, min_periods=2).mean()
    observation = alt.Chart(values).mark_line(
        color=OBSERVATION_COLOR,
        point=alt.OverlayMarkDef(color=OBSERVATION_COLOR, size=42),
        strokeWidth=1.5,
    ).encode(
        x=alt.X("Fecha:T", axis=alt.Axis(title=None, format="%d %b")),
        y=alt.Y(f"{field}:Q", axis=alt.Axis(title=f"{title} ({unit})"), scale=alt.Scale(zero=False)),
        tooltip=[
            alt.Tooltip("Fecha:T", title="Fecha", format="%d/%m/%Y"),
            alt.Tooltip(f"{field}:Q", title=title, format=f".{digits}f"),
        ],
    )
    trend = alt.Chart(values).mark_line(
        color=SEVEN_DAY_COLOR,
        strokeWidth=3,
    ).encode(
        x=alt.X("Fecha:T"),
        y=alt.Y("Promedio 7 registros:Q"),
        tooltip=[
            alt.Tooltip("Fecha:T", title="Fecha", format="%d/%m/%Y"),
            alt.Tooltip(
                "Promedio 7 registros:Q",
                title="Promedio 7 registros",
                format=f".{digits}f",
            ),
        ],
    )
    st.altair_chart(
        (observation + trend).properties(title=title, height=300),
        use_container_width=True,
    )


def _physical_charts(records):
    if not records:
        return
    frame = pd.DataFrame(records).copy()
    frame["Fecha"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values("Fecha")
    for field in ("weight_kg", "body_fat_percent"):
        if field not in frame:
            frame[field] = None
        frame[field] = pd.to_numeric(frame[field], errors="coerce")

    latest_weight = frame.dropna(subset=["weight_kg"])
    latest_fat = frame.dropna(subset=["body_fat_percent"])
    metrics = st.columns(3)
    metrics[0].metric(
        "Peso actual",
        _fmt(latest_weight.iloc[-1]["weight_kg"], 1, " kg") if not latest_weight.empty else "—",
    )
    metrics[1].metric(
        "Promedio últimos 7 registros",
        _fmt(latest_weight.tail(7)["weight_kg"].mean(), 1, " kg") if not latest_weight.empty else "—",
    )
    metrics[2].metric(
        "Grasa corporal actual",
        _fmt(latest_fat.iloc[-1]["body_fat_percent"], 1, "%") if not latest_fat.empty else "—",
    )
    left, right = st.columns(2)
    with left:
        _physical_chart(frame, "weight_kg", "Peso", "kg")
    with right:
        _physical_chart(frame, "body_fat_percent", "Grasa corporal", "%")
    st.caption("Celeste: medición individual · azul fuerte: promedio móvil de 7 registros.")


def _wellness_form():
    records = load_wellness()
    st.markdown("### Importar sueño desde Garmin")
    uploaded = st.file_uploader(
        "CSV de sueño Garmin",
        type=("csv",),
        key="garmin_sleep_csv",
        help=(
            "Puedes cargar semanalmente un reporte completo. Solo se agregan "
            "fechas nuevas o campos Garmin que todavía estén vacíos."
        ),
    )
    if uploaded is not None:
        try:
            parsed = parse_garmin_sleep_csv(uploaded.getvalue(), uploaded.name)
            preview = merge_garmin_sleep_rows(parsed["rows"], commit=False)
        except ValueError as error:
            st.error(f"No se pudo leer el CSV: {error}")
        else:
            rows = parsed["rows"]
            columns = st.columns(4)
            columns[0].metric("Filas detectadas", len(rows))
            columns[1].metric("Fecha más reciente guardada", preview["latest_before"] or "—")
            columns[2].metric("Fechas nuevas", preview["inserted"])
            columns[3].metric("Fechas a completar", preview["enriched"])
            st.caption(
                f"Rango detectado: {rows[0]['date']} a {rows[-1]['date']} · "
                f"sin cambios: {preview['skipped']} · "
                f"noches sin registro omitidas: {parsed['empty_rows']} · "
                f"conflictos preservados: {len(preview['conflicts'])}."
            )
            for warning in parsed["warnings"]:
                st.warning(warning)
            preview_frame = pd.DataFrame(rows).rename(
                columns={
                    "date": "Fecha",
                    "sleep_score": "Sleep Score",
                    "resting_hr_bpm": "FC reposo",
                    "hrv_ms": "VFC",
                    "garmin_sleep_quality": "Calidad Garmin",
                    "sleep_duration_hours": "Sueño (h)",
                    "bed_time": "Hora dormir",
                    "wake_time": "Hora despertar",
                }
            )
            if "Calidad Garmin" in preview_frame:
                preview_frame["Calidad Garmin"] = preview_frame["Calidad Garmin"].map(
                    GARMIN_SLEEP_QUALITY_LABELS
                )
            st.dataframe(
                preview_frame[
                    [
                        "Fecha", "Sleep Score", "FC reposo", "VFC",
                        "Calidad Garmin", "Sueño (h)", "Hora dormir", "Hora despertar",
                    ]
                ],
                use_container_width=True,
                hide_index=True,
                height=280,
            )
            if preview["conflicts"]:
                with st.expander("Ver conflictos que no se sobrescribirán"):
                    st.dataframe(
                        pd.DataFrame(preview["conflicts"]),
                        use_container_width=True,
                        hide_index=True,
                    )
            if st.button(
                "Importar datos faltantes",
                use_container_width=True,
                key="confirm_garmin_sleep_import",
                disabled=preview["inserted"] == 0 and preview["enriched"] == 0,
            ):
                result = merge_garmin_sleep_rows(rows, commit=True)
                st.success(
                    f"Importación completa: {result['inserted']} fechas nuevas y "
                    f"{result['enriched']} fechas completadas."
                )
                _rerun()

    st.divider()
    st.markdown("### Ingreso o edición manual")
    record_date = st.date_input("Fecha del registro", value=date.today(), key="wellness_date")
    existing = next((item for item in records if item["date"] == record_date.isoformat()), {})
    if existing:
        st.caption("Ya existe un registro para esta fecha. Guardar lo actualizará.")
    with st.form(f"wellness_form_{record_date.isoformat()}"):
        st.markdown("#### Sueño y señales Garmin")
        columns = st.columns(5)
        sleep_duration = columns[0].text_input(
            "Sueño (horas)", value=_text_number(existing.get("sleep_duration_hours"))
        )
        sleep_score = columns[1].text_input(
            "Sleep score (0–100)", value=_text_number(existing.get("sleep_score"))
        )
        quality_options = tuple(GARMIN_SLEEP_QUALITY_LABELS)
        current_quality = existing.get("garmin_sleep_quality")
        garmin_sleep_quality = columns[2].selectbox(
            "Calidad Garmin",
            quality_options,
            index=quality_options.index(current_quality) if current_quality in quality_options else 0,
            format_func=lambda item: GARMIN_SLEEP_QUALITY_LABELS[item],
            help="Si ingresas Sleep Score, la categoría se calculará automáticamente.",
        )
        hrv = columns[3].text_input(
            "VFC nocturna / HRV (ms)", value=_text_number(existing.get("hrv_ms"))
        )
        resting_hr = columns[4].text_input(
            "FC en reposo (bpm)", value=_text_number(existing.get("resting_hr_bpm"))
        )
        st.markdown("#### Percepción subjetiva")
        columns = st.columns(4)
        fatigue = columns[0].text_input(
            "Fatiga (1–10)", value=_text_number(existing.get("subjective_fatigue"))
        )
        soreness = columns[1].text_input(
            "Dolor muscular (1–10)", value=_text_number(existing.get("muscle_soreness"))
        )
        motivation = columns[2].text_input(
            "Motivación (1–10)", value=_text_number(existing.get("motivation"))
        )
        sleep_quality = columns[3].text_input(
            "Calidad subjetiva del sueño (1–10)",
            value=_text_number(existing.get("sleep_quality")),
        )
        discomfort = st.checkbox(
            "Tengo una molestia, dolor localizado o restricción",
            value=bool(existing.get("has_discomfort", False)),
        )
        notes = st.text_area("Notas", value=existing.get("notes", ""))
        submitted = st.form_submit_button("Guardar bienestar", use_container_width=True)
    if submitted:
        try:
            save_wellness(
                {
                    "date": record_date.isoformat(),
                    "sleep_duration_hours": _parse_optional(sleep_duration),
                    "sleep_score": _parse_optional(sleep_score),
                    "garmin_sleep_quality": garmin_sleep_quality,
                    "sleep_quality": _parse_optional(sleep_quality),
                    "hrv_ms": _parse_optional(hrv),
                    "resting_hr_bpm": _parse_optional(resting_hr),
                    "subjective_fatigue": _parse_optional(fatigue),
                    "muscle_soreness": _parse_optional(soreness),
                    "motivation": _parse_optional(motivation),
                    "has_discomfort": discomfort,
                    "notes": notes,
                }
            )
        except ValueError as error:
            st.error(str(error))
        else:
            st.success("Registro guardado.")
            _rerun()

    if records:
        _wellness_charts(records)
        st.markdown("#### Historial")
        frame = pd.DataFrame(records)
        visible = [
            "date", "sleep_duration_hours", "sleep_score", "garmin_sleep_quality", "hrv_ms",
            "resting_hr_bpm", "bed_time", "wake_time",
            "subjective_fatigue", "muscle_soreness", "motivation",
            "has_discomfort", "notes",
        ]
        if "garmin_sleep_quality" in frame:
            frame["garmin_sleep_quality"] = frame["garmin_sleep_quality"].map(
                GARMIN_SLEEP_QUALITY_LABELS
            )
        st.dataframe(
            frame[[column for column in visible if column in frame]],
            use_container_width=True,
            hide_index=True,
            height=360,
        )
        to_delete = st.selectbox(
            "Registro para eliminar",
            [item["date"] for item in reversed(records)],
            key="wellness_delete_date",
        )
        if st.button("Eliminar registro seleccionado", key="delete_wellness"):
            delete_wellness(to_delete)
            _rerun()


def _context_form():
    records = load_context()
    st.markdown("### Peso")
    uploaded_weight = st.file_uploader(
        "CSV de peso Garmin",
        type=("csv",),
        key="garmin_weight_csv",
        help="Puedes cargar el reporte completo; solo se agregan datos faltantes.",
    )
    if uploaded_weight is not None:
        try:
            parsed_weight = parse_garmin_weight_csv(
                uploaded_weight.getvalue(), uploaded_weight.name
            )
            weight_preview = merge_garmin_weight_rows(parsed_weight["rows"], commit=False)
        except ValueError as error:
            st.error(f"No se pudo leer el CSV: {error}")
        else:
            weight_rows = parsed_weight["rows"]
            columns = st.columns(4)
            columns[0].metric("Mediciones detectadas", len(weight_rows))
            columns[1].metric(
                "Última fecha guardada", weight_preview["latest_before"] or "—"
            )
            columns[2].metric("Fechas nuevas", weight_preview["inserted"])
            columns[3].metric("Fechas a completar", weight_preview["enriched"])
            st.caption(
                f"Rango: {weight_rows[0]['date']} a {weight_rows[-1]['date']} · "
                f"fechas sin medición omitidas: {parsed_weight['dates_without_measurement']} · "
                f"sin cambios: {weight_preview['skipped']} · "
                f"conflictos preservados: {len(weight_preview['conflicts'])}."
            )
            preview_frame = pd.DataFrame(weight_rows).rename(
                columns={
                    "date": "Fecha",
                    "measurement_time": "Hora",
                    "weight_kg": "Peso (kg)",
                    "body_fat_percent": "Grasa corporal (%)",
                }
            )
            st.dataframe(
                preview_frame[["Fecha", "Hora", "Peso (kg)", "Grasa corporal (%)"]],
                use_container_width=True,
                hide_index=True,
                height=260,
            )
            if weight_preview["conflicts"]:
                with st.expander("Ver conflictos que no se sobrescribirán"):
                    st.dataframe(
                        pd.DataFrame(weight_preview["conflicts"]),
                        use_container_width=True,
                        hide_index=True,
                    )
            if st.button(
                "Importar datos físicos faltantes",
                use_container_width=True,
                key="confirm_garmin_weight_import",
                disabled=(
                    weight_preview["inserted"] == 0
                    and weight_preview["enriched"] == 0
                ),
            ):
                result = merge_garmin_weight_rows(weight_rows, commit=True)
                st.success(
                    f"Importación completa: {result['inserted']} fechas nuevas y "
                    f"{result['enriched']} fechas completadas."
                )
                _rerun()

    st.markdown("#### Ingreso o edición manual")
    context_date = st.date_input("Fecha del registro", value=date.today(), key="context_date")
    existing = next((item for item in records if item["date"] == context_date.isoformat()), {})
    with st.form(f"context_form_{context_date.isoformat()}"):
        columns = st.columns(2)
        weight = columns[0].text_input(
            "Peso (kg)", value=_text_number(existing.get("weight_kg"))
        )
        body_fat = columns[1].text_input(
            "Grasa corporal (%)",
            value=_text_number(existing.get("body_fat_percent")),
        )
        submitted = st.form_submit_button("Guardar peso", use_container_width=True)
    if submitted:
        try:
            save_context(
                {
                    "date": context_date.isoformat(),
                    "weight_kg": _parse_optional(weight),
                    "body_fat_percent": _parse_optional(body_fat),
                }
            )
        except ValueError as error:
            st.error(str(error))
        else:
            st.success("Peso guardado.")
            _rerun()
    if records:
        st.markdown("#### Evolución física")
        _physical_charts(records)
        st.markdown("#### Historial")
        weight_frame = pd.DataFrame(records).rename(
            columns={
                "date": "Fecha",
                "measurement_time": "Hora",
                "weight_kg": "Peso (kg)",
                "body_fat_percent": "Grasa corporal (%)",
            }
        )
        for column in ("Hora", "Peso (kg)", "Grasa corporal (%)"):
            if column not in weight_frame:
                weight_frame[column] = None
        st.dataframe(
            weight_frame[["Fecha", "Hora", "Peso (kg)", "Grasa corporal (%)"]],
            use_container_width=True,
            hide_index=True,
            height=220,
        )

    st.divider()
    st.markdown("### Disponibilidad horaria futura")
    st.caption(
        "Libre permite que el sistema asigne la carga que estime conveniente. "
        "No disponible equivale a 0 minutos. También puedes fijar un máximo diario."
    )
    today = date.today()
    next_monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    selected_date = st.date_input(
        "Semana a planificar",
        value=next_monday,
        min_value=next_monday,
        key="availability_week",
    )
    week_start = selected_date - timedelta(days=selected_date.weekday())
    explicit = {item["date"]: item for item in load_availability()}
    weekdays = ("Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo")
    options = ("Libre", "No disponible (0 min)", "Tiempo limitado")
    availability_values = []
    with st.form(f"availability_form_{week_start.isoformat()}"):
        day_columns = st.columns(7)
        for offset, column in enumerate(day_columns):
            calendar_day = week_start + timedelta(days=offset)
            stored = explicit.get(calendar_day.isoformat())
            current_option = (
                "Libre" if stored is None
                else "No disponible (0 min)" if stored.get("max_minutes") == 0
                else "Tiempo limitado"
            )
            with column:
                st.markdown(f"**{weekdays[offset]}**  \n{calendar_day.strftime('%d/%m')}")
                choice = st.selectbox(
                    "Disponibilidad",
                    options,
                    index=options.index(current_option),
                    key=f"availability_status_{calendar_day.isoformat()}",
                    label_visibility="collapsed",
                )
                limit_minutes = st.number_input(
                    "Máximo si se limita (min)",
                    min_value=1,
                    max_value=1440,
                    value=(
                        int(stored["max_minutes"])
                        if stored and float(stored.get("max_minutes") or 0) > 0
                        else 60
                    ),
                    step=15,
                    key=f"availability_minutes_{calendar_day.isoformat()}",
                )
                minutes = (
                    limit_minutes if choice == "Tiempo limitado"
                    else 0 if choice == "No disponible (0 min)"
                    else None
                )
                availability_values.append(
                    {"date": calendar_day.isoformat(), "max_minutes": minutes}
                )
        availability_submitted = st.form_submit_button(
            "Guardar disponibilidad de la semana",
            use_container_width=True,
        )
    if availability_submitted:
        save_availability_calendar(availability_values)
        st.success("Disponibilidad guardada.")
        _rerun()


def _summary(state):
    training = state["training"]
    recovery = state["recovery"]
    interpretation = state["interpretation"]
    columns = st.columns(4)
    columns[0].metric(
        "Readiness descriptivo",
        interpretation["readiness_label"].capitalize(),
        _fmt(interpretation.get("readiness_score"), 0, "/100"),
    )
    columns[1].metric("Carga aguda 7d", _fmt(training["acute_load_7d"], 0))
    columns[2].metric(
        "Carga habitual 42d",
        _fmt(training["chronic_load_42d_equivalent"], 0),
    )
    columns[3].metric(
        "Completitud recuperación",
        _fmt(recovery["data_completeness"] * 100, 0, "%"),
    )
    st.caption(interpretation["note"])
    left, right = st.columns(2)
    with left:
        st.markdown("#### Factores favorables")
        if interpretation["positive_factors"]:
            for item in interpretation["positive_factors"]:
                st.success(item)
        else:
            st.info("No hay suficientes señales favorables registradas.")
    with right:
        st.markdown("#### Factores limitantes")
        if interpretation["limiting_factors"]:
            for item in interpretation["limiting_factors"]:
                st.warning(item)
        else:
            st.info("No hay señales limitantes suficientes o disponibles.")

    latest = recovery.get("latest") or {}
    st.markdown("#### Últimos datos de recuperación")
    recovery_values = {
        "Fecha": latest.get("date", "—"),
        "Sueño": _fmt(latest.get("sleep_duration_hours"), 1, " h"),
        "Calidad Garmin": GARMIN_SLEEP_QUALITY_LABELS.get(
            latest.get("garmin_sleep_quality"), "No disponible"
        ),
        "HRV": _fmt(latest.get("hrv_ms"), 1, " ms"),
        "FC reposo": _fmt(latest.get("resting_hr_bpm"), 0, " bpm"),
        "Fatiga": _fmt(latest.get("subjective_fatigue"), 0, "/10"),
    }
    st.dataframe(pd.DataFrame([recovery_values]), use_container_width=True, hide_index=True)

    st.markdown("#### Contexto de planificación")
    context = state["context"]
    next_event = context.get("next_event") or {}
    manual_context = context.get("latest_manual_context") or {}
    st.write(
        f"Próximo evento: **{next_event.get('name', '—')}** · "
        f"días restantes: **{context.get('days_to_next_event', '—')}** · "
        f"peso vigente: **{_fmt(manual_context.get('weight_kg'), 1, ' kg')}** · "
        f"grasa corporal: **{_fmt(manual_context.get('body_fat_percent'), 1, '%')}**"
    )
    active_names = [item["name"] for item in context.get("active_windows", [])]
    st.caption(
        "Ventanas activas: " + (" · ".join(active_names) if active_names else "ninguna")
    )

    if st.button("Guardar snapshot reproducible", use_container_width=True):
        path = save_snapshot(state)
        st.success(f"Snapshot guardado: {path.name}")


def _training_tab(state):
    training = state["training"]
    columns = st.columns(4)
    columns[0].metric("Balance de carga", _fmt(training["load_balance"], 0))
    columns[1].metric(
        "Cambio vs semana previa",
        _fmt(
            training["ramp_rate_7d"] * 100 if training["ramp_rate_7d"] is not None else None,
            1,
            "%",
        ),
    )
    columns[2].metric("Monotonía 7d", _fmt(training["monotony_7d"], 2))
    columns[3].metric("Días desde intensidad", _fmt(training["days_since_intensity"], 0))
    st.markdown("#### Evolución de carga · 90 días")
    timeline = pd.DataFrame(training["load_timeline_90d"])
    timeline["date"] = pd.to_datetime(timeline["date"])
    timeline = timeline.set_index("date").rename(
        columns={
            "acute_load_7d": "Carga aguda 7d",
            "chronic_load_42d_equivalent": "Carga habitual 42d",
        }
    )
    st.line_chart(
        timeline[["Carga aguda 7d", "Carga habitual 42d"]],
        color=[SEVEN_DAY_COLOR, OBSERVATION_COLOR],
    )
    st.caption(
        "Ambas líneas son cargas semanales equivalentes. No constituyen por "
        "sí solas una estimación de riesgo."
    )
    st.markdown("#### Carga diaria · últimos 7 días")
    daily = pd.DataFrame(training["daily_load_7d"])
    daily["date"] = pd.to_datetime(daily["date"])
    weekday_labels = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")
    daily["Día"] = daily["date"].map(
        lambda value: f"{weekday_labels[value.weekday()]} {value.day:02d}"
    )
    daily["Carga"] = daily["load"].astype(float)
    habitual_daily_load = float(training["chronic_load_42d_equivalent"] or 0) / 7.0

    def load_level(value):
        if value <= 0:
            return "Descanso"
        if value <= habitual_daily_load * 0.75:
            return "Baja"
        if value <= habitual_daily_load * 1.25:
            return "Habitual"
        if value <= habitual_daily_load * 1.75:
            return "Alta"
        return "Muy alta"

    daily["Nivel"] = daily["Carga"].map(load_level)
    daily["Promedio diario 42d"] = habitual_daily_load
    day_order = daily["Día"].tolist()
    bars = alt.Chart(daily).mark_bar(
        cornerRadiusTopLeft=5,
        cornerRadiusTopRight=5,
        size=48,
    ).encode(
        x=alt.X(
            "Día:N",
            sort=day_order,
            axis=alt.Axis(title=None, labelAngle=0),
        ),
        y=alt.Y("Carga:Q", axis=alt.Axis(title="Carga")),
        color=alt.Color(
            "Nivel:N",
            scale=alt.Scale(
                domain=["Descanso", "Baja", "Habitual", "Alta", "Muy alta"],
                range=["#CBD5E1", "#7DD3FC", "#2563EB", "#F59E0B", "#DC2626"],
            ),
            legend=alt.Legend(title="Nivel de carga", orient="top"),
        ),
        tooltip=[
            alt.Tooltip("Día:N", title="Fecha"),
            alt.Tooltip("Carga:Q", title="Carga", format=".1f"),
            alt.Tooltip("Nivel:N", title="Nivel"),
            alt.Tooltip(
                "Promedio diario 42d:Q",
                title="Promedio diario 42d",
                format=".1f",
            ),
        ],
    )
    values = alt.Chart(daily).mark_text(
        dy=-8,
        color="#334155",
        fontWeight="bold",
    ).encode(
        x=alt.X("Día:N", sort=day_order),
        y=alt.Y("Carga:Q"),
        text=alt.Text("Carga:Q", format=".1f"),
    )
    st.altair_chart(
        (bars + values).properties(height=300),
        use_container_width=True,
    )
    st.caption(
        f"Clasificación personalizada respecto de tu promedio diario de los "
        f"últimos 42 días ({habitual_daily_load:.1f}): baja ≤75%, "
        "habitual 75–125%, alta 125–175% y muy alta >175%."
    )
    left, right = st.columns(2)
    with left:
        st.markdown("#### Minutos por zona · 28 días")
        zone_frame = pd.DataFrame(
            [{"Zona": key, "Minutos": value} for key, value in training["zone_minutes_28d"].items()]
        )
        if zone_frame.empty:
            st.info("Sin tiempo por zonas disponible.")
        else:
            st.bar_chart(zone_frame.set_index("Zona"))
    with right:
        st.markdown("#### Carga por disciplina · 28 días")
        discipline_frame = pd.DataFrame(
            [{"Disciplina": key, "Carga": value} for key, value in training["discipline_load_28d"].items()]
        )
        if discipline_frame.empty:
            st.info("Sin carga por disciplina disponible.")
        else:
            st.bar_chart(discipline_frame.set_index("Disciplina"))
    modality_load = training.get("cycling_modality_load_28d", {})
    if modality_load:
        st.markdown("#### Carga de Bici por modalidad · 28 días")
        modality_frame = pd.DataFrame(
            [{"Modalidad": key, "Carga": value} for key, value in modality_load.items()]
        )
        st.bar_chart(modality_frame.set_index("Modalidad"))
    continuity = training.get("session_continuity", {})
    if continuity:
        st.markdown("#### Continuidad de las sesiones")
        recent = continuity.get("recent_7d", {})
        baseline = continuity.get("baseline_previous_42d", {})
        continuity_columns = st.columns(4)
        continuity_columns[0].metric(
            "Tiempo pausado · 7 días",
            _fmt(recent.get("paused_minutes"), 1, " min"),
        )
        continuity_columns[1].metric(
            "% pausado · 7 días",
            _fmt(
                100 * recent["paused_time_share"]
                if recent.get("paused_time_share") is not None else None,
                1,
                "%",
            ),
        )
        continuity_columns[2].metric(
            "Referencia · 6 semanas",
            _fmt(
                100 * baseline["paused_time_share"]
                if baseline.get("paused_time_share") is not None else None,
                1,
                "%",
            ),
        )
        continuity_columns[3].metric(
            "Cambio",
            _fmt(continuity.get("change_percentage_points"), 1, " pp"),
        )
        pause_frame = pd.DataFrame(continuity.get("activities_28d", []))
        if pause_frame.empty:
            st.info("No hay actividades con tiempo activo y transcurrido disponible.")
        else:
            pause_frame["Fecha"] = pd.to_datetime(pause_frame["date"])
            pause_frame["% pausado"] = 100 * pause_frame["paused_time_share"]
            daily_pause = (
                pause_frame.groupby("Fecha", as_index=False)[
                    ["paused_duration_seconds", "elapsed_duration_seconds"]
                ].sum()
            )
            daily_pause["% pausado"] = (
                100 * daily_pause["paused_duration_seconds"]
                / daily_pause["elapsed_duration_seconds"]
            )
            st.bar_chart(daily_pause.set_index("Fecha")[["% pausado"]])
            with st.expander("Detalle de pausas por actividad"):
                detail = pause_frame.copy()
                detail["Tiempo pausado (min)"] = detail["paused_duration_seconds"] / 60
                detail["Tiempo total (min)"] = detail["elapsed_duration_seconds"] / 60
                st.dataframe(
                    detail[[
                        "date", "discipline", "cycling_modality",
                        "Tiempo total (min)", "Tiempo pausado (min)", "% pausado",
                    ]].rename(columns={
                        "date": "Fecha", "discipline": "Disciplina",
                        "cycling_modality": "Modalidad",
                    }),
                    use_container_width=True,
                    hide_index=True,
                )
        st.caption(
            "Las pausas provienen exclusivamente de la diferencia entre tiempo "
            "transcurrido y tiempo activo informados por Garmin."
        )
    mechanical = training["running_mechanical_28d"]
    st.markdown("#### Exposición mecánica Corrida/Trail · 28 días")
    st.write(
        f"{mechanical['distance_km']:.1f} km · {mechanical['ascent_m']:.0f} m D+ · "
        f"{mechanical['descent_m']:.0f} m D−"
    )


def _progress_tab(state):
    progress = state["progress"]
    freshness = state.get("measurement_freshness", [])
    if freshness:
        st.markdown("### Vigencia de las métricas")
        st.dataframe(
            pd.DataFrame([
                {
                    "Métrica": item["metric_name"],
                    "Serie": item["series_label"],
                    "Estado": f"{item['status_icon']} {item['status']}",
                    "Última": item["latest_date"] or "—",
                    "Próxima evaluación": item["target_date"] or "Evaluación inicial",
                }
                for item in freshness
            ]),
            use_container_width=True,
            hide_index=True,
        )
    if not progress:
        st.info("Todavía no hay mediciones calculadas.")
        return
    for metric in progress.values():
        series_suffix = (
            f" · {metric.get('series_label')}"
            if metric.get("series_label") else ""
        )
        st.markdown(
            f"### {metric['name']} · {metric['discipline']}{series_suffix}"
        )
        columns = st.columns(4)
        columns[0].metric("Último valor", _fmt(metric["latest"], 2, f" {metric['unit']}"))
        columns[1].metric("Actividades medidas", metric.get("activity_summaries", metric["observations"]))
        columns[2].metric("Días desde medición", metric["days_since_measurement"])
        columns[3].metric("Confianza", metric["confidence"].capitalize())
        if metric["confidence"] == "baja":
            st.warning("La tendencia es exploratoria: hay pocas observaciones válidas.")
        frame = pd.DataFrame(metric["observations_data"])
        frame["date"] = pd.to_datetime(frame["date"])
        st.line_chart(frame.set_index("date")[["value"]])
        st.caption(
            f"Cada punto es la mediana de una actividad. Segmentos individuales conservados: {metric['observations']}."
        )
        st.caption(
            "Tendencia lineal estimada: "
            + _fmt(metric["trend_per_30d"], 2, f" {metric['unit']} cada 30 días")
        )


def _sources_tab(state):
    rows = []
    recovery = state["recovery"]
    latest = recovery.get("latest") or {}
    rows.extend(
        [
            {"Variable": "Training Load", "Fuente": "FIT", "Última actualización": state["training"].get("source_generated_at"), "Estado": "Disponible"},
            {"Variable": "Bienestar", "Fuente": latest.get("source", "—"), "Última actualización": latest.get("date", "—"), "Estado": "Disponible" if latest else "Ausente"},
            {"Variable": "Métricas", "Fuente": "Calculada desde FIT", "Última actualización": max((item["latest_date"] for item in state["progress"].values()), default="—"), "Estado": "Disponible" if state["progress"] else "Ausente"},
            {"Variable": "Contexto personal", "Fuente": "Manual", "Última actualización": (state["context"].get("latest_manual_context") or {}).get("date", "—"), "Estado": "Disponible" if state["context"].get("latest_manual_context") else "Ausente"},
        ]
    )
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)


def _session_feedback_tab(state):
    st.markdown("### Respuesta a cada entrenamiento")
    st.caption(
        "La actividad objetiva proviene del FIT. Tu evaluación permite decidir si el "
        "siguiente estímulo debe progresar, consolidarse o retroceder."
    )
    sessions = state.get("progression_memory", {}).get("recent_sessions", [])
    if not sessions:
        st.info("No existen actividades FIT procesadas hasta la fecha seleccionada.")
        return
    stored = {item["activity_id"]: item for item in load_session_feedback()}
    choices = {item["activity_id"]: item for item in sessions}
    activity_id = st.selectbox(
        "Actividad realizada",
        list(choices),
        format_func=lambda key: (
            f"{choices[key]['date']} · {choices[key]['discipline']} · "
            f"{choices[key]['distance_km']:.1f} km · "
            f"{choices[key]['duration_minutes']:.0f} min"
        ),
        key="session_feedback_activity",
    )
    selected, existing = choices[activity_id], stored.get(activity_id, {})
    objective = st.columns(4)
    objective[0].metric("Distancia", f"{selected['distance_km']:.1f} km")
    objective[1].metric("Duración", f"{selected['duration_minutes']:.0f} min")
    objective[2].metric("Carga", f"{selected['load']:.1f}")
    objective[3].metric(
        "Tiempo pausado",
        "—" if selected.get("paused_time_share") is None
        else f"{100 * selected['paused_time_share']:.1f}%",
    )
    finish_options = {
        "large_reserve": "Con bastante reserva",
        "some_reserve": "Podría haber hecho un poco más",
        "controlled": "Justo y controlado",
        "very_demanding": "Muy exigente",
        "excessive": "Excesivo o no repetible",
    }
    energy_options = {
        "stable": "Energía estable",
        "moderate_drop": "Caída moderada",
        "strong_drop": "Caída fuerte",
    }
    discomfort_options = {
        "none": "Ninguna molestia",
        "mild": "Molestia leve",
        "relevant": "Molestia relevante",
    }
    used_references = {
        (item.get("planned_session") or {}).get("reference_key")
        for item in stored.values()
        if item.get("activity_id") != activity_id
    }
    performed_date = date.fromisoformat(selected["date"])
    planned_options = {}
    for plan in load_active_plans():
        for session in plan.get("sessions", []):
            if session.get("sport") == "Descanso" or not session.get("date"):
                continue
            distance_days = abs((date.fromisoformat(session["date"]) - performed_date).days)
            if distance_days > 10:
                continue
            reference_key = f"{plan['id']}::{session['date']}"
            if reference_key in used_references:
                continue
            planned_options[reference_key] = {
                "reference_key": reference_key,
                "plan_id": plan["id"], "plan_version": plan.get("version"),
                "week_start": plan.get("week_start"), "date": session["date"],
                "sport": session.get("sport"), "session_type": session.get("session_type"),
                "zone": session.get("zone"), "terrain": session.get("terrain"),
                "distance_km": session.get("distance_km"), "template": session.get("template"),
            }
    existing_planned = existing.get("planned_session")
    if existing_planned:
        planned_options.setdefault(existing_planned["reference_key"], existing_planned)
    ordered_plans = sorted(
        planned_options,
        key=lambda key: (
            planned_options[key].get("sport") != selected["discipline"],
            abs((date.fromisoformat(planned_options[key]["date"]) - performed_date).days),
            planned_options[key]["date"],
        ),
    )
    match_options = [f"planned::{key}" for key in ordered_plans] + ["additional", "unmatched"]
    existing_match = existing.get("match_type", "planned" if existing_planned else "unmatched")
    existing_option = (
        f"planned::{existing_planned['reference_key']}"
        if existing_match == "planned" and existing_planned else existing_match
    )
    if existing_option not in match_options:
        existing_option = "unmatched"

    def match_label(option):
        if option == "additional":
            return "Actividad adicional / no planificada"
        if option == "unmatched":
            return "No corresponde a una sesión identificable"
        item = planned_options[option.split("planned::", 1)[1]]
        return (
            f"Plan {item['date']} · {item.get('sport') or '—'} · "
            f"{item.get('session_type') or '—'} · {item.get('zone') or '—'} · "
            f"{item.get('distance_km') if item.get('distance_km') is not None else '—'} km"
        )

    with st.form(f"session_feedback_form_{activity_id}"):
        match_choice = st.selectbox(
            "¿A qué sesión planificada corresponde?",
            match_options,
            index=match_options.index(existing_option),
            format_func=match_label,
            help=(
                "La sesión puede ser de otro día. Una sesión planificada sólo puede "
                "asociarse a una actividad FIT."
            ),
        )
        if not ordered_plans:
            st.caption("No hay sesiones publicadas cercanas disponibles; marca la actividad como adicional o sin correspondencia.")
        columns = st.columns(2)
        completion = columns[0].number_input(
            "Cumplimiento de lo previsto (%)", min_value=0, max_value=100,
            value=int(existing.get("completion_percent") or 100), step=5,
            help="Sólo se utiliza cuando asocias la actividad a una sesión planificada.",
        )
        rpe = columns[1].slider(
            "Esfuerzo percibido global (1–10)", 1, 10,
            int(existing.get("rpe", 5)),
        )
        finish_keys = list(finish_options)
        energy_keys = list(energy_options)
        discomfort_keys = list(discomfort_options)
        finish = st.selectbox(
            "Sensación al terminar", finish_keys,
            index=finish_keys.index(existing.get("finish_feeling", "controlled")),
            format_func=finish_options.get,
        )
        energy = st.selectbox(
            "Energía durante la sesión", energy_keys,
            index=energy_keys.index(existing.get("energy_response", "stable")),
            format_func=energy_options.get,
        )
        discomfort = st.selectbox(
            "Molestias", discomfort_keys,
            index=discomfort_keys.index(existing.get("discomfort", "none")),
            format_func=discomfort_options.get,
        )
        notes = st.text_area("Comentario opcional", value=existing.get("notes", ""))
        submitted = st.form_submit_button("Guardar evaluación", use_container_width=True)
    if submitted:
        match_type = "planned" if match_choice.startswith("planned::") else match_choice
        planned_session = (
            planned_options[match_choice.split("planned::", 1)[1]]
            if match_type == "planned" else None
        )
        try:
            save_session_feedback({
                "activity_id": activity_id, "date": selected["date"],
                "match_type": match_type, "planned_session": planned_session,
                "completion_percent": completion, "rpe": rpe,
                "finish_feeling": finish, "energy_response": energy,
                "discomfort": discomfort, "notes": notes,
            })
        except ValueError as error:
            st.error(str(error))
        else:
            st.success("Evaluación guardada. La memoria se actualizará al recalcular el Athlete State.")
            _rerun()
    if existing and st.button("Eliminar evaluación", key=f"delete_session_feedback_{activity_id}"):
        delete_session_feedback(activity_id)
        _rerun()

    st.divider()
    st.markdown("### Memoria de progresión")
    labels = {
        "progress": "Progresar", "consolidate": "Consolidar",
        "regress": "Retroceder", "insufficient_evidence": "Evidencia insuficiente",
    }
    rows = []
    for discipline, item in state.get("progression_memory", {}).get("disciplines", {}).items():
        latest = item.get("latest_evaluated") or {}
        longest = item.get("longest_well_tolerated") or {}
        rows.append({
            "Disciplina": discipline, "Decisión": labels.get(item.get("action"), item.get("action")),
            "Última evaluada": latest.get("date", "—"),
            "Respuesta": latest.get("status", "—"),
            "Mayor distancia bien tolerada": longest.get("distance_km"),
            "Racha bien tolerada": item.get("well_tolerated_streak", 0),
            "Sin evaluación": item.get("sessions_without_feedback", 0),
        })
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    st.caption(
        "Progresar requiere una evaluación bien tolerada. Una sesión difícil se consolida; "
        "una no tolerada retrocede; sin evaluación no se escala automáticamente."
    )


def render():
    st.write(
        "Integra entrenamiento, recuperación, progreso, respuesta subjetiva y contexto. "
        "La capa adaptativa utiliza este estado, pero no publica cambios sin tu aprobación."
    )
    as_of = st.date_input("Estado a fecha de", value=date.today(), key="athlete_state_as_of")
    state = build_athlete_state(as_of)
    summary_tab, wellness_tab, training_tab, progress_tab, feedback_tab, context_tab = st.tabs(
        ["Resumen", "Bienestar diario", "Carga y fatiga", "Progreso", "Respuesta al entrenamiento", "Contexto y fuentes"]
    )
    with summary_tab:
        _summary(state)
    with wellness_tab:
        _wellness_form()
    with training_tab:
        _training_tab(state)
    with progress_tab:
        _progress_tab(state)
    with feedback_tab:
        _session_feedback_tab(state)
    with context_tab:
        _context_form()
        st.divider()
        _sources_tab(state)
