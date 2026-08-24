from __future__ import annotations

from dataclasses import fields
import inspect
import json
from pathlib import Path
import sys

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from certifid_account_scoring.pipeline.config import FEATURE_VERSION, LANE_VERSION  # noqa: E402
from certifid_account_scoring.pipeline.contracts import (  # noqa: E402
    BindingStatus,
    Confidence,
    Lane,
    Lifecycle,
    SellableUnitDecision,
    WebsiteBinding,
)
from certifid_account_scoring.pipeline.evidence_features import (  # noqa: E402
    CachedEvidencePage,
    CandidateStatus,
    FeatureEligibility,
    StaffCategory,
    extract_entity_safe_features,
)
from certifid_account_scoring.pipeline.lanes import LaneInput, classify_icp_lane  # noqa: E402


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "lanes_features_v1.json"


@pytest.fixture(scope="module")
def fixture_data() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _pages(raw_pages: list[dict]) -> tuple[CachedEvidencePage, ...]:
    return tuple(CachedEvidencePage(**page) for page in raw_pages)


def _strong_eligibility() -> FeatureEligibility:
    return FeatureEligibility(
        binding_status="bound",
        binding_confidence=Confidence.HIGH,
        sellable_unit_eligible=True,
        sellable_unit_confidence=Confidence.HIGH,
    )


def _lane_input(case: dict, **overrides: object) -> LaneInput:
    values: dict[str, object] = {
        "account_id": f"001{case['id'].upper()}TEST",
        "account_name": case["account_name"],
        "crm_company_type": case["crm_company_type"],
        "billing_state": case["billing_state"],
        "pages": (
            CachedEvidencePage(
                url=f"https://{case['id'].replace('_', '-')}.fixture.invalid/about",
                title="About",
                text=case["text"],
                observed_at="2026-07-08T12:00:00Z",
            ),
        ),
        "binding_status": "bound",
        "binding_confidence": Confidence.HIGH,
        "sellable_unit_eligible": True,
        "sellable_unit_confidence": Confidence.HIGH,
        "lifecycle": "net_new",
        "crm_observed_at": "2026-07-08T00:00:00Z",
    }
    values.update(overrides)
    return LaneInput(**values)


def test_offices_are_normalized_deduped_and_non_operating_addresses_are_excluded(
    fixture_data: dict,
) -> None:
    case = fixture_data["feature_case"]
    result = extract_entity_safe_features(
        case["account_id"],
        _pages(case["pages"]),
        _strong_eligibility(),
        mapped_link_count=case["mapped_link_count"],
    )

    assert result.features.operating_office_count == case["expected"]["operating_office_count"]
    assert result.features.office_count_low == 2
    assert result.features.office_count_high == 2
    verified = [item for item in result.office_candidates if item.status == CandidateStatus.VERIFIED]
    assert len(verified) == 2
    assert len({item.normalized_address for item in verified}) == 2
    assert any("100 harbor st suite 200" in item.normalized_address for item in verified)

    exclusion_codes = {
        code
        for item in result.office_candidates
        if item.status == CandidateStatus.EXCLUDED
        for code in item.reason_codes
    }
    assert exclusion_codes == set(case["expected"]["office_exclusion_codes"])
    assert all(item.citations for item in result.office_candidates)


def test_relevant_operational_staff_are_distinct_from_attorneys_generic_names_and_testimonials(
    fixture_data: dict,
) -> None:
    case = fixture_data["feature_case"]
    result = extract_entity_safe_features(
        case["account_id"],
        _pages(case["pages"]),
        _strong_eligibility(),
        mapped_link_count=case["mapped_link_count"],
    )

    assert result.features.relevant_staff_count == case["expected"]["relevant_staff_count"]
    actual = {item.normalized_name: item.category.value for item in result.staff_candidates}
    for name, category in case["expected"]["staff_categories"].items():
        assert actual[name] == category
    assert sum(item.category == StaffCategory.RELEVANT_OPERATIONAL for item in result.staff_candidates) == 2
    assert "attorneys_not_counted_as_operational_staff" in result.features.uncertainty_codes
    assert "generic_roles_not_counted_as_operational_staff" in result.features.uncertainty_codes
    assert "testimonial_or_listing_names_excluded" in result.features.uncertainty_codes


