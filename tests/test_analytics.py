"""Tests for Shewhart/Westgard drift rules and batch release decisions."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from lab_qc_api.analytics import evaluate_batch_release, evaluate_control_series
from lab_qc_api.database import LabDatabase


def _make_qc_rows(values: list[float], instrument_id: str = "ICP-OES-02") -> list[dict]:
    start = datetime(2026, 1, 1, 8, 0, 0, tzinfo=timezone.utc)
    return [
        {
            "instrument_id": instrument_id,
            "batch_id": f"B-{i + 1:03d}",
            "sample_id": f"QC-{i + 1:03d}",
            "analyte": "nitrate",
            "value": val,
            "unit": "mg/L",
            "measured_at": (start + timedelta(days=i)).isoformat(),
        }
        for i, val in enumerate(values)
    ]


def test_westgard_1_3s_and_2_2s_and_8_x_rules() -> None:
    # Nitrate target=25.0, sd=0.60 -> +2s=26.2, +3s=26.8
    # 8 consecutive positive readings, with points 7 and 8 above +2s, point 9 above +3s
    values = [25.2, 25.3, 25.4, 25.5, 25.6, 25.8, 26.3, 26.4, 27.1]
    chart = evaluate_control_series("nitrate", _make_qc_rows(values), instrument_id="ICP-OES-02")

    assert chart["drift_detected"] is True
    assert "8-x" in chart["rule_violation_counts"]
    assert "2-2s" in chart["rule_violation_counts"]
    assert "1-3s" in chart["rule_violation_counts"]

    # Point index 6 (26.3 -> z=+2.17) triggers 1-2s warning only
    assert chart["points"][6]["violated_rules"] == ["1-2s"]
    assert chart["points"][6]["status"] == "WARNING"

    # Point index 7 (8th positive & 2nd consecutive >+2s) triggers 1-2s, 2-2s, 8-x -> OUT_OF_CONTROL
    assert set(chart["points"][7]["violated_rules"]) == {"1-2s", "2-2s", "8-x"}
    assert chart["points"][7]["status"] == "OUT_OF_CONTROL"

    # Point index 8 (27.1 -> z=+3.5) triggers 1-3s, 2-2s, 8-x
    assert set(chart["points"][8]["violated_rules"]) == {"1-3s", "2-2s", "8-x"}


def test_batch_release_hold_when_instrument_drifts_even_if_within_spec(tmp_path: Path) -> None:
    db = LabDatabase(tmp_path / "release.db")
    # Insert an out-of-control QC standard (> +3s) on ICP-OES-02 at 08:00
    # followed by an in-spec production sample (24.0 <= 50.0) on the same instrument at 10:00
    db.ingest_batch(
        [
            {
                "instrument_id": "ICP-OES-02",
                "batch_id": "BATCH-HOLD",
                "sample_id": "QC-1",
                "sample_type": "QC_STANDARD",
                "analyte": "nitrate",
                "value": 27.5,  # z = +4.17 -> 1-3s OUT_OF_CONTROL
                "unit": "mg/L",
                "measured_at": "2026-02-10T08:00:00Z",
            },
            {
                "instrument_id": "ICP-OES-02",
                "batch_id": "BATCH-HOLD",
                "sample_id": "PROD-1",
                "sample_type": "PRODUCTION_SAMPLE",
                "analyte": "nitrate",
                "value": 24.0,  # Well below 50 mg/L regulatory limit
                "unit": "mg/L",
                "measured_at": "2026-02-10T10:00:00Z",
            },
        ],
        source_file="hold_test.json",
    )

    verdict = evaluate_batch_release(db, "BATCH-HOLD")
    assert verdict is not None
    assert verdict["verdict"] == "HOLD_FOR_REVIEW"
    assert verdict["checks"][0]["within_spec"] is True
    assert verdict["checks"][0]["instrument_in_control"] is False
    assert "1-3s" in verdict["checks"][0]["active_instrument_rules"]
