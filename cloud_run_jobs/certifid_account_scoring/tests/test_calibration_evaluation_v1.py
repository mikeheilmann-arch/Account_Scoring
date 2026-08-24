from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import sys

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from certifid_account_scoring.pipeline.arr_calibration import (  # noqa: E402
    ARRCalibrationConfig,
    ComparableARRCalibrator,
    ConfigurableFinanceFormula,
    select_clean_new_logo_comparables,
)
from certifid_account_scoring.pipeline.contracts import (  # noqa: E402
    ARRPrediction,
    MCVPrediction,
)
from certifid_account_scoring.pipeline.evaluation import (  # noqa: E402
    acceptance_precision,
    assert_fixture_separation,
    evaluate_novel_negatives,
    fixture_separation_report,
    grouped_time_split,
    population_drift,
    quantile_calibration_metrics,
    reconcile_ids,
    regression_metrics,
    run_arr_backtest,
    run_mcv_backtest,
    source_coverage,
)
from certifid_account_scoring.pipeline.mcv_calibration import (  # noqa: E402
    MCVCalibrationConfig,
    SKLEARN_AVAILABLE,
    LaneSpecificMCVCalibrator,
    anchor_only_baseline,
    office_only_baseline,
)


def _mcv_rows(count_per_lane: int = 36) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    start = date(2023, 1, 1)
    for lane_index, lane in enumerate(("title_escrow", "legal")):
        for index in range(count_per_lane):
            offices = 1 + index % 6
            staff = 3 + (index * 3) % 22
            label = (
                18
                + offices * (28 if lane == "title_escrow" else 12)
                + staff * (1.6 if lane == "title_escrow" else 0.8)
                + (index % 4) * 3
            )
            label_date = start + timedelta(days=(lane_index * count_per_lane + index) * 12)
            anchor_date = label_date - timedelta(days=120 + index % 30)
            rows.append(
                {
                    "account_id": f"{lane[:1]}-{index:03d}",
                    "sellable_unit_id": f"unit-{lane}-{index:03d}",
                    "lane": lane,
                    "mcv_label": label,
                    "label_timestamp": label_date.isoformat(),
                    "operating_office_count": offices,
                    "office_count_low": offices,
                    "office_count_high": offices,
                    "relevant_staff_count": staff,
                    "staff_count_low": max(0, staff - 1),
                    "staff_count_high": staff + 2,
                    "closing_signal_count": 2 + index % 4,
                    "tool_signal_count": index % 3,
                    "evidence_quality_score": 0.70 + (index % 3) * 0.10,
                    "evidence_confidence": "High" if index % 3 else "Medium",
                    "state": ("ga", "nc", "va")[index % 3],
                    "segment": ("core", "strategic")[index % 2],
                    "subtype": "operator" if lane == "title_escrow" else "re_closing_focused",
                    "anchor_mcv": label * (0.85 + (index % 3) * 0.05),
                    "anchor_source": "sales rep" if index % 2 else "closed opportunity",
                    "anchor_timestamp": anchor_date.isoformat(),
                }
            )
    return rows


def _mcv_config(**overrides: object) -> MCVCalibrationConfig:
    values: dict[str, object] = {
        "min_model_rows": 12,
        "n_estimators": 28,
        "min_samples_leaf": 2,
        "outlier_lower_quantile": 0.05,
        "outlier_upper_quantile": 0.95,
    }
    values.update(overrides)
    return MCVCalibrationConfig(**values)


