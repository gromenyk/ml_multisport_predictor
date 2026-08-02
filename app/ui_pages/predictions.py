"""Next-week prediction page."""

import json
import subprocess
import sys
from datetime import date, timedelta

import altair as alt
import pandas as pd
import streamlit as st

from config import PROJECT_ROOT


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


def _read_json(path):
    """Read one derived dataset without hiding malformed or missing files."""
    if not path.exists():
        return None, f"File not found: {path.name}"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as error:
        return None, str(error)


def _rebuild_load(script_name):
    """Run one source-specific load adapter with the current Python runtime."""
    return subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(PROJECT_ROOT / "training_load" / script_name),
        ],
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
        return "Rest"
    if session.get("template"):
        return session["template"]
    if session.get("distance_km") is not None:
        return f"{session['distance_km']} km"
    return "-"


def _render_prediction_archive_detail(entry):
    """Render the selected archived generation as a seven-day table."""
    prediction = entry["prediction"]
    load_snapshot = entry["load_snapshot"] or {}
    daily_loads = load_snapshot.get("daily_loads", {})

    header_columns = st.columns(3)
    with header_columns[0]:
        st.metric(
            "Predicted week",
            f"{entry['week_start'].strftime('%d/%m/%Y')} – "
            f"{entry['week_end'].strftime('%d/%m/%Y')}",
        )
    with header_columns[1]:
        st.metric(
            "Generated",
            entry["generated_at"].strftime("%d/%m/%Y %H:%M"),
        )
    with header_columns[2]:
        weekly_load = entry["weekly_load"]
        st.metric(
            "Predicted load",
            f"{weekly_load:.2f}" if weekly_load is not None else "Unavailable",
        )

    rows = []
    for session in prediction.get("sessions", []):
        daily_load = daily_loads.get(session.get("date"), {})
        rows.append(
            {
                "Date": date.fromisoformat(session["date"]),
                "Day": session.get("weekday"),
                "Sport": session.get("sport"),
                "Session type": session.get("session_type"),
                "Objective": session.get("objective_class"),
                "Terrain": session.get("terrain"),
                "Zone": session.get("zone"),
                "Workout": _prediction_workout(session),
                "Load": daily_load.get("predicted_load"),
            }
        )

    st.dataframe(
        rows,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Date": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Load": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    st.caption(
        "Context weeks: "
        + ", ".join(prediction.get("context_weeks") or [])
    )
    st.caption(f"Prediction ID: {entry['prediction_id']}")

    model_fingerprints = prediction.get("production_models")
    if model_fingerprints:
        with st.expander("Production model fingerprints"):
            st.dataframe(
                [
                    {"Model": name, "SHA-256": fingerprint}
                    for name, fingerprint in model_fingerprints.items()
                ],
                hide_index=True,
                use_container_width=True,
            )
    else:
        st.caption("Production model fingerprints: unavailable")

    warnings = []
    for session in load_snapshot.get("sessions", []):
        warnings.extend(session.get("quality", {}).get("warnings", []))
    if warnings:
        with st.expander(f"Calculation warnings ({len(warnings)})"):
            for warning in warnings:
                st.warning(warning)

    with st.expander("Prediction JSON"):
        st.code(
            json.dumps(prediction, indent=4, ensure_ascii=False),
            language="json",
        )


def _render_prediction_history():
    """Render filters, archive index and the selected weekly generation."""
    st.divider()
    st.header("Prediction History")
    st.write(
        "Inspect every archived weekly generation without mixing different "
        "simulation dates or model runs."
    )

    entries, errors = _load_prediction_archive()
    if errors:
        with st.expander(f"Archive warnings ({len(errors)})"):
            for error in errors:
                st.warning(error)
    if not entries:
        st.info("No archived predictions are available yet.")
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
        "Show all predictions",
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
            "Target week starts between",
            value=(target_min, target_max),
            min_value=target_min,
            max_value=target_max,
            key="prediction_history_target_range",
            help=(
                "Historical date of the week produced by the model. This is "
                "independent from the real date when you ran the prediction."
            ),
        )
    with generation_col:
        generation_range = st.date_input(
            "File generated on",
            value=(generation_min, generation_max),
            min_value=generation_min,
            max_value=generation_max,
            key="prediction_history_generation_range",
            help=(
                "Real calendar date when Generate next week was pressed and "
                "the archived JSON was created."
            ),
        )

    if len(target_range) != 2 or len(generation_range) != 2:
        st.info("Select both endpoints of each date range.")
        return

    latest_only = st.checkbox(
        "Show only the latest generation for each predicted week",
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
                f"{hidden_by_date} outside the selected date ranges"
            )
        if hidden_versions:
            messages.append(
                f"{hidden_versions} older generation(s) hidden"
            )
        st.info(
            "Not all archived predictions are currently shown: "
            + "; ".join(messages)
            + "."
        )

    if not filtered:
        st.info("No predictions match the selected filters.")
        return

    index_rows = []
    for entry in filtered:
        context = entry["prediction"].get("context_weeks") or []
        index_rows.append(
            {
                "Generated": entry["generated_at"],
                "Target week start": entry["week_start"],
                "Target week end": entry["week_end"],
                "Last context week": context[-1] if context else None,
                "Sessions": len(entry["prediction"].get("sessions", [])),
                "Predicted load": entry["weekly_load"],
            }
        )
    st.dataframe(
        index_rows,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Generated": st.column_config.DatetimeColumn(
                format="DD/MM/YYYY HH:mm"
            ),
            "Target week start": st.column_config.DateColumn(
                format="DD/MM/YYYY"
            ),
            "Target week end": st.column_config.DateColumn(
                format="DD/MM/YYYY"
            ),
            "Predicted load": st.column_config.NumberColumn(format="%.2f"),
        },
    )

    entry_by_id = {
        entry["prediction_id"]: entry for entry in filtered
    }
    selected_id = st.selectbox(
        "Prediction to inspect",
        options=list(entry_by_id),
        format_func=lambda prediction_id: (
            f"{entry_by_id[prediction_id]['week_start'].strftime('%d/%m/%Y')}"
            " – "
            f"{entry_by_id[prediction_id]['week_end'].strftime('%d/%m/%Y')}"
            " · generated "
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


def _build_load_rows(prescribed, predicted, performed):
    """Join all three independently derived load sources by calendar date."""
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
        set(prescribed_days) | set(predicted_days) | set(performed_days)
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
                "Block": component.get("source_block_index"),
                "Type": component.get("type"),
                "Zone": component.get("zone"),
                "Duration (min)": component.get("total_duration_minutes"),
                "Duration source": component.get("duration_source"),
                "Distance (km)": component.get("total_distance_km"),
                "Sport factor": component.get("sport_factor"),
                "Zone factor": component.get("zone_factor"),
                "Terrain factor": component.get("terrain_factor"),
                "Load": component.get("scaled_load"),
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
    metadata_rows = [
        {"Field": key.replace("_", " ").title(), "Value": value}
        for key, value in metadata.items()
        if value is not None and key != "blocks"
    ]
    if metadata_rows:
        st.dataframe(metadata_rows, hide_index=True, use_container_width=True)

    if load:
        st.metric("Calculated load", f"{load['calculated_load']:.2f}")
        components = _component_table(load.get("components"))
        if components:
            st.dataframe(
                components,
                hide_index=True,
                use_container_width=True,
                column_config={
                    "Distance (km)": st.column_config.NumberColumn(format="%.2f"),
                    "Load": st.column_config.NumberColumn(format="%.2f"),
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

    st.subheader(f"Load detail · {day}")
    prescribed_tab, predicted_tab, performed_tab = st.tabs(
        ["Prescribed", "Predicted", "Performed"]
    )

    with prescribed_tab:
        if not prescribed_sessions:
            st.info("No prescribed session is available for this date.")
        for session in prescribed_sessions:
            _render_session_detail(
                session["session_id"],
                session.get("prescription", {}),
                session.get("load"),
                session.get("quality"),
            )

    with predicted_tab:
        if not predicted_sessions:
            st.info("No model prediction is available for this date.")
        for session in predicted_sessions:
            _render_session_detail(
                session["session_id"],
                session.get("prediction", {}),
                session.get("load"),
                session.get("quality"),
            )

    with performed_tab:
        if not performed_activities:
            st.info("No performed Garmin activity is available for this date.")
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
    st.header("Training Load")
    st.write(
        "Compare the human prescription, the model prediction and Garmin "
        "activities using the same load formula."
    )

    if st.button("Recalculate training loads", use_container_width=True):
        progress = st.progress(0)
        messages = []
        for index, (label, script_name) in enumerate(LOAD_SCRIPTS, start=1):
            result = _rebuild_load(script_name)
            output_exists = LOAD_OUTPUTS[label].exists()
            messages.append(
                {
                    "Source": label,
                    "Output generated": output_exists,
                    "Return code": result.returncode,
                }
            )
            progress.progress(index / len(LOAD_SCRIPTS))
        st.dataframe(messages, hide_index=True, use_container_width=True)
        st.caption(
            "A non-zero code can indicate audited incomplete historical "
            "sessions; valid derived rows are still preserved."
        )

    datasets = {}
    errors = []
    for label, path in LOAD_OUTPUTS.items():
        datasets[label], error = _read_json(path)
        if error:
            errors.append(f"{label}: {error}")

    if errors:
        st.warning(
            "Some load sources are unavailable. Use Recalculate training "
            "loads after their source data has been processed."
        )
        with st.expander("Unavailable sources"):
            for error in errors:
                st.write(error)

    rows, predictions = _build_load_rows(
        datasets["Prescribed"],
        datasets["Predicted"],
        datasets["Performed"],
    )
    if not rows:
        st.info("No training-load data is available yet.")
        return

    minimum_date = rows[0]["Date"]
    maximum_date = rows[-1]["Date"]
    default_start = max(minimum_date, maximum_date - timedelta(days=42))
    start_col, end_col = st.columns(2)
    with start_col:
        start_date = st.date_input(
            "From",
            value=default_start,
            min_value=minimum_date,
            max_value=maximum_date,
            key="load_start_date",
        )
    with end_col:
        end_date = st.date_input(
            "To",
            value=maximum_date,
            min_value=minimum_date,
            max_value=maximum_date,
            key="load_end_date",
        )

    if start_date > end_date:
        st.error("The start date must not be later than the end date.")
        return

    filtered_rows = [
        row for row in rows if start_date <= row["Date"] <= end_date
    ]
    visible_columns = ["Date", "Prescribed", "Predicted", "Performed"]
    table = pd.DataFrame(filtered_rows)
    complete_table = pd.DataFrame(rows)

    metric_columns = st.columns(3)
    for column, metric_column in zip(
        ("Prescribed", "Predicted", "Performed"),
        metric_columns,
    ):
        total = table[column].dropna().sum()
        available_days = int(table[column].notna().sum())
        with metric_column:
            st.metric(f"{column} total", f"{total:.1f}")
            st.caption(f"{available_days} days with data")

    chart_columns = ["Prescribed", "Predicted", "Performed"]
    chart_color_map = {
        "Prescribed": "#2563EB",
        "Predicted": "#F59E0B",
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
        "Sources shown in charts",
        options=chart_columns,
        default=chart_columns,
        key="training_load_chart_sources",
    )

    balance_timeline = _build_load_balance_timeline(
        datasets["Performed"], start_date, end_date
    )

    if selected_sources:
        selected_colors = [
            chart_color_map[source] for source in selected_sources
        ]

        st.subheader("Load evolution")
        st.line_chart(
            chart_data,
            x="Date",
            y=selected_sources,
            x_label="Date",
            y_label="Training load",
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

        st.subheader("Daily source comparison")
        st.bar_chart(
            chart_data,
            x="Date",
            y=selected_sources,
            x_label="Date",
            y_label="Training load",
            color=selected_colors,
            stack=False,
            height=420,
        )

        st.subheader("Weekly load totals")
        st.line_chart(
            weekly_table,
            x="Week start",
            y=selected_sources,
            x_label="Week starting",
            y_label="Weekly training load",
            color=selected_colors,
            height=420,
        )
    else:
        st.info("Select at least one source to display the charts.")

    st.subheader("Weekly values")
    weekly_values = weekly_table[
        ["Week start", "Week end", *chart_columns]
    ].copy()
    st.dataframe(
        weekly_values,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Week start": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Week end": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Prescribed": st.column_config.NumberColumn(format="%.2f"),
            "Predicted": st.column_config.NumberColumn(format="%.2f"),
            "Performed": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    st.caption(
        "Weeks run from Monday to Sunday. Performed totals include the Garmin "
        "activities imported so far, so the current week can still be partial."
    )

    st.subheader("Daily values")
    event = st.dataframe(
        table[visible_columns],
        hide_index=True,
        use_container_width=True,
        on_select="rerun",
        selection_mode="single-row",
        key="training_load_comparison",
        column_config={
            "Date": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Prescribed": st.column_config.NumberColumn(format="%.2f"),
            "Predicted": st.column_config.NumberColumn(format="%.2f"),
            "Performed": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    st.caption("Select a row to inspect its sessions and load components.")

    selected_rows = event.selection.rows
    if selected_rows:
        selected_date = filtered_rows[selected_rows[0]]["Date"]
        _render_load_detail(
            selected_date,
            datasets["Prescribed"],
            predictions,
            datasets["Performed"],
        )


def render():
    # STEP 6 - NEXT WEEK PREDICTION
    # ==========================================================

    st.header("Paso 6 · Predecir la próxima semana")

    st.write(
        """
    Generate the next unknown training week using the latest four weeks as
    context and the production models trained with 100% of the history.
    """
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
        st.warning("Train the production models before generating the next week.")

    if st.button(
        "Generar próxima semana",
        disabled=not production_ready,
        use_container_width=True,
    ):
        with st.spinner("Generating next week prediction..."):
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
            st.error("Next week prediction failed.")
            with st.expander("Prediction error", expanded=True):
                st.code(
                    (prediction_result.stdout + "\n" + prediction_result.stderr).strip(),
                    language="text",
                )
        else:
            prediction = json.loads(prediction_result.stdout)
            st.success(
                f"Prediction generated for {prediction['week_start']} "
                f"to {prediction['week_end']}."
            )

            rows = []
            for session in prediction["sessions"]:
                workout = session.get("template")
                if workout is None and session.get("distance_km") is not None:
                    workout = f"{session['distance_km']} km"
                if workout is None and session["sport"] == "Descanso":
                    workout = "Rest"

                rows.append(
                    {
                        "Date": session["date"],
                        "Day": session["weekday"],
                        "Sport": session["sport"],
                        "Session type": session.get("session_type"),
                        "Objective": session.get("objective_class"),
                        "Terrain": session.get("terrain"),
                        "Zone": session.get("zone"),
                        "Workout": workout,
                    }
                )

            st.table(rows)
            st.caption(
                "Context weeks: " + ", ".join(prediction["context_weeks"])
            )

            prediction_json = json.dumps(prediction, indent=4, ensure_ascii=False)

            with st.expander("Prediction JSON"):
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
