"""Visual exploration of candidate segments inside normalized FIT activities."""

from __future__ import annotations

from datetime import datetime
import json
import math
from pathlib import Path

import altair as alt
import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from config import PROJECT_ROOT
from backend.segment_store import (
    delete_segment,
    find_duplicate,
    load_segment_attempts,
    load_segments,
    save_segment,
    save_segment_attempts,
)
from backend.segment_matching import evaluate_historical_segments
from backend.route_matching import (
    load_route_families,
    rebuild_route_families,
    save_route_family,
)
from backend.activity_usage_store import permits
from backend.geographic_progress import build_geographic_progress
from backend.ui_translations import display_es


ACTIVITIES_DIR = PROJECT_ROOT / "garmin" / "activities"
SEGMENT_TYPES = ("Subida", "Llano", "Descenso", "Ondulado", "Recorrido completo")
CYCLING_MODALITIES = ("Ruta", "MTB", "Otra")
HEART_RATE_ZONE_OPTIONS = ("Z1", "Z2", "Z3", "Z4", "Z5", "Z6", "Mixto", "Sin clasificar")


def _attempt_zone(attempt: dict, nested_metrics: bool = False) -> dict:
    source = attempt.get("metrics", {}) if nested_metrics else attempt
    return source.get("heart_rate_zone", {}) or {}


def _zone_label(attempt: dict, nested_metrics: bool = False) -> str:
    return _attempt_zone(attempt, nested_metrics).get("observed_zone") or "Sin clasificar"


def _included_route_attempts(route: dict) -> list[dict]:
    return [
        item for item in route.get("attempts", [])
        if permits(item.get("activity_file"), "segments_routes")
    ]


def _render_speed_progress(entity_type: str, entity_id: str, zone: str) -> None:
    if zone == "Todas":
        st.caption("Selecciona una zona observada para calcular cambios comparables de velocidad.")
        return
    progress = build_geographic_progress()
    series = next((
        item for item in progress["series"]
        if item["entity_type"] == entity_type
        and item["entity_id"] == entity_id
        and item["zone"] == zone
    ), None)
    if not series:
        return
    st.markdown("#### Evolución comparable de velocidad")
    columns = st.columns(4)
    columns[0].metric("Velocidad actual", f"{series['current_speed_kmh']:.2f} km/h")
    columns[1].metric("Último intento", f"{series['latest_speed_kmh']:.2f} km/h")
    columns[2].metric("Intentos en la serie", series["attempts"])
    columns[3].metric(
        "Estado del atleta",
        "Incorporada" if series["eligible_for_athlete_state"] and zone not in {"Mixto", "Sin clasificar"}
        else f"Faltan {max(0, progress['minimum_attempts'] - series['attempts'])}",
    )
    st.caption("La velocidad actual es la mediana de los últimos tres intentos disponibles.")
    st.dataframe(pd.DataFrame([
        {
            "Referencia": comparison["label"],
            "Velocidad de referencia (km/h)": comparison.get("reference"),
            "Cambio (%)": comparison.get("change_percent"),
            "Interpretación": comparison.get("status"),
        }
        for comparison in series["comparisons"].values()
    ]), hide_index=True, use_container_width=True)


@st.cache_data(show_spinner=False)
def _load_activity(path_text: str, modified_ns: int) -> dict:
    """Cache a normalized activity while invalidating it when it changes."""
    del modified_ns
    return json.loads(Path(path_text).read_text(encoding="utf-8"))


def _sport_label(activity: dict) -> str:
    profile = activity.get("sport_profile_name")
    if profile:
        return str(profile)
    return {
        "cycling": "Bici",
        "running": "Corrida",
        "kayaking": "Kayak",
    }.get(activity.get("sport"), activity.get("sport") or "Sin disciplina")


def _infer_segment_type(metrics: dict) -> str:
    slope = float(metrics.get("slope_percent") or 0)
    ascent = float(metrics.get("ascent_m") or 0)
    descent = float(metrics.get("descent_m") or 0)
    distance_m = max(1.0, float(metrics.get("distance_km") or 0) * 1000)
    variation = 100 * (ascent + descent) / distance_m
    if slope >= 2:
        return "Subida"
    if slope <= -2:
        return "Descenso"
    if abs(slope) <= 1 and variation <= 3:
        return "Llano"
    return "Ondulado"


def _discipline_category(segment: dict) -> str:
    discipline = segment.get("discipline", "Otra")
    if discipline == "Bici":
        modality = segment.get("cycling_modality") or _legacy_cycling_modality(segment)
        return "Bici de ruta" if modality == "Ruta" else "Mountain Bike" if modality == "MTB" else "Bici"
    return discipline


def _legacy_cycling_modality(segment: dict):
    """Recover modality for segments saved before the catalog classification."""
    source = segment.get("source", {})
    searchable = " ".join(
        str(source.get(key, "")) for key in ("activity_file", "fit_file")
    ).casefold()
    if any(token in searchable for token in ("mtb", "mountain", "btt")):
        return "MTB"
    if any(token in searchable for token in ("ruta", "road")):
        return "Ruta"
    return None


def _duration_label(seconds) -> str:
    if not isinstance(seconds, (int, float)) or seconds < 0:
        return "—"
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:d}:{seconds:02d}"


def _records_frame(payload: dict) -> pd.DataFrame:
    columns = [
        "timestamp", "latitude", "longitude", "altitude_meters",
        "distance_meters", "speed_mps", "heart_rate_bpm",
    ]
    frame = pd.DataFrame(payload.get("records", []))
    if frame.empty or not set(columns[:6]).issubset(frame.columns):
        return pd.DataFrame(columns=[*columns, "distance_km"])
    for column in columns[1:]:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True)
    frame = frame.dropna(
        subset=["timestamp", "latitude", "longitude", "altitude_meters", "distance_meters"]
    ).sort_values(["distance_meters", "timestamp"])
    frame = frame[frame["distance_meters"] >= 0].copy()
    frame["distance_km"] = frame["distance_meters"] / 1000.0
    return frame.reset_index(drop=True)


def _nearest_interval(frame: pd.DataFrame, start_km: float, end_km: float) -> pd.DataFrame:
    distances = frame["distance_km"]
    start_index = (distances - start_km).abs().idxmin()
    end_index = (distances - end_km).abs().idxmin()
    if start_index > end_index:
        start_index, end_index = end_index, start_index
    return frame.loc[start_index:end_index].copy()