def test_lane_specific_mcv_quantiles_are_ordered_and_use_timestamped_history() -> None:
    rows = _mcv_rows()
    calibrator = LaneSpecificMCVCalibrator(_mcv_config()).fit(rows)
    targets = [
        {
            **rows[5],
            "account_id": "target-title",
            "as_of": "2025-02-01",
            "anchor_timestamp": "2024-02-01",
            "anchor_source": "sales rep",
            "anchor_mcv": 240,
        },
        {
            **rows[-5],
            "account_id": "target-legal",
            "as_of": "2025-02-01",
            "anchor_timestamp": "2024-04-01",
            "anchor_source": "closed opportunity",
            "anchor_mcv": 90,
        },
    ]
    predictions = calibrator.predict(targets)

    assert SKLEARN_AVAILABLE
    assert all(isinstance(prediction, MCVPrediction) for prediction in predictions)
    assert {prediction.lane for prediction in predictions} == {"title_escrow", "legal"}
    for prediction in predictions:
        assert prediction.external_p10 <= prediction.external_p50 <= prediction.external_p90
        assert prediction.history_p10 <= prediction.history_p50 <= prediction.history_p90
        assert prediction.published_low <= prediction.published_point <= prediction.published_high
        assert prediction.prediction_mode == "history_assisted"
        assert "history_prior_used" in prediction.reason_codes
    assert calibrator.diagnostics["lanes"]["title_escrow"]["external_model_kind"] == (
        "sklearn_quantile_gradient_boosting"
    )
    assert calibrator.diagnostics["lanes"]["legal"]["external_model_kind"] == (
        "sklearn_quantile_gradient_boosting"
    )


def test_future_or_unsourced_anchor_is_never_used_and_outliers_are_clipped() -> None:
    rows = _mcv_rows()
    rows[0] = {**rows[0], "mcv_label": 100_000}
    calibrator = LaneSpecificMCVCalibrator(_mcv_config()).fit(rows)
    target = {
        **rows[1],
        "account_id": "future-anchor",
        "as_of": "2025-01-01",
        "anchor_timestamp": "2025-02-01",
        "anchor_source": "sales rep",
        "anchor_mcv": 50_000,
    }
    prediction = calibrator.predict([target])[0]

    assert prediction.prediction_mode == "anchor_free_external"
    assert prediction.history_p50 is None
    assert prediction.published_point == prediction.external_p50
    title_diagnostics = calibrator.diagnostics["lanes"]["title_escrow"]
    assert title_diagnostics["clipped_high_count"] > 0
    assert title_diagnostics["target_clip_high"] < 100_000


def test_copied_label_rows_are_rejected_and_sparse_lane_fallback_is_explicit() -> None:
    rows = _mcv_rows(4)
    rows[0] = {**rows[0], "label_copied_to_predictor": True}
    calibrator = LaneSpecificMCVCalibrator(_mcv_config(min_model_rows=20)).fit(rows)

    assert calibrator.diagnostics["excluded"]["copied_label_flag"] == 1
    assert calibrator.diagnostics["lanes"]["title_escrow"]["external_model_kind"] == (
        "empirical_sparse_fallback"
    )


def test_mcv_baselines_and_group_time_backtest_are_anchor_safe() -> None:
    rows = _mcv_rows(45)
    config = _mcv_config()
    split = grouped_time_split(
        rows,
        group_field="sellable_unit_id",
        timestamp_field="label_timestamp",
        holdout_fraction=0.20,
    )
    train_groups = {row["sellable_unit_id"] for row in split.train_rows}
    holdout_groups = {row["sellable_unit_id"] for row in split.holdout_rows}
    assert train_groups.isdisjoint(holdout_groups)
    assert split.diagnostics["group_overlap_count"] == 0

    score_rows = [
        {**row, "as_of": row["label_timestamp"]}
        for row in split.holdout_rows
    ]
    office = office_only_baseline(split.train_rows, score_rows, config)
    anchor = anchor_only_baseline(split.train_rows, score_rows, config)
    assert all(row["office_only_point"] is not None for row in office)
    assert all(row["anchor_only_point"] is not None for row in anchor)

    report = run_mcv_backtest(rows, config=config, holdout_fraction=0.20)
    assert report["split"]["group_overlap_count"] == 0
    assert report["external_anchor_free"]["rows"] > 0
    assert report["external_anchor_free"]["interval_order_violations"] == 0
    assert report["office_only_baseline"]["rows"] > 0
    assert report["anchor_only_baseline"]["rows"] > 0
    assert set(report["diagnostics"]) == {"lane", "segment", "state"}


