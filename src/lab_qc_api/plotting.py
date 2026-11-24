"""Publication-grade Matplotlib visualizations rendered directly from API data."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
import io
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt


def _parse_dates(points: list[dict[str, Any]]) -> list[datetime]:
    return [datetime.fromisoformat(p["measured_at"].replace("Z", "+00:00")) for p in points]


def render_control_chart_png(chart: dict[str, Any]) -> bytes:
    """Render a single Shewhart / Westgard control chart as PNG bytes."""
    fig, ax = plt.subplots(figsize=(10.5, 4.8), dpi=150)
    fig.patch.set_facecolor("#FFFFFF")
    ax.set_facecolor("#F8FAFC")

    points = chart["points"]
    by_inst: dict[str, list[dict[str, Any]]] = {}
    for p in points:
        by_inst.setdefault(p["instrument_id"], []).append(p)

    palette = ["#1D4ED8", "#0F766E", "#7C3AED", "#B45309"]

    for idx, (inst, inst_pts) in enumerate(sorted(by_inst.items())):
        dates = _parse_dates(inst_pts)
        vals = [p["value"] for p in inst_pts]
        color = palette[idx % len(palette)]
        ax.plot(dates, vals, linewidth=1.8, color=color, alpha=0.85, label=inst, zorder=2)

        in_ctrl = [(d, p["value"]) for d, p in zip(dates, inst_pts) if p["status"] == "IN_CONTROL"]
        warns = [(d, p["value"]) for d, p in zip(dates, inst_pts) if p["status"] == "WARNING"]
        oocs = [(d, p["value"]) for d, p in zip(dates, inst_pts) if p["status"] == "OUT_OF_CONTROL"]

        if in_ctrl:
            ax.scatter(*zip(*in_ctrl), s=24, color=color, zorder=3)
        if warns:
            ax.scatter(*zip(*warns), s=48, color="#D97706", edgecolor="#FFFFFF", linewidth=0.8, zorder=4)
        if oocs:
            ax.scatter(*zip(*oocs), s=58, color="#DC2626", edgecolor="#FFFFFF", linewidth=0.9, zorder=5)

    # Control limits
    ax.axhline(chart["target_mean"], color="#0F172A", linewidth=1.2, linestyle="-", label=f"Target ({chart['target_mean']})")
    ax.axhline(chart["uwl_2s"], color="#D97706", linewidth=1.0, linestyle="--", alpha=0.85, label="±2σ Warning")
    ax.axhline(chart["lwl_2s"], color="#D97706", linewidth=1.0, linestyle="--", alpha=0.85)
    ax.axhline(chart["ucl_3s"], color="#DC2626", linewidth=1.2, linestyle=":", alpha=0.9, label="±3σ Control")
    ax.axhline(chart["lcl_3s"], color="#DC2626", linewidth=1.2, linestyle=":", alpha=0.9)

    ax.axhspan(chart["lwl_2s"], chart["uwl_2s"], color="#DCFCE7", alpha=0.25, zorder=0)

    inst_suffix = f" ({chart['instrument_id']})" if chart.get("instrument_id") else " (All Instruments)"
    ax.set_title(
        f"Shewhart QC Control Chart — {chart['display_name']}{inst_suffix}",
        fontsize=12.5,
        fontweight="bold",
        color="#0F172A",
        pad=10,
    )
    ax.set_ylabel(f"{chart['display_name']} ({chart['unit']})", fontsize=10, color="#334155")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.grid(True, linestyle="--", alpha=0.35, color="#94A3B8")
    ax.legend(loc="upper left", frameon=True, facecolor="#FFFFFF", framealpha=0.92, fontsize=8.5, ncol=2)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150)
    plt.close(fig)
    return buf.getvalue()


def render_overview_dashboard(
    nitrate_chart: dict[str, Any],
    quality_summary: dict[str, Any],
    batch_verdicts: list[dict[str, Any]],
    output_path: Path,
) -> Path:
    """Render a 3-panel README/portfolio overview directly from live FastAPI responses."""
    fig = plt.figure(figsize=(13.5, 7.6), dpi=180)
    fig.patch.set_facecolor("#FFFFFF")
    gs = fig.add_gridspec(2, 2, height_ratios=[1.25, 1.0], hspace=0.36, wspace=0.24)

    # Top panel (full width): Nitrate Shewhart Control Chart comparing ICP-OES-01 vs ICP-OES-02
    ax1 = fig.add_subplot(gs[0, :])
    ax1.set_facecolor("#F8FAFC")

    points = nitrate_chart["points"]
    by_inst: dict[str, list[dict[str, Any]]] = {}
    for p in points:
        by_inst.setdefault(p["instrument_id"], []).append(p)

    inst_styles = {
        "ICP-OES-01": ("#0F766E", "ICP-OES-01 (In Control)"),
        "ICP-OES-02": ("#1D4ED8", "ICP-OES-02 (Calibration Drift)"),
    }

    first_ooc_date = None
    first_ooc_val = None
    for inst, inst_pts in sorted(by_inst.items()):
        color, label = inst_styles.get(inst, ("#475569", inst))
        dates = _parse_dates(inst_pts)
        vals = [p["value"] for p in inst_pts]
        ax1.plot(dates, vals, linewidth=2.0, color=color, alpha=0.9, label=label, zorder=2)

        in_ctrl = [(d, p["value"]) for d, p in zip(dates, inst_pts) if p["status"] == "IN_CONTROL"]
        warns = [(d, p["value"]) for d, p in zip(dates, inst_pts) if p["status"] == "WARNING"]
        oocs = [(d, p["value"]) for d, p in zip(dates, inst_pts) if p["status"] == "OUT_OF_CONTROL"]

        if in_ctrl:
            ax1.scatter(*zip(*in_ctrl), s=22, color=color, zorder=3)
        if warns:
            ax1.scatter(*zip(*warns), s=48, color="#D97706", edgecolor="#FFFFFF", linewidth=0.8, zorder=4)
        if oocs:
            ax1.scatter(*zip(*oocs), s=56, color="#DC2626", edgecolor="#FFFFFF", linewidth=0.9, zorder=5)
            if first_ooc_date is None:
                first_ooc_date, first_ooc_val = oocs[0]

    ax1.axhline(nitrate_chart["target_mean"], color="#0F172A", linewidth=1.2, label="QC Target (25.0 mg/L)")
    ax1.axhline(nitrate_chart["uwl_2s"], color="#D97706", linewidth=1.1, linestyle="--", label="±2σ Warning (26.2 / 23.8)")
    ax1.axhline(nitrate_chart["lwl_2s"], color="#D97706", linewidth=1.1, linestyle="--")
    ax1.axhline(nitrate_chart["ucl_3s"], color="#DC2626", linewidth=1.2, linestyle=":", label="±3σ Control (26.8 / 23.2)")
    ax1.axhline(nitrate_chart["lcl_3s"], color="#DC2626", linewidth=1.2, linestyle=":")
    ax1.axhspan(nitrate_chart["lwl_2s"], nitrate_chart["uwl_2s"], color="#DCFCE7", alpha=0.30, zorder=0)

    # Legend proxy for OOC points
    ax1.scatter([], [], s=56, color="#DC2626", edgecolor="#FFFFFF", linewidth=0.9, label="Westgard Violation (1-3s / 2-2s / 8-x)")

    if first_ooc_date is not None and first_ooc_val is not None:
        ax1.annotate(
            "Westgard drift detected\n(8-x / 2-2s / 1-3s)",
            xy=(first_ooc_date, first_ooc_val),
            xytext=(-130, 26),
            textcoords="offset points",
            fontsize=8.8,
            fontweight="bold",
            color="#991B1B",
            bbox=dict(boxstyle="round,pad=0.35", facecolor="#FEE2E2", edgecolor="#DC2626", lw=0.9),
            arrowprops=dict(arrowstyle="->", color="#DC2626", lw=1.2),
        )

    ax1.set_title(
        "1. Live Shewhart & Westgard QC Monitoring — Nitrate (NO3-) via GET /api/v1/analytics/control-chart/nitrate",
        fontsize=11.5,
        fontweight="bold",
        color="#0F172A",
        loc="left",
        pad=8,
    )
    ax1.set_ylabel("Nitrate (mg/L)", fontsize=10, color="#334155")
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax1.grid(True, linestyle="--", alpha=0.35, color="#94A3B8")
    ax1.legend(loc="upper left", frameon=True, facecolor="#FFFFFF", framealpha=0.95, fontsize=8.3, ncol=3)

    # Bottom-left panel: Quarantine breakdown by data-contract error code
    ax2 = fig.add_subplot(gs[1, 0])
    ax2.set_facecolor("#F8FAFC")
    q_map = quality_summary["quarantine_by_error_code"]
    short_names = {
        "INCOMPATIBLE_UNIT": "Incompatible Unit",
        "OUT_OF_PHYSICAL_BOUNDS": "Out of Physical Bounds",
        "MISSING_REQUIRED_FIELD": "Missing Required Field",
        "UNKNOWN_ANALYTE": "Unknown Analyte",
        "INVALID_TIMESTAMP": "Invalid Timestamp",
        "INVALID_VALUE_TYPE": "Invalid Value Type",
    }
    labels = [short_names.get(k, k.replace("_", " ").title()) for k in q_map.keys()]
    counts = list(q_map.values())
    y_pos = list(range(len(labels)))

    bars = ax2.barh(y_pos, counts, color="#E11D48", alpha=0.86, height=0.58)
    ax2.set_yticks(y_pos)
    ax2.set_yticklabels(labels, fontsize=9.0, color="#1E293B")
    ax2.invert_yaxis()
    ax2.set_xlabel("Quarantined Records", fontsize=9.5, color="#334155")
    max_cnt = max(counts or [1])
    ax2.set_xlim(0, max_cnt + 1)
    ax2.set_xticks(range(0, max_cnt + 2))
    ax2.grid(True, axis="x", linestyle="--", alpha=0.35, color="#94A3B8")
    for bar, cnt in zip(bars, counts):
        ax2.text(cnt + 0.1, bar.get_y() + bar.get_height() / 2, str(cnt), va="center", fontsize=9.2, fontweight="bold", color="#0F172A")

    ax2.set_title(
        f"2. Ingestion Quarantine Breakdown ({quality_summary['total_quarantined']} rejected / {quality_summary['total_valid_stored']} stored)",
        fontsize=10.5,
        fontweight="bold",
        color="#0F172A",
        loc="left",
        pad=8,
    )

    # Bottom-right panel: Automated Batch Release Verdicts across 60 production batches
    ax3 = fig.add_subplot(gs[1, 1])
    ax3.set_facecolor("#F8FAFC")
    verdict_counts = Counter(v["verdict"] for v in batch_verdicts)
    verdict_order = ["APPROVED", "HOLD_FOR_REVIEW", "REJECTED"]
    verdict_labels = ["Approved\n(In Spec & In Control)", "Hold for Review\n(Instrument Drift)", "Rejected\n(Out of Spec)"]
    verdict_colors = ["#16A34A", "#D97706", "#DC2626"]
    verdict_vals = [verdict_counts.get(k, 0) for k in verdict_order]

    v_bars = ax3.bar(verdict_labels, verdict_vals, color=verdict_colors, alpha=0.88, width=0.52)
    ax3.set_ylabel("Production Batches", fontsize=9.5, color="#334155")
    ax3.set_ylim(0, max(verdict_vals or [1]) * 1.22)
    ax3.grid(True, axis="y", linestyle="--", alpha=0.35, color="#94A3B8")
    ax3.tick_params(axis="x", labelsize=8.6)
    for bar, cnt in zip(v_bars, verdict_vals):
        ax3.text(bar.get_x() + bar.get_width() / 2, cnt + 0.8, str(cnt), ha="center", fontsize=10, fontweight="bold", color="#0F172A")

    ax3.set_title(
        f"3. Automated Batch Release Verdicts ({len(batch_verdicts)} Batches)",
        fontsize=10.5,
        fontweight="bold",
        color="#0F172A",
        loc="left",
        pad=8,
    )

    fig.suptitle(
        "Lab QC API — FastAPI Ingestion Contracts, Statistical Drift Detection & Batch Release",
        fontsize=14,
        fontweight="bold",
        color="#0F172A",
        y=0.985,
    )
    fig.text(
        0.5,
        0.945,
        f"Unit harmonizations: {quality_summary['total_unit_conversions']}  |  "
        f"Idempotent duplicates skipped: {quality_summary['total_duplicates_skipped']}  |  "
        f"Acceptance rate: {quality_summary['overall_acceptance_rate_pct']}%  |  "
        f"Nitrate Westgard alerts: {nitrate_chart['out_of_control_points']}",
        ha="center",
        fontsize=9.2,
        color="#475569",
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.subplots_adjust(top=0.87, bottom=0.09, left=0.16, right=0.97)
    fig.savefig(output_path, dpi=180, bbox_inches="tight", pad_inches=0.25)
    plt.close(fig)
    return output_path
