"""
Calculate performed training load from normalized Garmin activity JSON files.

This module reads ``garmin/activities/activity_*.json``. It never modifies
those files and never rereads the original FIT binaries. For each activity it:

1. Maps Garmin's sport name to the project's canonical sport.
2. Infers actual terrain from Garmin distance/ascent/descent summaries.
3. Reconstructs distance travelled in each heart-rate zone from temporal
   records and cumulative distance.
4. Uses ``training_load_calculator`` to calculate an auditable session load.
5. Aggregates multiple activities by calendar date.

The generated file is derived data and can always be rebuilt after changing
factors or terrain thresholds.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Allow both ``python -m training_load.performed_load`` and the more convenient
# ``python training_load/performed_load.py`` from the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
APP_DIR = PROJECT_ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from garmin.cycling_modality import classify_cycling_modality
from backend.activity_usage_store import permits
from training_load.training_load_calculator import (
    DEFAULT_CONFIG_PATH,
    TrainingLoadError,
    calculate_session_load,
    load_config,
)


DEFAULT_ACTIVITIES_DIR = PROJECT_ROOT / "garmin" / "activities"
DEFAULT_OUTPUT_FILE = (
    PROJECT_ROOT
    / "data"
    / "derived"
    / "training_load"
    / "performed_load.json"
)
DEFAULT_CACHE_FILE = (
    PROJECT_ROOT / "data" / "derived" / "training_load" / "performed_load_activity_cache.json"
)


# Garmin names differ from the canonical names used by the planning pipeline.
# Profile names have priority because they can distinguish Trail from Corrida.
PROFILE_SPORT_MAP = {
    "Bici": "Bici",
    "Trail running": "Trail",
    "Corrida": "Corrida",
    "Kayak": "Kayak",
}

GARMIN_SPORT_MAP = {
    "cycling": "Bici",
    "running": "Corrida",
    "kayaking": "Kayak",
}


def _finite(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_sport(activity: dict) -> str:
    """Map a normalized Garmin activity to a canonical project sport."""
    profile = activity.get("sport_profile_name")
    if profile in PROFILE_SPORT_MAP:
        return PROFILE_SPORT_MAP[profile]

    garmin_sport = activity.get("sport")
    if garmin_sport in GARMIN_SPORT_MAP:
        return GARMIN_SPORT_MAP[garmin_sport]

    raise TrainingLoadError(
        f"Unknown Garmin sport/profile: sport={garmin_sport!r}, "
        f"profile={profile!r}."
    )


def infer_actual_terrain(
    activity: dict,
    sport: str,
    config: dict,
) -> dict:
    """
    Infer actual terrain exclusively from Garmin activity information.

    Garmin's already-processed ascent/descent summaries are preferred over
    resumming noisy point-by-point altitude changes.
    """
    terrain_config = config["actual_terrain_classification"]
    special_cases = terrain_config.get("special_cases", {})
    sub_sport = activity.get("sub_sport")

    if sport in special_cases:
        return {
            "terrain": special_cases[sport],
            "method": "sport_special_case",
            "reason": f"{sport} is configured as a special case.",
            "metrics": {},
        }

    if sub_sport in special_cases:
        return {
            "terrain": special_cases[sub_sport],
            "method": "sub_sport_special_case",
            "reason": f"{sub_sport} is configured as a special case.",
            "metrics": {},
        }

    distance_m = activity.get("distance_meters")
    ascent_m = activity.get("ascent_meters")
    descent_m = activity.get("descent_meters")

    if not all(_finite(value) for value in (distance_m, ascent_m, descent_m)):
        raise TrainingLoadError(
            "Terrain cannot be inferred because distance, ascent, or descent "
            "is missing."
        )
    if distance_m <= 0:
        raise TrainingLoadError(
            "Terrain cannot be inferred from a non-positive distance."
        )

    distance_km = distance_m / 1000.0
    ascent_per_km = ascent_m / distance_km
    descent_per_km = descent_m / distance_km
    variation_per_km = (ascent_m + descent_m) / distance_km
    elevation_total = ascent_m + descent_m
    ascent_share = ascent_m / elevation_total if elevation_total > 0 else 0.0

    metrics = {
        "distance_km": distance_km,
        "ascent_m": ascent_m,
        "descent_m": descent_m,
        "ascent_m_per_km": ascent_per_km,
        "descent_m_per_km": descent_per_km,
        "elevation_variation_m_per_km": variation_per_km,
        "ascent_share": ascent_share,
    }

    subida = terrain_config["subida_detection"]
    if (
        ascent_per_km >= subida["minimum_ascent_m_per_km"]
        and ascent_share >= subida["minimum_ascent_share"]
    ):
        return {
            "terrain": "Subida",
            "method": "elevation_summary",
            "reason": (
                "Ascent per kilometre and ascent share meet the configured "
                "Subida thresholds."
            ),
            "metrics": metrics,
        }

    thresholds = terrain_config["thresholds"]
    if variation_per_km <= thresholds["Plano"]["maximum_inclusive"]:
        terrain = "Plano"
    elif variation_per_km <= thresholds["Ondulado"]["maximum_inclusive"]:
        terrain = "Ondulado"
    else:
        terrain = "Montaña"

    return {
        "terrain": terrain,
        "method": "elevation_summary",
        "reason": (
            f"Elevation variation is {variation_per_km:.3f} m/km, classified "
            f"with configured {terrain} thresholds."
        ),
        "metrics": metrics,
    }


def _heart_rate_boundaries(load_section: dict) -> list[dict]:
    """Validate and return Garmin's ordered activity heart-rate-zone rows."""
    zones = load_section.get("heart_rate_zones")
    if not isinstance(zones, list) or not zones:
        raise TrainingLoadError(
            "The Garmin activity does not contain heart-rate-zone boundaries."
        )

    zones = sorted(zones, key=lambda row: row["zone_index"])
    if len(zones) < 2:
        raise TrainingLoadError(
            "At least two Garmin heart-rate-zone rows are required."
        )
    return zones


