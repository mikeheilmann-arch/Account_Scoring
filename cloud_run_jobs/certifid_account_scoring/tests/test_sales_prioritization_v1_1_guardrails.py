from __future__ import annotations

from certifid_account_scoring.pipeline.sales_prioritization_v1_1_guardrails import (
    _strong_closing_evidence,
    _title_anchor_corroborated,
)
from certifid_account_scoring.pipeline.salesforce_v1_1_staging import select_v1_1_canary


def test_service_keyword_without_relevant_staff_is_not_strong_operational_evidence() -> None:
    assert not _strong_closing_evidence(
        {
            "evidence_confidence": "High",
            "relevant_staff_count": "0",
            "operational_service_signals": '["title_insurance","abstracting"]',
        }
    )


def test_high_confidence_closing_signal_with_two_staff_is_strong_evidence() -> None:
    assert _strong_closing_evidence(
        {
            "evidence_confidence": "High",
            "relevant_staff_count": "2",
            "operational_service_signals": '["real_estate_closing"]',
        }
    )


def test_alta_and_title_identity_corroborate_untrusted_final_anchor() -> None:
    assert _title_anchor_corroborated(
        {"Name": "Harbor Title", "Company_Type__c": "Title company"},
        {"alta_confirmed": "true"},
        {},
        None,
    )


def test_guardrailed_canary_starts_with_exact_top_20_then_30_stratified() -> None:
    audit = {}
    for index in range(60):
        account_id = f"001000000000{index:03d}AAA"
        audit[account_id] = {
            "Name": f"Account {index:02d}",
            "score_source_tier": "tier_0_retained_v1" if index % 2 else "tier_1_trusted_anchor",
            "lane": "legal" if index % 3 == 0 else "title_escrow",
            "confidence": "Low" if index % 2 else "Medium",
            "mcv_band": "750+" if index < 20 else "100-249",
            "AI_Prospect_Value_MCV_Point__c": str(1000 - index),
            "AI_Prospect_Value_ARR_Point__c": str(200000 - index),
        }
    selected = select_v1_1_canary(audit, audit, run_id="test")
    expected_top20 = sorted(
        audit,
        key=lambda account_id: (
            -float(audit[account_id]["AI_Prospect_Value_ARR_Point__c"]),
            -float(audit[account_id]["AI_Prospect_Value_MCV_Point__c"]),
            audit[account_id]["Name"],
            account_id,
        ),
    )[:20]
    assert selected[:20] == expected_top20
    assert len(selected) == len(set(selected)) == 50
