"""Tests for partial-success ingestion, unit normalization, quarantine, and idempotency."""

from __future__ import annotations

from pathlib import Path

from lab_qc_api.database import LabDatabase


def test_unit_normalization_and_quarantine_isolation(tmp_path: Path) -> None:
    db = LabDatabase(tmp_path / "test.db")
    records = [
        # Valid ug/L -> mg/L conversion (120 ug/L = 0.12 mg/L)
        {
            "instrument_id": "ICP-OES-01",
            "batch_id": "B-001",
            "sample_id": "S-1",
            "sample_type": "PRODUCTION_SAMPLE",
            "analyte": "iron",
            "value": 120.0,
            "unit": "ug/L",
            "measured_at": "2026-01-01T10:00:00Z",
        },
        # Valid mS/cm -> uS/cm conversion (0.52 mS/cm = 520 uS/cm)
        {
            "instrument_id": "METER-01",
            "batch_id": "B-001",
            "sample_id": "S-2",
            "sample_type": "PRODUCTION_SAMPLE",
            "analyte": "conductivity",
            "value": 0.52,
            "unit": "mS/cm",
            "measured_at": "2026-01-01T10:05:00Z",
        },
        # Invalid: pH > 14
        {
            "instrument_id": "METER-01",
            "batch_id": "B-001",
            "sample_id": "S-3",
            "sample_type": "PRODUCTION_SAMPLE",
            "analyte": "ph",
            "value": 15.2,
            "unit": "pH",
            "measured_at": "2026-01-01T10:10:00Z",
        },
        # Invalid: incompatible unit
        {
            "instrument_id": "ICP-OES-01",
            "batch_id": "B-001",
            "sample_id": "S-4",
            "sample_type": "PRODUCTION_SAMPLE",
            "analyte": "nitrate",
            "value": 20.0,
            "unit": "NTU",
            "measured_at": "2026-01-01T10:15:00Z",
        },
        # Invalid: unknown analyte
        {
            "instrument_id": "ICP-OES-01",
            "batch_id": "B-001",
            "sample_id": "S-5",
            "sample_type": "PRODUCTION_SAMPLE",
            "analyte": "lead_unknown",
            "value": 0.01,
            "unit": "mg/L",
            "measured_at": "2026-01-01T10:20:00Z",
        },
        # Invalid: bad timestamp
        {
            "instrument_id": "ICP-OES-01",
            "batch_id": "B-001",
            "sample_id": "S-6",
            "sample_type": "PRODUCTION_SAMPLE",
            "analyte": "nitrate",
            "value": 20.0,
            "unit": "mg/L",
            "measured_at": "invalid-date",
        },
        # Invalid: non-numeric value
        {
            "instrument_id": "ICP-OES-01",
            "batch_id": "B-001",
            "sample_id": "S-7",
            "sample_type": "PRODUCTION_SAMPLE",
            "analyte": "nitrate",
            "value": "ND",
            "unit": "mg/L",
            "measured_at": "2026-01-01T10:25:00Z",
        },
    ]

    result = db.ingest_batch(records, source_file="unit_test.json")

    assert result["received"] == 7
    assert result["inserted"] == 2
    assert result["quarantined"] == 5
    assert result["unit_conversions"] == 2
    assert result["quarantine_breakdown"] == {
        "INCOMPATIBLE_UNIT": 1,
        "INVALID_TIMESTAMP": 1,
        "INVALID_VALUE_TYPE": 1,
        "OUT_OF_PHYSICAL_BOUNDS": 1,
        "UNKNOWN_ANALYTE": 1,
    }

    aggs = db.fetch_batch_production_aggregates("B-001")
    by_analyte = {row["analyte"]: row for row in aggs}
    assert by_analyte["iron"]["mean_value"] == 0.12
    assert by_analyte["iron"]["unit"] == "mg/L"
    assert by_analyte["conductivity"]["mean_value"] == 520.0
    assert by_analyte["conductivity"]["unit"] == "uS/cm"


def test_idempotent_reingestion_creates_zero_duplicates(tmp_path: Path) -> None:
    db = LabDatabase(tmp_path / "idempotent.db")
    batch = [
        {
            "instrument_id": "ICP-OES-01",
            "batch_id": "B-100",
            "sample_id": "S-100",
            "sample_type": "QC_STANDARD",
            "analyte": "nitrate",
            "value": 25.1,
            "unit": "mg/L",
            "measured_at": "2026-02-01T08:00:00Z",
        }
    ]

    first = db.ingest_batch(batch, source_file="run1.json")
    second = db.ingest_batch(batch, source_file="run2.json")

    assert first["inserted"] == 1
    assert first["duplicates_skipped"] == 0
    assert second["inserted"] == 0
    assert second["duplicates_skipped"] == 1

    stored, quarantined = db.get_health_counts()
    assert stored == 1
    assert quarantined == 0