def _segment_metrics(segment: pd.DataFrame) -> dict:
    first, last = segment.iloc[0], segment.iloc[-1]
    distance_km = max(0.0, (last["distance_meters"] - first["distance_meters"]) / 1000.0)
    elapsed_seconds = max(0.0, (last["timestamp"] - first["timestamp"]).total_seconds())
    elevation = segment["altitude_meters"].interpolate(limit_direction="both")
    deltas = elevation.diff().fillna(0)
    ascent = float(deltas.clip(lower=0).sum())
    descent = float((-deltas.clip(upper=0)).sum())
    net = float(elevation.iloc[-1] - elevation.iloc[0])
    speed_kmh = distance_km / (elapsed_seconds / 3600) if elapsed_seconds > 0 else None
    slope = 100 * net / (distance_km * 1000) if distance_km > 0 else None
    vam = max(0.0, net) / (elapsed_seconds / 3600) if elapsed_seconds > 0 else None
    heart_rate = segment["heart_rate_bpm"].dropna() if "heart_rate_bpm" in segment else pd.Series(dtype=float)
    return {
        "distance_km": distance_km,
        "elapsed_seconds": elapsed_seconds,
        "ascent_m": ascent,
        "descent_m": descent,
        "net_m": net,
        "speed_kmh": speed_kmh,
        "slope_percent": slope,
        "vam_mh": vam,
        "heart_rate_mean": float(heart_rate.mean()) if not heart_rate.empty else None,
        "heart_rate_max": float(heart_rate.max()) if not heart_rate.empty else None,
    }


