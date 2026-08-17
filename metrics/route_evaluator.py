"""Evaluate whether a planned GPX route contains a metric-compatible segment."""

from __future__ import annotations

from datetime import datetime
from math import asin, cos, radians, sin, sqrt
from typing import Any
from xml.etree import ElementTree as ET


EARTH_RADIUS_M = 6_371_000.0
MAX_GPX_BYTES = 20 * 1024 * 1024


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _distance(a: dict[str, Any], b: dict[str, Any]) -> float:
    lat1, lat2 = radians(a["lat"]), radians(b["lat"])
    dlat = lat2 - lat1
    dlon = radians(b["lon"] - a["lon"])
    value = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * asin(sqrt(min(1.0, value)))


def parse_gpx(payload: bytes) -> dict[str, Any]:
    if not payload:
        raise ValueError("El archivo GPX está vacío.")
    if len(payload) > MAX_GPX_BYTES:
        raise ValueError("El GPX supera el límite de 20 MB.")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as error:
        raise ValueError("No se pudo leer el XML del archivo GPX.") from error

    segments = []
    track_segments = [node for node in root.iter() if _local_name(node.tag) == "trkseg"]
    containers = track_segments
    point_name = "trkpt"
    if not containers:
        route_points = [node for node in root.iter() if _local_name(node.tag) == "rtept"]
        containers = [route_points] if route_points else []
        point_name = "rtept"

    for segment_index, container in enumerate(containers):
        nodes = (
            [node for node in container if _local_name(node.tag) == point_name]
            if not isinstance(container, list) else container
        )
        points = []
        cumulative = 0.0
        previous = None
        for node in nodes:
            try:
                lat, lon = float(node.attrib["lat"]), float(node.attrib["lon"])
            except (KeyError, TypeError, ValueError):
                continue
            children = {_local_name(child.tag): child.text for child in node}
            try:
                elevation = float(children.get("ele"))
            except (TypeError, ValueError):
                elevation = None
            point = {
                "lat": lat, "lon": lon, "elevation_m": elevation,
                "time": _time(children.get("time")), "segment_id": segment_index,
            }
            if previous is not None:
                cumulative += _distance(previous, point)
            point["distance_m"] = cumulative
            points.append(point)
            previous = point
        if len(points) >= 2:
            segments.append(points)

    if not segments:
        raise ValueError("El GPX no contiene una ruta o track con al menos dos puntos válidos.")
    if not any(point.get("elevation_m") is not None for segment in segments for point in segment):
        raise ValueError("El GPX no contiene datos de elevación; no es posible evaluar pendientes.")
    if any(point.get("elevation_m") is None for segment in segments for point in segment):
        raise ValueError("Algunos puntos del GPX no tienen elevación; utiliza una ruta con perfil completo.")

    total_distance = sum(segment[-1]["distance_m"] for segment in segments)
    total_ascent = sum(
        max(0.0, current["elevation_m"] - previous["elevation_m"])
        for segment in segments
        for previous, current in zip(segment, segment[1:])
    )
    timed = all(point.get("time") is not None for segment in segments for point in segment)
    monotonic_times = timed and all(
        current["time"] > previous["time"]
        for segment in segments
        for previous, current in zip(segment, segment[1:])
    )
    return {
        "segments": segments,
        "point_count": sum(len(segment) for segment in segments),
        "distance_m": total_distance,
        "ascent_m": total_ascent,
        "has_usable_timestamps": monotonic_times,
    }


def _bands(protocol: dict[str, Any]) -> list[dict[str, float | str]]:
    result = []
    for label in protocol.get("slope_bands") or []:
        try:
            low, high = label.replace("%", "").split("-", 1)
            result.append({"label": label, "minimum": float(low), "maximum": float(high)})
        except (TypeError, ValueError):
            continue
    if not result and protocol.get("minimum_slope_percent") is not None and protocol.get("maximum_slope_percent") is not None:
        result.append({
            "label": f"{protocol['minimum_slope_percent']:g}-{protocol['maximum_slope_percent']:g}%",
            "minimum": float(protocol["minimum_slope_percent"]),
            "maximum": float(protocol["maximum_slope_percent"]),
        })
    return result


