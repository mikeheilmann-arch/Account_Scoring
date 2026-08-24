#!/usr/bin/env python3
"""Re-score a greenfield Nimble run from cached artifacts only.

This intentionally performs no Nimble map/extract calls. It rebuilds the public
evidence from cached page_*.json files, re-runs deterministic signals and scoring,
and optionally writes a before/after delta against a prior output CSV.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
from pathlib import Path

from run_greenfield_nimble_test import (
    BAD_DOMAIN_PATTERNS,
    OUTPUT_COLUMNS,
    SERVICE_KEYWORDS,
    TOOL_KEYWORDS,
    choose_pages,
    clean,
    confidence,
    extract_offices,
    extract_staff,
    first_present,
    infer_website_quality,
    is_hygiene_control,
    is_icp_likely,
    is_non_icp,
    keyword_signals,
    markdown_from_extract,
    mcv_to_band,
    normalize_url,
    parse_int,
    read_csv,
    save_json,
    slug,
    source_anchor_fallback_allowed,
    source_anchor_fallback_review_reason,
    source_anchor_fallback_score,
    trusted_anchor_score,
    write_csv,
    customer_mcv_anchor,
    estimate_mcv,
)
from legal_entity_scoring import classify_legal_entity


DELTA_COLUMNS = [
    "Id",
    "Name",
    "Website",
    "OldReviewAction",
    "NewReviewAction",
    "OldEstimatedMCV",
    "NewEstimatedMCV",
    "MCVDelta",
    "OldEstimatedARR",
    "NewEstimatedARR",
    "OldScore",
    "NewScore",
    "OldConfidence",
    "NewConfidence",
    "OldLegalEntityRoute",
    "NewLegalEntityRoute",
    "Changed",
    "Evidence",
]


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def page_sort_key(path: Path) -> tuple[int, str]:
    match = re.search(r"page_(\d+)_", path.name)
    return (int(match.group(1)) if match else 9999, path.name)


def cached_pages(site_dir: Path) -> tuple[list[str], list[str], list[str]]:
    markdowns: list[str] = []
    extracted_urls: list[str] = []
    errors: list[str] = []
    for page_path in sorted(site_dir.glob("page_*.json"), key=page_sort_key):
        payload = load_json(page_path)
        markdown = markdown_from_extract(payload)
        if markdown:
            markdowns.append(markdown)
            extracted_urls.append(clean(payload.get("url")) or page_path.stem)
    for error_path in sorted(site_dir.glob("page_*_error.txt"), key=page_sort_key):
        text = error_path.read_text(encoding="utf-8", errors="replace")
        errors.append(text[:160])
    return markdowns, extracted_urls, errors


def output_base_for(row: dict[str, str], site_dir: Path) -> dict[str, object]:
    account_id = clean(row.get("Id"))
    account_name = clean(row.get("Name")) or account_id
    return {
        "SourceSet": clean(row.get("SourceSet")),
        "TestLane": clean(row.get("TestLane")),
        "Bucket": clean(row.get("Bucket")),
        "Id": account_id,
        "SalesforceUrl": clean(row.get("SalesforceUrl")),
        "Name": account_name,
        "Website": clean(row.get("Website")),
        "BillingState": clean(row.get("BillingState")),
        "Segment": clean(row.get("Segment")),
        "Owner": clean(row.get("Owner")),
        "InputFinalMCV": clean(row.get("FinalMCV")),
        "InputMCVSource": clean(row.get("MCVSource")),
        "InputHasAnyOpp": clean(row.get("HasAnyOpp")),
        "InputLegacyTier": clean(row.get("LegacyTier")),
        "RawArtifactDir": str(site_dir),
    }


def rescore_row(row: dict[str, str], raw_dir: Path) -> dict[str, object]:
    account_id = clean(row.get("Id"))
    account_name = clean(row.get("Name")) or account_id
    site_dir = raw_dir / f"{slug(account_name)}_{account_id[-6:]}"
    output_base = output_base_for(row, site_dir)
    base_url = normalize_url(clean(row.get("Website")))
    website_hygiene = clean(row.get("WebsiteHygiene"))
    customer_anchor_mcv, _customer_anchor_source = customer_mcv_anchor(row)

    if not base_url or (website_hygiene and website_hygiene != "confirmed"):
        reason = "No website available" if not base_url else f"Website hygiene status is {website_hygiene}"
        if customer_anchor_mcv:
            mcv, low, high, arr, arr_range, score, mcv_note = trusted_anchor_score(row)
            return {
                **output_base,
                "RetrievalStatus": "not_run",
                "ICPDisposition": "trusted_mcv_anchor",
                "ReviewAction": "score_now",
                "EstimatedMCV": mcv,
                "EstimatedMCVLow": low,
                "EstimatedMCVHigh": high,
                "EstimatedARR": arr,
                "ARRRange": arr_range,
                "Score": score,
                "Confidence": "High",
                "LegalEntityRoute": "not_evaluated",
                "LegalMarketFit": "",
                "LegalEvidence": "trusted MCV anchor scored before website classification",
                "Evidence": f"{reason}; {mcv_note}",
            }
        return {**output_base, "RetrievalStatus": "not_run", "ICPDisposition": "hygiene_needed", "ReviewAction": "hygiene_review", "Evidence": reason}

    map_payload = load_json(site_dir / "map.json")
    if not map_payload:
        if customer_anchor_mcv:
            mcv, low, high, arr, arr_range, score, mcv_note = trusted_anchor_score(row)
            return {
                **output_base,
                "RetrievalStatus": "map_failed",
                "ICPDisposition": "trusted_mcv_anchor",
                "ReviewAction": "score_now",
                "EstimatedMCV": mcv,
                "EstimatedMCVLow": low,
                "EstimatedMCVHigh": high,
                "EstimatedARR": arr,
                "ARRRange": arr_range,
                "Score": score,
                "Confidence": "High",
                "LegalEntityRoute": "not_evaluated",
                "LegalMarketFit": "",
                "LegalEvidence": "trusted MCV anchor scored before legal classification",
                "Evidence": f"Map cache missing/invalid; {mcv_note}",
            }
        legal_profile = classify_legal_entity(row, "", [], 0, 0, 0)
        if source_anchor_fallback_allowed(row, "", legal_profile):
            mcv, low, high, arr, arr_range, score, mcv_note = source_anchor_fallback_score(row)
            return {
                **output_base,
                "RetrievalStatus": "map_failed",
                "ICPDisposition": legal_profile.route if legal_profile.is_law_firm else "scorable_anchor_fallback",
                "ReviewAction": "score_now",
                "EstimatedMCV": mcv,
                "EstimatedMCVLow": low,
                "EstimatedMCVHigh": high,
                "EstimatedARR": arr,
                "ARRRange": arr_range,
                "Score": score,
                "Confidence": "Medium",
                "LegalEntityRoute": legal_profile.route,
                "LegalMarketFit": legal_profile.market_fit,
                "LegalEvidence": legal_profile.evidence or "non-law title/settlement operator fallback",
                "Evidence": f"Map cache missing/invalid; {mcv_note}; value requires backtest/rep validation",
            }
        fallback_review_reason = source_anchor_fallback_review_reason(row, "", legal_profile)
        if fallback_review_reason:
            return {
                **output_base,
                "RetrievalStatus": "map_failed",
                "ICPDisposition": "anchor_plausibility_review",
                "ReviewAction": "manual_review",
                "Confidence": "Medium",
                "LegalEntityRoute": legal_profile.route,
                "LegalMarketFit": legal_profile.market_fit,
                "LegalEvidence": legal_profile.evidence or "non-law title/settlement operator plausibility review",
                "Evidence": f"Map cache missing/invalid; {fallback_review_reason}",
            }
        return {
            **output_base,
            "RetrievalStatus": "map_failed",
            "ICPDisposition": "indeterminate",
            "ReviewAction": "insufficient_public_evidence",
            "Confidence": "Low",
            "LegalEntityRoute": "not_evaluated",
            "LegalMarketFit": "",
            "LegalEvidence": "website map failed before legal classification",
            "Evidence": "Map cache missing/invalid",
        }

    selected_pages = choose_pages(base_url, map_payload, 4)
    markdowns, extracted_urls, errors = cached_pages(site_dir)
    combined_text = "\n\n".join(markdowns)
    office_count, office_evidence = extract_offices(combined_text)
    staff_count, staff_evidence = extract_staff(combined_text)
    services = keyword_signals(combined_text, SERVICE_KEYWORDS)
    tools = keyword_signals(combined_text, TOOL_KEYWORDS)
    mapped_count = len(map_payload.get("links") or [])
    quality = infer_website_quality(combined_text, mapped_count, extracted_urls, services, tools, office_count, staff_count)
    legal_profile = classify_legal_entity(row, combined_text, services, office_count, staff_count, quality)

    if customer_anchor_mcv:
        icp = legal_profile.route if legal_profile.is_law_firm else "scorable"
        action = "score_now"
        mcv, low, high, arr, arr_range, score, mcv_note = estimate_mcv(
            office_count, staff_count, quality, services, tools, row, combined_text, legal_profile
        )
        if mcv_note:
            mcv_note = f"Trusted anchor made row score-eligible; {mcv_note}"
    elif is_hygiene_control(row, combined_text):
        icp = "hygiene_needed"
        action = "hygiene_review"
        mcv = low = high = arr = None
        arr_range = ""
        score = ""
        mcv_note = ""
    elif legal_profile.is_law_firm and legal_profile.score_now_eligible:
        icp = legal_profile.route
        action = "score_now"
        mcv, low, high, arr, arr_range, score, mcv_note = estimate_mcv(
            office_count, staff_count, quality, services, tools, row, combined_text, legal_profile
        )
    elif (not markdowns or (errors and not services)) and source_anchor_fallback_allowed(row, combined_text, legal_profile):
        icp = legal_profile.route if legal_profile.is_law_firm else "scorable_anchor_fallback"
        action = "score_now"
        mcv, low, high, arr, arr_range, score, mcv_note = source_anchor_fallback_score(row)
    elif (not markdowns or (errors and not services)) and (
        fallback_review_reason := source_anchor_fallback_review_reason(row, combined_text, legal_profile)
    ):
        icp = "anchor_plausibility_review"
        action = "manual_review"
        mcv = low = high = arr = None
        arr_range = ""
        score = ""
        mcv_note = fallback_review_reason
    elif not markdowns:
        icp = "indeterminate"
        action = "insufficient_public_evidence"
        mcv = low = high = arr = None
        arr_range = ""
        score = ""
        mcv_note = ""
    elif legal_profile.non_icp:
        icp = legal_profile.route
        action = "non_icp_confirmed"
        mcv = low = high = arr = None
        arr_range = ""
        score = ""
        mcv_note = ""
    elif legal_profile.review_needed:
        icp = legal_profile.route
        action = "manual_review"
        mcv = low = high = arr = None
        arr_range = ""
        score = ""
        mcv_note = ""
    elif is_non_icp(row, combined_text):
        icp = "non_icp"
        action = "non_icp_confirmed"
        mcv = low = high = arr = None
        arr_range = ""
        score = ""
        mcv_note = ""
    elif not is_icp_likely(row, combined_text, services):
        icp = "indeterminate"
        action = "manual_review"
        mcv = low = high = arr = None
        arr_range = ""
        score = ""
        mcv_note = ""
    else:
        icp = legal_profile.route if legal_profile.is_law_firm else "scorable"
        action = "score_now"
        mcv, low, high, arr, arr_range, score, mcv_note = estimate_mcv(
            office_count, staff_count, quality, services, tools, row, combined_text, legal_profile
        )

    conf = confidence(action, office_count, staff_count, quality, len(markdowns), legal_profile)
    if customer_anchor_mcv and action == "score_now":
        conf = "High"
    elif icp == "scorable_anchor_fallback" and action == "score_now":
        conf = "Medium"
    elif icp == "anchor_plausibility_review":
        conf = "Medium"
    status = "ok" if markdowns else "extract_failed"
    if errors and markdowns:
        status = "partial"

    evidence_parts = []
    if office_count:
        evidence_parts.append(f"{office_count} visible office/address signals")
    if staff_count:
        evidence_parts.append(f"{staff_count} visible staff/team name signals")
    if services:
        evidence_parts.append(f"services: {', '.join(services[:6])}")
    if tools:
        evidence_parts.append(f"tools/process: {', '.join(tools[:5])}")
    if quality:
        evidence_parts.append(f"website quality score {quality}/5")
    if legal_profile.evidence:
        evidence_parts.append(f"legal lane: {legal_profile.evidence}")
    if mcv_note:
        evidence_parts.append(mcv_note)
    if not evidence_parts:
        evidence_parts.append("limited usable public evidence")
    if errors:
        evidence_parts.append(f"retrieval warnings on {len(errors)} selected page(s)")

    return {
        **output_base,
        "RetrievalStatus": status,
        "MappedLinkCount": mapped_count,
        "ExtractedPageCount": len(markdowns),
        "SelectedPages": " | ".join(extracted_urls or selected_pages),
        "ICPDisposition": icp,
        "ReviewAction": action,
        "EstimatedMCV": "" if mcv is None else mcv,
        "EstimatedMCVLow": "" if low is None else low,
        "EstimatedMCVHigh": "" if high is None else high,
        "EstimatedARR": "" if arr is None else arr,
        "ARRRange": arr_range,
        "Score": score,
        "Confidence": conf,
        "OfficeCount": office_count,
        "OfficeEvidence": " | ".join(office_evidence),
        "StaffCountVisible": staff_count,
        "StaffEvidence": " | ".join(staff_evidence),
        "WebsiteQualityScore": quality,
        "LegalEntityRoute": legal_profile.route,
        "LegalMarketFit": legal_profile.market_fit,
        "LegalEvidence": legal_profile.evidence,
        "ServiceSignals": ", ".join(services),
        "ToolSignals": ", ".join(tools),
        "MarketSignals": "; ".join(
            signal
            for signal in (
                "national/multistate language present"
                if re.search(r"national|nationwide|all 50 states|multi-state|multistate", combined_text, flags=re.I)
                else "",
                f"legal_market_fit={legal_profile.market_fit}" if legal_profile.is_law_firm else "",
            )
            if signal
        ),
        "Evidence": "; ".join(evidence_parts),
    }


def count_by(rows: list[dict[str, object]], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        value = clean(row.get(key)) or "(blank)"
        counts[value] = counts.get(value, 0) + 1
    return counts


def numeric(value: object) -> int | None:
    return parse_int(value)


def build_delta(previous_rows: list[dict[str, str]], new_rows: list[dict[str, object]]) -> list[dict[str, object]]:
    previous_by_id = {clean(row.get("Id")): row for row in previous_rows}
    deltas: list[dict[str, object]] = []
    for row in new_rows:
        previous = previous_by_id.get(clean(row.get("Id")), {})
        old_mcv = numeric(previous.get("EstimatedMCV"))
        new_mcv = numeric(row.get("EstimatedMCV"))
        changed = any(
            clean(previous.get(old_key)) != clean(row.get(new_key))
            for old_key, new_key in (
                ("ReviewAction", "ReviewAction"),
                ("EstimatedMCV", "EstimatedMCV"),
                ("EstimatedARR", "EstimatedARR"),
                ("Score", "Score"),
                ("Confidence", "Confidence"),
                ("LegalEntityRoute", "LegalEntityRoute"),
            )
        )
        deltas.append(
            {
                "Id": clean(row.get("Id")),
                "Name": clean(row.get("Name")),
                "Website": clean(row.get("Website")),
                "OldReviewAction": clean(previous.get("ReviewAction")),
                "NewReviewAction": clean(row.get("ReviewAction")),
                "OldEstimatedMCV": "" if old_mcv is None else old_mcv,
                "NewEstimatedMCV": "" if new_mcv is None else new_mcv,
                "MCVDelta": "" if old_mcv is None or new_mcv is None else new_mcv - old_mcv,
                "OldEstimatedARR": clean(previous.get("EstimatedARR")),
                "NewEstimatedARR": clean(row.get("EstimatedARR")),
                "OldScore": clean(previous.get("Score")),
                "NewScore": clean(row.get("Score")),
                "OldConfidence": clean(previous.get("Confidence")),
                "NewConfidence": clean(row.get("Confidence")),
                "OldLegalEntityRoute": clean(previous.get("LegalEntityRoute")),
                "NewLegalEntityRoute": clean(row.get("LegalEntityRoute")),
                "Changed": "Yes" if changed else "No",
                "Evidence": clean(row.get("Evidence")),
            }
        )
    return deltas


def write_delta(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8", errors="replace") as handle:
        writer = csv.DictWriter(handle, fieldnames=DELTA_COLUMNS, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline re-score from cached Nimble artifacts.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--raw-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--previous", type=Path)
    parser.add_argument("--delta-output", type=Path)
    args = parser.parse_args()

    started = time.time()
    input_rows = read_csv(args.input)
    outputs = [rescore_row(row, args.raw_dir) for row in input_rows]
    write_csv(args.output, outputs)

    summary: dict[str, object] = {
        "input": str(args.input),
        "output": str(args.output),
        "raw_dir": str(args.raw_dir),
        "rows": len(outputs),
        "elapsed_seconds": round(time.time() - started, 2),
        "retrieval_status_counts": count_by(outputs, "RetrievalStatus"),
        "review_action_counts": count_by(outputs, "ReviewAction"),
        "confidence_counts": count_by(outputs, "Confidence"),
    }
    if args.previous and args.delta_output:
        previous_rows = read_csv(args.previous)
        delta_rows = build_delta(previous_rows, outputs)
        write_delta(args.delta_output, delta_rows)
        changed_rows = [row for row in delta_rows if row["Changed"] == "Yes"]
        summary["delta_output"] = str(args.delta_output)
        summary["changed_rows"] = len(changed_rows)
        summary["changed_score_now_mcv_rows"] = sum(
            1
            for row in changed_rows
            if row["OldReviewAction"] == "score_now"
            and row["NewReviewAction"] == "score_now"
            and row["OldEstimatedMCV"] != row["NewEstimatedMCV"]
        )
        summary["action_changed_rows"] = sum(
            1 for row in changed_rows if row["OldReviewAction"] != row["NewReviewAction"]
        )
    summary_path = args.output.with_name(args.output.stem + "_summary.json")
    save_json(summary_path, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
