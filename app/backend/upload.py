"""
upload.py

Handles uploading and storing raw PDF training plans.
"""

from pathlib import Path
from typing import Tuple

from config import RAW_DATA_DIR


def save_pdf(uploaded_file) -> Tuple[bool, str, Path]:
    """
    Save an uploaded PDF into the raw_data directory.

    Parameters
    ----------
    uploaded_file : streamlit UploadedFile

    Returns
    -------
    success : bool
        True if the file was saved correctly.

    message : str
        Status message.

    saved_path : Path
        Location of the stored PDF.
    """

    if uploaded_file is None:
        return False, "No file selected.", None

    if uploaded_file.type != "application/pdf":
        return False, "Only PDF files are allowed.", None

    save_path = RAW_DATA_DIR / uploaded_file.name

    try:

        with open(save_path, "wb") as f:
            f.write(uploaded_file.getbuffer())

        return (
            True,
            "PDF uploaded successfully.",
            save_path
        )

    except Exception as e:

        return (
            False,
            str(e),
            None
        )