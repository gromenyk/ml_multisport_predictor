"""Extract MTB VAM segments."""

from metrics.vam_at_hr_zone.extractor import extract_with_protocol


def extract(activity, assignment, zone_profile):
    return extract_with_protocol(
        activity, assignment, zone_profile,
        required_modality="MTB", minimum_slope=5.0,
        maximum_slope=10.0, slope_label="5-10%", continuity_mode="cycling",
    )
