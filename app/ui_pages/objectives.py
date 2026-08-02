"""Event objectives, reusable catalogs and time-window assignments."""

from __future__ import annotations

import calendar
from collections import defaultdict
from datetime import date, timedelta
import html
import inspect
import json

import pandas as pd
import streamlit as st

from config import PROJECT_ROOT
from metrics.measurement_store import (
    load_measurements as load_calculated_measurements,
    update_measurement_note,
)
from metrics.orchestrator import evaluate_all_activities
from metrics.aggregation import activity_medians
from metrics.freshness import metric_freshness
from metrics.registry import METRIC_REGISTRY
from backend.catalog_translations import (
    explain_metric_calculation,
    translate_calculation,
    translate_required_data,
)
from backend.heart_rate_zones_store import (
    ZONE_COLORS,
    ZONE_IDS,
    delete_zone_profile,
    load_zone_profiles,
    profile_for_date,
    save_zone_profile,
)
from backend.objectives_store import (
    add_measurement,
    delete_development_goal,
    delete_event,
    delete_measurement,
    delete_metric,
    delete_window,
    load_development_goals,
    load_events,
    load_measurements,
    load_metrics,
    load_windows,
    remove_goal_assignment,
    remove_metric_assignment,
    save_development_goal,
    save_discipline_weights,
    save_event,
    save_goal_assignment,
    save_metric,
    save_metric_assignment,
    save_window,
)


DISCIPLINES = ("Bici", "Corrida", "Trail", "Kayak")
STATUSES = ("Active", "Paused", "Completed")
PRIORITIES = ("Primary", "Secondary", "Supporting")
DIRECTIONS = {
    "Minimizar": "minimize",
    "Maximizar": "maximize",
    "Mantener un rango": "range",
}
SOURCES = ("Manual", "FIT", "Garmin", "Calculated")
EXTRACTION_TYPES = (
    "Manual",
    "Activity summary",
    "GPS segment",
    "Calculated",
)
STATUS_LABELS = {
    "Active": "Activo",
    "Paused": "Pausado",
    "Completed": "Completado",
}
PRIORITY_LABELS = {
    "Primary": "Primario",
    "Secondary": "Secundario",
    "Supporting": "Complementario",
}
SOURCE_LABELS = {
    "Manual": "Manual",
    "FIT": "FIT",
    "Garmin": "Garmin",
    "Calculated": "Calculada",
}
EXTRACTION_LABELS = {
    "Manual": "Manual",
    "Activity summary": "Resumen de actividad",
    "GPS segment": "Segmento GPS",
    "Calculated": "Calculada",
}

LOAD_DATA_DIR = PROJECT_ROOT / "data" / "derived" / "training_load"
PREDICTED_LOAD_FILE = LOAD_DATA_DIR / "predicted_load.json"
ADAPTIVE_PLANS_FILE = PROJECT_ROOT / "data" / "athlete_state" / "adaptive_published_plans.json"
PERFORMED_LOAD_FILE = LOAD_DATA_DIR / "performed_load.json"
CALENDAR_COLORS = ("#2563EB", "#7C3AED", "#0891B2", "#059669", "#D97706", "#DC2626")
MONTH_NAMES = (
    "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
    "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
)


def _rerun():
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()


def _optional_number(value, label):
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError as error:
        raise ValueError(f"{label} debe ser numérico o quedar vacío.") from error


def _by_id(values):
    return {item["id"]: item for item in values}


def _catalog_name(item):
    return item.get("name_es") or item.get("name") or "Sin nombre"


def _catalog_category(item):
    return item.get("category_es") or item.get("category") or ""


def _metric_uses_heart_rate_zone(metric):
    searchable = " ".join(
        str(metric.get(key, ""))
        for key in ("name", "name_es", "description", "calculation_es")
    ).casefold()
    return (
        "frecuencia cardiaca estandarizada" in searchable
        or "standardized heart rate" in searchable
    )


def _dataframe_parameters():
    return inspect.signature(st.dataframe).parameters


def _render_catalog_table(frame, items, state_key, table_key):
    """Render row selection with a fallback for older Streamlit releases."""
    parameters = _dataframe_parameters()
    kwargs = {"use_container_width": True}
    if "hide_index" in parameters:
        kwargs["hide_index"] = True
    if "on_select" in parameters:
        table_event = st.dataframe(
            frame,
            on_select="rerun",
            selection_mode="single-row",
            key=table_key,
            **kwargs,
        )
        rows = list(table_event.selection.rows)
        if rows:
            st.session_state[state_key] = items[rows[0]]["id"]
    else:
        st.dataframe(frame, **kwargs)
        valid_ids = [item["id"] for item in items]
        current = st.session_state.get(state_key)
        index = valid_ids.index(current) if current in valid_ids else 0
        st.selectbox(
            "Elemento seleccionado",
            valid_ids,
            index=index,
            format_func=lambda item_id: _catalog_name(
                next(item for item in items if item["id"] == item_id)
            ),
            key=state_key,
        )


def _selected_catalog_item(items, state_key):
    """Resolve the stable selection produced by either table implementation."""
    valid_ids = {item["id"] for item in items}
    selected_id = st.session_state.get(state_key)
    if selected_id not in valid_ids:
        selected_id = items[0]["id"]
        st.session_state[state_key] = selected_id
    return next(item for item in items if item["id"] == selected_id)


def _event_form(existing=None):
    existing = existing or {}
    form_id = existing.get("id", "new")
    with st.form(f"event_form_{form_id}", clear_on_submit=not existing):
        name = st.text_input("Nombre del evento *", value=existing.get("name", ""))
        description = st.text_area(
            "Descripción", value=existing.get("description", "")
        )
        columns = st.columns(3)
        with columns[0]:
            event_date = st.date_input(
                "Fecha del evento",
                value=date.fromisoformat(existing["event_date"])
                if existing.get("event_date")
                else None,
            )
        with columns[1]:
            status_value = existing.get("status", "Active")
            status = st.selectbox(
                "Estado",
                STATUSES,
                index=STATUSES.index(status_value)
                if status_value in STATUSES
                else 0,
                format_func=lambda item: STATUS_LABELS[item],
            )
        disciplines = st.multiselect(
            "Disciplinas",
            DISCIPLINES,
            default=[
                item
                for item in existing.get("disciplines", [])
                if item in DISCIPLINES
            ],
        )
        characteristics = st.text_area(
            "Características del evento",
            value=existing.get("characteristics", ""),
            help="Duración, desnivel, terreno, transiciones y otros detalles.",
        )
        submitted = st.form_submit_button("Guardar evento", use_container_width=True)
    if submitted:
        if not name.strip():
            st.error("El nombre del evento es obligatorio.")
            return
        save_event(
            {
                **({"id": existing["id"]} if existing.get("id") else {}),
                "name": name.strip(),
                "description": description.strip(),
                "event_date": event_date.isoformat() if event_date else None,
                "status": status,
                "disciplines": disciplines,
                "characteristics": characteristics.strip(),
            }
        )
        _rerun()


def _goal_form(existing=None):
    existing = existing or {}
    form_id = existing.get("id", "new")
    with st.form(f"goal_catalog_form_{form_id}", clear_on_submit=not existing):
        name = st.text_input(
            "Nombre del objetivo de desarrollo *", value=existing.get("name", "")
        )
        description = st.text_area(
            "Descripción", value=existing.get("description", "")
        )
        disciplines = st.multiselect(
            "Disciplinas compatibles",
            DISCIPLINES,
            default=[
                item
                for item in existing.get("disciplines", [])
                if item in DISCIPLINES
            ],
        )
        category = st.text_input(
            "Categoría", value=existing.get("category", "")
        )
        submitted = st.form_submit_button(
            "Guardar objetivo del catálogo", use_container_width=True
        )
    if submitted:
        if not name.strip():
            st.error("El nombre del objetivo de desarrollo es obligatorio.")
            return
        save_development_goal(
            {
                **existing,
                **({"id": existing["id"]} if existing.get("id") else {}),
                "name": name.strip(),
                "description": description.strip(),
                "disciplines": disciplines,
                "category": category.strip(),
            }
        )
        _rerun()


def _metric_form(goals, existing=None):
    existing = existing or {}
    form_id = existing.get("id", "new")
    goal_map = _by_id(goals)
    reverse_directions = {value: key for key, value in DIRECTIONS.items()}
    with st.form(f"metric_catalog_form_{form_id}", clear_on_submit=not existing):
        name = st.text_input("Nombre de la métrica *", value=existing.get("name", ""))
        description = st.text_area(
            "Descripción", value=existing.get("description", "")
        )
        columns = st.columns(3)
        with columns[0]:
            existing_disciplines = existing.get("disciplines", [])
            if not existing_disciplines and existing.get("discipline"):
                existing_disciplines = [existing["discipline"]]
            disciplines = st.multiselect(
                "Disciplinas",
                DISCIPLINES,
                default=[
                    item
                    for item in existing_disciplines
                    if item in DISCIPLINES
                ],
            )
        with columns[1]:
            modality = st.text_input(
                "Modalidad (opcional)", value=existing.get("modality", "")
            )
        with columns[2]:
            unit = st.text_input("Unidad *", value=existing.get("unit", ""))
        columns = st.columns(2)
        with columns[0]:
            direction_label = reverse_directions.get(
                existing.get("direction"), "Minimizar"
            )
            direction = st.selectbox(
                "Dirección de mejora",
                tuple(DIRECTIONS),
                index=tuple(DIRECTIONS).index(direction_label),
            )
        with columns[1]:
            extraction_value = existing.get("extraction_type", "Manual")
            extraction_type = st.selectbox(
                "Tipo de extracción",
                EXTRACTION_TYPES,
                index=EXTRACTION_TYPES.index(extraction_value)
                if extraction_value in EXTRACTION_TYPES
                else 0,
                format_func=lambda item: EXTRACTION_LABELS[item],
            )
        compatible_goals = st.multiselect(
            "Objetivos de desarrollo relacionados",
            tuple(goal_map),
            default=[
                item
                for item in existing.get("development_goal_ids", [])
                if item in goal_map
            ],
            format_func=lambda item: _catalog_name(goal_map[item]),
        )
        measurement_method = st.text_area(
            "Cálculo",
            value=existing.get(
                "calculation", existing.get("measurement_method", "")
            ),
        )
        requirements = st.text_area(
            "Datos requeridos",
            value="\n".join(existing.get("required_data", [])),
            help="Un requisito por línea.",
        )
        equipment = st.text_area(
            "Equipamiento especial",
            value="\n".join(existing.get("special_equipment", [])),
            help="Un elemento por línea.",
        )
        submitted = st.form_submit_button(
            "Guardar métrica del catálogo", use_container_width=True
        )
    if submitted:
        if not name.strip() or not unit.strip():
            st.error("El nombre y la unidad de la métrica son obligatorios.")
            return
        save_metric(
            {
                **{
                    key: value
                    for key, value in existing.items()
                    if key != "recommended_frequency_days"
                },
                **({"id": existing["id"]} if existing.get("id") else {}),
                "name": name.strip(),
                "description": description.strip(),
                "disciplines": disciplines,
                "modality": modality.strip(),
                "unit": unit.strip(),
                "direction": DIRECTIONS[direction],
                "extraction_type": extraction_type,
                "development_goal_ids": compatible_goals,
                "measurement_method": measurement_method.strip(),
                "calculation": measurement_method.strip(),
                "required_data": [
                    item.strip()
                    for item in requirements.splitlines()
                    if item.strip()
                ],
                "special_equipment": [
                    item.strip()
                    for item in equipment.splitlines()
                    if item.strip()
                ],
            }
        )
        _rerun()


