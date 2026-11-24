"""FastAPI application for Analytical Laboratory QC & Drift Detection."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Query, Response

from . import __version__
from .analytics import compute_control_chart, compute_drift_alerts, evaluate_batch_release
from .config import get_db_path
from .database import LabDatabase
from .domain import ANALYTE_SPECS
from .generator import generate_synthetic_lab_records
from .plotting import render_control_chart_png
from .schemas import (
    BatchReleaseResponse,
    ControlChartResponse,
    DriftAlertsResponse,
    HealthResponse,
    IngestRequest,
    IngestResponse,
    QualitySummaryResponse,
    QuarantineRecordOut,
)


def create_app(db_override: LabDatabase | None = None) -> FastAPI:
    """Application factory enabling isolated test databases and clean DI."""

    def get_db() -> LabDatabase:
        if db_override is not None:
            return db_override
        return LabDatabase(get_db_path())

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        get_db().init_schema()
        yield

    app = FastAPI(
        title="Lab QC API",
        version=__version__,
        description=(
            "FastAPI service for analytical laboratory batch ingestion with Pydantic data contracts, "
            "partial-success quarantine routing, unit harmonization, idempotent SQLite persistence, "
            "Shewhart/Westgard statistical drift detection, and automated batch release verdicts."
        ),
        lifespan=lifespan,
    )

    @app.get("/health", response_model=HealthResponse, tags=["System"])
    def health(db: LabDatabase = Depends(get_db)) -> HealthResponse:
        stored, quarantined = db.get_health_counts()
        return HealthResponse(
            status="ok",
            version=__version__,
            database_ready=True,
            stored_measurements=stored,
            quarantined_records=quarantined,
        )

    @app.get("/api/v1/analytes", tags=["Reference"])
    def list_analytes() -> list[dict[str, object]]:
        """List supported analytical parameters, accepted units, and QC limits."""
        return [
            {
                "analyte": spec.name,
                "display_name": spec.display_name,
                "canonical_unit": spec.canonical_unit,
                "accepted_units": sorted(spec.unit_factors.keys()),
                "spec_range": [spec.spec_min, spec.spec_max],
                "qc_target": spec.qc_target,
                "qc_sd": spec.qc_sd,
            }
            for spec in ANALYTE_SPECS.values()
        ]

    @app.post("/api/v1/measurements/ingest", response_model=IngestResponse, tags=["Ingestion"])
    def ingest_measurements(
        payload: IngestRequest,
        db: LabDatabase = Depends(get_db),
    ) -> IngestResponse:
        """Ingest raw instrument readings with partial-success quarantine and deduplication."""
        result = db.ingest_batch(records=payload.records, source_file=payload.source_file)
        return IngestResponse(**result)

    @app.post("/api/v1/demo/seed", response_model=IngestResponse, tags=["Demo"])
    def seed_demo_data(
        days: int = Query(default=60, ge=10, le=180),
        reset: bool = Query(default=True, description="Clear existing tables before seeding."),
        db: LabDatabase = Depends(get_db),
    ) -> IngestResponse:
        """Populate the database with a deterministic synthetic 60-day analytical lab dataset."""
        if reset:
            db.reset()
        records = generate_synthetic_lab_records(days=days, seed=42)
        result = db.ingest_batch(records=records, source_file=f"synthetic_lab_{days}d.json")
        return IngestResponse(**result)

    @app.get("/api/v1/quality/summary", response_model=QualitySummaryResponse, tags=["Data Quality"])
    def quality_summary(db: LabDatabase = Depends(get_db)) -> QualitySummaryResponse:
        """Return global ingestion KPIs, unit conversions, duplicates, and quarantine breakdown."""
        return QualitySummaryResponse(**db.get_quality_summary())

    @app.get(
        "/api/v1/quality/quarantine",
        response_model=list[QuarantineRecordOut],
        tags=["Data Quality"],
    )
    def list_quarantine(
        limit: int = Query(default=50, ge=1, le=500),
        error_code: str | None = Query(default=None),
        db: LabDatabase = Depends(get_db),
    ) -> list[QuarantineRecordOut]:
        """Inspect rejected raw records routed to the quarantine table."""
        rows = db.list_quarantine_records(limit=limit, error_code=error_code)
        return [QuarantineRecordOut(**r) for r in rows]

    @app.get(
        "/api/v1/analytics/control-chart/{analyte}",
        response_model=ControlChartResponse,
        tags=["QC Analytics"],
    )
    def get_control_chart(
        analyte: str,
        instrument_id: str | None = Query(default=None),
        db: LabDatabase = Depends(get_db),
    ) -> ControlChartResponse:
        """Compute Shewhart limits and Westgard multi-rule flags (1-2s, 1-3s, 2-2s, 8-x)."""
        try:
            chart = compute_control_chart(db, analyte_raw=analyte, instrument_id=instrument_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return ControlChartResponse(**chart)

    @app.get(
        "/api/v1/analytics/control-chart/{analyte}/plot",
        tags=["QC Analytics"],
        responses={200: {"content": {"image/png": {}}}},
    )
    def get_control_chart_plot(
        analyte: str,
        instrument_id: str | None = Query(default=None),
        db: LabDatabase = Depends(get_db),
    ) -> Response:
        """Render a live PNG Shewhart control chart directly from the QC database."""
        try:
            chart = compute_control_chart(db, analyte_raw=analyte, instrument_id=instrument_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if chart["total_qc_points"] == 0:
            raise HTTPException(status_code=404, detail=f"No QC standard points found for '{analyte}'.")
        png_bytes = render_control_chart_png(chart)
        return Response(content=png_bytes, media_type="image/png")

    @app.get("/api/v1/analytics/drift-alerts", response_model=DriftAlertsResponse, tags=["QC Analytics"])
    def get_drift_alerts(db: LabDatabase = Depends(get_db)) -> DriftAlertsResponse:
        """Return drift alerts across all monitored instrument-analyte pairs."""
        return DriftAlertsResponse(**compute_drift_alerts(db))

    @app.get("/api/v1/batches", tags=["Batch Release"])
    def list_batches(db: LabDatabase = Depends(get_db)) -> dict[str, object]:
        """List all production batch identifiers available for release evaluation."""
        batches = db.list_production_batches()
        return {"total_batches": len(batches), "batches": batches}

    @app.get(
        "/api/v1/batches/{batch_id}/release",
        response_model=BatchReleaseResponse,
        tags=["Batch Release"],
    )
    def get_batch_release(
        batch_id: str,
        db: LabDatabase = Depends(get_db),
    ) -> BatchReleaseResponse:
        """Compute automated batch release verdict (APPROVED, HOLD_FOR_REVIEW, or REJECTED)."""
        verdict = evaluate_batch_release(db, batch_id=batch_id)
        if verdict is None:
            raise HTTPException(status_code=404, detail=f"Production batch '{batch_id}' not found.")
        return BatchReleaseResponse(**verdict)

    return app


app = create_app()
