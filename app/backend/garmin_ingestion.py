"""Local ingestion helpers for manually uploaded Garmin FIT files."""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path

from config import PROJECT_ROOT


GARMIN_DIR = PROJECT_ROOT / "garmin"
FIT_FILES_DIR = GARMIN_DIR / "fit_files"
ACTIVITIES_DIR = GARMIN_DIR / "activities"
EXTRACTOR_PATH = GARMIN_DIR / "extract_fit.py"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def save_fit(uploaded_file):
    """Save a FIT file without silently overwriting different content."""
    if uploaded_file is None:
        return False, "No FIT file selected.", None

    safe_name = Path(uploaded_file.name).name
    if Path(safe_name).suffix.lower() != ".fit":
        return False, "Only .fit files are allowed.", None

    content = bytes(uploaded_file.getbuffer())
    if len(content) < 12 or content[8:12] != b".FIT":
        return False, "The uploaded file does not contain a valid FIT header.", None

    FIT_FILES_DIR.mkdir(parents=True, exist_ok=True)
    destination = FIT_FILES_DIR / safe_name

    if destination.exists():
        existing = destination.read_bytes()
        if _sha256(existing) == _sha256(content):
            return True, "This FIT file is already stored.", destination
        return (
            False,
            "A different FIT file already exists with the same name.",
            None,
        )

    destination.write_bytes(content)
    return True, "FIT file uploaded successfully.", destination


def extract_fit_activities():
    """Extract FIT activities and evaluate assigned automatic metrics."""
    if not EXTRACTOR_PATH.exists():
        return False, f"Extractor not found: {EXTRACTOR_PATH}"

    result = subprocess.run(
        [sys.executable, str(EXTRACTOR_PATH)],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = (result.stdout + "\n" + result.stderr).strip()
    if result.returncode != 0:
        return False, output
    try:
        from metrics.orchestrator import evaluate_all_activities

        summary = evaluate_all_activities()
        output += (
            "\n\nEvaluación de métricas\n"
            f"Actividades evaluadas: {summary['activities_evaluated']}\n"
            "Mediciones calculadas/actualizadas: "
            f"{summary['measurements_saved']}"
        )
    except Exception as error:
        output += (
            "\n\nLa extracción terminó correctamente, pero falló la evaluación "
            f"de métricas: {error}"
        )
        return False, output
    try:
        from backend.route_matching import update_route_families

        route_summary = update_route_families()
        output += (
            "\n\nComparación de rutas repetidas\n"
            f"Actividades nuevas comparadas: {route_summary.get('new_activities', 0)}\n"
            f"Familias de rutas disponibles: {route_summary.get('families', 0)}"
        )
        if route_summary.get("failures"):
            output += f"\nActividades sin comparar: {len(route_summary['failures'])}"
    except Exception as error:
        output += (
            "\n\nLa extracción y las métricas terminaron correctamente, pero no se "
            f"pudo actualizar la comparación de rutas: {error}"
        )
    try:
        from training_load.performed_load import calculate_all_performed_loads

        load_summary = calculate_all_performed_loads().get("summary", {})
        output += (
            "\n\nActualización incremental de carga\n"
            f"Actividades recalculadas: {load_summary.get('activities_recalculated', 0)}\n"
            f"Actividades reutilizadas: {load_summary.get('activities_reused', 0)}"
        )
    except Exception as error:
        output += f"\n\nNo se pudo actualizar la carga realizada: {error}"
    return True, output