def _interpolate(a: dict[str, Any], b: dict[str, Any], fraction: float) -> dict[str, Any]:
    fraction = max(0.0, min(1.0, fraction))
    return {
        "lat": a["lat"] + fraction * (b["lat"] - a["lat"]),
        "lon": a["lon"] + fraction * (b["lon"] - a["lon"]),
        "elevation_m": a["elevation_m"] + fraction * (b["elevation_m"] - a["elevation_m"]),
        "distance_m": a["distance_m"] + fraction * (b["distance_m"] - a["distance_m"]),
    }


def evaluate_route(
    parsed: dict[str, Any], protocol: dict[str, Any], speed_kmh: float
) -> dict[str, Any]:
    duration = float(protocol.get("duration_seconds") or 0)
    bands = _bands(protocol)
    if duration <= 0 or not bands:
        raise ValueError("Esta métrica no define una ventana temporal y una banda de pendiente evaluables en una ruta.")
    if speed_kmh <= 0:
        raise ValueError("La velocidad prevista debe ser mayor que cero.")
    target_distance = speed_kmh / 3.6 * duration
    candidates = []
    route_offset = 0.0

    for segment in parsed["segments"]:
        end_index = 1
        for start_index, start in enumerate(segment[:-1]):
            target = start["distance_m"] + target_distance
            end_index = max(end_index, start_index + 1)
            while end_index < len(segment) and segment[end_index]["distance_m"] < target:
                end_index += 1
            if end_index >= len(segment):
                break
            before, after = segment[end_index - 1], segment[end_index]
            interval = after["distance_m"] - before["distance_m"]
            fraction = 0.0 if interval <= 0 else (target - before["distance_m"]) / interval
            end = _interpolate(before, after, fraction)
            elevation_change = end["elevation_m"] - start["elevation_m"]
            slope = 100.0 * elevation_change / target_distance
            for band in bands:
                if band["minimum"] - 1e-9 <= slope <= band["maximum"] + 1e-9:
                    candidates.append({
                        "slope_band": band["label"],
                        "start_km": (route_offset + start["distance_m"]) / 1000.0,
                        "end_km": (route_offset + end["distance_m"]) / 1000.0,
                        "distance_km": target_distance / 1000.0,
                        "duration_seconds": duration,
                        "estimated_speed_kmh": speed_kmh,
                        "mean_slope_percent": slope,
                        "net_elevation_gain_m": elevation_change,
                        "start_lat": start["lat"], "start_lon": start["lon"],
                        "end_lat": end["lat"], "end_lon": end["lon"],
                    })
            
        route_offset += segment[-1]["distance_m"]

    # Keep spatially distinct options so a dense GPX does not produce hundreds
    # of nearly identical rolling windows.
    selected = []
    for candidate in sorted(candidates, key=lambda item: (-item["net_elevation_gain_m"], item["start_km"])):
        if all(abs(candidate["start_km"] - item["start_km"]) >= candidate["distance_km"] * 0.5 for item in selected):
            selected.append(candidate)
        if len(selected) >= 20:
            break
    selected.sort(key=lambda item: item["start_km"])
    return {
        "compatible": bool(candidates),
        "candidate_count": len(candidates),
        "candidates": selected,
        "duration_seconds": duration,
        "target_distance_m": target_distance,
        "speed_kmh": speed_kmh,
        "slope_bands": [item["label"] for item in bands],
        "assumptions": [
            "La duración se convirtió a distancia usando una velocidad constante indicada por el usuario.",
            "La pendiente es 100 × cambio neto de elevación ÷ distancia del tramo.",
            "El GPX no permite comprobar frecuencia cardíaca, pausas, superficie ni ejecución real.",
        ],
    }


def elevation_profile(parsed: dict[str, Any]) -> list[dict[str, float]]:
    rows = []
    offset = 0.0
    for segment in parsed["segments"]:
        rows.extend({"distance_km": (offset + point["distance_m"]) / 1000.0, "elevation_m": point["elevation_m"]} for point in segment)
        offset += segment[-1]["distance_m"]
    return rows
