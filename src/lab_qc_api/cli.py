"""Command-line utilities for seeding demo data and generating visual assets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fastapi.testclient import TestClient

from .config import get_db_path
from .database import LabDatabase
from .main import create_app
from .plotting import render_overview_dashboard


def cmd_seed(days: int) -> None:
    db = LabDatabase(get_db_path())
    app = create_app(db_override=db)
    with TestClient(app) as client:
        resp = client.post(f"/api/v1/demo/seed?days={days}&reset=true")
        resp.raise_for_status()
        print(json.dumps(resp.json(), indent=2))


def cmd_generate_assets(output_path: Path, days: int) -> None:
    """Run the real FastAPI endpoints via TestClient and render the overview dashboard."""
    db = LabDatabase(get_db_path())
    app = create_app(db_override=db)
    with TestClient(app) as client:
        seed_res = client.post(f"/api/v1/demo/seed?days={days}&reset=true")
        seed_res.raise_for_status()

        quality = client.get("/api/v1/quality/summary").json()
        nitrate_chart = client.get("/api/v1/analytics/control-chart/nitrate").json()
        drift_alerts = client.get("/api/v1/analytics/drift-alerts").json()
        batches = client.get("/api/v1/batches").json()["batches"]
        verdicts = [client.get(f"/api/v1/batches/{b}/release").json() for b in batches]

        out = render_overview_dashboard(
            nitrate_chart=nitrate_chart,
            quality_summary=quality,
            batch_verdicts=verdicts,
            output_path=output_path,
        )

        # Print verifiable summary metrics to stdout
        summary = {
            "asset_generated": str(out),
            "seed_result": seed_res.json(),
            "quality_summary": quality,
            "drift_alerts_summary": {
                "total_monitored_pairs": drift_alerts["total_monitored_pairs"],
                "active_critical_alerts": drift_alerts["active_critical_alerts"],
                "active_warning_alerts": drift_alerts["active_warning_alerts"],
                "top_alert": drift_alerts["alerts"][0] if drift_alerts["alerts"] else None,
            },
            "batch_verdict_counts": {
                k: sum(1 for v in verdicts if v["verdict"] == k)
                for k in ("APPROVED", "HOLD_FOR_REVIEW", "REJECTED")
            },
            "sample_verdicts": {
                "approved_example": client.get("/api/v1/batches/BATCH-2026-005/release").json(),
                "rejected_example": client.get("/api/v1/batches/BATCH-2026-018/release").json(),
                "hold_example": next(
                    (v for v in verdicts if v["verdict"] == "HOLD_FOR_REVIEW"),
                    None,
                ),
            },
        }
        print(json.dumps(summary, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(prog="lab-qc-api", description="Lab QC API CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_seed = sub.add_parser("seed", help="Seed local SQLite DB with deterministic demo data")
    p_seed.add_argument("--days", type=int, default=60)

    p_assets = sub.add_parser(
        "generate-assets",
        help="Seed DB, query FastAPI endpoints, and generate overview chart PNG",
    )
    p_assets.add_argument("--output", type=Path, default=Path("assets/qc_api_overview.png"))
    p_assets.add_argument("--days", type=int, default=60)

    args = parser.parse_args()
    if args.command == "seed":
        cmd_seed(days=args.days)
    elif args.command == "generate-assets":
        cmd_generate_assets(output_path=args.output, days=args.days)


if __name__ == "__main__":
    main()
