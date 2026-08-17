"""Reuse the standardized flat-speed extractor with a fixed Z5 assignment."""

from metrics.speed_at_hr_zone.extractor import extract as extract_base


def extract(activity, assignment, zone_profile):
    return extract_base(
        activity,
        {**assignment, "heart_rate_zone_id": "Z5", "segment_seconds": 180.0},
        zone_profile,
    )
