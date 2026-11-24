"""SQLite persistence layer with idempotent ingestion and analytical queries."""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator

from .domain import VALID_SAMPLE_TYPES, validate_and_normalize_value

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS measurements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    instrument_id TEXT NOT NULL,
    batch_id TEXT NOT NULL,
    sample_id TEXT NOT NULL,
    sample_type TEXT NOT NULL CHECK (sample_type IN ('QC_STANDARD', 'PRODUCTION_SAMPLE')),
    analyte TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    original_value REAL NOT NULL,
    original_unit TEXT NOT NULL,
    was_unit_converted INTEGER NOT NULL CHECK (was_unit_converted IN (0, 1)),
    measured_at TEXT NOT NULL,
    source_file TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    UNIQUE (instrument_id, batch_id, sample_id, sample_type, analyte, measured_at)
);

CREATE INDEX IF NOT EXISTS idx_measurements_qc_lookup
    ON measurements (sample_type, analyte, instrument_id, measured_at);

CREATE INDEX IF NOT EXISTS idx_measurements_batch
    ON measurements (batch_id, sample_type);

CREATE TABLE IF NOT EXISTS quarantine_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT NOT NULL,
    error_code TEXT NOT NULL,
    error_detail TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL,
    quarantined_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT NOT NULL,
    received INTEGER NOT NULL,
    inserted INTEGER NOT NULL,
    duplicates_skipped INTEGER NOT NULL,
    quarantined INTEGER NOT NULL,
    unit_conversions INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso_timestamp(raw: Any) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(microsecond=0).isoformat()