def _activity_zone_header(activity_path: Path) -> dict:
    """
    Read date and HR-zone boundaries without loading a large activity JSON.

    These fields occur before temporal records in extractor output, so a small
    prefix is sufficient for building fallbacks for sources such as Rouvy.
    """
    try:
        with activity_path.open("r", encoding="utf-8") as file:
            prefix = file.read(256 * 1024)
    except OSError:
        return {}

    date_match = re.search(
        r'"date"\s*:\s*"(\d{4}-\d{2}-\d{2})"',
        prefix,
    )
    marker = '"heart_rate_zones"'
    marker_index = prefix.find(marker)
    zones = None
    if marker_index >= 0:
        value_start = prefix.find(":", marker_index + len(marker))
        if value_start >= 0:
            try:
                zones, _ = json.JSONDecoder().raw_decode(
                    prefix[value_start + 1 :].lstrip()
                )
            except (json.JSONDecodeError, TypeError):
                zones = None

    return {
        "date": date_match.group(1) if date_match else None,
        "zones": zones if isinstance(zones, list) and zones else None,
    }


def _nearest_zone_fallbacks(
    activity_paths: list[Path],
) -> dict[Path, dict]:
    """Find the closest activity with Garmin HR-zone boundaries."""
    headers = {
        path: _activity_zone_header(path) for path in activity_paths
    }
    candidates = [
        (path, header)
        for path, header in headers.items()
        if header.get("date") and header.get("zones")
    ]
    fallbacks = {}

    for path, header in headers.items():
        if header.get("zones") or not header.get("date") or not candidates:
            continue
        activity_date = datetime.fromisoformat(header["date"]).date()
        source_path, source_header = min(
            candidates,
            key=lambda item: (
                abs(
                    (
                        datetime.fromisoformat(item[1]["date"]).date()
                        - activity_date
                    ).days
                ),
                item[0].name,
            ),
        )
        source_date = datetime.fromisoformat(source_header["date"]).date()
        zones = [
            {
                "zone_index": row.get("zone_index"),
                "seconds": None,
                "lower_boundary": row.get("lower_boundary"),
                "upper_boundary": row.get("upper_boundary"),
            }
            for row in source_header["zones"]
        ]
        fallbacks[path] = {
            "zones": zones,
            "source_activity_json": source_path.name,
            "source_date": source_header["date"],
            "distance_days": abs((source_date - activity_date).days),
        }
    return fallbacks


