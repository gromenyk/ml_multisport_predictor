"""Run every compatible metric implementation against normalized activities."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from datetime import datetime
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
APP_DIR = PROJECT_ROOT / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from backend.heart_rate_zones_store import profile_for_date
from backend.heart_rate_zones_store import load_zone_profiles
from metrics.measurement_store import replace_measurement_scope
from metrics.registry import METRIC_REGISTRY
from garmin.cycling_modality import classify_cycling_modality
from backend.activity_usage_store import load_activity_policies, permits


ACTIVITIES_DIR = PROJECT_ROOT / "garmin" / "activities"
METRIC_CATALOG_FILE = (
    PROJECT_ROOT / "data" / "objectives" / "metric_catalog.json"
)
EVALUATION_CACHE_FILE = (
    PROJECT_ROOT / "data" / "objectives" / "metric_evaluation_cache.json"
)


def _discipline(value: str) -> str:
    normalized = (value or "").strip().lower()
    if normalized in ("corrida", "carrera", "running"):
        return "Corrida"
    if normalized in ("trail", "trail running"):
        return "Trail"
    if normalized in ("bici", "mtb", "cycling", "ciclismo"):
        return "Bici"
    return value


def _load_catalog() -> dict[str, dict[str, Any]]:
    with METRIC_CATALOG_FILE.open("r", encoding="utf-8") as file:
        metrics = json.load(file).get("metrics", [])
    return {item["id"]: item for item in metrics}


def _activity_id(activity: dict[str, Any], path: Path) -> str:
    source = activity.get("activity", {})
    return (
        source.get("source_sha256")
        or hashlib.sha256(path.read_bytes()).hexdigest()
    )


def evaluate_activity_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        activity = json.load(file)
    metadata = activity.get("activity", {})
    activity_date = metadata.get("date")
    discipline = _discipline(
        metadata.get("sport_profile_name") or metadata.get("sport")
    )
    cycling_modality = metadata.get("cycling_modality") or classify_cycling_modality(metadata)
    activity_id = _activity_id(activity, path)
    catalog = _load_catalog()
    outcomes = []
    for metric_id, metric in catalog.items():
        implementation = METRIC_REGISTRY.get(metric_id)
        if implementation is None:
            outcomes.append(
                {"metric_id": metric_id, "status": "not_implemented"}
            )
            continue
        if not permits(path.name, "performance"):
            replace_measurement_scope(
                activity_id, metric_id, implementation["calculation_version"], []
            )
            outcomes.append({"metric_id": metric_id, "status": "excluded_by_activity_policy"})
            continue
        if discipline not in metric.get("disciplines", []):
            outcomes.append(
                {"metric_id": metric_id, "status": "discipline_not_supported"}
            )
            continue
        calculation_version = implementation["calculation_version"]
        zone_ids = metric.get("heart_rate_zone_ids") or (
            [metric["heart_rate_zone_id"]]
            if metric.get("heart_rate_zone_id")
            else []
        )
        zone_profile = profile_for_date(activity_date) if zone_ids else None
        if zone_ids and zone_profile is None:
            replace_measurement_scope(
                activity_id, metric_id, calculation_version, []
            )
            outcomes.append(
                {"metric_id": metric_id, "status": "zone_profile_missing"}
            )
            continue
        metric_configuration = {
            "metric_id": metric_id,
            "heart_rate_zone_id": zone_ids[0] if len(zone_ids) == 1 else None,
            "heart_rate_zone_ids": zone_ids,
        }
        extracted = implementation["extract"](
            activity, metric_configuration, zone_profile
        )
        calculated = implementation["calculate"](extracted)
        replacements = []
        for result in calculated:
            segment = result["segment"]
            result_zone_id = segment.get("heart_rate_zone_id") or (
                zone_ids[0] if len(zone_ids) == 1 else None
            )
            series_key = segment.get("slope_band", "")
            identity_key = ":".join(
                (
                    activity_id,
                    metric_id,
                    discipline,
                    result_zone_id or "",
                    series_key,
                    segment["start_time"],
                    f"v{result['calculation_version']}",
                )
            )
            replacements.append(
                {
                    "identity_key": identity_key,
                    "metric_id": metric_id,
                    "activity_id": activity_id,
                    "activity_file": path.name,
                    "date": activity_date,
                    "discipline": discipline,
                    "cycling_modality": cycling_modality,
                    "heart_rate_zone_id": result_zone_id,
                    "series_key": series_key or result_zone_id,
                    "zone_profile_id": (
                        zone_profile["id"] if zone_profile else None
                    ),
                    "value": result["value"],
                    "unit": result["unit"],
                    "status": result["status"],
                    "source": "FIT",
                    "calculation_version": result["calculation_version"],
                    "components": segment,
                }
            )
        measurement_ids = replace_measurement_scope(
            activity_id,
            metric_id,
            calculation_version,
            replacements,
        )
        outcomes.append(
            {
                "metric_id": metric_id,
                "status": extracted["status"],
                "measurements_saved": len(measurement_ids),
                "measurement_ids": measurement_ids,
                "rejections": extracted.get(
                    "candidate_rejection_counts", {}
                ),
            }
        )
    return {
        "activity_id": activity_id,
        "activity_file": path.name,
        "date": activity_date,
        "discipline": discipline,
        "metrics_evaluated": len(outcomes),
        "outcomes": outcomes,
    }


def _evaluation_signature() -> str:
    catalog = _load_catalog()
    implementations = {
        metric_id: {
            "calculation_version": value["calculation_version"],
            "protocol": value.get("protocol", {}),
            "catalog": catalog.get(metric_id, {}),
        }
        for metric_id, value in METRIC_REGISTRY.items()
    }
    payload = {
        "implementations": implementations,
        "heart_rate_zone_profiles": load_zone_profiles(),
        "activity_usage_policies": load_activity_policies(),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_evaluation_cache() -> dict[str, Any]:
    if not EVALUATION_CACHE_FILE.exists():
        return {"schema_version": 1, "files": {}}
    try:
        with EVALUATION_CACHE_FILE.open("r", encoding="utf-8") as file:
            payload = json.load(file)
        return payload if isinstance(payload.get("files"), dict) else {"schema_version": 1, "files": {}}
    except (OSError, json.JSONDecodeError):
        return {"schema_version": 1, "files": {}}


def _save_evaluation_cache(payload: dict[str, Any]) -> None:
    EVALUATION_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = EVALUATION_CACHE_FILE.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2, ensure_ascii=False)
        file.write("\n")
    temporary.replace(EVALUATION_CACHE_FILE)


def _file_fingerprint(path: Path) -> str:
    stat = path.stat()
    return f"{stat.st_size}:{stat.st_mtime_ns}"


def evaluate_all_activities(force: bool = False) -> dict[str, Any]:
    paths = sorted(ACTIVITIES_DIR.glob("activity_*.json"))
    cache = _load_evaluation_cache()
    signature = _evaluation_signature()
    cached_files = cache.get("files", {})
    results = []
    skipped = []
    updated_files = {}
    for path in paths:
        fingerprint = _file_fingerprint(path)
        cached = cached_files.get(path.name, {})
        if (
            not force
            and cached.get("fingerprint") == fingerprint
            and cached.get("evaluation_signature") == signature
        ):
            skipped.append(path.name)
            updated_files[path.name] = cached
            continue
        result = evaluate_activity_file(path)
        results.append(result)
        updated_files[path.name] = {
            "fingerprint": fingerprint,
            "evaluation_signature": signature,
            "activity_id": result.get("activity_id"),
            "date": result.get("date"),
            "evaluated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
    _save_evaluation_cache({
        "schema_version": 1,
        "evaluation_signature": signature,
        "files": updated_files,
    })
    return {
        "activities_discovered": len(paths),
        "activities_evaluated": len(results),
        "activities_skipped": len(skipped),
        "measurements_saved": sum(
            outcome.get("measurements_saved", 0)
            for result in results
            for outcome in result["outcomes"]
        ),
        "results": results,
    }
