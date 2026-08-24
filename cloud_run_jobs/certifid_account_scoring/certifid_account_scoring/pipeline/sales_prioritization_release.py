"""Build the directional Sales-prioritization V1 release population.

This module deliberately consumes the already-completed full-CRM scorer output.
It adds entity, ICP, sellable-unit, and evidence-strength controls without
recomputing MCV or ARR.  The output is a new immutable publication run; it does
not modify any of the strict architecture-review shadow artifacts.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

from .publication import CANARY_FIELDS, write_csv


MODEL_VERSION = "sales_prioritization_v1_20260710"
SOURCE = "crm_full_cached_directional_20260710"
EXPECTED_BASE_REVIEW_ACTION = "score_now"
MINIMUM_ACCEPTED_POPULATION = 3_000

SOURCE_COMPARE_FIELDS = (
    "Website",
    "Type",
    "Account_Status__c",
    "Company_Type__c",
    "Active_Customer__c",
    "ParentId",
)

AUDIT_FIELDS = (
    "Id",
    "Name",
    "lane",
    "entity_class",
    "entity_subtype",
    "binding_status",
    "relationship_status",
    "lifecycle_status",
    "source_confidence",
    "evidence_confidence",
    "mcv_band",
    "hard_failure_reasons",
    "quality_reason_codes",
    "source_score",
) + CANARY_FIELDS[1:]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def read_index(path: Path, key: str = "Id") -> dict[str, dict[str, str]]:
    rows = read_csv(path)
    return {str(row.get(key, "")).strip(): row for row in rows if str(row.get(key, "")).strip()}


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _lower(value: Any) -> str:
    return _text(value).lower()


def _truthy(value: Any) -> bool:
    return _lower(value) in {"1", "true", "yes", "y"}


def _number(value: Any) -> Decimal | None:
    text = _text(value).replace(",", "").replace("$", "")
    if not text:
        return None
    try:
        parsed = Decimal(text)
    except InvalidOperation:
        return None
    if not parsed.is_finite():
        return None
    return parsed


def _integer(value: Any) -> str:
    parsed = _number(value)
    if parsed is None:
        raise ValueError(f"Expected a finite numeric value, received {value!r}")
    return str(int(parsed.quantize(Decimal("1"), rounding=ROUND_HALF_UP)))


_MONEY_TOKEN = re.compile(r"^\s*\$?\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*([kKmM]?)\s*$")


def _parse_money_token(value: str) -> Decimal | None:
    match = _MONEY_TOKEN.match(value)
    if not match:
        return None
    parsed = Decimal(match.group(1).replace(",", ""))
    suffix = match.group(2).lower()
    if suffix == "k":
        parsed *= Decimal(1_000)
    elif suffix == "m":
        parsed *= Decimal(1_000_000)
    return parsed


def parse_arr_range(value: Any) -> tuple[Decimal, Decimal] | None:
    text = _text(value).replace("–", "-").replace("—", "-")
    if not text:
        return None
    parts = [part.strip() for part in text.split("-") if part.strip()]
    if len(parts) != 2:
        return None
    low = _parse_money_token(parts[0])
    high = _parse_money_token(parts[1])
    if low is None or high is None:
        return None
    return low, high


def _json_list(value: Any) -> list[str]:
    text = _text(value)
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return [text]
    if isinstance(parsed, list):
        return [_text(item) for item in parsed if _text(item)]
    return [_text(parsed)] if _text(parsed) else []


def _quality_reason_codes(binding: Mapping[str, str], sellable: Mapping[str, str]) -> list[str]:
    codes: list[str] = []
    for field in ("binding_reason_codes", "reason_codes"):
        for code in _json_list(binding.get(field)):
            if code not in codes:
                codes.append(code)
    for field in ("relationship_reason_codes", "lifecycle_reason_codes"):
        for code in _json_list(sellable.get(field)):
            if code not in codes:
                codes.append(code)
    return codes


def _numeric_failures(score: Mapping[str, str]) -> list[str]:
    failures: list[str] = []
    source_score = _number(score.get("Score"))
    point = _number(score.get("EstimatedMCV"))
    low = _number(score.get("EstimatedMCVLow"))
    high = _number(score.get("EstimatedMCVHigh"))
    arr = _number(score.get("EstimatedARR"))
    arr_range = parse_arr_range(score.get("ARRRange"))
    if source_score is None:
        failures.append("invalid_numeric_score")
    if point is None or low is None or high is None or low <= 0 or point <= 0 or high <= 0:
        failures.append("invalid_numeric_mcv")
    elif not (low <= point <= high):
        failures.append("invalid_mcv_interval")
    if arr is None or arr <= 0 or arr_range is None:
        failures.append("invalid_numeric_arr")
    elif arr_range[0] > arr_range[1] or not (arr_range[0] <= arr <= arr_range[1]):
        failures.append("invalid_arr_interval")
    return failures


def hard_failure_reasons(
    score: Mapping[str, str],
    account: Mapping[str, str],
    binding: Mapping[str, str],
    sellable: Mapping[str, str],
    lane: Mapping[str, str],
) -> list[str]:
    """Return only affirmative hard failures from the revised business policy."""

    failures = _numeric_failures(score)
    binding_status = _lower(binding.get("binding_status"))
    binding_confidence = _lower(binding.get("binding_confidence"))
    lifecycle = _lower(sellable.get("lifecycle_status"))
    lifecycle_confidence = _lower(sellable.get("lifecycle_confidence"))
    relationship = _lower(sellable.get("relationship_status"))
    relationship_confidence = _lower(sellable.get("relationship_confidence"))
    entity_class = _lower(lane.get("entity_class"))
    subtype = _lower(lane.get("entity_subtype"))
    lane_confidence = _lower(lane.get("classification_confidence"))

    if binding_status == "mismatch" and binding_confidence == "high":
        failures.append("confirmed_website_entity_mismatch")

    confirmed_active = _truthy(account.get("Active_Customer__c")) or _lower(account.get("Account_Status__c")) in {
        "active",
        "active customer",
        "customer",
    }
    confirmed_partner = lifecycle == "partner"
    confirmed_active = confirmed_active or lifecycle == "active_customer"
    if confirmed_active:
        failures.append("confirmed_active_customer")
    if confirmed_partner:
        failures.append("confirmed_partner")

    if relationship_confidence == "high":
        if relationship == "duplicate_loser":
            failures.append("verified_duplicate_loser")
        if relationship in {
            "non_independent_child",
            "branch",
            "dba",
            "owned_direct",
            "brokerage_affiliated",
            "parent_controlled",
        }:
            failures.append("confirmed_non_independent_sellable_unit")

    if entity_class == "adjacent" and lane_confidence == "high":
        failures.append("confirmed_non_icp_entity")
    if subtype in {"underwriter", "owned_direct", "brokerage_affiliated"} and lane_confidence == "high":
        failures.append(f"confirmed_{subtype}_non_independent")
    # The explicit abstract-only classification is itself the affirmative class
    # signal; evidence confidence continues to describe evidence strength and is
    # not used as a blanket suppression rule.
    if subtype == "abstract_only":
        failures.append("confirmed_abstract_only")
    if subtype == "non_closing_legal" and lane_confidence == "high":
        failures.append("confirmed_nonclosing_legal_non_icp")

    return list(dict.fromkeys(failures))


def _evidence_confidence(
    score: Mapping[str, str],
    binding: Mapping[str, str],
    sellable: Mapping[str, str],
    lane: Mapping[str, str],
) -> str:
    original = _text(score.get("Confidence")).title()
    binding_status = _lower(binding.get("binding_status"))
    binding_confidence = _lower(binding.get("binding_confidence"))
    lifecycle = _lower(sellable.get("lifecycle_status"))
    relationship = _lower(sellable.get("relationship_status"))
    lane_confidence = _lower(lane.get("classification_confidence"))

    if original == "Low" or lifecycle in {"winback", "unknown"}:
        return "Low"
    if relationship != "independent" or binding_status in {"ambiguous", "insufficient_evidence", "hygiene_blocked"}:
        return "Medium" if original in {"High", "Medium"} else "Low"
    if original == "High" and binding_status == "bound" and binding_confidence == "high" and lane_confidence == "high":
        return "High"
    return "Medium" if original in {"High", "Medium"} else "Low"


def _mcv_band(value: Any) -> str:
    parsed = _number(value)
    if parsed is None:
        return "invalid"
    if parsed < 50:
        return "low_lt_50"
    if parsed < 200:
        return "mid_50_199"
    return "high_200_plus"


def _selected_pages(score: Mapping[str, str]) -> list[str]:
    raw = _text(score.get("SelectedPages"))
    if not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return [part.strip() for part in raw.split("|") if part.strip().startswith(("http://", "https://"))]
    if isinstance(value, list):
        pages: list[str] = []
        for item in value:
            if isinstance(item, str) and item.startswith(("http://", "https://")):
                pages.append(item)
            elif isinstance(item, dict):
                candidate = _text(item.get("url") or item.get("URL"))
                if candidate.startswith(("http://", "https://")):
                    pages.append(candidate)
        return pages
    return []


def _canonical_url(score: Mapping[str, str], binding: Mapping[str, str]) -> str:
    pages = _selected_pages(score)
    if pages:
        return pages[0][:255]
    website = _text(binding.get("website"))
    if not website:
        return ""
    if not website.startswith(("http://", "https://")):
        website = f"https://{website}"
    try:
        parsed = urlparse(website)
    except ValueError:
        return ""
    if not parsed.netloc:
        return ""
    return website[:255]


def _url_status(score: Mapping[str, str], binding: Mapping[str, str]) -> str:
    status = _lower(score.get("RetrievalStatus"))
    binding_status = _lower(binding.get("binding_status"))
    if status in {"success", "ok", "cached", "complete"}:
        return "ok"
    if not _text(binding.get("website")):
        return "no_url"
    direct = {
        "blocked": "blocked",
        "dns_error": "dns_error",
        "timeout": "timeout",
        "ssl_error": "ssl_error",
        "server_error": "server_error",
        "redirects_unrelated": "redirects_unrelated",
        "not_run": "not_run",
    }
    if status in direct:
        return direct[status]
    if binding_status == "hygiene_blocked":
        reason_text = " ".join(_json_list(binding.get("binding_reason_codes"))).lower()
        if "park" in reason_text:
            return "parked_or_for_sale"
        if "dns" in reason_text:
            return "dns_error"
        return "error"
    return "error" if status else "not_run"


def _payload(
    score: Mapping[str, str],
    account: Mapping[str, str],
    binding: Mapping[str, str],
    sellable: Mapping[str, str],
    lane: Mapping[str, str],
    *,
    run_id: str,
    updated_at: str,
) -> dict[str, str]:
    evidence_confidence = _evidence_confidence(score, binding, sellable, lane)
    reasons = _quality_reason_codes(binding, sellable)
    lane_name = _lower(lane.get("lane")) or "review"
    evidence = " | ".join(
        part
        for part in (
            _text(score.get("Evidence")),
            f"lane={lane_name}",
            f"entity={_lower(lane.get('entity_subtype')) or _lower(lane.get('entity_class'))}",
            f"binding={_lower(binding.get('binding_status'))}",
            f"sellable_unit={_lower(sellable.get('relationship_status'))}",
            f"lifecycle={_lower(sellable.get('lifecycle_status'))}",
            "directional_sales_prioritization_v1",
        )
        if part
    )[:32768]
    components = {
        "semantic_version": "sales_prioritization_v1",
        "directional_use_only": True,
        "base_review_action": _text(score.get("ReviewAction")),
        "base_icp": _text(score.get("ICP")),
        "source_score": _text(score.get("Score")),
        "source_confidence": _text(score.get("Confidence")),
        "lane": lane_name,
        "entity_class": _lower(lane.get("entity_class")),
        "entity_subtype": _lower(lane.get("entity_subtype")),
        "lane_confidence": _text(lane.get("classification_confidence")),
        "binding_status": _lower(binding.get("binding_status")),
        "binding_confidence": _text(binding.get("binding_confidence")),
        "relationship_status": _lower(sellable.get("relationship_status")),
        "relationship_confidence": _text(sellable.get("relationship_confidence")),
        "lifecycle_status": _lower(sellable.get("lifecycle_status")),
        "lifecycle_confidence": _text(sellable.get("lifecycle_confidence")),
        "quality_reason_codes": reasons,
        "arr_semantics": "directional_proxy_not_finance_approved_forecast",
        "mcv_semantics": "guarded_existing_full_crm_scorer_output",
        "website_written": False,
        "run_id": run_id,
    }
    return {
        "Id": _text(score.get("Id")),
        "AI_Prospect_Value_MCV_Point__c": _integer(score.get("EstimatedMCV")),
        "AI_Prospect_Value_MCV_Low__c": _integer(score.get("EstimatedMCVLow")),
        "AI_Prospect_Value_MCV_High__c": _integer(score.get("EstimatedMCVHigh")),
        "AI_Prospect_Value_ARR_Point__c": _integer(score.get("EstimatedARR")),
        "AI_Prospect_Value_ARR_Range__c": _text(score.get("ARRRange"))[:50],
        "AI_Prospect_Value_Confidence__c": evidence_confidence,
        "AI_Prospect_Value_ICP__c": "scorable",
        "AI_Prospect_Value_Action__c": "score_now",
        "AI_Prospect_Value_URL_Status__c": _url_status(score, binding),
        "AI_Prospect_Value_Canonical_URL__c": _canonical_url(score, binding),
        "AI_Prospect_Value_Evidence__c": evidence,
        "AI_Prospect_Value_Components__c": json.dumps(components, sort_keys=True, separators=(",", ":")),
        "AI_Prospect_Value_Model_Version__c": MODEL_VERSION,
        "AI_Prospect_Value_Run_Id__c": run_id,
        "AI_Prospect_Value_Source__c": SOURCE,
        "AI_Prospect_Value_Updated_At__c": updated_at,
    }


def _assert_unique(rows: Iterable[Mapping[str, str]], label: str) -> None:
    ids = [_text(row.get("Id")) for row in rows]
    if not ids or any(not account_id for account_id in ids):
        raise ValueError(f"{label} contains a blank Id or is empty")
    duplicates = [account_id for account_id, count in Counter(ids).items() if count > 1]
    if duplicates:
        raise ValueError(f"{label} contains duplicate Id values: {duplicates[:10]}")


def _assert_known_controls(r3_dir: Path) -> None:
    fixture_path = r3_dir / "independent_fixture_results.json"
    readiness_path = r3_dir / "readiness_gates.json"
    schema_path = r3_dir / "salesforce_schema_validation.json"
    for path in (fixture_path, readiness_path, schema_path):
        if not path.exists():
            raise FileNotFoundError(f"Known-control artifact is missing: {path}")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    if int(fixture.get("failed_rows", -1)) != 0:
        raise RuntimeError(f"Known fixture-control failure blocks release: {fixture_path}")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    if schema.get("valid") is not True:
        raise RuntimeError(f"Known Salesforce schema-control failure blocks release: {schema_path}")
    required_passes = {
        "exact_21993_id_reconciliation",
        "zero_named_fixture_branches",
        "zero_crm_law_firm_lane_bypass",
        "zero_active_customer_net_new_publish",
        "zero_duplicate_loser_child_standalone_publish",
        "zero_fatal_fixture_negative_publish",
        "integrated_sellable_unit_controls",
        "integrated_title_legal_lane_controls",
        "integrated_entity_safe_feature_control",
        "account_website_prohibited",
        "complete_current_salesforce_describe",
    }
    readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
    statuses = {row.get("gate"): row.get("status") for row in readiness.get("gates", [])}
    failed = sorted(gate for gate in required_passes if statuses.get(gate) != "PASS")
    if failed:
        raise RuntimeError(f"Known release-relevant controls are not PASS: {failed}")


def _normalize_binding(row: Mapping[str, str]) -> dict[str, str]:
    return {
        **dict(row),
        "Id": _text(row.get("account_id")),
        "binding_status": _text(row.get("status")),
        "binding_confidence": _text(row.get("confidence")),
        "binding_reason_codes": _text(row.get("reason_codes")),
    }


def _normalize_sellable(row: Mapping[str, str]) -> dict[str, str]:
    return {
        **dict(row),
        "Id": _text(row.get("account_id")),
        "relationship_status": _text(row.get("relationship_type")),
        "relationship_confidence": _text(row.get("confidence")),
        "lifecycle_status": _text(row.get("lifecycle")),
        "lifecycle_confidence": _text(row.get("confidence")),
        "relationship_reason_codes": _text(row.get("reason_codes")),
        "lifecycle_reason_codes": _text(row.get("reason_codes")),
    }


def _normalize_lane(row: Mapping[str, str]) -> dict[str, str]:
    lane = _text(row.get("lane"))
    return {
        **dict(row),
        "Id": _text(row.get("account_id")),
        "entity_class": lane,
        "entity_subtype": _text(row.get("subtype")),
        "classification_confidence": _text(row.get("confidence")),
    }


def _funnel_rows(base_count: int, exclusion_counts: Counter[str], accepted_count: int) -> list[dict[str, Any]]:
    remaining = base_count
    rows: list[dict[str, Any]] = [
        {
            "step": 0,
            "stage": "existing_full_crm_score_now",
            "excluded_at_step": 0,
            "remaining": remaining,
        }
    ]
    for index, (reason, count) in enumerate(exclusion_counts.items(), start=1):
        remaining -= count
        rows.append({"step": index, "stage": reason, "excluded_at_step": count, "remaining": remaining})
    rows.append(
        {
            "step": len(rows),
            "stage": "sales_prioritization_v1_accepted",
            "excluded_at_step": 0,
            "remaining": accepted_count,
        }
    )
    return rows


def _first_failure(reasons: list[str]) -> str:
    priority = (
        "invalid_numeric_score",
        "invalid_numeric_mcv",
        "invalid_mcv_interval",
        "invalid_numeric_arr",
        "invalid_arr_interval",
        "confirmed_website_entity_mismatch",
        "confirmed_active_customer",
        "confirmed_partner",
        "verified_duplicate_loser",
        "confirmed_non_independent_sellable_unit",
        "confirmed_non_icp_entity",
        "confirmed_underwriter_non_independent",
        "confirmed_owned_direct_non_independent",
        "confirmed_brokerage_affiliated_non_independent",
        "confirmed_abstract_only",
        "confirmed_nonclosing_legal_non_icp",
    )
    return next((reason for reason in priority if reason in reasons), reasons[0])


def build_release_population(
    *,
    combined_scores_path: Path,
    accounts_path: Path,
    r3_dir: Path,
    output_dir: Path,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Build and write an immutable pre-publication release package."""

    if output_dir.exists():
        raise FileExistsError(f"Release output is immutable and already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    _assert_known_controls(r3_dir)

    built_at = utc_now()
    release_run_id = run_id or f"sales_prioritization_v1_{built_at:%Y%m%dT%H%M%SZ}"
    updated_at = iso_z(built_at)
    all_scores = read_csv(combined_scores_path)
    scores = [row for row in all_scores if _text(row.get("ReviewAction")) == EXPECTED_BASE_REVIEW_ACTION]
    _assert_unique(scores, "score_now base")

    accounts = read_index(accounts_path)
    bindings = {
        row["Id"]: row
        for raw in read_csv(r3_dir / "website_binding_decisions.csv")
        if (row := _normalize_binding(raw))["Id"]
    }
    sellable = {
        row["Id"]: row
        for raw in read_csv(r3_dir / "sellable_unit_membership.csv")
        if (row := _normalize_sellable(raw))["Id"]
    }
    lanes = {
        row["Id"]: row
        for raw in read_csv(r3_dir / "all_lane_decisions.csv")
        if (row := _normalize_lane(raw))["Id"]
    }

    missing_joins: dict[str, list[str]] = {"accounts": [], "binding": [], "sellable": [], "lane": []}
    accepted_audit: list[dict[str, str]] = []
    payload_rows: list[dict[str, str]] = []
    hard_queue: list[dict[str, str]] = []
    first_exclusions: Counter[str] = Counter()
    all_exclusions: Counter[str] = Counter()

    for score in scores:
        account_id = _text(score.get("Id"))
        account = accounts.get(account_id)
        binding = bindings.get(account_id)
        sale = sellable.get(account_id)
        lane = lanes.get(account_id)
        for label, value in (("accounts", account), ("binding", binding), ("sellable", sale), ("lane", lane)):
            if value is None:
                missing_joins[label].append(account_id)
        if account is None or binding is None or sale is None or lane is None:
            continue

        failures = hard_failure_reasons(score, account, binding, sale, lane)
        quality_reasons = _quality_reason_codes(binding, sale)
        if failures:
            first = _first_failure(failures)
            first_exclusions[first] += 1
            all_exclusions.update(failures)
            hard_queue.append(
                {
                    "Id": account_id,
                    "Name": _text(account.get("Name") or score.get("Name")),
                    "primary_hard_failure": first,
                    "hard_failure_reasons": json.dumps(failures),
                    "lane": _lower(lane.get("lane")),
                    "entity_class": _lower(lane.get("entity_class")),
                    "entity_subtype": _lower(lane.get("entity_subtype")),
                    "classification_confidence": _text(lane.get("classification_confidence")),
                    "binding_status": _lower(binding.get("binding_status")),
                    "binding_confidence": _text(binding.get("binding_confidence")),
                    "relationship_status": _lower(sale.get("relationship_status")),
                    "relationship_confidence": _text(sale.get("relationship_confidence")),
                    "lifecycle_status": _lower(sale.get("lifecycle_status")),
                    "lifecycle_confidence": _text(sale.get("lifecycle_confidence")),
                    "source_score": _text(score.get("Score")),
                    "source_confidence": _text(score.get("Confidence")),
                    "quality_reason_codes": json.dumps(quality_reasons),
                }
            )
            continue

        payload = _payload(score, account, binding, sale, lane, run_id=release_run_id, updated_at=updated_at)
        payload_rows.append(payload)
        accepted_audit.append(
            {
                "Id": account_id,
                "Name": _text(account.get("Name") or score.get("Name")),
                "lane": _lower(lane.get("lane")),
                "entity_class": _lower(lane.get("entity_class")),
                "entity_subtype": _lower(lane.get("entity_subtype")),
                "binding_status": _lower(binding.get("binding_status")),
                "relationship_status": _lower(sale.get("relationship_status")),
                "lifecycle_status": _lower(sale.get("lifecycle_status")),
                "source_confidence": _text(score.get("Confidence")),
                "evidence_confidence": payload["AI_Prospect_Value_Confidence__c"],
                "mcv_band": _mcv_band(score.get("EstimatedMCV")),
                "hard_failure_reasons": "[]",
                "quality_reason_codes": json.dumps(quality_reasons),
                "source_score": _text(score.get("Score")),
                **{field: payload[field] for field in CANARY_FIELDS[1:]},
            }
        )

    missing_counts = {label: len(ids) for label, ids in missing_joins.items()}
    if any(missing_counts.values()):
        missing_path = output_dir / "missing_join_ids.json"
        missing_path.write_text(json.dumps(missing_joins, indent=2, sort_keys=True), encoding="utf-8")
        raise RuntimeError(f"Missing source joins block release: {missing_counts}")
    if len(accepted_audit) < MINIMUM_ACCEPTED_POPULATION:
        raise RuntimeError(
            f"Accepted population {len(accepted_audit):,} is below {MINIMUM_ACCEPTED_POPULATION:,}; "
            f"largest exclusion is {first_exclusions.most_common(1)}"
        )

    _assert_unique(accepted_audit, "accepted population")
    _assert_unique(hard_queue, "hard-failure queue")
    accepted_audit.sort(key=lambda row: row["Id"])
    payload_rows.sort(key=lambda row: row["Id"])
    hard_queue.sort(key=lambda row: (row["primary_hard_failure"], row["Id"]))

    accepted_path = output_dir / "final_scored_population.csv"
    payload_path = output_dir / "accepted_full_payload.csv"
    queue_path = output_dir / "hard_failure_queue.csv"
    funnel_path = output_dir / "funnel_and_exclusions.csv"
    write_csv(accepted_path, accepted_audit, AUDIT_FIELDS)
    write_csv(payload_path, payload_rows, CANARY_FIELDS)
    write_csv(
        queue_path,
        hard_queue,
        (
            "Id",
            "Name",
            "primary_hard_failure",
            "hard_failure_reasons",
            "lane",
            "entity_class",
            "entity_subtype",
            "classification_confidence",
            "binding_status",
            "binding_confidence",
            "relationship_status",
            "relationship_confidence",
            "lifecycle_status",
            "lifecycle_confidence",
            "source_score",
            "source_confidence",
            "quality_reason_codes",
        ),
    )
    funnel_rows = _funnel_rows(len(scores), first_exclusions, len(accepted_audit))
    write_csv(funnel_path, funnel_rows, ("step", "stage", "excluded_at_step", "remaining"))

    confidence_counts = Counter(row["evidence_confidence"] for row in accepted_audit)
    lane_counts = Counter(row["lane"] for row in accepted_audit)
    mcv_band_counts = Counter(row["mcv_band"] for row in accepted_audit)
    lifecycle_counts = Counter(row["lifecycle_status"] for row in accepted_audit)
    relationship_counts = Counter(row["relationship_status"] for row in accepted_audit)
    summary = {
        "run_id": release_run_id,
        "model_version": MODEL_VERSION,
        "source": SOURCE,
        "built_at": updated_at,
        "base_population": len(scores),
        "accepted_population": len(accepted_audit),
        "hard_failure_population": len(hard_queue),
        "minimum_accepted_population": MINIMUM_ACCEPTED_POPULATION,
        "population_floor_passed": len(accepted_audit) >= MINIMUM_ACCEPTED_POPULATION,
        "first_exclusion_counts": dict(first_exclusions),
        "all_reason_counts_nonexclusive": dict(all_exclusions),
        "accepted_confidence_counts": dict(confidence_counts),
        "accepted_lane_counts": dict(lane_counts),
        "accepted_mcv_band_counts": dict(mcv_band_counts),
        "accepted_lifecycle_counts": dict(lifecycle_counts),
        "accepted_relationship_counts": dict(relationship_counts),
        "known_controls_passed": True,
        "source_hashes": {
            "combined_scores": sha256_file(combined_scores_path),
            "accounts": sha256_file(accounts_path),
            "website_binding_decisions": sha256_file(r3_dir / "website_binding_decisions.csv"),
            "sellable_unit_membership": sha256_file(r3_dir / "sellable_unit_membership.csv"),
            "all_lane_decisions": sha256_file(r3_dir / "all_lane_decisions.csv"),
        },
    }
    summary_path = output_dir / "population_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    report_definition = {
        "name": "Sales Prioritization V1 — Current Accepted Accounts",
        "primary_object": "Account",
        "filters": [
            {"field": "AI_Prospect_Value_Run_Id__c", "operator": "equals", "value": release_run_id},
            {"field": "AI_Prospect_Value_Model_Version__c", "operator": "equals", "value": MODEL_VERSION},
            {"field": "AI_Prospect_Value_Action__c", "operator": "equals", "value": "score_now"},
            {"field": "AI_Prospect_Value_ICP__c", "operator": "equals", "value": "scorable"},
        ],
        "columns": [
            "Account.Name",
            "Account.OwnerId",
            "Account.BillingState",
            *[f"Account.{field}" for field in CANARY_FIELDS[1:]],
        ],
        "default_sort": [
            {"field": "AI_Prospect_Value_ARR_Point__c", "direction": "descending"},
            {"field": "AI_Prospect_Value_Confidence__c", "direction": "descending"},
        ],
        "semantics": {
            "purpose": "directional Sales prioritization, not a Finance-approved forecast",
            "arr": "directional proxy from the existing guarded full-CRM scorer",
            "confidence": "evidence strength, not a probability of conversion or correctness",
            "population": "only this immutable run id; no Website writes and no clearing of excluded records",
        },
    }
    report_path = output_dir / "salesforce_report_definition.json"
    report_path.write_text(json.dumps(report_definition, indent=2, sort_keys=True), encoding="utf-8")

    artifact_paths = [accepted_path, payload_path, queue_path, funnel_path, summary_path, report_path]
    manifest = {
        "run_id": release_run_id,
        "created_at": updated_at,
        "immutable": True,
        "artifacts": [
            {"path": path.name, "sha256": sha256_file(path), "bytes": path.stat().st_size}
            for path in artifact_paths
        ],
    }
    manifest_path = output_dir / "prepublication_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return {**summary, "output_dir": str(output_dir), "manifest": str(manifest_path)}
