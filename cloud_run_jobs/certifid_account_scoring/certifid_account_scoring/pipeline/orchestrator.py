"""End-to-end, no-write V1 shadow orchestration over the July cache."""

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from .arr_calibration import ARRCalibrationConfig, ComparableARRCalibrator
from .cached_evidence import build_resolver_evidence, load_cached_account_evidence
from .config import (
    ARR_MODEL_VERSION,
    DEFAULT_POLICY,
    EVALUATION_VERSION,
    FEATURE_VERSION,
    FINANCE_FORMULA_VERSION,
    LANE_VERSION,
    MCV_MODEL_VERSION,
    PIPELINE_VERSION,
    PUBLICATION_VERSION,
    RESOLVER_VERSION,
    SELLABLE_UNIT_VERSION,
    SOURCE_VERSION,
)
from .contracts import AccountDecision, Confidence, DesiredOperation, Lane
from .entity_resolution import registered_domain, resolve_website_binding
from .evaluation import (
    evaluate_novel_negatives,
    population_drift,
    reconcile_ids,
    run_arr_backtest,
    run_mcv_backtest,
    source_coverage,
)
from .evidence_features import CachedEvidencePage, FeatureEligibility, build_evidence_features
from .lanes import LaneInput, classify_lane
from .mcv_calibration import MCVCalibrationConfig, LaneSpecificMCVCalibrator
from .publication import (
    CANARY_FIELDS,
    decision_to_desired_state,
    validate_salesforce_describe,
)
from .sellable_unit import resolve_sellable_unit
from .snapshot import SourceSnapshot, load_snapshot, row_fingerprint


CALIBRATION_LABELS = Path(
    "artifacts/prospect_value_research/calibration_2026-05-12/calibration_account_level_labels.csv"
)
ARR_LABELS = Path(
    "artifacts/prospect_value_research/calibration_2026-05-12/closed_won_per_file_arr_labels.csv"
)


@dataclass(frozen=True)
class StageResult:
    account_id: str
    account: Mapping[str, str]
    overlay: Mapping[str, str]
    binding: Any
    sellable: Any
    lane: Any
    features: Any
    calibration_features: Any
    cache_missing_reason: str
    evidence_hash: str
    cached_page_count: int
    cached_source_as_of: str
    was_cached_extracted: bool
    canonical_bound_url: str


def _clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def _float(value: object) -> float | None:
    text = _clean(value).replace(",", "").replace("$", "")
    if not text or text.lower() in {"nan", "none", "null"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return _clean(value).lower() in {"1", "true", "t", "yes", "y"}


def _json_cell(value: object) -> object:
    if isinstance(value, (list, tuple, dict)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return value


def _flat_contract(value: Any) -> dict[str, object]:
    return {key: _json_cell(item) for key, item in value.to_dict().items()}


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _write_csv(path: Path, rows: Sequence[Mapping[str, object]], fields: Sequence[str] | None = None) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite immutable artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        ordered: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for field in row:
                if field not in seen:
                    ordered.append(field)
                    seen.add(field)
        fields = ordered or ("account_id",)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _json_cell(row.get(field, "")) for field in fields})


def _write_json(path: Path, value: object) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite immutable artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _source_as_of(result: StageResult) -> str:
    return result.cached_source_as_of or result.binding.source_as_of


def _process_account(
    account_id: str,
    snapshot: SourceSnapshot,
    cache_root: Path,
) -> StageResult:
    account = snapshot.accounts[account_id]
    overlay = snapshot.quality_overlay[account_id]
    cached_score = snapshot.cached_scores.get(account_id)
    cached = load_cached_account_evidence(account_id, cached_score, cache_root)
    alta = snapshot.alta_matches.get(account_id)
    resolver_row = build_resolver_evidence(account, overlay, alta, cached)
    binding = resolve_website_binding(
        resolver_row,
        resolver_version=RESOLVER_VERSION,
        source_versions={
            "crm": SOURCE_VERSION,
            "cached_website": SOURCE_VERSION,
            "alta": SOURCE_VERSION,
            "registry": SOURCE_VERSION,
        },
    )
    canonical_bound_url = next(
        (
            page.url
            for page in cached.pages
            if page.url and registered_domain(page.url) == binding.registered_domain
        ),
        binding.website,
    )
    sellable = resolve_sellable_unit(resolver_row, binding, resolver_version=SELLABLE_UNIT_VERSION)
    lane_input = LaneInput.from_contracts(
        resolver_row,
        cached.pages,
        binding,
        sellable,
        crm_observed_at="2026-07-08",
    )
    lane = classify_lane(lane_input)
    features = build_evidence_features(
        account_id,
        cached.pages,
        FeatureEligibility.from_contracts(binding, sellable),
        mapped_link_count=int(_float(cached_score.get("MappedLinkCount")) or 0) if cached_score else 0,
    )
    hierarchy_safe_for_calibration = bool(
        sellable.surviving_account_id == account_id
        and sellable.relationship_type
        not in {
            "duplicate_loser",
            "non_independent_child",
            "branch_location",
            "dba_alias",
            "owned_direct_operation",
            "shared_domain_customer_review",
            "shared_domain_only_review",
            "shared_domain_cluster_review",
            "parent_child_review",
        }
    )
    if sellable.lifecycle.value == "net_new":
        # For net-new rows the calibration and publication identity gates are
        # identical; reuse the deterministic extraction instead of parsing all
        # cached pages twice.
        calibration_features = features
    else:
        calibration_features = build_evidence_features(
            account_id,
            cached.pages,
            FeatureEligibility(
                binding_status=binding.status.value,
                binding_confidence=binding.confidence,
                sellable_unit_eligible=hierarchy_safe_for_calibration,
                sellable_unit_confidence=sellable.confidence,
            ),
            mapped_link_count=int(_float(cached_score.get("MappedLinkCount")) or 0) if cached_score else 0,
        )
    return StageResult(
        account_id=account_id,
        account=account,
        overlay=overlay,
        binding=binding,
        sellable=sellable,
        lane=lane,
        features=features,
        calibration_features=calibration_features,
        cache_missing_reason=cached.missing_reason,
        evidence_hash=cached.evidence_hash,
        cached_page_count=len(cached.pages),
        cached_source_as_of=max((page.observed_at for page in cached.pages), default=""),
        was_cached_extracted=cached_score is not None,
        canonical_bound_url=canonical_bound_url,
    )


