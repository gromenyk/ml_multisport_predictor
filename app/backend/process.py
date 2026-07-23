"""
process.py

Handles processing of uploaded training plans.
"""

import json
import re

from datetime import datetime, timedelta

from backend.extractor import extract_plan_from_pdf
from backend.structured_processor import process_structured_sessions
from backend.normalizer import normalize_json

from pathlib import Path

from config import RAW_DATA_DIR, PROCESSED_DATA_DIR


# ==========================================================
# PUBLIC FUNCTIONS
# ==========================================================

def process_pending_pdfs():
    """
    Process every PDF that has not yet been converted into JSON.
    """

    pending_pdfs = _get_pending_pdfs()

    if not pending_pdfs:
        return False, "No new PDFs found."

    for pdf in pending_pdfs:
        process_single_pdf(pdf)

    return True, f"{len(pending_pdfs)} training plan(s) processed successfully."


def process_single_pdf(pdf_path: Path):
    """
    Process a single training plan.

    Workflow
    --------
    PDF
        ↓
    OpenAI extraction
        ↓
    Metadata
        ↓
    Save JSON
    """

    # -------------------------------
    # Extract training plan
    # -------------------------------

    plan = extract_plan_from_pdf(pdf_path)

    # Dates encoded in the Treinus filename are more reliable than dates
    # extracted from the document text. Apply them before normalization so
    # empty or incorrect LLM dates cannot place a week in the wrong period.
    plan = _apply_filename_dates(plan, pdf_path.name)

    # -------------------------------
    # Normalize extracted JSON
    # -------------------------------

    plan = normalize_json(plan)

    # -------------------------------
    # Process structured sessions
    # -------------------------------

    plan = process_structured_sessions(plan)

    # -------------------------------
    # Add metadata
    # -------------------------------

    plan["metadata"] = {
        "source_file": pdf_path.name,
        "processed_at": datetime.now().isoformat(timespec="seconds")
    }

    # -------------------------------
    # Save JSON
    # -------------------------------

    _save_json(plan, pdf_path)

def _save_json(plan: dict, pdf_path: Path):

    filename = _extract_week_from_filename(pdf_path.name)

    output_path = PROCESSED_DATA_DIR / f"{filename}.json"

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            plan,
            f,
            indent=4,
            ensure_ascii=False
        )

# ==========================================================
# PRIVATE FUNCTIONS
# ==========================================================

def _get_pending_pdfs():
    """
    Returns every PDF that has not yet been processed.
    """

    pending = []

    for pdf in sorted(RAW_DATA_DIR.glob("*.pdf")):

        if not _is_processed(pdf):

            pending.append(pdf)

    return pending


def _is_processed(pdf_path: Path):
    """
    Returns True if this PDF already has a processed JSON.

    Compatible with:
        - Old JSON files (without metadata)
        - New JSON files (with metadata.source_file)
    """

    # ------------------------------------------------------
    # First: check new JSON metadata
    # ------------------------------------------------------

    for json_file in PROCESSED_DATA_DIR.glob("*.json"):

        try:

            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            metadata = data.get("metadata", {})

            if metadata.get("source_file") == pdf_path.name:

                return True

        except Exception:
            continue

    # ------------------------------------------------------
    # Second: compatibility with old dataset
    # ------------------------------------------------------

    week_start = _extract_week_from_filename(pdf_path.name)

    if week_start is None:

        return False

    json_path = PROCESSED_DATA_DIR / f"{week_start}.json"

    return json_path.exists()


def _extract_week_from_filename(filename: str):
    """
    Extracts the first date from a TrainingPeaks PDF filename.

    Example
    -------
    Planilha_Gerardo_13-07-2026_a_19-07-2026.pdf

    returns

    2026-07-13
    """

    match = re.search(r"(\d{2})-(\d{2})-(\d{4})", filename)

    if match is None:

        return None

    day, month, year = match.groups()

    return f"{year}-{month}-{day}"


def _apply_filename_dates(plan: dict, filename: str):
    """Apply the PDF filename date range to the extracted plan."""

    matches = re.findall(r"(\d{2})-(\d{2})-(\d{4})", filename)

    if len(matches) < 2:
        return plan

    start_day, start_month, start_year = matches[0]
    end_day, end_month, end_year = matches[1]

    start = datetime(
        int(start_year),
        int(start_month),
        int(start_day),
    )
    end = datetime(
        int(end_year),
        int(end_month),
        int(end_day),
    )

    plan["week_start"] = start.strftime("%Y-%m-%d")
    plan["week_end"] = end.strftime("%Y-%m-%d")

    weekday_offsets = {
        "seg": 0,
        "ter": 1,
        "qua": 2,
        "qui": 3,
        "sex": 4,
        "sáb": 5,
        "sab": 5,
        "dom": 6,
    }

    for session in plan.get("sessions", []):
        weekday = str(session.get("weekday", "")).strip().lower()
        offset = weekday_offsets.get(weekday)

        if offset is not None:
            session["date"] = (start + timedelta(days=offset)).strftime(
                "%Y-%m-%d"
            )

    return plan
