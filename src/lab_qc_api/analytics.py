"""Statistical Quality Control (Shewhart / Westgard) and batch release engine."""

from __future__ import annotations

from collections import Counter
from typing import Any

from .database import LabDatabase
from .domain import ANALYTE_SPECS, normalize_analyte_key


def evaluate_control_series(
    analyte_raw: str,
    rows: list[dict[str, Any]],
    instrument_id: str | None = None,
) -> dict[str, Any]:
    """Compute Shewhart control limits and Westgard multi-rule flags.

    Rules evaluated chronologically per instrument:
      - 1-2s (warning): single point beyond +/-2 SD
      - 1-3s (out_of_control): single point beyond +/-3 SD
      - 2-2s (out_of_control): 2 consecutive points beyond +/-2 SD on the same side
      - 8-x  (out_of_control): 8 consecutive points on the same side of target mean (drift)
    """
    analyte_key = normalize_analyte_key(analyte_raw)
    if analyte_key not in ANALYTE_SPECS:
        raise ValueError(f"Unknown analyte '{analyte_raw}'. Allowed: {sorted(ANALYTE_SPECS)}")

    spec = ANALYTE_SPECS[analyte_key]
    target = spec.qc_target
    sd = spec.qc_sd

    # Track per-instrument history so rules never mix two different instruments.
    z_history: dict[str, list[float]] = {}
    rule_counts: Counter[str] = Counter()
    points: list[dict[str, Any]] = []
    out_of_control_count = 0
    warning_count = 0

    for row in rows:
        inst = row["instrument_id"]
        val = float(row["value"])
        z = round((val - target) / sd, 3)

        hist = z_history.setdefault(inst, [])
        hist.append(z)

        violated: list[str] = []
        is_ooc = False
        is_warn = False

        if abs(z) > 3.0:
            violated.append("1-3s")
            is_ooc = True
        elif abs(z) > 2.0:
            violated.append("1-2s")
            is_warn = True

        if len(hist) >= 2:
            prev_z = hist[-2]
            if (z > 2.0 and prev_z > 2.0) or (z < -2.0 and prev_z < -2.0):
                violated.append("2-2s")
                is_ooc = True

        if len(hist) >= 8:
            window = hist[-8:]
            if all(x > 0 for x in window) or all(x < 0 for x in window):
                violated.append("8-x")
                is_ooc = True

        for rule in violated:
            rule_counts[rule] += 1

        if is_ooc:
            status = "OUT_OF_CONTROL"
            out_of_control_count += 1
        elif is_warn:
            status = "WARNING"
            warning_count += 1
        else:
            status = "IN_CONTROL"

        points.append(
            {
                "measured_at": row["measured_at"],
                "instrument_id": inst,
                "batch_id": row["batch_id"],
                "sample_id": row["sample_id"],
                "value": val,
                "unit": row["unit"],
                "z_score": z,
                "violated_rules": violated,
                "status": status,
            }
        )

    return {
        "analyte": analyte_key,
        "display_name": spec.display_name,
        "unit": spec.canonical_unit,
        "instrument_id": instrument_id,
        "target_mean": target,
        "target_sd": sd,
        "ucl_3s": round(target + 3.0 * sd, 5),
        "lcl_3s": round(target - 3.0 * sd, 5),
        "uwl_2s": round(target + 2.0 * sd, 5),
        "lwl_2s": round(target - 2.0 * sd, 5),
        "total_qc_points": len(points),
        "out_of_control_points": out_of_control_count,
        "warning_points": warning_count,
        "drift_detected": out_of_control_count > 0,
        "rule_violation_counts": dict(sorted(rule_counts.items())),
        "points": points,
    }


def compute_control_chart(
    db: LabDatabase,
    analyte_raw: str,
    instrument_id: str | None = None,
) -> dict[str, Any]:
    """Fetch QC standard rows from SQLite and evaluate control chart."""
    analyte_key = normalize_analyte_key(analyte_raw)
    if analyte_key not in ANALYTE_SPECS:
        raise ValueError(f"Unknown analyte '{analyte_raw}'. Allowed: {sorted(ANALYTE_SPECS)}")
    rows = db.fetch_qc_series(analyte=analyte_key, instrument_id=instrument_id)
    return evaluate_control_series(analyte_key, rows, instrument_id=instrument_id)


