from pathlib import Path
import sys


APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from backend import wellness_store


CSV_TEXT = (
    "Tiempo,Peso,Cambio,IMC,Grasa corporal,Masa muscular esquelética,Masa ósea,Agua corporal,\n"
    '" Jul 31, 2026",\n'
    "6:45 am,78.6 kg,0.9 kg,24.3,18.4%,--,--,--,\n"
    '" Jul 30, 2026",\n'
    "7:36 am,77.7 kg,0.6 kg,24,--,--,--,--,\n"
    '" Jul 29, 2026",\n'
)


def test_parse_and_incremental_weight_merge(tmp_path):
    original_file = wellness_store.CONTEXT_FILE
    wellness_store.CONTEXT_FILE = tmp_path / "athlete_context.json"
    try:
        parsed = wellness_store.parse_garmin_weight_csv(
            CSV_TEXT.encode("utf-8"), "weight.csv"
        )
        assert len(parsed["rows"]) == 2
        assert parsed["dates_without_measurement"] == 1
        assert parsed["rows"][1]["date"] == "2026-07-31"
        assert parsed["rows"][1]["weight_kg"] == 78.6
        assert parsed["rows"][1]["body_fat_percent"] == 18.4

        dry_run = wellness_store.merge_garmin_weight_rows(parsed["rows"])
        assert dry_run["inserted"] == 2
        assert not wellness_store.CONTEXT_FILE.exists()

        wellness_store.merge_garmin_weight_rows(parsed["rows"], commit=True)
        repeated = wellness_store.merge_garmin_weight_rows(parsed["rows"], commit=True)
        assert repeated["inserted"] == 0
        assert repeated["skipped"] == 2
    finally:
        wellness_store.CONTEXT_FILE = original_file
