#!/usr/bin/env python3
"""Build a no-write impact view for the Virginia resolver overlay.

This joins the Virginia outreach scoring results, the prior Salesforce writeback
export, and the vertical resolver review package. It produces analysis artifacts
only; it does not write to Salesforce.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any


REVIEW_ACTIONS = {
    "",
    "hygiene_review",
    "insufficient_public_evidence",
    "manual_review",
    "non_icp_confirmed",
    "search_fallback_needed",
    "url_enrichment_needed",
}

RESCORE_ACTIONS = {
    "resolver_overlay_rescore_candidate",
    "attorney_settlement_rescore_candidate",
    "law_firm_rescore_candidate_pending_vsb",
}


OUTPUT_FIELDS = [
    "Id",
    "Name",
    "SalesforceUrl",
    "OriginalWebsite",
    "OriginalCanonicalURL",
    "OriginalAction",
    "OriginalICP",
    "OriginalURLStatus",
    "OriginalRetrievalStatus",
    "OriginalEstimatedMCV",
    "OriginalEstimatedARR",
    "OriginalConfidence",
    "ResolverBucket",
    "ResolverOutcome",
    "CurrentWebsite",
    "CandidateWebsite",
    "CandidateWebsiteConfidence",
    "CandidateEntityMatch",
    "SCCRegistryStatus",
    "SCCMatchedName",
    "SCCMatchedCity",
    "SCCMatchedState",
    "SCCMatchedZip",
    "ManualVSBCheckRequired",
    "ManualVSBRegisteredSettlementAgent",
    "GroundTruthCorrectWebsite",
    "GroundTruthICPClass",
    "ProvisionalRecoveryCredit",
    "HumanRatified",
    "OverlayICPStatus",
    "OverlayWebsiteStatus",
    "ProposedOverlayAction",
    "OverlayWouldChangeReport",
    "OverlayReason",
    "OverlayEvidence",
    "GroundTruthNotes",
]

RESCORE_QUEUE_FIELDS = [
    "RescorePriority",
    "Id",
    "Name",
    "SalesforceUrl",
    "Website",
    "RescoreInputSource",
    "OriginalAction",
    "ProposedOverlayAction",
    "SCCRegistryStatus",
    "SCCMatchedName",
    "SCCMatchedCity",
    "ManualVSBCheckRequired",
    "ManualVSBRegisteredSettlementAgent",
    "OverlayReason",
    "OverlayEvidence",
]

SCORING_INPUT_FIELDS = [
    "SourceSet",
    "TestLane",
    "Bucket",
    "Id",
    "SalesforceUrl",
    "Name",
    "Website",
    "BillingState",
    "Segment",
    "Owner",
    "FinalMCV",
    "MCVSource",
    "HasAnyOpp",
    "LegacyTier",
    "WebsiteHygiene",
    "ResolverOverlayAction",
    "ResolverOverlayPriority",
    "ResolverOverlayEvidence",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def by_id(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row.get("Id", "").strip(): row for row in rows if row.get("Id", "").strip()}


def clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def first(*values: Any) -> str:
    for value in values:
        text = clean(value)
        if text:
            return text
    return ""


def overlay_icp_status(row: dict[str, str]) -> str:
    icp = clean(row.get("GroundTruthICPClass"))
    scc = clean(row.get("SCCRegistryStatus"))
    vsb_required = clean(row.get("ManualVSBCheckRequired"))
    vsb = clean(row.get("ManualVSBRegisteredSettlementAgent"))

    if icp == "ICP - Title/Settlement Agency":
        if scc == "matched_active_agency":
            return "registry_confirmed_title_settlement_icp"
        return "title_settlement_icp_provisional"
    if icp == "ICP - Attorney Settlement Agent":
        if vsb == "Yes":
            return "attorney_settlement_icp_vsb_confirmed"
        if vsb_required == "Yes" and vsb in {"", "Pending"}:
            return "attorney_settlement_candidate_vsb_pending"
        return "attorney_settlement_icp_provisional"
    if icp == "Non-ICP":
        return "non_icp_candidate"
    if vsb_required == "Yes" and vsb in {"", "Pending"}:
        return "law_firm_vsb_pending"
    return "unknown"


def overlay_website_status(row: dict[str, str]) -> str:
    label = clean(row.get("GroundTruthCorrectWebsite"))
    candidate = clean(row.get("CandidateWebsite"))
    if label == "Yes":
        return "candidate_website_confirmed"
    if label == "No":
        return "candidate_website_rejected"
    if label == "Pending":
        return "candidate_website_pending"
    if candidate:
        return "candidate_website_unlabeled"
    return "no_candidate_website"


def proposed_action(row: dict[str, str], original_action: str) -> str:
    website = overlay_website_status(row)
    icp = clean(row.get("GroundTruthICPClass"))
    scc = clean(row.get("SCCRegistryStatus"))
    vsb_required = clean(row.get("ManualVSBCheckRequired"))
    vsb = clean(row.get("ManualVSBRegisteredSettlementAgent"))

    if website == "candidate_website_rejected":
        return "wrong_website_confirmed"
    if icp == "ICP - Title/Settlement Agency" and website == "candidate_website_confirmed":
        return "resolver_overlay_rescore_candidate"
    if icp == "ICP - Title/Settlement Agency" and scc == "matched_active_agency":
        return "registry_confirmed_website_needed"
    if icp == "ICP - Attorney Settlement Agent" and website == "candidate_website_confirmed":
        if vsb_required == "Yes" and vsb in {"", "Pending"}:
            return "law_firm_rescore_candidate_pending_vsb"
        return "attorney_settlement_rescore_candidate"
    if vsb_required == "Yes" and vsb in {"", "Pending"}:
        return "law_firm_vsb_review_needed"
    if icp == "Non-ICP":
        return "non_icp_confirmed_by_overlay"
    if website == "candidate_website_confirmed":
        return "candidate_website_review_needed"
    return "no_overlay_change"


def would_change_report(original_action: str, action: str) -> str:
    if action == "no_overlay_change":
        return "No"
    if original_action in REVIEW_ACTIONS:
        return "Yes"
    if action in {"wrong_website_confirmed", "non_icp_confirmed_by_overlay"}:
        return "Yes"
    if action.endswith("rescore_candidate") or action == "resolver_overlay_rescore_candidate":
        return "Review"
    if action == "law_firm_rescore_candidate_pending_vsb":
        return "Review"
    return "No"


def overlay_reason(row: dict[str, str], action: str) -> str:
    name = clean(row.get("Name"))
    candidate = clean(row.get("CandidateWebsite"))
    scc_status = clean(row.get("SCCRegistryStatus"))
    scc_name = clean(row.get("SCCMatchedName"))
    scc_city = clean(row.get("SCCMatchedCity"))
    icp = clean(row.get("GroundTruthICPClass"))

    if action == "resolver_overlay_rescore_candidate":
        if scc_status == "matched_active_agency":
            return f"Resolver found a candidate website and SCC confirmed an active agency match for {scc_name or name}."
        return "Resolver found a candidate website and the provisional class is title/settlement ICP."
    if action == "registry_confirmed_website_needed":
        place = f" in {scc_city}" if scc_city else ""
        return f"SCC confirmed an active title/settlement agency{place}, but the resolver did not confirm a usable website."
    if action == "law_firm_rescore_candidate_pending_vsb":
        return "Resolver found the law-firm website, but the attorney-settlement lane still needs VSB confirmation before production use."
    if action == "attorney_settlement_rescore_candidate":
        return "Resolver found a candidate website and the attorney-settlement classification is confirmed or accepted."
    if action == "law_firm_vsb_review_needed":
        return "Law-firm account requires VSB attorney-settlement confirmation; SCC absence is not disqualifying for this lane."
    if action == "non_icp_confirmed_by_overlay":
        return f"Resolver evidence classified the account as {icp or 'non-ICP'}."
    if action == "wrong_website_confirmed":
        return f"Resolver flagged the candidate website as the wrong entity or not safe to promote: {candidate or 'no candidate'}."
    if action == "candidate_website_review_needed":
        return "Resolver found a candidate website, but the ICP decision is still pending."
    return "No material overlay change identified."


def overlay_evidence(row: dict[str, str]) -> str:
    parts = []
    candidate = clean(row.get("CandidateWebsite"))
    if candidate:
        parts.append(f"candidate={candidate}")
    confidence = clean(row.get("CandidateWebsiteConfidence"))
    if confidence:
        parts.append(f"candidate_confidence={confidence}")
    entity = clean(row.get("CandidateEntityMatch"))
    if entity:
        parts.append(f"entity_match={entity}")
    scc = clean(row.get("SCCRegistryStatus"))
    if scc:
        scc_detail = clean(row.get("SCCMatchedName"))
        city = clean(row.get("SCCMatchedCity"))
        if scc_detail and city:
            parts.append(f"scc={scc}: {scc_detail}, {city}")
        elif scc_detail:
            parts.append(f"scc={scc}: {scc_detail}")
        else:
            parts.append(f"scc={scc}")
    recovery = clean(row.get("ProvisionalRecoveryCredit"))
    if recovery:
        parts.append(f"recovery={recovery}")
    return " | ".join(parts)


def build_rows(
    virginia_rows: list[dict[str, str]],
    writeback_rows: list[dict[str, str]],
    resolver_rows: list[dict[str, str]],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    virginia = by_id(virginia_rows)
    writeback = by_id(writeback_rows)
    output = []
    missing_from_virginia = []
    missing_from_writeback = []

    for resolver in resolver_rows:
        account_id = clean(resolver.get("Id"))
        base = virginia.get(account_id, {})
        prior = writeback.get(account_id, {})
        if not base:
            missing_from_virginia.append(account_id)
        if not prior:
            missing_from_writeback.append(account_id)

        original_action = first(
            prior.get("AI_Prospect_Value_Action__c"),
            base.get("ReviewAction"),
        )
        action = proposed_action(resolver, original_action)
        row = {
            "SourceSet": clean(base.get("SourceSet")),
            "TestLane": clean(base.get("TestLane")),
            "Bucket": clean(base.get("Bucket")),
            "BillingState": clean(base.get("BillingState")),
            "Segment": clean(base.get("Segment")),
            "Owner": clean(base.get("Owner")),
            "FinalMCV": clean(base.get("InputFinalMCV")),
            "MCVSource": clean(base.get("InputMCVSource")),
            "HasAnyOpp": clean(base.get("InputHasAnyOpp")),
            "LegacyTier": clean(base.get("InputLegacyTier")),
            "Id": account_id,
            "Name": first(base.get("Name"), resolver.get("Name")),
            "SalesforceUrl": first(base.get("SalesforceUrl"), resolver.get("SalesforceUrl")),
            "OriginalWebsite": first(base.get("Website"), resolver.get("CurrentWebsite")),
            "OriginalCanonicalURL": clean(prior.get("AI_Prospect_Value_Canonical_URL__c")),
            "OriginalAction": original_action,
            "OriginalICP": first(prior.get("AI_Prospect_Value_ICP__c"), base.get("ICPDisposition")),
            "OriginalURLStatus": clean(prior.get("AI_Prospect_Value_URL_Status__c")),
            "OriginalRetrievalStatus": clean(base.get("RetrievalStatus")),
            "OriginalEstimatedMCV": first(
                prior.get("AI_Prospect_Value_MCV_Point__c"),
                base.get("EstimatedMCV"),
            ),
            "OriginalEstimatedARR": first(
                prior.get("AI_Prospect_Value_ARR_Point__c"),
                base.get("EstimatedARR"),
            ),
            "OriginalConfidence": first(
                prior.get("AI_Prospect_Value_Confidence__c"),
                base.get("Confidence"),
            ),
            "ResolverBucket": clean(resolver.get("Bucket")),
            "ResolverOutcome": clean(resolver.get("ResolverOutcome")),
            "CurrentWebsite": clean(resolver.get("CurrentWebsite")),
            "CandidateWebsite": clean(resolver.get("CandidateWebsite")),
            "CandidateWebsiteConfidence": clean(resolver.get("CandidateWebsiteConfidence")),
            "CandidateEntityMatch": clean(resolver.get("CandidateEntityMatch")),
            "SCCRegistryStatus": clean(resolver.get("SCCRegistryStatus")),
            "SCCMatchedName": clean(resolver.get("SCCMatchedName")),
            "SCCMatchedCity": clean(resolver.get("SCCMatchedCity")),
            "SCCMatchedState": clean(resolver.get("SCCMatchedState")),
            "SCCMatchedZip": clean(resolver.get("SCCMatchedZip")),
            "ManualVSBCheckRequired": clean(resolver.get("ManualVSBCheckRequired")),
            "ManualVSBRegisteredSettlementAgent": clean(
                resolver.get("ManualVSBRegisteredSettlementAgent")
            ),
            "GroundTruthCorrectWebsite": clean(resolver.get("GroundTruthCorrectWebsite")),
            "GroundTruthICPClass": clean(resolver.get("GroundTruthICPClass")),
            "ProvisionalRecoveryCredit": clean(resolver.get("ProvisionalRecoveryCredit")),
            "HumanRatified": clean(resolver.get("HumanRatified")),
            "OverlayICPStatus": overlay_icp_status(resolver),
            "OverlayWebsiteStatus": overlay_website_status(resolver),
            "ProposedOverlayAction": action,
            "OverlayWouldChangeReport": would_change_report(original_action, action),
            "OverlayReason": overlay_reason(resolver, action),
            "OverlayEvidence": overlay_evidence(resolver),
            "GroundTruthNotes": clean(resolver.get("GroundTruthNotes")),
        }
        output.append(row)

    summary = {
        "generated_at": date.today().isoformat(),
        "source_virginia_rows": len(virginia_rows),
        "source_writeback_rows": len(writeback_rows),
        "source_resolver_rows": len(resolver_rows),
        "overlay_rows": len(output),
        "missing_from_virginia": missing_from_virginia,
        "missing_from_writeback": missing_from_writeback,
        "original_action_counts": dict(Counter(row["OriginalAction"] for row in output)),
        "proposed_overlay_action_counts": dict(
            Counter(row["ProposedOverlayAction"] for row in output)
        ),
        "overlay_would_change_counts": dict(
            Counter(row["OverlayWouldChangeReport"] for row in output)
        ),
        "ground_truth_website_counts": dict(
            Counter(row["GroundTruthCorrectWebsite"] for row in output)
        ),
        "ground_truth_icp_counts": dict(Counter(row["GroundTruthICPClass"] for row in output)),
        "scc_registry_counts": dict(Counter(row["SCCRegistryStatus"] for row in output)),
        "manual_vsb_counts": dict(
            Counter(row["ManualVSBRegisteredSettlementAgent"] for row in output)
        ),
        "rescore_candidate_count": sum(
            1 for row in output if row["ProposedOverlayAction"] in RESCORE_ACTIONS
        ),
        "scc_backed_rescore_candidate_count": sum(
            1
            for row in output
            if row["ProposedOverlayAction"] in RESCORE_ACTIONS
            and row["SCCRegistryStatus"] == "matched_active_agency"
        ),
        "scc_silent_rescore_candidate_count": sum(
            1
            for row in output
            if row["ProposedOverlayAction"] in RESCORE_ACTIONS
            and row["SCCRegistryStatus"] != "matched_active_agency"
        ),
        "review_to_rescore_candidate_count": sum(
            1
            for row in output
            if row["OriginalAction"] in REVIEW_ACTIONS
            and row["ProposedOverlayAction"] in RESCORE_ACTIONS
        ),
        "registry_confirmed_website_needed_count": sum(
            1
            for row in output
            if row["ProposedOverlayAction"] == "registry_confirmed_website_needed"
        ),
        "pending_vsb_count": sum(
            1
            for row in output
            if row["ProposedOverlayAction"]
            in {"law_firm_vsb_review_needed", "law_firm_rescore_candidate_pending_vsb"}
        ),
        "wrong_website_confirmed_count": sum(
            1 for row in output if row["ProposedOverlayAction"] == "wrong_website_confirmed"
        ),
    }
    return output, summary


def write_rows(path: Path, rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def rescore_priority(row: dict[str, str]) -> str:
    if (
        row["ProposedOverlayAction"] == "resolver_overlay_rescore_candidate"
        and row["SCCRegistryStatus"] == "matched_active_agency"
    ):
        return "P1 - SCC-backed title/settlement rescore"
    if row["ProposedOverlayAction"] == "resolver_overlay_rescore_candidate":
        return "P2 - provisional title/settlement rescore"
    if row["ProposedOverlayAction"] == "attorney_settlement_rescore_candidate":
        return "P3 - attorney settlement rescore"
    if row["ProposedOverlayAction"] == "law_firm_rescore_candidate_pending_vsb":
        return "P4 - law-firm rescore pending VSB"
    return ""


def write_rescore_queue(path: Path, rows: list[dict[str, str]]) -> None:
    rescore_rows = [
        row for row in rows if row["ProposedOverlayAction"] in RESCORE_ACTIONS
    ]
    output = []
    for row in rescore_rows:
        output.append(
            {
                "RescorePriority": rescore_priority(row),
                "Id": row["Id"],
                "Name": row["Name"],
                "SalesforceUrl": row["SalesforceUrl"],
                "Website": first(row["CandidateWebsite"], row["OriginalCanonicalURL"], row["OriginalWebsite"]),
                "RescoreInputSource": "Virginia resolver overlay no-write queue",
                "OriginalAction": row["OriginalAction"],
                "ProposedOverlayAction": row["ProposedOverlayAction"],
                "SCCRegistryStatus": row["SCCRegistryStatus"],
                "SCCMatchedName": row["SCCMatchedName"],
                "SCCMatchedCity": row["SCCMatchedCity"],
                "ManualVSBCheckRequired": row["ManualVSBCheckRequired"],
                "ManualVSBRegisteredSettlementAgent": row["ManualVSBRegisteredSettlementAgent"],
                "OverlayReason": row["OverlayReason"],
                "OverlayEvidence": row["OverlayEvidence"],
            }
        )

    priority_order = {
        "P1 - SCC-backed title/settlement rescore": 1,
        "P2 - provisional title/settlement rescore": 2,
        "P3 - attorney settlement rescore": 3,
        "P4 - law-firm rescore pending VSB": 4,
    }
    output.sort(key=lambda row: (priority_order.get(row["RescorePriority"], 99), row["Name"]))

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESCORE_QUEUE_FIELDS)
        writer.writeheader()
        writer.writerows(output)


def write_scoring_input(path: Path, rows: list[dict[str, str]]) -> None:
    rescore_rows = [
        row for row in rows if row["ProposedOverlayAction"] in RESCORE_ACTIONS
    ]
    output = []
    for row in rescore_rows:
        website = first(row["CandidateWebsite"], row["OriginalCanonicalURL"], row["OriginalWebsite"])
        output.append(
            {
                "SourceSet": "Virginia resolver overlay",
                "TestLane": "No-write rescore",
                "Bucket": "Virginia resolver overlay rescore",
                "Id": row["Id"],
                "SalesforceUrl": row["SalesforceUrl"],
                "Name": row["Name"],
                "Website": website,
                "BillingState": row.get("BillingState", ""),
                "Segment": row.get("Segment", ""),
                "Owner": row.get("Owner", ""),
                "FinalMCV": row.get("FinalMCV", ""),
                "MCVSource": row.get("MCVSource", ""),
                "HasAnyOpp": row.get("HasAnyOpp", ""),
                "LegacyTier": row.get("LegacyTier", ""),
                "WebsiteHygiene": "confirmed" if website else "",
                "ResolverOverlayAction": row["ProposedOverlayAction"],
                "ResolverOverlayPriority": rescore_priority(row),
                "ResolverOverlayEvidence": row["OverlayEvidence"],
            }
        )

    priority_order = {
        "P1 - SCC-backed title/settlement rescore": 1,
        "P2 - provisional title/settlement rescore": 2,
        "P3 - attorney settlement rescore": 3,
        "P4 - law-firm rescore pending VSB": 4,
    }
    output.sort(key=lambda row: (priority_order.get(row["ResolverOverlayPriority"], 99), row["Name"]))

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SCORING_INPUT_FIELDS)
        writer.writeheader()
        writer.writerows(output)


def write_summary(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def md_count_list(counts: dict[str, int]) -> str:
    if not counts:
        return "- None\n"
    return "".join(f"- `{key or '(blank)'}`: {value}\n" for key, value in counts.items())


def top_rows(rows: list[dict[str, str]], action: str, limit: int = 12) -> list[dict[str, str]]:
    return [row for row in rows if row["ProposedOverlayAction"] == action][:limit]


def write_readout(path: Path, rows: list[dict[str, str]], summary: dict[str, Any]) -> None:
    action_counts = summary["proposed_overlay_action_counts"]
    change_counts = summary["overlay_would_change_counts"]
    rescore_rows = [row for row in rows if row["ProposedOverlayAction"] in RESCORE_ACTIONS]
    scc_backed_rescore_rows = [
        row for row in rescore_rows if row["SCCRegistryStatus"] == "matched_active_agency"
    ]
    scc_silent_rescore_rows = [
        row for row in rescore_rows if row["SCCRegistryStatus"] != "matched_active_agency"
    ]
    registry_rows = top_rows(rows, "registry_confirmed_website_needed", 12)
    vsb_rows = [
        row
        for row in rows
        if row["ProposedOverlayAction"]
        in {"law_firm_vsb_review_needed", "law_firm_rescore_candidate_pending_vsb"}
    ][:20]
    wrong_rows = top_rows(rows, "wrong_website_confirmed", 10)

    lines = [
        "# Virginia Resolver Overlay Impact",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Status: no-write analysis. This package does not update Salesforce. It shows how the Virginia outreach report would change if the resolver overlay were used as an input to the scoring pipeline.",
        "",
        "## Summary",
        "",
        f"- Virginia outreach rows reviewed: {summary['source_virginia_rows']}",
        f"- Resolver overlay rows joined: {summary['overlay_rows']}",
        f"- Rows missing from Virginia results: {len(summary['missing_from_virginia'])}",
        f"- Rows missing from prior writeback export: {len(summary['missing_from_writeback'])}",
        f"- Overlay rows that would materially change or require report review: {change_counts.get('Yes', 0)} yes, {change_counts.get('Review', 0)} review, {change_counts.get('No', 0)} no",
        f"- Rescore candidates surfaced by the overlay: {summary['rescore_candidate_count']}",
        f"- Rows moving from a review lane into rescore-candidate status: {summary['review_to_rescore_candidate_count']}",
        f"- SCC-backed rescore candidates: {summary['scc_backed_rescore_candidate_count']}",
        f"- SCC-silent/provisional rescore candidates: {summary['scc_silent_rescore_candidate_count']}",
        f"- Registry-confirmed agencies that still need a website: {summary['registry_confirmed_website_needed_count']}",
        f"- Pending VSB attorney-settlement checks: {summary['pending_vsb_count']}",
        f"- Wrong website/entity confirmations: {summary['wrong_website_confirmed_count']}",
        "",
        "## Proposed Overlay Actions",
        "",
        md_count_list(action_counts).rstrip(),
        "",
        "## Original Report Actions In The Overlay Set",
        "",
        md_count_list(summary["original_action_counts"]).rstrip(),
        "",
        "## SCC-Backed Rescore Candidates",
        "",
    ]

    if scc_backed_rescore_rows:
        for row in scc_backed_rescore_rows:
            lines.append(
                f"- {row['Name']} - {row['CandidateWebsite'] or row['OriginalWebsite']} "
                f"({row['OverlayEvidence']})"
            )
    else:
        lines.append("- None")

    lines.extend(["", "## Provisional Rescore Candidates", ""])
    if scc_silent_rescore_rows:
        for row in scc_silent_rescore_rows:
            lines.append(
                f"- {row['Name']} - {row['CandidateWebsite'] or row['OriginalWebsite']} "
                f"({row['OverlayEvidence']})"
            )
    else:
        lines.append("- None")

    lines.extend(["", "## Registry Confirmed, Website Still Needed", ""])
    if registry_rows:
        for row in registry_rows:
            lines.append(
                f"- {row['Name']} - SCC match: {row['SCCMatchedName']} "
                f"{row['SCCMatchedCity']} {row['SCCMatchedState']}".rstrip()
            )
    else:
        lines.append("- None")

    lines.extend(["", "## Pending Attorney Settlement Review", ""])
    if vsb_rows:
        for row in vsb_rows:
            lines.append(
                f"- {row['Name']} - {row['CandidateWebsite'] or row['CurrentWebsite']} "
                f"({row['GroundTruthICPClass'] or row['OverlayICPStatus']})"
            )
    else:
        lines.append("- None")

    lines.extend(["", "## Wrong Website Checks", ""])
    if wrong_rows:
        for row in wrong_rows:
            lines.append(
                f"- {row['Name']} - candidate rejected: {row['CandidateWebsite'] or row['CurrentWebsite']}"
            )
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- The overlay is most useful as a scoring input, not a blind website writeback. Confirmed title/settlement agency matches can be rescored with better website/entity evidence.",
            "- SCC confirmation is a strong positive signal for title/settlement agency ICP in Virginia. SCC non-match is not a negative signal for law firms because attorney settlement agents can sit in the VSB lane.",
            "- The remaining production decision is whether to accept the provisional labels or complete the lightweight manual ratification before using this as a production scoring input.",
        ]
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--virginia-results", type=Path, required=True)
    parser.add_argument("--writeback", type=Path, required=True)
    parser.add_argument("--resolver", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary-json", type=Path, required=True)
    parser.add_argument("--readout-md", type=Path, required=True)
    parser.add_argument("--rescore-candidates-output", type=Path)
    parser.add_argument("--scoring-input-output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    virginia_rows = read_csv(args.virginia_results)
    writeback_rows = read_csv(args.writeback)
    resolver_rows = read_csv(args.resolver)
    rows, summary = build_rows(virginia_rows, writeback_rows, resolver_rows)
    write_rows(args.output, rows)
    write_summary(args.summary_json, summary)
    write_readout(args.readout_md, rows, summary)
    if args.rescore_candidates_output:
        write_rescore_queue(args.rescore_candidates_output, rows)
    if args.scoring_input_output:
        write_scoring_input(args.scoring_input_output, rows)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
