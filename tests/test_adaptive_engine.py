from copy import deepcopy

from adaptive_planning.engine import (
    _redistribute_consecutive_intensity,
    build_adaptive_proposal,
)


def _prediction():
    return {"sessions": [
        {"date": "2026-08-03", "weekday": "lun", "sport": "Descanso", "distance_km": None, "blocks": [{"type": "rest"}]},
        {"date": "2026-08-04", "weekday": "mar", "sport": "Trail", "session_type": "Leve", "zone": "Z2", "terrain": "Ondulado", "distance_km": 10, "blocks": [{"type": "steady", "zone": "Z2", "distance_km": 10}]},
        {"date": "2026-08-05", "weekday": "mié", "sport": "Bici", "session_type": "Umbral", "zone": "Z4", "terrain": "Ondulado", "distance_km": 20, "blocks": [{"type": "steady", "zone": "Z4", "distance_km": 20}]},
    ]}


def _state(score=60, acute=400, chronic=600, confidence="alta"):
    return {
        "interpretation": {"readiness_score": score, "readiness_label": "normal", "confidence": confidence,
                           "positive_factors": [], "limiting_factors": []},
        "training": {"acute_load_7d": acute, "chronic_load_42d_equivalent": chronic,
                     "load_balance": chronic - acute},
        "recovery": {"latest": {"has_discomfort": False}},
        "measurement_freshness": [],
        "context": {
            "availability_next_14d": [
                {"date": "2026-08-03", "status": "free", "max_minutes": None},
                {"date": "2026-08-04", "status": "free", "max_minutes": None},
                {"date": "2026-08-05", "status": "free", "max_minutes": None},
            ],
            "active_windows": [{"assignments": [{"development_goal_id": "goal", "priority": "Primary",
                                                    "metrics": [{"metric_id": "metric"}]}]}],
        },
    }


METRICS = [{"id": "metric", "disciplines": ["Trail"]}]


def test_progression_selects_and_applies_a_bounded_weekly_dose():
    proposal = build_adaptive_proposal(_prediction(), _state(), {"goal": "Eficiencia"}, METRICS)
    assert proposal["decision"] == "PROGRESIÓN AERÓBICA SIGNIFICATIVA"
    assert len(proposal["changes"]) == 1
    assert proposal["changes"][0]["before"]["distance_km"] == 10
    assert proposal["changes"][0]["after"]["distance_km"] == 11.5
    assert proposal["changes"][0]["rule_id"] == "P1"
    assert proposal["rule_evaluations"][-1]["triggered"] is True
    assert proposal["selected_dose_percent"] == 15
    assert {item["target_change_percent"] for item in proposal["optimization_candidates"]} == {0, 5, 10, 15, 20}


def test_low_recovery_reduces_easy_and_intense_distance_differently():
    proposal = build_adaptive_proposal(_prediction(), _state(score=30), metric_catalog=METRICS)
    assert proposal["decision"] == "REDUCCIÓN PREVENTIVA"
    by_date = {item["date"]: item for item in proposal["adapted_sessions"]}
    assert by_date["2026-08-04"]["distance_km"] == 9.0
    assert by_date["2026-08-05"]["distance_km"] == 17.0


def test_overload_reduces_volume_when_recovery_is_not_low():
    proposal = build_adaptive_proposal(_prediction(), _state(score=60, acute=700, chronic=500), metric_catalog=METRICS)
    assert proposal["decision"] == "DESCARGA POR SOBRECARGA AGUDA"
    assert len(proposal["changes"]) == 2


def test_unavailable_session_is_moved_before_any_load_adaptation():
    state = _state()
    next(item for item in state["context"]["availability_next_14d"] if item["date"] == "2026-08-04")["status"] = "unavailable"
    proposal = build_adaptive_proposal(_prediction(), state, metric_catalog=METRICS)
    assert proposal["decision"] == "RESOLVER DISPONIBILIDAD"
    assert len(proposal["changes"]) == 1
    assert proposal["changes"][0]["type"] == "relocate_session"
    by_date = {item["date"]: item for item in proposal["adapted_sessions"]}
    assert by_date["2026-08-03"]["sport"] == "Trail"
    assert by_date["2026-08-04"]["sport"] == "Descanso"


def _intensity_week(*sessions):
    return [
        {
            "date": f"2026-08-{3 + index:02d}",
            "weekday": str(index),
            "sport": sport,
            "session_type": "Continuo",
            "zone": zone,
            "distance_km": 10,
            "blocks": [{"type": "steady", "zone": zone, "distance_km": 10}],
        }
        for index, (sport, zone) in enumerate(sessions)
    ]


def test_two_z3_sessions_in_different_mechanical_families_are_allowed():
    sessions = _intensity_week(("Bici", "Z3"), ("Trail", "Z3"))
    changes = []
    assert _redistribute_consecutive_intensity(sessions, {}, changes) is False
    assert changes == []


def test_cross_family_z3_is_not_allowed_when_recovery_is_incompatible():
    sessions = _intensity_week(("Bici", "Z3"), ("Trail", "Z3"))
    assert (
        _redistribute_consecutive_intensity(
            sessions, {}, [], allow_cross_family_z3=False
        )
        is True
    )


def test_consecutive_z3_trail_and_running_are_a_conflict():
    sessions = _intensity_week(("Trail", "Z3"), ("Corrida", "Z3"))
    assert _redistribute_consecutive_intensity(sessions, {}, []) is True


def test_z4_next_to_z3_is_always_a_conflict_across_sports():
    sessions = _intensity_week(("Bici", "Z4"), ("Kayak", "Z3"))
    assert _redistribute_consecutive_intensity(sessions, {}, []) is True


def test_three_consecutive_z3_sessions_are_a_conflict_across_sports():
    sessions = _intensity_week(
        ("Bici", "Z3"), ("Trail", "Z3"), ("Kayak", "Z3")
    )
    assert _redistribute_consecutive_intensity(sessions, {}, []) is True


def test_intensity_conflict_softens_z3_when_no_relocation_slot_exists():
    sessions = _intensity_week(
        ("Bici", "Z3"), ("Trail", "Z3"), ("Bici", "Z3")
    )
    changes = []

    assert _redistribute_consecutive_intensity(sessions, {}, changes) is True
    assert len(changes) == 1
    assert changes[0]["rule_id"] == "S1"
    assert changes[0]["type"] == "replace_session"
    assert changes[0]["before"]["zone"] == "Z3"
    assert changes[0]["after"]["zone"] == "Z2"
    assert changes[0]["after"]["sport"] == changes[0]["before"]["sport"]
    assert changes[0]["after"]["distance_km"] == changes[0]["before"]["distance_km"]


def test_intensity_spacing_considers_two_realized_days_before_the_week():
    sessions = _intensity_week(("Trail", "Z3"), ("Bici", "Z2"))
    for index, session in enumerate(sessions):
        session["date"] = f"2026-08-{17 + index:02d}"
    carry_in = [
        {"date": "2026-08-15", "sport": "Trail", "zone": "Z3", "source": "FIT realizado"},
        {"date": "2026-08-16", "sport": "Bici", "zone": "Z3", "source": "FIT realizado"},
    ]
    changes = []

    assert _redistribute_consecutive_intensity(
        sessions, {}, changes, carry_in_sessions=carry_in
    ) is True
    assert changes[0]["rule_id"] == "S1"
    assert changes[0]["before"]["date"] == "2026-08-17"
    assert changes[0]["after"]["date"] == "2026-08-18"