def _window_form(event_id, existing=None):
    existing = existing or {}
    form_id = existing.get("id", "new")
    with st.form(f"window_form_{form_id}", clear_on_submit=not existing):
        name = st.text_input("Nombre de la ventana *", value=existing.get("name", ""))
        columns = st.columns(2)
        with columns[0]:
            start_date = st.date_input(
                "Fecha de inicio",
                value=date.fromisoformat(existing["start_date"])
                if existing.get("start_date")
                else date.today(),
                key=f"window_start_{form_id}",
            )
        with columns[1]:
            end_date = st.date_input(
                "Fecha de término",
                value=date.fromisoformat(existing["end_date"])
                if existing.get("end_date")
                else date.today(),
                key=f"window_end_{form_id}",
            )
        description = st.text_area(
            "Propósito de la ventana", value=existing.get("description", "")
        )
        submitted = st.form_submit_button(
            "Guardar ventana del calendario", use_container_width=True
        )
    if submitted:
        if not name.strip():
            st.error("El nombre de la ventana es obligatorio.")
            return
        if start_date > end_date:
            st.error("La fecha de inicio no puede ser posterior a la fecha de término.")
            return
        save_window(
            {
                **({"id": existing["id"]} if existing.get("id") else {}),
                "event_id": event_id,
                "name": name.strip(),
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "description": description.strip(),
                "assignments": existing.get("assignments", []),
                "discipline_priorities": existing.get(
                    "discipline_priorities", {}
                ),
                "discipline_weights": existing.get("discipline_weights", {}),
            }
        )
        _rerun()


def _render_events():
    events = load_events()
    with st.expander("Crear evento", expanded=not events):
        _event_form()
    if not events:
        st.info("Aún no se han creado eventos.")
        return
    event_map = _by_id(events)
    event_id = st.selectbox(
        "Evento",
        tuple(event_map),
        format_func=lambda item: event_map[item]["name"],
        key="event_tab_selector",
    )
    event = event_map[event_id]
    st.write(event.get("description") or "Sin descripción.")
    st.caption(
        f"Fecha: {event.get('event_date') or 'No definida'} · Disciplinas: "
        + (", ".join(event.get("disciplines", [])) or "Ninguna")
    )
    with st.expander("Editar o eliminar evento"):
        _event_form(event)
        st.warning("Eliminar un evento también elimina sus ventanas del calendario.")
        confirm = st.checkbox(
            "Confirmar eliminación del evento", key=f"confirm_event_{event_id}"
        )
        if st.button(
            "Eliminar evento",
            disabled=not confirm,
            key=f"delete_event_{event_id}",
        ):
            delete_event(event_id)
            _rerun()


def _render_catalog():
    goals = load_development_goals()
    metrics = load_metrics()
    goal_tab, metric_tab = st.tabs(
        ["Objetivos de desarrollo", "Métricas"]
    )
    with goal_tab:
        if goals:
            st.caption(
                "Selecciona una fila para consultar su descripción y detalles."
            )
            _render_catalog_table(
                pd.DataFrame(
                    [
                        {
                            "Objetivo": _catalog_name(item),
                            "Categoría": _catalog_category(item),
                            "Disciplinas": ", ".join(item.get("disciplines", [])),
                            "Aplicabilidad": ", ".join(
                                item.get("applicability_tags", [])
                            ),
                        }
                        for item in goals
                    ]
                ),
                goals,
                "selected_development_goal_id",
                "development_goal_catalog_table",
            )
        else:
            st.info("El catálogo de objetivos de desarrollo está vacío.")
        with st.expander("Agregar objetivo al catálogo", expanded=not goals):
            _goal_form()
        if goals:
            selected_goal = _selected_catalog_item(
                goals,
                "selected_development_goal_id",
            )
            selected = selected_goal["id"]
            with st.expander(
                f"Descripción · {_catalog_name(selected_goal)}",
                expanded=True,
            ):
                st.write(
                    selected_goal.get("description_es")
                    or selected_goal.get("description")
                    or "No hay una descripción disponible."
                )
                details = st.columns(2)
                details[0].markdown(
                    "**Disciplinas del proyecto**  \n"
                    + (
                        ", ".join(selected_goal.get("disciplines", []))
                        or "Ninguna"
                    )
                )
                details[1].markdown(
                    "**Aplicabilidad original**  \n"
                    + (
                        ", ".join(
                            selected_goal.get("applicability_tags", [])
                        )
                        or "Ninguna"
                    )
                )
            with st.expander("Editar o eliminar objetivo seleccionado"):
                _goal_form(selected_goal)
                st.warning(
                    "Al eliminarlo se retira de las métricas y ventanas relacionadas."
                )
                confirm = st.checkbox(
                    "Confirmar eliminación del objetivo", key=f"confirm_goal_{selected}"
                )
                if st.button(
                    "Eliminar objetivo del catálogo",
                    disabled=not confirm,
                    key=f"delete_goal_{selected}",
                ):
                    delete_development_goal(selected)
                    _rerun()

    with metric_tab:
        if metrics:
            st.caption(
                "Selecciona una fila para consultar qué mide y cómo se calcula."
            )
            _render_catalog_table(
                pd.DataFrame(
                    [
                        {
                            "Métrica": _catalog_name(item),
                            "Disciplinas": ", ".join(item.get("disciplines", [])),
                            "Modalidad": item.get("modality"),
                            "Unidad": item.get("unit"),
                            "Dirección": {
                                value: label for label, value in DIRECTIONS.items()
                            }.get(item.get("direction"), item.get("direction")),
                            "Extracción": EXTRACTION_LABELS.get(
                                item.get("extraction_type"),
                                item.get("extraction_type"),
                            ),
                        }
                        for item in metrics
                    ]
                ),
                metrics,
                "selected_metric_catalog_id",
                "metric_catalog_table",
            )
        else:
            st.info("El catálogo de métricas está vacío.")
        if not goals:
            st.caption(
                "Puedes crear métricas ahora y relacionarlas con objetivos después."
            )
        with st.expander("Agregar métrica al catálogo", expanded=not metrics):
            _metric_form(goals)
        if metrics:
            selected_metric = _selected_catalog_item(
                metrics,
                "selected_metric_catalog_id",
            )
            selected = selected_metric["id"]
            with st.expander(
                f"Descripción y medición · {_catalog_name(selected_metric)}",
                expanded=True,
            ):
                st.markdown("**Qué mide**")
                st.write(
                    selected_metric.get("what_it_measures")
                    or selected_metric.get("description")
                    or "No definido."
                )
                st.markdown("**Para qué sirve**")
                st.write(
                    selected_metric.get("why_it_matters")
                    or "No definido."
                )
                st.markdown("**Cómo interpretarla**")
                st.write(
                    selected_metric.get("how_to_interpret")
                    or "No definido."
                )
                st.markdown("**Cómo se calcula**")
                st.write(
                    selected_metric.get("calculation_es")
                    or selected_metric.get("measurement_method_es")
                    or translate_calculation(
                        selected_metric.get("calculation")
                        or selected_metric.get("measurement_method")
                        or ""
                    )
                    or "No definido."
                )
                details = st.columns(2)
                with details[0]:
                    st.markdown("**Datos requeridos**")
                    for item in selected_metric.get("required_data", []):
                        st.markdown(f"- {translate_required_data(item)}")
                with details[1]:
                    st.markdown("**Equipamiento especial**")
                    for item in selected_metric.get("special_equipment", []):
                        st.markdown(f"- {item}")
                st.caption(
                    "Disciplinas: "
                    + (
                        ", ".join(selected_metric.get("disciplines", []))
                        or "Ninguna"
                    )
                )
            with st.expander("Editar o eliminar métrica seleccionada"):
                _metric_form(goals, selected_metric)
                linked = sum(
                    item.get("metric_id") == selected
                    for item in load_measurements()
                )
                st.warning(
                    f"Eliminarla también borra {linked} medición(es) y todas "
                    "sus referencias en el calendario."
                )
                confirm = st.checkbox(
                    "Confirmar eliminación de la métrica",
                    key=f"confirm_catalog_metric_{selected}",
                )
                if st.button(
                    "Eliminar métrica del catálogo",
                    disabled=not confirm,
                    key=f"delete_catalog_metric_{selected}",
                ):
                    delete_metric(selected)
                    _rerun()


def _window_overlaps(windows):
    ordered = sorted(windows, key=lambda item: item["start_date"])
    overlaps = []
    for previous, current in zip(ordered, ordered[1:]):
        if current["start_date"] <= previous["end_date"]:
            overlaps.append(f"{previous['name']} / {current['name']}")
    return overlaps


def _read_optional_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _render_html(markup):
    """Render styled HTML across both current and legacy Streamlit versions."""
    if hasattr(st, "html"):
        st.html(markup)
    else:
        st.markdown(markup, unsafe_allow_html=True)


