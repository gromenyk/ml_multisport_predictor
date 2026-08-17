"""Aggregation helpers for metric visualization and trend estimation."""

from __future__ import annotations

from collections import defaultdict
from statistics import median


def activity_medians(measurements):
    """Return one median observation per source activity within a metric series."""
    groups = defaultdict(list)
    for item in measurements:
        activity_key = (
            item.get("activity_id")
            or item.get("activity_file")
            or f"manual:{item.get('date', '')}"
        )
        groups[(item.get("date", ""), activity_key)].append(item)
    result = []
    for (measurement_date, activity_key), rows in groups.items():
        values = [float(item["value"]) for item in rows]
        result.append({
            "date": measurement_date,
            "activity_id": activity_key,
            "activity_file": rows[0].get("activity_file", ""),
            "value": median(values),
            "segment_count": len(rows),
        })
    return sorted(result, key=lambda item: (item["date"], item["activity_id"]))
