"""Evaluate when each independent metric series should be measured again."""

from __future__ import annotations

from datetime import date, timedelta
from itertools import product

from metrics.registry import METRIC_REGISTRY


STATUS_STYLE = {
    "Reciente": ("🟢", "#16A34A"),
    "Vigente": ("🔵", "#2563EB"),
    "Próxima a vencer": ("🟡", "#CA8A04"),
    "Evaluación pendiente": ("🟠", "#EA580C"),
    "Desactualizada": ("🔴", "#DC2626"),
    "Sin medición": ("⚪", "#64748B"),
}


def classify_freshness(days_since):
    if days_since is None:
        return "Sin medición"
    if days_since <= 14:
        return "Reciente"
    if days_since <= 21:
        return "Vigente"
    if days_since <= 27:
        return "Próxima a vencer"
    if days_since <= 35:
        return "Evaluación pendiente"
    return "Desactualizada"


def _series(metric):
    dimensions = metric.get("series_dimensions", [])
    dimension_values = []
    for dimension in dimensions:
        if dimension == "heart_rate_zone_id":
            dimension_values.append(metric.get("heart_rate_zone_ids", []))
        elif dimension == "slope_band":
            dimension_values.append(metric.get("slope_bands", []))
    combinations = list(product(*dimension_values)) if dimension_values else [()]
    disciplines = metric.get("disciplines", []) if metric.get("separate_by_discipline", True) else [None]
    return [
        dict(zip(dimensions, values), discipline=discipline)
        for discipline in disciplines for values in combinations
    ]


def metric_freshness(metrics, measurements, as_of=None):
    as_of = as_of or date.today()
    result = []
    for metric in metrics:
        implementation = METRIC_REGISTRY.get(metric.get("id"), {})
        protocol = implementation.get("protocol", {})
        interval = int(protocol.get("target_interval_days", 28))
        for series in _series(metric):
            matching = []
            for item in measurements:
                if item.get("metric_id") != metric.get("id") or item.get("date", "") > as_of.isoformat():
                    continue
                if series.get("discipline") and item.get("discipline") != series["discipline"]:
                    continue
                if series.get("heart_rate_zone_id") and item.get("heart_rate_zone_id") != series["heart_rate_zone_id"]:
                    continue
                if series.get("slope_band") and item.get("components", {}).get("slope_band") != series["slope_band"]:
                    continue
                matching.append(item)
            latest = max((item.get("date") for item in matching), default=None)
            latest_date = date.fromisoformat(latest) if latest else None
            days_since = (as_of - latest_date).days if latest_date else None
            status = classify_freshness(days_since)
            parts = [series.get("discipline"), series.get("heart_rate_zone_id"), series.get("slope_band")]
            result.append({
                "metric_id": metric.get("id"),
                "metric_name": metric.get("name_es") or metric.get("name") or metric.get("id"),
                "series_label": " · ".join(value for value in parts if value) or "Serie general",
                **series,
                "latest_date": latest,
                "days_since": days_since,
                "status": status,
                "status_icon": STATUS_STYLE[status][0],
                "status_color": STATUS_STYLE[status][1],
                "target_date": (latest_date + timedelta(days=interval)).isoformat() if latest_date else None,
                "observations": len(matching),
                "protocol": protocol,
            })
    return result
