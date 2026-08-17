"""Find non-overlapping uphill segments for the Z2 and Z3 VAM series."""

from __future__ import annotations

from bisect import bisect_left
from datetime import datetime
from math import isfinite
from typing import Any

from garmin.cycling_modality import classify_cycling_modality
from metrics.vam_at_hr_zone.protocol import (
    HEART_RATE_TOLERANCE_BPM, MAX_RECORD_GAP_SECONDS, MAX_SLOPE_PERCENT,
    MIN_SLOPE_PERCENT, MIN_TIME_IN_EXPANDED_ZONE, SEGMENT_SECONDS,
    SLOPE_BANDS, SLOPE_EPSILON, ZONE_IDS,
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


def _slope_band(slope: float, minimum: float, maximum: float, label: str) -> str | None:
    if minimum - SLOPE_EPSILON <= slope <= maximum + SLOPE_EPSILON:
        return label
    return None


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
            value
            for value in (
                previous.get("heart_rate_bpm"),
                current.get("heart_rate_bpm"),
            )
            if _finite(value)
        ]
        start_altitude = previous.get("altitude_meters")
        end_altitude = current.get("altitude_meters")
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
                "start_altitude_meters": start_altitude if _finite(start_altitude) else None,
                "end_altitude_meters": end_altitude if _finite(end_altitude) else None,
                "continuous": (
                    0 < duration <= MAX_RECORD_GAP_SECONDS
                    and distance > 0
                    and _finite(speed)
                    and speed > 0
                ),
                "cycling_moving": (
                    0 < duration <= 15.0
                    and distance > 0
                    and _finite(speed)
                    and speed > 0
                ),
                "cycling_invalid": (
                    duration <= 0
                    or duration > 15.0
                    or distance < 0
                    or not _finite(speed)
                ),
            }
        )
    return prepared


def _prefixes(intervals, zones):
    result = {
        key: [0.0]
        for key in (
            "duration", "distance", "weighted_hr", "hr_duration",
            "bad_movement", "gap", "cycling_moving_duration",
            "cycling_invalid", "cycling_gap",
        )
    }
    for zone_id in zones:
        result[f"zone_duration_{zone_id}"] = [0.0]
    for index, item in enumerate(intervals):
        duration = item["duration_seconds"]
        heart_rate = item["heart_rate_bpm"]
        additions = {
            "duration": duration,
            "distance": item["distance_meters"],
            "weighted_hr": heart_rate * duration if heart_rate is not None else 0,
            "hr_duration": duration if heart_rate is not None else 0,
            "bad_movement": 0 if item["continuous"] else 1,
            "cycling_moving_duration": duration if item["cycling_moving"] else 0,
            "cycling_invalid": 1 if item["cycling_invalid"] else 0,
            "gap": (
                1
                if index
                and abs((item["start"] - intervals[index - 1]["end"]).total_seconds()) > 0.001
                else 0
            ),
            "cycling_gap": (
                1
                if index
                and abs((item["start"] - intervals[index - 1]["end"]).total_seconds()) > 15.0
                else 0
            ),
        }
        for zone_id, zone in zones.items():
            lower = zone["lower_bpm"] - HEART_RATE_TOLERANCE_BPM
            upper = zone["upper_bpm"] + HEART_RATE_TOLERANCE_BPM
            additions[f"zone_duration_{zone_id}"] = (
                duration
                if heart_rate is not None and lower <= heart_rate <= upper
                else 0
            )
        for key, addition in additions.items():
            result[key].append(result[key][-1] + addition)
    return result


