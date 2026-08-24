from __future__ import annotations

from certifid_account_scoring.pipeline.sales_prioritization_v1_1 import (
    _entity_context,
    _hard_exclusion,
)
from certifid_account_scoring.pipeline.salesforce_v1_1_staging import validate_v1_1_payload


def _account(**overrides: str) -> dict[str, str]:
    row = {
        "Name": "Harbor Title and Escrow",
        "Type": "Prospect",
        "Account_Status__c": "",
        "Company_Type__c": "Title company",
        "Active_Customer__c": "false",
        "BillingState": "Virginia",
    }
    row.update(overrides)
    return row


def test_mismatched_adjacent_website_does_not_exclude_account() -> None:
    reason = _hard_exclusion(
        _account(),
        {"status": "mismatch", "confidence": "High"},
        {"lifecycle": "net_new", "relationship_type": "independent", "confidence": "Medium"},
        {"lane": "adjacent", "confidence": "High"},
    )
    assert reason == ""


def test_ambiguous_adjacent_website_does_not_exclude_account() -> None:
    reason = _hard_exclusion(
        _account(),
        {"status": "ambiguous", "confidence": "Low"},
        {"lifecycle": "net_new", "relationship_type": "independent", "confidence": "Medium"},
        {"lane": "adjacent", "confidence": "High"},
    )
    assert reason == ""


def test_bound_high_adjacent_website_can_confirm_non_icp() -> None:
    reason = _hard_exclusion(
        _account(Company_Type__c="Other (see notes)", Name="Harbor Realty"),
        {"status": "bound", "confidence": "High"},
        {"lifecycle": "net_new", "relationship_type": "independent", "confidence": "Medium"},
        {"lane": "adjacent", "confidence": "High"},
    )
    assert reason == "bound_high_confidence_non_icp_website"


def test_crm_title_context_overrides_unbound_adjacent_lane() -> None:
    lane, reasons = _entity_context(
        _account(),
        {"AltaMember": "false", "AltaMatchConfidence": ""},
        {},
        {"lane": "adjacent", "confidence": "High"},
    )
    assert lane == "title_escrow"
    assert "crm_title_or_escrow_type" in reasons


def test_attorney_state_law_firm_receives_legal_context() -> None:
    lane, reasons = _entity_context(
        _account(Name="Smith Law PLLC", Company_Type__c="Law firm", BillingState="Virginia"),
        {"AltaMember": "false", "AltaMatchConfidence": ""},
        {},
        {"lane": "legal", "confidence": "Low"},
    )
    assert lane == "legal"
    assert "attorney_relevant_state" in reasons


def test_v11_payload_allows_blank_canonical_url_for_no_url_status() -> None:
    row = {
        "Id": "001000000000000AAA",
        "AI_Prospect_Value_MCV_Point__c": "25",
        "AI_Prospect_Value_MCV_Low__c": "20",
        "AI_Prospect_Value_MCV_High__c": "30",
        "AI_Prospect_Value_ARR_Point__c": "12000",
        "AI_Prospect_Value_ARR_Range__c": "$10K-$15K",
        "AI_Prospect_Value_Confidence__c": "Low",
        "AI_Prospect_Value_ICP__c": "scorable",
        "AI_Prospect_Value_Action__c": "score_now",
        "AI_Prospect_Value_URL_Status__c": "no_url",
        "AI_Prospect_Value_Canonical_URL__c": "",
        "AI_Prospect_Value_Evidence__c": "fallback evidence",
        "AI_Prospect_Value_Components__c": "{}",
        "AI_Prospect_Value_Model_Version__c": "sales_prioritization_v1_1_20260710",
        "AI_Prospect_Value_Run_Id__c": "sales_prioritization_v1_1_test",
        "AI_Prospect_Value_Source__c": "crm_full_cached_broad_coverage_20260710",
        "AI_Prospect_Value_Updated_At__c": "2026-07-10T00:00:00Z",
    }
    validate_v1_1_payload(row)


def test_v11_payload_allows_existing_ladder_zero_lower_bound() -> None:
    row = {
        "Id": "001000000000000AAA",
        "AI_Prospect_Value_MCV_Point__c": "10",
        "AI_Prospect_Value_MCV_Low__c": "0",
        "AI_Prospect_Value_MCV_High__c": "20",
        "AI_Prospect_Value_ARR_Point__c": "8000",
        "AI_Prospect_Value_ARR_Range__c": "$7K-$10K",
        "AI_Prospect_Value_Confidence__c": "Low",
        "AI_Prospect_Value_ICP__c": "scorable",
        "AI_Prospect_Value_Action__c": "score_now",
        "AI_Prospect_Value_URL_Status__c": "no_url",
        "AI_Prospect_Value_Canonical_URL__c": "",
        "AI_Prospect_Value_Evidence__c": "fallback evidence",
        "AI_Prospect_Value_Components__c": "{}",
        "AI_Prospect_Value_Model_Version__c": "sales_prioritization_v1_1_20260710",
        "AI_Prospect_Value_Run_Id__c": "sales_prioritization_v1_1_test",
        "AI_Prospect_Value_Source__c": "crm_full_cached_broad_coverage_20260710",
        "AI_Prospect_Value_Updated_At__c": "2026-07-10T00:00:00Z",
    }
    validate_v1_1_payload(row)
