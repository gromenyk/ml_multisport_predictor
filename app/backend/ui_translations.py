"""Spanish presentation labels for legacy English and Portuguese values."""

from __future__ import annotations


DISPLAY_ES = {
    "Rest": "Descanso", "Recovery": "Recuperación", "Light": "Suave",
    "Leve": "Suave", "Longão": "Tirada larga", "Longao": "Tirada larga",
    "Contínuo": "Continuo", "Continuo": "Continuo", "Simulado": "Simulación",
    "Regenerativo": "Recuperación", "Corrida": "Carrera",
    "Trail": "Carrera de montaña", "Trail running": "Carrera de montaña",
    "running": "Carrera", "cycling": "Bicicleta", "kayaking": "Kayak",
    "Flat": "Llano", "Hilly": "Ondulado", "Climb": "Ascenso",
    "Easy": "Suave", "Moderate": "Moderado", "Hard": "Exigente",
    "Monday": "Lunes", "Tuesday": "Martes", "Wednesday": "Miércoles",
    "Thursday": "Jueves", "Friday": "Viernes", "Saturday": "Sábado",
    "Sunday": "Domingo", "Segunda": "Lunes", "Terça": "Martes",
    "Quarta": "Miércoles", "Quinta": "Jueves", "Sexta": "Viernes",
    "Sábado": "Sábado", "Domingo": "Domingo",
    "progress": "Progresar", "consolidate": "Consolidar",
    "regress": "Retroceder", "insufficient_evidence": "Evidencia insuficiente",
    "well_tolerated": "Bien tolerada", "difficult": "Difícil",
    "not_tolerated": "No tolerada", "not_evaluable": "No evaluable",
}


def display_es(value):
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped in DISPLAY_ES:
        return DISPLAY_ES[stripped]
    replacements = (
        ("Longão", "Tirada larga"), ("Longao", "Tirada larga"),
        ("Contínuo", "Continuo"), ("Simulado", "Simulación"),
        ("Regenerativo", "Recuperación"), ("Leve", "Suave"),
        ("Corrida", "Carrera"), ("Trail running", "Carrera de montaña"),
        ("WU(", "CAL("), ("CD(", "VUELTA("), ("ST(", "CONT("),
    )
    translated = value
    for source, target in replacements:
        translated = translated.replace(source, target)
    return translated
