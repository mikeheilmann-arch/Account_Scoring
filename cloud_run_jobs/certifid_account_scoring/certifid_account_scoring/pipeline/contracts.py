"""Typed contracts shared by the Account Scoring V1 stages.

Contracts are deliberately serializable and contain no I/O.  Every decision
records a contract/model version so downstream artifacts cannot silently mix
schemas or decision logic.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


CONTRACT_VERSION = "account_scoring_v1_contract_20260710"


class StrEnum(str, Enum):
    """Python 3.11-compatible string enum with stable CSV/JSON values."""


class Confidence(StrEnum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class BindingStatus(StrEnum):
    BOUND = "bound"
    MISMATCH = "mismatch"
    AMBIGUOUS = "ambiguous"
    NO_WEBSITE = "no_website"
    HYGIENE_REVIEW = "hygiene_review"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class Lifecycle(StrEnum):
    NET_NEW = "net_new"
    ACTIVE_CUSTOMER = "active_customer"
    WINBACK = "winback"
    PARTNER = "partner"
    EXCLUDED = "excluded"
    UNKNOWN = "unknown"


class Lane(StrEnum):
    TITLE_ESCROW = "title_escrow"
    LEGAL = "legal"
    ADJACENT = "adjacent"
    REVIEW = "review"
    INELIGIBLE = "ineligible"


class DesiredOperation(StrEnum):
    PUBLISH_VALUE = "publish_value"
    ROUTE_REVIEW_METADATA_ONLY = "route_review_metadata_only"
    HARD_SUPPRESS_NO_CHANGE = "hard_suppress_no_change"
    NO_CHANGE = "no_change"


def _serialize(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class ContractRecord:
    contract_version: str = field(default=CONTRACT_VERSION, init=False)

    def to_dict(self) -> dict[str, Any]:
        return _serialize(asdict(self))


@dataclass(frozen=True)
class EvidenceCitation(ContractRecord):
    source: str
    reference: str
    observed_at: str = ""
    excerpt: str = ""
    evidence_hash: str = ""


@dataclass(frozen=True)
class WebsiteBinding(ContractRecord):
    account_id: str
    account_name: str
    website: str
    registered_domain: str
    status: BindingStatus
    confidence: Confidence
    entity_class: str
    reason_codes: tuple[str, ...]
    reasons: tuple[str, ...]
    citations: tuple[EvidenceCitation, ...]
    source_as_of: str
    resolver_version: str
    alta_entity_confirmed: bool = False
    alta_used_as_sole_website_proof: bool = False


@dataclass(frozen=True)
class SellableUnitDecision(ContractRecord):
    account_id: str
    sellable_unit_id: str
    surviving_account_id: str
    member_account_ids: tuple[str, ...]
    parent_unit_id: str
    lifecycle: Lifecycle
    relationship_type: str
    confidence: Confidence
    standalone_score_eligible: bool
    suggested_survivor_id: str
    reason_codes: tuple[str, ...]
    resolver_version: str


@dataclass(frozen=True)
class EvidenceFeatures(ContractRecord):
    account_id: str
    operating_office_count: int
    office_count_low: int
    office_count_high: int
    relevant_staff_count: int
    staff_count_low: int
    staff_count_high: int
    operational_service_signals: tuple[str, ...]
    tool_signals: tuple[str, ...]
    evidence_confidence: Confidence
    uncertainty_codes: tuple[str, ...]
    citations: tuple[EvidenceCitation, ...]
    feature_version: str


@dataclass(frozen=True)
class LaneDecision(ContractRecord):
    account_id: str
    lane: Lane
    subtype: str
    eligible_for_value: bool
    confidence: Confidence
    reason_codes: tuple[str, ...]
    reasons: tuple[str, ...]
    citations: tuple[EvidenceCitation, ...]
    lane_version: str


@dataclass(frozen=True)
class MCVPrediction(ContractRecord):
    account_id: str
    lane: str
    external_p10: float | None
    external_p50: float | None
    external_p90: float | None
    history_p10: float | None
    history_p50: float | None
    history_p90: float | None
    published_point: float | None
    published_low: float | None
    published_high: float | None
    prediction_mode: str
    confidence: Confidence
    model_version: str
    training_source_version: str
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ARRPrediction(ContractRecord):
    account_id: str
    point_p75: float | None
    range_p50: float | None
    range_p90: float | None
    comparable_count: int
    comparable_ids: tuple[str, ...]
    cohort_key: str
    shrinkage_level: str
    finance_formula_version: str
    model_version: str
    provisional: bool
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class AccountDecision(ContractRecord):
    account_id: str
    account_name: str
    website: str
    sellable_unit_id: str
    surviving_account_id: str
    binding_status: str
    url_status: str
    lifecycle: str
    lane: str
    lane_subtype: str
    quality_disposition: str
    desired_operation: DesiredOperation
    accepted: bool
    final_mcv: float | None
    final_mcv_low: float | None
    final_mcv_high: float | None
    final_arr: float | None
    final_arr_low: float | None
    final_arr_high: float | None
    confidence: Confidence
    reason_codes: tuple[str, ...]
    evidence_summary: str
    input_fingerprint: str
    evidence_hash: str
    run_id: str
    resolver_version: str
    lane_version: str
    feature_version: str
    mcv_model_version: str
    arr_model_version: str
    source_version: str
    evaluated_at: str