def _candidate(
    intervals, prefixes, start, end, zones, minimum_slope, maximum_slope,
    slope_label, continuity_mode="strict", zone_ids=ZONE_IDS,
    segment_seconds=SEGMENT_SECONDS,
):
    last_index = end - 1
    before_last = prefixes["duration"][last_index] - prefixes["duration"][start]
    last = intervals[last_index]
    used = segment_seconds - before_last
    fraction = used / last["duration_seconds"]

    def total(key, last_value):
        return prefixes[key][last_index] - prefixes[key][start] + last_value * fraction

    heart_rate = last["heart_rate_bpm"]
    distance = total("distance", last["distance_meters"])
    weighted_hr = total(
        "weighted_hr",
        heart_rate * last["duration_seconds"] if heart_rate is not None else 0,
    )
    hr_duration = total(
        "hr_duration", last["duration_seconds"] if heart_rate is not None else 0
    )
    mean_hr = weighted_hr / hr_duration if hr_duration else None
    start_altitude = intervals[start]["start_altitude_meters"]
    last_start_altitude = last["start_altitude_meters"]
    last_end_altitude = last["end_altitude_meters"]
    end_altitude = (
        last_start_altitude + (last_end_altitude - last_start_altitude) * fraction
        if last_start_altitude is not None and last_end_altitude is not None
        else None
    )
    elevation_gain = (
        end_altitude - start_altitude
        if start_altitude is not None and end_altitude is not None
        else None
    )
    slope = (
        100.0 * elevation_gain / distance
        if distance > 0 and elevation_gain is not None
        else None
    )
    band = (
        _slope_band(slope, minimum_slope, maximum_slope, slope_label)
        if slope is not None else None
    )
    bad_movement = prefixes["bad_movement"][end] - prefixes["bad_movement"][start]
    bad_gaps = prefixes["gap"][end] - prefixes["gap"][start + 1]
    moving_seconds = total(
        "cycling_moving_duration",
        last["duration_seconds"] if last["cycling_moving"] else 0,
    )
    stopped_seconds = max(0.0, segment_seconds - moving_seconds)
    moving_share = moving_seconds / segment_seconds
    cycling_invalid = prefixes["cycling_invalid"][end] - prefixes["cycling_invalid"][start]
    cycling_gaps = prefixes["cycling_gap"][end] - prefixes["cycling_gap"][start + 1]
    common_reasons = []
    if continuity_mode in ("cycling", "relaxed"):
        if cycling_invalid or cycling_gaps or moving_share < 0.95 or stopped_seconds > 10.0:
            common_reasons.append("movement_not_continuous")
    elif bad_movement or bad_gaps:
        common_reasons.append("movement_not_continuous")
    if band is None:
        common_reasons.append("mean_slope_outside_range")

    matched_zone = None
    zone_share = 0.0
    for zone_id in zone_ids:
        zone = zones[zone_id]
        last_in_zone = (
            last["duration_seconds"]
            if heart_rate is not None
            and zone["lower_bpm"] - HEART_RATE_TOLERANCE_BPM
            <= heart_rate
            <= zone["upper_bpm"] + HEART_RATE_TOLERANCE_BPM
            else 0
        )
        duration_in_zone = total(f"zone_duration_{zone_id}", last_in_zone)
        share = duration_in_zone / segment_seconds
        if (
            mean_hr is not None
            and zone["lower_bpm"] <= mean_hr <= zone["upper_bpm"]
            and share >= MIN_TIME_IN_EXPANDED_ZONE
        ):
            matched_zone = zone_id
            zone_share = share
            break
    reasons = list(common_reasons)
    if mean_hr is None or not any(
        zone["lower_bpm"] <= mean_hr <= zone["upper_bpm"]
        for zone in zones.values()
    ):
        reasons.append("mean_hr_outside_z2_z3")
    elif matched_zone is None:
        reasons.append("insufficient_time_in_zone")
    end_time = last["start"] + (last["end"] - last["start"]) * fraction
    return {
        "accepted": not reasons,
        "rejection_reasons": reasons,
        "start_time": intervals[start]["start"].isoformat(),
        "end_time": end_time.isoformat(),
        "duration_seconds": segment_seconds,
        "distance_meters": distance,
        "heart_rate_zone_id": matched_zone,
        "mean_heart_rate_bpm": mean_hr,
        "time_in_zone_seconds": zone_share * segment_seconds,
        "time_in_zone_share": zone_share,
        "moving_time_share": moving_share,
        "stopped_time_seconds": stopped_seconds,
        "mean_slope_percent": slope,
        "slope_band": band,
        "net_elevation_gain_meters": elevation_gain,
    }