def test_citations_and_versioned_output_are_deterministic(fixture_data: dict) -> None:
    case = fixture_data["feature_case"]
    pages = _pages(case["pages"])
    forward = extract_entity_safe_features(
        case["account_id"], pages, _strong_eligibility(), mapped_link_count=45
    )
    reverse = extract_entity_safe_features(
        case["account_id"], tuple(reversed(pages)), _strong_eligibility(), mapped_link_count=45
    )

    assert forward.to_dict() == reverse.to_dict()
    assert forward.features.feature_version == FEATURE_VERSION
    assert forward.features.citations
    assert all(citation.reference.startswith("https://") for citation in forward.features.citations)
    assert all(citation.observed_at == "2026-07-08T12:00:00Z" for citation in forward.features.citations)
    assert all(len(citation.evidence_hash) == 64 for citation in forward.features.citations)


def test_weak_upstream_gates_preserve_review_candidates_but_hide_capacity_features(
    fixture_data: dict,
) -> None:
    case = fixture_data["feature_case"]
    result = extract_entity_safe_features(
        case["account_id"],
        _pages(case["pages"]),
        FeatureEligibility(
            binding_status="ambiguous",
            binding_confidence=Confidence.LOW,
            sellable_unit_eligible=True,
            sellable_unit_confidence=Confidence.HIGH,
        ),
        mapped_link_count=45,
    )

    assert result.capacity_features_exposed is False
    assert result.office_candidates
    assert result.staff_candidates
    assert result.features.operating_office_count == 0
    assert result.features.office_count_high == 0
    assert result.features.relevant_staff_count == 0
    assert result.features.operational_service_signals == ()
    assert result.features.tool_signals == ()
    assert result.features.evidence_confidence == Confidence.LOW
    assert "upstream_identity_or_sellable_gate_not_strong" in result.features.uncertainty_codes


def test_sophistication_changes_confidence_metadata_only_not_capacity(fixture_data: dict) -> None:
    case = fixture_data["feature_case"]
    pages = _pages(case["pages"][:1])
    low = extract_entity_safe_features(
        case["account_id"], pages, _strong_eligibility(), mapped_link_count=0
    )
    high = extract_entity_safe_features(
        case["account_id"], pages, _strong_eligibility(), mapped_link_count=100
    )

    assert low.website_sophistication != high.website_sophistication
    assert low.features.operating_office_count == high.features.operating_office_count
    assert low.features.office_count_low == high.features.office_count_low
    assert low.features.office_count_high == high.features.office_count_high
    assert low.features.relevant_staff_count == high.features.relevant_staff_count
    assert low.features.staff_count_low == high.features.staff_count_low
    assert low.features.staff_count_high == high.features.staff_count_high
    assert low.features.operational_service_signals == high.features.operational_service_signals


def test_explicit_lane_fixture_matrix(fixture_data: dict) -> None:
    for case in fixture_data["lane_cases"]:
        decision = classify_icp_lane(_lane_input(case))
        assert decision.lane.value == case["expected_lane"], case["id"]
        assert decision.subtype == case["expected_subtype"], case["id"]
        assert decision.eligible_for_value is case["expected_eligible"], case["id"]
        assert decision.lane_version == LANE_VERSION
        assert decision.reason_codes


def test_every_crm_law_firm_enters_legal_lane_even_without_site_law_signals(fixture_data: dict) -> None:
    law_cases = [case for case in fixture_data["lane_cases"] if case["crm_company_type"] == "Law firm"]
    assert law_cases
    assert all(classify_icp_lane(_lane_input(case)).lane == Lane.LEGAL for case in law_cases)

    novel = {
        "id": "novel_crm_route",
        "account_name": "Northwind Professional Group",
        "crm_company_type": "Law firm",
        "billing_state": "UT",
        "text": "A deliberately sparse cached page with no practice keywords.",
    }
    decision = classify_icp_lane(_lane_input(novel))
    assert decision.lane == Lane.LEGAL
    assert "crm_company_type_law_firm" in decision.reason_codes
    assert any(citation.source == "salesforce_account" for citation in decision.citations)


