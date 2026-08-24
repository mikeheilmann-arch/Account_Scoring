"""Comparable-account calibration for pipeline-potential first-year ARR.

The point estimate is the P75 outcome for hierarchy-resolved, clean new-logo
comparables; the published range is P50-P90.  Cohorts shrink toward a broader
lane/global prior when sparse and retain the exact comparable Account IDs used.

Finance's final pricing formula was not available when V1 was implemented.  A
versioned interface is therefore mandatory, and predictions remain explicitly
provisional unless an available, approved formula is supplied.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite, log1p
from typing import Callable, Iterable, Mapping, Protocol, Sequence

import numpy as np

from .config import ARR_MODEL_VERSION, FINANCE_FORMULA_VERSION
from .contracts import ARRPrediction


ARR_QUANTILES = (0.50, 0.75, 0.90)


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _norm(value: object) -> str:
    return _text(value).lower().replace("-", " ").replace("_", " ").strip()


def _float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _bool(value: object, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    text = _norm(value)
    if text in {"1", "true", "t", "yes", "y"}:
        return True
    if text in {"0", "false", "f", "no", "n"}:
        return False
    return default


def _timestamp(value: object) -> datetime:
    text = _text(value)
    if not text:
        return datetime.min.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return datetime.min.replace(tzinfo=UTC)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class FinanceFormula(Protocol):
    """Versioned Finance interface used to blend an approved pricing prior."""

    version: str
    available: bool
    approved: bool

    def estimate_first_year_arr(self, row: Mapping[str, object], mcv: float) -> float | None:
        """Return the Finance-approved first-year ARR for this row and MCV."""


@dataclass(frozen=True)
class ConfigurableFinanceFormula:
    """Callable adapter; ``calculator=None`` is the V1 provisional default."""

    version: str = FINANCE_FORMULA_VERSION
    calculator: Callable[[Mapping[str, object], float], float | None] | None = None
    approved: bool = False

    @property
    def available(self) -> bool:
        return self.calculator is not None

    def estimate_first_year_arr(self, row: Mapping[str, object], mcv: float) -> float | None:
        if self.calculator is None:
            return None
        result = self.calculator(row, mcv)
        parsed = _float(result)
        return parsed if parsed is not None and parsed >= 0 else None


@dataclass(frozen=True)
class ARRCalibrationConfig:
    account_id_field: str = "account_id"
    sellable_unit_field: str = "sellable_unit_id"
    label_field: str = "first_year_arr"
    close_date_field: str = "close_date"
    mcv_field: str = "mcv"
    lane_field: str = "lane"
    segment_field: str = "segment"
    state_field: str = "state"
    motion_field: str = "motion"
    clean_flag_field: str = "clean_new_logo"
    hierarchy_resolved_field: str = "hierarchy_resolved"
    renewal_flag_field: str = "is_renewal"
    expansion_flag_field: str = "is_expansion"
    allowed_new_logo_motions: tuple[str, ...] = (
        "new business",
        "new logo",
        "new_logo",
    )
    minimum_comparables: int = 8
    maximum_comparable_references: int = 20
    mcv_ratio_low: float = 0.50
    mcv_ratio_high: float = 2.00
    shrinkage_strength: float = 8.0
    finance_weight: float = 0.25
    outlier_lower_quantile: float = 0.01
    outlier_upper_quantile: float = 0.99
    model_version: str = ARR_MODEL_VERSION

    def __post_init__(self) -> None:
        if self.minimum_comparables < 1:
            raise ValueError("minimum_comparables must be positive")
        if self.maximum_comparable_references < 1:
            raise ValueError("maximum_comparable_references must be positive")
        if not 0 < self.mcv_ratio_low <= 1 <= self.mcv_ratio_high:
            raise ValueError("MCV comparable ratio must straddle 1")
        if self.shrinkage_strength < 0:
            raise ValueError("shrinkage_strength cannot be negative")
        if not 0 <= self.finance_weight <= 1:
            raise ValueError("finance_weight must be between zero and one")
        if not 0 <= self.outlier_lower_quantile < self.outlier_upper_quantile <= 1:
            raise ValueError("outlier quantiles must satisfy 0 <= lower < upper <= 1")


@dataclass(frozen=True)
class ComparableCohort:
    key: str
    level: str
    rows: tuple[Mapping[str, object], ...]


def select_clean_new_logo_comparables(
    rows: Iterable[Mapping[str, object]],
    config: ARRCalibrationConfig | None = None,
) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Filter and hierarchy-deduplicate training outcomes.

    If a sellable unit has multiple ostensibly new-logo records, the earliest
    dated outcome is retained.  This prevents renewals or later expansions from
    masquerading as independent first-year comparables.
    """

    cfg = config or ARRCalibrationConfig()
    allowed = {_norm(value) for value in cfg.allowed_new_logo_motions}
    materialized = list(rows)
    excluded = {
        "missing_account_or_unit": 0,
        "invalid_arr": 0,
        "missing_close_date": 0,
        "not_clean_new_logo": 0,
        "not_hierarchy_resolved": 0,
        "renewal_or_expansion": 0,
        "invalid_motion": 0,
        "duplicate_sellable_unit": 0,
    }
    candidates: dict[str, dict[str, object]] = {}
    for source in materialized:
        row = dict(source)
        account_id = _text(row.get(cfg.account_id_field))
        unit_id = _text(row.get(cfg.sellable_unit_field))
        arr = _float(row.get(cfg.label_field))
        if not account_id or not unit_id:
            excluded["missing_account_or_unit"] += 1
            continue
        if arr is None or arr <= 0:
            excluded["invalid_arr"] += 1
            continue
        if _timestamp(row.get(cfg.close_date_field)) == datetime.min.replace(tzinfo=UTC):
            excluded["missing_close_date"] += 1
            continue
        if not _bool(row.get(cfg.clean_flag_field), default=False):
            excluded["not_clean_new_logo"] += 1
            continue
        if not _bool(row.get(cfg.hierarchy_resolved_field), default=False):
            excluded["not_hierarchy_resolved"] += 1
            continue
        if _bool(row.get(cfg.renewal_flag_field)) or _bool(row.get(cfg.expansion_flag_field)):
            excluded["renewal_or_expansion"] += 1
            continue
        if _norm(row.get(cfg.motion_field)) not in allowed:
            excluded["invalid_motion"] += 1
            continue
        row[cfg.label_field] = arr
        existing = candidates.get(unit_id)
        if existing is None:
            candidates[unit_id] = row
            continue
        excluded["duplicate_sellable_unit"] += 1
        if _timestamp(row.get(cfg.close_date_field)) < _timestamp(existing.get(cfg.close_date_field)):
            candidates[unit_id] = row

    selected = sorted(
        candidates.values(),
        key=lambda row: (
            _timestamp(row.get(cfg.close_date_field)),
            _text(row.get(cfg.account_id_field)),
        ),
    )
    diagnostics = {
        "input_rows": len(materialized),
        "clean_comparable_rows": len(selected),
        "unique_sellable_units": len(candidates),
        "excluded": excluded,
    }
    return selected, diagnostics


