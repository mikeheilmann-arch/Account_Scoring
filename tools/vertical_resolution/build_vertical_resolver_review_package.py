#!/usr/bin/env python3
"""Build reviewer-ready labels and metrics scaffold for the resolver POC."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path


REVIEW_COLUMNS = [
    "ReviewPriority",
    "ReviewerAction",
    "ProvisionalCorrectWebsite",
    "ProvisionalICPClass",
    "ProvisionalRecoveryCredit",
    "ManualVSBCheckRequired",
    "ManualVSBRegisteredSettlementAgent",
    "ManualVSBNotes",
]

LABEL_GUIDE_COLUMNS = [
    "GroundTruthCorrectWebsiteAllowedValues",
    "GroundTruthICPClassAllowedValues",
    "ManualVSBRegisteredSettlementAgentAllowedValues",
]

LABEL_COLUMNS = [
    "GroundTruthCorrectWebsite",
    "GroundTruthICPClass",
    "GroundTruthNotes",
    "Reviewer",
    "ReviewedAt",
]


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


def is_scc_matched(row: dict[str, str]) -> bool:
    return clean(row.get("SCCRegistryStatus")) == "matched_active_agency"


def is_law_named(row: dict[str, str]) -> bool:
    return clean(row.get("AccountLawSignal")).lower() == "yes" or clean(row.get("ResolverOutcome")) == "law_firm_review"


def has_strong_candidate(row: dict[str, str]) -> bool:
    return bool(clean(row.get("CandidateWebsite"))) and clean(row.get("CandidateEntityMatch")) == "strong"


def provisional_correct_website(row: dict[str, str]) -> str:
    if not clean(row.get("CandidateWebsite")):
        return "N/A - no candidate website"
    if has_strong_candidate(row) and is_scc_matched(row):
        return "Likely Yes - strong entity candidate plus SCC match; reviewer confirm"
    if clean(row.get("ResolverOutcome")) == "high_confidence_recovery" and has_strong_candidate(row):
        return "Likely Yes - high-confidence resolver candidate; reviewer confirm"
    if is_law_named(row) and not is_scc_matched(row):
        return "Unknown - manual VSB/law-firm review"
    return "Unknown - reviewer confirm"


def provisional_icp_class(row: dict[str, str]) -> str:
    if is_scc_matched(row) and is_law_named(row):
        return "Law-named title/settlement ICP - SCC confirmed"
    if is_scc_matched(row):
        return f"Title/settlement ICP - SCC {clean(row.get('SCCMatchType'))} confirmed"
    if is_law_named(row):
        return "Unknown - manual VSB/attorney settlement review"
    if clean(row.get("ResolverOutcome")) in {"high_confidence_recovery", "reviewer_assist"}:
        return "Likely title/settlement ICP - reviewer confirm"
    return "Unknown"


def manual_vsb_required(row: dict[str, str]) -> str:
    return "Yes" if is_law_named(row) and not is_scc_matched(row) else "No"


def reviewer_action(row: dict[str, str]) -> str:
    if manual_vsb_required(row) == "Yes":
        return "Manual VSB/attorney-settlement check; SCC non-match is not disqualifying"
    if is_scc_matched(row) and not clean(row.get("CandidateWebsite")):
        return "ICP SCC-confirmed; find or confirm official website"
    if is_scc_matched(row):
        return "Confirm candidate website; ICP is SCC-confirmed"
    if clean(row.get("ResolverOutcome")) == "high_confidence_recovery":
        return "Confirm high-confidence website and ICP; SCC did not match"
    if clean(row.get("ResolverOutcome")) == "unresolved":
        return "Manual unresolved review"
    return "Confirm evidence and final ICP class"


def review_priority(row: dict[str, str]) -> str:
    if manual_vsb_required(row) == "Yes":
        return "P1 - VSB law-firm lane"
    if is_scc_matched(row) and not clean(row.get("CandidateWebsite")):
        return "P0 - SCC match missing website"
    if clean(row.get("ResolverOutcome")) == "high_confidence_recovery":
        return "P0 - high-confidence recovery"
    if is_scc_matched(row):
        return "P1 - SCC-confirmed reviewer assist"
    return "P2 - manual/unknown"


def provisional_recovery_credit(row: dict[str, str]) -> str:
    if clean(row.get("CandidateWebsite")) and is_scc_matched(row):
        return "Website candidate - SCC-confirmed ICP; pending website label"
    if clean(row.get("CandidateWebsite")):
        return "Website candidate - SCC-silent; pending ICP/website label"
    if is_scc_matched(row):
        return "SCC-confirmed ICP - website still needed"
    if clean(row.get("ResolverOutcome")) == "reviewer_assist":
        return "Reviewer-assist evidence - no candidate website"
    return "No provisional recovery credit"


def build_review_rows(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    output = []
    for row in rows:
        review_values = {
            "GroundTruthCorrectWebsiteAllowedValues": "Yes | No | Pending",
            "GroundTruthICPClassAllowedValues": "ICP - Title/Settlement Agency | ICP - Attorney Settlement Agent | Non-ICP | Pending",
            "ManualVSBRegisteredSettlementAgentAllowedValues": "Yes | No | Pending",
            "ReviewPriority": review_priority(row),
            "ReviewerAction": reviewer_action(row),
            "ProvisionalCorrectWebsite": provisional_correct_website(row),
            "ProvisionalICPClass": provisional_icp_class(row),
            "ProvisionalRecoveryCredit": provisional_recovery_credit(row),
            "ManualVSBCheckRequired": manual_vsb_required(row),
            "ManualVSBRegisteredSettlementAgent": clean(row.get("ManualVSBRegisteredSettlementAgent")),
            "ManualVSBNotes": clean(row.get("ManualVSBNotes")),
        }
        label_values = {column: clean(row.get(column)) for column in LABEL_COLUMNS}
        base = {
            key: value
            for key, value in row.items()
            if key not in set(LABEL_GUIDE_COLUMNS + REVIEW_COLUMNS + LABEL_COLUMNS)
        }
        output.append({**review_values, **label_values, **base})
    return output


def metrics(rows: list[dict[str, object]]) -> dict[str, object]:
    total = len(rows)
    scc_matches = [row for row in rows if clean(row.get("SCCRegistryStatus")) == "matched_active_agency"]
    law_vsb = [row for row in rows if clean(row.get("ManualVSBCheckRequired")) == "Yes"]
    candidates = [row for row in rows if clean(row.get("CandidateWebsite"))]
    return {
        "rows": total,
        "candidate_websites_to_label": len(candidates),
        "scc_active_agency_matches": len(scc_matches),
        "manual_vsb_checks_required": len(law_vsb),
        "scc_confirmed_missing_candidate_website": sum(
            1 for row in scc_matches if not clean(row.get("CandidateWebsite"))
        ),
        "outcome_counts": dict(Counter(clean(row.get("ResolverOutcome")) or "(blank)" for row in rows)),
        "review_priority_counts": dict(Counter(clean(row.get("ReviewPriority")) or "(blank)" for row in rows)),
        "scc_by_outcome_counts": dict(
            Counter(
                f"{clean(row.get('ResolverOutcome')) or '(blank)'} | {clean(row.get('SCCRegistryStatus')) or '(blank)'}"
                for row in rows
            )
        ),
        "measurement_gate": {
            "candidate_website_precision": "pending reviewer labels",
            "icp_class_precision": "pending reviewer labels",
            "recovery_rate_by_bucket": "pending reviewer labels",
            "target_candidate_website_precision": ">=90%",
            "target_recovery_rate": ">=60% on recoverable buckets",
        },
    }


def write_markdown(path: Path, metrics_data: dict[str, object]) -> None:
    lines = [
        "# Vertical Resolver Review Metrics Scaffold",
        "",
        "Status: pending reviewer labels",
        "",
        f"- Rows: {metrics_data['rows']}",
        f"- Candidate websites to label: {metrics_data['candidate_websites_to_label']}",
        f"- SCC active agency matches: {metrics_data['scc_active_agency_matches']}",
        f"- Manual VSB checks required: {metrics_data['manual_vsb_checks_required']}",
        f"- SCC-confirmed rows missing candidate website: {metrics_data['scc_confirmed_missing_candidate_website']}",
        "",
        "## Measurement Gate",
        "",
        "- Candidate website precision: pending reviewer labels; target >=90%",
        "- ICP-class precision: pending reviewer labels; target >=90%",
        "- Recovery rate by bucket: pending reviewer labels; target >=60% on recoverable buckets",
        "",
        "## Required Label Values",
        "",
        "Use these exact values so final metrics do not parse free-text notes as labels:",
        "",
        "- `GroundTruthCorrectWebsite`: `Yes`, `No`, or `Pending`",
        "- `GroundTruthICPClass`: `ICP - Title/Settlement Agency`, `ICP - Attorney Settlement Agent`, `Non-ICP`, or `Pending`",
        "- `ManualVSBRegisteredSettlementAgent`: `Yes`, `No`, or `Pending`",
        "- Put explanations such as `no matches`, `no settlement work`, source notes, or uncertainty in `GroundTruthNotes` / `ManualVSBNotes`, not in the controlled label fields.",
        "",
        "## Review Priority Counts",
        "",
    ]
    for key, value in metrics_data["review_priority_counts"].items():
        lines.append(f"- {key}: {value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build vertical resolver reviewer package.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--metrics-json", required=True, type=Path)
    parser.add_argument("--metrics-md", required=True, type=Path)
    args = parser.parse_args()

    rows = read_csv(args.input)
    review_rows = build_review_rows(rows)
    input_columns = (
        [
            column
            for column in rows[0].keys()
            if column not in set(LABEL_GUIDE_COLUMNS + REVIEW_COLUMNS + LABEL_COLUMNS)
        ]
        if rows
        else []
    )
    write_csv(args.output, review_rows, LABEL_GUIDE_COLUMNS + REVIEW_COLUMNS + LABEL_COLUMNS + input_columns)
    metrics_data = metrics(review_rows)
    args.metrics_json.write_text(json.dumps(metrics_data, indent=2, sort_keys=True), encoding="utf-8")
    write_markdown(args.metrics_md, metrics_data)
    print(json.dumps(metrics_data, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
