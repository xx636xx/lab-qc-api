"""Application settings for Lab QC API."""

from __future__ import annotations

import os
from pathlib import Path


def get_db_path() -> Path:
    """Return the SQLite database path from environment or default local path."""
    raw = os.getenv("LAB_QC_DB_PATH", "data/lab_qc.db")
    path = Path(raw)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    return path
