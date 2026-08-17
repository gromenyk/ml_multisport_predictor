"""Find non-overlapping three-minute segments matching the metric protocol."""

from __future__ import annotations

from bisect import bisect_left
from copy import deepcopy
from datetime import datetime
from math import isfinite
from typing import Any

from metrics.speed_at_hr_zone.protocol import (
    HEART_RATE_TOLERANCE_BPM, MAX_RECORD_GAP_SECONDS, MAX_SLOPE_PERCENT,
    MIN_MOVING_SPEED_MPS, MIN_SLOPE_PERCENT, MIN_TIME_IN_EXPANDED_ZONE,
    SEGMENT_SECONDS,
)


def _discipline(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized in ("corrida", "carrera", "running"):
        return "Corrida"
    if normalized in ("trail", "trail running"):
        return "Trail"
    return value


def _finite(value) -> bool:
    return isinstance(value, (int, float)) and isfinite(value)


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _intervals(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared = []
    ordered = sorted(
        (row for row in records if row.get("timestamp")),
        key=lambda row: row["timestamp"],
    )
    for previous, current in zip(ordered, ordered[1:]):
        try:
            start = _timestamp(previous["timestamp"])
            end = _timestamp(current["timestamp"])
        except (TypeError, ValueError):
            continue
        duration = (end - start).total_seconds()
        previous_distance = previous.get("distance_meters")
        current_distance = current.get("distance_meters")
        if not (_finite(previous_distance) and _finite(current_distance)):
            continue
        distance = current_distance - previous_distance
        heart_rates = [
            item
            for item in (
                previous.get("heart_rate_bpm"),
                current.get("heart_rate_bpm"),
            )
            if _finite(item)
        ]
        altitudes = (
            previous.get("altitude_meters"),
            current.get("altitude_meters"),
        )
        speed = current.get("speed_mps")
        if not _finite(speed) and duration > 0:
            speed = distance / duration
        prepared.append(
            {
                "start": start,
                "end": end,
                "duration_seconds": duration,
                "distance_meters": distance,
                "heart_rate_bpm": (
                    sum(heart_rates) / len(heart_rates) if heart_rates else None
                ),
                "start_altitude_meters": (
                    altitudes[0] if _finite(altitudes[0]) else None
                ),
                "end_altitude_meters": (
                    altitudes[1] if _finite(altitudes[1]) else None
                ),
                "speed_mps": speed,
                "continuous": (
                    0 < duration <= MAX_RECORD_GAP_SECONDS
                    and distance > 0
                    and _finite(speed)
                    and speed >= MIN_MOVING_SPEED_MPS
                ),
                "relaxed_moving": (
                    0 < duration <= 15.0
                    and distance > 0
                    and _finite(speed)
                    and speed >= MIN_MOVING_SPEED_MPS
                ),
                "relaxed_invalid": (
                    duration <= 0
                    or duration > 15.0
                    or distance < 0
                    or not _finite(speed)
                ),
            }
        )
    return prepared


def _evaluate_candidate(
    intervals: list[dict[str, Any]],
    zone_lower_bpm: int,
    zone_upper_bpm: int,
) -> dict[str, Any]:
    duration = sum(item["duration_seconds"] for item in intervals)
    distance = sum(item["distance_meters"] for item in intervals)
    weighted_hr = sum(
        item["heart_rate_bpm"] * item["duration_seconds"]
        for item in intervals
        if item["heart_rate_bpm"] is not None
    )
    hr_duration = sum(
        item["duration_seconds"]
        for item in intervals
        if item["heart_rate_bpm"] is not None
    )
    expanded_lower = zone_lower_bpm - HEART_RATE_TOLERANCE_BPM
    expanded_upper = zone_upper_bpm + HEART_RATE_TOLERANCE_BPM
    time_in_zone = sum(
        item["duration_seconds"]
        for item in intervals
        if item["heart_rate_bpm"] is not None
        and expanded_lower <= item["heart_rate_bpm"] <= expanded_upper
    )
    start_altitude = intervals[0]["start_altitude_meters"]
    end_altitude = intervals[-1]["end_altitude_meters"]
    slope = (
        100.0 * (end_altitude - start_altitude) / distance
        if distance > 0
        and start_altitude is not None
        and end_altitude is not None
        else None
    )
    mean_hr = weighted_hr / hr_duration if hr_duration else None
    zone_share = time_in_zone / duration if duration else 0.0
    reasons = []
    has_time_gap = any(
        abs((current["start"] - previous["end"]).total_seconds()) > 0.001
        for previous, current in zip(intervals, intervals[1:])
    )
    if has_time_gap or not all(item["continuous"] for item in intervals):
        reasons.append("movement_not_continuous")
    if mean_hr is None or not zone_lower_bpm <= mean_hr <= zone_upper_bpm:
        reasons.append("mean_hr_outside_zone")
    if zone_share < MIN_TIME_IN_EXPANDED_ZONE:
        reasons.append("insufficient_time_in_zone")
    if slope is None or not MIN_SLOPE_PERCENT <= slope <= MAX_SLOPE_PERCENT:
        reasons.append("mean_slope_outside_range")
    return {
        "accepted": not reasons,
        "rejection_reasons": reasons,
        "start_time": intervals[0]["start"].isoformat(),
        "end_time": intervals[-1]["end"].isoformat(),
        "duration_seconds": duration,
        "distance_meters": distance,
        "mean_heart_rate_bpm": mean_hr,
        "time_in_zone_seconds": time_in_zone,
        "time_in_zone_share": zone_share,
        "mean_slope_percent": slope,
        "zone_lower_bpm": zone_lower_bpm,
        "zone_upper_bpm": zone_upper_bpm,
        "expanded_zone_lower_bpm": expanded_lower,
        "expanded_zone_upper_bpm": expanded_upper,
    }


def _clip_to_duration(
    intervals: list[dict[str, Any]], duration_seconds: float
) -> list[dict[str, Any]]:
    """Return intervals covering exactly the requested duration."""
    clipped = []
    remaining = duration_seconds
    for interval in intervals:
        if remaining <= 0:
            break
        value = deepcopy(interval)
        original_duration = value["duration_seconds"]
        used_duration = min(original_duration, remaining)
        fraction = used_duration / original_duration
        if fraction < 1:
            value["duration_seconds"] = used_duration
            value["distance_meters"] *= fraction
            value["end"] = value["start"] + (
                value["end"] - value["start"]
            ) * fraction
            start_altitude = value["start_altitude_meters"]
            end_altitude = value["end_altitude_meters"]
            if start_altitude is not None and end_altitude is not None:
                value["end_altitude_meters"] = start_altitude + (
                    end_altitude - start_altitude
                ) * fraction
        clipped.append(value)
        remaining -= used_duration
    return clipped


def _fast_candidate(
    intervals, prefixes, start, end, zone_lower_bpm, zone_upper_bpm,
    segment_seconds=SEGMENT_SECONDS,
):
    """Evaluate an exact protocol-duration window using cumulative values."""
    last_index = end - 1
    before_last = (
        prefixes["duration"][last_index] - prefixes["duration"][start]
    )
    last = intervals[last_index]
    used = segment_seconds - before_last
    fraction = used / last["duration_seconds"]

    def total(key, last_value):
        return (
            prefixes[key][last_index]
            - prefixes[key][start]
            + last_value * fraction
        )

    heart_rate = last["heart_rate_bpm"]
    distance = total("distance", last["distance_meters"])
    weighted_hr = total(
        "weighted_hr",
        heart_rate * last["duration_seconds"] if heart_rate is not None else 0,
    )
    hr_duration = total(
        "hr_duration",
        last["duration_seconds"] if heart_rate is not None else 0,
    )
    expanded_lower = zone_lower_bpm - HEART_RATE_TOLERANCE_BPM
    expanded_upper = zone_upper_bpm + HEART_RATE_TOLERANCE_BPM
    zone_duration = total(
        "zone_duration",
        last["duration_seconds"]
        if heart_rate is not None
        and expanded_lower <= heart_rate <= expanded_upper
        else 0,
    )
    mean_hr = weighted_hr / hr_duration if hr_duration else None
    start_altitude = intervals[start]["start_altitude_meters"]
    last_start_altitude = last["start_altitude_meters"]
    last_end_altitude = last["end_altitude_meters"]
    end_altitude = (
        last_start_altitude
        + (last_end_altitude - last_start_altitude) * fraction
        if last_start_altitude is not None and last_end_altitude is not None
        else None
    )
    slope = (
        100 * (end_altitude - start_altitude) / distance
        if distance > 0
        and start_altitude is not None
        and end_altitude is not None
        else None
    )
    zone_share = zone_duration / segment_seconds
    bad_movement = prefixes["bad_movement"][end] - prefixes["bad_movement"][start]
    # gap[index] represents the boundary before interval[index].
    bad_gaps = prefixes["gap"][end] - prefixes["gap"][start + 1]
    moving_seconds = total(
        "relaxed_moving_duration",
        last["duration_seconds"] if last["relaxed_moving"] else 0,
    )
    stopped_seconds = max(0.0, segment_seconds - moving_seconds)
    moving_share = moving_seconds / segment_seconds
    invalid = prefixes["relaxed_invalid"][end] - prefixes["relaxed_invalid"][start]
    long_gaps = prefixes["relaxed_gap"][end] - prefixes["relaxed_gap"][start + 1]
    reasons = []
    if invalid or long_gaps or moving_share < 0.95 or stopped_seconds > 10.0:
        reasons.append("movement_not_continuous")
    if mean_hr is None or not zone_lower_bpm <= mean_hr <= zone_upper_bpm:
        reasons.append("mean_hr_outside_zone")
    if zone_share < MIN_TIME_IN_EXPANDED_ZONE:
        reasons.append("insufficient_time_in_zone")
    if slope is None or not MIN_SLOPE_PERCENT <= slope <= MAX_SLOPE_PERCENT:
        reasons.append("mean_slope_outside_range")
    end_time = last["start"] + (last["end"] - last["start"]) * fraction
    return {
        "accepted": not reasons,
        "rejection_reasons": reasons,
        "start_time": intervals[start]["start"].isoformat(),
        "end_time": end_time.isoformat(),
        "duration_seconds": segment_seconds,
        "distance_meters": distance,
        "mean_heart_rate_bpm": mean_hr,
        "time_in_zone_seconds": zone_duration,
        "time_in_zone_share": zone_share,
        "moving_time_share": moving_share,
        "stopped_time_seconds": stopped_seconds,
        "mean_slope_percent": slope,
        "zone_lower_bpm": zone_lower_bpm,
        "zone_upper_bpm": zone_upper_bpm,
        "expanded_zone_lower_bpm": expanded_lower,
        "expanded_zone_upper_bpm": expanded_upper,
    }


def _build_prefixes(intervals, zone_lower_bpm, zone_upper_bpm):
    keys = (
        "duration",
        "distance",
        "weighted_hr",
        "hr_duration",
        "zone_duration",
        "bad_movement",
        "gap",
        "relaxed_moving_duration",
        "relaxed_invalid",
        "relaxed_gap",
    )
    result = {key: [0.0] for key in keys}
    expanded_lower = zone_lower_bpm - HEART_RATE_TOLERANCE_BPM
    expanded_upper = zone_upper_bpm + HEART_RATE_TOLERANCE_BPM
    for index, item in enumerate(intervals):
        duration = item["duration_seconds"]
        heart_rate = item["heart_rate_bpm"]
        additions = {
            "duration": duration,
            "distance": item["distance_meters"],
            "weighted_hr": (
                heart_rate * duration if heart_rate is not None else 0
            ),
            "hr_duration": duration if heart_rate is not None else 0,
            "zone_duration": (
                duration
                if heart_rate is not None
                and expanded_lower <= heart_rate <= expanded_upper
                else 0
            ),
            "bad_movement": 0 if item["continuous"] else 1,
            "relaxed_moving_duration": duration if item["relaxed_moving"] else 0,
            "relaxed_invalid": 1 if item["relaxed_invalid"] else 0,
            "gap": (
                1
                if index
                and abs(
                    (
                        item["start"] - intervals[index - 1]["end"]
                    ).total_seconds()
                )
                > 0.001
                else 0
            ),
            "relaxed_gap": (
                1
                if index
                and abs(
                    (item["start"] - intervals[index - 1]["end"]).total_seconds()
                ) > 15.0
                else 0
            ),
        }
        for key in keys:
            result[key].append(result[key][-1] + additions[key])
    return result


def extract(
    activity: dict[str, Any],
    assignment: dict[str, Any],
    zone_profile: dict[str, Any],
) -> dict[str, Any]:
    discipline = _discipline(
        activity.get("activity", {}).get("sport_profile_name")
    )
    if discipline not in ("Corrida", "Trail"):
        return {
            "status": "not_applicable",
            "reason": "discipline_not_supported",
            "segments": [],
        }
    zone_id = assignment.get("heart_rate_zone_id")
    segment_seconds = float(assignment.get("segment_seconds", SEGMENT_SECONDS))
    zone = next(
        (item for item in zone_profile.get("zones", []) if item["id"] == zone_id),
        None,
    )
    if zone is None:
        return {
            "status": "incomplete",
            "reason": "heart_rate_zone_not_configured",
            "segments": [],
        }
    intervals = _intervals(activity.get("records", []))
    zone_lower_bpm = int(zone["lower_bpm"])
    zone_upper_bpm = int(zone["upper_bpm"])
    prefixes = _build_prefixes(
        intervals, zone_lower_bpm, zone_upper_bpm
    )
    accepted = []
    rejected_counts: dict[str, int] = {}
    start_index = 0
    while start_index < len(intervals):
        target = prefixes["duration"][start_index] + segment_seconds
        end_index = bisect_left(
            prefixes["duration"], target, lo=start_index + 1
        )
        if end_index >= len(prefixes["duration"]):
            break
        elapsed = (
            prefixes["duration"][end_index]
            - prefixes["duration"][start_index]
        )
        if elapsed <= segment_seconds + 15.0:
            evaluated = _fast_candidate(
                intervals,
                prefixes,
                start_index,
                end_index,
                zone_lower_bpm,
                zone_upper_bpm,
                segment_seconds,
            )
            if evaluated["accepted"]:
                accepted.append(evaluated)
                start_index = end_index
                continue
            for reason in evaluated["rejection_reasons"]:
                rejected_counts[reason] = rejected_counts.get(reason, 0) + 1
        start_index += 1
    return {
        "status": "ready" if accepted else "no_qualifying_segments",
        "discipline": discipline,
        "zone_id": zone_id,
        "zone_profile_id": zone_profile["id"],
        "segments": accepted,
        "candidate_rejection_counts": rejected_counts,
        "protocol": {
            "duration_seconds": segment_seconds,
            "minimum_time_in_zone_share": MIN_TIME_IN_EXPANDED_ZONE,
            "heart_rate_tolerance_bpm": HEART_RATE_TOLERANCE_BPM,
            "minimum_slope_percent": MIN_SLOPE_PERCENT,
            "maximum_slope_percent": MAX_SLOPE_PERCENT,
            "overlap_allowed": False,
            "maximum_record_gap_seconds": 15,
            "minimum_moving_time_share": 0.95,
            "maximum_stopped_time_seconds": 10,
        },
    }
