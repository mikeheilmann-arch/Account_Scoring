"""Version and policy configuration for the no-write V1 shadow candidate."""

from __future__ import annotations

from dataclasses import asdict, dataclass


PIPELINE_VERSION = "account_scoring_v1_shadow_20260710"
SOURCE_VERSION = "sfdc_crm_full_prod_20260708_plus_cached_gcp_20260708"
RESOLVER_VERSION = "website_binding_v1_20260710"
SELLABLE_UNIT_VERSION = "sellable_unit_v1_20260710"
FEATURE_VERSION = "entity_safe_features_v1_20260710"
LANE_VERSION = "explicit_icp_lanes_v1_20260710"
MCV_MODEL_VERSION = "lane_mcv_quantile_v1_20260710"
ARR_MODEL_VERSION = "comparable_arr_quantile_v1_provisional_20260710"
EVALUATION_VERSION = "independent_evaluation_v1_20260710"
PUBLICATION_VERSION = "desired_state_no_write_v1_20260710"
FINANCE_FORMULA_VERSION = "pending_finance_approval_v1_interface"


@dataclass(frozen=True)
class V1Policy:
    alta_min_confidence: str = "medium"
    binding_high_threshold: float = 0.86
    binding_medium_threshold: float = 0.68
    history_anchor_half_life_days: int = 365
    anchor_conflict_ratio: float = 2.0
    minimum_arr_comparables: int = 8
    maximum_comparable_references: int = 20
    accepted_confidence: tuple[str, ...] = ("High",)
    first_canary_no_clear: bool = True
    allow_review_numeric_values: bool = False
    allow_hard_clear: bool = False
    evidence_ttl_days: int = 90

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


DEFAULT_POLICY = V1Policy()