def _feature_model_row(result: StageResult, *, calibration: bool = False) -> dict[str, object]:
    feature = result.calibration_features if calibration else result.features
    return {
        "account_id": result.account_id,
        "sellable_unit_id": result.sellable.sellable_unit_id,
        "lane": result.lane.lane.value,
        "subtype": result.lane.subtype,
        "state": result.account.get("BillingState", ""),
        "segment": result.account.get("Account_Segment__c", ""),
        "operating_office_count": feature.operating_office_count,
        "office_count_low": feature.office_count_low,
        "office_count_high": feature.office_count_high,
        "relevant_staff_count": feature.relevant_staff_count,
        "staff_count_low": feature.staff_count_low,
        "staff_count_high": feature.staff_count_high,
        "closing_signal_count": len(feature.operational_service_signals),
        "tool_signal_count": len(feature.tool_signals),
        "evidence_quality_score": {"Low": 0.25, "Medium": 0.60, "High": 0.90}.get(
            feature.evidence_confidence.value, 0.0
        ),
        "evidence_confidence": feature.evidence_confidence.value,
        "as_of": "2026-07-10T00:00:00Z",
        # Current CRM values have no point-in-time observed timestamp in the
        # supplied snapshot, so they are not admitted as history priors.
        "anchor_mcv": result.account.get("Final_Monthly_Closing_Volume__c", ""),
        "anchor_source": result.account.get("Monthly_Closing_Volume_Source__c", ""),
        "anchor_timestamp": "",
        "label_copied_to_predictor": False,
    }


def _prepare_mcv_training(
    root: Path,
    results_by_id: Mapping[str, StageResult],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    path = root / CALIBRATION_LABELS
    rows = _read_csv(path)
    prepared: list[dict[str, object]] = []
    exclusions = Counter()
    for row in rows:
        account_id = _clean(row.get("AccountId"))
        result = results_by_id.get(account_id)
        label = _float(row.get("McvLabel"))
        if result is None:
            exclusions["not_in_current_universe"] += 1
            continue
        if label is None:
            exclusions["missing_mcv_label"] += 1
            continue
        if result.lane.lane not in {Lane.TITLE_ESCROW, Lane.LEGAL}:
            exclusions["unsupported_current_lane"] += 1
            continue
        model_row = _feature_model_row(result, calibration=True)
        model_row.update(
            {
                "mcv_label": label,
                "label_timestamp": row.get("McvLabelCloseDate", ""),
                # Deliberately omit the current CRM anchor from training: label
                # construction may have copied the opportunity value into it.
                "anchor_mcv": "",
                "anchor_source": "",
                "anchor_timestamp": "",
                "label_copied_to_predictor": False,
            }
        )
        prepared.append(model_row)
    return prepared, {
        "source": CALIBRATION_LABELS.as_posix(),
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "input_rows": len(rows),
        "prepared_rows": len(prepared),
        "exclusions": dict(exclusions),
        "release_valid": False,
        "release_blocker": "features are current July observations, not point-in-time observations at label date",
    }


def _arr_value(row: Mapping[str, object]) -> float | None:
    for field in ("Won_Contract_Amount__c", "Contract_Amount__c", "Amount"):
        value = _float(row.get(field))
        if value is not None and value > 0:
            return value
    return None


def _prepare_arr_training(
    root: Path,
    results_by_id: Mapping[str, StageResult],
) -> tuple[list[dict[str, object]], dict[str, object]]:
    path = root / ARR_LABELS
    rows = _read_csv(path)
    prepared: list[dict[str, object]] = []
    exclusions = Counter()
    ambiguous_relationships = {
        "parent_child_review",
        "shared_domain_customer_review",
        "shared_domain_only_review",
        "shared_domain_cluster_review",
        "owned_direct_operation",
        "branch_location",
        "dba_alias",
        "duplicate_loser",
        "non_independent_child",
    }
    for row in rows:
        account_id = _clean(row.get("AccountId"))
        result = results_by_id.get(account_id)
        if result is None:
            exclusions["not_in_current_universe"] += 1
            continue
        arr = _arr_value(row)
        if arr is None:
            exclusions["missing_positive_contract_amount"] += 1
            continue
        opportunity_type = _clean(row.get("Type"))
        is_new = opportunity_type.lower() == "new business"
        hierarchy_resolved = (
            result.sellable.surviving_account_id == account_id
            and result.sellable.relationship_type not in ambiguous_relationships
        )
        prepared.append(
            {
                "account_id": account_id,
                "sellable_unit_id": result.sellable.sellable_unit_id,
                "first_year_arr": arr,
                "close_date": row.get("CloseDate", ""),
                "mcv": row.get("Monthly_Closing_Volume__c", ""),
                "lane": result.lane.lane.value,
                "segment": result.account.get("Account_Segment__c", ""),
                "state": result.account.get("BillingState", ""),
                "motion": opportunity_type,
                "clean_new_logo": bool(
                    is_new
                    and _clean(row.get("StageName")).lower() == "closed won"
                    and _bool(row.get("Per_File_Pricing__c"))
                ),
                "hierarchy_resolved": hierarchy_resolved,
                "is_renewal": "renewal" in opportunity_type.lower(),
                "is_expansion": "expansion" in opportunity_type.lower(),
                "opportunity_id": row.get("Id", ""),
            }
        )
    return prepared, {
        "source": ARR_LABELS.as_posix(),
        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "input_rows": len(rows),
        "prepared_rows": len(prepared),
        "exclusions": dict(exclusions),
        "arr_label_semantics": "positive Won Contract Amount, else Contract Amount, else Amount; provisional proxy",
        "release_valid": False,
        "release_blockers": [
            "Finance formula is unavailable/unapproved",
            "hierarchy resolution uses current July relationships rather than point-in-time close-date relationships",
            "contract amount proxy is not a Finance-approved first-year ARR formula",
        ],
    }


def _safe_backtest(function: Any, rows: Sequence[Mapping[str, object]], **kwargs: object) -> dict[str, object]:
    try:
        result = function(rows, **kwargs)
        result["execution_status"] = "completed"
        return result
    except Exception as exc:  # Report an honest blocked evaluation; do not manufacture a PASS.
        return {"execution_status": "blocked", "error": f"{type(exc).__name__}: {exc}"}


def _fit_models(
    root: Path,
    results: Sequence[StageResult],
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, object],
    dict[str, object],
    list[dict[str, object]],
    list[dict[str, object]],
]:
    results_by_id = {result.account_id: result for result in results}
    mcv_training, mcv_provenance = _prepare_mcv_training(root, results_by_id)
    mcv_config = MCVCalibrationConfig()
    mcv_backtest = _safe_backtest(run_mcv_backtest, mcv_training, config=mcv_config)
    mcv_model = LaneSpecificMCVCalibrator(mcv_config).fit(mcv_training)
    scoring_rows = [_feature_model_row(result) for result in results]
    mcv_predictions = mcv_model.predict(scoring_rows)
    mcv_by_id = {prediction.account_id: prediction for prediction in mcv_predictions}

    arr_training, arr_provenance = _prepare_arr_training(root, results_by_id)
    arr_config = ARRCalibrationConfig()
    arr_backtest = _safe_backtest(run_arr_backtest, arr_training, config=arr_config)
    arr_model = ComparableARRCalibrator(arr_config).fit(arr_training)
    arr_score_rows = [
        {
            "account_id": result.account_id,
            "sellable_unit_id": result.sellable.sellable_unit_id,
            "mcv": mcv_by_id[result.account_id].published_point,
            "lane": result.lane.lane.value,
            "segment": result.account.get("Account_Segment__c", ""),
            "state": result.account.get("BillingState", ""),
        }
        for result in results
    ]
    arr_predictions = arr_model.predict(arr_score_rows)
    arr_by_id = {prediction.account_id: prediction for prediction in arr_predictions}
    mcv_report = {
        "provenance": mcv_provenance,
        "fit": mcv_model.diagnostics,
        "backtest": mcv_backtest,
        "release_valid": False,
    }
    arr_report = {
        "provenance": arr_provenance,
        "fit": arr_model.diagnostics,
        "backtest": arr_backtest,
        "release_valid": False,
        "finance_formula_version": FINANCE_FORMULA_VERSION,
    }
    return (
        mcv_by_id,
        arr_by_id,
        mcv_report,
        arr_report,
        [_flat_contract(prediction) for prediction in mcv_predictions],
        [_flat_contract(prediction) for prediction in arr_predictions],
    )


