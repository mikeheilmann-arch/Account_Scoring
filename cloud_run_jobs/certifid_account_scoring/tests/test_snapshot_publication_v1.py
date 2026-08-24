from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import json

import pytest

from certifid_account_scoring.pipeline.contracts import (
    AccountDecision,
    Confidence,
    DesiredOperation,
)
from certifid_account_scoring.pipeline.publication import (
    CANARY_FIELDS,
    PROHIBITED_FIELDS,
    PublicationValidationError,
    changed_only_diff,
    decision_to_desired_state,
    rollback_rows,
    validate_payload_row,
    validate_salesforce_describe,
    verify_normalized_readback,
)
from certifid_account_scoring.pipeline.entity_resolution import resolve_website_binding
from certifid_account_scoring.pipeline.evidence_features import CachedEvidencePage, FeatureEligibility, build_evidence_features
from certifid_account_scoring.pipeline.lanes import LaneInput, classify_lane
from certifid_account_scoring.pipeline.sellable_unit import resolve_sellable_unit
from certifid_account_scoring.pipeline.snapshot import (
    EXPECTED_ACCOUNT_COUNT,
    EXPECTED_EXTRACTED_COUNT,
    load_snapshot,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def accepted_decision() -> AccountDecision:
    return AccountDecision(
        account_id="001TEST000000000001",
        account_name="Fixture Title LLC",
        website="https://fixture-title.example",
        sellable_unit_id="001TEST000000000001",
        surviving_account_id="001TEST000000000001",
        binding_status="confirmed",
        url_status="ok",
        lifecycle="net_new",
        lane="title_escrow",
        lane_subtype="title_escrow_operator",
        quality_disposition="accepted",
        desired_operation=DesiredOperation.PUBLISH_VALUE,
        accepted=True,
        final_mcv=75,
        final_mcv_low=40,
        final_mcv_high=120,
        final_arr=24_000,
        final_arr_low=18_000,
        final_arr_high=32_000,
        confidence=Confidence.HIGH,
        reason_codes=("fixture",),
        evidence_summary="Independent fixture evidence.",
        input_fingerprint="a" * 64,
        evidence_hash="b" * 64,
        run_id="account_scoring_v1_shadow_test",
        resolver_version="resolver-test",
        lane_version="lane-test",
        feature_version="features-test",
        mcv_model_version="mcv-test",
        arr_model_version="arr-test",
        source_version="source-test",
        evaluated_at="2026-07-10T12:00:00Z",
    )


@pytest.mark.private_artifacts
def test_repository_snapshot_reconciles_full_universe() -> None:
    snapshot = load_snapshot(REPOSITORY_ROOT)
    assert len(snapshot.accounts) == EXPECTED_ACCOUNT_COUNT
    assert len(snapshot.cached_scores) == EXPECTED_EXTRACTED_COUNT
    assert set(snapshot.quality_overlay) == set(snapshot.accounts)


@pytest.mark.private_artifacts
def test_actual_account_describe_accepts_real_candidate_schema() -> None:
    describe_path = (
        REPOSITORY_ROOT
        / "tmp/account_scoring_v2_shadow_20260710/salesforce_account_describe_readonly_2026-07-10.json"
    )
    describe = json.loads(describe_path.read_text(encoding="utf-8-sig"))
    row = decision_to_desired_state(accepted_decision())
    assert row is not None
    result = validate_salesforce_describe(
        describe,
        describe_source=str(describe_path),
        intended_rows=[row],
    )
    assert result.valid, result


def test_nonaccepted_decision_never_creates_payload() -> None:
    decision = replace(
        accepted_decision(),
        accepted=False,
        desired_operation=DesiredOperation.ROUTE_REVIEW_METADATA_ONLY,
        final_mcv=None,
        final_mcv_low=None,
        final_mcv_high=None,
        final_arr=None,
        final_arr_low=None,
        final_arr_high=None,
    )
    assert decision_to_desired_state(decision) is None


def test_payload_schema_excludes_website_and_deprecated_numeric_aliases() -> None:
    row = decision_to_desired_state(accepted_decision())
    assert row is not None
    assert tuple(row) == CANARY_FIELDS
    assert not (set(row) & PROHIBITED_FIELDS)
    assert "Website" not in row


def test_accepted_mcv_must_be_inside_interval() -> None:
    with pytest.raises(PublicationValidationError, match="violates"):
        decision_to_desired_state(replace(accepted_decision(), final_mcv=500))
    with pytest.raises(PublicationValidationError, match="ARR interval"):
        decision_to_desired_state(replace(accepted_decision(), final_arr=100_000))


def test_active_customer_and_duplicate_loser_cannot_publish() -> None:
    with pytest.raises(PublicationValidationError, match="Active customer"):
        decision_to_desired_state(replace(accepted_decision(), lifecycle="active_customer"))
    with pytest.raises(PublicationValidationError, match="Duplicate/child loser"):
        decision_to_desired_state(
            replace(accepted_decision(), surviving_account_id="001TEST000000000002")
        )


def test_changed_only_requires_current_state_and_drops_equal_row() -> None:
    row = decision_to_desired_state(accepted_decision())
    assert row is not None
    with pytest.raises(PublicationValidationError, match="Missing current"):
        changed_only_diff([row], {})
    assert changed_only_diff([row], {row["Id"]: dict(row)}) == []
    current = dict(row)
    current["AI_Prospect_Value_MCV_Point__c"] = "74"
    assert changed_only_diff([row], {row["Id"]: current}) == [row]


def test_blank_numeric_has_no_clear_semantics_in_first_canary() -> None:
    row = decision_to_desired_state(accepted_decision())
    assert row is not None
    unsafe = dict(row)
    unsafe["AI_Prospect_Value_MCV_Point__c"] = ""
    with pytest.raises(PublicationValidationError, match="blank numeric"):
        validate_payload_row(unsafe)
    unsafe = dict(row)
    unsafe["AI_Prospect_Value_Evidence__c"] = ""
    with pytest.raises(PublicationValidationError, match="blank required"):
        validate_payload_row(unsafe)


def test_rollback_is_scoped_to_success_ledger_and_preserves_null() -> None:
    account_id = accepted_decision().account_id
    backup = {field: "old" for field in CANARY_FIELDS}
    backup["Id"] = account_id
    backup["AI_Prospect_Value_ARR_Point__c"] = None
    current = {
        account_id: {
            "Id": account_id,
            "AI_Prospect_Value_Run_Id__c": "failed-run",
            "SystemModstamp": "2026-07-10T12:01:00Z",
        }
    }
    successful_modstamps = {account_id: "2026-07-10T12:01:00Z"}
    rows = rollback_rows(
        {account_id: backup},
        [account_id],
        expected_failed_run_id="failed-run",
        successful_systemmodstamp_by_id=successful_modstamps,
        current_by_id=current,
    )
    assert len(rows) == 1
    assert rows[0]["Id"] == account_id
    assert rows[0]["AI_Prospect_Value_ARR_Point__c"] == "#N/A"
    with pytest.raises(PublicationValidationError, match="Missing immutable backup"):
        rollback_rows(
            {},
            [account_id],
            expected_failed_run_id="failed-run",
            successful_systemmodstamp_by_id=successful_modstamps,
            current_by_id=current,
        )
    incomplete = dict(backup)
    incomplete.pop("AI_Prospect_Value_Evidence__c")
    with pytest.raises(PublicationValidationError, match="incomplete"):
        rollback_rows(
            {account_id: incomplete},
            [account_id],
            expected_failed_run_id="failed-run",
            successful_systemmodstamp_by_id=successful_modstamps,
            current_by_id=current,
        )
    with pytest.raises(PublicationValidationError, match="compare-and-swap"):
        rollback_rows(
            {account_id: backup},
            [account_id],
            expected_failed_run_id="failed-run",
            successful_systemmodstamp_by_id=successful_modstamps,
            current_by_id={
                account_id: {
                    "Id": account_id,
                    "AI_Prospect_Value_Run_Id__c": "later-run",
                    "SystemModstamp": "2026-07-10T12:01:00Z",
                }
            },
        )
    with pytest.raises(PublicationValidationError, match="SystemModstamp"):
        rollback_rows(
            {account_id: backup},
            [account_id],
            expected_failed_run_id="failed-run",
            successful_systemmodstamp_by_id=successful_modstamps,
            current_by_id={
                account_id: {
                    "Id": account_id,
                    "AI_Prospect_Value_Run_Id__c": "failed-run",
                    "SystemModstamp": "2026-07-10T12:02:00Z",
                }
            },
        )


def test_normalized_readback_is_exact_and_field_complete() -> None:
    row = decision_to_desired_state(accepted_decision())
    assert row is not None
    readback = dict(row)
    readback["AI_Prospect_Value_MCV_Point__c"] = "75.0"
    assert verify_normalized_readback([row], {row["Id"]: readback})["passed"] is True
    readback["AI_Prospect_Value_ARR_Point__c"] = "24001"
    report = verify_normalized_readback([row], {row["Id"]: readback})
    assert report["passed"] is False
    assert report["mismatches"][0]["field"] == "AI_Prospect_Value_ARR_Point__c"


def test_accepted_values_must_be_nonnegative_and_positive_points() -> None:
    with pytest.raises(PublicationValidationError, match="nonpositive MCV"):
        decision_to_desired_state(
            replace(accepted_decision(), final_mcv=-1, final_mcv_low=-2, final_mcv_high=0)
        )
    with pytest.raises(PublicationValidationError, match="nonpositive ARR"):
        decision_to_desired_state(
            replace(accepted_decision(), final_arr=-1, final_arr_low=-2, final_arr_high=0)
        )


def test_production_contract_chain_can_reach_high_eligible_without_anchor() -> None:
    row = {
        "Id": "001TESTCHAIN0000001",
        "Name": "Cedar Harbor Title LLC",
        "Website": "https://cedarharbortitle.com",
        "Type": "Prospect",
        "Active_Customer__c": "false",
        "Company_Type__c": "Title company",
        "BillingCity": "Olympia",
        "BillingState": "WA",
        "BillingCountry": "US",
        "Phone": "360-555-0101",
        "SiteOrganizationName": "Cedar Harbor Title",
        "AboutText": "Cedar Harbor Title provides title insurance, escrow, settlement and closing services.",
        "SiteCity": "Olympia",
        "SiteState": "WA",
        "SiteCountry": "US",
        "SitePhones": "360-555-0101",
        "DomainClusterSize": "1",
        "SourceObservedAt": "2026-07-08T12:00:00Z",
    }
    page = CachedEvidencePage(
        url="https://cedarharbortitle.com/contact",
        title="Contact Cedar Harbor Title",
        text=(
            "Our office 100 Harbor Street, Olympia, WA 98501. "
            "Jane Doe - Escrow Officer. Title insurance services, escrow services, "
            "settlement services and closing services."
        ),
        observed_at="2026-07-08T12:00:00Z",
    )
    binding = resolve_website_binding(row)
    sellable = resolve_sellable_unit(row, binding)
    lane = classify_lane(LaneInput.from_contracts(row, (page,), binding, sellable))
    features = build_evidence_features(
        row["Id"], (page,), FeatureEligibility.from_contracts(binding, sellable)
    )
    assert binding.status.value == "bound"
    assert binding.confidence.value == "High"
    assert sellable.confidence.value == "High"
    assert sellable.standalone_score_eligible is True
    assert lane.eligible_for_value is True
    assert lane.confidence.value == "High"
    assert features.operating_office_count == 1


def test_alta_state_name_and_code_are_equivalent_but_not_website_proof() -> None:
    row = {
        "Id": "001TESTALTA0000001",
        "Name": "Harbor Title LLC",
        "Website": "https://unresolved.example",
        "BillingState": "Florida",
        "ALTA_Member": "true",
        "ALTA_Company_Name": "Harbor Title LLC",
        "ALTA_State": "FL",
        "Match_Confidence": "high",
    }
    decision = resolve_website_binding(row)
    assert decision.alta_entity_confirmed is True
    assert decision.status.value != "bound"
    assert decision.alta_used_as_sole_website_proof is False
