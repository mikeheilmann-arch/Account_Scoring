"""Final no-write rank guardrails for Sales Prioritization V1.1.

This module consumes the approved 16,924-row V1.1 audit.  It never changes
population membership: it only demotes unsupported inherited legal estimates
and uncorroborated 750-MCV Final anchors, then rebuilds the directional ARR
ladder and immutable review artifacts.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence

from ..scoring.run_greenfield_nimble_test import mcv_to_band
from .publication import CANARY_FIELDS, write_csv
from .sales_prioritization_release import (
    _lower,
    _mcv_band,
    _number,
    _text,
    iso_z,
    read_csv,
    sha256_file,
    utc_now,
)


MODEL_VERSION = "sales_prioritization_v1_1_guardrailed_20260710"
SOURCE_VERSION = "crm_full_cached_broad_coverage_guardrailed_20260710"
RECENT_OPPORTUNITY_CUTOFF = date(2024, 7, 10)
LEGAL_SUBTYPES = {"unclear_broad_practice", "non_closing_legal"}
LEGAL_CONSERVATIVE_CEILING = 75
TITLE_CONSERVATIVE_CEILING = 225
TRUSTED_REP_TOKENS = ("sales rep", "account executive", "bdr", " ae", "rep")
TITLE_NAME_RE = re.compile(r"\b(title|escrow|settlement|abstract|closing|closings|land title)\b", re.I)
STRONG_CLOSING_SIGNALS = {
    "closing",
    "closings",
    "escrow",
    "settlement",
    "title_insurance",
    "real_estate_closing",
    "conveyancing",
    "abstracting",
}

GUARDRAIL_FIELDS = (
    "guardrail_applied",
    "guardrail_reason",
    "pre_guardrail_mcv",
    "post_guardrail_mcv",
    "pre_guardrail_arr",
    "post_guardrail_arr",
    "guardrail_support_codes",
    "recent_opportunity_stage",
    "recent_opportunity_close_date",
    "recent_opportunity_mcv",
    "strong_closing_operational_evidence",
)


def _index(path: Path, key: str) -> dict[str, dict[str, str]]:
    csv.field_size_limit(sys.maxsize)
    return {row[key]: row for row in read_csv(path) if _text(row.get(key))}


def _truthy(value: Any) -> bool:
    return _lower(value) in {"true", "1", "yes", "y"}


def _parse_date(value: Any) -> date | None:
    text = _text(value)
    try:
        return date.fromisoformat(text[:10]) if text else None
    except ValueError:
        return None


def _signals(row: Mapping[str, str]) -> set[str]:
    try:
        value = json.loads(_text(row.get("operational_service_signals")) or "[]")
    except json.JSONDecodeError:
        return set()
    return {_lower(item) for item in value if _text(item)}


def _strong_closing_evidence(row: Mapping[str, str]) -> bool:
    # A service word alone is not operational proof.  Require High evidence
    # confidence and at least two named/relevant closing staff.
    return (
        _lower(row.get("evidence_confidence")) == "high"
        and int(_number(row.get("relevant_staff_count")) or 0) >= 2
        and bool(_signals(row) & STRONG_CLOSING_SIGNALS)
    )


def _recent_opportunities(path: Path) -> dict[str, dict[str, str]]:
    output: dict[str, dict[str, str]] = {}
    for row in read_csv(path):
        account_id = _text(row.get("AccountId"))
        stage = _text(row.get("StageName"))
        close_date = _parse_date(row.get("CloseDate"))
        mcv = _number(row.get("Monthly_Closing_Volume__c"))
        if (
            not account_id
            or stage not in {"Closed Won", "Closed Lost"}
            or close_date is None
            or close_date < RECENT_OPPORTUNITY_CUTOFF
            or mcv is None
            or mcv <= 0
        ):
            continue
        current = output.get(account_id)
        if current is None or _text(row.get("CloseDate")) > _text(current.get("CloseDate")):
            output[account_id] = row
    return output


def _band_values(point: int) -> dict[str, str]:
    low, high, score, arr, arr_range = mcv_to_band(point)
    return {
        "AI_Prospect_Value_MCV_Point__c": str(point),
        "AI_Prospect_Value_MCV_Low__c": str(low),
        "AI_Prospect_Value_MCV_High__c": str(high),
        "AI_Prospect_Value_ARR_Point__c": str(arr),
        "AI_Prospect_Value_ARR_Range__c": arr_range,
        "source_score": str(score),
        "mcv_band": _mcv_band(str(point)),
    }


def _support_codes(
    account: Mapping[str, str],
    audit: Mapping[str, str],
    evidence: Mapping[str, str],
    recent_opp: Mapping[str, str] | None,
) -> list[str]:
    codes: list[str] = []
    strong_ops = _strong_closing_evidence(evidence)
    if recent_opp:
        codes.append("recent_closed_won_or_lost_opportunity_mcv")
    if strong_ops:
        codes.append("strong_real_estate_closing_operational_evidence")
    source = _lower(account.get("Monthly_Closing_Volume_Source__c"))
    trusted_rep = any(token in source for token in TRUSTED_REP_TOKENS)
    if trusted_rep and (_truthy(audit.get("alta_confirmed")) or strong_ops):
        codes.append("trusted_rep_anchor_with_corroborating_closing_evidence")
    return codes


def _title_anchor_corroborated(
    account: Mapping[str, str],
    audit: Mapping[str, str],
    evidence: Mapping[str, str],
    recent_opp: Mapping[str, str] | None,
) -> bool:
    if recent_opp or _strong_closing_evidence(evidence):
        return True
    title_identity = (
        _lower(account.get("Company_Type__c")) in {"title company", "escrow company"}
        or bool(TITLE_NAME_RE.search(_text(account.get("Name"))))
    )
    return _truthy(audit.get("alta_confirmed")) and title_identity


def _update_components(row: dict[str, str], *, run_id: str, reason: str, support: Sequence[str]) -> None:
    try:
        components = json.loads(_text(row.get("AI_Prospect_Value_Components__c")) or "{}")
    except json.JSONDecodeError:
        components = {}
    components.update(
        {
            "semantic_version": "sales_prioritization_v1_1_guardrailed",
            "top_rank_guardrail_applied": bool(reason),
            "top_rank_guardrail_reason": reason,
            "top_rank_guardrail_support_codes": list(support),
            "run_id": run_id,
        }
    )
    row["AI_Prospect_Value_Components__c"] = json.dumps(
        components, sort_keys=True, separators=(",", ":")
    )


def _top_rows(rows: Sequence[Mapping[str, str]], size: int = 20) -> list[dict[str, str]]:
    return [
        dict(row)
        for row in sorted(
            rows,
            key=lambda row: (
                -float(row["AI_Prospect_Value_ARR_Point__c"]),
                -float(row["AI_Prospect_Value_MCV_Point__c"]),
                _text(row.get("Name")),
                row["Id"],
            ),
        )[:size]
    ]


def apply_v1_1_top_rank_guardrails(
    *,
    scored_audit_path: Path,
    full_decisions_path: Path,
    lane_path: Path,
    evidence_path: Path,
    accounts_path: Path,
    opportunity_labels_path: Path,
    output_dir: Path,
    run_id: str | None = None,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"Guardrailed V1.1 output is immutable and exists: {output_dir}")
    output_dir.mkdir(parents=True)
    built_at = utc_now()
    release_run_id = run_id or f"sales_prioritization_v1_1_guardrailed_{built_at:%Y%m%dT%H%M%SZ}"
    updated_at = iso_z(built_at)

    scored = read_csv(scored_audit_path)
    decisions = read_csv(full_decisions_path)
    lanes = _index(lane_path, "account_id")
    evidence = _index(evidence_path, "account_id")
    accounts = _index(accounts_path, "Id")
    recent_opps = _recent_opportunities(opportunity_labels_path)
    if len(scored) != 16_924 or len(decisions) != 21_993:
        raise RuntimeError("Guardrail input must be the approved 16,924/21,993 V1.1 population")
    if {row["Id"] for row in scored} - set(accounts):
        raise RuntimeError("Guardrail input contains Accounts outside the CRM snapshot")

    scored_out: list[dict[str, str]] = []
    by_id: dict[str, dict[str, str]] = {}
    reason_counts: Counter[str] = Counter()
    support_counts: Counter[str] = Counter()
    preserved_candidate_count = 0

    for original in scored:
        row = dict(original)
        account_id = row["Id"]
        account = accounts[account_id]
        lane = lanes[account_id]
        evidence_row = evidence[account_id]
        recent_opp = recent_opps.get(account_id)
        pre_mcv = int(float(row["AI_Prospect_Value_MCV_Point__c"]))
        pre_arr = row["AI_Prospect_Value_ARR_Point__c"]
        support = _support_codes(account, row, evidence_row, recent_opp)
        reason = ""
        ceiling: int | None = None

        broad_legal_candidate = (
            row["lane"] == "legal"
            and _lower(lane.get("subtype")) in LEGAL_SUBTYPES
            and row["score_source_tier"] in {"tier_0_retained_v1", "tier_2_usable_website_score"}
            and pre_mcv >= 200
        )
        if broad_legal_candidate:
            if support:
                preserved_candidate_count += 1
            else:
                ceiling = LEGAL_CONSERVATIVE_CEILING
                reason = "unsupported_200_plus_broad_or_non_closing_legal_estimate"

        final_anchor_750_candidate = (
            row["score_source_tier"] == "tier_1_trusted_anchor"
            and row["score_source_detail"] == "trusted_final_mcv"
            and int(float(row.get("trusted_anchor_used") or 0)) >= 750
            and _lower(row.get("binding_status")) in {"mismatch", "insufficient", "insufficient_evidence"}
        )
        if final_anchor_750_candidate:
            corroborated = (
                _title_anchor_corroborated(account, row, evidence_row, recent_opp)
                if row["lane"] == "title_escrow"
                else bool(support)
            )
            if corroborated:
                preserved_candidate_count += 1
            else:
                ceiling = TITLE_CONSERVATIVE_CEILING if row["lane"] == "title_escrow" else LEGAL_CONSERVATIVE_CEILING
                reason = "uncorroborated_750_final_mcv_with_untrusted_binding"

        if ceiling is not None and pre_mcv > ceiling:
            row.update(_band_values(ceiling))
            row["AI_Prospect_Value_Confidence__c"] = "Low"
            row["confidence"] = "Low"
            row["anchor_rail_applied"] = "true"
            row["trusted_anchor_used"] = str(ceiling) if final_anchor_750_candidate else row.get("trusted_anchor_used", "")
            codes = json.loads(row.get("fallback_reason_codes") or "[]")
            codes.append(f"top_rank_guardrail:{reason}:ceiling={ceiling}")
            row["fallback_reason_codes"] = json.dumps(codes, separators=(",", ":"))
            row["fallback_reason_primary"] = f"top_rank_guardrail:{reason}"
            row["AI_Prospect_Value_Evidence__c"] = (
                _text(row.get("AI_Prospect_Value_Evidence__c"))
                + f" | top_rank_guardrail={reason} | ceiling={ceiling}"
            )[:32768]
            reason_counts[reason] += 1
        for code in support:
            support_counts[code] += 1

        row.update(
            {
                "guardrail_applied": "true" if reason else "false",
                "guardrail_reason": reason,
                "pre_guardrail_mcv": str(pre_mcv),
                "post_guardrail_mcv": row["AI_Prospect_Value_MCV_Point__c"],
                "pre_guardrail_arr": pre_arr,
                "post_guardrail_arr": row["AI_Prospect_Value_ARR_Point__c"],
                "guardrail_support_codes": json.dumps(support, separators=(",", ":")),
                "recent_opportunity_stage": _text(recent_opp.get("StageName")) if recent_opp else "",
                "recent_opportunity_close_date": _text(recent_opp.get("CloseDate")) if recent_opp else "",
                "recent_opportunity_mcv": _text(recent_opp.get("Monthly_Closing_Volume__c")) if recent_opp else "",
                "strong_closing_operational_evidence": "true" if _strong_closing_evidence(evidence_row) else "false",
                "AI_Prospect_Value_Model_Version__c": MODEL_VERSION,
                "AI_Prospect_Value_Run_Id__c": release_run_id,
                "AI_Prospect_Value_Source__c": SOURCE_VERSION,
                "AI_Prospect_Value_Updated_At__c": updated_at,
            }
        )
        _update_components(row, run_id=release_run_id, reason=reason, support=support)
        scored_out.append(row)
        by_id[account_id] = row

    decision_out: list[dict[str, str]] = []
    for original in decisions:
        replacement = by_id.get(original["Id"])
        row = dict(replacement) if replacement else {**original, **{field: "" for field in GUARDRAIL_FIELDS}}
        decision_out.append(row)

    if len(scored_out) != 16_924 or len({row["Id"] for row in scored_out}) != 16_924:
        raise RuntimeError("Guardrail changed the approved scored population")
    if {row["Id"] for row in decision_out if row.get("decision") == "score"} != set(by_id):
        raise RuntimeError("Guardrail full-decision reconciliation failed")

    audit_fields = tuple(scored_out[0].keys())
    decision_fields = tuple(decision_out[0].keys())
    payload_rows = [{field: row[field] for field in CANARY_FIELDS} for row in scored_out]
    top20 = _top_rows(scored_out)
    top20_fields = (
        "rank",
        "Id",
        "Name",
        "lane",
        "score_source_tier",
        "score_source_detail",
        "confidence",
        "binding_status",
        "AI_Prospect_Value_MCV_Point__c",
        "AI_Prospect_Value_ARR_Point__c",
        "AI_Prospect_Value_ARR_Range__c",
        *GUARDRAIL_FIELDS,
    )
    top20_rows = [
        {
            field: str(index) if field == "rank" else _text(row.get(field))
            for field in top20_fields
        }
        for index, row in enumerate(top20, 1)
    ]

    files = {
        "scored_population_guardrailed.csv": (scored_out, audit_fields),
        "full_population_decisions_guardrailed.csv": (decision_out, decision_fields),
        "full_candidate_payload_no_write.csv": (payload_rows, CANARY_FIELDS),
        "top_20_after_guardrails.csv": (top20_rows, top20_fields),
    }
    for name, (rows, fields) in files.items():
        write_csv(output_dir / name, rows, fields)

    summary = {
        "run_id": release_run_id,
        "model_version": MODEL_VERSION,
        "no_write": True,
        "salesforce_writes": 0,
        "approved_scored_population": len(scored_out),
        "scored_population": len(scored_out),
        "coverage_gate": "PASS",
        "population_preserved": True,
        "guardrail_candidates": 56,
        "guardrail_demoted": sum(reason_counts.values()),
        "guardrail_preserved_with_support": preserved_candidate_count,
        "guardrail_reason_counts": dict(reason_counts),
        "support_code_counts": dict(support_counts),
        "legal_conservative_ceiling": LEGAL_CONSERVATIVE_CEILING,
        "title_conservative_ceiling": TITLE_CONSERVATIVE_CEILING,
        "top_20_available": True,
        "publication_allowed": False,
        "publication_hold_reason": "Revised top-20 and canary require review; no Salesforce write authorized",
        "source_hashes": {
            "scored_audit": sha256_file(scored_audit_path),
            "full_decisions": sha256_file(full_decisions_path),
            "lane": sha256_file(lane_path),
            "evidence": sha256_file(evidence_path),
            "accounts": sha256_file(accounts_path),
            "opportunity_labels": sha256_file(opportunity_labels_path),
        },
    }
    summary_path = output_dir / "guardrail_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    artifact_paths = [output_dir / name for name in files] + [summary_path]
    manifest = {
        "run_id": release_run_id,
        "immutable": True,
        "no_write": True,
        "artifacts": [
            {"path": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
            for path in artifact_paths
        ],
    }
    (output_dir / "no_write_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    return {**summary, "output_dir": str(output_dir)}