def _arr_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index in range(30):
        lane = "title_escrow" if index < 20 else "legal"
        rows.append(
            {
                "account_id": f"arr-{index:03d}",
                "sellable_unit_id": f"arr-unit-{index:03d}",
                "first_year_arr": 12_000 + index * 1_600 + (index % 4) * 500,
                "close_date": (date(2023, 1, 1) + timedelta(days=index * 15)).isoformat(),
                "mcv": 30 + index * 8,
                "lane": lane,
                "segment": "core" if index % 2 else "strategic",
                "state": ("ga", "nc", "va")[index % 3],
                "motion": "New Business",
                "clean_new_logo": True,
                "hierarchy_resolved": True,
                "is_renewal": False,
                "is_expansion": False,
            }
        )
    rows.extend(
        [
            {
                **rows[0],
                "account_id": "renewal-row",
                "sellable_unit_id": "renewal-unit",
                "is_renewal": True,
            },
            {
                **rows[1],
                "account_id": "unresolved-row",
                "sellable_unit_id": "unresolved-unit",
                "hierarchy_resolved": False,
            },
            {
                **rows[2],
                "account_id": "duplicate-later",
                "first_year_arr": 900_000,
                "close_date": "2026-01-01",
            },
        ]
    )
    return rows


def test_arr_clean_cohort_shrinkage_references_and_provisional_semantics() -> None:
    rows = _arr_rows()
    config = ARRCalibrationConfig(minimum_comparables=5, maximum_comparable_references=7)
    selected, selection = select_clean_new_logo_comparables(rows, config)
    assert len(selected) == 30
    assert selection["excluded"]["renewal_or_expansion"] == 1
    assert selection["excluded"]["not_hierarchy_resolved"] == 1
    assert selection["excluded"]["duplicate_sellable_unit"] == 1
    assert next(row for row in selected if row["sellable_unit_id"] == "arr-unit-002")["account_id"] == "arr-002"

    calibrator = ComparableARRCalibrator(config).fit(rows)
    prediction = calibrator.predict(
        [
            {
                "account_id": "arr-target",
                "mcv": 115,
                "lane": "title_escrow",
                "segment": "core",
                "state": "ga",
            }
        ]
    )[0]
    assert isinstance(prediction, ARRPrediction)
    assert prediction.range_p50 <= prediction.point_p75 <= prediction.range_p90
    assert prediction.comparable_count >= config.minimum_comparables
    assert 0 < len(prediction.comparable_ids) <= config.maximum_comparable_references
    assert prediction.provisional is True
    assert "finance_formula_unavailable_or_unapproved" in prediction.reason_codes
    assert calibrator.diagnostics["clean_comparable_rows"] == 30


def test_arr_approved_finance_formula_clears_provisional_flag() -> None:
    formula = ConfigurableFinanceFormula(
        version="finance_first_year_arr_v7",
        calculator=lambda row, mcv: 9_000 + 180 * mcv,
        approved=True,
    )
    calibrator = ComparableARRCalibrator(
        ARRCalibrationConfig(minimum_comparables=5),
        finance_formula=formula,
    ).fit(_arr_rows())
    prediction = calibrator.predict(
        [{"account_id": "approved", "mcv": 100, "lane": "legal", "segment": "core", "state": "nc"}]
    )[0]
    assert prediction.provisional is False
    assert prediction.finance_formula_version == "finance_first_year_arr_v7"
    assert "finance_formula_blended" in prediction.reason_codes


def test_arr_backtest_reports_quantile_bias_and_mcv_lane_segment_state_diagnostics() -> None:
    config = ARRCalibrationConfig(minimum_comparables=4, maximum_comparable_references=8)
    report = run_arr_backtest(_arr_rows(), config=config, holdout_fraction=0.20)

    assert report["split"]["group_overlap_count"] == 0
    assert report["point_p75"]["rows"] > 0
    assert report["quantile_calibration"]["rows"] > 0
    assert set(report["diagnostics"]) == {"mcv_bucket", "lane", "segment", "state"}
    assert report["model_fit"]["provisional"] is True


