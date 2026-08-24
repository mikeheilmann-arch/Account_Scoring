#!/usr/bin/env python3
"""Create a first-pass validation artifact without overwriting ground truth."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path


FIRST_PASS_COLUMNS = [
    "ProvisionalFirstPassWebsiteLabel",
    "ProvisionalFirstPassICPLabel",
    "ProvisionalFirstPassRecoveryLabel",
    "ProvisionalFirstPassConfidence",
    "ProvisionalFirstPassNotes",
    "HumanReviewStillRequired",
]

WEBSITE_REVIEW_OVERRIDES = {
    "Quinn, Thomas F., Law Office Of": (
        "Needs Human Review",
        "Candidate points to attorney profile at OFP Law; may be current attorney affiliation rather than the Account entity.",
    ),
}


def clean(value: object) -> str:
    return str(value or "").strip()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def scc_matched(row: dict[str, str]) -> bool:
    return clean(row.get("SCCRegistryStatus")) == "matched_active_agency"


def vsb_required(row: dict[str, str]) -> bool:
    return clean(row.get("ManualVSBCheckRequired")) == "Yes"


def candidate(row: dict[str, str]) -> str:
    return clean(row.get("CandidateWebsite"))


def strong_candidate(row: dict[str, str]) -> bool:
    return candidate(row) != "" and clean(row.get("CandidateEntityMatch")) == "strong"


def website_label(row: dict[str, str]) -> tuple[str, str]:
    name = clean(row.get("Name"))
    if not candidate(row):
        return "No Candidate", "No candidate website was promoted by the resolver."
    if name in WEBSITE_REVIEW_OVERRIDES:
        return WEBSITE_REVIEW_OVERRIDES[name]
    if strong_candidate(row) and scc_matched(row):
        return "Likely Correct", "Strong entity-matched candidate and active SCC agency match."
    if clean(row.get("ResolverOutcome")) == "high_confidence_recovery" and strong_candidate(row):
        return "Likely Correct", "High-confidence resolver candidate with strong entity match."
    if strong_candidate(row):
        return "Likely Correct", "Strong entity-matched official-looking candidate; ICP may still require separate review."
    if scc_matched(row):
        return "Needs Human Review", "SCC confirms ICP, but website candidate is not a strong entity match."
    return "Needs Human Review", "Candidate exists, but evidence is not enough for first-pass website label."


def icp_label(row: dict[str, str]) -> tuple[str, str]:
    if scc_matched(row):
        return "Confirmed ICP", f"SCC active agency match: {clean(row.get('SCCMatchType'))} / {clean(row.get('SCCMatchedName'))}."
    if vsb_required(row):
        return "Pending VSB Review", "Law-named SCC non-match; SCC absence is not disqualifying for attorney settlement agents."
    if clean(row.get("ResolverOutcome")) == "high_confidence_recovery":
        return "Likely ICP", "High-confidence resolver recovery, but SCC did not match."
    if clean(row.get("ResolverOutcome")) == "reviewer_assist":
        return "Likely ICP", "Reviewer-assist web/directory evidence; needs confirmation."
    return "Unknown", "No authoritative ICP confirmation in first-pass evidence."


def recovery_label(row: dict[str, str], site_label: str, icp: str) -> str:
    if site_label == "Likely Correct" and icp in {"Confirmed ICP", "Likely ICP"}:
        if scc_matched(row):
            return "Website Candidate - SCC Confirmed ICP"
        return "Website Candidate - SCC Silent"
    if scc_matched(row) and candidate(row):
        return "SCC Confirmed ICP - Website Needs Review"
    if scc_matched(row) and not candidate(row):
        return "SCC Confirmed ICP - Website Still Needed"
    if vsb_required(row):
        return "Pending VSB Lane"
    return "No First-Pass Recovery Credit"


def confidence(row: dict[str, str], site_label: str, icp: str) -> str:
    if site_label == "Likely Correct" and icp == "Confirmed ICP":
        return "High"
    if site_label == "Likely Correct" or icp == "Confirmed ICP":
        return "Medium"
    return "Low"


def human_review_required(site_label: str, icp: str, recovery: str) -> str:
    if site_label != "Likely Correct":
        return "Yes"
    if icp in {"Pending VSB Review", "Unknown"}:
        return "Yes"
    if recovery in {
        "SCC Confirmed ICP - Website Needs Review",
        "SCC Confirmed ICP - Website Still Needed",
    }:
        return "Yes"
    return "No"


def build_rows(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    output = []
    for row in rows:
        site_label, site_note = website_label(row)
        icp, icp_note = icp_label(row)
        recovery = recovery_label(row, site_label, icp)
        conf = confidence(row, site_label, icp)
        review = human_review_required(site_label, icp, recovery)
        output.append(
            {
                **{
                    "ProvisionalFirstPassWebsiteLabel": site_label,
                    "ProvisionalFirstPassICPLabel": icp,
                    "ProvisionalFirstPassRecoveryLabel": recovery,
                    "ProvisionalFirstPassConfidence": conf,
                    "ProvisionalFirstPassNotes": f"{site_note} {icp_note}",
                    "HumanReviewStillRequired": review,
                },
                **row,
            }
        )
    return output


def metrics(rows: list[dict[str, object]]) -> dict[str, object]:
    candidate_rows = [row for row in rows if clean(row.get("CandidateWebsite"))]
    likely_correct_candidates = [
        row for row in candidate_rows if clean(row.get("ProvisionalFirstPassWebsiteLabel")) == "Likely Correct"
    ]
    scc_confirmed_website_candidates = [
        row for row in rows if clean(row.get("ProvisionalFirstPassRecoveryLabel")) == "Website Candidate - SCC Confirmed ICP"
    ]
    scc_silent_website_candidates = [
        row for row in rows if clean(row.get("ProvisionalFirstPassRecoveryLabel")) == "Website Candidate - SCC Silent"
    ]
    scc_confirmed_website_needs_review = [
        row
        for row in rows
        if clean(row.get("ProvisionalFirstPassRecoveryLabel")) == "SCC Confirmed ICP - Website Needs Review"
    ]
    confirmed_icp_missing_website = [
        row
        for row in rows
        if clean(row.get("ProvisionalFirstPassRecoveryLabel")) == "SCC Confirmed ICP - Website Still Needed"
    ]
    by_bucket: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        by_bucket[clean(row.get("Bucket")) or "(blank)"][
            clean(row.get("ProvisionalFirstPassRecoveryLabel")) or "(blank)"
        ] += 1

    return {
        "rows": len(rows),
        "candidate_websites": len(candidate_rows),
        "candidate_websites_likely_correct_first_pass": len(likely_correct_candidates),
        "candidate_websites_needing_human_review": sum(
            1 for row in candidate_rows if clean(row.get("ProvisionalFirstPassWebsiteLabel")) == "Needs Human Review"
        ),
        "first_pass_candidate_website_note": "Counts only. No precision or likely-correct rate is computed until GroundTruthCorrectWebsite is populated.",
        "confirmed_icp_scc": sum(1 for row in rows if clean(row.get("ProvisionalFirstPassICPLabel")) == "Confirmed ICP"),
        "pending_vsb_review": sum(
            1 for row in rows if clean(row.get("ProvisionalFirstPassICPLabel")) == "Pending VSB Review"
        ),
        "human_review_still_required": sum(1 for row in rows if clean(row.get("HumanReviewStillRequired")) == "Yes"),
        "website_candidate_scc_confirmed_icp_count": len(scc_confirmed_website_candidates),
        "website_candidate_scc_silent_count": len(scc_silent_website_candidates),
        "scc_confirmed_website_needs_review_count": len(scc_confirmed_website_needs_review),
        "confirmed_icp_missing_website_count": len(confirmed_icp_missing_website),
        "website_label_counts": dict(Counter(clean(row.get("ProvisionalFirstPassWebsiteLabel")) for row in rows)),
        "icp_label_counts": dict(Counter(clean(row.get("ProvisionalFirstPassICPLabel")) for row in rows)),
        "recovery_label_counts": dict(Counter(clean(row.get("ProvisionalFirstPassRecoveryLabel")) for row in rows)),
        "recovery_by_bucket": {bucket: dict(counter) for bucket, counter in by_bucket.items()},
    }


def write_markdown(path: Path, metrics_data: dict[str, object]) -> None:
    lines = [
        "# Vertical Resolver First-Pass Validation",
        "",
        "Status: provisional validation, pending human labels.",
        "",
        f"- Candidate websites: {metrics_data['candidate_websites']}",
        f"- Candidate websites labeled likely correct in first pass: {metrics_data['candidate_websites_likely_correct_first_pass']}",
        f"- Candidate websites needing human review: {metrics_data['candidate_websites_needing_human_review']}",
        f"- SCC-confirmed ICP rows: {metrics_data['confirmed_icp_scc']}",
        f"- Pending VSB review rows: {metrics_data['pending_vsb_review']}",
        f"- Human review still required: {metrics_data['human_review_still_required']}",
        f"- Website candidates on SCC-confirmed ICP rows: {metrics_data['website_candidate_scc_confirmed_icp_count']}",
        f"- Website candidates on SCC-silent rows: {metrics_data['website_candidate_scc_silent_count']}",
        f"- SCC-confirmed ICP rows with candidate website still needing review: {metrics_data['scc_confirmed_website_needs_review_count']}",
        f"- SCC-confirmed ICP rows still missing website: {metrics_data['confirmed_icp_missing_website_count']}",
        "",
        "Important: no precision or likely-correct rate is computed here. Final go/no-go requires reviewer labels.",
        "",
        "## Recovery By Bucket",
        "",
    ]
    for bucket, values in metrics_data["recovery_by_bucket"].items():
        parts = ", ".join(f"{key}: {value}" for key, value in values.items())
        lines.append(f"- {bucket}: {parts}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build first-pass validation labels and metrics.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--metrics-json", required=True, type=Path)
    parser.add_argument("--metrics-md", required=True, type=Path)
    args = parser.parse_args()

    rows = read_csv(args.input)
    output = build_rows(rows)
    input_columns = list(rows[0].keys()) if rows else []
    write_csv(args.output, output, FIRST_PASS_COLUMNS + input_columns)
    metrics_data = metrics(output)
    args.metrics_json.write_text(json.dumps(metrics_data, indent=2, sort_keys=True), encoding="utf-8")
    write_markdown(args.metrics_md, metrics_data)
    print(json.dumps(metrics_data, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