def _load_zone_for_heart_rate(
    heart_rate: float,
    garmin_zones: list[dict],
) -> tuple[str, str]:
    """
    Map observed heart rate to project load zones.

    Garmin zone index 0 means below Z1. Per the agreed rule, distance below Z1
    uses the Z1 load factor but remains separately auditable as ``below_z1``.
    """
    for row in garmin_zones:
        upper = row.get("upper_boundary")
        if upper is None or heart_rate < upper:
            index = int(row["zone_index"])
            if index == 0:
                return "Z1", "below_z1"
            return f"Z{index}", f"Z{index}"

    # A final open-ended Garmin zone should normally catch this branch.
    highest = int(garmin_zones[-1]["zone_index"])
    return f"Z{highest}", f"Z{highest}"


def calculate_distance_by_zone(
    records: list[dict],
    load_section: dict,
) -> dict:
    """
    Reconstruct travelled distance by observed heart-rate zone.

    ``distance_meters`` in FIT records is cumulative. Therefore each segment is
    the current value minus the previous value. Negative deltas are treated as
    resets and never subtracted from total distance.
    """
    garmin_zones = _heart_rate_boundaries(load_section)
    by_load_zone_m = defaultdict(float)
    by_observed_bucket_m = defaultdict(float)
    unclassified_m = 0.0
    reconstructed_m = 0.0
    negative_delta_count = 0
    large_delta_count = 0
    records_with_distance = 0
    segments_with_heart_rate = 0
    previous_distance = None

    for record in records:
        current_distance = record.get("distance_meters")
        if not _finite(current_distance):
            continue

        records_with_distance += 1
        if previous_distance is None:
            previous_distance = current_distance
            continue

        delta_m = current_distance - previous_distance
        previous_distance = current_distance

        if delta_m < 0:
            negative_delta_count += 1
            continue
        if delta_m == 0:
            continue

        # Large segments are preserved rather than silently discarded. They are
        # flagged so the result can be reviewed before optimization.
        if delta_m > 1000:
            large_delta_count += 1

        reconstructed_m += delta_m
        heart_rate = record.get("heart_rate_bpm")

        if not _finite(heart_rate):
            unclassified_m += delta_m
            continue

        load_zone, observed_bucket = _load_zone_for_heart_rate(
            heart_rate,
            garmin_zones,
        )
        by_load_zone_m[load_zone] += delta_m
        by_observed_bucket_m[observed_bucket] += delta_m
        segments_with_heart_rate += 1

    classified_m = sum(by_load_zone_m.values())
    coverage = classified_m / reconstructed_m if reconstructed_m > 0 else 0.0

    warnings = []
    if negative_delta_count:
        warnings.append(
            f"{negative_delta_count} negative cumulative-distance deltas were "
            "treated as counter resets."
        )
    if large_delta_count:
        warnings.append(
            f"{large_delta_count} distance increments exceeded 1 km and were "
            "preserved for audit."
        )
    if unclassified_m > 0:
        warnings.append(
            f"{unclassified_m / 1000.0:.6f} km had no heart-rate value and "
            "were excluded from calculated load."
        )

    return {
        "distance_by_load_zone_km": {
            zone: value / 1000.0
            for zone, value in sorted(by_load_zone_m.items())
        },
        "distance_by_observed_bucket_km": {
            zone: value / 1000.0
            for zone, value in sorted(by_observed_bucket_m.items())
        },
        "unclassified_distance_km": unclassified_m / 1000.0,
        "reconstructed_distance_km": reconstructed_m / 1000.0,
        "classified_distance_km": classified_m / 1000.0,
        "heart_rate_distance_coverage": coverage,
        "records_total": len(records),
        "records_with_distance": records_with_distance,
        "segments_with_heart_rate": segments_with_heart_rate,
        "negative_distance_delta_count": negative_delta_count,
        "large_distance_delta_count": large_delta_count,
        "garmin_zone_boundaries": garmin_zones,
        "warnings": warnings,
    }


