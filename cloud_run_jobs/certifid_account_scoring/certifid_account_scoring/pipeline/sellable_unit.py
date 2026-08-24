"""Deterministic V1 independently-sellable-unit resolution.

The resolver accepts Salesforce-style dictionaries and a prior
``WebsiteBinding``.  It separates lifecycle from organizational relationship
and never treats a Salesforce Account, shared website domain, parent link, DBA,
or branch row as an independently sellable unit by default.

Expected fields (aliases are accepted):

* ``Id`` plus lifecycle evidence such as ``Type``, ``Account_Status__c``,
  ``Active_Customer__c`` and active subscription/contract fields.
* relationship evidence such as ``ParentId``, an explicit relationship type,
  DBA/branch/owned-direct parent IDs, centrally-billed/independent-buying flags,
  and verified dedupe survivor/confidence fields.
* optional related Account ID lists and shared-domain/customer-domain lists.

Shared-domain lists cause a review decision only.  They never prove a duplicate,
customer relationship, parent ownership, or survivor.
"""

from __future__ import annotations

import json
import re
from typing import Iterable, Mapping, Sequence

from .config import SELLABLE_UNIT_VERSION
from .contracts import BindingStatus, Confidence, Lifecycle, SellableUnitDecision, WebsiteBinding


SELLABLE_RESOLVER_VERSION = SELLABLE_UNIT_VERSION


def _clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def _first(row: Mapping[str, object], names: Sequence[str]) -> str:
    for name in names:
        if name in row:
            value = _clean(row[name])
            if value:
                return value
    return ""


def _truth(value: object) -> bool:
    return _clean(value).casefold() in {"1", "true", "t", "yes", "y"}


def _number(value: object) -> float:
    try:
        return float(_clean(value).replace(",", "").replace("$", ""))
    except ValueError:
        return 0.0


