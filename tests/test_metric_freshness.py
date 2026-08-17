from datetime import date, timedelta

from metrics.freshness import classify_freshness, metric_freshness


def test_freshness_boundaries():
    expected = {
        None: "Sin medición", 0: "Reciente", 14: "Reciente",
        15: "Vigente", 21: "Vigente", 22: "Próxima a vencer",
        27: "Próxima a vencer", 28: "Evaluación pendiente",
        35: "Evaluación pendiente", 36: "Desactualizada",
    }
    assert {value: classify_freshness(value) for value in expected} == expected


def test_vam_has_exactly_two_series_and_target_date():
    metric = {
        "id": "metric_vam_at_standardized_heart_rate", "name_es": "VAM",
        "disciplines": ["Corrida", "Trail"],
        "heart_rate_zone_ids": ["Z2", "Z3"],
        "slope_bands": ["10-15%"],
        "series_dimensions": ["heart_rate_zone_id", "slope_band"],
        "separate_by_discipline": False,
    }
    as_of = date(2026, 7, 31)
    measured = as_of - timedelta(days=22)
    rows = metric_freshness([metric], [{
        "metric_id": metric["id"], "date": measured.isoformat(),
        "discipline": "Trail", "heart_rate_zone_id": "Z2",
        "components": {"slope_band": "10-15%"},
    }], as_of)
    assert len(rows) == 2
    observed = next(item for item in rows if item["observations"] == 1)
    assert observed["status"] == "Próxima a vencer"
    assert observed["target_date"] == (measured + timedelta(days=28)).isoformat()


def test_speed_series_are_separate_by_discipline():
    metric = {
        "id": "metric_speed_at_standardized_heart_rate", "name_es": "Speed",
        "disciplines": ["Corrida", "Trail"],
    }
    rows = metric_freshness([metric], [], date(2026, 7, 31))
    assert {item["series_label"] for item in rows} == {"Corrida", "Trail"}
    assert all(item["status"] == "Sin medición" for item in rows)