def calculate_duration_by_zone(
    records: list[dict],
    load_section: dict,
) -> dict:
    """Return moving minutes per load zone, preferring Garmin FIT summaries."""
    garmin_zones = _heart_rate_boundaries(load_section)
    by_zone_seconds = defaultdict(float)
    if any(_finite(row.get("seconds")) for row in garmin_zones):
        for row in garmin_zones:
            seconds = row.get("seconds")
            if not _finite(seconds) or seconds <= 0:
                continue
            index = int(row["zone_index"])
            load_zone = "Z1" if index == 0 else f"Z{index}"
            by_zone_seconds[load_zone] += seconds
        method = "garmin_fit_zone_seconds"
    else:
        ordered = sorted(
            (row for row in records if row.get("timestamp")),
            key=lambda row: row["timestamp"],
        )
        for previous, current in zip(ordered, ordered[1:]):
            try:
                start = datetime.fromisoformat(
                    previous["timestamp"].replace("Z", "+00:00")
                )
                end = datetime.fromisoformat(
                    current["timestamp"].replace("Z", "+00:00")
                )
            except (TypeError, ValueError):
                continue
            seconds = (end - start).total_seconds()
            heart_rate = current.get("heart_rate_bpm")
            if not 0 < seconds <= 60 or not _finite(heart_rate):
                continue
            load_zone, _ = _load_zone_for_heart_rate(
                heart_rate, garmin_zones
            )
            by_zone_seconds[load_zone] += seconds
        method = "record_timestamps"
    return {
        "duration_by_load_zone_minutes": {
            zone: seconds / 60.0
            for zone, seconds in sorted(by_zone_seconds.items())
        },
        "classified_duration_minutes": sum(by_zone_seconds.values()) / 60.0,
        "duration_method": method,
    }


def calculate_performed_activity(
    activity_path: Path,
    config: dict,
    config_sha256: str,
    zone_fallback: dict | None = None,
) -> dict:
    """Calculate and fully document one performed Garmin activity."""
    source = json.loads(activity_path.read_text(encoding="utf-8"))
    activity = source["activity"]
    sport = canonical_sport(activity)
    cycling_modality = (
        activity.get("cycling_modality")
        or classify_cycling_modality(activity)
    )
    terrain = infer_actual_terrain(activity, sport, config)
    load_section = dict(source.get("load", {}))
    fallback_warning = None
    if not load_section.get("heart_rate_zones") and zone_fallback:
        load_section["heart_rate_zones"] = zone_fallback["zones"]
        fallback_warning = (
            "Heart-rate-zone boundaries were unavailable; used boundaries "
            f"from {zone_fallback['source_activity_json']} "
            f"({zone_fallback['distance_days']} day(s) away)."
        )
    zones = calculate_distance_by_zone(
        source.get("records", []),
        load_section,
    )
    zones.update(
        calculate_duration_by_zone(
            source.get("records", []),
            load_section,
        )
    )
    zones["zone_boundary_source"] = (
        {
            "method": "nearest_activity",
            **{
                key: zone_fallback[key]
                for key in (
                    "source_activity_json",
                    "source_date",
                    "distance_days",
                )
            },
        }
        if fallback_warning
        else {"method": "activity_fit"}
    )

    components = [
        {
            "duration_minutes": duration_minutes,
            "zone": zone,
        }
        for zone, duration_minutes in zones[
            "duration_by_load_zone_minutes"
        ].items()
        if duration_minutes > 0
    ]

    duration_estimation_warning = None
    if not components:
        components = [
            {"distance_km": distance_km, "zone": zone}
            for zone, distance_km in zones[
                "distance_by_load_zone_km"
            ].items()
            if distance_km > 0
        ]
        if components:
            duration_estimation_warning = (
                "No heart-rate-zone duration was available; duration was "
                "estimated from zone distance and reference performance."
            )
        else:
            raise TrainingLoadError(
                "No time or distance with heart-rate data is available for "
                "load calculation."
            )

    calculated = calculate_session_load(
        sport=sport,
        terrain=terrain["terrain"],
        components=components,
        structured=False,
        config=config,
    )

    garmin_distance_km = activity.get("distance_meters", 0) / 1000.0
    distance_difference_km = (
        zones["reconstructed_distance_km"] - garmin_distance_km
    )
    warnings = list(zones["warnings"])
    if fallback_warning:
        warnings.append(fallback_warning)
    if duration_estimation_warning:
        warnings.append(duration_estimation_warning)
    if abs(distance_difference_km) > max(0.1, garmin_distance_km * 0.01):
        warnings.append(
            "Reconstructed record distance differs from Garmin summary "
            f"distance by {distance_difference_km:.6f} km."
        )

    activity_date = activity.get("date")
    if not activity_date:
        raise TrainingLoadError("The normalized Garmin activity has no date.")

    quality_status = "complete" if not warnings else "complete_with_warnings"
    timer_seconds = activity.get("duration_seconds")
    elapsed_seconds = activity.get("elapsed_duration_seconds")
    pause_seconds = (
        max(0.0, float(elapsed_seconds) - float(timer_seconds))
        if _finite(elapsed_seconds) and _finite(timer_seconds) and elapsed_seconds > 0
        else None
    )
    pause_share = (
        pause_seconds / float(elapsed_seconds)
        if pause_seconds is not None else None
    )

    return {
        "activity_id": activity_path.stem.removeprefix("activity_"),
        "date": activity_date,
        "source": {
            "activity_json": activity_path.name,
            "activity_json_sha256": _sha256_file(activity_path),
            "fit_file": activity.get("source_file"),
            "fit_sha256": activity.get("source_sha256"),
            "configuration_sha256": config_sha256,
        },
        "activity": {
            "sport": sport,
            "cycling_modality": cycling_modality,
            "garmin_sport": activity.get("sport"),
            "garmin_profile": activity.get("sport_profile_name"),
            "distance_km": garmin_distance_km,
            "duration_seconds": activity.get("duration_seconds"),
            "elapsed_duration_seconds": elapsed_seconds,
            "paused_duration_seconds": pause_seconds,
            "paused_time_share": pause_share,
            "ascent_meters": activity.get("ascent_meters"),
            "descent_meters": activity.get("descent_meters"),
        },
        "terrain_inference": terrain,
        "zone_distribution": zones,
        "load": {
            "calculated_load": calculated["total_load"],
            "base_load": calculated["base_load"],
            "global_scale": calculated["global_scale"],
            "garmin_training_load": source.get("load", {}).get("training_load"),
            "components": calculated["components"],
        },
        "quality": {
            "status": quality_status,
            "warnings": warnings,
        },
    }


