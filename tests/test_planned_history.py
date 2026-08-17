from datetime import date, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
for path in (ROOT, APP_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from ML.dataset import TrainingDataset, validate_consecutive_context
from adaptive_planning import planned_history


def _week(start, source=None):
    start_date = date.fromisoformat(start)
    sessions = []
    for offset in range(7):
        day = start_date + timedelta(days=offset)
        sessions.append({
            "date": day.isoformat(), "weekday": ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")[offset],
            "sport": "Descanso", "session_type": None, "structured": False,
            "terrain": None, "blocks": [{"type": "rest"}],
        })
    value = {
        "week_start": start, "week_end": (start_date + timedelta(days=6)).isoformat(),
        "week_type": "Otros", "sessions": sessions,
    }
    if source:
        value["metadata"] = {"source": source}
    return value


class PlannedHistoryTests(unittest.TestCase):
    def test_adaptive_week_replaces_same_human_week_for_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            human, adaptive = root / "human", root / "adaptive"
            human.mkdir(); adaptive.mkdir()
            starts = ["2026-07-13", "2026-07-20", "2026-07-27", "2026-08-03"]
            for start in starts:
                (human / f"{start}.json").write_text(json.dumps(_week(start)), encoding="utf-8")
            replacement = _week("2026-08-03", "adaptive_approved")
            replacement["sessions"][1]["sport"] = "Bici"
            (adaptive / "2026-08-03.json").write_text(json.dumps(replacement), encoding="utf-8")
            dataset = TrainingDataset(human, planned_history_dir=adaptive)
            context = validate_consecutive_context(dataset.weeks)
            self.assertEqual(context[-1]["metadata"]["source"], "adaptive_approved")
            self.assertEqual(context[-1]["sessions"][1]["sport"], "Bici")

    def test_context_rejects_weekly_gap(self):
        weeks = [_week(start) for start in ("2026-07-06", "2026-07-13", "2026-07-27", "2026-08-03")]
        with self.assertRaisesRegex(ValueError, "salto"):
            validate_consecutive_context(weeks)

    def test_export_has_model_context_provenance(self):
        with tempfile.TemporaryDirectory() as directory:
            original = planned_history.HISTORY_DIR
            planned_history.HISTORY_DIR = Path(directory)
            try:
                plan = {
                    "id": "plan-1", "version": 1, "status": "active",
                    "prediction_id": "prediction-1", "decision_id": "decision-1",
                    "engine_version": "test", "published_at": "2026-08-03T10:00:00+02:00",
                    **_week("2026-08-03"),
                }
                path = planned_history.export_approved_plan(plan)
                exported = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(exported["metadata"]["source"], "adaptive_approved")
                self.assertEqual(exported["metadata"]["adaptive_plan_id"], "plan-1")
                self.assertEqual(len(exported["sessions"]), 7)
            finally:
                planned_history.HISTORY_DIR = original


if __name__ == "__main__":
    unittest.main()
