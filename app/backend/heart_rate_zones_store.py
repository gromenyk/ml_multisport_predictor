"""Versioned persistence and lookup for personal heart-rate zones."""

from __future__ import annotations

from datetime import date, datetime
import json
from typing import Any, Optional, Union
from uuid import uuid4

from config import OBJECTIVES_DATA_DIR


HEART_RATE_ZONES_FILE = OBJECTIVES_DATA_DIR / "heart_rate_zone_profiles.json"
ZONE_IDS = ("Z1", "Z2", "Z3", "Z4", "Z5", "Z6")
ZONE_COLORS = {
    "Z1": "#60A5FA",
    "Z2": "#22C55E",
    "Z3": "#EAB308",
    "Z4": "#F97316",
    "Z5": "#EF4444",
    "Z6": "#A855F7",
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_zone_profiles() -> list[dict[str, Any]]:
    if not HEART_RATE_ZONES_FILE.exists():
        return []
    with HEART_RATE_ZONES_FILE.open("r", encoding="utf-8") as file:
        payload = json.load(file)
    profiles = payload.get("profiles", [])
    if not isinstance(profiles, list):
        raise ValueError("heart_rate_zone_profiles.json: 'profiles' must be a list.")
    return sorted(profiles, key=lambda item: item["effective_from"])


def _write(profiles: list[dict[str, Any]]) -> None:
    HEART_RATE_ZONES_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = HEART_RATE_ZONES_FILE.with_suffix(".json.tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump({"profiles": profiles}, file, indent=4, ensure_ascii=False)
        file.write("\n")
    temporary.replace(HEART_RATE_ZONES_FILE)


def validate_zones(zones: list[dict[str, Any]]) -> None:
    if [item.get("id") for item in zones] != list(ZONE_IDS):
        raise ValueError("El perfil debe contener Z1–Z6 en ese orden.")
    previous_upper = None
    for zone in zones:
        lower = int(zone["lower_bpm"])
        upper = int(zone["upper_bpm"])
        if lower >= upper:
            raise ValueError(f"{zone['id']}: el límite inferior debe ser menor.")
        if previous_upper is not None and lower != previous_upper + 1:
            raise ValueError(
                f"{zone['id']}: las zonas deben ser consecutivas, sin huecos "
                "ni superposiciones."
            )
        previous_upper = upper


def save_zone_profile(value: dict[str, Any]) -> str:
    zones = value["zones"]
    validate_zones(zones)
    effective_from = date.fromisoformat(value["effective_from"]).isoformat()
    profiles = load_zone_profiles()
    if any(item["effective_from"] == effective_from for item in profiles):
        raise ValueError(
            "Ya existe un perfil con esta fecha de vigencia. Elimínalo o usa otra fecha."
        )
    timestamp = _now()
    profile_id = f"hrzones_{uuid4().hex[:12]}"
    profiles.append(
        {
            "id": profile_id,
            "name": value.get("name", "").strip() or f"Perfil {effective_from}",
            "effective_from": effective_from,
            "zones": zones,
            "created_at": timestamp,
            "updated_at": timestamp,
        }
    )
    _write(sorted(profiles, key=lambda item: item["effective_from"]))
    return profile_id


def delete_zone_profile(profile_id: str) -> None:
    profiles = load_zone_profiles()
    retained = [item for item in profiles if item.get("id") != profile_id]
    if len(retained) == len(profiles):
        raise KeyError(f"Perfil desconocido: {profile_id}")
    _write(retained)


def profile_for_date(
    on_date: Union[date, str]
) -> Optional[dict[str, Any]]:
    target = date.fromisoformat(on_date) if isinstance(on_date, str) else on_date
    applicable = [
        item
        for item in load_zone_profiles()
        if date.fromisoformat(item["effective_from"]) <= target
    ]
    return applicable[-1] if applicable else None


def resolve_heart_rate_zone(
    on_date: Union[date, str], bpm: float
) -> Optional[str]:
    """Resolve bpm with the zone profile that was effective on the given date."""
    profile = profile_for_date(on_date)
    if profile is None:
        return None
    for zone in profile["zones"]:
        if zone["lower_bpm"] <= bpm <= zone["upper_bpm"]:
            return zone["id"]
    return None
