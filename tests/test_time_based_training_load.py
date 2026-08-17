from training_load.training_load_calculator import (
    calculate_session_load,
    load_config,
)


def test_explicit_duration_is_the_load_basis():
    result = calculate_session_load(
        sport="Trail",
        terrain="Montaña",
        components=[{"duration_minutes": 60, "zone": "Z3"}],
        config=load_config(),
    )

    assert result["total_load"] == 117.0
    assert result["components"][0]["duration_source"] == "explicit_duration"


def test_distance_only_bike_is_converted_to_reference_duration():
    result = calculate_session_load(
        sport="Bici",
        terrain="Ondulado",
        components=[{"distance_km": 23, "zone": "Z2"}],
        config=load_config(),
    )

    component = result["components"][0]
    assert component["total_duration_minutes"] == 60.0
    assert result["total_load"] == 78.0
    assert component["duration_source"] == "reference_performance"


def test_terrain_does_not_double_count_heart_rate_load():
    config = load_config()
    flat = calculate_session_load(
        sport="Corrida",
        terrain="Plano",
        components=[{"duration_minutes": 30, "zone": "Z4"}],
        config=config,
    )
    mountain = calculate_session_load(
        sport="Trail",
        terrain="Montaña",
        components=[{"duration_minutes": 30, "zone": "Z4"}],
        config=config,
    )

    assert flat["total_load"] == mountain["total_load"]