class ComparableARRCalibrator:
    """P50/P75/P90 comparable estimator with hierarchical shrinkage."""

    def __init__(
        self,
        config: ARRCalibrationConfig | None = None,
        finance_formula: FinanceFormula | None = None,
    ) -> None:
        self.config = config or ARRCalibrationConfig()
        self.finance_formula: FinanceFormula = finance_formula or ConfigurableFinanceFormula()
        self._rows: list[dict[str, object]] = []
        self._fit_diagnostics: dict[str, object] = {}
        self._global_quantiles: tuple[float, float, float] | None = None
        self._lane_quantiles: dict[str, tuple[float, float, float]] = {}

    def fit(self, rows: Iterable[Mapping[str, object]]) -> "ComparableARRCalibrator":
        selected, diagnostics = select_clean_new_logo_comparables(rows, self.config)
        if selected:
            values = np.asarray([float(row[self.config.label_field]) for row in selected])
            low, high = np.quantile(
                values,
                [self.config.outlier_lower_quantile, self.config.outlier_upper_quantile],
                method="linear",
            )
            for row in selected:
                raw = float(row[self.config.label_field])
                row["__robust_arr"] = float(np.clip(raw, low, high))
            self._global_quantiles = self._quantiles(selected)
            self._lane_quantiles = {}
            for lane in sorted({_norm(row.get(self.config.lane_field)) for row in selected}):
                lane_rows = [row for row in selected if _norm(row.get(self.config.lane_field)) == lane]
                self._lane_quantiles[lane] = self._quantiles(lane_rows)
            diagnostics.update(
                {
                    "arr_clip_low": float(low),
                    "arr_clip_high": float(high),
                    "clipped_low_count": int(np.sum(values < low)),
                    "clipped_high_count": int(np.sum(values > high)),
                }
            )
        else:
            self._global_quantiles = None
            self._lane_quantiles = {}
        self._rows = selected
        diagnostics.update(
            {
                "finance_formula_version": self.finance_formula.version,
                "finance_formula_available": self.finance_formula.available,
                "finance_formula_approved": self.finance_formula.approved,
                "provisional": not (self.finance_formula.available and self.finance_formula.approved),
                "model_version": self.config.model_version,
                "numpy_version": np.__version__,
            }
        )
        self._fit_diagnostics = diagnostics
        return self

    @property
    def diagnostics(self) -> dict[str, object]:
        return self._fit_diagnostics.copy()

    def predict(self, rows: Iterable[Mapping[str, object]]) -> list[ARRPrediction]:
        return [self._predict_one(row) for row in rows]

    def predict_dicts(self, rows: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
        return [prediction.to_dict() for prediction in self.predict(rows)]

    def _predict_one(self, row: Mapping[str, object]) -> ARRPrediction:
        cfg = self.config
        account_id = _text(row.get(cfg.account_id_field))
        mcv = _float(row.get(cfg.mcv_field))
        provisional = not (self.finance_formula.available and self.finance_formula.approved)
        if not self._rows or self._global_quantiles is None:
            return self._empty(account_id, "no_valid_clean_new_logo_comparables", provisional)
        if mcv is None or mcv < 0:
            return self._empty(account_id, "missing_mcv", provisional)

        cohort = self._choose_cohort(row, mcv)
        if not cohort.rows:
            return self._empty(account_id, "no_valid_comparable_cohort", provisional)
        local = self._quantiles(cohort.rows)
        lane = _norm(row.get(cfg.lane_field))
        prior = self._lane_quantiles.get(lane, self._global_quantiles)
        count = len(cohort.rows)
        weight = count / (count + cfg.shrinkage_strength) if cfg.shrinkage_strength else 1.0
        shrunk = tuple(weight * local[index] + (1.0 - weight) * prior[index] for index in range(3))

        formula_point = self.finance_formula.estimate_first_year_arr(row, mcv)
        if formula_point is not None and self.finance_formula.available and self.finance_formula.approved:
            p50, p75, p90 = shrunk
            point = (1.0 - cfg.finance_weight) * p75 + cfg.finance_weight * formula_point
            # Preserve empirical dispersion around the Finance-approved point.
            spread_low = max(0.0, p75 - p50)
            spread_high = max(0.0, p90 - p75)
            p75 = point
            p50 = max(0.0, point - spread_low)
            p90 = point + spread_high
            shrunk = (p50, p75, p90)

        ordered = sorted(max(0.0, float(value)) for value in shrunk)
        reference_rows = sorted(
            cohort.rows,
            key=lambda candidate: (
                abs(log1p(max(0.0, _float(candidate.get(cfg.mcv_field)) or 0.0)) - log1p(mcv)),
                _text(candidate.get(cfg.account_id_field)),
            ),
        )
        comparable_ids = tuple(
            _text(candidate.get(cfg.account_id_field))
            for candidate in reference_rows[: cfg.maximum_comparable_references]
        )
        reasons = ["clean_new_logo_hierarchy_resolved_comparables"]
        if weight < 1:
            reasons.append("empirical_bayes_shrinkage")
        if provisional:
            reasons.append("finance_formula_unavailable_or_unapproved")
        else:
            reasons.append("finance_formula_blended")
        return ARRPrediction(
            account_id=account_id,
            point_p75=round(ordered[1], 2),
            range_p50=round(ordered[0], 2),
            range_p90=round(ordered[2], 2),
            comparable_count=count,
            comparable_ids=comparable_ids,
            cohort_key=cohort.key,
            shrinkage_level=cohort.level,
            finance_formula_version=self.finance_formula.version,
            model_version=cfg.model_version,
            provisional=provisional,
            reason_codes=tuple(reasons),
        )

    def _choose_cohort(self, target: Mapping[str, object], mcv: float) -> ComparableCohort:
        cfg = self.config
        lane = _norm(target.get(cfg.lane_field))
        segment = _norm(target.get(cfg.segment_field))
        state = _norm(target.get(cfg.state_field))
        levels = (
            ("lane_segment_state", (lane, segment, state)),
            ("lane_segment", (lane, segment)),
            ("lane_state", (lane, state)),
            ("lane", (lane,)),
            ("global", ()),
        )
        for level, values in levels:
            base = [row for row in self._rows if self._matches(row, level, values)]
            ratio_rows = [
                row
                for row in base
                if self._mcv_in_ratio(_float(row.get(cfg.mcv_field)), mcv)
            ]
            if len(ratio_rows) >= cfg.minimum_comparables:
                return ComparableCohort(
                    key="|".join((level, *values, "mcv_ratio")),
                    level=level,
                    rows=tuple(ratio_rows),
                )

        # If exact cohorts are sparse, use the nearest hierarchy-resolved rows
        # within the lane, then global.  This is explicit fallback, not a silent
        # widening of the requested cohort.
        pool = [row for row in self._rows if _norm(row.get(cfg.lane_field)) == lane]
        level = "lane_nearest_mcv_fallback"
        if len(pool) < cfg.minimum_comparables:
            pool = list(self._rows)
            level = "global_nearest_mcv_fallback"
        nearest = sorted(
            pool,
            key=lambda row: (
                abs(log1p(max(0.0, _float(row.get(cfg.mcv_field)) or 0.0)) - log1p(mcv)),
                _text(row.get(cfg.account_id_field)),
            ),
        )[: max(cfg.minimum_comparables, min(len(pool), cfg.maximum_comparable_references))]
        return ComparableCohort(key=level, level=level, rows=tuple(nearest))

    def _matches(self, row: Mapping[str, object], level: str, values: Sequence[str]) -> bool:
        cfg = self.config
        if level == "global":
            return True
        fields = {
            "lane": (cfg.lane_field,),
            "lane_segment": (cfg.lane_field, cfg.segment_field),
            "lane_state": (cfg.lane_field, cfg.state_field),
            "lane_segment_state": (cfg.lane_field, cfg.segment_field, cfg.state_field),
        }[level]
        return all(_norm(row.get(field)) == value for field, value in zip(fields, values))

    def _mcv_in_ratio(self, candidate_mcv: float | None, target_mcv: float) -> bool:
        if candidate_mcv is None or candidate_mcv < 0:
            return False
        if target_mcv == 0:
            return candidate_mcv == 0
        ratio = candidate_mcv / target_mcv
        return self.config.mcv_ratio_low <= ratio <= self.config.mcv_ratio_high

    def _quantiles(self, rows: Sequence[Mapping[str, object]]) -> tuple[float, float, float]:
        values = np.asarray(
            [float(row.get("__robust_arr", row[self.config.label_field])) for row in rows],
            dtype=float,
        )
        return tuple(float(value) for value in np.quantile(values, ARR_QUANTILES, method="linear"))

    def _empty(self, account_id: str, reason: str, provisional: bool) -> ARRPrediction:
        return ARRPrediction(
            account_id=account_id,
            point_p75=None,
            range_p50=None,
            range_p90=None,
            comparable_count=0,
            comparable_ids=(),
            cohort_key="",
            shrinkage_level="unavailable",
            finance_formula_version=self.finance_formula.version,
            model_version=self.config.model_version,
            provisional=provisional,
            reason_codes=(reason,),
        )


def calibration_diagnostics(calibrator: ComparableARRCalibrator) -> dict[str, object]:
    """Stable functional adapter for artifact builders."""

    return calibrator.diagnostics