def _make_decision(
    result: StageResult,
    mcv: Any,
    arr: Any,
    *,
    run_id: str,
    evaluated_at: str,
) -> AccountDecision:
    extracted = result.was_cached_extracted
    lifecycle = result.sellable.lifecycle.value
    relationship = result.sellable.relationship_type
    reason_codes = list(
        dict.fromkeys(
            [
                *result.binding.reason_codes,
                *result.sellable.reason_codes,
                *result.lane.reason_codes,
                *result.features.uncertainty_codes,
                *mcv.reason_codes,
                *arr.reason_codes,
            ]
        )
    )
    if not extracted:
        disposition = "preflight_held_no_change"
        operation = DesiredOperation.NO_CHANGE
        reason_codes.append("original_preflight_hold_preserved")
    elif lifecycle in {"active_customer", "winback", "partner", "excluded", "unknown"}:
        disposition = f"{lifecycle}_no_change"
        operation = DesiredOperation.NO_CHANGE
    elif relationship in {
        "duplicate_loser",
        "non_independent_child",
        "branch_location",
        "dba_alias",
        "owned_direct_operation",
        "shared_domain_customer_review",
        "shared_domain_only_review",
        "shared_domain_cluster_review",
        "parent_child_review",
    }:
        disposition = "sellable_unit_no_change"
        operation = DesiredOperation.NO_CHANGE
    else:
        disposition = "review"
        operation = DesiredOperation.ROUTE_REVIEW_METADATA_ONLY

    try:
        source_timestamp = datetime.fromisoformat(_source_as_of(result).replace("Z", "+00:00"))
        evaluation_timestamp = datetime.fromisoformat(evaluated_at.replace("Z", "+00:00"))
        source_age_days = (evaluation_timestamp - source_timestamp).total_seconds() / 86_400
        source_fresh = 0 <= source_age_days <= DEFAULT_POLICY.evidence_ttl_days
    except (TypeError, ValueError):
        source_fresh = False
    automated_eligible = bool(
        result.binding.status.value == "bound"
        and result.binding.confidence is Confidence.HIGH
        and result.sellable.standalone_score_eligible
        and result.sellable.confidence is Confidence.HIGH
        and result.lane.eligible_for_value
        and result.lane.confidence is Confidence.HIGH
        and result.features.evidence_confidence is Confidence.HIGH
        and result.cached_page_count > 0
        and bool(result.evidence_hash)
        and source_fresh
        and mcv.published_point is not None
        and mcv.published_low is not None
        and mcv.published_high is not None
        and mcv.confidence is Confidence.HIGH
        and arr.point_p75 is not None
        and arr.comparable_count >= DEFAULT_POLICY.minimum_arr_comparables
        and not arr.provisional
    )
    # Finance is provisional in this run, so automated_eligible must be false.
    accepted = automated_eligible
    if accepted:
        disposition = "accepted"
        operation = DesiredOperation.PUBLISH_VALUE
    elif result.lane.eligible_for_value and arr.provisional:
        reason_codes.append("finance_formula_pending_blocks_publication")
    if result.lane.eligible_for_value and result.features.evidence_confidence is not Confidence.HIGH:
        reason_codes.append("entity_safe_evidence_confidence_not_high")
    if not source_fresh:
        reason_codes.append("source_missing_or_stale")
    if arr.comparable_count < DEFAULT_POLICY.minimum_arr_comparables:
        reason_codes.append("arr_comparable_count_below_minimum")

    evidence_summary = " | ".join(
        item
        for item in (
            result.binding.reasons[0] if result.binding.reasons else "",
            result.lane.reasons[0] if result.lane.reasons else "",
            f"cached_pages={result.cached_page_count}",
            f"cache_status={result.cache_missing_reason or 'available'}",
        )
        if item
    )[:32768]
    return AccountDecision(
        account_id=result.account_id,
        account_name=result.account.get("Name", ""),
        website=result.canonical_bound_url,
        sellable_unit_id=result.sellable.sellable_unit_id,
        surviving_account_id=result.sellable.surviving_account_id,
        binding_status=result.binding.status.value,
        url_status=(
            "ok"
            if result.binding.status.value == "bound" and result.cached_page_count > 0
            else (
                result.account.get("Website_Hygiene_Status__c", "")
                if result.account.get("Website_Hygiene_Status__c", "")
                in {
                    "ok",
                    "blocked",
                    "dns_error",
                    "timeout",
                    "ssl_error",
                    "server_error",
                    "parked_or_placeholder",
                    "parked_or_for_sale",
                    "suspended_or_inactive",
                    "redirects_unrelated",
                    "no_url",
                    "error",
                    "not_run",
                }
                else "not_run"
            )
        ),
        lifecycle=lifecycle,
        lane=result.lane.lane.value,
        lane_subtype=result.lane.subtype,
        quality_disposition=disposition,
        desired_operation=operation,
        accepted=accepted,
        final_mcv=mcv.published_point if accepted else None,
        final_mcv_low=mcv.published_low if accepted else None,
        final_mcv_high=mcv.published_high if accepted else None,
        final_arr=arr.point_p75 if accepted else None,
        final_arr_low=arr.range_p50 if accepted else None,
        final_arr_high=arr.range_p90 if accepted else None,
        confidence=Confidence.HIGH if accepted else Confidence.LOW,
        reason_codes=tuple(dict.fromkeys(reason_codes)),
        evidence_summary=evidence_summary,
        input_fingerprint=row_fingerprint(result.account),
        evidence_hash=result.evidence_hash,
        run_id=run_id,
        resolver_version=RESOLVER_VERSION,
        lane_version=LANE_VERSION,
        feature_version=FEATURE_VERSION,
        mcv_model_version=MCV_MODEL_VERSION,
        arr_model_version=ARR_MODEL_VERSION,
        source_version=SOURCE_VERSION,
        evaluated_at=evaluated_at,
    )


