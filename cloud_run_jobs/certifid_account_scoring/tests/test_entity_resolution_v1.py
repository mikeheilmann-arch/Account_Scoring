from __future__ import annotations

import csv
import inspect
import sys
from pathlib import Path

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT))

from certifid_account_scoring.pipeline.contracts import (  # noqa: E402
    BindingStatus,
    Confidence,
    Lifecycle,
)
from certifid_account_scoring.pipeline.entity_resolution import (  # noqa: E402
    parse_domain,
    resolve_website_binding,
    resolve_website_bindings,
)
from certifid_account_scoring.pipeline.sellable_unit import (  # noqa: E402
    resolve_sellable_unit,
    resolve_sellable_units,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _rows(name: str) -> list[dict[str, str]]:
    with (FIXTURES / name).open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _bool(value: str) -> bool:
    return value.strip().lower() == "true"


@pytest.mark.parametrize("row", _rows("entity_resolution_v1_cases.csv"), ids=lambda row: row["Case"])
def test_external_entity_fixture(row: dict[str, str]) -> None:
    decision = resolve_website_binding(row, source_versions={"crm": "fixture_crm_v1", "cached_website": "fixture_cache_v1"})

    assert decision.account_id == row["Id"]
    assert decision.status.value == row["ExpectedStatus"]
    assert decision.entity_class == row["ExpectedEntityClass"]
    assert decision.alta_entity_confirmed is _bool(row["ExpectedAltaEntityConfirmed"])
    assert decision.alta_used_as_sole_website_proof is False
    assert decision.resolver_version
    assert decision.source_as_of == row["EvidenceAsOf"]
    assert decision.reason_codes
    if row["Website"]:
        assert decision.citations


def test_alta_true_is_corrobation_never_sole_binding_proof() -> None:
    row = next(row for row in _rows("entity_resolution_v1_cases.csv") if row["Case"] == "alta_only_not_binding")
    decision = resolve_website_binding(row)

    assert decision.alta_entity_confirmed is True
    assert decision.status is BindingStatus.INSUFFICIENT_EVIDENCE
    assert "alta_cannot_be_sole_website_proof" in decision.reason_codes
    assert decision.alta_used_as_sole_website_proof is False


def test_alta_false_never_becomes_positive_evidence() -> None:
    row = next(row for row in _rows("entity_resolution_v1_cases.csv") if row["Case"] == "alta_false_correct_site")
    decision = resolve_website_binding(row)

    assert decision.status is BindingStatus.BOUND
    assert decision.alta_entity_confirmed is False
    assert "alta_member_false_or_missing" in decision.reason_codes
    assert "non_alta_website_identity_proof" in decision.reason_codes


def test_public_suffix_and_generic_host_handling() -> None:
    uk = parse_domain("https://locations.example-title.co.uk/contact")
    generic = parse_domain("https://tenant.wixsite.com/home")

    assert uk.registered_domain == "example-title.co.uk"
    assert uk.public_suffix == "co.uk"
    assert uk.subdomain == "locations"
    assert generic.registered_domain == "wixsite.com"
    assert generic.is_generic_host is True


def test_novel_structure_generalizes_without_fixture_name_or_id() -> None:
    # This synthetic organization is absent from the external CSV.  It follows
    # the same evidence structure but changes every identity literal.
    row = {
        "Id": "NOVEL-991",
        "Name": "Violet Ridge Settlement PLLC",
        "Website": "https://violetridgesettlement.com",
        "Company_Type__c": "Escrow company",
        "BillingCity": "Cheyenne",
        "BillingState": "WY",
        "BillingCountry": "USA",
        "SiteOrganizationName": "Violet Ridge Settlement",
        "PageTitle": "Violet Ridge Settlement | Escrow Closings",
        "AboutText": "We provide escrow, settlement, and real estate closing services.",
        "SiteCity": "Cheyenne",
        "SiteState": "WY",
        "SiteCountry": "USA",
    }

    decision = resolve_website_binding(row)
    assert decision.status is BindingStatus.BOUND
    assert decision.entity_class == "title_escrow"
    assert decision.confidence is Confidence.HIGH


def test_irrelevant_cached_text_and_row_order_do_not_change_identity() -> None:
    base = next(row for row in _rows("entity_resolution_v1_cases.csv") if row["Case"] == "correct_title")
    irrelevant = dict(base, CachedPageText="Copyright, accessibility, cookie preferences, and weather forecast.")
    original = resolve_website_binding(base)
    changed = resolve_website_binding(irrelevant)

    assert (changed.status, changed.entity_class, changed.confidence) == (
        original.status,
        original.entity_class,
        original.confidence,
    )

    rows = [
        next(row for row in _rows("entity_resolution_v1_cases.csv") if row["Case"] == name)
        for name in ("wrong_bank", "correct_title", "generic_host")
    ]
    forward = resolve_website_bindings(rows)
    reverse = resolve_website_bindings(reversed(rows))
    assert [item.to_dict() for item in forward] == [item.to_dict() for item in reverse]


def test_population_api_rejects_duplicate_account_ids() -> None:
    row = next(row for row in _rows("entity_resolution_v1_cases.csv") if row["Case"] == "correct_title")
    with pytest.raises(ValueError, match="duplicate Account IDs"):
        resolve_website_bindings([row, dict(row)])


@pytest.mark.parametrize("row", _rows("sellable_unit_v1_cases.csv"), ids=lambda row: row["Case"])
def test_external_sellable_unit_fixture(row: dict[str, str]) -> None:
    binding = {"status": row["BindingStatus"], "confidence": row["BindingConfidence"]}
    decision = resolve_sellable_unit(row, binding)

    assert decision.account_id == row["Id"]
    assert decision.lifecycle.value == row["ExpectedLifecycle"]
    assert decision.relationship_type == row["ExpectedRelationship"]
    assert decision.surviving_account_id == row["ExpectedSurvivor"]
    assert decision.standalone_score_eligible is _bool(row["ExpectedEligible"])
    assert decision.sellable_unit_id == f"su:{decision.surviving_account_id}"
    assert decision.account_id in decision.member_account_ids
    assert decision.resolver_version
    assert decision.reason_codes


def test_shared_domain_cannot_prove_duplicate_or_customer_ownership() -> None:
    rows = _rows("sellable_unit_v1_cases.csv")
    shared = next(row for row in rows if row["Case"] == "shared_domain_only")
    customer_domain = next(row for row in rows if row["Case"] == "shared_customer_domain")

    shared_decision = resolve_sellable_unit(shared, {"status": "bound", "confidence": "High"})
    customer_decision = resolve_sellable_unit(customer_domain, {"status": "bound", "confidence": "High"})

    assert shared_decision.relationship_type == "shared_domain_only_review"
    assert shared_decision.surviving_account_id == shared["Id"]
    assert "shared_domain_insufficient_for_duplicate_or_parent" in shared_decision.reason_codes
    assert customer_decision.lifecycle is Lifecycle.NET_NEW
    assert customer_decision.relationship_type == "shared_domain_customer_review"
    assert "shared_domain_insufficient_for_customer_ownership" in customer_decision.reason_codes


def test_unverified_duplicate_hint_does_not_select_survivor() -> None:
    row = next(row for row in _rows("sellable_unit_v1_cases.csv") if row["Case"] == "unverified_duplicate_hint")
    decision = resolve_sellable_unit(row, {"status": "bound", "confidence": "High"})

    assert decision.relationship_type == "shared_domain_only_review"
    assert decision.surviving_account_id == row["Id"]
    assert decision.suggested_survivor_id == row["Id"]


def test_binding_is_required_for_standalone_eligibility() -> None:
    row = next(row for row in _rows("sellable_unit_v1_cases.csv") if row["Case"] == "independent_net_new")
    bound = resolve_sellable_unit(row, {"status": "bound", "confidence": "High"})
    review = resolve_sellable_unit(row, {"status": "ambiguous", "confidence": "Medium"})

    assert bound.standalone_score_eligible is True
    assert review.standalone_score_eligible is False
    assert "website_binding_not_high_confidence_bound" in review.reason_codes


def test_population_sellable_api_is_order_stable_and_rejects_duplicates() -> None:
    rows = _rows("sellable_unit_v1_cases.csv")[:3]
    bindings = {
        row["Id"]: {"status": row["BindingStatus"], "confidence": row["BindingConfidence"]}
        for row in rows
    }
    forward = resolve_sellable_units(rows, bindings)
    reverse = resolve_sellable_units(reversed(rows), bindings)
    assert [item.to_dict() for item in forward] == [item.to_dict() for item in reverse]

    with pytest.raises(ValueError, match="duplicate Account IDs"):
        resolve_sellable_units([rows[0], dict(rows[0])], bindings)


def test_production_resolvers_do_not_reference_fixture_artifacts() -> None:
    # External labels are deliberately absent from the production dependency
    # graph; this catches accidental imports or future fixture-name branches.
    entity_source = inspect.getsource(resolve_website_binding)
    sellable_source = inspect.getsource(resolve_sellable_unit)
    assert "tests/fixtures" not in entity_source
    assert "entity_resolution_v1_cases" not in entity_source
    assert "tests/fixtures" not in sellable_source
    assert "sellable_unit_v1_cases" not in sellable_source
