"""
Shared training-load calculation engine.

This module contains the source-independent rules used to calculate training
load. It deliberately knows nothing about Garmin FIT files, prescribed plans,
model predictions, or Streamlit. Those sources will be converted into the
common session/component format accepted by ``calculate_session_load``.

The calculation follows the formula configured in
``config/training_load_factors.json``:

    global_scale * sum(
        duration_minutes * sport_factor * zone_factor * terrain_factor
    )

No intermediate value is rounded. This keeps the result reproducible and lets
the presentation layer decide how many decimal places to display.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = (
    PROJECT_ROOT / "config" / "training_load_factors.json"
)


class TrainingLoadError(ValueError):
    """Raised when a session or configuration cannot produce a valid load."""


def _require_positive_number(value: Any, name: str) -> float:
    """Return a finite positive float or raise a descriptive validation error."""
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
    ):
        raise TrainingLoadError(f"{name} must be a finite number greater than 0.")
    return float(value)


def _factor_value(config: dict, group: str, name: str) -> float:
    """Read one factor and report unknown canonical values explicitly."""
    factors = config["factors"][group]
    if name not in factors:
        raise TrainingLoadError(
            f"Unknown {group} value '{name}'. "
            f"Expected one of: {', '.join(factors)}."
        )
    return _require_positive_number(
        factors[name],
        f"factors.{group}.{name}",
    )


def _resolve_factor_reference(config: dict, reference: str) -> float:
    """Resolve references such as ``sport.Corrida`` used by constraints."""
    try:
        group, name = reference.split(".", 1)
    except ValueError as error:
        raise TrainingLoadError(
            f"Invalid factor reference '{reference}'."
        ) from error
    return _factor_value(config, group, name)


def validate_config(config: dict) -> None:
    """
    Validate the configuration before it is used for any calculation.

    The checks intentionally fail early. A misspelled terrain or an invalid
    optimized factor should never silently change a historical load.
    """
    required_sections = {
        "schema_version",
        "distance_unit",
        "factors",
        "reference_performance",
        "calculation_rules",
        "optimization",
    }
    missing = required_sections.difference(config)
    if missing:
        raise TrainingLoadError(
            f"Missing configuration sections: {', '.join(sorted(missing))}."
        )

    if config["distance_unit"] != "km":
        raise TrainingLoadError("The training-load distance unit must be 'km'.")

    factors = config["factors"]
    for group in ("sport", "zone", "terrain"):
        if group not in factors or not isinstance(factors[group], dict):
            raise TrainingLoadError(f"Missing factor group: {group}.")
        for name, value in factors[group].items():
            _require_positive_number(value, f"factors.{group}.{name}")

    _require_positive_number(
        factors.get("global_scale"),
        "factors.global_scale",
    )

    # Every active sport needs an explicit fallback for unstructured sessions
    # whose source plan or prediction does not provide terrain.
    terrain_defaults = config["calculation_rules"].get(
        "unstructured_session_default_terrain_by_sport",
        {},
    )
    for sport in set(factors["sport"]).difference({"Descanso"}):
        if sport not in terrain_defaults:
            raise TrainingLoadError(
                "Missing unstructured-session terrain default for "
                f"sport '{sport}'."
            )
        _factor_value(config, "terrain", terrain_defaults[sport])

    # Anchor values must agree with the corresponding initialization factor.
    anchors = config["optimization"].get("anchor_factors", {})
    for reference, anchor_value in anchors.items():
        actual_value = _resolve_factor_reference(config, reference)
        if not math.isclose(
            actual_value,
            float(anchor_value),
            rel_tol=0,
            abs_tol=1e-12,
        ):
            raise TrainingLoadError(
                f"Anchor {reference}={anchor_value} does not match "
                f"its configured factor {actual_value}."
            )

    constraints = config["optimization"].get(
        "factor_constraints",
        {},
    )

    # Monotonic sequences are validated in their declared order.
    for group, ordered_names in constraints.get("monotonic", {}).items():
        values = [_factor_value(config, group, name) for name in ordered_names]
        for left_name, right_name, left, right in zip(
            ordered_names,
            ordered_names[1:],
            values,
            values[1:],
        ):
            if right < left:
                raise TrainingLoadError(
                    f"Monotonic constraint violated: "
                    f"{group}.{right_name} ({right}) is lower than "
                    f"{group}.{left_name} ({left})."
                )

    # Equality and greater-than relationships keep initialization and future
    # optimized configurations subject to the same domain rules.
    for reference, relationship in constraints.get(
        "relationships",
        {},
    ).items():
        value = _resolve_factor_reference(config, reference)
        comparison = _resolve_factor_reference(
            config,
            relationship["reference"],
        )
        operator = relationship["operator"]

        if operator == "equal_to" and not math.isclose(
            value,
            comparison,
            rel_tol=0,
            abs_tol=1e-12,
        ):
            raise TrainingLoadError(
                f"Relationship violated: {reference} must equal "
                f"{relationship['reference']}."
            )
        if operator == "greater_than_or_equal_to" and value < comparison:
            raise TrainingLoadError(
                f"Relationship violated: {reference} must be greater than "
                f"or equal to {relationship['reference']}."
            )
        if operator not in {"equal_to", "greater_than_or_equal_to"}:
            raise TrainingLoadError(
                f"Unsupported relationship operator '{operator}'."
            )

    # Every running and cycling zone must have a reference conversion.
    zones = factors["zone"]
    references = config["reference_performance"]
    for table_name in (
        "running_pace_min_per_km",
        "cycling_speed_kmh",
        "kayak_speed_kmh",
    ):
        table = references.get(table_name, {})
        missing_zones = set(zones).difference(table)
        if missing_zones:
            raise TrainingLoadError(
                f"{table_name} is missing zones: "
                f"{', '.join(sorted(missing_zones))}."
            )
        for zone, value in table.items():
            _require_positive_number(
                value,
                f"reference_performance.{table_name}.{zone}",
            )


def load_config(config_path: Path | str = DEFAULT_CONFIG_PATH) -> dict:
    """Load and validate a UTF-8 training-load configuration file."""
    path = Path(config_path)
    if not path.exists():
        raise TrainingLoadError(f"Configuration file not found: {path}.")

    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise TrainingLoadError(
            f"Invalid JSON configuration at {path}: {error}."
        ) from error

    validate_config(config)
    return config


def duration_to_distance_km(
    sport: str,
    zone: str,
    duration_minutes: float,
    config: dict,
) -> float:
    """
    Convert a time-based structured block into its reference distance.

    Running and Trail use pace in minutes per kilometre. Cycling and Kayak use
    speed in kilometres per hour.
    """
    minutes = _require_positive_number(
        duration_minutes,
        "duration_minutes",
    )
    _factor_value(config, "zone", zone)
    references = config["reference_performance"]

    if sport in {"Corrida", "Trail"}:
        pace = _require_positive_number(
            references["running_pace_min_per_km"][zone],
            f"running pace for {zone}",
        )
        return minutes / pace

    if sport in {"Bici", "Kayak"}:
        table_name = (
            "cycling_speed_kmh"
            if sport == "Bici"
            else "kayak_speed_kmh"
        )
        speed = _require_positive_number(
            references[table_name][zone],
            f"{sport} speed for {zone}",
        )
        return minutes * speed / 60.0

    if sport == "Descanso":
        return 0.0

    raise TrainingLoadError(
        f"Duration-to-distance conversion is not defined for sport '{sport}'."
    )


def distance_to_duration_minutes(
    sport: str,
    zone: str,
    distance_km: float,
    config: dict,
) -> float:
    """Estimate duration when a planned component provides only distance."""
    distance = _require_positive_number(distance_km, "distance_km")
    _factor_value(config, "zone", zone)
    references = config["reference_performance"]
    if sport in {"Corrida", "Trail"}:
        return distance * _require_positive_number(
            references["running_pace_min_per_km"][zone],
            f"running pace for {zone}",
        )
    if sport in {"Bici", "Kayak"}:
        table_name = (
            "cycling_speed_kmh"
            if sport == "Bici"
            else "kayak_speed_kmh"
        )
        speed = _require_positive_number(
            references[table_name][zone],
            f"{sport} speed for {zone}",
        )
        return distance / speed * 60.0
    if sport == "Descanso":
        return 0.0
    raise TrainingLoadError(
        f"Distance-to-duration conversion is not defined for sport '{sport}'."
    )


def _component_zone(component: dict, config: dict) -> tuple[str, bool]:
    """
    Resolve the component zone.

    Recovery blocks without a zone use the configured default (currently Z2).
    Other blocks must provide their zone explicitly.
    """
    zone = component.get("zone")
    is_recovery = (
        component.get("is_recovery") is True
        or component.get("type") in {"recovery", "rest"}
    )

    if zone is None and is_recovery:
        zone = config["calculation_rules"]["default_recovery_zone"]
        return zone, True

    if zone is None:
        raise TrainingLoadError(
            "A non-recovery component must specify its zone."
        )

    return zone, False


def calculate_component_load(
    *,
    sport: str,
    terrain: str,
    component: dict,
    config: dict,
) -> dict:
    """
    Calculate one distance or time component and return its full audit trail.

    Duration is authoritative. Distance-only plans are converted to estimated
    minutes using the configured reference performance for the sport and zone.
    """
    zone, used_default_zone = _component_zone(component, config)
    sport_factor = _factor_value(config, "sport", sport)
    zone_factor = _factor_value(config, "zone", zone)
    terrain_factor = _factor_value(config, "terrain", terrain)
    global_scale = _require_positive_number(
        config["factors"]["global_scale"],
        "factors.global_scale",
    )

    repetitions = component.get("repetitions", 1)
    repetitions = _require_positive_number(repetitions, "repetitions")

    explicit_distance = component.get("distance_km")
    duration_minutes = component.get("duration_minutes")

    if duration_minutes is not None:
        duration_per_repetition = _require_positive_number(
            duration_minutes,
            "duration_minutes",
        )
        duration_source = "explicit_duration"
        distance_per_repetition = (
            _require_positive_number(explicit_distance, "distance_km")
            if explicit_distance is not None
            else duration_to_distance_km(
                sport,
                zone,
                duration_per_repetition,
                config,
            )
        )
    elif explicit_distance is not None:
        distance_per_repetition = _require_positive_number(
            explicit_distance,
            "distance_km",
        )
        duration_per_repetition = distance_to_duration_minutes(
            sport,
            zone,
            distance_per_repetition,
            config,
        )
        duration_source = "reference_performance"
    else:
        raise TrainingLoadError(
            "A component must contain duration_minutes or distance_km."
        )

    if explicit_distance is None and duration_minutes is not None:
        distance_per_repetition = duration_to_distance_km(
            sport,
            zone,
            duration_per_repetition,
            config,
        )

    total_distance = distance_per_repetition * repetitions
    total_duration = duration_per_repetition * repetitions
    base_load = (
        total_duration
        * sport_factor
        * zone_factor
        * terrain_factor
    )
    scaled_load = global_scale * base_load

    return {
        "zone": zone,
        "used_default_recovery_zone": used_default_zone,
        "duration_source": duration_source,
        "duration_per_repetition_minutes": duration_per_repetition,
        "total_duration_minutes": total_duration,
        "distance_per_repetition_km": distance_per_repetition,
        "repetitions": repetitions,
        "total_distance_km": total_distance,
        "sport_factor": sport_factor,
        "zone_factor": zone_factor,
        "terrain_factor": terrain_factor,
        "global_scale": global_scale,
        "base_load": base_load,
        "scaled_load": scaled_load,
    }


def calculate_session_load(
    *,
    sport: str,
    terrain: str | None,
    components: list[dict],
    structured: bool = False,
    config: dict | None = None,
    config_path: Path | str = DEFAULT_CONFIG_PATH,
) -> dict:
    """
    Calculate a complete session from normalized load components.

    The returned component details are intended to power future UI drill-downs
    and make every displayed number reproducible.
    """
    if config is None:
        config = load_config(config_path)
    else:
        validate_config(config)

    warnings = []

    # Rest always has zero load regardless of configured factor values.
    if sport == "Descanso":
        return {
            "sport": sport,
            "terrain": None,
            "structured": False,
            "base_load": 0.0,
            "global_scale": config["factors"]["global_scale"],
            "total_load": 0.0,
            "components": [],
            "warnings": (
                ["Components were ignored because rest load is always zero."]
                if components
                else []
            ),
        }

    _factor_value(config, "sport", sport)

    if terrain is None and structured:
        terrain = config["calculation_rules"][
            "structured_session_default_terrain"
        ]
        warnings.append(
            f"Structured session terrain defaulted to {terrain}."
        )
    if terrain is None:
        raise TrainingLoadError("A non-rest session must specify its terrain.")
    _factor_value(config, "terrain", terrain)

    if structured and sport == "Kayak":
        warnings.append(
            "Structured Kayak is anomalous. Only components with explicit "
            "distance and zone can be calculated."
        )

    if not isinstance(components, list) or not components:
        raise TrainingLoadError(
            "A non-rest session must contain at least one component."
        )

    calculated_components = []
    for index, component in enumerate(components):
        if not isinstance(component, dict):
            raise TrainingLoadError(
                f"Component {index} must be a JSON-like object."
            )
        try:
            calculated = calculate_component_load(
                sport=sport,
                terrain=terrain,
                component=component,
                config=config,
            )
        except TrainingLoadError as error:
            raise TrainingLoadError(
                f"Component {index} could not be calculated: {error}"
            ) from error
        calculated["component_index"] = index
        calculated_components.append(calculated)

    base_load = sum(item["base_load"] for item in calculated_components)
    total_load = sum(item["scaled_load"] for item in calculated_components)

    return {
        "sport": sport,
        "terrain": terrain,
        "structured": structured,
        "base_load": base_load,
        "global_scale": config["factors"]["global_scale"],
        "total_load": total_load,
        "components": calculated_components,
        "warnings": warnings,
    }
