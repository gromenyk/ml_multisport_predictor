from pathlib import Path
import sys


APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from backend import wellness_store


CSV_TEXT = (
    "Puntuaci\u00f3n de sue\u00f1o 4 semanas,Puntuaci\u00f3n,"
    "Frecuencia cardiaca en reposo,Body Battery,Pulsioximetr\u00eda,Respiraci\u00f3n,"
    "Estado de VFC,Calidad,Duraci\u00f3n,Hora de dormir,Hora de despertarte\n"
    "2026-03-13,81,48,71,--,13.49,54,Bueno,7h 1min,11:48 pm,6:49 am\n"
    "2026-03-12,--,--,--,--,--,--,--,--,--,--\n"
    "2026-03-11,52,48,48,--,14.44,53,Deficiente,5h 27min,12:03 am,6:18 am\n"
)


def test_parse_and_incremental_merge(tmp_path):
    original_file = wellness_store.WELLNESS_FILE
    wellness_store.WELLNESS_FILE = tmp_path / "daily_wellness.json"
    try:
        parsed = wellness_store.parse_garmin_sleep_csv(
            CSV_TEXT.encode("utf-8"), "sleep.csv"
        )
        assert len(parsed["rows"]) == 2
        assert parsed["empty_rows"] == 1
        assert parsed["rows"][1]["date"] == "2026-03-13"
        assert parsed["rows"][1]["garmin_sleep_quality"] == "good"
        assert abs(parsed["rows"][1]["sleep_duration_hours"] - (7 + 1 / 60)) < 1e-9

        dry_run = wellness_store.merge_garmin_sleep_rows(parsed["rows"])
        assert dry_run["inserted"] == 2
        assert not wellness_store.WELLNESS_FILE.exists()

        committed = wellness_store.merge_garmin_sleep_rows(parsed["rows"], commit=True)
        assert committed["inserted"] == 2
        repeated = wellness_store.merge_garmin_sleep_rows(parsed["rows"], commit=True)
        assert repeated["inserted"] == 0
        assert repeated["skipped"] == 2
    finally:
        wellness_store.WELLNESS_FILE = original_file
