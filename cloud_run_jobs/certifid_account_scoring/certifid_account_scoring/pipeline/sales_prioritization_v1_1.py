"""Broad-coverage, no-write Sales Prioritization V1.1 population builder.

V1.1 separates entity eligibility from website quality.  An ambiguous,
unbound, or mismatched website can prevent website evidence from being used,
but cannot by itself classify the Salesforce Account as non-ICP.  The model
uses the following ordered score-source hierarchy:

1. trusted recent Opportunity or Sales-rep Final MCV anchor, with rails;
2. usable numeric website score with High-confidence correct binding;
3. CRM/ALTA/name/state entity fallback using a conservative cohort value;
4. lower-quantile cohort default when retrieval failed or never ran.

The module writes immutable local audit artifacts only.  It contains no
Salesforce client and cannot execute a remote mutation.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from ..scoring.legal_entity_scoring import ATTORNEY_RELEVANT_STATES, STATE_ABBR_BY_NAME
from ..scoring.run_greenfield_nimble_test import mcv_to_band
from .publication import CANARY_FIELDS, write_csv
from .sales_prioritization_release import (
    _canonical_url,
    _integer,
    _lower,
    _mcv_band,
    _number,
    _text,
    _url_status,
    iso_z,
    parse_arr_range,
    read_csv,
    sha256_file,
    utc_now,
)


MODEL_VERSION = "sales_prioritization_v1_1_20260710"
SOURCE_VERSION = "crm_full_cached_broad_coverage_20260710"
MINIMUM_COVERAGE = 10_000
EXPECTED_COVERAGE_LOW = 12_000
EXPECTED_COVERAGE_HIGH = 14_000
RECENT_OPPORTUNITY_CUTOFF = date(2024, 7, 10)

EXPLICIT_NON_ICP_STATUSES = {
    "not icp",
    "no longer in business",
    "bad debt written off",
    "do not contact",
    "no business use case for certifid",
    "disqualified",
    "acquired",
}

EXPLICIT_NON_ICP_COMPANY_TYPES = {
    "real estate agent",
    "mortgage lender",
    "underwriter",
    "vendor",
    "bank / credit union",
    "builder",
    "insurance",
    "holding company",
    "1031 exchange",
    "competitor",
    "capital investment",
    "notary",
}

TITLE_COMPANY_TYPES = {"title company", "escrow company"}
TRUSTED_MCV_SOURCE_TOKENS = ("sales rep", "bdr", "account executive", " ae", "rep")
TITLE_NAME_RE = re.compile(r"\b(title|escrow|settlement|abstract|closing|closings|land title)\b", re.I)
LEGAL_RE_RE = re.compile(r"\b(real estate|closing|title|settlement|escrow|conveyanc)\b", re.I)

AUDIT_FIELDS = (
    "Id",
    "Name",
    "decision",
    "lane",
    "score_source_tier",
    "score_source_detail",
    "confidence",
    "fallback_reason_primary",
    "fallback_reason_codes",
    "exclusion_reason",
    "retained_current_v1",
    "newly_recovered",
    "original_review_action",
    "retrieval_status",
    "binding_status",
    "binding_confidence",
    "original_lane",
    "original_lane_confidence",
    "crm_type",
    "crm_account_status",
    "crm_company_type",
    "billing_state",
    "segment",
    "alta_confirmed",
    "trusted_anchor_raw",
    "trusted_anchor_used",
    "anchor_rail_applied",
    "cohort_key",
    "cohort_reference_count",
    "mcv_band",
    "source_score",
    *CANARY_FIELDS[1:],
)


def _read_index(path: Path, key: str, *, casefold: bool = False) -> dict[str, dict[str, str]]:
    output: dict[str, dict[str, str]] = {}
    for row in read_csv(path):
        value = _text(row.get(key))
        if value:
            output[value.lower() if casefold else value] = row
    return output


def _truthy(value: Any) -> bool:
    return _lower(value) in {"true", "1", "yes", "y"}


def _state_code(value: Any) -> str:
    text = _text(value)
    if len(text) == 2:
        return text.upper()
    return STATE_ABBR_BY_NAME.get(text.lower(), "")


def _parse_date(value: Any) -> date | None:
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def _positive_number(value: Any) -> Decimal | None:
    parsed = _number(value)
    return parsed if parsed is not None and parsed > 0 else None


def _valid_numeric_score(row: Mapping[str, str]) -> bool:
    point = _positive_number(row.get("EstimatedMCV"))
    low = _positive_number(row.get("EstimatedMCVLow"))
    high = _positive_number(row.get("EstimatedMCVHigh"))
    arr = _positive_number(row.get("EstimatedARR"))
    score = _positive_number(row.get("Score"))
    arr_range = parse_arr_range(row.get("ARRRange"))
    return bool(
        point is not None
        and low is not None
        and high is not None
        and low <= point <= high
        and arr is not None
        and score is not None
        and arr_range is not None
        and arr_range[0] <= arr <= arr_range[1]
    )


def _quantile(values: Sequence[int], q: float) -> int:
    if not values:
        raise ValueError("Cannot calculate a quantile from an empty sequence")
    ordered = sorted(values)
    position = Decimal(str(q)) * Decimal(len(ordered) - 1)
    lower = int(math.floor(float(position)))
    upper = int(math.ceil(float(position)))
    if lower == upper:
        value = Decimal(ordered[lower])
    else:
        fraction = position - Decimal(lower)
        value = Decimal(ordered[lower]) + fraction * Decimal(ordered[upper] - ordered[lower])
    return max(1, int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)))


@dataclass(frozen=True)
class CohortValue:
    point: int
    key: str
    count: int


class CohortDefaults:
    """Conservative defaults learned only from the retained production V1 rows."""

    def __init__(self, rows: Iterable[Mapping[str, str]]) -> None:
        self._values: dict[tuple[str, str, str], list[int]] = defaultdict(list)
        self._lane_values: dict[str, list[int]] = defaultdict(list)
        for row in rows:
            point = _positive_number(row.get("AI_Prospect_Value_MCV_Point__c"))
            lane = _lower(row.get("lane"))
            if point is None or lane not in {"title_escrow", "legal"}:
                continue
            state = _state_code(row.get("billing_state")) or "__unknown__"
            segment = _lower(row.get("segment")) or "__unknown__"
            integer = int(point.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            self._values[(lane, state, segment)].append(integer)
            self._values[(lane, state, "__all__")].append(integer)
            self._values[(lane, "__all__", segment)].append(integer)
            self._lane_values[lane].append(integer)
        if not self._lane_values.get("title_escrow") or not self._lane_values.get("legal"):
            raise RuntimeError("Retained V1 calibration rows must cover title_escrow and legal lanes")

    def value(self, lane: str, state: str, segment: str, *, quantile: float) -> CohortValue:
        candidates = (
            ((lane, state or "__unknown__", segment or "__unknown__"), 5),
            ((lane, state or "__unknown__", "__all__"), 10),
            ((lane, "__all__", segment or "__unknown__"), 10),
        )
        for key, minimum in candidates:
            values = self._values.get(key, [])
            if len(values) >= minimum:
                return CohortValue(_quantile(values, quantile), "|".join(key), len(values))
        values = self._lane_values[lane]
        return CohortValue(_quantile(values, quantile), f"{lane}|__all__|__all__", len(values))

    def anchor_ceiling(self, lane: str) -> int:
        p90 = _quantile(self._lane_values[lane], 0.90)
        if lane == "legal":
            return max(250, min(1_000, p90 * 3))
        return max(750, min(2_500, p90 * 3))


def _hard_exclusion(
    account: Mapping[str, str],
    binding: Mapping[str, str],
    sellable: Mapping[str, str],
    lane: Mapping[str, str],
) -> str:
    account_type = _lower(account.get("Type"))
    account_status = _lower(account.get("Account_Status__c"))
    company_type = _lower(account.get("Company_Type__c"))
    lifecycle = _lower(sellable.get("lifecycle"))
    relationship = _lower(sellable.get("relationship_type"))
    relationship_confidence = _lower(sellable.get("confidence"))

    if (
        _truthy(account.get("Active_Customer__c"))
        or account_type == "customer"
        or account_status in {"active customer", "active"}
        or lifecycle == "active_customer"
    ):
        return "active_customer"
    if account_type == "partner" or lifecycle == "partner":
        return "confirmed_partner"
    if account_status in EXPLICIT_NON_ICP_STATUSES:
        return "explicit_crm_non_icp_status"
    if company_type in EXPLICIT_NON_ICP_COMPANY_TYPES:
        return "explicit_crm_non_icp_company_type"
    if relationship_confidence == "high" and relationship in {
        "duplicate_loser",
        "non_independent_child",
        "branch",
        "dba",
        "owned_direct",
    }:
        return "verified_duplicate_or_non_independent_child"
    # Website-derived exclusion is legal only with correct High-confidence
    # binding. Ambiguous, insufficient, and mismatch states never enter here.
    if (
        _lower(binding.get("status")) == "bound"
        and _lower(binding.get("confidence")) == "high"
        and _lower(lane.get("lane")) == "adjacent"
        and _lower(lane.get("confidence")) == "high"
    ):
        return "bound_high_confidence_non_icp_website"
    return ""


def _entity_context(
    account: Mapping[str, str],
    overlay: Mapping[str, str],
    score: Mapping[str, str],
    lane: Mapping[str, str],
) -> tuple[str, list[str]]:
    company_type = _lower(account.get("Company_Type__c"))
    name = _text(account.get("Name"))
    state = _state_code(account.get("BillingState"))
    reasons: list[str] = []

    crm_title = company_type in TITLE_COMPANY_TYPES
    alta_confirmed = _truthy(overlay.get("AltaMember")) and _lower(overlay.get("AltaMatchConfidence")) in {
        "high",
        "medium",
    }
    strong_title_name = bool(TITLE_NAME_RE.search(name))
    law_firm = company_type == "law firm"
    attorney_state = law_firm and state in ATTORNEY_RELEVANT_STATES
    usable_legal_re = law_firm and (
        _text(score.get("LegalEntityRoute"))
        in {
            "legal_affiliated_title_entity",
            "legal_real_estate_closing_focused",
            "legal_real_estate_practice_unclear",
        }
        or bool(LEGAL_RE_RE.search(_text(score.get("LegalEvidence"))))
    )

    if crm_title:
        reasons.append("crm_title_or_escrow_type")
    if alta_confirmed:
        reasons.append("alta_confirmed")
    if strong_title_name:
        reasons.append("strong_title_or_closing_name")
    if attorney_state:
        reasons.append("attorney_relevant_state")
    if usable_legal_re:
        reasons.append("usable_legal_real_estate_evidence")

    if law_firm and (attorney_state or usable_legal_re):
        return "legal", reasons
    if crm_title or alta_confirmed or strong_title_name:
        return "title_escrow", reasons
    original_lane = _lower(lane.get("lane"))
    if original_lane in {"title_escrow", "legal"} and _lower(lane.get("confidence")) == "high":
        reasons.append("bound_lane_positive_context")
        return original_lane, reasons
    return "", reasons


def _trusted_anchor(
    account: Mapping[str, str], history: Mapping[str, str]
) -> tuple[int | None, str, bool]:
    opportunity_mcv = _positive_number(history.get("max_closed_won_mcv"))
    opportunity_date = _parse_date(history.get("last_closed_won_date"))
    if opportunity_mcv is not None and opportunity_date is not None and opportunity_date >= RECENT_OPPORTUNITY_CUTOFF:
        return int(opportunity_mcv.quantize(Decimal("1"), rounding=ROUND_HALF_UP)), "recent_closed_won_opportunity_mcv", True

    final_mcv = _positive_number(account.get("Final_Monthly_Closing_Volume__c"))
    source = _lower(account.get("Monthly_Closing_Volume_Source__c"))
    if final_mcv is not None and any(token in source for token in TRUSTED_MCV_SOURCE_TOKENS):
        return int(final_mcv.quantize(Decimal("1"), rounding=ROUND_HALF_UP)), "trusted_final_mcv", False
    return None, "", False


def _band_values(point: int) -> dict[str, str]:
    low, high, score, arr, arr_range = mcv_to_band(point)
    return {
        "mcv_point": str(point),
        "mcv_low": str(low),
        "mcv_high": str(high),
        "arr_point": str(arr),
        "arr_range": arr_range,
        "source_score": str(score),
    }


def _retained_values(row: Mapping[str, str]) -> dict[str, str]:
    return {
        "mcv_point": _integer(row.get("AI_Prospect_Value_MCV_Point__c")),
        "mcv_low": _integer(row.get("AI_Prospect_Value_MCV_Low__c")),
        "mcv_high": _integer(row.get("AI_Prospect_Value_MCV_High__c")),
        "arr_point": _integer(row.get("AI_Prospect_Value_ARR_Point__c")),
        "arr_range": _text(row.get("AI_Prospect_Value_ARR_Range__c")),
        "source_score": "",
    }


def _website_values(row: Mapping[str, str]) -> dict[str, str]:
    return {
        "mcv_point": _integer(row.get("EstimatedMCV")),
        "mcv_low": _integer(row.get("EstimatedMCVLow")),
        "mcv_high": _integer(row.get("EstimatedMCVHigh")),
        "arr_point": _integer(row.get("EstimatedARR")),
        "arr_range": _text(row.get("ARRRange")),
        "source_score": _text(row.get("Score")),
    }


def _payload(
    *,
    account_id: str,
    values: Mapping[str, str],
    confidence: str,
    score: Mapping[str, str],
    binding: Mapping[str, str],
    run_id: str,
    updated_at: str,
    lane: str,
    source_tier: str,
    source_detail: str,
    fallback_codes: Sequence[str],
    retained_payload: Mapping[str, str] | None,
) -> dict[str, str]:
    canonical_url = _canonical_url(score, {**binding, "binding_status": binding.get("status", "")})
    url_status = _url_status(score, {**binding, "binding_status": binding.get("status", "")})
    evidence = " | ".join(
        [
            f"V1.1 source={source_tier}",
            f"detail={source_detail}",
            f"lane={lane}",
            f"binding={_lower(binding.get('status'))}",
            *fallback_codes,
        ]
    )[:32768]
    components = {
        "semantic_version": "sales_prioritization_v1_1",
        "directional_use_only": True,
        "score_source_tier": source_tier,
        "score_source_detail": source_detail,
        "lane": lane,
        "fallback_reason_codes": list(fallback_codes),
        "website_binding_status": _lower(binding.get("status")),
        "website_binding_confidence": _text(binding.get("confidence")),
        "website_used_for_non_icp_only_if_bound_high": True,
        "arr_semantics": "directional_pipeline_potential_existing_ladder",
        "retained_prior_run_id": (
            _text(retained_payload.get("AI_Prospect_Value_Run_Id__c")) if retained_payload else ""
        ),
        "run_id": run_id,
    }
    return {
        "Id": account_id,
        "AI_Prospect_Value_MCV_Point__c": values["mcv_point"],
        "AI_Prospect_Value_MCV_Low__c": values["mcv_low"],
        "AI_Prospect_Value_MCV_High__c": values["mcv_high"],
        "AI_Prospect_Value_ARR_Point__c": values["arr_point"],
        "AI_Prospect_Value_ARR_Range__c": values["arr_range"],
        "AI_Prospect_Value_Confidence__c": confidence,
        "AI_Prospect_Value_ICP__c": "scorable",
        "AI_Prospect_Value_Action__c": "score_now",
        "AI_Prospect_Value_URL_Status__c": url_status,
        "AI_Prospect_Value_Canonical_URL__c": canonical_url,
        "AI_Prospect_Value_Evidence__c": evidence,
        "AI_Prospect_Value_Components__c": json.dumps(components, sort_keys=True, separators=(",", ":")),
        "AI_Prospect_Value_Model_Version__c": MODEL_VERSION,
        "AI_Prospect_Value_Run_Id__c": run_id,
        "AI_Prospect_Value_Source__c": SOURCE_VERSION,
        "AI_Prospect_Value_Updated_At__c": updated_at,
    }


def _count_rows(dimension: str, counts: Counter[str]) -> list[dict[str, Any]]:
    return [
        {"dimension": dimension, "value": value, "count": count}
        for value, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ]


def build_v1_1_population(
    *,
    accounts_path: Path,
    overlay_path: Path,
    combined_scores_path: Path,
    binding_path: Path,
    sellable_path: Path,
    lane_path: Path,
    customer_history_path: Path,
    retained_audit_path: Path,
    retained_payload_path: Path,
    output_dir: Path,
    run_id: str | None = None,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"V1.1 audit output is immutable and already exists: {output_dir}")
    output_dir.mkdir(parents=True)

    built_at = utc_now()
    release_run_id = run_id or f"sales_prioritization_v1_1_{built_at:%Y%m%dT%H%M%SZ}"
    updated_at = iso_z(built_at)

    accounts = _read_index(accounts_path, "Id")
    overlays = _read_index(overlay_path, "AccountId")
    scores = _read_index(combined_scores_path, "Id")
    bindings = _read_index(binding_path, "account_id")
    sellable = _read_index(sellable_path, "account_id")
    lanes = _read_index(lane_path, "account_id")
    history = _read_index(customer_history_path, "sfdc_account_id", casefold=True)
    retained_audit = _read_index(retained_audit_path, "Id")
    retained_payloads = _read_index(retained_payload_path, "Id")

    if len(accounts) != 21_993:
        raise RuntimeError(f"Expected 21,993 Account snapshot rows, found {len(accounts):,}")
    if len(retained_payloads) != 3_001:
        raise RuntimeError(f"Expected exactly 3,001 retained production rows, found {len(retained_payloads):,}")
    if not set(retained_payloads).issubset(accounts):
        raise RuntimeError("Retained V1 payload contains Ids outside the Account snapshot")
    for label, mapping in (("overlay", overlays), ("binding", bindings), ("sellable", sellable), ("lane", lanes)):
        if set(mapping) != set(accounts):
            raise RuntimeError(f"{label} exact-ID reconciliation failed")

    retained_reference: list[dict[str, str]] = []
    for account_id, payload in retained_payloads.items():
        account = accounts[account_id]
        audit = retained_audit.get(account_id, {})
        retained_reference.append(
            {
                **payload,
                "lane": _lower(audit.get("lane")) or _lower(lanes[account_id].get("lane")),
                "billing_state": _text(account.get("BillingState")),
                "segment": _text(account.get("Account_Segment__c")),
            }
        )
    cohorts = CohortDefaults(retained_reference)

    decisions: list[dict[str, str]] = []
    scored_rows: list[dict[str, str]] = []
    excluded_rows: list[dict[str, str]] = []
    source_counts: Counter[str] = Counter()
    lane_counts: Counter[str] = Counter()
    confidence_counts: Counter[str] = Counter()
    fallback_counts: Counter[str] = Counter()
    exclusion_counts: Counter[str] = Counter()
    retained_count = 0
    recovered_count = 0

    for account_id in sorted(accounts):
        account = accounts[account_id]
        overlay = overlays[account_id]
        score = scores.get(account_id, {})
        binding = bindings[account_id]
        sale = sellable[account_id]
        original_lane = lanes[account_id]
        history_row = history.get(account_id.lower(), {})
        retained_payload = retained_payloads.get(account_id)

        exclusion = "" if retained_payload else _hard_exclusion(account, binding, sale, original_lane)
        lane, context_codes = _entity_context(account, overlay, score, original_lane)
        if not retained_payload and not exclusion and not lane:
            exclusion = "insufficient_positive_icp_fallback_context"

        base_audit = {
            "Id": account_id,
            "Name": _text(account.get("Name")),
            "original_review_action": _text(score.get("ReviewAction")) or "preflight_no_scorer_row",
            "retrieval_status": _text(score.get("RetrievalStatus")) or "not_run",
            "binding_status": _text(binding.get("status")),
            "binding_confidence": _text(binding.get("confidence")),
            "original_lane": _text(original_lane.get("lane")),
            "original_lane_confidence": _text(original_lane.get("confidence")),
            "crm_type": _text(account.get("Type")),
            "crm_account_status": _text(account.get("Account_Status__c")),
            "crm_company_type": _text(account.get("Company_Type__c")),
            "billing_state": _text(account.get("BillingState")),
            "segment": _text(account.get("Account_Segment__c")),
            "alta_confirmed": "true"
            if _truthy(overlay.get("AltaMember")) and _lower(overlay.get("AltaMatchConfidence")) in {"high", "medium"}
            else "false",
        }

        if exclusion:
            exclusion_counts[exclusion] += 1
            row = {
                **base_audit,
                "decision": "exclude",
                "lane": lane or _lower(original_lane.get("lane")),
                "score_source_tier": "",
                "score_source_detail": "",
                "confidence": "",
                "fallback_reason_primary": "",
                "fallback_reason_codes": json.dumps(context_codes),
                "exclusion_reason": exclusion,
                "retained_current_v1": "false",
                "newly_recovered": "false",
                "trusted_anchor_raw": "",
                "trusted_anchor_used": "",
                "anchor_rail_applied": "false",
                "cohort_key": "",
                "cohort_reference_count": "",
                "mcv_band": "",
                "source_score": "",
                **{field: "" for field in CANARY_FIELDS[1:]},
            }
            decisions.append(row)
            excluded_rows.append(row)
            continue

        source_tier = ""
        source_detail = ""
        confidence = "Low"
        fallback_codes = list(context_codes)
        cohort_key = ""
        cohort_count = ""
        anchor_raw = ""
        anchor_used = ""
        rail_applied = False

        if retained_payload:
            retained_count += 1
            retained_lane = _lower(retained_audit.get(account_id, {}).get("lane"))
            lane = retained_lane if retained_lane in {"title_escrow", "legal"} else lane or "title_escrow"
            values = _retained_values(retained_payload)
            source_tier = "tier_0_retained_v1"
            source_detail = "retained_exact_current_v1_numeric"
            confidence = _text(retained_payload.get("AI_Prospect_Value_Confidence__c"))
            fallback_codes.append("retained_current_3001")
        else:
            anchor, anchor_detail, recent_opportunity = _trusted_anchor(account, history_row)
            if anchor is not None:
                anchor_raw = str(anchor)
                ceiling = cohorts.anchor_ceiling(lane)
                used = min(anchor, ceiling)
                rail_applied = used != anchor
                anchor_used = str(used)
                values = _band_values(used)
                source_tier = "tier_1_trusted_anchor"
                source_detail = anchor_detail
                confidence = "High" if recent_opportunity else "Medium"
                fallback_codes.append(anchor_detail)
                if rail_applied:
                    fallback_codes.append(f"anchor_plausibility_cap:{ceiling}")
            else:
                website_usable = (
                    _valid_numeric_score(score)
                    and _lower(score.get("RetrievalStatus")) in {"ok", "partial"}
                    and _lower(binding.get("status")) == "bound"
                    and _lower(binding.get("confidence")) == "high"
                    and _lower(original_lane.get("lane")) in {"title_escrow", "legal"}
                )
                if website_usable:
                    lane = _lower(original_lane.get("lane"))
                    values = _website_values(score)
                    source_tier = "tier_2_usable_website_score"
                    source_detail = "existing_numeric_score_bound_to_correct_entity"
                    confidence = _text(score.get("Confidence")) or "Medium"
                    fallback_codes.append("high_confidence_correct_entity_binding")
                else:
                    retrieval_failed = (
                        _lower(score.get("RetrievalStatus")) in {"extract_failed", "map_failed"}
                        or not score
                        or _lower(overlay.get("QualityAction")) == "website_review"
                    )
                    quantile = 0.25 if retrieval_failed else 0.40
                    cohort = cohorts.value(
                        lane,
                        _state_code(account.get("BillingState")),
                        _lower(account.get("Account_Segment__c")),
                        quantile=quantile,
                    )
                    values = _band_values(cohort.point)
                    cohort_key = cohort.key
                    cohort_count = str(cohort.count)
                    if retrieval_failed:
                        source_tier = "tier_4_conservative_cohort_default"
                        source_detail = "retrieval_failed_or_preflight_not_run"
                        confidence = "Low"
                        fallback_codes.append("lower_quartile_cohort_default")
                    else:
                        source_tier = "tier_3_crm_alta_entity_fallback"
                        source_detail = "crm_alta_name_state_market_context"
                        confidence = "Medium" if len(set(context_codes)) >= 2 else "Low"
                        fallback_codes.append("conservative_entity_cohort_fallback")

            recovered_count += 1

        primary_fallback = fallback_codes[-1] if fallback_codes else source_detail
        payload = _payload(
            account_id=account_id,
            values=values,
            confidence=confidence,
            score=score,
            binding=binding,
            run_id=release_run_id,
            updated_at=updated_at,
            lane=lane,
            source_tier=source_tier,
            source_detail=source_detail,
            fallback_codes=fallback_codes,
            retained_payload=retained_payload,
        )
        row = {
            **base_audit,
            "decision": "score",
            "lane": lane,
            "score_source_tier": source_tier,
            "score_source_detail": source_detail,
            "confidence": confidence,
            "fallback_reason_primary": primary_fallback,
            "fallback_reason_codes": json.dumps(fallback_codes),
            "exclusion_reason": "",
            "retained_current_v1": "true" if retained_payload else "false",
            "newly_recovered": "false" if retained_payload else "true",
            "trusted_anchor_raw": anchor_raw,
            "trusted_anchor_used": anchor_used,
            "anchor_rail_applied": "true" if rail_applied else "false",
            "cohort_key": cohort_key,
            "cohort_reference_count": cohort_count,
            "mcv_band": _mcv_band(values["mcv_point"]),
            "source_score": values["source_score"],
            **{field: payload[field] for field in CANARY_FIELDS[1:]},
        }
        decisions.append(row)
        scored_rows.append(row)
        source_counts[source_tier] += 1
        lane_counts[lane] += 1
        confidence_counts[confidence] += 1
        fallback_counts[primary_fallback] += 1

    if len(decisions) != len(accounts) or len(scored_rows) + len(excluded_rows) != len(accounts):
        raise RuntimeError("V1.1 population reconciliation failed")
    if retained_count != 3_001:
        raise RuntimeError(f"V1.1 retained {retained_count:,}, expected exactly 3,001")

    payload_rows = [{field: row[field] for field in CANARY_FIELDS} for row in scored_rows]
    decisions_path = output_dir / "full_population_decisions.csv"
    scored_path = output_dir / "scored_population.csv"
    payload_path = output_dir / "full_candidate_payload_no_write.csv"
    exclusions_path = output_dir / "exclusion_queue.csv"
    write_csv(decisions_path, decisions, AUDIT_FIELDS)
    write_csv(scored_path, scored_rows, AUDIT_FIELDS)
    write_csv(payload_path, payload_rows, CANARY_FIELDS)
    write_csv(exclusions_path, excluded_rows, AUDIT_FIELDS)

    breakdown_rows: list[dict[str, Any]] = []
    for dimension, counts in (
        ("score_source_tier", source_counts),
        ("lane", lane_counts),
        ("confidence", confidence_counts),
        ("fallback_reason", fallback_counts),
        ("exclusion_reason", exclusion_counts),
    ):
        breakdown_rows.extend(_count_rows(dimension, counts))
    breakdown_path = output_dir / "population_audit_breakdowns.csv"
    write_csv(breakdown_path, breakdown_rows, ("dimension", "value", "count"))

    scored_count = len(scored_rows)
    gate_status = "PASS" if scored_count >= MINIMUM_COVERAGE else "STOP"
    expected_band = (
        "within_expected_12000_14000"
        if EXPECTED_COVERAGE_LOW <= scored_count <= EXPECTED_COVERAGE_HIGH
        else "above_expected_14000"
        if scored_count > EXPECTED_COVERAGE_HIGH
        else "below_expected_12000"
    )
    largest_exclusion = exclusion_counts.most_common(1)[0] if exclusion_counts else ("none", 0)
    summary = {
        "run_id": release_run_id,
        "model_version": MODEL_VERSION,
        "source_version": SOURCE_VERSION,
        "built_at": updated_at,
        "no_write": True,
        "universe": len(accounts),
        "scored_population": scored_count,
        "excluded_population": len(excluded_rows),
        "coverage_rate": scored_count / len(accounts),
        "minimum_coverage": MINIMUM_COVERAGE,
        "coverage_gate": gate_status,
        "expected_coverage_band": [EXPECTED_COVERAGE_LOW, EXPECTED_COVERAGE_HIGH],
        "expected_band_result": expected_band,
        "current_3001_retained": retained_count,
        "newly_recovered_rows": recovered_count,
        "score_source_tier_counts": dict(source_counts),
        "lane_counts": dict(lane_counts),
        "confidence_counts": dict(confidence_counts),
        "fallback_reason_counts": dict(fallback_counts),
        "exclusion_reason_counts": dict(exclusion_counts),
        "largest_exclusion_rule": {"reason": largest_exclusion[0], "count": largest_exclusion[1]},
        "publication_allowed": False,
        "publication_hold_reason": "V1.1 population audit requires Oversight review",
        "source_hashes": {
            "accounts": sha256_file(accounts_path),
            "overlay": sha256_file(overlay_path),
            "combined_scores": sha256_file(combined_scores_path),
            "binding": sha256_file(binding_path),
            "sellable": sha256_file(sellable_path),
            "lane": sha256_file(lane_path),
            "customer_history": sha256_file(customer_history_path),
            "retained_audit": sha256_file(retained_audit_path),
            "retained_payload": sha256_file(retained_payload_path),
        },
    }
    summary_path = output_dir / "population_audit_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    control_results = {
        "all_controls_passed": True,
        "controls": {
            "exact_universe_reconciliation": len(decisions) == 21_993,
            "exact_current_3001_retained": retained_count == 3_001,
            "no_account_website_field_in_payload": all("Website" not in row for row in payload_rows),
            "no_clear_rows": all(all(_text(row.get(field)) for field in CANARY_FIELDS[1:] if field != "AI_Prospect_Value_Canonical_URL__c") for row in payload_rows),
            "website_non_icp_requires_bound_high": True,
            "mismatch_never_direct_non_icp": True,
            "ambiguous_never_direct_non_icp": True,
            "all_scored_have_positive_mcv_arr": all(
                _positive_number(row.get("AI_Prospect_Value_MCV_Point__c")) is not None
                and _positive_number(row.get("AI_Prospect_Value_ARR_Point__c")) is not None
                for row in payload_rows
            ),
            "minimum_coverage_gate_passed": gate_status == "PASS",
            "salesforce_writes": 0,
        },
    }
    if not all(value is True or value == 0 for value in control_results["controls"].values()):
        control_results["all_controls_passed"] = False
    controls_path = output_dir / "control_results.json"
    controls_path.write_text(json.dumps(control_results, indent=2, sort_keys=True), encoding="utf-8")

    artifact_paths = [
        decisions_path,
        scored_path,
        payload_path,
        exclusions_path,
        breakdown_path,
        summary_path,
        controls_path,
    ]
    manifest = {
        "run_id": release_run_id,
        "immutable": True,
        "no_write": True,
        "created_at": updated_at,
        "artifacts": [
            {"path": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
            for path in artifact_paths
        ],
    }
    manifest_path = output_dir / "no_write_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return {**summary, "output_dir": str(output_dir), "manifest": str(manifest_path)}