def aggregate_by_date(activities: list[dict]) -> dict:
    """Sum any number of performed activities on the same calendar date."""
    daily = {}
    grouped = defaultdict(list)
    for activity in activities:
        grouped[activity["date"]].append(activity)

    for activity_date, rows in sorted(grouped.items()):
        calculated_load = sum(
            row["load"]["calculated_load"] for row in rows
        )
        garmin_values = [
            row["load"]["garmin_training_load"]
            for row in rows
            if _finite(row["load"]["garmin_training_load"])
        ]
        warnings = [
            warning
            for row in rows
            for warning in row["quality"]["warnings"]
        ]

        daily[activity_date] = {
            "activity_count": len(rows),
            "activity_ids": [row["activity_id"] for row in rows],
            "calculated_load": calculated_load,
            "garmin_training_load": (
                sum(garmin_values) if garmin_values else None
            ),
            "quality_status": (
                "complete" if not warnings else "complete_with_warnings"
            ),
            "warning_count": len(warnings),
        }

    return daily


def calculate_all_performed_loads(
    activities_dir: Path = DEFAULT_ACTIVITIES_DIR,
    output_file: Path = DEFAULT_OUTPUT_FILE,
    config_path: Path = DEFAULT_CONFIG_PATH,
    cache_file: Path = DEFAULT_CACHE_FILE,
    force_rebuild: bool = False,
) -> dict:
    """Incrementally calculate normalized activities and rebuild the fast daily aggregate."""
    config = load_config(config_path)
    config_sha256 = _sha256_file(config_path)
    discovered_paths = sorted(activities_dir.glob("activity_*.json"))
    activity_paths = [path for path in discovered_paths if permits(path.name, "load")]

    if not activity_paths:
        raise TrainingLoadError(
            f"No normalized Garmin activities found in {activities_dir}."
        )

    calculation_signature = hashlib.sha256(
        (
            config_sha256
            + _sha256_file(Path(__file__))
            + _sha256_file(PROJECT_ROOT / "training_load" / "training_load_calculator.py")
        ).encode("utf-8")
    ).hexdigest()
    cache = {"entries": {}}
    if cache_file.exists() and not force_rebuild:
        try:
            loaded_cache = json.loads(cache_file.read_text(encoding="utf-8"))
            if loaded_cache.get("calculation_signature") == calculation_signature:
                cache = loaded_cache
        except (OSError, json.JSONDecodeError):
            pass
    cached_entries = cache.get("entries", {})
    updated_entries = {}
    results = []
    failures = []
    recalculated = reused = 0
    source_fingerprints = {
        path.name: f"{path.stat().st_size}:{path.stat().st_mtime_ns}"
        for path in activity_paths
    }
    inventory_unchanged = (
        set(cached_entries) == set(source_fingerprints)
        and all(
            cached_entries[name].get("source_fingerprint") == fingerprint
            for name, fingerprint in source_fingerprints.items()
        )
    )
    # The common case does not open any activity JSON: stat metadata is enough.
    zone_fallbacks = {} if inventory_unchanged and not force_rebuild else _nearest_zone_fallbacks(activity_paths)
    for path in activity_paths:
        cached = cached_entries.get(path.name, {})
        fallback = cached.get("fallback") if inventory_unchanged and not force_rebuild else zone_fallbacks.get(path)
        fingerprint = hashlib.sha256(
            (
                source_fingerprints[path.name]
                + json.dumps(fallback, sort_keys=True, ensure_ascii=False)
            ).encode("utf-8")
        ).hexdigest()
        if not force_rebuild and cached.get("fingerprint") == fingerprint and cached.get("failure"):
            failures.append(cached["failure"])
            updated_entries[path.name] = cached
            reused += 1
            continue
        if not force_rebuild and cached.get("fingerprint") == fingerprint and cached.get("result"):
            results.append(cached["result"])
            updated_entries[path.name] = cached
            reused += 1
            continue
        try:
            result = calculate_performed_activity(
                path, config, config_sha256, fallback,
            )
            results.append(result)
            updated_entries[path.name] = {
                "source_fingerprint": source_fingerprints[path.name],
                "fingerprint": fingerprint,
                "fallback": fallback,
                "result": result,
            }
            recalculated += 1
        except Exception as error:
            failure = {
                "activity_json": path.name,
                "error_type": type(error).__name__,
                "error": str(error),
            }
            failures.append(failure)
            updated_entries[path.name] = {
                "source_fingerprint": source_fingerprints[path.name],
                "fingerprint": fingerprint,
                "fallback": fallback,
                "failure": failure,
            }

    output = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "configuration": {
            "file": str(config_path),
            "sha256": config_sha256,
        },
        "summary": {
            "activities_discovered": len(discovered_paths),
            "activities_excluded": len(discovered_paths) - len(activity_paths),
            "activities_calculated": len(results),
            "activities_recalculated": recalculated,
            "activities_reused": reused,
            "activities_failed": len(failures),
            "dates_calculated": len({row["date"] for row in results}),
        },
        "daily_loads": aggregate_by_date(results),
        "activities": results,
        "failures": failures,
    }

    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(
        json.dumps(
            output,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        ),
        encoding="utf-8",
    )
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    temporary_cache = cache_file.with_suffix(".tmp")
    temporary_cache.write_text(
        json.dumps({
            "schema_version": 1,
            "calculation_signature": calculation_signature,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "entries": updated_entries,
        }, indent=2, ensure_ascii=False, allow_nan=False),
        encoding="utf-8",
    )
    temporary_cache.replace(cache_file)
    return output