def _values(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        result: list[str] = []
        for nested in value.values():
            result.extend(_values(nested))
        return result
    if isinstance(value, (list, tuple, set)):
        result = []
        for nested in value:
            result.extend(_values(nested))
        return result
    text = _clean(value)
    if not text:
        return []
    if text[:1] in "[{":
        try:
            return _values(json.loads(text))
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return [part.strip() for part in re.split(r"[|;,\n]+", text) if part.strip()]


def _field_values(row: Mapping[str, object], names: Sequence[str]) -> list[str]:
    values: list[str] = []
    for name in names:
        if name in row:
            values.extend(_values(row[name]))
    return list(dict.fromkeys(values))


def _confidence_value(value: object) -> str:
    raw = _clean(value).casefold()
    if raw in {"verified", "exact", "high", "confirmed", "1.0", "100"}:
        return "high"
    if raw in {"medium", "probable", "review"}:
        return "medium"
    return "low"


def _binding_values(binding: WebsiteBinding | Mapping[str, object] | None) -> tuple[str, str]:
    if binding is None:
        return "", ""
    if isinstance(binding, WebsiteBinding):
        return binding.status.value.casefold(), binding.confidence.value.casefold()
    status = _first(binding, ("status", "binding_status", "BindingStatus"))
    confidence = _first(binding, ("confidence", "binding_confidence", "BindingConfidence"))
    return status.casefold(), confidence.casefold()


def _lifecycle(row: Mapping[str, object]) -> tuple[Lifecycle, tuple[str, ...], Confidence]:
    status = " ".join(
        value.casefold()
        for value in _field_values(
            row,
            ("Account_Status__c", "AccountStatus", "LifecycleStatus", "CustomerStatus", "SubscriptionStatus"),
        )
    )
    account_type = _first(row, ("Type", "AccountType")).casefold()
    company_type = _first(row, ("Company_Type__c", "CompanyType")).casefold()

    active_flag = any(
        _truth(row.get(name))
        for name in (
            "Active_Customer__c",
            "ActiveCustomer",
            "HasActiveSubscription",
            "HasActiveContract",
            "CustomerActive",
        )
    )
    explicit_inactive_flag = any(
        name in row and _clean(row.get(name)).casefold() in {"false", "0", "no", "n"}
        for name in ("Active_Customer__c", "ActiveCustomer", "CustomerActive")
    )
    active_revenue = any(
        _number(row.get(name)) > 0
        for name in (
            "Active_Subscription_Revenue__c",
            "ActiveSubscriptionRevenue",
            "CurrentSubscriptionARR",
        )
    )
    churned = bool(re.search(r"\b(churned|former customer|lapsed|cancelled|terminated customer|inactive customer)\b", status))
    active_status = bool(re.search(r"\b(active customer|current customer|live subscription|contracted)\b", status))

    # An explicit churn state outranks a weak historical Type=Customer marker,
    # but never outranks a currently active subscription/contract assertion.
    if active_flag or active_revenue or active_status:
        codes = ["active_customer_flag" if active_flag else "active_customer_status"]
        if active_revenue:
            codes.append("active_subscription_revenue")
        return Lifecycle.ACTIVE_CUSTOMER, tuple(codes), Confidence.HIGH
    if churned:
        return Lifecycle.WINBACK, ("churned_or_former_customer",), Confidence.HIGH
    if account_type in {"customer", "client"}:
        return Lifecycle.ACTIVE_CUSTOMER, ("crm_customer_type",), Confidence.MEDIUM
    if "partner" in account_type or "partner" in company_type:
        return Lifecycle.PARTNER, ("crm_partner_type",), Confidence.HIGH
    if any(term in company_type for term in ("competitor", "vendor", "government", "education")):
        return Lifecycle.EXCLUDED, ("crm_excluded_company_type",), Confidence.HIGH
    if account_type in {"prospect", "target", "lead", ""}:
        if account_type in {"prospect", "target", "lead"} and explicit_inactive_flag:
            return Lifecycle.NET_NEW, ("explicit_non_customer_prospect",), Confidence.HIGH
        return Lifecycle.NET_NEW, ("no_customer_lifecycle_evidence",), Confidence.MEDIUM
    return Lifecycle.UNKNOWN, ("lifecycle_unresolved",), Confidence.LOW


def _unit_id(surviving_account_id: str) -> str:
    return f"su:{surviving_account_id}"


def resolve_sellable_unit(
    row: Mapping[str, object],
    website_binding: WebsiteBinding | Mapping[str, object] | None = None,
    *,
    resolver_version: str = SELLABLE_RESOLVER_VERSION,
) -> SellableUnitDecision:
    """Resolve one Account to a lifecycle and sellable-unit relationship.

    ``standalone_score_eligible`` is true only for a high-confidence bound site,
    a net-new lifecycle, and an independently sellable survivor.  Ambiguous
    hierarchy or lifecycle evidence is review/no-score, never residual accept.
    """

    account_id = _first(row, ("Id", "AccountId", "Account_Id", "account_id"))
    if not account_id:
        raise ValueError("Sellable-unit resolution requires an Account Id")

    lifecycle, lifecycle_codes, lifecycle_confidence = _lifecycle(row)
    reason_codes = list(lifecycle_codes)
    parent_id = _first(row, ("ParentId", "ParentAccountId", "Parent_Account__c"))
    dba_parent_id = _first(row, ("DBAOfAccountId", "DBA_Of_Account__c", "DbaParentAccountId"))
    branch_parent_id = _first(row, ("BranchOfAccountId", "Branch_Of_Account__c", "BranchParentAccountId"))
    owned_parent_id = _first(
        row,
        ("UnderwriterParentAccountId", "Underwriter_Parent_Account__c", "OwnedDirectParentAccountId"),
    )
    duplicate_survivor = _first(
        row,
        (
            "DuplicateSurvivorId",
            "Duplicate_Survivor_Id__c",
            "SuggestedSurvivorId",
            "DedupeSuggestedSurvivorId",
        ),
    )
    dedupe_confidence = _confidence_value(
        _first(row, ("DedupeMatchConfidence", "DuplicateConfidence", "DedupeConfidence"))
    )
    duplicate_loser = _truth(row.get("IsDuplicateLoser")) or _truth(row.get("DuplicateLoser"))

    relationship_hint = _first(
        row,
        ("RelationshipType", "Relationship_Type__c", "EntityRelationshipType", "SellableRelationshipType"),
    ).casefold()
    centrally_billed = any(
        _truth(row.get(name))
        for name in ("CentrallyBilled", "Centrally_Billed__c", "ParentBilled", "Parent_Billed__c", "CentralContracting")
    )
    independent_buying = any(
        _truth(row.get(name))
        for name in (
            "IndependentBuyingUnit",
            "Independent_Buying_Unit__c",
            "IndependentContracting",
            "IndependentBudget",
        )
    )
    owned_direct = any(
        _truth(row.get(name)) for name in ("OwnedDirect", "Owned_Direct__c", "UnderwriterOwnedOperation")
    ) or bool(re.search(r"owned.?direct|underwriter.?owned|direct operation", relationship_hint))

    shared_domain_ids = _field_values(
        row,
        ("SharedDomainAccountIds", "DomainAccountIds", "SharedWebsiteAccountIds", "DomainClusterAccountIds"),
    )
    domain_cluster_size = _number(
        _first(row, ("DomainClusterSize", "SharedDomainClusterSize", "WebsiteDomainClusterSize"))
    )
    domain_customer_ids = _field_values(
        row,
        ("DomainCustomerAccountIds", "SharedDomainCustomerIds", "CustomerDomainAccountIds"),
    )
    explicit_members = _field_values(
        row,
        ("MemberAccountIds", "RelatedAccountIds", "DuplicateAccountIds", "ChildAccountIds"),
    )
    member_ids = set(explicit_members)
    member_ids.add(account_id)

    surviving_id = account_id
    suggested_id = account_id
    parent_unit_id = ""
    relationship_type = "independent"
    relationship_confidence = Confidence.MEDIUM
    structural_eligible = True

    # Verified duplicate evidence requires both a survivor and high-confidence
    # match semantics.  A shared domain or name cluster alone never enters here.
    if duplicate_survivor and duplicate_survivor != account_id and (duplicate_loser or dedupe_confidence == "high"):
        surviving_id = duplicate_survivor
        suggested_id = duplicate_survivor
        parent_unit_id = _unit_id(duplicate_survivor)
        relationship_type = "duplicate_loser"
        relationship_confidence = Confidence.HIGH
        structural_eligible = False
        member_ids.add(duplicate_survivor)
        reason_codes.append("verified_duplicate_survivor")
    elif owned_direct or owned_parent_id:
        suggested_id = owned_parent_id or parent_id or account_id
        surviving_id = suggested_id
        parent_unit_id = _unit_id(suggested_id) if suggested_id != account_id else ""
        relationship_type = "owned_direct_operation"
        relationship_confidence = Confidence.MEDIUM
        structural_eligible = False
        member_ids.add(suggested_id)
        reason_codes.append("owned_direct_requires_parent_review")
    elif dba_parent_id or "dba" in relationship_hint:
        related_parent = dba_parent_id or parent_id
        suggested_id = account_id if independent_buying else (related_parent or account_id)
        surviving_id = suggested_id
        parent_unit_id = _unit_id(related_parent) if related_parent else ""
        relationship_type = "independent_dba" if independent_buying else "dba_alias"
        relationship_confidence = Confidence.MEDIUM if independent_buying else Confidence.HIGH
        structural_eligible = independent_buying
        if related_parent:
            member_ids.add(related_parent)
        reason_codes.append("dba_independent_contracting" if independent_buying else "dba_rolls_to_legal_entity")
    elif branch_parent_id or re.search(r"\b(branch|location|office)\b", relationship_hint):
        related_parent = branch_parent_id or parent_id
        suggested_id = account_id if independent_buying else (related_parent or account_id)
        surviving_id = suggested_id
        parent_unit_id = _unit_id(related_parent) if related_parent else ""
        relationship_type = "independent_branch" if independent_buying else "branch_location"
        relationship_confidence = Confidence.MEDIUM if independent_buying else Confidence.HIGH
        structural_eligible = independent_buying
        if related_parent:
            member_ids.add(related_parent)
        reason_codes.append("branch_independent_contracting" if independent_buying else "branch_not_standalone")
    elif parent_id and (centrally_billed or re.search(r"non.?independent|central|rollup", relationship_hint)):
        surviving_id = parent_id
        suggested_id = parent_id
        parent_unit_id = _unit_id(parent_id)
        relationship_type = "non_independent_child"
        relationship_confidence = Confidence.HIGH
        structural_eligible = False
        member_ids.add(parent_id)
        reason_codes.append("parent_central_contracting_evidence")
    elif parent_id and independent_buying:
        relationship_type = "independent_child"
        relationship_confidence = Confidence.HIGH
        structural_eligible = True
        parent_unit_id = _unit_id(parent_id)
        reason_codes.append("child_has_independent_buying_evidence")
    elif parent_id:
        suggested_id = parent_id
        parent_unit_id = _unit_id(parent_id)
        relationship_type = "parent_child_review"
        relationship_confidence = Confidence.MEDIUM
        structural_eligible = False
        member_ids.add(parent_id)
        reason_codes.append("parent_link_without_buying_independence_evidence")
    elif domain_customer_ids:
        relationship_type = "shared_domain_customer_review"
        relationship_confidence = Confidence.LOW
        structural_eligible = False
        reason_codes.append("shared_domain_insufficient_for_customer_ownership")
    elif shared_domain_ids:
        relationship_type = "shared_domain_only_review"
        relationship_confidence = Confidence.LOW
        structural_eligible = False
        reason_codes.append("shared_domain_insufficient_for_duplicate_or_parent")
    elif domain_cluster_size > 1:
        relationship_type = "shared_domain_cluster_review"
        relationship_confidence = Confidence.LOW
        structural_eligible = False
        reason_codes.append("shared_domain_cluster_members_unresolved")
    elif explicit_members:
        relationship_type = "surviving_parent"
        relationship_confidence = Confidence.MEDIUM
        reason_codes.append("account_has_related_members")
    elif domain_cluster_size == 1:
        relationship_confidence = Confidence.HIGH
        reason_codes.append("unique_domain_no_hierarchy_relationship_evidence")
    else:
        reason_codes.append("no_rollup_relationship_evidence")

    if lifecycle is Lifecycle.ACTIVE_CUSTOMER:
        relationship_type = "active_customer" if relationship_type == "independent" else relationship_type
        structural_eligible = False
        reason_codes.append("active_customer_excluded_from_net_new")
    elif lifecycle is Lifecycle.WINBACK:
        relationship_type = "churned_winback" if relationship_type == "independent" else relationship_type
        structural_eligible = False
        reason_codes.append("winback_separate_from_net_new")
    elif lifecycle is Lifecycle.PARTNER:
        structural_eligible = False
        reason_codes.append("partner_excluded_from_net_new")
    elif lifecycle in {Lifecycle.EXCLUDED, Lifecycle.UNKNOWN}:
        structural_eligible = False
        reason_codes.append("lifecycle_not_net_new_eligible")

    binding_status, binding_confidence = _binding_values(website_binding)
    binding_eligible = binding_status == BindingStatus.BOUND.value and binding_confidence == Confidence.HIGH.value.casefold()
    if not binding_eligible:
        structural_eligible = False
        reason_codes.append("website_binding_not_high_confidence_bound")

    # Confidence is for the complete sellable decision.  An unresolved binding
    # or lifecycle prevents a High decision even if one relationship is clear.
    confidence = relationship_confidence
    if lifecycle_confidence is Confidence.LOW or not binding_status:
        confidence = Confidence.LOW
    elif confidence is Confidence.HIGH and lifecycle_confidence is Confidence.MEDIUM:
        confidence = Confidence.MEDIUM
    if binding_status and not binding_eligible and confidence is Confidence.HIGH:
        confidence = Confidence.MEDIUM

    return SellableUnitDecision(
        account_id=account_id,
        sellable_unit_id=_unit_id(surviving_id),
        surviving_account_id=surviving_id,
        member_account_ids=tuple(sorted(member_ids)),
        parent_unit_id=parent_unit_id,
        lifecycle=lifecycle,
        relationship_type=relationship_type,
        confidence=confidence,
        standalone_score_eligible=bool(structural_eligible and lifecycle is Lifecycle.NET_NEW and binding_eligible),
        suggested_survivor_id=suggested_id,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
        resolver_version=resolver_version,
    )


def resolve_sellable_units(
    rows: Iterable[Mapping[str, object]],
    website_bindings: Mapping[str, WebsiteBinding | Mapping[str, object]] | None = None,
    **kwargs: object,
) -> list[SellableUnitDecision]:
    """Resolve a population, reject duplicate IDs, and return stable ID order."""

    bindings = website_bindings or {}
    decisions: list[SellableUnitDecision] = []
    for row in rows:
        account_id = _first(row, ("Id", "AccountId", "Account_Id", "account_id"))
        decisions.append(resolve_sellable_unit(row, bindings.get(account_id), **kwargs))
    ids = [decision.account_id for decision in decisions]
    if len(ids) != len(set(ids)):
        raise ValueError("Sellable-unit input contains duplicate Account IDs")
    return sorted(decisions, key=lambda decision: decision.account_id)
