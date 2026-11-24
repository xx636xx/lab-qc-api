"""Deterministic synthetic analytical laboratory dataset generator.

Generates 60 days of multi-instrument QC standard runs and production batches with:
  - Realistic unit variations (ug/L, ppb, mS/cm) to exercise unit harmonization
  - Progressive calibration drift on ICP-OES-02 (nitrate) starting on day 36
  - Two intentional out-of-spec production batches (iron / nitrate excursions)
  - Malformed, out-of-bounds, unknown-analyte, and duplicate records for quarantine/idempotency tests
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import random
from typing import Any

from .domain import ANALYTE_SPECS


def generate_synthetic_lab_records(days: int = 60, seed: int = 42) -> list[dict[str, Any]]:
    """Build a deterministic batch of raw laboratory records including dirty edge cases."""
    rng = random.Random(seed)
    start_date = datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)
    records: list[dict[str, Any]] = []

    for day_idx in range(days):
        day_dt = start_date + timedelta(days=day_idx)
        batch_id = f"BATCH-2026-{day_idx + 1:03d}"

        # 1) Daily QC standard runs across instruments
        # ICP-OES-01: stable control across nitrate & iron
        # ICP-OES-02: develops positive calibration drift on nitrate from day 36 onward
        # METER-01: stable pH, conductivity, turbidity
        qc_plan: list[tuple[str, str, float]] = [
            ("ICP-OES-01", "nitrate", 0.0),
            ("ICP-OES-01", "iron", 0.0),
            ("ICP-OES-02", "nitrate", 0.0 if day_idx < 35 else 0.14 * (day_idx - 34)),
            ("ICP-OES-02", "iron", 0.0),
            ("METER-01", "ph", 0.0),
            ("METER-01", "conductivity", 0.0),
            ("METER-01", "turbidity", 0.0),
        ]

        for idx, (inst, analyte, drift_offset) in enumerate(qc_plan):
            spec = ANALYTE_SPECS[analyte]
            # Keep normal noise well within +/-1.8 SD so baseline instruments stay clean
            noise = rng.gauss(0.0, spec.qc_sd * 0.72)
            # Clamp extreme baseline tails on healthy instruments for deterministic demo clarity
            if drift_offset == 0.0:
                noise = max(-1.85 * spec.qc_sd, min(1.85 * spec.qc_sd, noise))
            val = round(spec.qc_target + drift_offset + noise, 4)

            unit = spec.canonical_unit
            raw_val = val
            # Exercise unit harmonization on ~20% of nitrate/iron/conductivity readings
            if analyte == "iron" and day_idx % 4 == 0:
                unit = "ug/L"
                raw_val = round(val * 1000.0, 2)
            elif analyte == "nitrate" and day_idx % 7 == 0:
                unit = "ppm"
            elif analyte == "conductivity" and day_idx % 5 == 0:
                unit = "mS/cm"
                raw_val = round(val / 1000.0, 4)

            records.append(
                {
                    "instrument_id": inst,
                    "batch_id": batch_id,
                    "sample_id": f"QC-{inst}-{analyte[:3].upper()}-{day_idx + 1:03d}",
                    "sample_type": "QC_STANDARD",
                    "analyte": analyte,
                    "value": raw_val,
                    "unit": unit,
                    "measured_at": (day_dt + timedelta(minutes=idx * 5)).isoformat(),
                }
            )

        # 2) Production batch replicates (2 replicates per analyte per batch)
        nitrate_inst = "ICP-OES-01" if day_idx % 2 == 0 else "ICP-OES-02"
        iron_inst = "ICP-OES-01" if day_idx % 2 == 0 else "ICP-OES-02"

        prod_baselines: list[tuple[str, str, float, float]] = [
            (nitrate_inst, "nitrate", 22.0, 2.2),
            (iron_inst, "iron", 0.085, 0.015),
            ("METER-01", "ph", 7.30, 0.18),
            ("METER-01", "conductivity", 440.0, 35.0),
            ("METER-01", "turbidity", 1.15, 0.20),
        ]

        for rep in (1, 2):
            for idx, (inst, analyte, mean_val, std_val) in enumerate(prod_baselines):
                spec = ANALYTE_SPECS[analyte]
                val = round(max(0.001, rng.gauss(mean_val, std_val)), 4)

                # Inject two genuine out-of-spec production excursions:
                # Batch 018 -> Iron above 0.20 mg/L; Batch 049 -> Nitrate above 50.0 mg/L
                if batch_id == "BATCH-2026-018" and analyte == "iron":
                    val = round(0.245 + 0.01 * rep, 4)
                elif batch_id == "BATCH-2026-049" and analyte == "nitrate":
                    val = round(54.8 + 0.7 * rep, 4)

                unit = spec.canonical_unit
                raw_val = val
                if analyte == "iron" and rep == 2 and day_idx % 3 == 0:
                    unit = "ppb"
                    raw_val = round(val * 1000.0, 2)
                elif analyte == "conductivity" and rep == 1 and day_idx % 6 == 0:
                    unit = "mS/cm"
                    raw_val = round(val / 1000.0, 4)

                records.append(
                    {
                        "instrument_id": inst,
                        "batch_id": batch_id,
                        "sample_id": f"PROD-{batch_id[-3:]}-{analyte[:3].upper()}-R{rep}",
                        "sample_type": "PRODUCTION_SAMPLE",
                        "analyte": analyte,
                        "value": raw_val,
                        "unit": unit,
                        "measured_at": (
                            day_dt + timedelta(hours=2, minutes=rep * 20 + idx * 3)
                        ).isoformat(),
                    }
                )

    # 3) Inject realistic exact duplicate records (to verify idempotent deduplication)
    duplicate_samples = [dict(records[i]) for i in (0, 12, 45, 90, 150, 210)]
    records.extend(duplicate_samples)

    # 4) Inject realistic dirty / corrupt instrument rows (to verify quarantine contract)
    dirty_records: list[dict[str, Any]] = [
        # Out-of-physical-bounds pH (> 14)
        {
            "instrument_id": "METER-01",
            "batch_id": "BATCH-2026-010",
            "sample_id": "BAD-PH-01",
            "sample_type": "PRODUCTION_SAMPLE",
            "analyte": "ph",
            "value": 16.4,
            "unit": "pH",
            "measured_at": "2026-01-10T11:00:00Z",
        },
        # Negative concentration
        {
            "instrument_id": "ICP-OES-01",
            "batch_id": "BATCH-2026-014",
            "sample_id": "BAD-NO3-02",
            "sample_type": "PRODUCTION_SAMPLE",
            "analyte": "nitrate",
            "value": -3.2,
            "unit": "mg/L",
            "measured_at": "2026-01-14T11:00:00Z",
        },
        # Negative turbidity
        {
            "instrument_id": "METER-01",
            "batch_id": "BATCH-2026-022",
            "sample_id": "BAD-TURB-03",
            "sample_type": "PRODUCTION_SAMPLE",
            "analyte": "turbidity",
            "value": -0.45,
            "unit": "NTU",
            "measured_at": "2026-01-22T11:00:00Z",
        },
        # Incompatible units (NTU reported for nitrate, mg/L for pH, pH for iron)
        {
            "instrument_id": "ICP-OES-02",
            "batch_id": "BATCH-2026-019",
            "sample_id": "BAD-UNIT-01",
            "sample_type": "PRODUCTION_SAMPLE",
            "analyte": "nitrate",
            "value": 18.5,
            "unit": "NTU",
            "measured_at": "2026-01-19T11:00:00Z",
        },
        {
            "instrument_id": "METER-01",
            "batch_id": "BATCH-2026-025",
            "sample_id": "BAD-UNIT-02",
            "sample_type": "PRODUCTION_SAMPLE",
            "analyte": "ph",
            "value": 7.1,
            "unit": "mg/L",
            "measured_at": "2026-01-25T11:00:00Z",
        },
        {
            "instrument_id": "ICP-OES-01",
            "batch_id": "BATCH-2026-031",
            "sample_id": "BAD-UNIT-03",
            "sample_type": "PRODUCTION_SAMPLE",
            "analyte": "iron",
            "value": 0.09,
            "unit": "uS/cm",
            "measured_at": "2026-01-31T11:00:00Z",
        },
        # Unknown analytes
        {
            "instrument_id": "ICP-OES-01",
            "batch_id": "BATCH-2026-027",
            "sample_id": "BAD-ANA-01",
            "sample_type": "PRODUCTION_SAMPLE",
            "analyte": "mercury_unregistered",
            "value": 0.002,
            "unit": "mg/L",
            "measured_at": "2026-01-27T11:00:00Z",
        },
        {
            "instrument_id": "ICP-OES-02",
            "batch_id": "BATCH-2026-041",
            "sample_id": "BAD-ANA-02",
            "sample_type": "PRODUCTION_SAMPLE",
            "analyte": "arsenic_draft",
            "value": 0.005,
            "unit": "mg/L",
            "measured_at": "2026-02-10T11:00:00Z",
        },
        # Missing required fields
        {
            "instrument_id": "",
            "batch_id": "BATCH-2026-033",
            "sample_id": "BAD-MISS-01",
            "sample_type": "PRODUCTION_SAMPLE",
            "analyte": "nitrate",
            "value": 21.0,
            "unit": "mg/L",
            "measured_at": "2026-02-02T11:00:00Z",
        },
        {
            "instrument_id": "METER-01",
            "batch_id": "BATCH-2026-038",
            "sample_id": "BAD-MISS-02",
            "sample_type": "PRODUCTION_SAMPLE",
            "analyte": "ph",
            "value": 7.2,
            "measured_at": "2026-02-07T11:00:00Z",
        },
        # Bad timestamps
        {
            "instrument_id": "ICP-OES-01",
            "batch_id": "BATCH-2026-044",
            "sample_id": "BAD-TS-01",
            "sample_type": "PRODUCTION_SAMPLE",
            "analyte": "iron",
            "value": 0.08,
            "unit": "mg/L",
            "measured_at": "not-a-timestamp",
        },
        # Non-numeric value
        {
            "instrument_id": "ICP-OES-02",
            "batch_id": "BATCH-2026-052",
            "sample_id": "BAD-VAL-01",
            "sample_type": "PRODUCTION_SAMPLE",
            "analyte": "nitrate",
            "value": "ND",
            "unit": "mg/L",
            "measured_at": "2026-02-21T11:00:00Z",
        },
    ]
    records.extend(dirty_records)
    return records
