"""Lane-specific monthly-closing-volume calibration.

The production estimators in this module deliberately consume row dictionaries
instead of Salesforce objects.  That keeps calibration independent of I/O and
makes the protected holdout reusable by the local shadow runner.

Two models are fitted for every supported lane:

* ``external`` uses only pre-acquisition, entity-bound evidence; and
* ``history`` adds a timestamped, sourced, recency-weighted MCV prior.

Targets are winsorized and modelled on ``log1p(MCV)`` with scikit-learn quantile
gradient boosting.  Sparse lanes fall back to deterministic empirical
quantiles; the office-only and anchor-only functions remain explicit baselines,
not production estimators.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import exp, expm1, isfinite, log1p
from statistics import median
from typing import Any, Iterable, Mapping, Sequence

import numpy as np

from .config import MCV_MODEL_VERSION, SOURCE_VERSION
from .contracts import Confidence, MCVPrediction

try:  # The Cloud Run image must pin scikit-learn before release.
    from sklearn import __version__ as SKLEARN_VERSION
    from sklearn.ensemble import GradientBoostingRegressor
    from sklearn.feature_extraction import DictVectorizer

    SKLEARN_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only in a degraded image.
    SKLEARN_VERSION = "unavailable"
    GradientBoostingRegressor = None  # type: ignore[assignment]
    DictVectorizer = None  # type: ignore[assignment]
    SKLEARN_AVAILABLE = False


SUPPORTED_LANES = ("title_escrow", "legal")
QUANTILES = (0.10, 0.50, 0.90)


def _text(value: object) -> str:
    return "" if value is None else str(value).strip()


def _float(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return parsed if isfinite(parsed) else None


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return _text(value).lower() in {"1", "true", "t", "yes", "y"}


def _timestamp(value: object) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _lane(value: object) -> str:
    normalized = _text(value).lower().replace("-", "_").replace("/", "_").replace(" ", "_")
    aliases = {
        "title": "title_escrow",
        "escrow": "title_escrow",
        "title__escrow": "title_escrow",
        "law": "legal",
        "law_firm": "legal",
    }
    return aliases.get(normalized, normalized)


@dataclass(frozen=True)
class MCVCalibrationConfig:
    """Column mapping and deterministic estimator policy."""

    account_id_field: str = "account_id"
    lane_field: str = "lane"
    label_field: str = "mcv_label"
    label_timestamp_field: str = "label_timestamp"
    prediction_as_of_field: str = "as_of"
    group_field: str = "sellable_unit_id"
    state_field: str = "state"
    segment_field: str = "segment"
    office_field: str = "operating_office_count"
    anchor_value_field: str = "anchor_mcv"
    anchor_source_field: str = "anchor_source"
    anchor_timestamp_field: str = "anchor_timestamp"
    copied_label_flag_field: str = "label_copied_to_predictor"
    supported_lanes: tuple[str, ...] = SUPPORTED_LANES
    numeric_features: tuple[str, ...] = (
        "operating_office_count",
        "office_count_low",
        "office_count_high",
        "relevant_staff_count",
        "staff_count_low",
        "staff_count_high",
        "closing_signal_count",
        "tool_signal_count",
        "evidence_quality_score",
    )
    categorical_features: tuple[str, ...] = (
        "state",
        "segment",
        "subtype",
        "evidence_confidence",
    )
    min_model_rows: int = 24
    outlier_lower_quantile: float = 0.01
    outlier_upper_quantile: float = 0.99
    anchor_half_life_days: float = 365.0
    n_estimators: int = 120
    max_depth: int = 2
    learning_rate: float = 0.035
    min_samples_leaf: int = 5
    random_state: int = 20260710
    model_version: str = MCV_MODEL_VERSION
    training_source_version: str = SOURCE_VERSION

    def __post_init__(self) -> None:
        if self.min_model_rows < 2:
            raise ValueError("min_model_rows must be at least 2")
        if not 0 <= self.outlier_lower_quantile < self.outlier_upper_quantile <= 1:
            raise ValueError("outlier quantiles must satisfy 0 <= lower < upper <= 1")
        if self.anchor_half_life_days <= 0:
            raise ValueError("anchor_half_life_days must be positive")


@dataclass
class _QuantileBundle:
    lane: str
    mode: str
    train_count: int
    raw_target_min: float
    raw_target_max: float
    clip_low: float
    clip_high: float
    clipped_low_count: int
    clipped_high_count: int
    empirical_quantiles: tuple[float, float, float]
    model_kind: str
    vectorizer: Any = None
    models: dict[float, Any] = field(default_factory=dict)

    def predict(self, feature_rows: Sequence[dict[str, object]]) -> list[tuple[float, float, float]]:
        if not feature_rows:
            return []
        if self.model_kind != "sklearn_quantile_gradient_boosting":
            return [self.empirical_quantiles for _ in feature_rows]
        matrix = self.vectorizer.transform(feature_rows)
        raw_by_quantile = [
            np.maximum(0.0, np.expm1(self.models[quantile].predict(matrix)))
            for quantile in QUANTILES
        ]
        output: list[tuple[float, float, float]] = []
        for values in zip(*raw_by_quantile):
            ordered = sorted(float(max(0.0, value)) for value in values)
            output.append((ordered[0], ordered[1], ordered[2]))
        return output


def _history_features(
    row: Mapping[str, object],
    config: MCVCalibrationConfig,
    *,
    training: bool,
) -> dict[str, object] | None:
    """Return a prior only when value, source, and point-in-time timestamp exist."""

    anchor = _float(row.get(config.anchor_value_field))
    source = _text(row.get(config.anchor_source_field)).lower()
    observed_at = _timestamp(row.get(config.anchor_timestamp_field))
    as_of_field = config.label_timestamp_field if training else config.prediction_as_of_field
    as_of = _timestamp(row.get(as_of_field))
    if anchor is None or anchor < 0 or not source or observed_at is None or as_of is None:
        return None
    age_days = (as_of - observed_at).total_seconds() / 86_400
    # A same-time or future prior is indistinguishable from target leakage.
    if age_days <= 0:
        return None
    if training and _truthy(row.get(config.copied_label_flag_field)):
        return None
    recency_weight = exp(-log1p(1.0) * age_days / config.anchor_half_life_days)
    return {
        "history_anchor_log_mcv": log1p(anchor),
        "history_anchor_age_days": age_days,
        "history_anchor_recency_weight": recency_weight,
        "history_anchor_source": source,
    }


def _feature_row(
    row: Mapping[str, object],
    config: MCVCalibrationConfig,
    *,
    history_assisted: bool,
    training: bool,
) -> tuple[dict[str, object], bool]:
    features: dict[str, object] = {}
    for name in config.numeric_features:
        value = _float(row.get(name))
        features[name] = 0.0 if value is None else value
        features[f"{name}__missing"] = 1.0 if value is None else 0.0
    for name in config.categorical_features:
        features[name] = _text(row.get(name)).lower() or "__missing__"
    prior = _history_features(row, config, training=training) if history_assisted else None
    if prior:
        features.update(prior)
        features["history_anchor_available"] = 1.0
    elif history_assisted:
        features.update(
            {
                "history_anchor_log_mcv": 0.0,
                "history_anchor_age_days": 0.0,
                "history_anchor_recency_weight": 0.0,
                "history_anchor_source": "__missing__",
                "history_anchor_available": 0.0,
            }
        )
    return features, prior is not None


def _fit_bundle(
    lane: str,
    mode: str,
    rows: Sequence[Mapping[str, object]],
    config: MCVCalibrationConfig,
) -> _QuantileBundle:
    labels = np.asarray([float(_float(row.get(config.label_field))) for row in rows], dtype=float)
    clip_low, clip_high = np.quantile(
        labels,
        [config.outlier_lower_quantile, config.outlier_upper_quantile],
        method="linear",
    )
    clipped = np.clip(labels, clip_low, clip_high)
    empirical = tuple(float(value) for value in np.quantile(clipped, QUANTILES, method="linear"))
    bundle = _QuantileBundle(
        lane=lane,
        mode=mode,
        train_count=len(rows),
        raw_target_min=float(np.min(labels)),
        raw_target_max=float(np.max(labels)),
        clip_low=float(clip_low),
        clip_high=float(clip_high),
        clipped_low_count=int(np.sum(labels < clip_low)),
        clipped_high_count=int(np.sum(labels > clip_high)),
        empirical_quantiles=empirical,
        model_kind="empirical_sparse_fallback",
    )
    if not SKLEARN_AVAILABLE or len(rows) < config.min_model_rows:
        return bundle

    history_assisted = mode == "history"
    feature_rows = [
        _feature_row(row, config, history_assisted=history_assisted, training=True)[0]
        for row in rows
    ]
    vectorizer = DictVectorizer(sparse=False, sort=True)
    matrix = vectorizer.fit_transform(feature_rows)
    target = np.log1p(clipped)
    models: dict[float, Any] = {}
    for quantile in QUANTILES:
        model = GradientBoostingRegressor(
            loss="quantile",
            alpha=quantile,
            n_estimators=config.n_estimators,
            learning_rate=config.learning_rate,
            max_depth=config.max_depth,
            min_samples_leaf=min(config.min_samples_leaf, max(1, len(rows) // 4)),
            random_state=config.random_state,
        )
        model.fit(matrix, target)
        models[quantile] = model
    bundle.vectorizer = vectorizer
    bundle.models = models
    bundle.model_kind = "sklearn_quantile_gradient_boosting"
    return bundle


class LaneSpecificMCVCalibrator:
    """Fit external and history-assisted quantile estimators per ICP lane."""

    def __init__(self, config: MCVCalibrationConfig | None = None) -> None:
        self.config = config or MCVCalibrationConfig()
        self._external: dict[str, _QuantileBundle] = {}
        self._history: dict[str, _QuantileBundle] = {}
        self._fit_diagnostics: dict[str, object] = {}

    def fit(self, rows: Iterable[Mapping[str, object]]) -> "LaneSpecificMCVCalibrator":
        materialized = list(rows)
        eligible: dict[str, list[Mapping[str, object]]] = {
            lane: [] for lane in self.config.supported_lanes
        }
        excluded = {
            "unsupported_lane": 0,
            "missing_or_negative_label": 0,
            "copied_label_flag": 0,
        }
        for row in materialized:
            lane = _lane(row.get(self.config.lane_field))
            label = _float(row.get(self.config.label_field))
            if lane not in eligible:
                excluded["unsupported_lane"] += 1
                continue
            if label is None or label < 0:
                excluded["missing_or_negative_label"] += 1
                continue
            if _truthy(row.get(self.config.copied_label_flag_field)):
                excluded["copied_label_flag"] += 1
                continue
            eligible[lane].append(row)

        self._external.clear()
        self._history.clear()
        lane_diagnostics: dict[str, object] = {}
        for lane, lane_rows in eligible.items():
            if not lane_rows:
                lane_diagnostics[lane] = {"train_rows": 0, "status": "no_labels"}
                continue
            external = _fit_bundle(lane, "external", lane_rows, self.config)
            # History training retains rows without a valid prior so missingness is
            # learned; prediction uses history only when a valid prior is present.
            history = _fit_bundle(lane, "history", lane_rows, self.config)
            self._external[lane] = external
            self._history[lane] = history
            lane_diagnostics[lane] = {
                "train_rows": len(lane_rows),
                "external_model_kind": external.model_kind,
                "history_model_kind": history.model_kind,
                "target_clip_low": external.clip_low,
                "target_clip_high": external.clip_high,
                "clipped_low_count": external.clipped_low_count,
                "clipped_high_count": external.clipped_high_count,
                "valid_history_prior_rows": sum(
                    _history_features(row, self.config, training=True) is not None for row in lane_rows
                ),
            }
        self._fit_diagnostics = {
            "input_rows": len(materialized),
            "eligible_rows": sum(len(group) for group in eligible.values()),
            "excluded": excluded,
            "lanes": lane_diagnostics,
            "sklearn_available": SKLEARN_AVAILABLE,
            "sklearn_version": SKLEARN_VERSION,
            "numpy_version": np.__version__,
            "model_version": self.config.model_version,
            "training_source_version": self.config.training_source_version,
        }
        return self

    @property
    def diagnostics(self) -> dict[str, object]:
        return self._fit_diagnostics.copy()

    def predict(self, rows: Iterable[Mapping[str, object]]) -> list[MCVPrediction]:
        materialized = list(rows)
        predictions: list[MCVPrediction | None] = [None] * len(materialized)
        by_lane: dict[str, list[tuple[int, Mapping[str, object]]]] = {}
        for index, row in enumerate(materialized):
            by_lane.setdefault(_lane(row.get(self.config.lane_field)), []).append((index, row))

        for lane, indexed_rows in by_lane.items():
            external_bundle = self._external.get(lane)
            history_bundle = self._history.get(lane)
            if external_bundle is None:
                for index, row in indexed_rows:
                    predictions[index] = self._empty_prediction(row, lane, "missing_lane_model")
                continue

            external_features = [
                _feature_row(row, self.config, history_assisted=False, training=False)[0]
                for _, row in indexed_rows
            ]
            external_values = external_bundle.predict(external_features)
            history_features: list[dict[str, object]] = []
            has_prior: list[bool] = []
            for _, row in indexed_rows:
                features, valid_prior = _feature_row(
                    row, self.config, history_assisted=True, training=False
                )
                history_features.append(features)
                has_prior.append(valid_prior)
            history_values = history_bundle.predict(history_features) if history_bundle else external_values

            for position, (index, row) in enumerate(indexed_rows):
                external_p10, external_p50, external_p90 = external_values[position]
                use_history = bool(history_bundle and has_prior[position])
                history_tuple = history_values[position] if use_history else (None, None, None)
                published = history_tuple if use_history else external_values[position]
                office = _float(row.get(self.config.office_field))
                confidence = self._confidence(
                    external_bundle,
                    has_prior=use_history,
                    has_office=office is not None and office > 0,
                )
                reason_codes = [
                    f"external:{external_bundle.model_kind}",
                    "history_prior_used" if use_history else "external_evidence_only",
                ]
                if use_history and history_bundle:
                    reason_codes.append(f"history:{history_bundle.model_kind}")
                    prior = _history_features(row, self.config, training=False)
                    if prior:
                        reason_codes.append(f"history_source:{prior['history_anchor_source']}")
                        reason_codes.append(
                            f"history_age_days:{round(float(prior['history_anchor_age_days']))}"
                        )
                predictions[index] = MCVPrediction(
                    account_id=_text(row.get(self.config.account_id_field)),
                    lane=lane,
                    external_p10=round(external_p10, 3),
                    external_p50=round(external_p50, 3),
                    external_p90=round(external_p90, 3),
                    history_p10=None if history_tuple[0] is None else round(float(history_tuple[0]), 3),
                    history_p50=None if history_tuple[1] is None else round(float(history_tuple[1]), 3),
                    history_p90=None if history_tuple[2] is None else round(float(history_tuple[2]), 3),
                    published_point=round(float(published[1]), 3),
                    published_low=round(float(published[0]), 3),
                    published_high=round(float(published[2]), 3),
                    prediction_mode="history_assisted" if use_history else "anchor_free_external",
                    confidence=confidence,
                    model_version=self.config.model_version,
                    training_source_version=self.config.training_source_version,
                    reason_codes=tuple(reason_codes),
                )
        return [prediction for prediction in predictions if prediction is not None]

    def predict_dicts(self, rows: Iterable[Mapping[str, object]]) -> list[dict[str, object]]:
        return [prediction.to_dict() for prediction in self.predict(rows)]

    def _empty_prediction(
        self,
        row: Mapping[str, object],
        lane: str,
        reason: str,
    ) -> MCVPrediction:
        return MCVPrediction(
            account_id=_text(row.get(self.config.account_id_field)),
            lane=lane,
            external_p10=None,
            external_p50=None,
            external_p90=None,
            history_p10=None,
            history_p50=None,
            history_p90=None,
            published_point=None,
            published_low=None,
            published_high=None,
            prediction_mode="unavailable",
            confidence=Confidence.LOW,
            model_version=self.config.model_version,
            training_source_version=self.config.training_source_version,
            reason_codes=(reason,),
        )

    def _confidence(
        self,
        bundle: _QuantileBundle,
        *,
        has_prior: bool,
        has_office: bool,
    ) -> Confidence:
        if bundle.model_kind == "sklearn_quantile_gradient_boosting" and (has_prior or has_office):
            return Confidence.HIGH
        if has_prior or has_office:
            return Confidence.MEDIUM
        return Confidence.LOW


def office_only_baseline(
    train_rows: Iterable[Mapping[str, object]],
    score_rows: Iterable[Mapping[str, object]],
    config: MCVCalibrationConfig | None = None,
) -> list[dict[str, object]]:
    """Frozen robust baseline: median label by lane and office count.

    Exact-office medians with fewer than three observations shrink to the lane
    median.  No staff, anchor, or post-sale feature is used.
    """

    cfg = config or MCVCalibrationConfig()
    lane_values: dict[str, list[float]] = {}
    office_values: dict[tuple[str, int], list[float]] = {}
    for row in train_rows:
        lane = _lane(row.get(cfg.lane_field))
        label = _float(row.get(cfg.label_field))
        office = _float(row.get(cfg.office_field))
        if lane not in cfg.supported_lanes or label is None or label < 0:
            continue
        lane_values.setdefault(lane, []).append(label)
        if office is not None and office >= 0:
            office_values.setdefault((lane, int(round(office))), []).append(label)

    output: list[dict[str, object]] = []
    for row in score_rows:
        lane = _lane(row.get(cfg.lane_field))
        office = _float(row.get(cfg.office_field))
        lane_group = lane_values.get(lane, [])
        exact = office_values.get((lane, int(round(office))), []) if office is not None else []
        if not lane_group:
            point = None
            source = "no_lane_baseline"
        elif len(exact) >= 3:
            point = float(median(exact))
            source = "lane_office_median"
        else:
            point = float(median(lane_group))
            source = "lane_median_fallback"
        output.append(
            {
                "account_id": _text(row.get(cfg.account_id_field)),
                "lane": lane,
                "office_only_point": point,
                "baseline_source": source,
            }
        )
    return output


def anchor_only_baseline(
    train_rows: Iterable[Mapping[str, object]],
    score_rows: Iterable[Mapping[str, object]],
    config: MCVCalibrationConfig | None = None,
) -> list[dict[str, object]]:
    """Timestamp-safe anchor baseline with a lane-median fallback."""

    cfg = config or MCVCalibrationConfig()
    lane_values: dict[str, list[float]] = {}
    for row in train_rows:
        lane = _lane(row.get(cfg.lane_field))
        label = _float(row.get(cfg.label_field))
        if lane in cfg.supported_lanes and label is not None and label >= 0:
            lane_values.setdefault(lane, []).append(label)

    output: list[dict[str, object]] = []
    for row in score_rows:
        lane = _lane(row.get(cfg.lane_field))
        prior = _history_features(row, cfg, training=False)
        if prior:
            point = expm1(float(prior["history_anchor_log_mcv"]))
            source = "timestamped_anchor"
        elif lane_values.get(lane):
            point = float(median(lane_values[lane]))
            source = "lane_median_fallback"
        else:
            point = None
            source = "no_lane_baseline"
        output.append(
            {
                "account_id": _text(row.get(cfg.account_id_field)),
                "lane": lane,
                "anchor_only_point": point,
                "baseline_source": source,
            }
        )
    return output


def model_diagnostics(calibrator: LaneSpecificMCVCalibrator) -> dict[str, object]:
    """Stable functional adapter for artifact builders."""

    return calibrator.diagnostics
