"""Classify cycling modality from Garmin's explicit FIT metadata."""

from __future__ import annotations


CYCLING_MODALITIES = ("Ruta", "MTB", "Gravel", "Indoor", "Desconocida")


def classify_cycling_modality(activity: dict) -> str | None:
    """Return a conservative modality; non-cycling activities return None."""
    sport = str(activity.get("sport") or "").strip().lower()
    profile = str(activity.get("sport_profile_name") or "").strip().lower()
    sub_sport = str(activity.get("sub_sport") or "").strip().lower()
    if sport not in {"cycling", "bici", "ciclismo"} and not any(
        token in profile for token in ("bici", "cycl", "cicl", "mtb", "mountain", "gravel")
    ):
        return None
    if sub_sport in {"virtual_activity", "indoor_cycling"} or any(
        token in profile for token in ("indoor", "virtual", "rodillo")
    ):
        return "Indoor"
    if any(token in profile or token in sub_sport for token in ("mtb", "mountain")):
        return "MTB"
    if "gravel" in profile or "gravel" in sub_sport:
        return "Gravel"
    if profile in {"bici", "road", "road cycling", "ciclismo de ruta", "ruta"} or sub_sport == "road":
        return "Ruta"
    return "Desconocida"
