from pathlib import Path
import sys


APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from backend import wellness_store


def test_free_is_default_and_zero_blocks_training(tmp_path):
    original_file = wellness_store.AVAILABILITY_FILE
    wellness_store.AVAILABILITY_FILE = tmp_path / "availability_calendar.json"
    try:
        wellness_store.save_availability_calendar(
            [
                {"date": "2026-08-03", "max_minutes": None},
                {"date": "2026-08-04", "max_minutes": 0},
                {"date": "2026-08-05", "max_minutes": 90},
            ]
        )
        records = wellness_store.load_availability()
        assert [item["date"] for item in records] == ["2026-08-04", "2026-08-05"]
        assert records[0]["status"] == "unavailable"
        assert records[0]["max_minutes"] == 0
        assert records[1]["status"] == "limited"
        assert records[1]["max_minutes"] == 90

        wellness_store.save_availability_calendar(
            [{"date": "2026-08-04", "max_minutes": None}]
        )
        assert [item["date"] for item in wellness_store.load_availability()] == [
            "2026-08-05"
        ]
    finally:
        wellness_store.AVAILABILITY_FILE = original_file