class LabDatabase:
    """Thin SQLite repository enforcing data contracts and idempotent loads."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        if str(db_path) != ":memory:":
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        if str(self.db_path) != ":memory:":
            conn.execute("PRAGMA journal_mode = WAL;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA_SQL)

    def reset(self) -> None:
        """Clear all tables (used for deterministic demo reseeding and tests)."""
        with self.connect() as conn:
            conn.execute("DELETE FROM measurements;")
            conn.execute("DELETE FROM quarantine_records;")
            conn.execute("DELETE FROM ingestion_runs;")

    def ingest_batch(self, records: list[dict[str, Any]], source_file: str) -> dict[str, Any]:
        """Validate, normalize, deduplicate, and store a batch of raw records."""
        now_iso = _utc_now_iso()
        inserted = 0
        duplicates_skipped = 0
        quarantined = 0
        unit_conversions = 0
        quarantine_counts: Counter[str] = Counter()

        valid_rows: list[tuple[Any, ...]] = []
        bad_rows: list[tuple[Any, ...]] = []

        required_strings = ("instrument_id", "batch_id", "sample_id", "analyte", "unit")

        for record in records:
            error_code: str | None = None
            error_detail: str | None = None

            if not isinstance(record, dict):
                error_code = "MALFORMED_RECORD"
                error_detail = "Record must be a JSON object."
            else:
                for field in required_strings:
                    val = record.get(field)
                    if not isinstance(val, str) or not val.strip():
                        error_code = "MISSING_REQUIRED_FIELD"
                        error_detail = f"Required string field '{field}' is missing or empty."
                        break

            sample_type_norm = ""
            measured_at_iso: str | None = None
            analyte_key: str | None = None
            canonical_val: float | None = None
            canonical_unit: str | None = None
            was_converted = False

            if error_code is None:
                sample_type_raw = record.get("sample_type", "PRODUCTION_SAMPLE")
                if not isinstance(sample_type_raw, str):
                    error_code = "INVALID_SAMPLE_TYPE"
                    error_detail = f"Invalid sample_type {sample_type_raw!r}."
                else:
                    sample_type_norm = sample_type_raw.strip().upper()
                    if sample_type_norm not in VALID_SAMPLE_TYPES:
                        error_code = "INVALID_SAMPLE_TYPE"
                        error_detail = (
                            f"sample_type '{sample_type_raw}' must be one of "
                            f"{sorted(VALID_SAMPLE_TYPES)}."
                        )

            if error_code is None:
                measured_at_iso = _parse_iso_timestamp(record.get("measured_at"))
                if measured_at_iso is None:
                    error_code = "INVALID_TIMESTAMP"
                    error_detail = (
                        f"Field 'measured_at' must be a valid ISO-8601 timestamp; "
                        f"received {record.get('measured_at')!r}."
                    )

            if error_code is None:
                (
                    analyte_key,
                    canonical_val,
                    canonical_unit,
                    was_converted,
                    error_code,
                    error_detail,
                ) = validate_and_normalize_value(
                    analyte_raw=record["analyte"],
                    value_raw=record.get("value"),
                    unit_raw=record["unit"],
                )

            if error_code is not None:
                quarantined += 1
                quarantine_counts[error_code] += 1
                bad_rows.append(
                    (
                        source_file,
                        error_code,
                        error_detail or "Validation error",
                        json.dumps(record, default=str),
                        now_iso,
                    )
                )
            else:
                assert analyte_key is not None
                assert canonical_val is not None
                assert canonical_unit is not None
                assert measured_at_iso is not None
                valid_rows.append(
                    (
                        record["instrument_id"].strip(),
                        record["batch_id"].strip(),
                        record["sample_id"].strip(),
                        sample_type_norm,
                        analyte_key,
                        canonical_val,
                        canonical_unit,
                        float(record["value"]),
                        record["unit"].strip(),
                        1 if was_converted else 0,
                        measured_at_iso,
                        source_file,
                        now_iso,
                    )
                )

        with self.connect() as conn:
            for row in valid_rows:
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO measurements (
                        instrument_id, batch_id, sample_id, sample_type,
                        analyte, value, unit, original_value, original_unit,
                        was_unit_converted, measured_at, source_file, ingested_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    row,
                )
                if cur.rowcount == 1:
                    inserted += 1
                    if row[9] == 1:
                        unit_conversions += 1
                else:
                    duplicates_skipped += 1

            if bad_rows:
                conn.executemany(
                    """
                    INSERT INTO quarantine_records (
                        source_file, error_code, error_detail, raw_payload_json, quarantined_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    bad_rows,
                )

            run_cur = conn.execute(
                """
                INSERT INTO ingestion_runs (
                    source_file, received, inserted, duplicates_skipped,
                    quarantined, unit_conversions, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_file,
                    len(records),
                    inserted,
                    duplicates_skipped,
                    quarantined,
                    unit_conversions,
                    now_iso,
                ),
            )
            run_id = int(run_cur.lastrowid)

        acceptance_pct = round(((len(records) - quarantined) / len(records)) * 100.0, 2)
        return {
            "run_id": run_id,
            "source_file": source_file,
            "received": len(records),
            "inserted": inserted,
            "duplicates_skipped": duplicates_skipped,
            "quarantined": quarantined,
            "unit_conversions": unit_conversions,
            "acceptance_rate_pct": acceptance_pct,
            "quarantine_breakdown": dict(sorted(quarantine_counts.items())),
        }

    def get_health_counts(self) -> tuple[int, int]:
        with self.connect() as conn:
            m_count = conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
            q_count = conn.execute("SELECT COUNT(*) FROM quarantine_records").fetchone()[0]
            return int(m_count), int(q_count)

    def get_quality_summary(self) -> dict[str, Any]:
        with self.connect() as conn:
            run_totals = conn.execute(
                """
                SELECT
                    COUNT(*) AS total_runs,
                    COALESCE(SUM(received), 0) AS total_received,
                    COALESCE(SUM(duplicates_skipped), 0) AS total_duplicates_skipped,
                    COALESCE(SUM(quarantined), 0) AS total_quarantined,
                    COALESCE(SUM(unit_conversions), 0) AS total_unit_conversions
                FROM ingestion_runs
                """
            ).fetchone()

            valid_stored = int(
                conn.execute("SELECT COUNT(*) FROM measurements").fetchone()[0]
            )

            q_rows = conn.execute(
                """
                SELECT error_code, COUNT(*) AS cnt
                FROM quarantine_records
                GROUP BY error_code
                ORDER BY cnt DESC, error_code ASC
                """
            ).fetchall()

            analyte_rows = conn.execute(
                """
                SELECT analyte, COUNT(*) AS cnt
                FROM measurements
                GROUP BY analyte
                ORDER BY cnt DESC, analyte ASC
                """
            ).fetchall()

        total_received = int(run_totals["total_received"])
        total_quarantined = int(run_totals["total_quarantined"])
        acceptance_pct = (
            round(((total_received - total_quarantined) / total_received) * 100.0, 2)
            if total_received > 0
            else 0.0
        )

        return {
            "total_runs": int(run_totals["total_runs"]),
            "total_received": total_received,
            "total_valid_stored": valid_stored,
            "total_duplicates_skipped": int(run_totals["total_duplicates_skipped"]),
            "total_quarantined": total_quarantined,
            "total_unit_conversions": int(run_totals["total_unit_conversions"]),
            "overall_acceptance_rate_pct": acceptance_pct,
            "quarantine_by_error_code": {r["error_code"]: int(r["cnt"]) for r in q_rows},
            "measurements_by_analyte": {r["analyte"]: int(r["cnt"]) for r in analyte_rows},
        }

    def list_quarantine_records(self, limit: int = 50, error_code: str | None = None) -> list[dict[str, Any]]:
        query = """
            SELECT id, source_file, error_code, error_detail, raw_payload_json, quarantined_at
            FROM quarantine_records
        """
        params: list[Any] = []
        if error_code:
            query += " WHERE error_code = ?"
            params.append(error_code)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()

        return [
            {
                "id": int(r["id"]),
                "source_file": r["source_file"],
                "error_code": r["error_code"],
                "error_detail": r["error_detail"],
                "raw_payload": json.loads(r["raw_payload_json"]),
                "quarantined_at": r["quarantined_at"],
            }
            for r in rows
        ]

    def fetch_qc_series(
        self,
        analyte: str,
        instrument_id: str | None = None,
    ) -> list[dict[str, Any]]:
        query = """
            SELECT instrument_id, batch_id, sample_id, analyte, value, unit, measured_at
            FROM measurements
            WHERE sample_type = 'QC_STANDARD' AND analyte = ?
        """
        params: list[Any] = [analyte]
        if instrument_id:
            query += " AND instrument_id = ?"
            params.append(instrument_id)
        query += " ORDER BY measured_at ASC, id ASC"

        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def list_qc_instrument_analyte_pairs(self) -> list[tuple[str, str]]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT instrument_id, analyte
                FROM measurements
                WHERE sample_type = 'QC_STANDARD'
                ORDER BY instrument_id ASC, analyte ASC
                """
            ).fetchall()
        return [(r["instrument_id"], r["analyte"]) for r in rows]

    def fetch_batch_production_aggregates(self, batch_id: str) -> list[dict[str, Any]]:
        """Aggregate production sample replicates per analyte and instrument for a batch."""
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT
                    analyte,
                    instrument_id,
                    unit,
                    COUNT(*) AS sample_count,
                    ROUND(AVG(value), 4) AS mean_value,
                    ROUND(MIN(value), 4) AS min_value,
                    ROUND(MAX(value), 4) AS max_value,
                    MAX(measured_at) AS latest_measured_at
                FROM measurements
                WHERE batch_id = ? AND sample_type = 'PRODUCTION_SAMPLE'
                GROUP BY analyte, instrument_id, unit
                ORDER BY analyte ASC, instrument_id ASC
                """,
                (batch_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_production_batches(self) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT DISTINCT batch_id
                FROM measurements
                WHERE sample_type = 'PRODUCTION_SAMPLE'
                ORDER BY batch_id ASC
                """
            ).fetchall()
        return [r["batch_id"] for r in rows]
