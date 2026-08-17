"""
Generic Garmin FIT extractor.

Creates a normalized JSON for software consumption, a concise human-readable
TXT report, and an auditable schema showing where every FIT field was stored.
All decoded messages are retained in raw_metrics so future calculations do not
require reading the FIT file again.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import re
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

from explore_fit import decode_fit
from make_readable import write_activity_text
from cycling_modality import classify_cycling_modality


GARMIN_DIR = Path(__file__).resolve().parent
DEFAULT_INPUT_DIR = GARMIN_DIR / "fit_files"
DEFAULT_OUTPUT_DIR = GARMIN_DIR / "activities"
SEMICIRCLES_TO_DEGREES = 180.0 / (2**31)


class SchemaAudit:
    """Track every decoded field and every normalized destination."""

    def __init__(self):
        self._entries: dict[tuple[str, str], dict] = {}

    def discover(self, messages: dict[str, list[dict]]):
        for message_type, rows in messages.items():
            for row in rows:
                for field in row:
                    key = (str(message_type), str(field))
                    entry = self._entries.setdefault(
                        key,
                        {
                            "message_type": str(message_type),
                            "field": str(field),
                            "occurrences": 0,
                            "destinations": set(),
                        },
                    )
                    entry["occurrences"] += 1

    def map(self, message_type: str, field: str, destination: str):
        key = (str(message_type), str(field))
        entry = self._entries.setdefault(
            key,
            {
                "message_type": str(message_type),
                "field": str(field),
                "occurrences": 0,
                "destinations": set(),
            },
        )
        entry["destinations"].add(destination)

    def finalize(self):
        for entry in self._entries.values():
            if not entry["destinations"]:
                entry["destinations"].add("raw_metrics")

    def entries(self) -> list[dict]:
        self.finalize()
        return [
            {
                **entry,
                "destinations": sorted(entry["destinations"]),
            }
            for entry in sorted(
                self._entries.values(),
                key=lambda item: (item["message_type"], item["field"]),
            )
        ]


def clean(value: Any) -> Any:
    """Make values strict-JSON compatible without silently losing special data."""
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return {"__type__": "float", "value": str(value)}
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    return clean(str(value))


def finite(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def first(rows: Iterable[dict], *names: str) -> tuple[Any, str | None]:
    for name in names:
        for row in rows:
            value = row.get(name)
            if value is not None and not (
                isinstance(value, float) and not math.isfinite(value)
            ):
                return value, name
    return None, None


def primary_session(messages: dict[str, list[dict]]) -> dict:
    sessions = messages.get("session_mesgs", [])
    if not sessions:
        return {}
    return max(
        sessions,
        key=lambda row: row.get("total_timer_time", 0)
        if finite(row.get("total_timer_time"))
        else 0,
    )


def file_date(path: Path, session: dict) -> str:
    stem = path.stem
    for pattern in ("%d-%m-%Y", "%Y-%m-%d", "%d_%m_%Y", "%Y_%m_%d"):
        try:
            return datetime.strptime(stem, pattern).date().isoformat()
        except ValueError:
            pass

    match = re.search(r"(\d{4})[-_](\d{2})[-_](\d{2})", stem)
    if match:
        try:
            return date(*map(int, match.groups())).isoformat()
        except ValueError:
            pass

    start_time = session.get("start_time")
    if isinstance(start_time, str):
        try:
            return datetime.fromisoformat(start_time).date().isoformat()
        except ValueError:
            pass

    raise ValueError(
        f"Cannot determine activity date from FIT filename '{path.name}'."
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def output_matches_fit(
    fit_path: Path,
    json_path: Path,
    text_path: Path,
) -> bool:
    """
    Return whether both derived files already represent this exact FIT.

    The source hash is near the beginning of the normalized JSON, so only a
    small prefix is read. This avoids loading activity files that may contain
    tens of megabytes of temporal records merely to decide whether to skip.
    """
    if not json_path.exists() or not text_path.exists():
        return False
    try:
        with json_path.open("r", encoding="utf-8") as file:
            prefix = file.read(16 * 1024)
    except OSError:
        return False

    hash_match = re.search(
        r'"source_sha256"\s*:\s*"([0-9a-fA-F]{64})"',
        prefix,
    )
    source_match = re.search(
        r'"source_file"\s*:\s*"([^"]+)"',
        prefix,
    )
    if not hash_match or not source_match:
        return False
    return (
        source_match.group(1) == fit_path.name
        and hash_match.group(1).lower() == sha256_file(fit_path).lower()
    )


def read_schema_entries(path: Path) -> dict[tuple[str, str], dict]:
    """Read the existing fixed-width schema table for incremental merging."""
    if not path.exists():
        return {}

    entries = {}
    in_table = False
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}

    for line in lines:
        if line.startswith("MESSAGE TYPE"):
            in_table = True
            continue
        if not in_table or not line or set(line) == {"-"}:
            continue
        if line == "AUDIT RESULT":
            break
        if len(line) < 79:
            continue

        message_type = line[:30].strip()
        field = line[31:69].strip()
        count_text = line[70:77].strip()
        destinations = [
            item.strip() for item in line[79:].split(",") if item.strip()
        ]
        if not message_type or not field or not count_text.isdigit():
            continue
        entries[(message_type, field)] = {
            "message_type": message_type,
            "field": field,
            "occurrences": int(count_text),
            "destinations": set(destinations),
            "source_files": set(),
        }
    return entries


def map_field(
    output: dict,
    output_name: str,
    source: dict,
    source_names: tuple[str, ...],
    audit: SchemaAudit,
    message_type: str,
    destination: str,
    transform=None,
):
    value, source_name = first([source], *source_names)
    if source_name is None:
        return
    output[output_name] = clean(transform(value) if transform else value)
    audit.map(message_type, source_name, destination)


def seconds_per_km(speed_mps: Any) -> float | None:
    return 1000.0 / speed_mps if finite(speed_mps) and speed_mps > 0 else None


def device_details(messages: dict[str, list[dict]], audit: SchemaAudit) -> dict:
    devices = messages.get("device_info_mesgs", [])
    if not devices:
        return {}
    device = next(
        (row for row in devices if row.get("device_index") == "creator"),
        devices[0],
    )
    result = {}
    for target, candidates in (
        ("manufacturer", ("manufacturer",)),
        ("product", ("garmin_product", "product")),
        ("serial_number", ("serial_number",)),
        ("software_version", ("software_version",)),
        ("battery_level_percent", ("battery_level",)),
        ("battery_status", ("battery_status",)),
    ):
        map_field(
            result,
            target,
            device,
            candidates,
            audit,
            "device_info_mesgs",
            "activity",
        )
    return result


def build_activity(
    path: Path,
    activity_date: str,
    session: dict,
    messages: dict[str, list[dict]],
    audit: SchemaAudit,
) -> dict:
    result = {
        "source_file": path.name,
        "source_sha256": sha256_file(path),
        "date": activity_date,
    }
    start_time = session.get("start_time")
    if start_time is not None:
        result["start_time"] = clean(start_time)
        audit.map("session_mesgs", "start_time", "activity")
    for target, sources in (
        ("sport", ("sport",)),
        ("sport_profile_name", ("sport_profile_name",)),
        ("sub_sport", ("sub_sport",)),
        ("duration_seconds", ("total_timer_time",)),
        ("elapsed_duration_seconds", ("total_elapsed_time",)),
        ("distance_meters", ("total_distance",)),
        ("calories_kcal", ("total_calories",)),
        ("ascent_meters", ("total_ascent",)),
        ("descent_meters", ("total_descent",)),
    ):
        map_field(
            result,
            target,
            session,
            sources,
            audit,
            "session_mesgs",
            "activity",
        )
    cycling_modality = classify_cycling_modality(result)
    if cycling_modality:
        result["cycling_modality"] = cycling_modality
    result["device"] = device_details(messages, audit)
    return result


def build_summary(session: dict, audit: SchemaAudit) -> dict:
    result = {}
    mappings = (
        ("average_speed_mps", ("enhanced_avg_speed", "avg_speed"), None),
        ("maximum_speed_mps", ("enhanced_max_speed", "max_speed"), None),
        (
            "average_pace_seconds_per_km",
            ("enhanced_avg_speed", "avg_speed"),
            seconds_per_km,
        ),
        (
            "fastest_pace_seconds_per_km",
            ("enhanced_max_speed", "max_speed"),
            seconds_per_km,
        ),
        ("average_heart_rate_bpm", ("avg_heart_rate",), None),
        ("maximum_heart_rate_bpm", ("max_heart_rate",), None),
        ("average_power_watts", ("avg_power",), None),
        ("maximum_power_watts", ("max_power",), None),
        ("normalized_power_watts", ("normalized_power",), None),
        ("average_cadence_rpm", ("avg_cadence",), None),
        ("maximum_cadence_rpm", ("max_cadence",), None),
    )
    for target, sources, transform in mappings:
        map_field(
            result,
            target,
            session,
            sources,
            audit,
            "session_mesgs",
            "summary",
            transform,
        )
    return result


def zone_summary(
    messages: dict[str, list[dict]],
    audit: SchemaAudit,
    zone_field: str,
    boundary_field: str,
) -> list[dict]:
    candidates = [
        row
        for row in messages.get("time_in_zone_mesgs", [])
        if row.get("reference_mesg") == "session" and row.get(zone_field)
    ]
    if not candidates:
        return []
    row = candidates[-1]
    audit.map("time_in_zone_mesgs", zone_field, "load")
    if boundary_field in row:
        audit.map("time_in_zone_mesgs", boundary_field, "load")
    times = row.get(zone_field, [])
    boundaries = row.get(boundary_field, [])
    return [
        {
            "zone_index": index,
            "seconds": clean(seconds),
            "lower_boundary": clean(boundaries[index - 1])
            if index > 0 and index - 1 < len(boundaries)
            else None,
            "upper_boundary": clean(boundaries[index])
            if index < len(boundaries)
            else None,
        }
        for index, seconds in enumerate(times)
    ]


def build_load(
    session: dict,
    messages: dict[str, list[dict]],
    audit: SchemaAudit,
) -> dict:
    result = {}
    for target, sources in (
        ("aerobic_training_effect", ("total_training_effect",)),
        (
            "anaerobic_training_effect",
            ("total_anaerobic_training_effect",),
        ),
        ("training_load", ("training_load_peak", "training_load")),
        ("recovery_time_seconds", ("recovery_time",)),
        ("epoc", ("epoc", "total_epoc")),
        ("training_stress_score", ("training_stress_score", "tss")),
        ("intensity_factor", ("intensity_factor", "if")),
    ):
        map_field(
            result,
            target,
            session,
            sources,
            audit,
            "session_mesgs",
            "load",
        )
    hr_zones = zone_summary(
        messages,
        audit,
        "time_in_hr_zone",
        "hr_zone_high_boundary",
    )
    power_zones = zone_summary(
        messages,
        audit,
        "time_in_power_zone",
        "power_zone_high_boundary",
    )
    if hr_zones:
        result["heart_rate_zones"] = hr_zones
    if power_zones:
        result["power_zones"] = power_zones
    return result


def known_metric(
    messages: dict[str, list[dict]],
    names: tuple[str, ...],
    audit: SchemaAudit,
    destination: str,
) -> Any:
    for message_type, rows in messages.items():
        value, field = first(rows, *names)
        if field is not None:
            audit.map(message_type, field, destination)
            return clean(value)
    return None


def build_physiology(
    session: dict,
    messages: dict[str, list[dict]],
    audit: SchemaAudit,
) -> dict:
    result = {}
    candidates = (
        ("vo2_max", ("vo2_max", "vo2max")),
        ("average_respiration_rate_brpm", ("enhanced_avg_respiration_rate", "avg_respiration_rate")),
        ("maximum_respiration_rate_brpm", ("enhanced_max_respiration_rate", "max_respiration_rate")),
        ("minimum_respiration_rate_brpm", ("enhanced_min_respiration_rate", "min_respiration_rate")),
        ("body_battery", ("body_battery",)),
        ("stress", ("stress_level", "stress")),
        ("endurance_score", ("endurance_score",)),
        ("hill_score", ("hill_score",)),
    )
    for target, names in candidates:
        session_value, session_field = first([session], *names)
        if session_field is not None:
            result[target] = clean(session_value)
            audit.map("session_mesgs", session_field, "physiology")
        else:
            value = known_metric(messages, names, audit, "physiology")
            if value is not None:
                result[target] = value

    hrv = messages.get("hrv_mesgs", [])
    if hrv:
        result["hrv_samples"] = clean(hrv)
        for row in hrv:
            for field in row:
                audit.map("hrv_mesgs", field, "physiology")
    return result


def build_environment(session: dict, audit: SchemaAudit) -> dict:
    result = {}
    for target, sources in (
        ("average_temperature_celsius", ("avg_temperature",)),
        ("maximum_temperature_celsius", ("max_temperature",)),
        ("minimum_temperature_celsius", ("min_temperature",)),
        ("average_humidity_percent", ("avg_humidity", "humidity")),
    ):
        map_field(
            result,
            target,
            session,
            sources,
            audit,
            "session_mesgs",
            "environment",
        )
    return result


def degrees(value: Any) -> float | None:
    return value * SEMICIRCLES_TO_DEGREES if finite(value) else None


def build_records(
    interpreted: dict[str, list[dict]],
    raw: dict[str, list[dict]],
    audit: SchemaAudit,
) -> list[dict]:
    records = interpreted.get("record_mesgs", [])
    raw_records = raw.get("record_mesgs", [])
    output = []
    aliases = {
        "timestamp": ("timestamp", None),
        "latitude": ("position_lat", degrees),
        "longitude": ("position_long", degrees),
        "altitude_meters": ("enhanced_altitude", None),
        "distance_meters": ("distance", None),
        "speed_mps": ("enhanced_speed", None),
        "heart_rate_bpm": ("heart_rate", None),
        "power_watts": ("power", None),
        "cadence_rpm": ("cadence", None),
        "temperature_celsius": ("temperature", None),
        "respiration_rate_brpm": ("enhanced_respiration_rate", None),
    }

    for index, row in enumerate(records):
        normalized = {}
        consumed = set()
        for target, (source, transform) in aliases.items():
            if source not in row:
                continue
            value = row[source]
            normalized[target] = clean(transform(value) if transform else value)
            audit.map("record_mesgs", source, "records")
            consumed.add(source)

        speed = row.get("enhanced_speed")
        pace = seconds_per_km(speed)
        if pace is not None:
            normalized["pace_seconds_per_km"] = pace

        additional = {
            field: clean(value)
            for field, value in row.items()
            if field not in consumed
        }
        if additional:
            normalized["additional_metrics"] = additional
            for field in additional:
                audit.map("record_mesgs", field, "records")

        if index < len(raw_records):
            normalized["raw_fit_fields"] = clean(raw_records[index])

        output.append(normalized)
    return output


def build_gps(records: list[dict], audit: SchemaAudit) -> dict:
    points = []
    for record in records:
        if "latitude" not in record or "longitude" not in record:
            continue
        point = {
            key: record[key]
            for key in (
                "timestamp",
                "latitude",
                "longitude",
                "altitude_meters",
            )
            if key in record
        }
        points.append(point)
    if points:
        audit.map("record_mesgs", "position_lat", "gps")
        audit.map("record_mesgs", "position_long", "gps")
        if any("altitude_meters" in point for point in points):
            audit.map("record_mesgs", "enhanced_altitude", "gps")
        if any("timestamp" in point for point in points):
            audit.map("record_mesgs", "timestamp", "gps")
    return {
        "coordinate_system": "WGS84 decimal degrees",
        "precision_policy": (
            "Coordinates are converted directly from FIT semicircles using "
            "180 / 2^31, without rounding. Original semicircle values remain "
            "available in records.raw_fit_fields and raw_metrics."
        ),
        "points": points,
    }


def normalize_fit(path: Path) -> tuple[dict, SchemaAudit]:
    interpreted, interpreted_errors, interpreted_order = decode_fit(
        path,
        interpreted=True,
    )
    raw, raw_errors, raw_order = decode_fit(path, interpreted=False)

    audit = SchemaAudit()
    audit.discover(interpreted)
    session = primary_session(interpreted)
    activity_date = file_date(path, session)
    records = build_records(interpreted, raw, audit)

    normalized = {
        "activity": build_activity(
            path,
            activity_date,
            session,
            interpreted,
            audit,
        ),
        "summary": build_summary(session, audit),
        "load": build_load(session, interpreted, audit),
        "physiology": build_physiology(session, interpreted, audit),
        "environment": build_environment(session, audit),
        "gps": build_gps(records, audit),
        "records": records,
        "raw_metrics": {
            "retention_policy": (
                "Every decoded FIT message is retained below in interpreted "
                "and raw form. When a field appears repeatedly, normalized "
                "sections use the primary session (the session with the longest "
                "timer duration); every occurrence remains in this archive."
            ),
            "decoder_errors": {
                "interpreted": clean(interpreted_errors),
                "raw": clean(raw_errors),
            },
            "interpreted_messages": clean(interpreted),
            "raw_messages": clean(raw),
            "interpreted_messages_in_file_order": clean(interpreted_order),
            "raw_messages_in_file_order": clean(raw_order),
        },
    }
    return normalized, audit


def schema_report(
    source_files: list[str],
    activity_files: list[str],
    entries: list[dict],
) -> str:
    lines = [
        "GARMIN FIT SCHEMA AUDIT",
        "=" * 110,
        f"Source FIT files : {', '.join(source_files)}",
        f"Normalized JSON  : {', '.join(activity_files)}",
        "",
        "POLICY",
        "-" * 110,
        "Every discovered interpreted field is listed below.",
        "A field may have multiple destinations (for example records and gps).",
        "Fields without a clear normalized destination are stored in raw_metrics.",
        "All fields, including categorized fields, are also retained in the complete raw_metrics archive.",
        "",
        f"{'MESSAGE TYPE':<30} {'FIELD':<38} {'COUNT':>7}  DESTINATION",
        "-" * 110,
    ]
    for entry in entries:
        destinations = ", ".join(entry["destinations"])
        lines.append(
            f"{entry['message_type']:<30} "
            f"{entry['field']:<38} "
            f"{entry['occurrences']:>7}  "
            f"{destinations}"
        )
    lines.extend(
        [
            "",
            "AUDIT RESULT",
            "-" * 110,
            f"Unique message/field pairs: {len(entries)}",
            "Unassigned fields           : 0",
        ]
    )
    return "\n".join(lines) + "\n"


def parse_args():
    parser = argparse.ArgumentParser(
        description="Normalize Garmin FIT files into JSON and TXT activities."
    )
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess every FIT and rebuild the schema from scratch.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = args.input.resolve()
    output_dir = args.output.resolve()
    iterator = input_dir.rglob("*") if args.recursive else input_dir.glob("*")
    fit_files = sorted(
        path
        for path in iterator
        if path.is_file() and path.suffix.lower() == ".fit"
    )

    if not fit_files:
        print(f"No FIT files found in: {input_dir}")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    schema_path = output_dir / "fit_schema.txt"
    output_paths = {
        fit_path: (
            output_dir / f"activity_{fit_path.stem}.json",
            output_dir / f"activity_{fit_path.stem}.txt",
        )
        for fit_path in fit_files
    }
    current_files = {
        fit_path
        for fit_path, (json_path, text_path) in output_paths.items()
        if output_matches_fit(fit_path, json_path, text_path)
    }

    # If a previously generated output is stale or incomplete, rebuild the
    # complete collection. This rare path keeps schema counts exact. Ordinary
    # new uploads remain incremental.
    stale_files = {
        fit_path
        for fit_path, (json_path, text_path) in output_paths.items()
        if fit_path not in current_files
        and (json_path.exists() or text_path.exists())
    }
    full_rebuild = args.force or bool(stale_files) or (
        bool(current_files) and not schema_path.exists()
    )
    if full_rebuild:
        files_to_process = set(fit_files)
        current_files = set()
        combined_schema: dict[tuple[str, str], dict] = {}
        reason = (
            "--force requested"
            if args.force
            else "stale outputs or missing schema detected"
        )
        print(f"Full rebuild: {reason}.")
    else:
        files_to_process = set(fit_files).difference(current_files)
        combined_schema = read_schema_entries(schema_path)

    activity_files = []
    failures = []
    processed_count = 0
    skipped_count = 0

    for fit_path in fit_files:
        json_path, text_path = output_paths[fit_path]
        activity_files.append(json_path.name)
        if fit_path not in files_to_process:
            skipped_count += 1
            continue

        print(f"Extracting {fit_path.name}...")
        try:
            normalized, audit = normalize_fit(fit_path)

            with json_path.open("w", encoding="utf-8") as file:
                json.dump(
                    normalized,
                    file,
                    indent=2,
                    ensure_ascii=False,
                    allow_nan=False,
                )
            write_activity_text(normalized, text_path)
            processed_count += 1

            for entry in audit.entries():
                key = (entry["message_type"], entry["field"])
                combined = combined_schema.setdefault(
                    key,
                    {
                        **entry,
                        "occurrences": 0,
                        "destinations": set(),
                        "source_files": set(),
                    },
                )
                combined["occurrences"] += entry["occurrences"]
                combined["destinations"].update(entry["destinations"])
                combined["source_files"].add(fit_path.name)

            print(f"  JSON: {json_path.name}")
            print(f"  TXT : {text_path.name}")
        except Exception as error:
            failures.append((fit_path.name, type(error).__name__, str(error)))
            print(f"  ERROR: {type(error).__name__}: {error}")

    entries = [
        {
            **entry,
            "destinations": sorted(entry["destinations"]),
            "source_files": sorted(entry["source_files"]),
        }
        for entry in sorted(
            combined_schema.values(),
            key=lambda item: (item["message_type"], item["field"]),
        )
    ]
    if processed_count or full_rebuild:
        schema_path.write_text(
            schema_report(
                [path.name for path in fit_files],
                activity_files,
                entries,
            ),
            encoding="utf-8",
        )
        print(f"  Schema: {schema_path.name}")
    else:
        print(f"  Schema unchanged: {schema_path.name}")

    print("")
    print(f"Processed: {processed_count}")
    print(f"Skipped  : {skipped_count}")
    print(f"Failed   : {len(failures)}")

    if failures:
        print("\nFailures:")
        for failure in failures:
            print(f"  {failure[0]}: {failure[1]}: {failure[2]}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