def _latest_prediction_snapshots(payload):
    """Keep the latest generated prediction for every modelled week."""
    latest = {}
    for prediction in payload.get("predictions", []):
        daily_loads = prediction.get("daily_loads", {})
        week_key = prediction.get("week_start") or (
            min(daily_loads) if daily_loads else None
        )
        generated = prediction.get("generated_at", "")
        if week_key and (
            week_key not in latest
            or generated >= latest[week_key].get("generated_at", "")
        ):
            latest[week_key] = prediction
    return list(latest.values())


def _session_summary(session, source):
    details = session.get("prediction", session)
    if source == "performed":
        details = session.get("activity", session.get("source", session))
    sport = (
        details.get("sport")
        or details.get("activity_type")
        or details.get("discipline")
        or "Entrenamiento"
    )
    workout = (
        details.get("template")
        or details.get("session_type")
        or details.get("name")
        or details.get("objective_class")
    )
    if source == "performed":
        modality = details.get("cycling_modality")
        if modality:
            sport = f"{sport} · {modality}"
    distance = details.get("distance_km")
    zone = details.get("zone")
    if source == "performed" and not zone:
        durations = session.get("zone_distribution", {}).get("duration_by_load_zone_minutes", {})
        if durations:
            zone = max(durations, key=lambda item: durations[item])
    parts = [str(sport)]
    if workout:
        parts.append(str(workout))
    if isinstance(distance, (int, float)):
        parts.append(f"{distance:.1f} km" if abs(distance - round(distance)) > .01 else f"{distance:.0f} km")
    if zone:
        parts.append(str(zone))
    return " · ".join(parts)


def _calendar_session_detail(session, source, adapted=False):
    """Return structured session information for the large calendar cards."""
    details = session.get("prediction", session)
    if source == "performed":
        details = session.get("activity", session.get("source", session))
    sport = details.get("sport") or details.get("activity_type") or "Entrenamiento"
    if source == "performed" and details.get("cycling_modality"):
        sport = f"{sport} · {details['cycling_modality']}"
    zone = details.get("zone")
    if source == "performed" and not zone:
        durations = session.get("zone_distribution", {}).get("duration_by_load_zone_minutes", {})
        if durations:
            zone = max(durations, key=lambda item: durations[item])
    duration = details.get("estimated_duration_minutes")
    if duration is None and source == "predicted":
        duration = sum(
            float(item.get("total_duration_minutes") or 0)
            for item in session.get("load", {}).get("components", [])
        ) or None
    if duration is None and source == "performed":
        duration = float(details.get("duration_seconds") or 0) / 60 or None
    load = (
        details.get("estimated_load")
        if adapted else session.get("load", {}).get("calculated_load")
    )
    return {
        "adapted": adapted, "source": source, "sport": sport,
        "session_type": details.get("session_type"), "zone": zone,
        "terrain": details.get("terrain") or session.get("resolved_terrain"),
        "distance_km": details.get("distance_km"), "duration_minutes": duration,
        "template": details.get("template"), "load": load,
    }


def _build_calendar_days(windows, metric_map):
    """Join planning, prediction and performed data by calendar date."""
    days = defaultdict(
        lambda: {
            "windows": [],
            "metrics": [],
            "predicted_sessions": [],
            "predicted_session_details": [],
            "performed_sessions": [],
            "performed_session_details": [],
            "evaluations": [],
            "predicted_load": None,
            "performed_load": None,
            "performed_status": None,
        }
    )
    assigned_metric_windows = defaultdict(list)
    for window in windows:
        start = date.fromisoformat(window["start_date"])
        end = date.fromisoformat(window["end_date"])
        cursor = start
        while cursor <= end:
            days[cursor]["windows"].append(window)
            cursor += timedelta(days=1)
        for assignment in window.get("assignments", []):
            for metric_assignment in assignment.get("metrics", []):
                assigned_metric_windows[
                    metric_assignment.get("metric_id")
                ].append((start, end, window))

    for measurement in load_measurements():
        metric_id = measurement.get("metric_id")
        try:
            measured_on = date.fromisoformat(measurement["date"])
        except (KeyError, TypeError, ValueError):
            continue
        for start, end, window in assigned_metric_windows.get(metric_id, []):
            if start <= measured_on <= end:
                metric = metric_map.get(metric_id, {})
                days[measured_on]["metrics"].append(
                    {
                        "name": _catalog_name(metric),
                        "unit": metric.get("unit", ""),
                        "value": measurement.get("value"),
                        "source": measurement.get("source"),
                        "window": window["name"],
                    }
                )

    predicted = _read_optional_json(PREDICTED_LOAD_FILE)
    for snapshot in _latest_prediction_snapshots(predicted):
        for day_text, values in snapshot.get("daily_loads", {}).items():
            try:
                calendar_day = date.fromisoformat(day_text)
            except (TypeError, ValueError):
                continue
            days[calendar_day]["predicted_load"] = values.get("predicted_load")
        for session in snapshot.get("sessions", []):
            try:
                calendar_day = date.fromisoformat(session["date"])
            except (KeyError, TypeError, ValueError):
                continue
            days[calendar_day]["predicted_sessions"].append(
                _session_summary(session, "predicted")
            )
            days[calendar_day]["predicted_session_details"].append(
                _calendar_session_detail(session, "predicted")
            )

    # An approved adaptive plan supersedes the model prediction visually for
    # the same dates while the immutable base prediction remains archived.
    adaptive = _read_optional_json(ADAPTIVE_PLANS_FILE)
    for plan in adaptive.get("plans", []):
        if plan.get("status") != "active":
            continue
        plan_days = {item.get("date") for item in plan.get("sessions", [])}
        for calendar_day in list(days):
            if calendar_day.isoformat() in plan_days:
                days[calendar_day]["predicted_sessions"] = []
                days[calendar_day]["predicted_session_details"] = []
                days[calendar_day]["predicted_load"] = None
        for session in plan.get("sessions", []):
            try:
                calendar_day = date.fromisoformat(session["date"])
            except (KeyError, TypeError, ValueError):
                continue
            days[calendar_day]["predicted_sessions"].append(
                "Adaptado · " + _session_summary(session, "predicted")
            )
            days[calendar_day]["predicted_session_details"].append(
                _calendar_session_detail(session, "predicted", adapted=True)
            )
            days[calendar_day]["predicted_load"] = plan.get("daily_loads", {}).get(session["date"])

    performed = _read_optional_json(PERFORMED_LOAD_FILE)
    for day_text, values in performed.get("daily_loads", {}).items():
        try:
            calendar_day = date.fromisoformat(day_text)
        except (TypeError, ValueError):
            continue
        days[calendar_day]["performed_load"] = values.get("calculated_load")
        days[calendar_day]["performed_status"] = values.get("quality_status")
    for activity in performed.get("activities", []):
        try:
            calendar_day = date.fromisoformat(activity["date"])
        except (KeyError, TypeError, ValueError):
            continue
        days[calendar_day]["performed_sessions"].append(
            _session_summary(activity, "performed")
        )
        days[calendar_day]["performed_session_details"].append(
            _calendar_session_detail(activity, "performed")
        )
    return days


def _evaluation_session_score(metric, evaluation, session, candidate_day, target):
    """Return a compatibility score or None when a session cannot host the test."""
    sport = session.get("sport")
    allowed_disciplines = (
        [evaluation["discipline"]] if evaluation.get("discipline")
        else metric.get("disciplines", [])
    )
    if sport == "Descanso" or (allowed_disciplines and sport not in allowed_disciplines):
        return None
    required_zone = evaluation.get("heart_rate_zone_id")
    planned_zone = session.get("zone")
    if required_zone and planned_zone and planned_zone != required_zone:
        return None
    protocol = evaluation.get("protocol") or {}
    minimum_slope = protocol.get("minimum_slope_percent")
    maximum_slope = protocol.get("maximum_slope_percent")
    terrain = str(session.get("terrain") or "").lower()
    if isinstance(minimum_slope, (int, float)) and isinstance(maximum_slope, (int, float)):
        flat_test = minimum_slope <= 0 <= maximum_slope and maximum_slope - minimum_slope <= 3
        climbing_test = minimum_slope >= 5
        if flat_test and terrain in {"montaña", "subida"}:
            return None
        if climbing_test and terrain == "plano":
            return None
    distance_days = abs((candidate_day - target).days)
    zone_penalty = 0 if not required_zone or planned_zone == required_zone else 2
    terrain_penalty = 0
    if isinstance(minimum_slope, (int, float)) and minimum_slope >= 5:
        terrain_penalty = 0 if terrain in {"montaña", "subida"} else 2
    return (distance_days, zone_penalty, terrain_penalty, candidate_day)


def _schedule_calendar_evaluations(days, freshness, metric_map):
    """Attach each protocol to a nearby compatible planned session."""
    unscheduled = []
    today = date.today()
    for item in freshness:
        metric = metric_map.get(item.get("metric_id"), {})
        target_text = item.get("target_date")
        try:
            target = date.fromisoformat(target_text) if target_text else today
        except (TypeError, ValueError):
            target = today
        target = max(today, target)
        protocol = item.get("protocol") or {
            "title": metric.get("measurement_method") or "Protocolo no especificado",
            "steps": [],
        }
        evaluation = {
            "metric_id": item.get("metric_id"), "metric_name": item.get("metric_name"),
            "target_date": target.isoformat(), "status": item.get("status"),
            "status_icon": item.get("status_icon"), "protocol": protocol,
            "series": [item.get("series_label") or "Serie general"],
            "discipline": item.get("discipline"),
            "heart_rate_zone_id": item.get("heart_rate_zone_id"),
            "slope_band": item.get("slope_band"),
        }
        candidates = []
        for candidate_day, info in list(days.items()):
            if candidate_day < today or abs((candidate_day - target).days) > 7:
                continue
            for session in info.get("predicted_session_details", []):
                score = _evaluation_session_score(
                    metric, evaluation, session, candidate_day, target
                )
                if score is not None:
                    candidates.append((score, candidate_day, session))
        if not candidates:
            evaluation["reason"] = "No existe una sesión prevista compatible dentro de ±7 días."
            unscheduled.append(evaluation)
            continue
        _, scheduled_day, matched_session = min(candidates, key=lambda item: item[0])
        evaluation["scheduled_date"] = scheduled_day.isoformat()
        evaluation["matched_session"] = {
            "sport": matched_session.get("sport"),
            "session_type": matched_session.get("session_type"),
            "zone": matched_session.get("zone"),
            "terrain": matched_session.get("terrain"),
            "distance_km": matched_session.get("distance_km"),
        }
        days[scheduled_day]["evaluations"].append(evaluation)
    return unscheduled


