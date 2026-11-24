"""Pydantic v2 request and response models for Lab QC API."""

from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field


class IngestRequest(BaseModel):
    """Batch ingestion payload accepting raw instrument records.

    Records are intentionally typed as generic mappings so that malformed rows
    are routed to the quarantine table instead of failing the entire batch.
    """

    model_config = ConfigDict(extra="forbid")

    source_file: str = Field(
        default="api_upload",
        min_length=1,
        max_length=120,
        description="Origin filename or instrument export identifier.",
    )
    records: list[dict[str, Any]] = Field(
        ...,
        min_length=1,
        max_length=10_000,
        description="Raw analytical measurement records to validate and ingest.",
    )


class IngestResponse(BaseModel):
    """Structured summary of a partial-success batch ingestion."""

    run_id: int
    source_file: str
    received: int
    inserted: int
    duplicates_skipped: int
    quarantined: int
    unit_conversions: int
    acceptance_rate_pct: float
    quarantine_breakdown: dict[str, int]


class QuarantineRecordOut(BaseModel):
    """Quarantined raw record with machine-readable rejection reason."""

    id: int
    source_file: str
    error_code: str
    error_detail: str
    raw_payload: dict[str, Any]
    quarantined_at: str


class QualitySummaryResponse(BaseModel):
    """Global data-quality KPIs across all ingested runs."""

    total_runs: int
    total_received: int
    total_valid_stored: int
    total_duplicates_skipped: int
    total_quarantined: int
    total_unit_conversions: int
    overall_acceptance_rate_pct: float
    quarantine_by_error_code: dict[str, int]
    measurements_by_analyte: dict[str, int]


class ControlChartPoint(BaseModel):
    """Single QC standard observation with Shewhart z-score and Westgard flags."""

    measured_at: str
    instrument_id: str
    batch_id: str
    sample_id: str
    value: float
    unit: str
    z_score: float
    violated_rules: list[str]
    status: Literal["IN_CONTROL", "WARNING", "OUT_OF_CONTROL"]


class ControlChartResponse(BaseModel):
    """Shewhart / Westgard statistical control chart summary for an analyte."""

    analyte: str
    display_name: str
    unit: str
    instrument_id: str | None
    target_mean: float
    target_sd: float
    ucl_3s: float
    lcl_3s: float
    uwl_2s: float
    lwl_2s: float
    total_qc_points: int
    out_of_control_points: int
    warning_points: int
    drift_detected: bool
    rule_violation_counts: dict[str, int]
    points: list[ControlChartPoint]


class DriftAlertItem(BaseModel):
    """Instrument + analyte drift summary alert."""

    instrument_id: str
    analyte: str
    display_name: str
    unit: str
    total_qc_points: int
    out_of_control_points: int
    latest_z_score: float
    first_violation_at: str | None
    triggered_rules: list[str]
    severity: Literal["OK", "WARNING", "CRITICAL"]


class DriftAlertsResponse(BaseModel):
    """Overview of drift status across all monitored instrument-analyte pairs."""

    total_monitored_pairs: int
    active_critical_alerts: int
    active_warning_alerts: int
    alerts: list[DriftAlertItem]


class BatchAnalyteCheck(BaseModel):
    """Specification and instrument QC evaluation for one analyte in a batch."""

    analyte: str
    display_name: str
    instrument_id: str
    sample_count: int
    mean_value: float
    max_value: float
    min_value: float
    unit: str
    spec_min: float
    spec_max: float
    within_spec: bool
    instrument_in_control: bool
    active_instrument_rules: list[str]


class BatchReleaseResponse(BaseModel):
    """Automated laboratory batch release verdict."""

    batch_id: str
    verdict: Literal["APPROVED", "HOLD_FOR_REVIEW", "REJECTED"]
    reasons: list[str]
    checked_analytes: int
    checks: list[BatchAnalyteCheck]


class HealthResponse(BaseModel):
    """Liveness and database readiness payload."""

    status: Literal["ok"]
    version: str
    database_ready: bool
    stored_measurements: int
    quarantined_records: int