def _downsample(frame: pd.DataFrame, maximum: int = 2500) -> pd.DataFrame:
    if len(frame) <= maximum:
        return frame
    step = max(1, len(frame) // maximum)
    sampled = frame.iloc[::step]
    if sampled.index[-1] != frame.index[-1]:
        sampled = pd.concat([sampled, frame.iloc[[-1]]])
    return sampled


def _geometry(segment: pd.DataFrame, maximum: int = 800) -> list[dict]:
    """Store a compact, ordered reference geometry with relative distance."""
    sampled = _downsample(segment, maximum).copy()
    origin = float(sampled.iloc[0]["distance_meters"])
    return [
        {
            "distance_meters": round(float(row.distance_meters - origin), 2),
            "latitude": round(float(row.latitude), 7),
            "longitude": round(float(row.longitude), 7),
            "altitude_meters": round(float(row.altitude_meters), 1),
        }
        for row in sampled.itertuples()
    ]


def _stored_frame(segment: dict) -> pd.DataFrame:
    frame = pd.DataFrame(segment.get("geometry", []))
    if frame.empty:
        return frame
    frame["distance_km"] = frame["distance_meters"] / 1000.0
    return frame


def _render_map(
    frame: pd.DataFrame,
    segment: pd.DataFrame,
    map_key: str,
    layer_name: str,
):
    route = _downsample(frame, 3000)
    selected = _downsample(segment, 1500)
    center = selected.iloc[len(selected) // 2]
    map_view = _base_map(
        [float(center["latitude"]), float(center["longitude"])], layer_name
    )
    folium.PolyLine(
        route[["latitude", "longitude"]].values.tolist(),
        color="#8797AD", weight=4, opacity=0.65, tooltip="Ruta completa",
    ).add_to(map_view)
    folium.PolyLine(
        selected[["latitude", "longitude"]].values.tolist(),
        color="#1267E5", weight=7, opacity=0.95, tooltip="Tramo seleccionado",
    ).add_to(map_view)
    endpoints = (
        (selected.iloc[0], "Inicio", "#12A6B8"),
        (selected.iloc[-1], "Final", "#FF6B4A"),
    )
    for point, label, color in endpoints:
        folium.Marker(
            [float(point["latitude"]), float(point["longitude"])],
            tooltip=label,
            icon=folium.Icon(color="blue" if label == "Inicio" else "red", icon="flag"),
        ).add_to(map_view)
        folium.CircleMarker(
            [float(point["latitude"]), float(point["longitude"])],
            radius=7, color=color, fill=True, fill_color=color, fill_opacity=0.9,
            interactive=False,
        ).add_to(map_view)
    return st_folium(
        map_view,
        key=map_key,
        height=500,
        use_container_width=True,
        returned_objects=["last_clicked"],
    )


def _nearest_route_km(frame: pd.DataFrame, latitude: float, longitude: float) -> float:
    """Snap a map coordinate to the closest recorded point of the route."""
    latitude_scale = math.cos(math.radians(float(latitude)))
    squared_distance = (
        (frame["latitude"] - latitude) ** 2
        + ((frame["longitude"] - longitude) * latitude_scale) ** 2
    )
    return float(frame.loc[squared_distance.idxmin(), "distance_km"])


def _render_profile(
    frame: pd.DataFrame,
    start_km: float,
    end_km: float,
    chart_key: str,
):
    profile = _downsample(frame, 3500)[["distance_km", "altitude_meters"]].copy()
    profile["Tramo"] = profile["distance_km"].between(start_km, end_km)
    base = alt.Chart(profile).encode(
        x=alt.X("distance_km:Q", title="Distancia (km)"),
        y=alt.Y("altitude_meters:Q", title="Altitud (m)", scale=alt.Scale(zero=False)),
    )
    area = base.mark_area(opacity=0.16, color="#7890A9")
    line = base.mark_line(color="#7890A9", strokeWidth=2)
    selected = base.transform_filter(alt.datum.Tramo).mark_line(
        color="#1267E5", strokeWidth=4
    )
    brush = alt.selection_interval(
        name="segment_interval",
        encodings=["x"],
        empty=False,
    )
    chart = (area + line + selected).add_params(brush).properties(height=300)
    return st.altair_chart(
        chart,
        use_container_width=True,
        key=chart_key,
        on_select="rerun",
        selection_mode="segment_interval",
    )


def _selected_distance_range(event):
    """Extract an x interval from Streamlit's Altair selection payload."""
    selection = getattr(event, "selection", None)
    if selection is None and isinstance(event, dict):
        selection = event.get("selection")
    if not selection:
        return None
    payload = selection.get("segment_interval", {})
    if not isinstance(payload, dict):
        return None
    for key in ("distance_km", "distance_km_1", "x"):
        values = payload.get(key)
        if (
            isinstance(values, (list, tuple))
            and len(values) == 2
            and all(isinstance(value, (int, float)) for value in values)
        ):
            return tuple(sorted(float(value) for value in values))
    return None


def _rerun():
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()


def _render_stored_map(frame: pd.DataFrame, map_key: str) -> None:
    center = frame.iloc[len(frame) // 2]
    layer_name = _map_layer_selector(f"{map_key}_layer")
    map_view = _base_map(
        [float(center["latitude"]), float(center["longitude"])], layer_name
    )
    coordinates = frame[["latitude", "longitude"]].values.tolist()
    folium.PolyLine(coordinates, color="#1267E5", weight=6, opacity=.92).add_to(map_view)
    for point, color, label in (
        (frame.iloc[0], "#12A6B8", "Inicio"),
        (frame.iloc[-1], "#FF6B4A", "Final"),
    ):
        folium.CircleMarker(
            [float(point["latitude"]), float(point["longitude"])], radius=7,
            color=color, fill=True, fill_color=color, fill_opacity=.95, tooltip=label,
        ).add_to(map_view)
    st_folium(map_view, key=map_key, height=430, use_container_width=True, returned_objects=[])


def _render_stored_profile(frame: pd.DataFrame) -> None:
    chart = alt.Chart(frame).mark_area(
        line={"color": "#1267E5", "strokeWidth": 3},
        color="#1267E5", opacity=0.18,
    ).encode(
        x=alt.X("distance_km:Q", title="Distancia dentro del segmento (km)"),
        y=alt.Y("altitude_meters:Q", title="Altitud (m)", scale=alt.Scale(zero=False)),
    ).properties(height=280).interactive(bind_y=False)
    st.altair_chart(chart, use_container_width=True)


def _render_catalog() -> None:
    segments = load_segments()
    if not segments:
        st.info("Todavía no hay segmentos guardados.")
        return
    for item in segments:
        item.setdefault("segment_type", _infer_segment_type(item.get("metrics", {})))
        item.setdefault("cycling_modality", _legacy_cycling_modality(item))
    st.subheader("Catálogo de segmentos")
    if st.button(
        "Buscar intentos en los FIT históricos",
        use_container_width=True,
        key="evaluate_historical_segments",
    ):
        with st.spinner("Comparando los segmentos activos con las actividades FIT..."):
            summary = evaluate_historical_segments()
        st.session_state["segment_detection_summary"] = summary
        _rerun()
    detection_summary = st.session_state.pop("segment_detection_summary", None)
    if detection_summary:
        st.success(
            f"Se evaluaron {detection_summary['activities_evaluated']} actividades "
            f"contra {detection_summary['segments_evaluated']} segmentos activos y "
            f"se detectaron {detection_summary['attempts_detected']} intentos."
        )
        if detection_summary.get("failures"):
            with st.expander("Actividades que no pudieron evaluarse"):
                st.dataframe(detection_summary["failures"], hide_index=True, use_container_width=True)
    attempts = load_segment_attempts()
    attempts_by_segment = {}
    for attempt in attempts:
        if not permits(attempt.get("activity_file"), "segments_routes"):
            continue
        attempts_by_segment.setdefault(attempt.get("segment_id"), []).append(attempt)
    categories = sorted({_discipline_category(item) for item in segments})
    filter_columns = st.columns(3)
    with filter_columns[0]:
        discipline_filter = st.multiselect(
            "Disciplina",
            categories,
            default=categories,
            key="segment_catalog_discipline_filter",
        )
    with filter_columns[1]:
        type_options = sorted({item.get("segment_type", "Ondulado") for item in segments})
        type_filter = st.multiselect(
            "Tipo de segmento",
            type_options,
            default=type_options,
            key="segment_catalog_type_filter",
        )
    with filter_columns[2]:
        status_filter = st.selectbox(
            "Estado", ("Todos", "Activos", "Inactivos"), key="segment_catalog_status_filter"
        )
    filtered = [
        item for item in segments
        if _discipline_category(item) in discipline_filter
        and item.get("segment_type", "Ondulado") in type_filter
        and (
            status_filter == "Todos"
            or (status_filter == "Activos" and item.get("active", True))
            or (status_filter == "Inactivos" and not item.get("active", True))
        )
    ]
    summary_columns = st.columns(max(1, min(5, len(categories) + 1)))
    summary_columns[0].metric("Segmentos", len(segments))
    for column, category in zip(summary_columns[1:], categories):
        column.metric(category, sum(_discipline_category(item) == category for item in segments))
    if not filtered:
        st.info("No hay segmentos que coincidan con los filtros seleccionados.")
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Nombre": item.get("name", "Sin nombre"),
                    "Disciplina": _discipline_category(item),
                    "Tipo": item.get("segment_type", "Ondulado"),
                    "Distancia (km)": round(float(item.get("metrics", {}).get("distance_km", 0)), 2),
                    "Desnivel + (m)": round(float(item.get("metrics", {}).get("ascent_m", 0))),
                    "Pendiente media (%)": round(float(item.get("metrics", {}).get("slope_percent", 0)), 1),
                    "Origen": item.get("source", {}).get("activity_date", "—"),
                    "Intentos": len(attempts_by_segment.get(item.get("id"), [])),
                    "Estado": "Activo" if item.get("active", True) else "Inactivo",
                }
                for item in filtered
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )
    segment_map = {item["id"]: item for item in filtered}
    selected_id = st.selectbox(
        "Consultar segmento",
        tuple(segment_map),
        format_func=lambda item_id: segment_map[item_id].get("name", "Sin nombre"),
        key="stored_segment_selector",
    )
    selected = segment_map[selected_id]
    status = "Activo" if selected.get("active", True) else "Inactivo"
    st.caption(
        f"{_discipline_category(selected)} · {selected.get('segment_type', '—')} · "
        f"{selected.get('metrics', {}).get('distance_km', 0):.2f} km · {status} · "
        f"origen: {selected.get('source', {}).get('activity_date', '—')}"
    )
    frame = _stored_frame(selected)
    if frame.empty:
        st.warning("Este segmento no contiene una geometría utilizable.")
    else:
        map_tab, profile_tab = st.tabs(["Mapa del segmento", "Perfil altimétrico"])
        with map_tab:
            _render_stored_map(frame, f"stored_segment_map_{selected_id}")
        with profile_tab:
            _render_stored_profile(frame)
    metrics = selected.get("metrics", {})
    columns = st.columns(5)
    columns[0].metric("Distancia", f"{metrics.get('distance_km', 0):.2f} km")
    columns[1].metric("Desnivel +", f"{metrics.get('ascent_m', 0):.0f} m")
    columns[2].metric("Desnivel neto", f"{metrics.get('net_m', 0):+.0f} m")
    columns[3].metric("Pendiente media", f"{metrics.get('slope_percent', 0):+.1f}%")
    columns[4].metric("Puntos guardados", len(selected.get("geometry", [])))
    all_selected_attempts = sorted(
        attempts_by_segment.get(selected_id, []),
        key=lambda item: (item.get("activity_date", ""), item.get("metrics", {}).get("start_time", "")),
    )
    available_zones = sorted({_zone_label(item, True) for item in all_selected_attempts})
    selected_zone = st.selectbox(
        "Comparar intentos en zona observada",
        tuple(available_zones + ["Todas"]),
        key=f"segment_zone_filter_{selected_id}",
        help="Cada serie se analiza por separado. 'Todas' sirve solamente para inspección.",
    ) if available_zones else "Todas"
    selected_attempts = [
        item for item in all_selected_attempts
        if selected_zone == "Todas" or _zone_label(item, True) == selected_zone
    ]
    st.subheader(f"Intentos detectados · {len(selected_attempts)}")
    if not selected_attempts:
        st.info(
            "No hay intentos calculados para este segmento. Ejecuta la búsqueda "
            "histórica o revisa que el segmento esté activo."
        )
    else:
        attempt_rows = pd.DataFrame(
            [
                {
                    "Fecha": item.get("activity_date"),
                    "Tiempo (s)": item.get("metrics", {}).get("duration_seconds"),
                    "Velocidad (km/h)": item.get("metrics", {}).get("speed_kmh"),
                    "VAM (m/h)": item.get("metrics", {}).get("vam_mh"),
                    "FC media": item.get("metrics", {}).get("heart_rate_mean"),
                    "FC máxima": item.get("metrics", {}).get("heart_rate_max"),
                    "Zona objetivo": _attempt_zone(item, True).get("target_zone") or "—",
                    "Zona observada": _zone_label(item, True),
                    "Tiempo zona dominante (%)": round(
                        100 * _attempt_zone(item, True).get("dominant_share", 0), 1
                    ) if _attempt_zone(item, True).get("dominant_share") is not None else None,
                    "Confianza": _attempt_zone(item, True).get("zone_confidence", "Sin datos"),
                    "Origen zona": "Manual" if _attempt_zone(item, True).get("zone_source") == "manual" else "Automático",
                    "Desnivel neto (m)": item.get("metrics", {}).get("net_m"),
                    "Error mediano GPS (m)": item.get("match_quality", {}).get("median_error_m"),
                    "Actividad": item.get("activity_file"),
                }
                for item in selected_attempts
            ]
        )
        st.dataframe(attempt_rows, hide_index=True, use_container_width=True)
        chart_data = attempt_rows.copy()
        chart_data["Fecha"] = pd.to_datetime(chart_data["Fecha"], errors="coerce")
        chart_data = chart_data.dropna(subset=["Fecha"])
        if len(chart_data) >= 2:
            value_options = [
                column for column in ("Velocidad (km/h)", "VAM (m/h)", "Tiempo (s)")
                if chart_data[column].notna().any()
            ]
            if value_options:
                chart_value = st.selectbox(
                    "Variable de evolución",
                    value_options,
                    key=f"segment_attempt_chart_{selected_id}",
                )
                evolution = alt.Chart(chart_data).mark_line(point=True, color="#1267E5").encode(
                    x=alt.X("Fecha:T", title="Fecha"),
                    y=alt.Y(f"{chart_value}:Q", title=chart_value, scale=alt.Scale(zero=False)),
                    tooltip=["Fecha:T", alt.Tooltip(f"{chart_value}:Q", format=".2f")],
                ).properties(height=280)
                st.altair_chart(evolution, use_container_width=True)
    _render_speed_progress("segment", selected_id, selected_zone)
    if all_selected_attempts:
        with st.expander("Revisar o corregir zona de un intento"):
            attempt_map = {item["id"]: item for item in all_selected_attempts}
            edit_attempt_id = st.selectbox(
                "Intento",
                tuple(attempt_map),
                format_func=lambda attempt_id: (
                    f"{attempt_map[attempt_id].get('activity_date')} · "
                    f"{_zone_label(attempt_map[attempt_id], True)}"
                ),
                key=f"segment_zone_attempt_{selected_id}",
            )
            edit_attempt = attempt_map[edit_attempt_id]
            zone_data = _attempt_zone(edit_attempt, True)
            st.caption(
                f"Propuesta automática: {zone_data.get('automatic_zone') or 'Sin clasificar'} · "
                f"confianza: {zone_data.get('zone_confidence', 'Sin datos')}"
            )
            with st.form(f"segment_zone_edit_{edit_attempt_id}"):
                observed = st.selectbox(
                    "Zona observada corregida",
                    HEART_RATE_ZONE_OPTIONS,
                    index=HEART_RATE_ZONE_OPTIONS.index(_zone_label(edit_attempt, True)),
                )
                target_options = ("No definida",) + HEART_RATE_ZONE_OPTIONS[:-1]
                current_target = zone_data.get("target_zone") or "No definida"
                target = st.selectbox(
                    "Zona objetivo del entrenamiento",
                    target_options,
                    index=target_options.index(current_target) if current_target in target_options else 0,
                )
                save_zone = st.form_submit_button("Guardar clasificación", use_container_width=True)
            if save_zone:
                for stored_attempt in attempts:
                    if stored_attempt.get("id") == edit_attempt_id:
                        stored_zone = stored_attempt.setdefault("metrics", {}).setdefault("heart_rate_zone", {})
                        stored_zone.update({
                            "observed_zone": observed,
                            "target_zone": None if target == "No definida" else target,
                            "zone_source": "manual",
                        })
                        break
                save_segment_attempts(attempts)
                st.session_state["segment_confirmation"] = "Clasificación de zona actualizada."
                _rerun()
    with st.expander("Editar o eliminar segmento"):
        with st.form(f"edit_segment_{selected_id}"):
            name = st.text_input("Nombre", value=selected.get("name", ""))
            discipline_options = ["Bici", "Trail", "Corrida", "Kayak", "Otra"]
            discipline_value = selected.get("discipline", "Otra")
            discipline = st.selectbox(
                "Disciplina",
                discipline_options,
                index=discipline_options.index(discipline_value) if discipline_value in discipline_options else 4,
                format_func=display_es,
            )
            segment_type = st.selectbox(
                "Tipo de segmento",
                SEGMENT_TYPES,
                index=SEGMENT_TYPES.index(selected.get("segment_type"))
                if selected.get("segment_type") in SEGMENT_TYPES else 0,
            )
            cycling_modality = selected.get("cycling_modality")
            if discipline_value == "Bici":
                cycling_modality = st.selectbox(
                    "Modalidad de ciclismo",
                    CYCLING_MODALITIES,
                    index=CYCLING_MODALITIES.index(cycling_modality)
                    if cycling_modality in CYCLING_MODALITIES else 2,
                )
            notes = st.text_area("Notas", value=selected.get("notes", ""))
            active = st.checkbox("Segmento activo", value=selected.get("active", True))
            submitted = st.form_submit_button("Guardar cambios", use_container_width=True)
        if submitted:
            if not name.strip():
                st.error("El nombre es obligatorio.")
            else:
                save_segment({
                    **selected,
                    "name": name.strip(),
                    "discipline": discipline,
                    "segment_type": segment_type,
                    "cycling_modality": cycling_modality if discipline == "Bici" else None,
                    "notes": notes.strip(),
                    "active": active,
                })
                st.session_state["segment_confirmation"] = f"Segmento «{name.strip()}» actualizado."
                _rerun()
        confirmed = st.checkbox("Confirmar eliminación", key=f"delete_segment_confirm_{selected_id}")
        if st.button("Eliminar segmento", disabled=not confirmed, key=f"delete_segment_{selected_id}"):
            delete_segment(selected_id)
            st.session_state["segment_confirmation"] = "Segmento eliminado."
            _rerun()


def _render_routes() -> None:
    st.subheader("Rutas repetidas")
    st.write(
        "Agrupa automáticamente actividades que recorren la misma ruta completa, "
        "en la misma dirección y disciplina. Así puedes comparar cada nuevo intento "
        "sin definir manualmente un segmento de principio a fin."
    )
    if st.button(
        "Reconstruir rutas con todos los FIT históricos",
        use_container_width=True,
        key="rebuild_route_families",
    ):
        with st.spinner("Comparando la geometría completa de las actividades FIT..."):
            summary = rebuild_route_families()
        st.session_state["route_rebuild_summary"] = summary
        _rerun()

    summary = st.session_state.pop("route_rebuild_summary", None)
    if summary:
        st.success(
            f"Se evaluaron {summary['activities_evaluated']} actividades y se formaron "
            f"{summary['families']} rutas; {summary['repeated_families']} tienen dos o más intentos."
        )
        if summary.get("failures"):
            with st.expander("Actividades que no pudieron compararse"):
                st.dataframe(summary["failures"], hide_index=True, use_container_width=True)

    routes = load_route_families()
    if not routes:
        st.info("Aún no hay rutas agrupadas. Reconstruye el historial para comenzar.")
        return

    categories = sorted({_discipline_category(item) for item in routes})
    filter_columns = st.columns(2)
    with filter_columns[0]:
        category_filter = st.multiselect(
            "Disciplina", categories, default=categories, key="route_category_filter"
        )
    with filter_columns[1]:
        show_single = st.checkbox(
            "Mostrar rutas con un solo intento", value=False, key="route_show_single"
        )
    filtered = [
        item for item in routes
        if item.get("active", True)
        and _discipline_category(item) in category_filter
        and (show_single or len(_included_route_attempts(item)) >= 2)
    ]
    repeated = [item for item in routes if len(_included_route_attempts(item)) >= 2]
    metric_columns = st.columns(3)
    metric_columns[0].metric("Rutas identificadas", len(routes))
    metric_columns[1].metric("Rutas repetidas", len(repeated))
    metric_columns[2].metric(
        "Intentos comparables", sum(len(_included_route_attempts(item)) for item in repeated)
    )
    if not filtered:
        st.info("No hay rutas que coincidan con los filtros seleccionados.")
        return

    def best_speed(item):
        speeds = [attempt.get("speed_kmh") for attempt in _included_route_attempts(item)]
        return max((value for value in speeds if isinstance(value, (int, float))), default=None)

    st.dataframe(
        pd.DataFrame([
            {
                "Ruta": item.get("name"),
                "Disciplina": _discipline_category(item),
                "Distancia (km)": round(float(item.get("distance_km", 0)), 1),
                "Intentos": len(_included_route_attempts(item)),
                "Mejor velocidad (km/h)": best_speed(item),
                "Último intento": max(
                    (attempt.get("date") or "" for attempt in _included_route_attempts(item)), default=""
                ),
            }
            for item in sorted(filtered, key=lambda value: len(_included_route_attempts(value)), reverse=True)
        ]),
        hide_index=True,
        use_container_width=True,
    )

    route_map = {item["id"]: item for item in filtered}
    selected_id = st.selectbox(
        "Consultar ruta",
        tuple(route_map),
        format_func=lambda route_id: (
            f"{route_map[route_id].get('name')} · "
            f"{len(_included_route_attempts(route_map[route_id]))} intentos"
        ),
        key="stored_route_selector",
    )
    selected = route_map[selected_id]
    all_route_attempts = sorted(_included_route_attempts(selected), key=lambda item: item.get("date") or "")
    available_zones = sorted({_zone_label(item) for item in all_route_attempts})
    selected_zone = st.selectbox(
        "Comparar intentos en zona observada",
        tuple(available_zones + ["Todas"]),
        key=f"route_zone_filter_{selected_id}",
        help="La velocidad y las demás variables se comparan dentro de la misma zona observada.",
    ) if available_zones else "Todas"
    attempts = [
        item for item in all_route_attempts
        if selected_zone == "Todas" or _zone_label(item) == selected_zone
    ]
    st.caption(
        f"{_discipline_category(selected)} · {selected.get('distance_km', 0):.1f} km · "
        f"{len(attempts)} intentos comparables en {selected_zone}"
    )
    frame = _stored_frame(selected)
    map_tab, profile_tab = st.tabs(["Mapa de la ruta", "Perfil altimétrico"])
    with map_tab:
        _render_stored_map(frame, f"stored_route_map_{selected_id}") if not frame.empty else st.warning("La ruta no contiene geometría utilizable.")
    with profile_tab:
        if not frame.empty and frame["altitude_meters"].notna().any():
            _render_stored_profile(frame)
        else:
            st.info("No hay altitud disponible para esta ruta.")

    rows = []
    speeds = [item.get("speed_kmh") for item in attempts if isinstance(item.get("speed_kmh"), (int, float))]
    median_speed = float(pd.Series(speeds).median()) if speeds else None
    for item in attempts:
        speed = item.get("speed_kmh")
        rows.append({
            "Fecha": item.get("date"),
            "Tiempo en movimiento": _duration_label(item.get("duration_seconds")),
            "Tiempo total": _duration_label(item.get("elapsed_seconds")),
            "Pausa": _duration_label(item.get("paused_seconds")),
            "Pausa (%)": round(100 * item["paused_share"], 1)
            if isinstance(item.get("paused_share"), (int, float)) else None,
            "Distancia (km)": item.get("distance_km"),
            "Velocidad (km/h)": speed,
            "Vs. mediana (%)": round(100 * (speed / median_speed - 1), 1)
            if median_speed and isinstance(speed, (int, float)) else None,
            "Desnivel + (m)": item.get("ascent_m"),
            "FC media": item.get("heart_rate_mean"),
            "FC máxima": item.get("heart_rate_max"),
            "Zona objetivo": _attempt_zone(item).get("target_zone") or "—",
            "Zona observada": _zone_label(item),
            "Tiempo zona dominante (%)": round(
                100 * _attempt_zone(item).get("dominant_share", 0), 1
            ) if _attempt_zone(item).get("dominant_share") is not None else None,
            "Confianza": _attempt_zone(item).get("zone_confidence", "Sin datos"),
            "Origen zona": "Manual" if _attempt_zone(item).get("zone_source") == "manual" else "Automático",
            "Error GPS mediano (m)": item.get("match_quality", {}).get("median_error_m"),
        })
    attempt_frame = pd.DataFrame(rows)
    st.subheader("Comparación de intentos")
    st.dataframe(attempt_frame, hide_index=True, use_container_width=True)
    if len(attempt_frame) >= 2:
        chart_options = [
            column for column in ("Velocidad (km/h)", "Pausa (%)", "FC media", "Desnivel + (m)")
            if attempt_frame[column].notna().any()
        ]
        if chart_options:
            chart_value = st.selectbox(
                "Variable de evolución", chart_options, key=f"route_chart_{selected_id}"
            )
            chart_frame = attempt_frame.copy()
            chart_frame["Fecha"] = pd.to_datetime(chart_frame["Fecha"], errors="coerce")
            chart_frame = chart_frame.dropna(subset=["Fecha", chart_value])
            chart = alt.Chart(chart_frame).mark_line(point=True, color="#1267E5").encode(
                x=alt.X("Fecha:T", title="Fecha"),
                y=alt.Y(f"{chart_value}:Q", title=chart_value, scale=alt.Scale(zero=False)),
                tooltip=["Fecha:T", alt.Tooltip(f"{chart_value}:Q", format=".2f")],
            ).properties(height=280)
            st.altair_chart(chart, use_container_width=True)

    _render_speed_progress("route", selected_id, selected_zone)

    if all_route_attempts:
        with st.expander("Revisar o corregir zona de un intento"):
            attempt_map = {item["activity_file"]: item for item in all_route_attempts}
            edit_file = st.selectbox(
                "Intento",
                tuple(attempt_map),
                format_func=lambda activity_file: (
                    f"{attempt_map[activity_file].get('date')} · {_zone_label(attempt_map[activity_file])}"
                ),
                key=f"route_zone_attempt_{selected_id}",
            )
            edit_attempt = attempt_map[edit_file]
            zone_data = _attempt_zone(edit_attempt)
            st.caption(
                f"Propuesta automática: {zone_data.get('automatic_zone') or 'Sin clasificar'} · "
                f"confianza: {zone_data.get('zone_confidence', 'Sin datos')}"
            )
            with st.form(f"route_zone_edit_{selected_id}_{edit_file}"):
                observed = st.selectbox(
                    "Zona observada corregida",
                    HEART_RATE_ZONE_OPTIONS,
                    index=HEART_RATE_ZONE_OPTIONS.index(_zone_label(edit_attempt)),
                )
                target_options = ("No definida",) + HEART_RATE_ZONE_OPTIONS[:-1]
                current_target = zone_data.get("target_zone") or "No definida"
                target = st.selectbox(
                    "Zona objetivo del entrenamiento",
                    target_options,
                    index=target_options.index(current_target) if current_target in target_options else 0,
                )
                save_zone = st.form_submit_button("Guardar clasificación", use_container_width=True)
            if save_zone:
                edit_attempt.setdefault("heart_rate_zone", {}).update({
                    "observed_zone": observed,
                    "target_zone": None if target == "No definida" else target,
                    "zone_source": "manual",
                })
                save_route_family(selected)
                st.session_state["segment_confirmation"] = "Clasificación de zona actualizada."
                _rerun()

    with st.expander("Nombre y estado de la ruta"):
        with st.form(f"edit_route_{selected_id}"):
            name = st.text_input("Nombre", value=selected.get("name", ""))
            active = st.checkbox("Ruta activa", value=selected.get("active", True))
            submitted = st.form_submit_button("Guardar cambios", use_container_width=True)
        if submitted:
            if not name.strip():
                st.error("El nombre es obligatorio.")
            else:
                save_route_family({**selected, "name": name.strip(), "active": active})
                st.session_state["segment_confirmation"] = f"Ruta «{name.strip()}» actualizada."
                _rerun()
    with st.expander("Cómo se reconoce una misma ruta"):
        st.write(
            "Se exige la misma disciplina y modalidad, una distancia dentro de ±10%, "
            "inicio y final a menos de 250 m, la misma dirección y una geometría ordenada "
            "similar a lo largo de todo el recorrido. La frecuencia cardiaca, el tiempo y "
            "la velocidad no influyen en el emparejamiento: son justamente lo que se compara."
        )


def _render_save_form(path: Path, payload: dict, segment: pd.DataFrame, metrics: dict, start_km: float, end_km: float) -> None:
    activity = payload.get("activity", {})
    detected = _sport_label(activity)
    if detected == "Trail running":
        detected = "Trail"
    disciplines = ["Bici", "Trail", "Corrida", "Kayak", "Otra"]
    default_discipline = detected if detected in disciplines else "Otra"
    st.subheader("Guardar como segmento")
    with st.form(f"save_segment_{path.name}_{start_km:.2f}_{end_km:.2f}"):
        name = st.text_input("Nombre del segmento *")
        discipline = st.selectbox(
            "Disciplina", disciplines, index=disciplines.index(default_discipline),
            format_func=display_es,
        )
        inferred_type = _infer_segment_type(metrics)
        segment_type = st.selectbox(
            "Tipo de segmento",
            SEGMENT_TYPES,
            index=SEGMENT_TYPES.index(inferred_type),
            help="Se propone automáticamente a partir del desnivel, pero puedes corregirlo.",
        )
        cycling_modality = activity.get("cycling_modality")
        if default_discipline == "Bici":
            cycling_modality = st.selectbox(
                "Modalidad de ciclismo",
                CYCLING_MODALITIES,
                index=CYCLING_MODALITIES.index(cycling_modality)
                if cycling_modality in CYCLING_MODALITIES else 2,
            )
        notes = st.text_area("Notas", help="Referencia del lugar, superficie o propósito del segmento.")
        submitted = st.form_submit_button("Guardar segmento", use_container_width=True)
    if not submitted:
        return
    if not name.strip():
        st.error("El nombre del segmento es obligatorio.")
        return
    geometry = _geometry(segment)
    value = {
        "name": name.strip(),
        "discipline": discipline,
        "cycling_modality": (
            cycling_modality if discipline == "Bici" else None
        ),
        "segment_type": segment_type,
        "attempt_count": 1,
        "notes": notes.strip(),
        "active": True,
        "source": {
            "activity_file": path.name,
            "fit_file": activity.get("source_file"),
            "activity_sha256": activity.get("source_sha256"),
            "activity_date": activity.get("date"),
            "start_km": round(float(start_km), 3),
            "end_km": round(float(end_km), 3),
        },
        "direction": {
            "start": {"latitude": geometry[0]["latitude"], "longitude": geometry[0]["longitude"]},
            "end": {"latitude": geometry[-1]["latitude"], "longitude": geometry[-1]["longitude"]},
        },
        "metrics": {
            key: round(float(value), 4) if isinstance(value, (int, float)) else value
            for key, value in metrics.items()
        },
        "geometry": geometry,
    }
    duplicate = find_duplicate(value)
    if duplicate:
        st.warning(
            f"Ese intervalo ya está guardado como «{duplicate.get('name', 'Sin nombre')}». "
            "Puedes editarlo desde el catálogo."
        )
        return
    save_segment(value)
    st.session_state["segment_confirmation"] = f"Segmento «{name.strip()}» guardado correctamente."
    _rerun()


def render():
    confirmation = st.session_state.pop("segment_confirmation", None)
    if confirmation:
        st.success(confirmation)
    explorer_tab, catalog_tab, routes_tab = st.tabs(
        ["Explorar y guardar", "Segmentos guardados", "Rutas repetidas"]
    )
    with catalog_tab:
        _render_catalog()
    with explorer_tab:
        _render_explorer()
    with routes_tab:
        _render_routes()


def _render_explorer():
    st.write(
        "Selecciona un tramo dentro de una actividad FIT, comprueba su geometría "
        "y guárdalo como referencia para el reconocimiento futuro."
    )
    paths = sorted(
        ACTIVITIES_DIR.glob("activity_*.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    ) if ACTIVITIES_DIR.exists() else []
    if not paths:
        st.info("No hay actividades FIT procesadas para explorar.")
        return

    path_map = {path.name: path for path in paths}
    selected_name = st.selectbox(
        "Actividad FIT procesada",
        tuple(path_map),
        format_func=lambda name: name.removeprefix("activity_").removesuffix(".json"),
        key="segment_activity_selector",
    )
    path = path_map[selected_name]
    with st.spinner("Cargando recorrido y muestras..."):
        payload = _load_activity(str(path), path.stat().st_mtime_ns)
        frame = _records_frame(payload)
    if len(frame) < 2:
        st.warning("La actividad seleccionada no contiene un recorrido GPS utilizable.")
        return

    activity, summary = payload.get("activity", {}), payload.get("summary", {})
    header = st.columns(5)
    header[0].metric("Fecha", activity.get("date") or "—")
    header[1].metric("Disciplina", _sport_label(activity))
    header[2].metric("Distancia", f"{frame['distance_km'].max():.2f} km")
    header[3].metric("Duración", _duration_label(activity.get("duration_seconds")))
    header[4].metric("Desnivel +", f"{activity.get('ascent_meters', 0):.0f} m")
    st.caption(
        f"FC media de la actividad: {summary.get('average_heart_rate_bpm', '—')} bpm · "
        f"{len(frame):,} muestras GPS disponibles".replace(",", ".")
    )

    maximum_km = float(frame["distance_km"].max())
    default_end = min(maximum_km, max(1.0, maximum_km * 0.25))
    range_key = f"segment_range_{selected_name}"
    pending_key = f"pending_{range_key}"
    if pending_key in st.session_state:
        st.session_state[range_key] = st.session_state.pop(pending_key)
    start_km, end_km = st.slider(
        "Selecciona el tramo (km inicial y final)",
        min_value=0.0,
        max_value=round(maximum_km, 2),
        value=(0.0, round(default_end, 2)),
        step=0.05,
        key=range_key,
    )
    if end_km <= start_km:
        st.warning("El final del tramo debe estar después del inicio.")
        return

    segment = _nearest_interval(frame, start_km, end_km)
    metrics = _segment_metrics(segment)
    st.subheader("Mapa")
    endpoint_to_adjust = st.radio(
        "Punto que quieres ajustar en el mapa",
        ("Inicio", "Final"),
        horizontal=True,
        key=f"segment_endpoint_{selected_name}",
        help=(
            "Selecciona Inicio o Final y luego haz clic sobre el punto deseado de la ruta. "
            "La posición se ajustará al punto GPS más cercano."
        ),
    )
    layer_name = _map_layer_selector(f"segment_map_layer_{selected_name}")
    map_state = _render_map(
        frame,
        segment,
        map_key=f"segment_map_{selected_name}",
        layer_name=layer_name,
    )
    st.caption(
        "Marcador azul: inicio · marcador rojo: final. Selecciona el extremo que "
        "quieres cambiar y haz clic sobre la ruta."
    )
    map_state = map_state or {}
    map_clicked = map_state.get("last_clicked")
    map_processed_key = f"processed_map_click_{selected_name}"
    map_signature = None
    if isinstance(map_clicked, dict) and {"lat", "lng"}.issubset(map_clicked):
        map_signature = (
            round(float(map_clicked["lat"]), 7),
            round(float(map_clicked["lng"]), 7),
        )
    if map_signature and st.session_state.get(map_processed_key) != map_signature:
        clicked = map_clicked
        effective_endpoint = endpoint_to_adjust
        st.session_state[map_processed_key] = map_signature
    else:
        clicked, effective_endpoint = None, None
    if isinstance(clicked, dict) and {"lat", "lng"}.issubset(clicked):
        snapped_km = round(
            _nearest_route_km(
                frame, float(clicked["lat"]), float(clicked["lng"])
            ),
            2,
        )
        selected_value = (
            (snapped_km, end_km)
            if effective_endpoint == "Inicio"
            else (start_km, snapped_km)
        )
        if selected_value[1] > selected_value[0]:
            st.session_state[pending_key] = selected_value
            _rerun()
        else:
            st.warning(
                "Ese punto dejaría el final antes del inicio. Selecciona "
                "otro punto o cambia el extremo que estás ajustando."
            )
    st.subheader("Perfil altimétrico y selección")
    st.caption(
        "Arrastra horizontalmente sobre el perfil para cambiar el inicio y el final. "
        "También puedes usar el selector numérico superior para un ajuste preciso."
    )
    profile_event = _render_profile(
        frame,
        start_km,
        end_km,
        chart_key=f"segment_profile_{selected_name}",
    )
    selected_range = _selected_distance_range(profile_event)
    if selected_range:
        selected_start = max(0.0, min(maximum_km, selected_range[0]))
        selected_end = max(0.0, min(maximum_km, selected_range[1]))
        selected_value = (round(selected_start, 2), round(selected_end, 2))
        if (
            selected_value[1] > selected_value[0]
            and (
                abs(selected_value[0] - start_km) > 0.01
                or abs(selected_value[1] - end_km) > 0.01
            )
        ):
            st.session_state[pending_key] = selected_value
            _rerun()

    st.subheader("Previsualización del tramo")
    first_row = st.columns(6)
    first_row[0].metric("Distancia", f"{metrics['distance_km']:.2f} km")
    first_row[1].metric("Tiempo", _duration_label(metrics["elapsed_seconds"]))
    first_row[2].metric("Velocidad media", f"{metrics['speed_kmh']:.1f} km/h" if metrics["speed_kmh"] is not None else "—")
    first_row[3].metric("Desnivel neto", f"{metrics['net_m']:+.0f} m")
    first_row[4].metric("Desnivel +", f"{metrics['ascent_m']:.0f} m")
    first_row[5].metric("Pendiente media", f"{metrics['slope_percent']:+.1f}%" if metrics["slope_percent"] is not None else "—")
    second_row = st.columns(4)
    second_row[0].metric("Desnivel −", f"{metrics['descent_m']:.0f} m")
    second_row[1].metric("VAM neta", f"{metrics['vam_mh']:.0f} m/h" if metrics["vam_mh"] is not None else "—")
    second_row[2].metric("FC media", f"{metrics['heart_rate_mean']:.0f} bpm" if metrics["heart_rate_mean"] is not None else "—")
    second_row[3].metric("FC máxima", f"{metrics['heart_rate_max']:.0f} bpm" if metrics["heart_rate_max"] is not None else "—")
    _render_save_form(path, payload, segment, metrics, start_km, end_km)
MAP_LAYERS = {
    "OpenTopoMap": {
        "label": "Topográfico",
        "url": "https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png",
        "attribution": "© OpenTopoMap · © OpenStreetMap contributors",
        "help": "Curvas de nivel y relieve; recomendado para análisis general.",
    },
    "OpenHikingMap": {
        "label": "Senderismo",
        "url": "https://tile.openmaps.fr/openhikingmap/{z}/{x}/{y}.png",
        "attribution": "© OpenHikingMap · © OpenStreetMap contributors",
        "help": "Prioriza senderos y elementos de montaña.",
    },
    "CyclOSM": {
        "label": "Ciclismo",
        "url": "https://{s}.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png",
        "attribution": "© CyclOSM · © OpenStreetMap contributors",
        "help": "Muestra infraestructura y rutas útiles para bicicleta.",
    },
    "OSM Swiss Style": {
        "label": "Suiza",
        "url": "https://tile.osm.ch/osm-swiss-style/{z}/{x}/{y}.png",
        "attribution": "© OpenStreetMap contributors · OSM Swiss Style",
        "help": "Estilo local de OpenStreetMap para rutas en Suiza.",
    },
}


def _map_layer_selector(key: str) -> str:
    return st.selectbox(
        "Mapa base",
        tuple(MAP_LAYERS),
        format_func=lambda item: MAP_LAYERS[item]["label"],
        key=key,
        help="Cambia solo la capa visual: el recorrido y el análisis no se modifican.",
    )


def _base_map(location, layer_name: str, zoom_start=12):
    layer = MAP_LAYERS[layer_name]
    map_view = folium.Map(location=location, zoom_start=zoom_start, tiles=None, control_scale=True)
    folium.TileLayer(
        tiles=layer["url"], attr=layer["attribution"], name=layer["label"],
        subdomains="abc", max_zoom=20,
    ).add_to(map_view)
    return map_view
