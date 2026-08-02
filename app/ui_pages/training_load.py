"""Standalone Training Load comparison page."""

try:
    # Current name used on a clean application start.
    from ui_pages.predictions import render_training_load
except ImportError:
    # Streamlit hot reload can temporarily retain the previous predictions
    # module, where this renderer still had its original private name.
    from ui_pages.predictions import (
        _render_training_load_comparison as render_training_load,
    )


def render():
    """
    Display prescribed, predicted and performed load in one daily view.

    The implementation is shared with the prediction workflow so a newly
    generated prediction can refresh its derived load without duplicating
    calculation or presentation rules.
    """
    render_training_load()
