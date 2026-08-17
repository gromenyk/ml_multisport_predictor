"""Persistence for dated, user-authored athlete wellness and context data."""

from __future__ import annotations

from datetime import datetime
import csv
import io
import json
from pathlib import Path
import re
from typing import Any
import unicodedata
from uuid import uuid4

from config import PROJECT_ROOT


DATA_DIR = PROJECT_ROOT / "data" / "athlete_state"
WELLNESS_FILE = DATA_DIR / "daily_wellness.json"
CONTEXT_FILE = DATA_DIR / "athlete_context.json"
AVAILABILITY_FILE = DATA_DIR / "availability_calendar.json"
GARMIN_SLEEP_QUALITIES = ("poor", "fair", "good", "excellent")


def garmin_sleep_quality_from_score(score):
    if score is None:
        return None
    if score < 60:
        return "poor"
    if score < 80:
        return "fair"
    if score < 90:
        return "good"
    return "excellent"


GARMIN_QUALITY_VALUES = {
    "deficiente": "poor",
    "aceptable": "fair",
    "bueno": "good",
    "excelente": "excellent",
}

ENGLISH_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _normalized_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", (value or "").strip())
    return "".join(character for character in normalized if not unicodedata.combining(character)).lower()


def _csv_number(value):
    text = (value or "").strip()
    if not text or text in {"--", "-"}:
        return None
    return float(text.replace(",", "."))


def _csv_text(value):
    text = (value or "").strip()
    return None if not text or text in {"--", "-"} else text


def _sleep_hours(value: str):
    text = (value or "").strip().lower()
    if not text or text == "--":
        return None
    match = re.fullmatch(r"\s*(\d+)\s*h(?:\s*(\d+)\s*min)?\s*", text)
    if not match:
        raise ValueError(f"Duración de sueño no reconocida: {value!r}")
    return int(match.group(1)) + int(match.group(2) or 0) / 60.0


