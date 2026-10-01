"""Global, cross-sport training statistics."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any
import unicodedata

import altair as alt
import pandas as pd
import streamlit as st

from backend.activity_usage_store import permits
from config import PROJECT_ROOT


PERFORMED_LOAD_FILE = (
    PROJECT_ROOT / "data" / "derived" / "training_load" / "performed_load.json"
)
ACTIVITIES_DIR = PROJECT_ROOT / "garmin" / "activities"


def _number(value: Any) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else 0.0
    except (TypeError, ValueError):
        return 0.0


def _normalized_text(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or ""))
    return "".join(char for char in text if unicodedata.category(char) != "Mn").casefold()


def _is_gym_activity(activity: dict[str, Any]) -> bool:
    """Garmin exports strength workouts as generic / 'Ejercicios'."""
    sport = _normalized_text(activity.get("sport"))
    profile = _normalized_text(activity.get("sport_profile_name"))
    return sport == "generic" and any(term in profile for term in ("ejercicio", "gimnasio", "fuerza", "strength"))


def _training_group(sport: Any) -> str:
    """Map detailed FIT disciplines to the broad groups used in the overview."""
    normalized = _normalized_text(sport)
    if any(term in normalized for term in ("bici", "cicl", "rodillo", "mtb")):
        return "Bici"
    if any(term in normalized for term in ("corrida", "carrera", "trail", "trekking", "camin")):
        return "A pie"
    if "kayak" in normalized:
        return "Kayak"
    if any(term in normalized for term in ("gimnas", "ejercicio", "fuerza", "strength")):
        return "Gimnasio"
    return "Otros"


def _load_activity_frame() -> pd.DataFrame:
    """Return a flat table from the derived, auditable FIT load dataset."""
    if not PERFORMED_LOAD_FILE.exists():
        return pd.DataFrame()

    try:
        payload = json.loads(PERFORMED_LOAD_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return pd.DataFrame()

    rows: list[dict[str, Any]] = []
    for item in payload.get("activities", []):
        activity = item.get("activity") or {}
        source = item.get("source") or {}
        load = item.get("load") or {}
        date = pd.to_datetime(item.get("date"), errors="coerce")
        if pd.isna(date):
            continue
        sport = str(activity.get("sport") or "Otro").strip() or "Otro"
        modality = activity.get("cycling_modality")
        if sport == "Bici" and modality in {"MTB", "Ruta"}:
            sport_label = f"Bici · {modality}"
        elif sport == "Bici":
            # Las actividades de ciclismo sin modalidad corresponden al rodillo.
            # Esto es una etiqueta de visualización: no modifica el FIT ni la
            # disciplina canónica que utiliza la planificación.
            sport_label = "Rodillo"
        else:
            sport_label = sport
        duration_hours = _number(activity.get("duration_seconds")) / 3600
        distance_km = _number(activity.get("distance_km"))
        rows.append(
            {
                "fecha": date.normalize(),
                "actividad": item.get("activity_id") or source.get("fit_file") or "Actividad FIT",
                "archivo": source.get("activity_json") or "",
                "deporte": sport_label,
                "distancia_km": distance_km,
                "duracion_h": duration_hours,
                "tiempo_pausado_h": _number(activity.get("paused_duration_seconds")) / 3600,
                "desnivel_m": _number(activity.get("ascent_meters")),
                "descenso_m": _number(activity.get("descent_meters")),
                "carga": _number(load.get("calculated_load")),
                "velocidad_kmh": (distance_km / duration_hours) if duration_hours else 0.0,
                "incluir_volumen": permits(source.get("activity_json"), "volume"),
                "incluir_carga": permits(source.get("activity_json"), "load"),
            }
        )

    # Strength sessions are intentionally outside the aerobic load pipeline.
    # Add them here only to account for their time in the global overview.
    included_files = {str(row["archivo"]) for row in rows}
    for path in ACTIVITIES_DIR.glob("activity_*.json") if ACTIVITIES_DIR.exists() else []:
        if path.name in included_files:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            activity = payload.get("activity") or {}
        except (json.JSONDecodeError, OSError):
            continue
        if not _is_gym_activity(activity):
            continue
        date = pd.to_datetime(activity.get("date"), errors="coerce")
        if pd.isna(date):
            continue
        rows.append(
            {
                "fecha": date.normalize(),
                "actividad": activity.get("source_file") or path.stem.replace("activity_", ""),
                "archivo": path.name,
                "deporte": "Gimnasio",
                "distancia_km": 0.0,
                "duracion_h": _number(activity.get("duration_seconds")) / 3600,
                "tiempo_pausado_h": max(
                    0.0,
                    _number(activity.get("elapsed_duration_seconds")) / 3600
                    - _number(activity.get("duration_seconds")) / 3600,
                ),
                "desnivel_m": 0.0,
                "descenso_m": 0.0,
                "carga": 0.0,
                "velocidad_kmh": 0.0,
                "incluir_volumen": permits(path.name, "volume"),
                "incluir_carga": False,
            }
        )
    frame = pd.DataFrame(rows)
    if not frame.empty:
        frame["grupo"] = frame["deporte"].map(_training_group)
    return frame


def _format_duration(hours: float) -> str:
    total_minutes = max(0, round(hours * 60))
    return f"{total_minutes // 60} h {total_minutes % 60:02d} min"


def _render_kpis(frame: pd.DataFrame) -> None:
    total_hours = frame["duracion_h"].sum()
    total_distance = frame["distancia_km"].sum()
    total_ascent = frame["desnivel_m"].sum()
    total_load = frame.loc[frame["incluir_carga"], "carga"].sum()
    active_days = frame["fecha"].nunique()
    weighted_speed = total_distance / total_hours if total_hours else 0.0
    cards = st.columns(6)
    cards[0].metric("Sesiones", f"{len(frame):,}")
    cards[1].metric("Días activos", f"{active_days:,}")
    cards[2].metric("Distancia", f"{total_distance:,.1f} km")
    cards[3].metric("Tiempo activo", _format_duration(total_hours))
    cards[4].metric("Desnivel positivo", f"{total_ascent:,.0f} m")
    cards[5].metric("Carga calculada", f"{total_load:,.0f}")
    st.caption(f"Velocidad media ponderada por tiempo: {weighted_speed:.1f} km/h.")


def _sport_summary(frame: pd.DataFrame) -> pd.DataFrame:
    summary = (
        frame.groupby("deporte", as_index=False)
        .agg(
            Sesiones=("actividad", "count"),
            Distancia_km=("distancia_km", "sum"),
            Tiempo_h=("duracion_h", "sum"),
            Desnivel_m=("desnivel_m", "sum"),
            Carga=("carga", "sum"),
        )
    )
    summary["Velocidad_media_kmh"] = summary["Distancia_km"] / summary["Tiempo_h"].replace(0, pd.NA)
    return summary.sort_values("Tiempo_h", ascending=False)


def _group_summary(frame: pd.DataFrame) -> pd.DataFrame:
    """Totals by practical training family, independent of detailed modality."""
    summary = (
        frame.groupby("grupo", as_index=False)
        .agg(
            Sesiones=("actividad", "count"),
            Distancia_km=("distancia_km", "sum"),
            Tiempo_h=("duracion_h", "sum"),
            Carga=("carga", "sum"),
        )
    )
    order = {"Bici": 0, "A pie": 1, "Kayak": 2, "Gimnasio": 3, "Otros": 4}
    return summary.sort_values("grupo", key=lambda value: value.map(order).fillna(99))


def _period_totals(frame: pd.DataFrame, frequency: str, periods: int) -> pd.DataFrame:
    """Aggregate the latest complete-looking rolling set of calendar periods."""
    latest = frame["fecha"].max()
    if frequency == "M":
        latest_period = latest.to_period("M")
        start = (latest_period - (periods - 1)).to_timestamp()
        index = pd.date_range(start, periods=periods, freq="MS")
        working = frame.assign(periodo=frame["fecha"].dt.to_period("M").dt.to_timestamp())
    else:
        latest_monday = latest - pd.to_timedelta(latest.weekday(), unit="D")
        start = latest_monday - pd.Timedelta(weeks=periods - 1)
        index = pd.date_range(start, periods=periods, freq="W-MON")
        working = frame.assign(periodo=frame["fecha"] - pd.to_timedelta(frame["fecha"].dt.weekday, unit="D"))
    totals = working.groupby("periodo", as_index=False).agg(
        Distancia_km=("distancia_km", "sum"), Tiempo_h=("duracion_h", "sum"), Carga=("carga", "sum")
    )
    return pd.DataFrame({"periodo": index}).merge(totals, on="periodo", how="left").fillna(0)


def _render_total_bar_chart(totals: pd.DataFrame, field: str, title: str, axis_title: str, date_format: str) -> None:
    is_monthly = date_format == "%b %y"
    x_encoding = (
        alt.X(
            "periodo:T", timeUnit="yearmonth", title=None,
            axis=alt.Axis(format=date_format, labelAngle=-35, labelOverlap=False),
        )
        if is_monthly
        else alt.X(
            "periodo:T", title=None,
            axis=alt.Axis(format=date_format, labelAngle=-35, labelOverlap=False),
        )
    )
    chart = alt.Chart(totals).mark_bar(
        size=42, cornerRadiusTopLeft=4, cornerRadiusTopRight=4
    ).encode(
        x=x_encoding,
        y=alt.Y(f"{field}:Q", title=axis_title),
        color=alt.value("#1267E5"),
        tooltip=[
            alt.Tooltip("periodo:T", title="Período", format="%d/%m/%Y"),
            alt.Tooltip(f"{field}:Q", title=axis_title, format=".1f"),
        ],
    ).properties(title=title, height=250)
    st.altair_chart(chart, use_container_width=True)


def render() -> None:
    st.title("Estadísticas globales")
    st.caption(
        "Resumen transversal de las actividades FIT procesadas: volumen, tiempo, desnivel, carga y distribución por deporte."
    )
    all_activities = _load_activity_frame()
    if all_activities.empty:
        st.info("Aún no hay actividades procesadas. Carga y procesa al menos un archivo FIT en Ingestión de datos.")
        return

    minimum, maximum = all_activities["fecha"].min().date(), all_activities["fecha"].max().date()
    filters = st.columns((2, 2, 1))
    with filters[0]:
        dates = st.date_input("Período", value=(minimum, maximum), min_value=minimum, max_value=maximum)
    with filters[1]:
        sports = st.multiselect("Deportes", sorted(all_activities["deporte"].unique()), default=sorted(all_activities["deporte"].unique()))
    with filters[2]:
        include_excluded = st.checkbox("Incluir excluidas", help="Incluye actividades que se excluyeron manualmente del análisis de volumen o carga.")

    start, end = (dates if isinstance(dates, (tuple, list)) and len(dates) == 2 else (minimum, maximum))
    frame = all_activities[
        all_activities["fecha"].between(pd.Timestamp(start), pd.Timestamp(end))
        & all_activities["deporte"].isin(sports)
    ].copy()
    if not include_excluded:
        frame = frame[frame["incluir_volumen"]].copy()
    if frame.empty:
        st.info("No hay actividades que coincidan con los filtros elegidos.")
        return

    _render_kpis(frame)
    st.divider()
    st.subheader("Comparación por deporte")
    summary = _sport_summary(frame)
    chart_height = max(250, len(summary) * 54)
    selection = alt.selection_point(fields=["deporte"], bind="legend")
    distance_chart = alt.Chart(summary).mark_bar(cornerRadiusEnd=5).encode(
        x=alt.X("Distancia_km:Q", title="Distancia (km)"),
        y=alt.Y("deporte:N", sort="-x", title=None),
        color=alt.Color("deporte:N", title="Deporte"),
        opacity=alt.condition(selection, alt.value(1), alt.value(0.18)),
        tooltip=["deporte", alt.Tooltip("Distancia_km:Q", format=".1f"), "Sesiones"],
    ).add_params(selection).properties(title="Distancia por deporte", height=chart_height)
    st.altair_chart(distance_chart, use_container_width=True)

    selection = alt.selection_point(fields=["deporte"], bind="legend")
    time_chart = alt.Chart(summary).mark_bar(cornerRadiusEnd=5).encode(
        x=alt.X("Tiempo_h:Q", title="Tiempo activo (h)"),
        y=alt.Y("deporte:N", sort="-x", title=None),
        color=alt.Color("deporte:N", title="Deporte"),
        opacity=alt.condition(selection, alt.value(1), alt.value(0.18)),
        tooltip=["deporte", alt.Tooltip("Tiempo_h:Q", format=".1f"), "Sesiones"],
    ).add_params(selection).properties(title="Tiempo activo por deporte", height=chart_height)
    st.altair_chart(time_chart, use_container_width=True)

    selection = alt.selection_point(fields=["deporte"], bind="legend")
    load_chart = alt.Chart(summary).mark_bar(cornerRadiusEnd=5).encode(
        x=alt.X("Carga:Q", title="Carga calculada"),
        y=alt.Y("deporte:N", sort="-x", title=None),
        color=alt.Color("deporte:N", title="Deporte"),
        opacity=alt.condition(selection, alt.value(1), alt.value(0.18)),
        tooltip=["deporte", alt.Tooltip("Carga:Q", format=".1f"), "Sesiones"],
    ).add_params(selection).properties(title="Carga por deporte", height=chart_height)
    st.altair_chart(load_chart, use_container_width=True)
    st.dataframe(
        summary.rename(columns={
            "deporte": "Deporte", "Velocidad_media_kmh": "Velocidad media (km/h)",
            "Distancia_km": "Distancia (km)", "Tiempo_h": "Tiempo (h)",
            "Desnivel_m": "Desnivel+ (m)",
        }),
        hide_index=True,
        use_container_width=True,
        column_config={
            "Distancia (km)": st.column_config.NumberColumn(format="%.1f"),
            "Tiempo (h)": st.column_config.NumberColumn(format="%.1f"),
            "Desnivel+ (m)": st.column_config.NumberColumn(format="%.0f"),
            "Carga": st.column_config.NumberColumn(format="%.0f"),
            "Velocidad media (km/h)": st.column_config.NumberColumn(format="%.1f"),
        },
    )

    st.subheader("Comparación por grupos de entrenamiento")
    st.caption("Agrupa Bici de ruta, MTB y rodillo; Corrida, Trail y actividades a pie; además de Kayak y Gimnasio.")
    group_summary = _group_summary(frame)
    group_fields = {
        "Tiempo activo": ("Tiempo_h", "Tiempo activo (h)", ".1f"),
        "Distancia": ("Distancia_km", "Distancia (km)", ".1f"),
        "Carga calculada": ("Carga", "Carga calculada", ".0f"),
    }
    group_colors = alt.Scale(
        domain=["Bici", "A pie", "Kayak", "Gimnasio", "Otros"],
        range=["#1267E5", "#FF5A36", "#11A683", "#8B5CF6", "#94A3B8"],
    )
    for group_measure, (group_field, group_title, group_format) in group_fields.items():
        group_selection = alt.selection_point(fields=["grupo"], bind="legend")
        group_chart = alt.Chart(group_summary).mark_bar(cornerRadiusEnd=6).encode(
            x=alt.X(f"{group_field}:Q", title=group_title),
            y=alt.Y("grupo:N", title=None, sort=["Bici", "A pie", "Kayak", "Gimnasio", "Otros"]),
            color=alt.Color("grupo:N", title="Grupo", scale=group_colors),
            opacity=alt.condition(group_selection, alt.value(1), alt.value(0.18)),
            tooltip=[
                alt.Tooltip("grupo:N", title="Grupo"),
                alt.Tooltip(f"{group_field}:Q", title=group_title, format=group_format),
                alt.Tooltip("Sesiones:Q", title="Sesiones"),
                alt.Tooltip("Tiempo_h:Q", title="Tiempo (h)", format=".1f"),
            ],
        ).add_params(group_selection).properties(
            title=f"{group_measure} por grupo de entrenamiento", height=max(220, len(group_summary) * 54)
        )
        st.altair_chart(group_chart, use_container_width=True)

    st.divider()
    st.subheader("Evolución semanal")
    weekly = frame.copy()
    weekly["semana"] = weekly["fecha"] - pd.to_timedelta(weekly["fecha"].dt.weekday, unit="D")
    weekly_summary = weekly.groupby(["semana", "deporte"], as_index=False).agg(
        Distancia_km=("distancia_km", "sum"), Tiempo_h=("duracion_h", "sum"), Carga=("carga", "sum")
    )
    selection = alt.selection_point(fields=["deporte"], bind="legend")
    chart = alt.Chart(weekly_summary).mark_bar().encode(
        x=alt.X("semana:T", title="Semana", axis=alt.Axis(format="%d %b")),
        y=alt.Y("Distancia_km:Q", title="Distancia (km)"),
        color=alt.Color("deporte:N", title="Deporte"),
        tooltip=[alt.Tooltip("semana:T", format="%d/%m/%Y"), "deporte", alt.Tooltip("Distancia_km:Q", format=".1f")],
    ).add_params(selection).transform_filter(selection).properties(title="Distancia semanal por deporte", height=330)
    st.altair_chart(chart, use_container_width=True)

    selection = alt.selection_point(fields=["deporte"], bind="legend")
    chart = alt.Chart(weekly_summary).mark_line(point=True, strokeWidth=2.5).encode(
        x=alt.X("semana:T", title="Semana", axis=alt.Axis(format="%d %b")),
        y=alt.Y("Carga:Q", title="Carga calculada"),
        color=alt.Color("deporte:N", title="Deporte"),
        tooltip=[alt.Tooltip("semana:T", format="%d/%m/%Y"), "deporte", alt.Tooltip("Carga:Q", format=".1f")],
    ).add_params(selection).transform_filter(selection).properties(title="Carga semanal por deporte", height=330)
    st.altair_chart(chart, use_container_width=True)

    st.divider()
    st.subheader("Totales recientes")
    st.caption("Barras verticales con el total de todos los deportes seleccionados en cada período.")
    monthly = _period_totals(frame, "M", 12)
    st.markdown("##### Últimos 12 meses")
    _render_total_bar_chart(monthly, "Distancia_km", "Distancia mensual", "Kilómetros", "%b %y")
    _render_total_bar_chart(monthly, "Tiempo_h", "Tiempo activo mensual", "Horas", "%b %y")
    _render_total_bar_chart(monthly, "Carga", "Carga mensual", "Carga", "%b %y")

    weekly_totals = _period_totals(frame, "W", 12)
    st.markdown("##### Últimas 12 semanas")
    _render_total_bar_chart(weekly_totals, "Distancia_km", "Distancia semanal", "Kilómetros", "%d %b")
    _render_total_bar_chart(weekly_totals, "Tiempo_h", "Tiempo activo semanal", "Horas", "%d %b")
    _render_total_bar_chart(weekly_totals, "Carga", "Carga semanal", "Carga", "%d %b")

    st.divider()
    st.subheader("Sesiones destacadas")
    highlighted_sport = st.selectbox(
        "Deporte para sesiones destacadas",
        sorted(frame["deporte"].unique()),
        key="global_statistics_highlighted_sport",
    )
    highlighted = frame[frame["deporte"] == highlighted_sport]
    top_left, top_right = st.columns(2)
    display_columns = ["fecha", "deporte", "actividad", "distancia_km", "duracion_h", "desnivel_m", "carga"]
    column_config = {
        "fecha": st.column_config.DateColumn("Fecha", format="DD/MM/YYYY"),
        "deporte": "Deporte", "actividad": "Actividad",
        "distancia_km": st.column_config.NumberColumn("Distancia (km)", format="%.1f"),
        "duracion_h": st.column_config.NumberColumn("Tiempo (h)", format="%.2f"),
        "desnivel_m": st.column_config.NumberColumn("Desnivel+ (m)", format="%.0f"),
        "carga": st.column_config.NumberColumn("Carga", format="%.0f"),
    }
    with top_left:
        st.caption("Mayor distancia")
        st.dataframe(highlighted.nlargest(8, "distancia_km")[display_columns], hide_index=True, use_container_width=True, column_config=column_config)
    with top_right:
        st.caption("Mayor desnivel positivo")
        st.dataframe(highlighted.nlargest(8, "desnivel_m")[display_columns], hide_index=True, use_container_width=True, column_config=column_config)