def parse_args():
    parser = argparse.ArgumentParser(
        description="Calculate performed training load from Garmin activities."
    )
    parser.add_argument(
        "--activities-dir",
        type=Path,
        default=DEFAULT_ACTIVITIES_DIR,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_FILE,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Ignore the per-activity cache and rebuild every performed activity.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = calculate_all_performed_loads(
            activities_dir=args.activities_dir,
            output_file=args.output,
            config_path=args.config,
            force_rebuild=args.force,
        )
    except Exception as error:
        print(f"ERROR: {type(error).__name__}: {error}")
        return 1

    summary = result["summary"]
    print("PERFORMED TRAINING LOAD")
    print("=" * 60)
    print(f"Activities discovered : {summary['activities_discovered']}")
    print(f"Activities calculated : {summary['activities_calculated']}")
    print(f"Activities recalculated: {summary['activities_recalculated']}")
    print(f"Activities reused      : {summary['activities_reused']}")
    print(f"Activities failed     : {summary['activities_failed']}")
    print(f"Dates calculated      : {summary['dates_calculated']}")
    print(f"Output                : {args.output}")

    if result["failures"]:
        print("\nFAILURES")
        print("-" * 60)
        for failure in result["failures"]:
            print(
                f"{failure['activity_json']}: "
                f"{failure['error_type']}: {failure['error']}"
            )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