def parse_garmin_sleep_csv(content: bytes, source_file: str) -> dict[str, Any]:
    """Parse Garmin's localized sleep-report CSV without writing data."""
    decoding_error = None
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            text = content.decode(encoding)
            break
        except UnicodeDecodeError as error:
            decoding_error = error
    else:
        raise ValueError(f"No se pudo decodificar el CSV: {decoding_error}")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise ValueError("El CSV no contiene encabezados.")
    headers = {_normalized_text(name): name for name in reader.fieldnames}
    first_header = reader.fieldnames[0]

    def header(name):
        return headers.get(_normalized_text(name))

    required = {
        "Puntuación": header("Puntuación"),
        "Frecuencia cardiaca en reposo": header("Frecuencia cardiaca en reposo"),
        "Estado de VFC": header("Estado de VFC"),
        "Calidad": header("Calidad"),
        "Duración": header("Duración"),
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        raise ValueError("Faltan columnas Garmin: " + ", ".join(missing))

    rows = []
    warnings = []
    empty_rows = 0
    for line_number, raw in enumerate(reader, start=2):
        date_text = (raw.get(first_header) or "").strip()
        if not date_text:
            continue
        try:
            datetime.strptime(date_text, "%Y-%m-%d")
            score = _csv_number(raw.get(required["Puntuación"]))
            quality_raw = _csv_text(raw.get(required["Calidad"]))
            quality_text = _normalized_text(quality_raw)
            quality = GARMIN_QUALITY_VALUES.get(quality_text)
            if quality_text and quality is None:
                raise ValueError(f"Calidad Garmin desconocida: {raw.get(required['Calidad'])!r}")
            derived_quality = garmin_sleep_quality_from_score(score)
            if quality and derived_quality and quality != derived_quality:
                warnings.append(
                    f"Línea {line_number}: puntuación y calidad no coinciden; se preservó la calidad Garmin."
                )
            rows.append(
                {
                    "date": date_text,
                    "sleep_score": score,
                    "resting_hr_bpm": _csv_number(raw.get(required["Frecuencia cardiaca en reposo"])),
                    "hrv_ms": _csv_number(raw.get(required["Estado de VFC"])),
                    "garmin_sleep_quality": quality or derived_quality,
                    "sleep_duration_hours": _sleep_hours(raw.get(required["Duración"])),
                    "bed_time": _csv_text(raw.get(header("Hora de dormir"))),
                    "wake_time": _csv_text(raw.get(header("Hora de despertarte"))),
                    "imported_from": source_file,
                }
            )
            sleep_fields = (
                "sleep_score", "resting_hr_bpm", "hrv_ms", "garmin_sleep_quality",
                "sleep_duration_hours", "bed_time", "wake_time",
            )
            if not any(rows[-1].get(field) is not None for field in sleep_fields):
                rows.pop()
                empty_rows += 1
        except (TypeError, ValueError) as error:
            raise ValueError(f"Línea {line_number}: {error}") from error
    if not rows:
        raise ValueError("El CSV no contiene filas de sueño válidas.")
    unique = {}
    for row in rows:
        unique[row["date"]] = row
    return {
        "rows": sorted(unique.values(), key=lambda item: item["date"]),
        "warnings": warnings,
        "duplicates_in_file": len(rows) - len(unique),
        "empty_rows": empty_rows,
    }


def _decode_csv(content: bytes) -> str:
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("No se pudo decodificar el CSV.")


def _garmin_weight_date(value: str):
    match = re.fullmatch(r"\s*([A-Za-z]{3})\s+(\d{1,2}),\s*(\d{4})\s*", value or "")
    if not match:
        return None
    month = ENGLISH_MONTHS.get(match.group(1).lower())
    if month is None:
        return None
    return datetime(int(match.group(3)), month, int(match.group(2))).date().isoformat()


def _number_with_unit(value, unit):
    text = _csv_text(value)
    if text is None:
        return None
    normalized = text.lower().replace(unit.lower(), "").strip().replace(",", ".")
    return float(normalized)


def parse_garmin_weight_csv(content: bytes, source_file: str) -> dict[str, Any]:
    """Parse Garmin's alternating date/measurement weight report."""
    text = _decode_csv(content)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect=dialect)
    rows = list(reader)
    if not rows:
        raise ValueError("El CSV está vacío.")
    headers = {_normalized_text(name): index for index, name in enumerate(rows[0])}
    required = ("tiempo", "peso", "grasa corporal")
    missing = [name for name in required if name not in headers]
    if missing:
        raise ValueError("Faltan columnas Garmin: " + ", ".join(missing))

    current_date = None
    pending_without_measurement = 0
    parsed_rows = []
    for line_number, row in enumerate(rows[1:], start=2):
        if not row or not any((cell or "").strip() for cell in row):
            continue
        date_value = _garmin_weight_date(row[0])
        if date_value:
            if current_date is not None:
                pending_without_measurement += 1
            current_date = date_value
            continue
        if current_date is None:
            continue
        try:
            weight = _number_with_unit(row[headers["peso"]], "kg")
            body_fat = _number_with_unit(row[headers["grasa corporal"]], "%")
            if weight is None and body_fat is None:
                pending_without_measurement += 1
                current_date = None
                continue
            parsed_rows.append(
                {
                    "date": current_date,
                    "measurement_time": _csv_text(row[headers["tiempo"]]),
                    "weight_kg": weight,
                    "body_fat_percent": body_fat,
                    "imported_from": source_file,
                }
            )
            current_date = None
        except (IndexError, TypeError, ValueError) as error:
            raise ValueError(f"Línea {line_number}: {error}") from error
    if current_date is not None:
        pending_without_measurement += 1
    if not parsed_rows:
        raise ValueError("El CSV no contiene mediciones de peso válidas.")
    unique = {item["date"]: item for item in parsed_rows}
    return {
        "rows": sorted(unique.values(), key=lambda item: item["date"]),
        "duplicates_in_file": len(parsed_rows) - len(unique),
        "dates_without_measurement": pending_without_measurement,
    }


def merge_garmin_weight_rows(rows: list[dict[str, Any]], commit: bool = False) -> dict[str, Any]:
    """Insert new dates and fill empty physical fields without overwriting values."""
    values = [dict(item) for item in load_context()]
    by_date = {item["date"]: item for item in values}
    latest_before = max(by_date) if by_date else None
    inserted = enriched = skipped = 0
    conflicts = []
    fields = ("weight_kg", "body_fat_percent", "measurement_time")
    timestamp = _now()
    for incoming in sorted(rows, key=lambda item: item["date"]):
        existing = by_date.get(incoming["date"])
        if existing is None:
            existing = {
                "date": incoming["date"],
                "source": "garmin_csv",
                "created_at": timestamp,
            }
            for field in fields:
                existing[field] = incoming.get(field)
            existing["imported_from"] = incoming.get("imported_from")
            existing["updated_at"] = timestamp
            values.append(existing)
            by_date[existing["date"]] = existing
            inserted += 1
            continue
        changed = False
        for field in fields:
            incoming_value = incoming.get(field)
            if incoming_value is None:
                continue
            if existing.get(field) is None:
                existing[field] = incoming_value
                changed = True
            elif existing.get(field) != incoming_value:
                conflicts.append(
                    {"date": incoming["date"], "field": field,
                     "existing": existing.get(field), "csv": incoming_value}
                )
        if changed:
            existing["source"] = "manual+garmin_csv" if existing.get("source") == "manual" else "garmin_csv"
            existing["imported_from"] = incoming.get("imported_from")
            existing["updated_at"] = timestamp
            enriched += 1
        else:
            skipped += 1
    if commit:
        _write(CONTEXT_FILE, "records", sorted(values, key=lambda item: item["date"]))
    return {
        "inserted": inserted, "enriched": enriched, "skipped": skipped,
        "conflicts": conflicts, "latest_before": latest_before,
        "latest_after": max(by_date) if by_date else None,
    }