def _render_timeline(event, windows, goal_map):
    st.markdown("### Línea de tiempo")
    if not windows:
        st.caption("Crea una ventana para comenzar a diseñar el ciclo.")
        return
    starts = [date.fromisoformat(item["start_date"]) for item in windows]
    ends = [date.fromisoformat(item["end_date"]) for item in windows]
    event_date = (
        date.fromisoformat(event["event_date"]) if event.get("event_date") else max(ends)
    )
    timeline_start = min(starts + [date.today()])
    timeline_end = max(ends + [event_date])
    total_days = max(1, (timeline_end - timeline_start).days + 1)
    cards = []
    for index, window in enumerate(sorted(windows, key=lambda item: item["start_date"])):
        start = date.fromisoformat(window["start_date"])
        end = date.fromisoformat(window["end_date"])
        left = (start - timeline_start).days / total_days * 100
        width = max(2.5, ((end - start).days + 1) / total_days * 100)
        goals = [
            _catalog_name(goal_map.get(item.get("development_goal_id"), {}))
            for item in window.get("assignments", [])
        ]
        subtitle = " · ".join(goals[:2]) if goals else "Sin objetivos asignados"
        cards.append(
            f'<div class="cycle" style="left:{left:.2f}%;width:{width:.2f}%;'
            f'background:{CALENDAR_COLORS[index % len(CALENDAR_COLORS)]}">'
            f'<strong>{html.escape(window["name"])}</strong>'
            f'<span>{html.escape(subtitle)}</span></div>'
        )
    today_left = (date.today() - timeline_start).days / total_days * 100
    event_left = (event_date - timeline_start).days / total_days * 100
    _render_html(
        f"""
        <style>
          .timeline-shell {{background:#F8FAFC;border:1px solid #E2E8F0;border-radius:16px;
            padding:20px 18px 14px;margin:6px 0 18px;overflow:hidden}}
          .timeline-labels {{display:flex;justify-content:space-between;color:#64748B;
            font-size:12px;margin-bottom:12px}}
          .timeline-track {{position:relative;height:92px;border-radius:12px;
            background:linear-gradient(90deg,#EEF2FF,#F8FAFC);border:1px solid #E2E8F0}}
          .cycle {{position:absolute;top:19px;height:54px;border-radius:9px;padding:8px 10px;
            color:white;box-sizing:border-box;overflow:hidden;box-shadow:0 5px 12px #0F172A20}}
          .cycle strong,.cycle span {{display:block;white-space:nowrap;overflow:hidden;
            text-overflow:ellipsis}} .cycle strong {{font-size:13px}} .cycle span {{font-size:10px;
            opacity:.86;margin-top:3px}}
          .marker {{position:absolute;top:0;height:92px;width:2px;background:#0F172A}}
          .marker.event {{background:#E11D48}} .marker-label {{position:absolute;top:-18px;
            transform:translateX(-50%);font-size:10px;font-weight:700;color:#475569}}
        </style>
        <div class="timeline-shell">
          <div class="timeline-labels"><span>{timeline_start.strftime("%d/%m/%Y")}</span>
          <span>{timeline_end.strftime("%d/%m/%Y")}</span></div>
          <div class="timeline-track">
            {''.join(cards)}
            <div class="marker" style="left:{today_left:.2f}%"><span class="marker-label">HOY</span></div>
            <div class="marker event" style="left:{event_left:.2f}%"><span class="marker-label">EVENTO</span></div>
          </div>
        </div>
        """
    )


def _calendar_badges(info):
    badges = []
    if info["windows"]:
        badges.append(("cycle-badge", info["windows"][0]["name"]))
    if info["performed_sessions"]:
        badges.append(("performed-badge", f"✓ {info['performed_sessions'][0]}"))
    if info["metrics"]:
        metric = info["metrics"][0]
        value = metric.get("value")
        result = "" if value is None else f": {value} {metric.get('unit', '')}"
        badges.append(("metric-badge", f"◆ {metric['name']}{result}"))
    return badges


def _calendar_training_cards(info):
    cards = []
    for session in info.get("predicted_session_details", []):
        sport = str(session.get("sport") or "Entrenamiento")
        session_type = session.get("session_type") or (
            "Descanso" if sport == "Descanso" else "Sesión prevista"
        )
        facts = []
        distance = session.get("distance_km")
        if isinstance(distance, (int, float)):
            facts.append(f"{distance:.1f} km" if abs(distance - round(distance)) > .01 else f"{distance:.0f} km")
        if session.get("zone"):
            facts.append(str(session["zone"]))
        if session.get("terrain"):
            facts.append(str(session["terrain"]))
        duration = session.get("duration_minutes")
        if isinstance(duration, (int, float)) and duration > 0:
            facts.append(f"{duration:.0f} min")
        template = session.get("template")
        template_html = (
            f'<div class="training-template">{html.escape(str(template))}</div>'
            if template and template != session_type else ""
        )
        label = "PLAN ADAPTADO" if session.get("adapted") else "SESIÓN PREVISTA"
        card_class = "training-card adapted-card" if session.get("adapted") else "training-card"
        cards.append(
            f'<div class="{card_class}"><div class="training-label">{label}</div>'
            f'<div class="training-sport">{html.escape(sport)}</div>'
            f'<div class="training-type">{html.escape(str(session_type))}</div>'
            f'<div class="training-facts">{html.escape(" · ".join(facts))}</div>'
            f'{template_html}</div>'
        )
    return "".join(cards)


def _calendar_evaluation_cards(info):
    cards = []
    evaluations = info.get("evaluations", [])
    for evaluation in evaluations[:2]:
        protocol = evaluation.get("protocol") or {}
        title = protocol.get("title") or "Protocolo de evaluación"
        facts = []
        duration = protocol.get("duration_seconds")
        if isinstance(duration, (int, float)):
            facts.append(f"{duration / 60:g} min")
        zones = protocol.get("heart_rate_zone_ids") or []
        if zones:
            facts.append("/".join(zones))
        slopes = protocol.get("slope_bands") or []
        if slopes:
            facts.append("Pendiente " + "/".join(slopes))
        series = " · ".join(evaluation.get("series", []))
        cards.append(
            '<div class="evaluation-card">'
            f'<div class="evaluation-label">EVALUACIÓN ASOCIADA · {html.escape(str(evaluation.get("status") or ""))}</div>'
            f'<div class="evaluation-name">{html.escape(str(evaluation.get("metric_name") or "Métrica"))}</div>'
            f'<div class="evaluation-protocol">{html.escape(str(title))}</div>'
            f'<div class="evaluation-facts">{html.escape(" · ".join(facts))}</div>'
            f'<div class="evaluation-series">{html.escape(series)}</div>'
            '</div>'
        )
    if len(evaluations) > 2:
        cards.append(
            f'<div class="evaluation-more">+{len(evaluations) - 2} protocolos más · ver detalle del día</div>'
        )
    return "".join(cards)


