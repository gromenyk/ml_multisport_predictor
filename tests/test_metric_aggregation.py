from metrics.aggregation import activity_medians


def test_activity_medians_keep_one_point_per_activity():
    rows = [
        {"activity_id": "a", "date": "2026-01-01", "value": 10, "activity_file": "a.fit"},
        {"activity_id": "a", "date": "2026-01-01", "value": 100, "activity_file": "a.fit"},
        {"activity_id": "a", "date": "2026-01-01", "value": 12, "activity_file": "a.fit"},
        {"activity_id": "b", "date": "2026-01-02", "value": 20, "activity_file": "b.fit"},
    ]
    result = activity_medians(rows)
    assert len(result) == 2
    assert result[0]["value"] == 12
    assert result[0]["segment_count"] == 3
    assert result[1]["value"] == 20