def merge_garmin_sleep_rows(rows: list[dict[str, Any]], commit: bool = False) -> dict[str, Any]:
    """Insert new dates and fill only empty Garmin fields on existing dates."""
    values = [dict(item) for item in load_wellness()]
    by_date = {item["date"]: item for item in values}
    latest_before = max(by_date) if by_date else None
    inserted = enriched = skipped = 0
    conflicts = []
    fields = (
        "sleep_score", "resting_hr_bpm", "hrv_ms", "garmin_sleep_quality",
        "sleep_duration_hours", "bed_time", "wake_time",
    )
    timestamp = _now()
    for incoming in sorted(rows, key=lambda item: item["date"]):
        existing = by_date.get(incoming["date"])
        if existing is None:
            existing = {
                "date": incoming["date"],
                "source": "garmin_csv",
                "sleep_quality": None,
                "subjective_fatigue": None,
                "muscle_fatigue": None,
                "muscle_soreness": None,
                "motivation": None,
                "has_discomfort": None,
                "notes": "",
                "created_at": timestamp,
            }
            for field in fields:
                existing[field] = incoming.get(field)
            existing["imported_from"] = incoming.get("imported_from")
            existing["updated_at"] = timestamp
            values.append(existing)
            by_date[existing["date"]] = existing
            inserted += 1
            continue
        changed = False
        for field in fields:
            incoming_value = incoming.get(field)
            if incoming_value is None:
                continue
            if existing.get(field) is None:
                existing[field] = incoming_value
                changed = True
            elif existing.get(field) != incoming_value:
                conflicts.append(
                    {
                        "date": incoming["date"],
                        "field": field,
                        "existing": existing.get(field),
                        "csv": incoming_value,
                    }
                )
        if changed:
            existing["source"] = (
                "manual+garmin_csv" if existing.get("source") == "manual" else "garmin_csv"
            )
            existing["imported_from"] = incoming.get("imported_from")
            existing["updated_at"] = timestamp
            enriched += 1
        else:
            skipped += 1
    if commit:
        _write(WELLNESS_FILE, "records", sorted(values, key=lambda item: item["date"]))
    return {
        "inserted": inserted,
        "enriched": enriched,
        "skipped": skipped,
        "conflicts": conflicts,
        "latest_before": latest_before,
        "latest_after": max(by_date) if by_date else None,
        "total_after": len(values),
    }


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _load(path: Path, key: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as file:
        values = json.load(file).get(key, [])
    if not isinstance(values, list):
        raise ValueError(f"{path.name}: '{key}' debe ser una lista.")
    return sorted(values, key=lambda item: item["date"])


def _write(path: Path, key: str, values: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f".{uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump({key: values}, file, indent=4, ensure_ascii=False)
        file.write("\n")
    try:
        temporary.replace(path)
    except PermissionError:
        with path.open("w", encoding="utf-8") as file:
            json.dump({key: values}, file, indent=4, ensure_ascii=False)
            file.write("\n")
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _optional_number(value: Any, minimum=None, maximum=None):
    if value in (None, ""):
        return None
    number = float(value)
    if minimum is not None and number < minimum:
        raise ValueError(f"El valor debe ser mayor o igual a {minimum}.")
    if maximum is not None and number > maximum:
        raise ValueError(f"El valor debe ser menor o igual a {maximum}.")
    return number


def load_wellness() -> list[dict[str, Any]]:
    return _load(WELLNESS_FILE, "records")


def save_wellness(value: dict[str, Any]) -> None:
    sleep_score = _optional_number(value.get("sleep_score"), 0, 100)
    garmin_sleep_quality = value.get("garmin_sleep_quality") or None
    if garmin_sleep_quality not in (None,) + GARMIN_SLEEP_QUALITIES:
        raise ValueError("Categoría de calidad de sueño Garmin desconocida.")
    if sleep_score is not None:
        garmin_sleep_quality = garmin_sleep_quality_from_score(sleep_score)
    record = {
        "date": value["date"],
        "source": value.get("source", "manual"),
        "sleep_duration_hours": _optional_number(
            value.get("sleep_duration_hours"), 0, 24
        ),
        "sleep_score": sleep_score,
        "garmin_sleep_quality": garmin_sleep_quality,
        "sleep_quality": _optional_number(value.get("sleep_quality"), 1, 10),
        "hrv_ms": _optional_number(value.get("hrv_ms"), 0),
        "resting_hr_bpm": _optional_number(value.get("resting_hr_bpm"), 20, 220),
        "stress_score": _optional_number(value.get("stress_score"), 0, 100),
        "body_battery_morning": _optional_number(
            value.get("body_battery_morning"), 0, 100
        ),
        "body_battery_evening": _optional_number(
            value.get("body_battery_evening"), 0, 100
        ),
        "subjective_fatigue": _optional_number(
            value.get("subjective_fatigue"), 1, 10
        ),
        "muscle_fatigue": _optional_number(
            value.get("muscle_fatigue", value.get("muscle_soreness")), 1, 10
        ),
        # Campo legado: se conserva para poder abrir snapshots y registros
        # anteriores, pero los nuevos cálculos usan muscle_fatigue.
        "muscle_soreness": _optional_number(value.get("muscle_soreness"), 1, 10),
        "motivation": _optional_number(value.get("motivation"), 1, 10),
        "has_discomfort": bool(value.get("has_discomfort", False)),
        "notes": (value.get("notes") or "").strip(),
    }
    values = load_wellness()
    existing = next((item for item in values if item["date"] == record["date"]), None)
    # These Garmin-derived fields are no longer part of manual entry, but an
    # imported value must survive a later manual edit of the same date.
    for field in (
        "stress_score",
        "body_battery_morning",
        "body_battery_evening",
        "bed_time",
        "wake_time",
        "imported_from",
    ):
        if field not in value:
            record[field] = existing.get(field) if existing else None
    if existing and "garmin_csv" in existing.get("source", ""):
        record["source"] = "manual+garmin_csv"
    timestamp = _now()
    if existing:
        created_at = existing.get("created_at", timestamp)
        existing.clear()
        existing.update({**record, "created_at": created_at, "updated_at": timestamp})
    else:
        values.append({**record, "created_at": timestamp, "updated_at": timestamp})
    _write(WELLNESS_FILE, "records", sorted(values, key=lambda item: item["date"]))


def delete_wellness(record_date: str) -> None:
    values = load_wellness()
    retained = [item for item in values if item["date"] != record_date]
    if len(retained) == len(values):
        raise KeyError(f"No existe bienestar para {record_date}.")
    _write(WELLNESS_FILE, "records", retained)


def load_context() -> list[dict[str, Any]]:
    fields = (
        "date", "weight_kg", "body_fat_percent", "measurement_time",
        "imported_from", "source", "created_at", "updated_at",
    )
    return [
        {field: item.get(field) for field in fields if field in item}
        for item in _load(CONTEXT_FILE, "records")
    ]


def save_context(value: dict[str, Any]) -> None:
    record = {
        "date": value["date"],
        "weight_kg": _optional_number(value.get("weight_kg"), 20, 400),
        "body_fat_percent": _optional_number(value.get("body_fat_percent"), 0, 100),
        "source": "manual",
    }
    values = load_context()
    existing = next((item for item in values if item["date"] == record["date"]), None)
    timestamp = _now()
    if existing:
        for field in ("measurement_time", "imported_from"):
            record[field] = existing.get(field)
        if existing.get("source") and "garmin_csv" in existing["source"]:
            record["source"] = "manual+garmin_csv"
        created_at = existing.get("created_at", timestamp)
        existing.clear()
        existing.update({**record, "created_at": created_at, "updated_at": timestamp})
    else:
        values.append({**record, "created_at": timestamp, "updated_at": timestamp})
    _write(CONTEXT_FILE, "records", sorted(values, key=lambda item: item["date"]))


def load_availability() -> list[dict[str, Any]]:
    """Load explicit calendar constraints; absent dates are always free."""
    return _load(AVAILABILITY_FILE, "records")


def save_availability_calendar(values: list[dict[str, Any]]) -> None:
    """Upsert dated limits. ``None`` removes the limit and restores Free."""
    records = load_availability()
    by_date = {item["date"]: item for item in records}
    timestamp = _now()
    for value in values:
        record_date = value["date"]
        datetime.strptime(record_date, "%Y-%m-%d")
        maximum = _optional_number(value.get("max_minutes"), 0, 1440)
        if maximum is None:
            by_date.pop(record_date, None)
            continue
        existing = by_date.get(record_date, {})
        by_date[record_date] = {
            "date": record_date,
            "status": "unavailable" if maximum == 0 else "limited",
            "max_minutes": maximum,
            "source": "manual",
            "created_at": existing.get("created_at", timestamp),
            "updated_at": timestamp,
        }
    _write(
        AVAILABILITY_FILE,
        "records",
        sorted(by_date.values(), key=lambda item: item["date"]),
    )