def test_shared_contract_adapters_preserve_company_type_and_strong_gates() -> None:
    binding = WebsiteBinding(
        account_id="001ADAPTERTEST0001",
        account_name="Adapter Professional Group",
        website="https://adapter.fixture.invalid",
        registered_domain="fixture.invalid",
        status=BindingStatus.BOUND,
        confidence=Confidence.HIGH,
        entity_class="law_firm",
        reason_codes=("organization_match",),
        reasons=("Fixture contract binding.",),
        citations=(),
        source_as_of="2026-07-08",
        resolver_version="test_resolver",
    )
    sellable = SellableUnitDecision(
        account_id="001ADAPTERTEST0001",
        sellable_unit_id="SU-ADAPTER",
        surviving_account_id="001ADAPTERTEST0001",
        member_account_ids=("001ADAPTERTEST0001",),
        parent_unit_id="",
        lifecycle=Lifecycle.NET_NEW,
        relationship_type="independent",
        confidence=Confidence.HIGH,
        standalone_score_eligible=True,
        suggested_survivor_id="001ADAPTERTEST0001",
        reason_codes=("independent_unit",),
        resolver_version="test_sellable",
    )
    page = CachedEvidencePage(
        url="https://adapter.fixture.invalid/about",
        text="Our closing attorneys conduct real estate closings.",
        observed_at="2026-07-08",
    )

    eligibility = FeatureEligibility.from_contracts(binding, sellable)
    assert eligibility.is_strong
    lane_input = LaneInput.from_contracts(
        {"Id": binding.account_id, "Name": binding.account_name, "Company_Type__c": "Law firm", "BillingState": "CA"},
        (page,),
        binding,
        sellable,
        crm_observed_at="2026-07-08",
    )
    decision = classify_icp_lane(lane_input)
    assert lane_input.crm_company_type == "Law firm"
    assert decision.lane == Lane.LEGAL
    assert decision.eligible_for_value is True


@pytest.mark.parametrize(
    "overrides, expected_block",
    [
        ({"binding_status": "ambiguous"}, "website_not_bound"),
        ({"binding_confidence": Confidence.MEDIUM}, "website_binding_not_high_confidence"),
        ({"sellable_unit_eligible": False}, "sellable_unit_not_standalone_eligible"),
        ({"sellable_unit_confidence": Confidence.MEDIUM}, "sellable_unit_not_high_confidence"),
        ({"lifecycle": "active_customer"}, "lifecycle_not_net_new"),
        ({"lifecycle": "winback"}, "lifecycle_not_net_new"),
    ],
)
def test_no_lane_auto_scores_without_strong_binding_sellable_and_lifecycle_gates(
    fixture_data: dict,
    overrides: dict,
    expected_block: str,
) -> None:
    case = next(item for item in fixture_data["lane_cases"] if item["id"] == "title_operator")
    decision = classify_icp_lane(_lane_input(case, **overrides))
    assert decision.subtype == "title_escrow_settlement_operator"
    assert decision.eligible_for_value is False
    assert expected_block in decision.reason_codes


def test_anchor_values_cannot_enter_or_bypass_lane_decisions(fixture_data: dict) -> None:
    lane_field_names = {field.name.lower() for field in fields(LaneInput)}
    signature_names = {name.lower() for name in inspect.signature(classify_icp_lane).parameters}
    assert not any("anchor" in name or "mcv" in name or "arr" in name for name in lane_field_names)
    assert not any("anchor" in name or "mcv" in name or "arr" in name for name in signature_names)

    case = next(item for item in fixture_data["lane_cases"] if item["id"] == "title_operator")
    weak = _lane_input(case, binding_status="mismatch", sellable_unit_eligible=False)
    decision = classify_icp_lane(weak)
    assert decision.eligible_for_value is False


def test_fixture_logic_generalizes_to_novel_names_and_domains(fixture_data: dict) -> None:
    source = next(item for item in fixture_data["lane_cases"] if item["id"] == "title_operator")
    novel = dict(source)
    novel.update(
        {
            "id": "never_seen_generated_case",
            "account_name": "Generated Quartz Services 8472",
            "text": "Customers can use our settlement services, escrow company, and title insurance agency for residential transactions.",
        }
    )
    decision = classify_icp_lane(_lane_input(novel))
    assert decision.lane == Lane.TITLE_ESCROW
    assert decision.subtype == "title_escrow_settlement_operator"
    assert decision.eligible_for_value is True

    production_source = inspect.getsource(sys.modules[classify_icp_lane.__module__])
    assert "tests/fixtures" not in production_source
    assert "never_seen_generated_case" not in production_source
