"""Domain specifications for analytical water/lab quality control."""

from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class AnalyteSpec:
    """Analytical contract, unit conversions, and QC targets for an analyte."""

    name: str
    display_name: str
    canonical_unit: str
    unit_factors: dict[str, float]
    physical_min: float
    physical_max: float
    spec_min: float
    spec_max: float
    qc_target: float
    qc_sd: float


# Regulatory-inspired water analysis specs (e.g., EU Drinking Water Directive thresholds:
# nitrate <= 50 mg/L, iron <= 0.20 mg/L, pH 6.5-8.5, turbidity <= 4.0 NTU).
ANALYTE_SPECS: dict[str, AnalyteSpec] = {
    "ph": AnalyteSpec(
        name="ph",
        display_name="pH",
        canonical_unit="pH",
        unit_factors={"ph": 1.0, "ph units": 1.0, "su": 1.0},
        physical_min=0.0,
        physical_max=14.0,
        spec_min=6.5,
        spec_max=8.5,
        qc_target=7.00,
        qc_sd=0.06,
    ),
    "conductivity": AnalyteSpec(
        name="conductivity",
        display_name="Conductivity",
        canonical_unit="uS/cm",
        unit_factors={
            "us/cm": 1.0,
            "µs/cm": 1.0,
            "μs/cm": 1.0,
            "ms/cm": 1000.0,
        },
        physical_min=0.0,
        physical_max=100_000.0,
        spec_min=0.0,
        spec_max=1000.0,
        qc_target=500.0,
        qc_sd=10.0,
    ),
    "nitrate": AnalyteSpec(
        name="nitrate",
        display_name="Nitrate (NO3-)",
        canonical_unit="mg/L",
        unit_factors={
            "mg/l": 1.0,
            "ppm": 1.0,
            "ug/l": 0.001,
            "µg/l": 0.001,
            "μg/l": 0.001,
            "ppb": 0.001,
        },
        physical_min=0.0,
        physical_max=5000.0,
        spec_min=0.0,
        spec_max=50.0,
        qc_target=25.0,
        qc_sd=0.60,
    ),
    "iron": AnalyteSpec(
        name="iron",
        display_name="Iron (Fe)",
        canonical_unit="mg/L",
        unit_factors={
            "mg/l": 1.0,
            "ppm": 1.0,
            "ug/l": 0.001,
            "µg/l": 0.001,
            "μg/l": 0.001,
            "ppb": 0.001,
        },
        physical_min=0.0,
        physical_max=1000.0,
        spec_min=0.0,
        spec_max=0.20,
        qc_target=0.100,
        qc_sd=0.0035,
    ),
    "turbidity": AnalyteSpec(
        name="turbidity",
        display_name="Turbidity",
        canonical_unit="NTU",
        unit_factors={"ntu": 1.0, "fnu": 1.0},
        physical_min=0.0,
        physical_max=4000.0,
        spec_min=0.0,
        spec_max=4.0,
        qc_target=1.50,
        qc_sd=0.05,
    ),
}

VALID_SAMPLE_TYPES = {"QC_STANDARD", "PRODUCTION_SAMPLE"}


def normalize_analyte_key(analyte: str) -> str:
    """Normalize analyte identifier to lowercase canonical lookup key."""
    return analyte.strip().lower()


def validate_and_normalize_value(
    analyte_raw: str,
    value_raw: float,
    unit_raw: str,
) -> tuple[str | None, float | None, str | None, bool, str | None, str | None]:
    """Validate an analytical reading and convert it to its canonical unit.

    Returns:
        (analyte_key, canonical_value, canonical_unit, was_converted, error_code, error_detail)
    """
    analyte_key = normalize_analyte_key(analyte_raw)
    spec = ANALYTE_SPECS.get(analyte_key)
    if spec is None:
        return (
            None,
            None,
            None,
            False,
            "UNKNOWN_ANALYTE",
            f"Unsupported analyte '{analyte_raw}'. Allowed: {sorted(ANALYTE_SPECS)}",
        )

    if value_raw is None or not isinstance(value_raw, (int, float)) or isinstance(value_raw, bool):
        return (
            None,
            None,
            None,
            False,
            "INVALID_VALUE_TYPE",
            f"Measurement value must be numeric; received {value_raw!r}",
        )

    val = float(value_raw)
    if math.isnan(val) or math.isinf(val):
        return (
            None,
            None,
            None,
            False,
            "NON_FINITE_VALUE",
            f"Measurement value must be finite; received {value_raw!r}",
        )

    unit_key = unit_raw.strip().lower()
    factor = spec.unit_factors.get(unit_key)
    if factor is None:
        return (
            None,
            None,
            None,
            False,
            "INCOMPATIBLE_UNIT",
            f"Unit '{unit_raw}' is not valid for analyte '{analyte_key}'. "
            f"Expected one of {sorted(spec.unit_factors)}",
        )

    canonical_value = round(val * factor, 6)
    was_converted = unit_raw.strip() != spec.canonical_unit

    if canonical_value < spec.physical_min or canonical_value > spec.physical_max:
        return (
            None,
            None,
            None,
            False,
            "OUT_OF_PHYSICAL_BOUNDS",
            f"Normalized value {canonical_value} {spec.canonical_unit} for '{analyte_key}' "
            f"is outside physical bounds [{spec.physical_min}, {spec.physical_max}]",
        )

    return analyte_key, canonical_value, spec.canonical_unit, was_converted, None, None