def _render_month_calendar(month_start, event, days, week_start=None):
    weekly = week_start is not None
    weeks = (
        [[week_start + timedelta(days=offset) for offset in range(7)]]
        if weekly else calendar.Calendar(firstweekday=0).monthdatescalendar(
            month_start.year, month_start.month
        )
    )
    event_day = (
        date.fromisoformat(event["event_date"]) if event.get("event_date") else None
    )
    cells = []
    for week in weeks:
        for calendar_day in week:
            info = days[calendar_day]
            classes = ["day-cell"]
            if not weekly and calendar_day.month != month_start.month:
                classes.append("outside")
            if calendar_day == date.today():
                classes.append("today")
            if calendar_day == event_day:
                classes.append("event-day")
            badges = "".join(
                f'<div class="badge {css}">{html.escape(label)}</div>'
                for css, label in _calendar_badges(info)
            )
            training_cards = _calendar_training_cards(info)
            evaluation_cards = _calendar_evaluation_cards(info)
            load_text = ""
            predicted_load = info["predicted_load"]
            performed_load = info["performed_load"]
            if predicted_load is not None or performed_load is not None:
                predicted_label = "—" if predicted_load is None else f"{predicted_load:.0f}"
                performed_label = "—" if performed_load is None else f"{performed_load:.0f}"
                load_text = (
                    f'<div class="load-row"><span>Carga</span>'
                    f'<strong>{predicted_label} / {performed_label}</strong></div>'
                )
            cells.append(
                f'<div class="{" ".join(classes)}"><div class="day-number">'
                f'{calendar_day.day}</div>{badges}{evaluation_cards}{training_cards}{load_text}</div>'
            )
    weekday_labels = ("LUN", "MAR", "MIÉ", "JUE", "VIE", "SÁB", "DOM")
    weekday_names = (
        tuple(
            f"{label}<span>{day.strftime('%d/%m')}</span>"
            for label, day in zip(weekday_labels, weeks[0])
        )
        if weekly else weekday_labels
    )
    minimum_width = 1540 if weekly else 1680
    cell_height = 500 if weekly else 330
    _render_html(
        f"""
        <style>
          .calendar-scroll {{width:100%;max-width:100%;overflow-x:auto;padding-bottom:8px;
            scrollbar-color:#94A3B8 #E2E8F0;scrollbar-width:thin}}
          .calendar-wrap {{width:100%;min-width:{minimum_width}px;box-sizing:border-box;border:1px solid #E2E8F0;border-radius:16px;overflow:hidden;
            box-shadow:0 8px 24px #0F172A0C;background:white}}
          .week-head,.month-grid {{display:grid;grid-template-columns:repeat(7,minmax(0,1fr));
            width:100%;max-width:100%}}
          .week-head div {{padding:16px;text-align:center;background:#F8FAFC;color:#64748B;
            font-size:14px;font-weight:700;letter-spacing:.08em}}
          .week-head span {{display:block;font-size:11px;letter-spacing:0;color:#94A3B8;margin-top:4px}}
          .day-cell {{position:relative;min-width:0;max-width:100%;min-height:{cell_height}px;padding:16px 16px 48px;
            overflow:hidden;border-top:1px solid #E2E8F0;border-right:1px solid #E2E8F0;
            box-sizing:border-box}}
          .day-cell:nth-child(7n) {{border-right:none}} .day-cell.outside {{background:#F8FAFC;
            color:#94A3B8}} .day-cell.today {{box-shadow:inset 0 0 0 2px #2563EB}}
          .day-cell.event-day {{background:#FFF1F2;box-shadow:inset 0 0 0 2px #E11D48}}
          .day-number {{font-size:19px;font-weight:800;color:#243247;margin-bottom:12px}}
          .badge {{display:block;width:100%;max-width:100%;box-sizing:border-box;
            font-size:13px;line-height:1.4;padding:7px 9px;border-radius:7px;margin:7px 0;
            white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
          .cycle-badge {{background:#EEF2FF;color:#4338CA}} .predicted-badge {{background:#EFF6FF;
            color:#1D4ED8;border:1px dashed #60A5FA}} .performed-badge {{background:#ECFDF5;color:#047857}}
          .metric-badge {{background:#FFF7ED;color:#C2410C}}
          .training-card {{margin:9px 0;padding:11px 12px;border-radius:10px;background:#EFF6FF;
            border:1px solid #93C5FD;border-left:5px solid #2563EB;box-shadow:0 4px 10px #1D4ED80D}}
          .training-card.adapted-card {{background:#ECFEFF;border-color:#67E8F9;border-left-color:#0891B2}}
          .training-label {{font-size:9px;font-weight:800;letter-spacing:.09em;color:#2563EB;margin-bottom:5px}}
          .adapted-card .training-label {{color:#0E7490}}
          .training-sport {{font-size:16px;font-weight:800;color:#172033;line-height:1.2}}
          .training-type {{font-size:13px;font-weight:650;color:#334155;margin-top:3px;line-height:1.3}}
          .training-facts {{font-size:12px;font-weight:700;color:#1D4ED8;margin-top:7px;line-height:1.35}}
          .adapted-card .training-facts {{color:#0E7490}}
          .training-template {{font-size:10px;color:#64748B;margin-top:7px;line-height:1.35;
            display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}}
          .evaluation-card {{margin:9px 0;padding:10px 11px;border-radius:10px;background:#FFF7ED;
            border:1px solid #FDBA74;border-left:5px solid #F97316}}
          .evaluation-label {{font-size:9px;font-weight:800;letter-spacing:.07em;color:#C2410C}}
          .evaluation-name {{font-size:13px;font-weight:800;color:#7C2D12;margin-top:4px;line-height:1.25}}
          .evaluation-protocol {{font-size:11px;color:#9A3412;margin-top:4px;line-height:1.3}}
          .evaluation-facts {{font-size:10px;font-weight:700;color:#C2410C;margin-top:5px}}
          .evaluation-series {{font-size:9px;color:#78716C;margin-top:4px;white-space:nowrap;
            overflow:hidden;text-overflow:ellipsis}}
          .evaluation-more {{font-size:10px;font-weight:700;color:#C2410C;margin:5px 0 10px}}
          .load-row {{position:absolute;bottom:13px;left:15px;right:15px;display:flex;
            justify-content:space-between;font-size:13px;color:#64748B}}
          .load-row strong {{color:#0F172A}}
          @media(max-width:800px) {{
            .calendar-wrap {{min-width:1260px}}
            .week-head div {{padding:8px 3px;font-size:10px}}
            .day-cell {{min-height:230px;padding:8px 8px 30px}}
            .day-number {{font-size:12px;margin-bottom:5px}}
            .badge {{font-size:8px;padding:3px 4px;margin:3px 0}}
            .training-card {{padding:7px;margin:5px 0;border-left-width:3px}}
            .training-sport {{font-size:12px}} .training-type {{font-size:10px}}
            .training-facts {{font-size:9px}} .training-template {{font-size:8px;-webkit-line-clamp:2}}
            .evaluation-card {{padding:7px;margin:5px 0;border-left-width:3px}}
            .evaluation-name {{font-size:10px}} .evaluation-protocol {{font-size:8px}}
            .evaluation-facts,.evaluation-series,.evaluation-label {{font-size:7px}}
            .load-row {{left:6px;right:6px;bottom:5px;font-size:8px}}
          }}
        </style>
        <div class="calendar-scroll">
          <div class="calendar-wrap">
            <div class="week-head">{''.join(f"<div>{item}</div>" for item in weekday_names)}</div>
            <div class="month-grid">{''.join(cells)}</div>
          </div>
        </div>
        """
    )


def _render_day_detail(selected_day, event, days):
    info = days[selected_day]
    st.markdown(f"#### {selected_day.strftime('%d/%m/%Y')}")
    if event.get("event_date") == selected_day.isoformat():
        st.success(f"Evento objetivo: {event['name']}")
    columns = st.columns(2)
    with columns[0]:
        st.markdown("**Planificación**")
        if info["windows"]:
            for window in info["windows"]:
                st.write(
                    f"**{window['name']}** · "
                    f"{window.get('description') or 'Sin descripción'}"
                )
        else:
            st.caption("Fuera de una ventana planificada.")
        for metric in info["metrics"]:
            value = metric.get("value")
            value_label = "Sin valor" if value is None else f"{value} {metric['unit']}"
            st.write(
                f"◆ {metric['name']}: **{value_label.strip()}** "
                f"· {SOURCE_LABELS.get(metric.get('source'), metric.get('source', ''))}"
            )
        for evaluation in info.get("evaluations", []):
            protocol = evaluation.get("protocol") or {}
            with st.expander(
                f"Evaluación · {evaluation.get('metric_name')} · {evaluation.get('status')}",
                expanded=True,
            ):
                st.markdown(f"**{protocol.get('title') or 'Protocolo de evaluación'}**")
                st.caption("Series: " + " · ".join(evaluation.get("series", [])))
                matched = evaluation.get("matched_session") or {}
                st.success(
                    "Asociada a: "
                    f"{matched.get('sport', '—')} · {matched.get('session_type') or 'sesión prevista'} · "
                    f"{matched.get('zone') or 'sin zona'} · {matched.get('terrain') or 'sin terreno'}"
                )
                st.caption(f"Fecha objetivo original: {evaluation.get('target_date', '—')}")
                protocol_facts = []
                if protocol.get("duration_seconds") is not None:
                    protocol_facts.append(f"Duración: {float(protocol['duration_seconds']) / 60:g} min")
                if protocol.get("heart_rate_zone_ids"):
                    protocol_facts.append("Zona: " + "/".join(protocol["heart_rate_zone_ids"]))
                if protocol.get("slope_bands"):
                    protocol_facts.append("Pendiente: " + "/".join(protocol["slope_bands"]))
                if protocol_facts:
                    st.write(" · ".join(protocol_facts))
                for number, step in enumerate(protocol.get("steps", []), start=1):
                    st.write(f"{number}. {step}")
    with columns[1]:
        st.markdown("**Entrenamiento y carga**")
        for session in info["predicted_sessions"]:
            st.write(f"Previsto · {session}")
        for session in info["performed_sessions"]:
            st.write(f"✓ Realizado · {session}")
        if not info["predicted_sessions"] and not info["performed_sessions"]:
            st.caption("No hay entrenamientos para este día.")
        predicted = info["predicted_load"]
        performed = info["performed_load"]
        predicted_label = "Sin dato" if predicted is None else f"**{predicted:.1f}**"
        performed_label = "Sin dato" if performed is None else f"**{performed:.1f}**"
        st.markdown(
            f"Carga prevista: {predicted_label} · realizada: {performed_label}"
        )


def _zone_cards(profile):
    cards = []
    for zone in profile["zones"]:
        zone_id = zone["id"]
        cards.append(
            f'<div class="hr-zone-card" style="border-top-color:{ZONE_COLORS[zone_id]}">'
            f'<strong style="color:{ZONE_COLORS[zone_id]}">{zone_id}</strong>'
            f'<span>{zone["lower_bpm"]}–{zone["upper_bpm"]} bpm</span></div>'
        )
    _render_html(
        f"""
        <style>
          .hr-zone-grid {{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));
            gap:10px;margin:8px 0 18px}}
          .hr-zone-card {{background:#F8FAFC;border:1px solid #E2E8F0;border-top:6px solid;
            border-radius:10px;padding:13px 10px;text-align:center;box-sizing:border-box}}
          .hr-zone-card strong,.hr-zone-card span {{display:block}}
          .hr-zone-card strong {{font-size:17px;margin-bottom:5px}}
          .hr-zone-card span {{font-size:12px;color:#334155;white-space:nowrap}}
          @media(max-width:800px) {{
            .hr-zone-grid {{grid-template-columns:repeat(3,minmax(0,1fr))}}
          }}
        </style>
        <div class="hr-zone-grid">{''.join(cards)}</div>
        """
    )