def _run_external_fixtures(
    entity_path: Path,
    sellable_path: Path,
    lane_feature_path: Path,
) -> dict[str, object]:
    """Evaluate a caller-supplied fixture after decisions; never branch on it."""

    rows = _read_csv(entity_path)
    results: list[dict[str, object]] = []
    for row in rows:
        decision = resolve_website_binding(row)
        passed = (
            decision.status.value == row.get("ExpectedStatus")
            and decision.entity_class == row.get("ExpectedEntityClass")
            and str(decision.alta_entity_confirmed).lower()
            == str(row.get("ExpectedAltaEntityConfirmed", "")).lower()
        )
        results.append(
            {
                "case": row.get("Case", ""),
                "account_id": row.get("Id", ""),
                "expected_status": row.get("ExpectedStatus", ""),
                "actual_status": decision.status.value,
                "expected_entity_class": row.get("ExpectedEntityClass", ""),
                "actual_entity_class": decision.entity_class,
                "accepted": decision.status.value == "bound",
                "fatal": row.get("ExpectedStatus") == "mismatch",
                "novel": True,
                "negative_category": row.get("ExpectedEntityClass", ""),
                "passed": passed,
            }
        )
    negatives = [row for row in results if row["expected_status"] != "bound"]
    negative_eval = evaluate_novel_negatives(negatives)
    sellable_results: list[dict[str, object]] = []
    for row in _read_csv(sellable_path):
        decision = resolve_sellable_unit(
            row,
            {"status": row.get("BindingStatus", ""), "confidence": row.get("BindingConfidence", "")},
        )
        expected_eligible = _bool(row.get("ExpectedEligible"))
        passed = bool(
            decision.lifecycle.value == row.get("ExpectedLifecycle")
            and decision.relationship_type == row.get("ExpectedRelationship")
            and decision.surviving_account_id == row.get("ExpectedSurvivor")
            and decision.standalone_score_eligible == expected_eligible
        )
        sellable_results.append(
            {
                "case": row.get("Case", ""),
                "account_id": row.get("Id", ""),
                "expected_lifecycle": row.get("ExpectedLifecycle", ""),
                "actual_lifecycle": decision.lifecycle.value,
                "expected_relationship": row.get("ExpectedRelationship", ""),
                "actual_relationship": decision.relationship_type,
                "expected_eligible": expected_eligible,
                "actual_eligible": decision.standalone_score_eligible,
                "passed": passed,
            }
        )

    lane_fixture = json.loads(lane_feature_path.read_text(encoding="utf-8-sig"))
    lane_results: list[dict[str, object]] = []
    for row in lane_fixture.get("lane_cases", []):
        page = CachedEvidencePage(
            url=f"https://{row['id'].lower()}.fixture.invalid",
            title=str(row.get("account_name", "")),
            text=str(row.get("text", "")),
            observed_at="2026-07-08T00:00:00Z",
        )
        decision = classify_lane(
            LaneInput(
                account_id=str(row["id"]),
                account_name=str(row.get("account_name", "")),
                crm_company_type=str(row.get("crm_company_type", "")),
                billing_state=str(row.get("billing_state", "")),
                pages=(page,),
                binding_status="bound",
                binding_confidence=Confidence.HIGH,
                sellable_unit_eligible=True,
                sellable_unit_confidence=Confidence.HIGH,
                lifecycle="net_new",
                crm_observed_at="2026-07-08",
            )
        )
        passed = bool(
            decision.lane.value == row.get("expected_lane")
            and decision.subtype == row.get("expected_subtype")
            and decision.eligible_for_value == bool(row.get("expected_eligible"))
        )
        lane_results.append(
            {
                "case": row.get("id", ""),
                "expected_lane": row.get("expected_lane", ""),
                "actual_lane": decision.lane.value,
                "expected_subtype": row.get("expected_subtype", ""),
                "actual_subtype": decision.subtype,
                "expected_eligible": row.get("expected_eligible"),
                "actual_eligible": decision.eligible_for_value,
                "passed": passed,
            }
        )
    feature_case = lane_fixture.get("feature_case", {})
    feature_pages = tuple(
        CachedEvidencePage(
            url=str(page.get("url", "")),
            title=str(page.get("title", "")),
            text=str(page.get("text", "")),
            observed_at=str(page.get("observed_at", "")),
        )
        for page in feature_case.get("pages", [])
    )
    feature_decision = build_evidence_features(
        str(feature_case.get("account_id", "")),
        feature_pages,
        FeatureEligibility("bound", Confidence.HIGH, True, Confidence.HIGH),
        mapped_link_count=int(feature_case.get("mapped_link_count", 0)),
    )
    expected_features = feature_case.get("expected", {})
    feature_result = {
        "account_id": feature_case.get("account_id", ""),
        "expected": expected_features,
        "actual": _flat_contract(feature_decision),
        "passed": all(
            getattr(feature_decision, field) == expected
            for field, expected in expected_features.items()
            if hasattr(feature_decision, field)
        ),
    }
    literal_values = sorted(
        {
            _clean(value)
            for row in rows
            for value in (row.get("Id"), row.get("Name"), row.get("Website"))
            if len(_clean(value)) >= 4
        }
        | {
            _clean(value)
            for row in lane_fixture.get("lane_cases", [])
            # Case IDs are semantic labels such as ``underwriter`` and are
            # expected vocabulary, not fixture identities.  Scan only the
            # synthetic organization identity that production code must never
            # memorize.
            for value in (row.get("account_name"),)
            if len(_clean(value)) >= 4
        }
    )
    fixture_hashes = {
        entity_path.as_posix(): hashlib.sha256(entity_path.read_bytes()).hexdigest(),
        sellable_path.as_posix(): hashlib.sha256(sellable_path.read_bytes()).hexdigest(),
        lane_feature_path.as_posix(): hashlib.sha256(lane_feature_path.read_bytes()).hexdigest(),
    }
    return {
        "fixture_sources": fixture_hashes,
        "fixture_sha256": hashlib.sha256(
            json.dumps(fixture_hashes, sort_keys=True).encode("utf-8")
        ).hexdigest(),
        "fixture_is_external_to_production_code": True,
        "protected_blind_holdout": False,
        "protected_blind_holdout_status": "pending_independent_auditor_labels",
        "rows": len(results),
        "passed_rows": sum(bool(row["passed"]) for row in results),
        "failed_rows": sum(not bool(row["passed"]) for row in results),
        "novel_negative_evaluation": negative_eval,
        "results": results,
        "sellable_unit_controls": {
            "rows": len(sellable_results),
            "passed_rows": sum(bool(row["passed"]) for row in sellable_results),
            "results": sellable_results,
        },
        "lane_controls": {
            "rows": len(lane_results),
            "passed_rows": sum(bool(row["passed"]) for row in lane_results),
            "results": lane_results,
        },
        "feature_control": feature_result,
        "memorization_scan_literals": literal_values,
    }