def extract_with_protocol(
    activity, assignment, zone_profile, *, required_modality=None,
    minimum_slope=MIN_SLOPE_PERCENT, maximum_slope=MAX_SLOPE_PERCENT,
    slope_label="10-15%", continuity_mode="relaxed", zone_ids=ZONE_IDS,
    segment_seconds=SEGMENT_SECONDS, required_disciplines=None,
):
    metadata = activity.get("activity", {})
    discipline = _discipline(
        metadata.get("sport_profile_name") or metadata.get("sport")
    )
    modality = metadata.get("cycling_modality") or classify_cycling_modality(metadata)
    if required_modality:
        if str(metadata.get("sport") or "").lower() != "cycling" or modality != required_modality:
            return {"status": "not_applicable", "reason": "cycling_modality_not_supported", "segments": []}
        discipline = "Bici"
    elif discipline not in (required_disciplines or ("Corrida", "Trail")):
        return {"status": "not_applicable", "reason": "discipline_not_supported", "segments": []}
    configured = {item["id"]: item for item in zone_profile.get("zones", [])}
    missing = [zone_id for zone_id in zone_ids if zone_id not in configured]
    if missing:
        return {
            "status": "incomplete",
            "reason": "heart_rate_zones_not_configured",
            "missing_zones": missing,
            "segments": [],
        }
    zones = {
        zone_id: {
            "lower_bpm": int(configured[zone_id]["lower_bpm"]),
            "upper_bpm": int(configured[zone_id]["upper_bpm"]),
        }
        for zone_id in zone_ids
    }
    intervals = _intervals(activity.get("records", []))
    prefixes = _prefixes(intervals, zones)
    accepted = []
    rejected_counts = {}
    start_index = 0
    while start_index < len(intervals):
        target = prefixes["duration"][start_index] + segment_seconds
        end_index = bisect_left(prefixes["duration"], target, lo=start_index + 1)
        if end_index >= len(prefixes["duration"]):
            break
        elapsed = prefixes["duration"][end_index] - prefixes["duration"][start_index]
        allowed_overshoot = (
            15.0 if continuity_mode in ("cycling", "relaxed")
            else MAX_RECORD_GAP_SECONDS
        )
        if elapsed <= segment_seconds + allowed_overshoot:
            evaluated = _candidate(
                intervals, prefixes, start_index, end_index, zones,
                minimum_slope, maximum_slope, slope_label, continuity_mode,
                zone_ids, segment_seconds,
            )
            if evaluated["accepted"]:
                accepted.append(evaluated)
                start_index = end_index
                continue
            for reason in evaluated["rejection_reasons"]:
                rejected_counts[reason] = rejected_counts.get(reason, 0) + 1
        start_index += 1
    for segment in accepted:
        segment["cycling_modality"] = modality
    return {
        "status": "ready" if accepted else "no_qualifying_segments",
        "discipline": discipline,
        "zone_profile_id": zone_profile["id"],
        "segments": accepted,
        "candidate_rejection_counts": rejected_counts,
        "protocol": {
            "duration_seconds": segment_seconds,
            "heart_rate_zone_ids": list(zone_ids),
            "minimum_time_in_zone_share": MIN_TIME_IN_EXPANDED_ZONE,
            "heart_rate_tolerance_bpm": HEART_RATE_TOLERANCE_BPM,
            "minimum_slope_percent": minimum_slope,
            "maximum_slope_percent": maximum_slope,
            "slope_bands": [slope_label],
            "overlap_allowed": False,
            "gait_classification_used": False,
            "cycling_modality": required_modality,
            "continuity_mode": continuity_mode,
            "maximum_record_gap_seconds": 15 if continuity_mode in ("cycling", "relaxed") else MAX_RECORD_GAP_SECONDS,
            "minimum_moving_time_share": 0.95 if continuity_mode in ("cycling", "relaxed") else 1.0,
            "maximum_stopped_time_seconds": 10 if continuity_mode in ("cycling", "relaxed") else 0,
        },
    }


def extract(activity, assignment, zone_profile):
    return extract_with_protocol(activity, assignment, zone_profile)
