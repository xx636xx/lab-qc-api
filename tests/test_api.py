"""End-to-end integration tests for FastAPI endpoints."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from lab_qc_api.database import LabDatabase
from lab_qc_api.main import create_app


def _make_client(tmp_path: Path) -> TestClient:
    db = LabDatabase(tmp_path / "api_test.db")
    app = create_app(db_override=db)
    return TestClient(app)


def test_end_to_end_seed_analytics_plot_and_release(tmp_path: Path) -> None:
    with _make_client(tmp_path) as client:
        # 1. Health check on empty DB
        h0 = client.get("/health")
        assert h0.status_code == 200
        assert h0.json()["stored_measurements"] == 0

        # 2. Supported analytes contract
        analytes = client.get("/api/v1/analytes")
        assert analytes.status_code == 200
        names = {a["analyte"] for a in analytes.json()}
        assert names == {"ph", "conductivity", "nitrate", "iron", "turbidity"}

        # 3. Deterministic demo seed
        seed = client.post("/api/v1/demo/seed?days=60&reset=true")
        assert seed.status_code == 200
        seed_body = seed.json()
        assert seed_body["inserted"] > 900
        assert seed_body["duplicates_skipped"] == 6
        assert seed_body["quarantined"] == 12
        assert seed_body["unit_conversions"] > 40

        # 4. Data quality summary & quarantine endpoint
        q_sum = client.get("/api/v1/quality/summary")
        assert q_sum.status_code == 200
        assert q_sum.json()["total_quarantined"] == 12

        q_list = client.get("/api/v1/quality/quarantine?error_code=INCOMPATIBLE_UNIT")
        assert q_list.status_code == 200
        assert len(q_list.json()) == 3

        # 5. Control chart detects drift on ICP-OES-02 nitrate, while ICP-OES-01 stays in control
        drift_chart = client.get("/api/v1/analytics/control-chart/nitrate?instrument_id=ICP-OES-02")
        assert drift_chart.status_code == 200
        assert drift_chart.json()["drift_detected"] is True
        assert drift_chart.json()["out_of_control_points"] >= 10

        stable_chart = client.get("/api/v1/analytics/control-chart/nitrate?instrument_id=ICP-OES-01")
        assert stable_chart.status_code == 200
        assert stable_chart.json()["drift_detected"] is False

        # 6. Drift alerts endpoint flags ICP-OES-02 / nitrate as CRITICAL
        alerts = client.get("/api/v1/analytics/drift-alerts")
        assert alerts.status_code == 200
        alerts_body = alerts.json()
        assert alerts_body["active_critical_alerts"] >= 1
        top = alerts_body["alerts"][0]
        assert top["instrument_id"] == "ICP-OES-02"
        assert top["analyte"] == "nitrate"
        assert top["severity"] == "CRITICAL"

        # 7. Live PNG control chart endpoint returns valid PNG bytes
        plot = client.get("/api/v1/analytics/control-chart/nitrate/plot")
        assert plot.status_code == 200
        assert plot.headers["content-type"] == "image/png"
        assert plot.content[:8] == b"\x89PNG\r\n\x1a\n"
        assert len(plot.content) > 10_000

        # 8. Batch release verdicts: early batch APPROVED, batch 018 REJECTED (iron > 0.20),
        # late batch on drifting ICP-OES-02 HOLD_FOR_REVIEW
        approved = client.get("/api/v1/batches/BATCH-2026-005/release")
        assert approved.status_code == 200
        assert approved.json()["verdict"] == "APPROVED"

        rejected = client.get("/api/v1/batches/BATCH-2026-018/release")
        assert rejected.status_code == 200
        assert rejected.json()["verdict"] == "REJECTED"

        hold = client.get("/api/v1/batches/BATCH-2026-056/release")
        assert hold.status_code == 200
        assert hold.json()["verdict"] == "HOLD_FOR_REVIEW"

        # 9. Unknown analyte / batch return clean 404
        assert client.get("/api/v1/analytics/control-chart/unobtainium").status_code == 404
        assert client.get("/api/v1/batches/BATCH-DOES-NOT-EXIST/release").status_code == 404