def _render_heart_rate_zones():
    st.markdown("### Zonas personales de frecuencia cardiaca")
    st.write(
        "Los perfiles se versionan por fecha. Las métricas y actividades deben usar "
        "el perfil que estaba vigente el día del entrenamiento."
    )
    profiles = load_zone_profiles()
    if profiles:
        profile_map = _by_id(profiles)
        selected_profile_id = st.selectbox(
            "Perfil",
            tuple(reversed(tuple(profile_map))),
            format_func=lambda item: (
                f"{profile_map[item]['name']} · vigente desde "
                f"{profile_map[item]['effective_from']}"
            ),
            key="heart_rate_zone_profile_selector",
        )
        selected_profile = profile_map[selected_profile_id]
        _zone_cards(selected_profile)
        current = profile_for_date(date.today())
        if current and current["id"] == selected_profile_id:
            st.success("Este es el perfil vigente hoy.")
        elif current:
            st.caption(
                f"Perfil vigente hoy: {current['name']} "
                f"(desde {current['effective_from']})."
            )
    else:
        selected_profile = None
        st.info("Todavía no has guardado un perfil de zonas.")

    default_limits = (
        [(item["lower_bpm"], item["upper_bpm"]) for item in selected_profile["zones"]]
        if selected_profile
        else [(80, 109), (110, 129), (130, 149), (150, 169), (170, 189), (190, 220)]
    )
    form_label = "Crear una nueva versión" if profiles else "Crear primer perfil"
    with st.expander(form_label, expanded=not profiles):
        st.caption(
            "Si tus zonas cambian, crea una nueva versión. No edites el perfil "
            "anterior: así se conserva la interpretación histórica."
        )
        with st.form("heart_rate_zone_profile_form"):
            form_columns = st.columns(2)
            with form_columns[0]:
                profile_name = st.text_input(
                    "Nombre del perfil",
                    value="Zonas personales",
                )
            with form_columns[1]:
                effective_from = st.date_input(
                    "Vigente desde",
                    value=date.today(),
                )
            zones = []
            for index, zone_id in enumerate(ZONE_IDS):
                columns = st.columns([1, 2, 2])
                columns[0].markdown(
                    f"<div style='margin-top:30px;font-weight:700;color:"
                    f"{ZONE_COLORS[zone_id]}'>{zone_id}</div>",
                    unsafe_allow_html=True,
                )
                lower = columns[1].number_input(
                    f"{zone_id} · mínimo (bpm)",
                    min_value=30,
                    max_value=250,
                    value=int(default_limits[index][0]),
                    key=f"hr_zone_lower_{zone_id}",
                )
                upper = columns[2].number_input(
                    f"{zone_id} · máximo (bpm)",
                    min_value=30,
                    max_value=250,
                    value=int(default_limits[index][1]),
                    key=f"hr_zone_upper_{zone_id}",
                )
                zones.append(
                    {
                        "id": zone_id,
                        "lower_bpm": int(lower),
                        "upper_bpm": int(upper),
                        "color": ZONE_COLORS[zone_id],
                    }
                )
            submitted = st.form_submit_button(
                "Guardar nueva versión",
                use_container_width=True,
            )
        if submitted:
            try:
                save_zone_profile(
                    {
                        "name": profile_name,
                        "effective_from": effective_from.isoformat(),
                        "zones": zones,
                    }
                )
            except ValueError as error:
                st.error(str(error))
            else:
                _rerun()

    if profiles:
        with st.expander("Eliminar perfil seleccionado"):
            st.warning(
                "Eliminar un perfil puede cambiar la interpretación histórica "
                "de actividades y métricas."
            )
            confirm = st.checkbox(
                "Confirmar eliminación",
                key=f"confirm_hr_profile_{selected_profile_id}",
            )
            if st.button(
                "Eliminar perfil",
                disabled=not confirm,
                key=f"delete_hr_profile_{selected_profile_id}",
            ):
                delete_zone_profile(selected_profile_id)
                _rerun()


def _render_calendar():
    events = load_events()
    goals = load_development_goals()
    metrics = load_metrics()
    if not events:
        st.info("Crea un evento antes de definir ventanas del calendario.")
        return
    event_map = _by_id(events)
    goal_map = _by_id(goals)
    metric_map = _by_id(metrics)
    event_id = st.selectbox(
        "Evento",
        tuple(event_map),
        format_func=lambda item: event_map[item]["name"],
        key="calendar_event_selector",
    )
    windows = [
        item for item in load_windows() if item.get("event_id") == event_id
    ]
    event = event_map[event_id]
    overlaps = _window_overlaps(windows)
    if overlaps:
        st.warning("Ventanas superpuestas: " + ", ".join(overlaps))

    _render_timeline(event, windows, goal_map)
    assigned_metric_ids = {
        metric_assignment.get("metric_id")
        for window in windows
        for assignment in window.get("assignments", [])
        for metric_assignment in assignment.get("metrics", [])
    }
    freshness = metric_freshness(
        [metric_map[item] for item in assigned_metric_ids if item in metric_map],
        load_calculated_measurements(),
    )
    assigned_metric_disciplines = {
        (metric_assignment.get("metric_id"), metric_assignment.get("discipline"))
        for window in windows
        for assignment in window.get("assignments", [])
        for metric_assignment in assignment.get("metrics", [])
    }
    freshness = [
        item for item in freshness
        if not metric_map.get(item["metric_id"], {}).get("separate_by_discipline", True)
        or (item["metric_id"], item.get("discipline")) in assigned_metric_disciplines
    ]
    freshness.sort(
        key=lambda item: (
            item.get("target_date") is not None,
            item.get("target_date") or "",
            item.get("metric_name", ""),
            item.get("series_label", ""),
        )
    )
    if freshness:
        st.markdown("### Próximas evaluaciones")
        st.caption(
            "Estado de las métricas asignadas a los ciclos de este evento. "
            "El objetivo es obtener una observación comparable cada 28 días."
        )
        st.dataframe(
            pd.DataFrame([
                {
                    "Métrica": item["metric_name"],
                    "Serie": item["series_label"],
                    "Estado": f"{item['status_icon']} {item['status']}",
                    "Última medición": item["latest_date"] or "—",
                    "Próxima evaluación": item["target_date"] or "Realizar evaluación inicial",
                }
                for item in freshness
            ]),
            use_container_width=True,
            hide_index=True,
        )
    calendar_days = _build_calendar_days(windows, metric_map)
    unscheduled_evaluations = _schedule_calendar_evaluations(
        calendar_days, freshness, metric_map
    )
    if unscheduled_evaluations:
        with st.expander(
            f"Evaluaciones pendientes de calendarizar · {len(unscheduled_evaluations)}",
            expanded=False,
        ):
            st.caption(
                "No se colocan sobre un descanso o una disciplina incompatible. "
                "La capa adaptativa puede proponer una sesión adecuada."
            )
            st.dataframe(
                pd.DataFrame([{
                    "Métrica": item["metric_name"],
                    "Serie": " · ".join(item["series"]),
                    "Estado": item["status"],
                    "Fecha objetivo": item["target_date"],
                    "Motivo": item["reason"],
                } for item in unscheduled_evaluations]),
                use_container_width=True,
                hide_index=True,
            )
    st.markdown("### Calendario")
    default_month = date.today().replace(day=1)
    view = st.segmented_control(
        "Vista", ("Mensual", "Semanal"), default="Mensual",
        key="planning_calendar_view",
    ) if hasattr(st, "segmented_control") else st.radio(
        "Vista", ("Mensual", "Semanal"), horizontal=True,
        key="planning_calendar_view",
    )
    if view == "Semanal":
        control_columns = st.columns([1, 2, 3])
        selected_week_day = control_columns[0].date_input(
            "Semana que contiene",
            value=date.today(),
            key="planning_calendar_week_day",
        )
        week_start = selected_week_day - timedelta(days=selected_week_day.weekday())
        control_columns[1].markdown(
            f"**Semana:** {week_start.strftime('%d/%m/%Y')} – "
            f"{(week_start + timedelta(days=6)).strftime('%d/%m/%Y')}"
        )
        month_start = week_start.replace(day=1)
        _render_month_calendar(
            month_start, event, calendar_days, week_start=week_start
        )
        detail_default = selected_week_day
    else:
        control_columns = st.columns([1, 1, 3])
        with control_columns[0]:
            month_number = st.selectbox(
                "Mes",
                tuple(range(1, 13)),
                index=default_month.month - 1,
                format_func=lambda item: MONTH_NAMES[item - 1],
                key="planning_calendar_month",
            )
        with control_columns[1]:
            year = st.number_input(
                "Año",
                min_value=2000,
                max_value=2100,
                value=default_month.year,
                step=1,
                key="planning_calendar_year",
            )
        month_start = date(int(year), month_number, 1)
        _render_month_calendar(month_start, event, calendar_days)
        detail_default = (
            date.today()
            if date.today().year == int(year) and date.today().month == month_number
            else month_start
        )
    st.caption(
        "Carga: prevista / realizada · Desplázate horizontalmente para conservar "
        "celdas amplias · Selecciona una fecha para consultar el detalle."
    )
    detail_day = st.date_input(
        "Detalle del día",
        value=detail_default,
        key="planning_calendar_detail_day",
    )
    _render_day_detail(detail_day, event, calendar_days)

    st.divider()
    st.markdown("### Configurar ciclos")
    with st.expander("Agregar ventana al calendario", expanded=not windows):
        _window_form(event_id)
    if not windows:
        return

    window_map = _by_id(windows)
    window_id = st.selectbox(
        "Ventana para configurar",
        tuple(window_map),
        format_func=lambda item: window_map[item]["name"],
    )
    window = window_map[window_id]
    with st.expander("Editar o eliminar ventana"):
        _window_form(event_id, window)
        confirm = st.checkbox(
            "Confirmar eliminación de la ventana", key=f"confirm_window_{window_id}"
        )
        if st.button(
            "Eliminar ventana",
            disabled=not confirm,
            key=f"delete_window_{window_id}",
        ):
            delete_window(window_id)
            _rerun()

    st.markdown("#### Énfasis por disciplina")
    st.caption(
        "Distribuye el 100% de influencia adaptativa. No son cuotas de carga ni "
        "de distancia: priorizan recetas y cambios. Si quedan iguales, las "
        "métricas y el estado del atleta deciden dónde intervenir."
    )
    event_disciplines = event.get("disciplines") or list(DISCIPLINES)
    stored_weights = window.get("discipline_weights", {})
    if not stored_weights:
        equal = round(100.0 / len(event_disciplines), 1)
        stored_weights = {item: equal for item in event_disciplines}
        stored_weights[event_disciplines[-1]] += 100.0 - sum(stored_weights.values())
    with st.form(f"discipline_weights_{window_id}"):
        priority_columns = st.columns(min(4, len(event_disciplines)))
        selected_weights = {}
        for index, discipline in enumerate(event_disciplines):
            selected_weights[discipline] = priority_columns[
                index % len(priority_columns)
            ].number_input(
                f"{discipline} (%)",
                min_value=0.0,
                max_value=100.0,
                value=float(stored_weights.get(discipline, 0)),
                step=5.0,
                key=f"discipline_weight_{window_id}_{discipline}",
            )
        save_priorities = st.form_submit_button(
            "Guardar énfasis de la ventana", use_container_width=True
        )
    if save_priorities:
        total_weight = sum(selected_weights.values())
        if abs(total_weight - 100.0) > 0.11:
            st.error(f"Los porcentajes deben sumar 100%. Total actual: {total_weight:.1f}%.")
        else:
            save_discipline_weights(window_id, selected_weights)
            _rerun()

    st.markdown("#### Asignaciones de objetivos de desarrollo")
    assignments = window.get("assignments", [])
    rows = []
    for assignment in assignments:
        goal = goal_map.get(assignment.get("development_goal_id"), {})
        rows.append(
            {
                "Objetivo": (
                    _catalog_name(goal)
                    if goal
                    else "Objetivo ausente del catálogo"
                ),
                "Prioridad": PRIORITY_LABELS.get(
                    assignment.get("priority"), assignment.get("priority")
                ),
                "Métricas": len(assignment.get("metrics", [])),
            }
        )
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    elif not goals:
        st.info("Primero agrega objetivos de desarrollo al catálogo.")
    else:
        st.info("No hay objetivos de desarrollo asignados a esta ventana.")

    if goals:
        with st.form(f"goal_assignment_{window_id}"):
            goal_id = st.selectbox(
                "Objetivo de desarrollo",
                tuple(goal_map),
                format_func=lambda item: _catalog_name(goal_map[item]),
            )
            priority = st.selectbox(
                "Prioridad en esta ventana",
                PRIORITIES,
                format_func=lambda item: PRIORITY_LABELS[item],
            )
            submitted = st.form_submit_button(
                "Agregar o actualizar asignación", use_container_width=True
            )
        if submitted:
            save_goal_assignment(window_id, goal_id, priority)
            _rerun()

    if assignments:
        assigned_goal_ids = [
            item["development_goal_id"] for item in assignments
        ]
        st.caption(
            "Quitar un objetivo de esta ventana también retira sus métricas "
            "asignadas dentro de este ciclo; el catálogo no se modifica."
        )
        for assigned_goal in assignments:
            assigned_goal_id_for_remove = assigned_goal["development_goal_id"]
            assigned_goal_name = (
                _catalog_name(goal_map[assigned_goal_id_for_remove])
                if assigned_goal_id_for_remove in goal_map
                else "Objetivo ausente del catálogo"
            )
            goal_columns = st.columns([4, 2, 1])
            goal_columns[0].write(f"**{assigned_goal_name}**")
            goal_columns[1].caption(
                PRIORITY_LABELS.get(
                    assigned_goal.get("priority"),
                    assigned_goal.get("priority", ""),
                )
            )
            if goal_columns[2].button(
                "Quitar",
                key=(
                    f"remove_goal_{window_id}_"
                    f"{assigned_goal_id_for_remove}"
                ),
            ):
                remove_goal_assignment(
                    window_id, assigned_goal_id_for_remove
                )
                _rerun()

        st.markdown("#### Asignaciones de métricas")
        assigned_goal_id = st.selectbox(
            "Objetivo de desarrollo asignado",
            assigned_goal_ids,
            format_func=lambda item: (
                _catalog_name(goal_map[item])
                if item in goal_map
                else "Objetivo ausente del catálogo"
            ),
            key=f"metric_goal_{window_id}",
        )
        assignment = next(
            item
            for item in assignments
            if item["development_goal_id"] == assigned_goal_id
        )
        compatible = {
            metric_id: metric
            for metric_id, metric in metric_map.items()
            if not metric.get("development_goal_ids")
            or assigned_goal_id in metric.get("development_goal_ids", [])
        }
        if compatible:
            metric_id = st.selectbox(
                "Métrica para asignar",
                tuple(compatible),
                format_func=lambda item: _catalog_name(compatible[item]),
                key=f"metric_selector_{window_id}_{assigned_goal_id}",
            )
            selected_metric = compatible[metric_id]
            metric_description = (
                selected_metric.get("description_es")
                or selected_metric.get("what_it_measures")
                or selected_metric.get("description")
                or "No hay una descripción disponible."
            )
            st.markdown("**Descripción**")
            st.write(metric_description)
            calculation = explain_metric_calculation(selected_metric)
            if calculation:
                st.markdown("**Cómo se calcula**")
                st.markdown(calculation)
            required_data = selected_metric.get("required_data", [])
            if required_data:
                st.markdown("**Datos requeridos**")
                st.write(
                    " · ".join(
                        translate_required_data(item) for item in required_data
                    )
                )
            supported_disciplines = [
                item
                for item in selected_metric.get("disciplines", DISCIPLINES)
                if item in DISCIPLINES
            ]
            discipline = st.selectbox(
                "Disciplina",
                supported_disciplines,
                key=f"metric_discipline_{window_id}_{assigned_goal_id}",
            )
            heart_rate_zone_id = None
            if _metric_uses_heart_rate_zone(selected_metric):
                fixed_zone = selected_metric.get("heart_rate_zone_id")
                fixed_zones = selected_metric.get("heart_rate_zone_ids", [])
                if fixed_zones:
                    st.info(
                        "Zonas del protocolo: "
                        + " y ".join(fixed_zones)
                        + ". Cada zona se conservará como una serie independiente."
                    )
                elif fixed_zone:
                    heart_rate_zone_id = fixed_zone
                    st.info(
                        f"Zona del protocolo: {fixed_zone}. Los límites se "
                        "resolverán con el perfil vigente en la fecha."
                    )
                else:
                    heart_rate_zone_id = st.selectbox(
                        "Zona de frecuencia cardiaca de referencia",
                        ZONE_IDS,
                        index=1,
                        format_func=lambda item: (
                            f"{item} · los límites se resolverán según el perfil "
                            "vigente en la fecha del entrenamiento"
                        ),
                        key=f"metric_hr_zone_{window_id}_{assigned_goal_id}",
                    )
            st.caption(
                "Se intentará calcular esta métrica cada vez que existan "
                "todos sus datos requeridos, ya sean automáticos o manuales."
            )
            if st.button(
                "Asignar métrica a la ventana",
                key=f"assign_metric_{window_id}_{assigned_goal_id}",
                use_container_width=True,
            ):
                save_metric_assignment(
                    window_id,
                    assigned_goal_id,
                    metric_id,
                    heart_rate_zone_id,
                    discipline,
                )
                _rerun()
        else:
            st.info("No existen métricas compatibles en el catálogo.")

        assigned_metrics = assignment.get("metrics", [])
        if assigned_metrics:
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Métrica": (
                                _catalog_name(metric_map[item["metric_id"]])
                                if item["metric_id"] in metric_map
                                else "Métrica ausente del catálogo"
                            ),
                            "Cálculo": "Cuando estén disponibles los datos",
                            "Zona FC": item.get("heart_rate_zone_id", "No aplica"),
                            "Disciplina": item.get(
                                "discipline", "Todas las compatibles"
                            ),
                        }
                        for item in assigned_metrics
                    ]
                ),
                use_container_width=True,
                hide_index=True,
            )
            metric_to_remove = st.selectbox(
                "Asignación de métrica para retirar",
                [item["metric_id"] for item in assigned_metrics],
                format_func=lambda item: (
                    _catalog_name(metric_map[item])
                    if item in metric_map
                    else "Métrica ausente del catálogo"
                ),
            )
            if st.button("Retirar métrica de la ventana"):
                remove_metric_assignment(
                    window_id, assigned_goal_id, metric_to_remove
                )
                _rerun()


