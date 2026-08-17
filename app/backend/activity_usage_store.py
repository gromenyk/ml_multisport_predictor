"""Per-activity analytical inclusion policies."""

from __future__ import annotations

from datetime import datetime
import json
from typing import Any

from config import PROJECT_ROOT


POLICIES_FILE = PROJECT_ROOT / "data" / "athlete_state" / "activity_usage_policies.json"
PROFILE_RULES = {
    "complete": {
        "use_for_load": True, "use_for_volume": True, "use_for_zones": True,
        "use_for_performance": True, "use_for_segments_routes": True,
        "use_for_pauses": True, "use_for_memory": True, "use_for_compliance": True,
    },
    "load_only": {
        "use_for_load": True, "use_for_volume": True, "use_for_zones": True,
        "use_for_performance": False, "use_for_segments_routes": False,
        "use_for_pauses": False, "use_for_memory": False, "use_for_compliance": False,
    },
    "load_compliance": {
        "use_for_load": True, "use_for_volume": True, "use_for_zones": True,
        "use_for_performance": False, "use_for_segments_routes": False,
        "use_for_pauses": False, "use_for_memory": False, "use_for_compliance": True,
    },
    "exclude": {
        "use_for_load": False, "use_for_volume": False, "use_for_zones": False,
        "use_for_performance": False, "use_for_segments_routes": False,
        "use_for_pauses": False, "use_for_memory": False, "use_for_compliance": False,
    },
}


def load_activity_policies() -> dict[str, dict[str, Any]]:
    if not POLICIES_FILE.exists():
        return {}
    payload = json.loads(POLICIES_FILE.read_text(encoding="utf-8"))
    return payload.get("activities", {})


def policy_for(activity_file: str | None) -> dict[str, Any]:
    stored = load_activity_policies().get(activity_file or "", {})
    profile = stored.get("profile", "complete")
    return {**PROFILE_RULES["complete"], **PROFILE_RULES.get(profile, {}), **stored}


def permits(activity_file: str | None, purpose: str) -> bool:
    return bool(policy_for(activity_file).get(f"use_for_{purpose}", True))


def save_activity_policy(activity_file: str, value: dict[str, Any]) -> None:
    policies = load_activity_policies()
    profile = value.get("profile", "complete")
    rules = PROFILE_RULES.get(profile, {}) if profile != "custom" else {
        key: bool(value.get(key)) for key in PROFILE_RULES["complete"]
    }
    policies[activity_file] = {
        "profile": profile,
        **rules,
        "reason": (value.get("reason") or "").strip(),
        "notes": (value.get("notes") or "").strip(),
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    POLICIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = POLICIES_FILE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"activities": policies}, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(POLICIES_FILE)