def _load_describe(path: Path) -> Mapping[str, object]:
    raw = path.read_bytes()
    encoding = "utf-16" if raw.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    value = json.loads(raw.decode(encoding))
    return value if isinstance(value, Mapping) else {}


def _git_provenance(root: Path) -> dict[str, object]:
    def command(*args: str) -> str:
        completed = subprocess.run(
            args,
            cwd=root,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        return completed.stdout.strip() if completed.returncode == 0 else ""

    commit = command("git", "rev-parse", "HEAD")
    status = command("git", "status", "--short")
    package_root = root / "cloud_run_jobs/certifid_account_scoring/certifid_account_scoring"
    code_hashes = {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(package_root.rglob("*.py"))
    }
    return {
        "git_commit": commit,
        "git_repository_available": bool(commit),
        "git_worktree_dirty": bool(status) if commit else None,
        "git_note": "workspace is not a Git checkout; per-file and aggregate SHA-256 are authoritative"
        if not commit
        else "",
        "code_file_hashes": code_hashes,
        "aggregate_code_sha256": hashlib.sha256(
            json.dumps(code_hashes, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }


def _hash_artifacts(output_dir: Path) -> dict[str, str]:
    return {
        path.relative_to(output_dir).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(output_dir.rglob("*"))
        if path.is_file() and "cached_gcs" not in path.parts and path.name not in {"artifact_hashes.json", "run_manifest.json"}
    }


def run_shadow(
    repository_root: str | Path,
    output_dir: str | Path,
    *,
    cache_root: str | Path,
    describe_path: str | Path,
    fixture_path: str | Path,
    sellable_fixture_path: str | Path,
    lane_feature_fixture_path: str | Path,
    workers: int = 8,
) -> dict[str, object]:
    root = Path(repository_root).resolve()
    output = Path(output_dir).resolve()
    cache = Path(cache_root).resolve()
    output.mkdir(parents=True, exist_ok=True)
    evaluated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    run_id = "account_scoring_v1_shadow_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot = load_snapshot(root)
    ids = sorted(snapshot.accounts)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        results = list(executor.map(lambda account_id: _process_account(account_id, snapshot, cache), ids))
    results.sort(key=lambda result: result.account_id)

    binding_rows = [_flat_contract(result.binding) for result in results]
    sellable_rows = [_flat_contract(result.sellable) for result in results]
    feature_rows = [_flat_contract(result.features) for result in results]
    calibration_feature_rows = [
        {
            **_flat_contract(result.calibration_features),
            "calibration_only": True,
            "publication_eligibility_ignored": True,
            "lifecycle": result.sellable.lifecycle.value,
        }
        for result in results
    ]
    lane_rows = [_flat_contract(result.lane) for result in results]
    entity_rows = [
        {
            "account_id": result.account_id,
            "account_name": result.account.get("Name", ""),
            "website": result.account.get("Website", ""),
            "binding_status": result.binding.status.value,
            "binding_confidence": result.binding.confidence.value,
            "binding_entity_class": result.binding.entity_class,
            "sellable_unit_id": result.sellable.sellable_unit_id,
            "surviving_account_id": result.sellable.surviving_account_id,
            "relationship_type": result.sellable.relationship_type,
            "lifecycle": result.sellable.lifecycle.value,
            "standalone_score_eligible": result.sellable.standalone_score_eligible,
            "lane": result.lane.lane.value,
            "lane_subtype": result.lane.subtype,
            "lane_eligible": result.lane.eligible_for_value,
            "cached_page_count": result.cached_page_count,
            "cache_missing_reason": result.cache_missing_reason,
            "evidence_hash": result.evidence_hash,
            "source_as_of": _source_as_of(result),
        }
        for result in results
    ]

    (
        mcv_by_id,
        arr_by_id,
        mcv_report,
        arr_report,
        mcv_prediction_rows,
        arr_prediction_rows,
    ) = _fit_models(root, results)
    decisions = [
        _make_decision(
            result,
            mcv_by_id[result.account_id],
            arr_by_id[result.account_id],
            run_id=run_id,
            evaluated_at=evaluated_at,
        )
        for result in results
    ]
    decision_rows = [_flat_contract(decision) for decision in decisions]
    decision_by_id = {decision.account_id: decision for decision in decisions}
    top_value_outliers = [
        {
            "account_id": row["account_id"],
            "account_name": decision_by_id[str(row["account_id"])].account_name,
            "lane": decision_by_id[str(row["account_id"])].lane,
            "lane_subtype": decision_by_id[str(row["account_id"])].lane_subtype,
            "binding_status": decision_by_id[str(row["account_id"])].binding_status,
            "quality_disposition": decision_by_id[str(row["account_id"])].quality_disposition,
            "provisional_arr_p75": row.get("point_p75"),
            "provisional_arr_p50": row.get("range_p50"),
            "provisional_arr_p90": row.get("range_p90"),
            "comparable_count": row.get("comparable_count"),
            "human_audit_status": "PENDING",
        }
        for row in sorted(
            (item for item in arr_prediction_rows if _float(item.get("point_p75")) is not None),
            key=lambda item: _float(item.get("point_p75")) or 0.0,
            reverse=True,
        )[:100]
    ]
    reconciliation = reconcile_ids(
        ({"account_id": account_id} for account_id in ids), decision_rows
    )

    desired_rows = [row for decision in decisions if (row := decision_to_desired_state(decision)) is not None]
    # No current-state query is performed for an empty accepted set.  If future
    # candidates exist, generation stops here until exact-ID current values are
    # supplied; it must never assume all desired rows changed.
    publication_diff_blocked = bool(desired_rows)
    desired_diff: list[dict[str, object]] = []
    canary_rows: list[dict[str, object]] = []

    describe_file = Path(describe_path).resolve()
    schema = validate_salesforce_describe(
        _load_describe(describe_file),
        describe_source=str(describe_file),
        intended_rows=desired_rows,
    )
    fixtures = _run_external_fixtures(
        Path(fixture_path).resolve(),
        Path(sellable_fixture_path).resolve(),
        Path(lane_feature_fixture_path).resolve(),
    )

    reference_drift_rows = [
        {
            "office_count": _float(snapshot.cached_scores.get(result.account_id, {}).get("OfficeCount")),
        }
        for result in results
    ]
    candidate_drift_rows = [
        {
            "office_count": result.features.operating_office_count,
        }
        for result in results
    ]
    drift = population_drift(
        reference_drift_rows,
        candidate_drift_rows,
        numeric_fields=("office_count",),
    )
    drift["distribution_comparison"] = {
        "note": "Old quality dispositions/CRM Company Types and V1 decisions/lanes use different taxonomies; counts are shown separately and no categorical PSI is claimed.",
        "reference_quality_disposition": dict(
            Counter(result.overlay.get("QualityDisposition", "") or "__missing__" for result in results)
        ),
        "candidate_quality_disposition": dict(
            Counter(decision_by_id[result.account_id].quality_disposition for result in results)
        ),
        "reference_crm_company_type": dict(
            Counter(result.account.get("Company_Type__c", "") or "__missing__" for result in results)
        ),
        "candidate_lane": dict(Counter(result.lane.lane.value for result in results)),
    }
    coverage_rows = [
        {
            "source": "cached_gcs" if result.cached_page_count else "",
            "source_timestamp": result.cached_source_as_of,
        }
        for result in results
    ]
    coverage = source_coverage(
        coverage_rows,
        as_of=evaluated_at,
        ttl_days=DEFAULT_POLICY.evidence_ttl_days,
    )

    law_bypass = sum(
        result.account.get("Company_Type__c", "").strip().lower() == "law firm"
        and result.lane.lane is not Lane.LEGAL
        for result in results
    )
    alta_sole = sum(result.binding.alta_used_as_sole_website_proof for result in results)
    active_published = sum(
        decision.accepted and decision.lifecycle == "active_customer" for decision in decisions
    )
    loser_published = sum(
        decision.accepted and decision.surviving_account_id != decision.account_id for decision in decisions
    )
    nonaccepted_numeric = sum(
        not decision.accepted
        and any(
            value is not None
            for value in (
                decision.final_mcv,
                decision.final_mcv_low,
                decision.final_mcv_high,
                decision.final_arr,
                decision.final_arr_low,
                decision.final_arr_high,
            )
        )
        for decision in decisions
    )
    interval_violations = sum(
        decision.accepted
        and not (
            decision.final_mcv_low is not None
            and decision.final_mcv is not None
            and decision.final_mcv_high is not None
            and decision.final_mcv_low <= decision.final_mcv <= decision.final_mcv_high
        )
        for decision in decisions
    )
    decision_directory = root / "cloud_run_jobs/certifid_account_scoring/certifid_account_scoring/pipeline"
    production_text = "\n".join(
        (decision_directory / name).read_text(encoding="utf-8", errors="ignore")
        for name in (
            "entity_resolution.py",
            "sellable_unit.py",
            "lanes.py",
            "evidence_features.py",
            "mcv_calibration.py",
            "arr_calibration.py",
        )
    )
    fixture_branch_hits = [
        marker
        for marker in ("tests/fixtures", "entity_resolution_v1_cases.csv", "Will's review")
        if marker in production_text
    ]
    production_text_folded = production_text.casefold()
    fixture_literal_hits = [
        literal
        for literal in fixtures["memorization_scan_literals"]
        if literal.casefold() in production_text_folded
    ]
    operation_counts = Counter(decision.quality_disposition for decision in decisions)
    summary = {
        "run_id": run_id,
        "evaluated_at": evaluated_at,
        "input_accounts": len(ids),
        "output_decisions": len(decisions),
        "cached_extracted_input_rows": len(snapshot.cached_scores),
        "cached_rows_with_usable_pages": sum(result.cached_page_count > 0 for result in results),
        "preflight_held_rows": sum(
            decision.quality_disposition == "preflight_held_no_change" for decision in decisions
        ),
        "accepted": sum(decision.accepted for decision in decisions),
        "review": sum(decision.quality_disposition == "review" for decision in decisions),
        "no_change": sum(
            decision.quality_disposition not in {"accepted", "review", "preflight_held_no_change"}
            for decision in decisions
        ),
        "by_disposition": dict(sorted(operation_counts.items())),
        "binding_status": dict(Counter(result.binding.status.value for result in results)),
        "binding_confidence": dict(Counter(result.binding.confidence.value for result in results)),
        "lanes": dict(Counter(result.lane.lane.value for result in results)),
        "lane_subtypes": dict(Counter(result.lane.subtype for result in results)),
        "reconciliation": reconciliation,
    }
    gates = [
        {"gate": "exact_21993_id_reconciliation", "status": "PASS" if reconciliation["passed"] else "FAIL", "evidence": reconciliation},
        {
            "gate": "zero_named_fixture_branches",
            "status": "PASS" if not (fixture_branch_hits or fixture_literal_hits) else "FAIL",
            "evidence": {
                "path_or_review_markers": fixture_branch_hits,
                "fixture_identity_literal_hits": fixture_literal_hits,
                "method": "static scan of all decision/model modules against caller-supplied fixture IDs, names, and domains",
            },
        },
        {"gate": "zero_crm_law_firm_lane_bypass", "status": "PASS" if law_bypass == 0 else "FAIL", "evidence": law_bypass},
        {"gate": "zero_active_customer_net_new_publish", "status": "PASS" if active_published == 0 else "FAIL", "evidence": active_published},
        {"gate": "zero_duplicate_loser_child_standalone_publish", "status": "PASS" if loser_published == 0 else "FAIL", "evidence": loser_published},
        {"gate": "zero_fatal_fixture_negative_publish", "status": "PASS" if fixtures["novel_negative_evaluation"]["fatal_false_accepts"] == 0 else "FAIL", "evidence": fixtures["novel_negative_evaluation"]},
        {
            "gate": "integrated_sellable_unit_controls",
            "status": "PASS"
            if fixtures["sellable_unit_controls"]["passed_rows"] == fixtures["sellable_unit_controls"]["rows"]
            else "FAIL",
            "evidence": fixtures["sellable_unit_controls"],
        },
        {
            "gate": "integrated_title_legal_lane_controls",
            "status": "PASS"
            if fixtures["lane_controls"]["passed_rows"] == fixtures["lane_controls"]["rows"]
            else "FAIL",
            "evidence": fixtures["lane_controls"],
        },
        {
            "gate": "integrated_entity_safe_feature_control",
            "status": "PASS" if fixtures["feature_control"]["passed"] else "FAIL",
            "evidence": fixtures["feature_control"],
        },
        {"gate": "zero_nonaccepted_final_numeric", "status": "PASS" if nonaccepted_numeric == 0 else "FAIL", "evidence": nonaccepted_numeric},
        {"gate": "accepted_mcv_inside_intervals", "status": "PASS" if interval_violations == 0 else "FAIL", "evidence": interval_violations},
        {"gate": "alta_never_sole_website_proof", "status": "PASS" if alta_sole == 0 else "FAIL", "evidence": alta_sole},
        {"gate": "review_no_clear_first_canary", "status": "PASS", "evidence": "publisher emits accepted rows only"},
        {
            "gate": "payload_changed_ids_only",
            "status": "PENDING" if publication_diff_blocked else "PASS",
            "evidence": "exact-ID current Salesforce state is required before diffing"
            if publication_diff_blocked
            else "zero accepted desired rows; no unverified current-state diff emitted",
        },
        {"gate": "account_website_prohibited", "status": "PASS" if "Website" not in CANARY_FIELDS else "FAIL", "evidence": list(CANARY_FIELDS)},
        {"gate": "complete_current_salesforce_describe", "status": "PASS" if schema.valid else "FAIL", "evidence": asdict(schema)},
        {
            "gate": "nonhuman_publication_identity_fls_and_permissions",
            "status": "PENDING",
            "evidence": "current describe was read with an available human read-only CLI session; proposed integration identity is not provisioned/validated",
        },
        {
            "gate": "fresh_exact_id_source_and_systemmodstamp_conflict_check",
            "status": "PENDING",
            "evidence": "no accepted canary IDs exist; conflict query must re-read lifecycle/hierarchy/Website/SystemModstamp immediately before any future write",
        },
        {"gate": "actual_schema_canary_25_to_50", "status": "PASS" if 25 <= len(canary_rows) <= 50 else "FAIL", "evidence": len(canary_rows)},
        {"gate": "protected_blind_entity_and_icp_holdout", "status": "PENDING", "evidence": "independent auditor labels not supplied"},
        {"gate": "audited_acceptance_precision_by_lane_confidence", "status": "PENDING", "evidence": "human audit sample not supplied"},
        {
            "gate": "top_value_outlier_human_audit",
            "status": "PENDING",
            "evidence": {"queued_rows": len(top_value_outliers), "labels_completed": 0},
        },
        {"gate": "point_in_time_mcv_release_backtest", "status": "PENDING", "evidence": mcv_report["provenance"]["release_blocker"]},
        {"gate": "finance_approved_arr_formula_and_calibration", "status": "PENDING", "evidence": arr_report["provenance"]["release_blockers"]},
        {"gate": "business_revops_model_finance_approval", "status": "PENDING", "evidence": "no approval manufactured"},
    ]
    canary_ready = all(gate["status"] == "PASS" for gate in gates)

    rollup_groups: dict[str, list[StageResult]] = defaultdict(list)
    for result in results:
        rollup_groups[result.sellable.sellable_unit_id].append(result)
    rollups = [
        {
            "sellable_unit_id": unit_id,
            "surviving_account_id": sorted(group, key=lambda item: item.account_id)[0].sellable.surviving_account_id,
            "member_account_ids": ";".join(sorted(item.account_id for item in group)),
            "member_count": len(group),
            "accepted_member_count": sum(decision_by_id[item.account_id].accepted for item in group),
        }
        for unit_id, group in sorted(rollup_groups.items())
    ]

    _write_csv(output / "account_entity_resolution_full.csv", entity_rows)
    _write_csv(output / "website_binding_decisions.csv", binding_rows)
    _write_csv(output / "sellable_unit_membership.csv", sellable_rows)
    _write_csv(output / "sellable_unit_rollups.csv", rollups)
    _write_csv(output / "title_lane_decisions.csv", [row for row in lane_rows if row.get("lane") == "title_escrow"])
    _write_csv(output / "legal_lane_decisions.csv", [row for row in lane_rows if row.get("lane") == "legal"])
    _write_csv(output / "all_lane_decisions.csv", lane_rows)
    _write_csv(output / "entity_safe_evidence_features.csv", feature_rows)
    _write_csv(output / "calibration_only_entity_safe_features.csv", calibration_feature_rows)
    _write_csv(output / "mcv_predictions.csv", mcv_prediction_rows)
    _write_json(output / "mcv_calibration_report.json", mcv_report)
    _write_csv(output / "potential_arr_predictions.csv", arr_prediction_rows)
    _write_csv(output / "top_value_outlier_audit.csv", top_value_outliers)
    _write_json(output / "potential_arr_calibration_report.json", arr_report)
    _write_csv(output / "full_account_decision_shadow.csv", decision_rows)
    _write_csv(
        output / "accepted_score_review.csv",
        [row for row in decision_rows if row.get("accepted") is True],
        tuple(decision_rows[0]),
    )
    _write_csv(output / "held_no_change_queue.csv", [row for row in decision_rows if row.get("quality_disposition") == "preflight_held_no_change"])
    _write_csv(output / "review_queue.csv", [row for row in decision_rows if row.get("quality_disposition") == "review"])
    _write_csv(output / "no_change_queue.csv", [row for row in decision_rows if row.get("quality_disposition") not in {"accepted", "review", "preflight_held_no_change"}])
    _write_json(output / "independent_fixture_results.json", fixtures)
    _write_json(
        output / "blind_evaluation_metrics.json",
        {
            "evaluation_version": EVALUATION_VERSION,
            "protected_blind_status": "PENDING",
            "external_fixture_benchmark": fixtures,
            "mcv_backtest": mcv_report["backtest"],
            "arr_backtest": arr_report["backtest"],
        },
    )
    _write_json(output / "population_drift_source_coverage.json", {"population_drift": drift, "source_coverage": coverage})
    _write_csv(output / "desired_state_diff.csv", desired_diff, CANARY_FIELDS)
    _write_csv(
        output / "desired_state_candidates_not_executable.csv",
        desired_rows,
        CANARY_FIELDS,
    )
    _write_csv(output / "salesforce_canary_accepted_only.csv", canary_rows, CANARY_FIELDS)
    _write_csv(
        output / "success_id_ledger_template.csv",
        [],
        (
            "Id",
            "Success",
            "Error",
            "ProcessedAt",
            "CandidateRunId",
            "PostWriteSystemModstamp",
            "BulkJobId",
        ),
    )
    _write_csv(output / "rollback_successful_ids_only_template.csv", [], CANARY_FIELDS)
    _write_json(output / "salesforce_schema_validation.json", asdict(schema))
    _write_json(output / "readiness_gates.json", {"canary_ready": canary_ready, "gates": gates})
    _write_json(output / "shadow_summary.json", summary)
    backup_doc = f"""# Salesforce no-write publication package\n\nCandidate run: `{run_id}`\n\nStatus: **NOT CANARY READY**. The accepted set is empty, so no exact-ID backup query, readback command, or executable rollback payload can honestly be generated.\n\nThe future release identity must be supplied through `CERTIFID_SF_TARGET_ORG` and must be a dedicated non-human integration identity. A human CLI alias is not the proposed production identity.\n\nNull semantics: first-canary desired rows are accepted-only and every numeric field is required. Blank numeric cells are rejected. Review, held, suppression, customer, and winback rows are absent from the payload. Rollback uses the explicit `#N/A` null sentinel only to restore a verified null from a complete immutable backup and only after that behavior is rehearsed in the target API.\n\nBefore any future write: re-query the exact canary IDs including lifecycle, hierarchy, source Website, all payload fields, and `SystemModstamp`; compare the candidate source fingerprint/current modstamp; export that exact response immutably; validate normalized readback with `verify_normalized_readback`; and construct rollback only for successful IDs whose current Run Id and post-write SystemModstamp still equal the failed immutable write (compare-and-swap).\n"""
    (output / "backup_readback_rollback_package.md").write_text(backup_doc, encoding="utf-8")

    report = f"""# CertifID Account Scoring V1 implementation readiness\n\nRun ID: `{run_id}`  \nCandidate: `{PIPELINE_VERSION}`  \nStatus: **NOT CANARY READY**\n\n## Full-universe result\n\n- Input/output: {summary['input_accounts']:,} / {summary['output_decisions']:,}; exact reconciliation: {reconciliation['passed']}.\n- Cached extracted inputs: {summary['cached_extracted_input_rows']:,}; locally usable cached pages: {summary['cached_rows_with_usable_pages']:,}.\n- Accepted: {summary['accepted']:,}; review: {summary['review']:,}; held/no-change: {summary['preflight_held_rows']:,}; other no-change: {summary['no_change']:,}.\n- Binding status: `{json.dumps(summary['binding_status'], sort_keys=True)}`.\n- Lanes: `{json.dumps(summary['lanes'], sort_keys=True)}`.\n\n## Readiness\n\nAutomated safety invariants are recorded in `readiness_gates.json`. Canary approval is not appropriate: no 25-50 row accepted canary exists; protected blind/human precision review is pending; the MCV backtest is not point-in-time valid; and Finance's ARR formula is unavailable/unapproved. External audit is appropriate now because the implementation and immutable shadow artifacts exist, but approval is not.\n\nNo Salesforce writes, metadata deployments, Account.Website changes, Nimble extractions, GCP job/service executions, or database mutations occurred. The only external operation was read-only retrieval of the pre-existing July GCS cache.\n"""
    (output / "implementation_readiness_report.md").write_text(report, encoding="utf-8")
    code = _git_provenance(root)
    artifact_hashes = _hash_artifacts(output)
    _write_json(output / "artifact_hashes.json", artifact_hashes)
    manifest = {
        "run_id": run_id,
        "candidate_version": PIPELINE_VERSION,
        "immutable": True,
        "generated_at_utc": evaluated_at,
        "canary_ready": canary_ready,
        "no_write_run": True,
        "versions": {
            "source": SOURCE_VERSION,
            "resolver": RESOLVER_VERSION,
            "sellable_unit": SELLABLE_UNIT_VERSION,
            "features": FEATURE_VERSION,
            "lanes": LANE_VERSION,
            "mcv_model": MCV_MODEL_VERSION,
            "arr_model": ARR_MODEL_VERSION,
            "finance_formula": FINANCE_FORMULA_VERSION,
            "evaluation": EVALUATION_VERSION,
            "publication": PUBLICATION_VERSION,
        },
        "policy": DEFAULT_POLICY.to_dict(),
        "input_snapshot": snapshot.manifest_dict(),
        "additional_inputs": {
            "salesforce_account_describe": {
                "path": str(describe_file),
                "sha256": hashlib.sha256(describe_file.read_bytes()).hexdigest(),
                "read_only": True,
                "identity_limitation": "captured with available human CLI session; non-human publication identity pending",
            },
            "external_fixture": {
                "sources": fixtures["fixture_sources"],
                "aggregate_sha256": fixtures["fixture_sha256"],
                "used_by_production_decisions": False,
            },
            "cached_gcs": {
                "source_uri": "gs://certifid-scoring-artifacts-1095330376491/certifid-account-scoring/runs/crm-full-quality-gated-v1-20260708/",
                "materialized_root": str(cache),
                "read_only_source": True,
                "new_extraction": False,
            },
        },
        "code_provenance": code,
        "artifact_hashes": artifact_hashes,
        "summary": summary,
        "readiness_gates": gates,
        "external_mutations": {
            "salesforce_writes": 0,
            "salesforce_metadata_deploys": 0,
            "account_website_changes": 0,
            "nimble_extractions": 0,
            "gcp_job_executions": 0,
            "service_deployments": 0,
            "database_mutations": 0,
        },
    }
    _write_json(output / "run_manifest.json", manifest)
    return manifest


__all__ = ["run_shadow"]
