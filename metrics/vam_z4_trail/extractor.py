from metrics.vam_at_hr_zone.extractor import extract_with_protocol


def extract(activity, assignment, zone_profile):
    return extract_with_protocol(
        activity, assignment, zone_profile, zone_ids=("Z4",),
        segment_seconds=600.0, minimum_slope=10.0, maximum_slope=15.0,
        slope_label="10-15%", required_disciplines=("Trail",),
    )
