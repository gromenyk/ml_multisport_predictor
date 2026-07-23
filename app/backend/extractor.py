from pathlib import Path
import os
import json
import time

import fitz
from dotenv import load_dotenv
os.environ.pop("SSL_CERT_FILE", None)
from openai import OpenAI

from config import PROJECT_DIR, PROMPTS_DIR

# =====================================================
# CONFIGURACIÓN
# =====================================================

load_dotenv(PROJECT_DIR / ".env")

MODEL = "gpt-5-mini"

PROMPT_FILE = PROMPTS_DIR / "prompt.txt"
SCHEMA_FILE = PROMPTS_DIR / "schema.json"

client = OpenAI()

# =====================================================
# FUNCIONES
# =====================================================

def extract_pdf_text(pdf_path: Path) -> str:

    doc = fitz.open(pdf_path)

    text = ""

    for page in doc:
        text += page.get_text()

    doc.close()

    return text


def load_prompt():

    with open(PROMPT_FILE, "r", encoding="utf-8") as f:
        return f.read()


def load_schema():

    with open(SCHEMA_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_plan_from_pdf(pdf_path: Path):

    print(f"\n==============================")
    print(f"Processing {pdf_path.name}")

    prompt = load_prompt()
    schema = load_schema()

    t0 = time.time()

    text = extract_pdf_text(pdf_path)

    t1 = time.time()

    print(f"PDF extraído en {t1 - t0:.2f} s")
    print(f"Caracteres extraídos: {len(text):,}")

    print("Llamando a OpenAI...")

    api_start = time.time()

    response = client.responses.create(
        model=MODEL,
        reasoning={
            "effort": "low"
        },
        input=[
            {
                "role": "system",
                "content": prompt
            },
            {
                "role": "user",
                "content": f"""
Schema:

{json.dumps(schema, indent=2)}

Training plan:

{text}
"""
            }
        ]
    )

    api_end = time.time()

    print(f"Respuesta recibida en {api_end-api_start:.2f} s")

    raw_output = response.output_text

    result = json.loads(raw_output)

    return result