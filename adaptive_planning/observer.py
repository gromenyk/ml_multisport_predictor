"""Backward-compatible entry point for the adaptive engine."""

from adaptive_planning.engine import build_adaptive_proposal


def build_observer_proposal(prediction, athlete_state, goal_names=None, metric_catalog=None):
    return build_adaptive_proposal(prediction, athlete_state, goal_names, metric_catalog)