def _render_measurements():
    metrics = load_metrics()
    if not metrics:
        st.info("Agrega métricas al catálogo antes de registrar mediciones.")
        return
    force_rebuild = st.checkbox(
        "Forzar reconstrucción histórica completa",
        value=False,
        key="force_automatic_metric_rebuild",
        help="Úsalo sólo si necesitas ignorar la caché y recalcular todos los FIT.",
    )
    if st.button(
        "Evaluar actividades FIT procesadas",
        use_container_width=True,
        key="evaluate_automatic_metrics",
    ):
        with st.spinner("Evaluando el histórico de actividades compatibles..."):
            summary = evaluate_all_activities(force=force_rebuild)
        st.success(
            f"Se detectaron {summary['activities_discovered']} actividades: "
            f"se evaluaron {summary['activities_evaluated']} y se omitieron "
            f"{summary['activities_skipped']} sin cambios. Se "
            f"calcularon o actualizaron {summary['measurements_saved']} "
            "mediciones."
        )

    all_measurements = load_calculated_measurements()
    freshness = metric_freshness(metrics, all_measurements)
    st.markdown("### Estado de las mediciones")
    st.caption(
        "Se recomienda una medición comparable cada 28 días. El aviso comienza "
        "7 días antes y existe una tolerancia de 7 días después de la fecha objetivo."
    )
    st.dataframe(
        pd.DataFrame([
            {
                "Métrica": item["metric_name"],
                "Serie": item["series_label"],
                "Estado": f"{item['status_icon']} {item['status']}",
                "Última": item["latest_date"] or "—",
                "Días": item["days_since"] if item["days_since"] is not None else "—",
                "Objetivo": item["target_date"] or "Evaluación inicial",
                "Observaciones": item["observations"],
            }
            for item in freshness
        ]),
        use_container_width=True,
        hide_index=True,
    )

    metric_map = _by_id(metrics)
    metric_id = st.selectbox(
        "Métrica",
        tuple(metric_map),
        format_func=lambda item: _catalog_name(metric_map[item]),
        key="measurement_metric_selector",
    )
    metric = metric_map[metric_id]
    protocol = METRIC_REGISTRY.get(metric_id, {}).get("protocol")
    if protocol:
        with st.expander("Protocolo de evaluación", expanded=True):
            st.markdown(f"**{protocol['title']}**")
            for step in protocol.get("steps", []):
                st.markdown(f"- {step}")
            st.caption(
                f"Frecuencia objetivo: cada {protocol.get('target_interval_days', 28)} días."
            )

    calculated = [
        item
        for item in all_measurements
        if item.get("metric_id") == metric_id
    ]
    windows = sorted(load_windows(), key=lambda item: item["start_date"])
    window_options = {"all": "Todo el histórico"}
    window_options.update(
        {
            item["id"]: (
                f"{item['name']} · {item['start_date']} a {item['end_date']}"
            )
            for item in windows
        }
    )
    selected_window_id = st.selectbox(
        "Periodo",
        tuple(window_options),
        format_func=lambda item: window_options[item],
        key=f"measurement_window_{metric_id}",
    )
    selected_window = next(
        (
            item
            for item in windows
            if item["id"] == selected_window_id
        ),
        None,
    )
    if selected_window:
        calculated = [
            item
            for item in calculated
            if selected_window["start_date"]
            <= item.get("date", "")
            <= selected_window["end_date"]
        ]
    disciplines = sorted(
        {item.get("discipline") for item in calculated if item.get("discipline")}
    )
    selected_discipline = None
    if disciplines and metric.get("separate_by_discipline", True):
        selected_discipline = st.selectbox(
            "Disciplina analizada",
            disciplines,
            key=f"measurement_discipline_{metric_id}",
        )
        calculated = [
            item
            for item in calculated
            if item.get("discipline") == selected_discipline
        ]
    if metric.get("heart_rate_zone_ids") and metric.get("slope_bands"):
        series = [
            (zone_id, slope_band)
            for zone_id in metric["heart_rate_zone_ids"]
            for slope_band in metric["slope_bands"]
        ]
    else:
        series = sorted(
            {
                (
                    item.get("heart_rate_zone_id") or "Sin zona",
                    item.get("components", {}).get("slope_band") or "Sin banda",
                )
                for item in calculated
            }
        )
    if len(series) > 1:
        selected_series = st.selectbox(
            "Serie analizada",
            series,
            format_func=lambda item: f"{item[0]} · pendiente {item[1]}",
            key=f"measurement_series_{metric_id}",
        )
        calculated = [
            item
            for item in calculated
            if (
                item.get("heart_rate_zone_id") or "Sin zona",
                item.get("components", {}).get("slope_band") or "Sin banda",
            )
            == selected_series
        ]

    if calculated:
        calculated.sort(
            key=lambda item: (
                item.get("date", ""),
                item.get("components", {}).get("start_time", ""),
            )
        )
        values = [
            {
                "Fecha": item["date"],
                "Valor": float(item["value"]),
                "Disciplina": item.get("discipline", ""),
                "Ventana": " · ".join(
                    window["name"]
                    for window in windows
                    if window["start_date"]
                    <= item.get("date", "")
                    <= window["end_date"]
                )
                or "Fuera de ventanas",
                "Zona FC": item.get("heart_rate_zone_id", ""),
                "Inicio": item.get("components", {}).get("start_time", ""),
                "Fin": item.get("components", {}).get("end_time", ""),
                "FC media": item.get("components", {}).get(
                    "mean_heart_rate_bpm"
                ),
                "Tiempo en zona (%)": 100
                * item.get("components", {}).get("time_in_zone_share", 0),
                "Pendiente media (%)": item.get("components", {}).get(
                    "mean_slope_percent"
                ),
                "Banda de pendiente": item.get("components", {}).get(
                    "slope_band", ""
                ),
                "Desnivel neto (m)": item.get("components", {}).get(
                    "net_elevation_gain_meters"
                ),
                "Distancia (m)": item.get("components", {}).get(
                    "distance_meters"
                ),
                "Actividad": item.get("activity_file", ""),
                "Nota": item.get("manual_note", ""),
            }
            for item in calculated
        ]
        chart_values = activity_medians(calculated)
        unique_dates = sorted({item["date"] for item in chart_values})
        if len(unique_dates) >= 2:
            origin = pd.Timestamp(unique_dates[0])
            x_values = [
                (pd.Timestamp(item["date"]) - origin).days
                for item in chart_values
            ]
            y_values = [item["value"] for item in chart_values]
            x_mean = sum(x_values) / len(x_values)
            y_mean = sum(y_values) / len(y_values)
            denominator = sum((value - x_mean) ** 2 for value in x_values)
            slope = (
                sum(
                    (x_value - x_mean) * (y_value - y_mean)
                    for x_value, y_value in zip(x_values, y_values)
                )
                / denominator
                if denominator
                else 0.0
            )
            intercept = y_mean - slope * x_mean
            for item, x_value in zip(chart_values, x_values):
                item["trend"] = intercept + slope * x_value
            st.metric(
                "Tendencia lineal estimada",
                f"{slope * 30:+.2f} {metric.get('unit', '')} cada 30 días",
            )
        else:
            for item in chart_values:
                item["trend"] = item["value"]
            st.caption(
                "Se necesitan mediciones en al menos dos fechas para calcular "
                "la tendencia lineal."
            )
        st.vega_lite_chart(
            {
                "data": {"values": chart_values},
                "height": 380,
                "encoding": {
                    "x": {
                        "field": "date",
                        "type": "temporal",
                        "title": "Fecha",
                    }
                },
                "layer": [
                    {
                        "mark": {
                            "type": "line",
                            "point": {"filled": True, "size": 90},
                            "strokeWidth": 2,
                            "color": "#2563EB",
                        },
                        "encoding": {
                            "y": {
                                "field": "value",
                                "type": "quantitative",
                                "title": f"Valor ({metric.get('unit', '')})",
                                "scale": {"zero": False},
                            },
                            "tooltip": [
                                {
                                    "field": "date",
                                    "type": "temporal",
                                    "title": "Fecha",
                                },
                                {
                                    "field": "value",
                                    "type": "quantitative",
                                    "title": "Valor",
                                    "format": ".2f",
                                },
                                {
                                    "field": "segment_count",
                                    "type": "quantitative",
                                    "title": "Segmentos de la actividad",
                                },
                                {
                                    "field": "activity_file",
                                    "type": "nominal",
                                    "title": "Actividad",
                                },
                            ],
                        },
                    },
                    {
                        "mark": {
                            "type": "line",
                            "strokeDash": [7, 5],
                            "strokeWidth": 2,
                            "color": "#F97316",
                        },
                        "encoding": {
                            "y": {
                                "field": "trend",
                                "type": "quantitative",
                                "scale": {"zero": False},
                            }
                        },
                    },
                ],
            },
            use_container_width=True,
        )
        st.caption(
            "El gráfico y la tendencia utilizan una mediana por actividad. "
            "La tabla conserva todos los segmentos individuales."
        )
        st.dataframe(
            pd.DataFrame(values),
            use_container_width=True,
            hide_index=True,
            height=420,
            column_config={
                "Fecha": st.column_config.TextColumn(width="small"),
                "Valor": st.column_config.NumberColumn(
                    width="small", format="%.2f"
                ),
                "Disciplina": st.column_config.TextColumn(width="small"),
                "Ventana": st.column_config.TextColumn(width="large"),
                "Zona FC": st.column_config.TextColumn(width="small"),
                "Inicio": st.column_config.TextColumn(width="medium"),
                "Fin": st.column_config.TextColumn(width="medium"),
                "FC media": st.column_config.NumberColumn(
                    width="small", format="%.1f"
                ),
                "Tiempo en zona (%)": st.column_config.NumberColumn(
                    width="medium", format="%.1f"
                ),
                "Pendiente media (%)": st.column_config.NumberColumn(
                    width="medium", format="%.2f"
                ),
                "Banda de pendiente": st.column_config.TextColumn(
                    width="medium"
                ),
                "Desnivel neto (m)": st.column_config.NumberColumn(
                    width="medium", format="%.1f"
                ),
                "Distancia (m)": st.column_config.NumberColumn(
                    width="medium", format="%.1f"
                ),
                "Actividad": st.column_config.TextColumn(width="large"),
                "Nota": st.column_config.TextColumn(width="large"),
            },
        )
        measurement_labels = {
            item["id"]: (
                f"{item['date']} · "
                f"{(item.get('components', {}).get('start_time') or '')[11:19]} · "
                f"{float(item['value']):.2f} {item.get('unit', '')}"
            )
            for item in calculated
        }
        selected_measurement_id = st.selectbox(
            "Medición para agregar o editar una nota",
            tuple(measurement_labels),
            format_func=lambda item: measurement_labels[item],
            key=f"automatic_note_measurement_{metric_id}_{selected_window_id}",
        )
        selected_measurement = next(
            item
            for item in calculated
            if item["id"] == selected_measurement_id
        )
        with st.form(
            f"automatic_note_form_{selected_measurement_id}",
            clear_on_submit=False,
        ):
            manual_note = st.text_area(
                "Nota manual",
                value=selected_measurement.get("manual_note", ""),
                placeholder=(
                    "Ej.: terreno húmedo, fatiga acumulada, viento fuerte..."
                ),
            )
            save_note = st.form_submit_button(
                "Guardar nota", use_container_width=True
            )
        if save_note:
            update_measurement_note(selected_measurement_id, manual_note)
            _rerun()
    else:
        st.info(
            "Aún no hay segmentos FIT que cumplan el protocolo para esta "
            "métrica, disciplina y periodo seleccionados."
        )

    st.markdown("#### Agregar una medición manual")
    with st.form(f"measurement_form_{metric_id}", clear_on_submit=True):
        columns = st.columns(3)
        with columns[0]:
            measured_on = st.date_input("Fecha", value=date.today())
        with columns[1]:
            value = st.number_input(
                f"Valor ({metric.get('unit')})", value=0.0, format="%.4f"
            )
        with columns[2]:
            source = st.selectbox(
                "Fuente", SOURCES, format_func=lambda item: SOURCE_LABELS[item]
            )
        notes = st.text_input("Notas")
        submitted = st.form_submit_button(
            "Agregar medición", use_container_width=True
        )
    if submitted:
        add_measurement(
            {
                "metric_id": metric_id,
                "date": measured_on.isoformat(),
                "value": float(value),
                "source": source,
                "notes": notes.strip(),
            }
        )
        _rerun()

    measurements = [
        item
        for item in load_measurements()
        if item.get("metric_id") == metric_id
    ]
    measurements.sort(key=lambda item: item.get("date", ""))
    if not measurements:
        return
    frame = pd.DataFrame(measurements)
    st.markdown("#### Mediciones ingresadas manualmente")
    st.dataframe(
        frame[["date", "value", "source", "notes"]].rename(
            columns={
                "date": "Fecha",
                "value": "Valor",
                "source": "Fuente",
                "notes": "Notas",
            }
        ),
        use_container_width=True,
        hide_index=True,
    )
    measurement_map = {
        item["id"]: f"{item['date']} · {item['value']} {metric.get('unit', '')}"
        for item in measurements
    }
    measurement_id = st.selectbox(
        "Medición para eliminar",
        tuple(measurement_map),
        format_func=lambda item: measurement_map[item],
    )
    if st.button("Eliminar medición seleccionada"):
        delete_measurement(measurement_id)
        _rerun()


def render():
    st.markdown("## Objetivos")
    st.write(
        "Mantén objetivos y métricas reutilizables en catálogos y asígnalos "
        "a ventanas temporales para cada evento."
    )
    event_tab, catalog_tab, zones_tab, measurement_tab = st.tabs(
        ["Evento", "Catálogo", "Zonas FC", "Mediciones"]
    )
    with event_tab:
        _render_events()
    with catalog_tab:
        _render_catalog()
    with zones_tab:
        _render_heart_rate_zones()
    with measurement_tab:
        _render_measurements()