def compute_drift_alerts(db: LabDatabase) -> dict[str, Any]:
    """Scan all instrument-analyte QC series and summarize drift severity."""
    pairs = db.list_qc_instrument_analyte_pairs()
    alerts: list[dict[str, Any]] = []
    critical_count = 0
    warning_count = 0

    for inst, analyte in pairs:
        chart = compute_control_chart(db, analyte, instrument_id=inst)
        pts = chart["points"]
        if not pts:
            continue

        ooc_pts = [p for p in pts if p["status"] == "OUT_OF_CONTROL"]
        warn_pts = [p for p in pts if p["status"] == "WARNING"]
        triggered_rules = sorted(chart["rule_violation_counts"].keys())

        first_violation = (
            ooc_pts[0]["measured_at"]
            if ooc_pts
            else (warn_pts[0]["measured_at"] if warn_pts else None)
        )

        # Look at recent tail (last 5 points) to distinguish active drift vs isolated warning
        recent_ooc = any(p["status"] == "OUT_OF_CONTROL" for p in pts[-5:])
        if ooc_pts and recent_ooc:
            severity = "CRITICAL"
            critical_count += 1
        elif ooc_pts or len(warn_pts) >= 2:
            severity = "WARNING"
            warning_count += 1
        else:
            severity = "OK"

        alerts.append(
            {
                "instrument_id": inst,
                "analyte": analyte,
                "display_name": chart["display_name"],
                "unit": chart["unit"],
                "total_qc_points": len(pts),
                "out_of_control_points": len(ooc_pts),
                "latest_z_score": pts[-1]["z_score"],
                "first_violation_at": first_violation,
                "triggered_rules": triggered_rules,
                "severity": severity,
            }
        )

    severity_rank = {"CRITICAL": 0, "WARNING": 1, "OK": 2}
    alerts.sort(key=lambda a: (severity_rank[a["severity"]], -a["out_of_control_points"], a["instrument_id"]))

    return {
        "total_monitored_pairs": len(alerts),
        "active_critical_alerts": critical_count,
        "active_warning_alerts": warning_count,
        "alerts": alerts,
    }


def evaluate_batch_release(db: LabDatabase, batch_id: str) -> dict[str, Any] | None:
    """Decide whether a production batch can be APPROVED, HELD, or REJECTED.

    Decision logic:
      1. REJECTED if any analyte reading falls outside regulatory/spec limits.
      2. HOLD_FOR_REVIEW if all readings are within spec, but the instrument used
         was out of statistical QC control at or immediately before the batch timestamp.
      3. APPROVED if all analytes are within spec and instruments were in control.
    """
    aggregates = db.fetch_batch_production_aggregates(batch_id)
    if not aggregates:
        return None

    checks: list[dict[str, Any]] = []
    reasons: list[str] = []
    any_out_of_spec = False
    any_instrument_ooc = False

    # Cache control charts per (analyte, instrument_id)
    chart_cache: dict[tuple[str, str], dict[str, Any]] = {}

    for agg in aggregates:
        analyte = agg["analyte"]
        inst = agg["instrument_id"]
        spec = ANALYTE_SPECS[analyte]
        within_spec = (agg["min_value"] >= spec.spec_min) and (agg["max_value"] <= spec.spec_max)
        if not within_spec:
            any_out_of_spec = True
            reasons.append(
                f"{spec.display_name} on {inst} out of spec "
                f"(observed [{agg['min_value']}, {agg['max_value']}] {spec.canonical_unit}; "
                f"allowed [{spec.spec_min}, {spec.spec_max}] {spec.canonical_unit})."
            )

        key = (analyte, inst)
        if key not in chart_cache:
            chart_cache[key] = compute_control_chart(db, analyte, instrument_id=inst)

        # Find latest QC point for this instrument at or before this batch's measurement time
        batch_ts = agg["latest_measured_at"]
        prior_qc = [p for p in chart_cache[key]["points"] if p["measured_at"] <= batch_ts]
        instrument_in_control = True
        active_rules: list[str] = []
        if prior_qc:
            latest_qc = prior_qc[-1]
            if latest_qc["status"] == "OUT_OF_CONTROL":
                instrument_in_control = False
                active_rules = list(latest_qc["violated_rules"])
                any_instrument_ooc = True
                reasons.append(
                    f"Instrument {inst} was OUT_OF_CONTROL for {spec.display_name} "
                    f"(Westgard rules: {', '.join(active_rules)}) at batch run time."
                )

        checks.append(
            {
                "analyte": analyte,
                "display_name": spec.display_name,
                "instrument_id": inst,
                "sample_count": int(agg["sample_count"]),
                "mean_value": float(agg["mean_value"]),
                "max_value": float(agg["max_value"]),
                "min_value": float(agg["min_value"]),
                "unit": agg["unit"],
                "spec_min": spec.spec_min,
                "spec_max": spec.spec_max,
                "within_spec": within_spec,
                "instrument_in_control": instrument_in_control,
                "active_instrument_rules": active_rules,
            }
        )

    if any_out_of_spec:
        verdict = "REJECTED"
    elif any_instrument_ooc:
        verdict = "HOLD_FOR_REVIEW"
    else:
        verdict = "APPROVED"
        reasons.append("All analytes within specification and all instruments in statistical control.")

    return {
        "batch_id": batch_id,
        "verdict": verdict,
        "reasons": reasons,
        "checked_analytes": len(checks),
        "checks": checks,
    }
