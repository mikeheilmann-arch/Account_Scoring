"""Independent evaluation utilities for the Account Scoring V1 shadow.

This module contains no production decision branches and does not load fixture
files.  Callers pass external fixture rows explicitly, which allows repository
tests and independent auditors to keep labels outside decision code.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite, sqrt
from statistics import median
from typing import Iterable, Mapping, Sequence

import numpy as np

from .config import EVALUATION_VERSION


DEFAULT_MCV_BUCKETS = (0.0, 25.0, 50.0, 100.0, 200.0, 500.0, 1_000.0, float("inf"))


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _norm(value: object) -> str:
    return _text(value).lower().strip()


def _float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return _norm(value) in {"1", "true", "t", "yes", "y", "accepted", "score_now", "publish_value"}


def _timestamp(value: object) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class GroupTimeSplit:
    train_rows: tuple[Mapping[str, object], ...]
    holdout_rows: tuple[Mapping[str, object], ...]
    purged_rows: tuple[Mapping[str, object], ...]
    cutoff: str
    diagnostics: dict[str, object]


def grouped_time_split(
    rows: Iterable[Mapping[str, object]],
    *,
    group_field: str = "sellable_unit_id",
    account_id_field: str = "account_id",
    timestamp_field: str = "label_timestamp",
    holdout_fraction: float = 0.20,
) -> GroupTimeSplit:
    """Build a strict, latest-period holdout without related-entity leakage.

    Groups spanning the time cutoff are purged, as are rows with missing time.
    This is intentionally more conservative than random ``GroupShuffleSplit``.
    """

    if not 0 < holdout_fraction < 1:
        raise ValueError("holdout_fraction must be between zero and one")
    materialized = list(rows)
    dated = [(row, _timestamp(row.get(timestamp_field))) for row in materialized]
    valid_dates = sorted(timestamp for _, timestamp in dated if timestamp is not None)
    if len(valid_dates) < 2:
        raise ValueError("at least two timestamped rows are required for a time-aware split")
    cutoff_index = min(
        len(valid_dates) - 1,
        max(1, int(np.floor(len(valid_dates) * (1.0 - holdout_fraction)))),
    )
    cutoff = valid_dates[cutoff_index]

    grouped: dict[str, list[tuple[Mapping[str, object], datetime | None]]] = defaultdict(list)
    for row, timestamp in dated:
        group = _text(row.get(group_field)) or _text(row.get(account_id_field))
        if not group:
            group = f"__missing_group_{len(grouped)}"
        grouped[group].append((row, timestamp))

    train: list[Mapping[str, object]] = []
    holdout: list[Mapping[str, object]] = []
    purged: list[Mapping[str, object]] = []
    train_groups: set[str] = set()
    holdout_groups: set[str] = set()
    purged_groups: set[str] = set()
    missing_time_rows = 0
    for group, members in grouped.items():
        times = [timestamp for _, timestamp in members if timestamp is not None]
        missing_time_rows += sum(timestamp is None for _, timestamp in members)
        if len(times) != len(members):
            purged.extend(row for row, _ in members)
            purged_groups.add(group)
        elif max(times) < cutoff:
            train.extend(row for row, _ in members)
            train_groups.add(group)
        elif min(times) >= cutoff:
            holdout.extend(row for row, _ in members)
            holdout_groups.add(group)
        else:
            purged.extend(row for row, _ in members)
            purged_groups.add(group)

    if not train or not holdout:
        raise ValueError(
            "strict group/time split produced an empty partition; provide more periods or adjust holdout_fraction"
        )
    overlap = train_groups & holdout_groups
    diagnostics = {
        "evaluation_version": EVALUATION_VERSION,
        "input_rows": len(materialized),
        "train_rows": len(train),
        "holdout_rows": len(holdout),
        "purged_rows": len(purged),
        "train_groups": len(train_groups),
        "holdout_groups": len(holdout_groups),
        "purged_groups": len(purged_groups),
        "missing_timestamp_rows": missing_time_rows,
        "group_overlap_count": len(overlap),
        "strict_group_time_partition": True,
        "point_in_time_feature_validity": "must_be_asserted_by_caller",
    }
    return GroupTimeSplit(
        train_rows=tuple(train),
        holdout_rows=tuple(holdout),
        purged_rows=tuple(purged),
        cutoff=cutoff.isoformat(),
        diagnostics=diagnostics,
    )


# Backwards-readable alias used in design notes.
group_time_split = grouped_time_split


def fixture_separation_report(
    training_rows: Iterable[Mapping[str, object]],
    fixture_rows: Iterable[Mapping[str, object]],
    *,
    identity_fields: Sequence[str] = ("account_id", "sellable_unit_id", "registered_domain"),
) -> dict[str, object]:
    """Report exact normalized identity overlap between model data and fixtures."""

    training = list(training_rows)
    fixtures = list(fixture_rows)
    overlap_by_field: dict[str, list[str]] = {}
    for field in identity_fields:
        train_values = {_norm(row.get(field)) for row in training if _norm(row.get(field))}
        fixture_values = {_norm(row.get(field)) for row in fixtures if _norm(row.get(field))}
        overlap_by_field[field] = sorted(train_values & fixture_values)
    overlap_count = sum(len(values) for values in overlap_by_field.values())
    return {
        "evaluation_version": EVALUATION_VERSION,
        "training_rows": len(training),
        "fixture_rows": len(fixtures),
        "identity_fields": list(identity_fields),
        "overlap_by_field": overlap_by_field,
        "overlap_value_count": overlap_count,
        "independent": overlap_count == 0,
    }


def assert_fixture_separation(
    training_rows: Iterable[Mapping[str, object]],
    fixture_rows: Iterable[Mapping[str, object]],
    *,
    identity_fields: Sequence[str] = ("account_id", "sellable_unit_id", "registered_domain"),
) -> dict[str, object]:
    report = fixture_separation_report(training_rows, fixture_rows, identity_fields=identity_fields)
    if not report["independent"]:
        raise ValueError(f"fixture leakage detected: {report['overlap_by_field']}")
    return report


def evaluate_novel_negatives(
    rows: Iterable[Mapping[str, object]],
    *,
    accepted_field: str = "accepted",
    category_field: str = "negative_category",
    novel_field: str = "novel",
    fatal_field: str = "fatal",
    account_id_field: str = "account_id",
    require_novel_marker: bool = True,
) -> dict[str, object]:
    """Measure false acceptance on independently supplied adversarial negatives."""

    materialized = list(rows)
    if require_novel_marker and any(not _bool(row.get(novel_field)) for row in materialized):
        raise ValueError("every novel-negative fixture must carry novel=true")
    accepted = [row for row in materialized if _bool(row.get(accepted_field))]
    fatal_accepted = [row for row in accepted if _bool(row.get(fatal_field))]
    by_category: dict[str, dict[str, object]] = {}
    for category in sorted({_norm(row.get(category_field)) or "unknown" for row in materialized}):
        group = [row for row in materialized if (_norm(row.get(category_field)) or "unknown") == category]
        failures = [row for row in group if _bool(row.get(accepted_field))]
        by_category[category] = {
            "rows": len(group),
            "false_accepts": len(failures),
            "false_accept_rate": round(len(failures) / len(group), 6) if group else None,
        }
    return {
        "evaluation_version": EVALUATION_VERSION,
        "rows": len(materialized),
        "false_accepts": len(accepted),
        "false_accept_rate": round(len(accepted) / len(materialized), 6) if materialized else None,
        "fatal_false_accepts": len(fatal_accepted),
        "false_accept_ids": [_text(row.get(account_id_field)) for row in accepted],
        "passed": len(accepted) == 0,
        "by_category": by_category,
    }


def _bucket(value: float, boundaries: Sequence[float]) -> int:
    for index in range(len(boundaries) - 1):
        if boundaries[index] <= value < boundaries[index + 1]:
            return index
    return max(0, len(boundaries) - 2)


def _pinball(actual: np.ndarray, predicted: np.ndarray, quantile: float) -> float:
    residual = actual - predicted
    return float(np.mean(np.maximum(quantile * residual, (quantile - 1.0) * residual)))


def regression_metrics(
    rows: Iterable[Mapping[str, object]],
    *,
    actual_field: str,
    point_field: str,
    low_field: str | None = None,
    high_field: str | None = None,
    bucket_boundaries: Sequence[float] = DEFAULT_MCV_BUCKETS,
) -> dict[str, object]:
    """Error, bias, bucket accuracy, and optional interval coverage."""

    valid: list[tuple[float, float, float | None, float | None]] = []
    for row in rows:
        actual = _float(row.get(actual_field))
        point = _float(row.get(point_field))
        if actual is None or point is None:
            continue
        low = _float(row.get(low_field)) if low_field else None
        high = _float(row.get(high_field)) if high_field else None
        valid.append((actual, point, low, high))
    if not valid:
        return {"rows": 0, "status": "no_evaluable_rows"}

    actual = np.asarray([item[0] for item in valid])
    point = np.asarray([item[1] for item in valid])
    error = point - actual
    absolute = np.abs(error)
    nonzero = actual != 0
    smape_denominator = np.abs(actual) + np.abs(point)
    smape_mask = smape_denominator != 0
    bucket_hits = [
        _bucket(item[0], bucket_boundaries) == _bucket(item[1], bucket_boundaries)
        for item in valid
    ]
    interval_rows = [item for item in valid if item[2] is not None and item[3] is not None]
    metrics: dict[str, object] = {
        "rows": len(valid),
        "mae": round(float(np.mean(absolute)), 6),
        "median_absolute_error": round(float(np.median(absolute)), 6),
        "rmse": round(float(sqrt(float(np.mean(error**2)))), 6),
        "bias": round(float(np.mean(error)), 6),
        "median_bias": round(float(np.median(error)), 6),
        "mape": round(float(np.mean(np.abs(error[nonzero] / actual[nonzero]))), 6) if np.any(nonzero) else None,
        "smape": round(
            float(np.mean(2.0 * absolute[smape_mask] / smape_denominator[smape_mask])), 6
        )
        if np.any(smape_mask)
        else None,
        "bucket_accuracy": round(float(np.mean(bucket_hits)), 6),
        "overprediction_rate": round(float(np.mean(error > 0)), 6),
        "underprediction_rate": round(float(np.mean(error < 0)), 6),
        "interval_rows": len(interval_rows),
    }
    if interval_rows:
        coverage = [float(low) <= actual_value <= float(high) for actual_value, _, low, high in interval_rows]
        widths = [float(high) - float(low) for _, _, low, high in interval_rows]
        violations = [low > high for _, _, low, high in interval_rows]
        metrics.update(
            {
                "interval_coverage": round(float(np.mean(coverage)), 6),
                "mean_interval_width": round(float(np.mean(widths)), 6),
                "interval_order_violations": int(sum(violations)),
            }
        )
    return metrics


def quantile_calibration_metrics(
    rows: Iterable[Mapping[str, object]],
    *,
    actual_field: str,
    p50_field: str,
    p75_field: str,
    p90_field: str,
) -> dict[str, object]:
    """Quantile calibration for potential ARR or any P50/P75/P90 outcome."""

    valid: list[tuple[float, float, float, float]] = []
    order_violations = 0
    for row in rows:
        values = tuple(
            _float(row.get(field)) for field in (actual_field, p50_field, p75_field, p90_field)
        )
        if any(value is None for value in values):
            continue
        actual, p50, p75, p90 = (float(value) for value in values)
        order_violations += int(not (p50 <= p75 <= p90))
        valid.append((actual, p50, p75, p90))
    if not valid:
        return {"rows": 0, "status": "no_evaluable_rows"}
    data = np.asarray(valid, dtype=float)
    actual = data[:, 0]
    output: dict[str, object] = {"rows": len(valid), "quantile_order_violations": order_violations}
    for index, quantile in enumerate((0.50, 0.75, 0.90), start=1):
        prediction = data[:, index]
        output[f"p{int(quantile * 100)}_observed_below_rate"] = round(
            float(np.mean(actual <= prediction)), 6
        )
        output[f"p{int(quantile * 100)}_calibration_error"] = round(
            float(np.mean(actual <= prediction) - quantile), 6
        )
        output[f"p{int(quantile * 100)}_pinball_loss"] = round(
            _pinball(actual, prediction, quantile), 6
        )
    output["p50_p90_coverage"] = round(float(np.mean((actual >= data[:, 1]) & (actual <= data[:, 3]))), 6)
    output["p75_bias"] = round(float(np.mean(data[:, 2] - actual)), 6)
    return output


def segmented_regression_diagnostics(
    rows: Iterable[Mapping[str, object]],
    *,
    segment_fields: Sequence[str] = ("lane", "segment", "state"),
    actual_field: str,
    point_field: str,
    low_field: str | None = None,
    high_field: str | None = None,
    bucket_boundaries: Sequence[float] = DEFAULT_MCV_BUCKETS,
) -> dict[str, list[dict[str, object]]]:
    materialized = list(rows)
    output: dict[str, list[dict[str, object]]] = {}
    for field in segment_fields:
        summaries: list[dict[str, object]] = []
        groups = sorted({_text(row.get(field)) or "__missing__" for row in materialized})
        for group in groups:
            subset = [row for row in materialized if (_text(row.get(field)) or "__missing__") == group]
            summary = regression_metrics(
                subset,
                actual_field=actual_field,
                point_field=point_field,
                low_field=low_field,
                high_field=high_field,
                bucket_boundaries=bucket_boundaries,
            )
            summaries.append({"group": group, **summary})
        output[field] = summaries
    return output


def _wilson_lower(successes: int, total: int, z: float = 1.959963984540054) -> float | None:
    if total == 0:
        return None
    p = successes / total
    denominator = 1 + z * z / total
    center = p + z * z / (2 * total)
    margin = z * sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return max(0.0, (center - margin) / denominator)


def acceptance_precision(
    rows: Iterable[Mapping[str, object]],
    *,
    accepted_field: str = "accepted",
    truth_field: str = "is_valid_accept",
    lane_field: str = "lane",
    confidence_field: str = "confidence",
    fatal_field: str = "fatal_error",
) -> dict[str, object]:
    """Accepted-row precision overall and by lane/confidence."""

    materialized = list(rows)
    accepted = [row for row in materialized if _bool(row.get(accepted_field))]

    def summarize(group: Sequence[Mapping[str, object]]) -> dict[str, object]:
        correct = sum(_bool(row.get(truth_field)) for row in group)
        fatal = sum(
            _bool(row.get(fatal_field)) and not _bool(row.get(truth_field)) for row in group
        )
        total = len(group)
        return {
            "accepted_rows": total,
            "correct_accepts": correct,
            "false_accepts": total - correct,
            "fatal_false_accepts": fatal,
            "precision": round(correct / total, 6) if total else None,
            "precision_wilson_95_lower": round(_wilson_lower(correct, total), 6) if total else None,
        }

    by_lane: dict[str, object] = {}
    for lane in sorted({_text(row.get(lane_field)) or "__missing__" for row in accepted}):
        by_lane[lane] = summarize(
            [row for row in accepted if (_text(row.get(lane_field)) or "__missing__") == lane]
        )
    by_confidence: dict[str, object] = {}
    for confidence in sorted({_text(row.get(confidence_field)) or "__missing__" for row in accepted}):
        by_confidence[confidence] = summarize(
            [
                row
                for row in accepted
                if (_text(row.get(confidence_field)) or "__missing__") == confidence
            ]
        )
    return {
        "evaluation_version": EVALUATION_VERSION,
        "population_rows": len(materialized),
        "overall": summarize(accepted),
        "by_lane": by_lane,
        "by_confidence": by_confidence,
    }


def _psi(reference: np.ndarray, candidate: np.ndarray, epsilon: float = 1e-6) -> float:
    reference = np.maximum(reference, epsilon)
    candidate = np.maximum(candidate, epsilon)
    reference = reference / np.sum(reference)
    candidate = candidate / np.sum(candidate)
    return float(np.sum((candidate - reference) * np.log(candidate / reference)))


def population_drift(
    reference_rows: Iterable[Mapping[str, object]],
    candidate_rows: Iterable[Mapping[str, object]],
    *,
    numeric_fields: Sequence[str] = (),
    categorical_fields: Sequence[str] = (),
    bins: int = 10,
) -> dict[str, object]:
    """Population stability index and missingness drift by feature."""

    reference = list(reference_rows)
    candidate = list(candidate_rows)
    if not reference or not candidate:
        raise ValueError("reference and candidate populations must both be non-empty")
    feature_reports: dict[str, dict[str, object]] = {}
    for field in numeric_fields:
        ref_values = np.asarray([value for row in reference if (value := _float(row.get(field))) is not None])
        cand_values = np.asarray([value for row in candidate if (value := _float(row.get(field))) is not None])
        if len(ref_values) == 0 or len(cand_values) == 0:
            psi = None
        else:
            quantile_edges = np.unique(np.quantile(ref_values, np.linspace(0, 1, bins + 1)))
            if len(quantile_edges) < 2:
                quantile_edges = np.asarray([-np.inf, np.inf])
            else:
                quantile_edges[0] = -np.inf
                quantile_edges[-1] = np.inf
            ref_counts, _ = np.histogram(ref_values, bins=quantile_edges)
            cand_counts, _ = np.histogram(cand_values, bins=quantile_edges)
            psi = _psi(ref_counts.astype(float), cand_counts.astype(float))
        feature_reports[field] = {
            "kind": "numeric",
            "psi": None if psi is None else round(psi, 6),
            "reference_missing_rate": round(1 - len(ref_values) / len(reference), 6),
            "candidate_missing_rate": round(1 - len(cand_values) / len(candidate), 6),
            "reference_median": round(float(np.median(ref_values)), 6) if len(ref_values) else None,
            "candidate_median": round(float(np.median(cand_values)), 6) if len(cand_values) else None,
        }
    for field in categorical_fields:
        ref_counter = Counter(_text(row.get(field)) or "__missing__" for row in reference)
        cand_counter = Counter(_text(row.get(field)) or "__missing__" for row in candidate)
        categories = sorted(set(ref_counter) | set(cand_counter))
        ref_counts = np.asarray([ref_counter[category] for category in categories], dtype=float)
        cand_counts = np.asarray([cand_counter[category] for category in categories], dtype=float)
        ref_proportions = ref_counts / np.sum(ref_counts)
        cand_proportions = cand_counts / np.sum(cand_counts)
        feature_reports[field] = {
            "kind": "categorical",
            "psi": round(_psi(ref_counts, cand_counts), 6),
            "total_variation": round(float(0.5 * np.sum(np.abs(cand_proportions - ref_proportions))), 6),
            "reference_unique": len(ref_counter),
            "candidate_unique": len(cand_counter),
            "new_categories": sorted(set(cand_counter) - set(ref_counter)),
        }
    psi_values = [
        float(report["psi"])
        for report in feature_reports.values()
        if report.get("psi") is not None
    ]
    maximum = max(psi_values, default=0.0)
    status = "stable" if maximum < 0.10 else "review" if maximum < 0.25 else "material_drift"
    return {
        "evaluation_version": EVALUATION_VERSION,
        "reference_rows": len(reference),
        "candidate_rows": len(candidate),
        "maximum_psi": round(maximum, 6),
        "status": status,
        "features": feature_reports,
    }


def source_coverage(
    rows: Iterable[Mapping[str, object]],
    *,
    source_field: str = "source",
    timestamp_field: str = "source_timestamp",
    as_of: str | datetime,
    ttl_days: int,
) -> dict[str, object]:
    """Coverage/freshness diagnostics for any evidence source."""

    if ttl_days < 0:
        raise ValueError("ttl_days cannot be negative")
    reference_time = _timestamp(as_of)
    if reference_time is None:
        raise ValueError("as_of must be a valid timestamp")
    materialized = list(rows)
    ages: list[float] = []
    fresh = 0
    stale = 0
    missing_source = 0
    missing_timestamp = 0
    by_source: dict[str, Counter[str]] = defaultdict(Counter)
    for row in materialized:
        source = _text(row.get(source_field))
        observed = _timestamp(row.get(timestamp_field))
        if not source:
            missing_source += 1
            source = "__missing__"
        if observed is None:
            missing_timestamp += 1
            by_source[source]["missing_timestamp"] += 1
            continue
        age = max(0.0, (reference_time - observed).total_seconds() / 86_400)
        ages.append(age)
        if age <= ttl_days:
            fresh += 1
            by_source[source]["fresh"] += 1
        else:
            stale += 1
            by_source[source]["stale"] += 1
    return {
        "evaluation_version": EVALUATION_VERSION,
        "rows": len(materialized),
        "source_present_rows": len(materialized) - missing_source,
        "source_coverage_rate": round((len(materialized) - missing_source) / len(materialized), 6)
        if materialized
        else None,
        "fresh_rows": fresh,
        "stale_rows": stale,
        "missing_source_rows": missing_source,
        "missing_timestamp_rows": missing_timestamp,
        "fresh_rate": round(fresh / len(materialized), 6) if materialized else None,
        "median_age_days": round(float(median(ages)), 3) if ages else None,
        "p90_age_days": round(float(np.quantile(ages, 0.90)), 3) if ages else None,
        "ttl_days": ttl_days,
        "as_of": reference_time.isoformat(),
        "by_source": {source: dict(counts) for source, counts in sorted(by_source.items())},
    }


def reconcile_ids(
    input_rows: Iterable[Mapping[str, object]],
    output_rows: Iterable[Mapping[str, object]],
    *,
    id_field: str = "account_id",
) -> dict[str, object]:
    """Exact full-universe reconciliation including duplicates."""

    inputs = [_text(row.get(id_field)) for row in input_rows]
    outputs = [_text(row.get(id_field)) for row in output_rows]
    input_counts = Counter(inputs)
    output_counts = Counter(outputs)
    input_ids = {value for value in inputs if value}
    output_ids = {value for value in outputs if value}
    duplicate_input = sorted(value for value, count in input_counts.items() if value and count > 1)
    duplicate_output = sorted(value for value, count in output_counts.items() if value and count > 1)
    missing = sorted(input_ids - output_ids)
    unexpected = sorted(output_ids - input_ids)
    blank_inputs = input_counts.get("", 0)
    blank_outputs = output_counts.get("", 0)
    passed = not (duplicate_input or duplicate_output or missing or unexpected or blank_inputs or blank_outputs)
    return {
        "input_rows": len(inputs),
        "output_rows": len(outputs),
        "unique_input_ids": len(input_ids),
        "unique_output_ids": len(output_ids),
        "missing_ids": missing,
        "unexpected_ids": unexpected,
        "duplicate_input_ids": duplicate_input,
        "duplicate_output_ids": duplicate_output,
        "blank_input_ids": blank_inputs,
        "blank_output_ids": blank_outputs,
        "passed": passed,
    }


def run_mcv_backtest(
    rows: Iterable[Mapping[str, object]],
    *,
    config: object,
    holdout_fraction: float = 0.20,
) -> dict[str, object]:
    """Fit/evaluate external, history, office-only, and anchor-only models.

    ``config`` is typed loosely to avoid an import cycle; callers pass an
    ``MCVCalibrationConfig``.  The split is strict group/time and the external
    metrics are always computed from the anchor-free prediction fields.
    """

    from .mcv_calibration import (  # Local import keeps evaluation standalone.
        LaneSpecificMCVCalibrator,
        anchor_only_baseline,
        office_only_baseline,
    )

    materialized = list(rows)
    split = grouped_time_split(
        materialized,
        group_field=config.group_field,
        account_id_field=config.account_id_field,
        timestamp_field=config.label_timestamp_field,
        holdout_fraction=holdout_fraction,
    )
    calibrator = LaneSpecificMCVCalibrator(config).fit(split.train_rows)
    # At historical scoring time the holdout label timestamp is the prediction
    # as-of date; copy it without exposing the label value itself.
    scoring_rows = [
        {**row, config.prediction_as_of_field: row.get(config.label_timestamp_field)}
        for row in split.holdout_rows
    ]
    predictions = calibrator.predict_dicts(scoring_rows)
    by_id = {row["account_id"]: row for row in predictions}
    joined = [
        {**row, **by_id.get(_text(row.get(config.account_id_field)), {})}
        for row in split.holdout_rows
    ]
    external_metrics = regression_metrics(
        joined,
        actual_field=config.label_field,
        point_field="external_p50",
        low_field="external_p10",
        high_field="external_p90",
    )
    history_rows = [row for row in joined if row.get("history_p50") is not None]
    history_metrics = regression_metrics(
        history_rows,
        actual_field=config.label_field,
        point_field="history_p50",
        low_field="history_p10",
        high_field="history_p90",
    )
    office = office_only_baseline(split.train_rows, scoring_rows, config)
    anchor = anchor_only_baseline(split.train_rows, scoring_rows, config)
    office_by_id = {row["account_id"]: row for row in office}
    anchor_by_id = {row["account_id"]: row for row in anchor}
    baseline_joined = [
        {
            **row,
            **office_by_id.get(_text(row.get(config.account_id_field)), {}),
            **anchor_by_id.get(_text(row.get(config.account_id_field)), {}),
        }
        for row in split.holdout_rows
    ]
    office_metrics = regression_metrics(
        baseline_joined,
        actual_field=config.label_field,
        point_field="office_only_point",
    )
    anchor_metrics = regression_metrics(
        baseline_joined,
        actual_field=config.label_field,
        point_field="anchor_only_point",
    )
    comparison = {
        "external_mae_lift_vs_office": _metric_lift(external_metrics, office_metrics, "mae"),
        "history_mae_lift_vs_anchor": _metric_lift(history_metrics, anchor_metrics, "mae"),
    }
    return {
        "evaluation_version": EVALUATION_VERSION,
        "external_model_excludes_anchor_predictors": True,
        "point_in_time_feature_validity": "not_inferred_by_evaluator",
        "split": {"cutoff": split.cutoff, **split.diagnostics},
        "model_fit": calibrator.diagnostics,
        "external_anchor_free": external_metrics,
        "history_assisted": history_metrics,
        "office_only_baseline": office_metrics,
        "anchor_only_baseline": anchor_metrics,
        "comparison": comparison,
        "diagnostics": segmented_regression_diagnostics(
            joined,
            segment_fields=(config.lane_field, config.segment_field, config.state_field),
            actual_field=config.label_field,
            point_field="published_point",
            low_field="published_low",
            high_field="published_high",
        ),
        "predictions": joined,
    }


def _metric_lift(
    candidate: Mapping[str, object],
    baseline: Mapping[str, object],
    metric: str,
) -> float | None:
    candidate_value = _float(candidate.get(metric))
    baseline_value = _float(baseline.get(metric))
    if candidate_value is None or baseline_value is None or baseline_value == 0:
        return None
    return round((baseline_value - candidate_value) / baseline_value, 6)


def run_arr_backtest(
    rows: Iterable[Mapping[str, object]],
    *,
    config: object,
    finance_formula: object | None = None,
    holdout_fraction: float = 0.20,
) -> dict[str, object]:
    """Protected clean-new-logo ARR calibration with cohort diagnostics.

    The holdout is filtered by the same hierarchy/new-logo eligibility contract
    but is never available to ``fit``.  Reported point bias is P75 versus actual;
    quantile calibration separately reports P50/P75/P90 behavior.
    """

    from .arr_calibration import ComparableARRCalibrator, select_clean_new_logo_comparables

    materialized = list(rows)
    split = grouped_time_split(
        materialized,
        group_field=config.sellable_unit_field,
        account_id_field=config.account_id_field,
        timestamp_field=config.close_date_field,
        holdout_fraction=holdout_fraction,
    )
    holdout, holdout_selection = select_clean_new_logo_comparables(split.holdout_rows, config)
    calibrator = ComparableARRCalibrator(config, finance_formula=finance_formula).fit(split.train_rows)
    predictions = calibrator.predict_dicts(holdout)
    by_id = {row["account_id"]: row for row in predictions}
    joined: list[dict[str, object]] = []
    for row in holdout:
        account_id = _text(row.get(config.account_id_field))
        candidate = {**row, **by_id.get(account_id, {})}
        mcv = _float(row.get(config.mcv_field))
        candidate["mcv_bucket"] = _numeric_bucket_label(mcv, DEFAULT_MCV_BUCKETS)
        joined.append(candidate)
    evaluable = [row for row in joined if row.get("point_p75") is not None]
    point_metrics = regression_metrics(
        evaluable,
        actual_field=config.label_field,
        point_field="point_p75",
        low_field="range_p50",
        high_field="range_p90",
        bucket_boundaries=(0.0, 10_000.0, 25_000.0, 50_000.0, 100_000.0, 250_000.0, float("inf")),
    )
    return {
        "evaluation_version": EVALUATION_VERSION,
        "split": {"cutoff": split.cutoff, **split.diagnostics},
        "model_fit": calibrator.diagnostics,
        "holdout_selection": holdout_selection,
        "point_p75": point_metrics,
        "quantile_calibration": quantile_calibration_metrics(
            evaluable,
            actual_field=config.label_field,
            p50_field="range_p50",
            p75_field="point_p75",
            p90_field="range_p90",
        ),
        "diagnostics": segmented_regression_diagnostics(
            evaluable,
            segment_fields=("mcv_bucket", config.lane_field, config.segment_field, config.state_field),
            actual_field=config.label_field,
            point_field="point_p75",
            low_field="range_p50",
            high_field="range_p90",
            bucket_boundaries=(0.0, 10_000.0, 25_000.0, 50_000.0, 100_000.0, 250_000.0, float("inf")),
        ),
        "predictions": joined,
    }


def _numeric_bucket_label(value: float | None, boundaries: Sequence[float]) -> str:
    if value is None:
        return "__missing__"
    index = _bucket(value, boundaries)
    low = boundaries[index]
    high = boundaries[index + 1]
    high_label = "inf" if high == float("inf") else f"{high:g}"
    return f"{low:g}-{high_label}"