def test_independent_fixture_and_novel_negative_controls() -> None:
    training = [
        {"account_id": "train-1", "sellable_unit_id": "unit-1", "registered_domain": "train.example"}
    ]
    fixtures = [
        {"account_id": "blind-1", "sellable_unit_id": "blind-unit", "registered_domain": "blind.example"}
    ]
    assert assert_fixture_separation(training, fixtures)["independent"] is True
    leaked = [{**fixtures[0], "registered_domain": "train.example"}]
    assert fixture_separation_report(training, leaked)["independent"] is False
    with pytest.raises(ValueError, match="fixture leakage"):
        assert_fixture_separation(training, leaked)

    negatives = [
        {"account_id": "n-1", "negative_category": "bank", "novel": True, "accepted": False, "fatal": True},
        {"account_id": "n-2", "negative_category": "government", "novel": True, "accepted": False, "fatal": True},
        {"account_id": "n-3", "negative_category": "generic_host", "novel": True, "accepted": False, "fatal": False},
    ]
    result = evaluate_novel_negatives(negatives)
    assert result["passed"] is True
    assert result["false_accepts"] == 0
    assert set(result["by_category"]) == {"bank", "generic_host", "government"}
    with pytest.raises(ValueError, match="novel=true"):
        evaluate_novel_negatives([{**negatives[0], "novel": False}])


def test_metrics_drift_source_coverage_precision_and_reconciliation() -> None:
    quantile_rows = [
        {"actual": 10, "p50": 8, "p75": 11, "p90": 15},
        {"actual": 20, "p50": 18, "p75": 22, "p90": 28},
        {"actual": 30, "p50": 25, "p75": 31, "p90": 40},
        {"actual": 40, "p50": 35, "p75": 42, "p90": 50},
    ]
    quantiles = quantile_calibration_metrics(
        quantile_rows,
        actual_field="actual",
        p50_field="p50",
        p75_field="p75",
        p90_field="p90",
    )
    assert quantiles["rows"] == 4
    assert quantiles["quantile_order_violations"] == 0
    regression = regression_metrics(
        quantile_rows,
        actual_field="actual",
        point_field="p75",
        low_field="p50",
        high_field="p90",
    )
    assert regression["interval_coverage"] == 1.0

    reference = [{"mcv": index, "lane": "title"} for index in range(1, 31)]
    candidate = [{"mcv": index * 8, "lane": "legal"} for index in range(1, 31)]
    drift = population_drift(reference, candidate, numeric_fields=("mcv",), categorical_fields=("lane",))
    assert drift["status"] == "material_drift"
    assert drift["features"]["mcv"]["psi"] > 0
    assert drift["features"]["lane"]["new_categories"] == ["legal"]

    coverage = source_coverage(
        [
            {"source": "cache", "source_timestamp": "2026-07-01"},
            {"source": "cache", "source_timestamp": "2026-01-01"},
            {"source": "", "source_timestamp": ""},
        ],
        as_of="2026-07-10",
        ttl_days=90,
    )
    assert coverage["fresh_rows"] == 1
    assert coverage["stale_rows"] == 1
    assert coverage["missing_source_rows"] == 1
    assert coverage["missing_timestamp_rows"] == 1

    precision = acceptance_precision(
        [
            {"accepted": True, "is_valid_accept": True, "lane": "title", "confidence": "High"},
            {"accepted": True, "is_valid_accept": False, "fatal_error": True, "lane": "legal", "confidence": "High"},
            {"accepted": False, "is_valid_accept": False, "lane": "legal", "confidence": "Low"},
        ]
    )
    assert precision["overall"]["precision"] == 0.5
    assert precision["overall"]["fatal_false_accepts"] == 1
    assert precision["by_lane"]["title"]["precision"] == 1.0

    reconciled = reconcile_ids(
        [{"account_id": "a"}, {"account_id": "b"}],
        [{"account_id": "a"}, {"account_id": "b"}],
    )
    assert reconciled["passed"] is True
    failed = reconcile_ids(
        [{"account_id": "a"}, {"account_id": "b"}],
        [{"account_id": "a"}, {"account_id": "a"}],
    )
    assert failed["passed"] is False
    assert failed["missing_ids"] == ["b"]
    assert failed["duplicate_output_ids"] == ["a"]
