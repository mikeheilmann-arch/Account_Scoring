#!/usr/bin/env python3
"""Compute final vertical resolver metrics from populated ground-truth labels."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import urlparse


DEFAULT_RECOVERABLE_BUCKETS = {
    "bad_or_missing_url",
    "confirmed_url_extraction_failed",
    "manual_or_search_fallback",
    "non_icp_suspect",
}

PENDING_LABELS = {"", "pending"}
ICP_LABELS = {
    "icp - title/settlement agency",
    "icp - attorney settlement agent",
}
NON_ICP_LABELS = {"non-icp"}
PRECISION_THRESHOLD = 0.9


def clean(value: object) -> str:
    return str(value or "").strip()


def norm(value: object) -> str:
    return re.sub(r"\s+", " ", clean(value).lower())


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parse_yes_no(value: object) -> bool | None:
    text = norm(value)
    if text in PENDING_LABELS:
        return None
    if text == "yes":
        return True
    if text == "no":
        return False
    return None


def human_ratified(row: dict[str, object]) -> bool | None:
    return parse_yes_no(row.get("HumanRatified"))


def yes_no_parse_status(value: object) -> str:
    text = norm(value)
    if text in PENDING_LABELS:
        return "pending_or_blank"
    if parse_yes_no(text) is not None:
        return "valid"
    return "invalid_uncontrolled_value"


def parse_icp_label(value: object) -> str | None:
    text = norm(value)
    if text in PENDING_LABELS:
        return None
    if text in ICP_LABELS:
        return "ICP"
    if text in NON_ICP_LABELS:
        return "Non-ICP"
    return None


def icp_parse_status(value: object) -> str:
    text = norm(value)
    if text in PENDING_LABELS:
        return "pending_or_blank"
    if parse_icp_label(text) is not None:
        return "valid"
    return "invalid_uncontrolled_value"


def ground_truth_icp(row: dict[str, str]) -> str | None:
    vsb_registered = parse_yes_no(row.get("ManualVSBRegisteredSettlementAgent"))
    if vsb_registered is True:
        return "ICP"
    return parse_icp_label(row.get("GroundTruthICPClass"))


def icp_vsb_conflict(row: dict[str, str]) -> str:
    vsb_registered = parse_yes_no(row.get("ManualVSBRegisteredSettlementAgent"))
    icp_class = parse_icp_label(row.get("GroundTruthICPClass"))
    if vsb_registered is True and icp_class == "Non-ICP":
        return "conflict_vsb_yes_groundtruth_non_icp"
    return ""


def predicted_icp(row: dict[str, str]) -> str:
    text = norm(row.get("ProvisionalFirstPassICPLabel") or row.get("ProvisionalICPClass"))
    if any(token in text for token in ("confirmed icp", "likely icp", "title/settlement icp")):
        return "ICP"
    if any(token in text for token in ("non-icp", "non icp")):
        return "Non-ICP"
    return "Unknown"


def candidate_host(row: dict[str, str]) -> str:
    url = clean(row.get("CandidateWebsite"))
    if not url:
        return ""
    parsed = urlparse(url if "://" in url else f"https://{url}")
    return parsed.netloc.lower().removeprefix("www.")


def evidence_entries(row: dict[str, str]) -> list[str]:
    text = clean(row.get("AllEvidence")) or clean(row.get("TopEvidence"))
    return [entry.strip() for entry in text.split("||") if entry.strip()]


def scc_city_cross_check(row: dict[str, str]) -> tuple[str, str]:
    if clean(row.get("SCCRegistryStatus")) != "matched_active_agency":
        return "not_applicable_no_scc_match", ""
    city = clean(row.get("SCCMatchedCity"))
    host = candidate_host(row)
    if not city or not host:
        return "not_applicable_missing_city_or_candidate", ""

    city_lower = city.lower()
    any_city_evidence = ""
    for entry in evidence_entries(row):
        entry_lower = entry.lower()
        if city_lower not in entry_lower:
            continue
        if not any_city_evidence:
            any_city_evidence = entry
        if host in entry_lower:
            return "scc_city_found_in_candidate_evidence", truncate(entry)
    if any_city_evidence:
        return "scc_city_found_in_other_evidence", truncate(any_city_evidence)
    return "scc_city_not_found_in_evidence", ""


def truncate(text: str, limit: int = 350) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def rate(numerator: int, denominator: int) -> dict[str, object]:
    if denominator == 0:
        return {"status": "pending_labels", "numerator": numerator, "denominator": denominator, "rate": None}
    return {"status": "computed", "numerator": numerator, "denominator": denominator, "rate": numerator / denominator}


def rate_passes(value: dict[str, object], threshold: float) -> bool | None:
    if value["status"] != "computed":
        return None
    return bool(value["rate"] >= threshold)


def blended_rate(bucket_rates: dict[str, dict[str, object]]) -> dict[str, object]:
    numerator = sum(int(value["numerator"]) for value in bucket_rates.values())
    denominator = sum(int(value["denominator"]) for value in bucket_rates.values())
    return rate(numerator, denominator)


def one_yes_to_no_flip(value: dict[str, object]) -> dict[str, object]:
    if value["status"] != "computed" or int(value["denominator"]) == 0:
        return {"status": "pending_labels", "numerator": 0, "denominator": 0, "rate": None, "passes_gate": None}
    numerator = max(int(value["numerator"]) - 1, 0)
    denominator = int(value["denominator"])
    flipped = rate(numerator, denominator)
    flipped["passes_gate"] = rate_passes(flipped, PRECISION_THRESHOLD)
    return flipped


def labeler_provenance(rows: list[dict[str, object]], candidate_rows: list[dict[str, object]]) -> dict[str, object]:
    reviewer_counts = Counter(clean(row.get("Reviewer")) or "(blank)" for row in rows)
    ratified_counts = Counter(clean(row.get("HumanRatified")) or "(blank)" for row in rows)
    candidate_labeled = [row for row in candidate_rows if parse_yes_no(row.get("GroundTruthCorrectWebsite")) is not None]
    candidate_ratified = [row for row in candidate_labeled if human_ratified(row) is True]
    all_candidate_labels_ratified = bool(candidate_labeled) and len(candidate_ratified) == len(candidate_labeled)

    if not all_candidate_labels_ratified:
        status = "Provisional labeling pass; pending independent human ratification."
        labeler_line = "Provisional review complete; human ratification pending."
    else:
        status = "Human-ratified labels; computed where controlled labels are populated."
        labeler_line = "Candidate website labels human-ratified."

    return {
        "status": status,
        "labeler_line": labeler_line,
        "reviewer_counts": dict(reviewer_counts),
        "human_ratification_counts": dict(ratified_counts),
        "candidate_website_labels_ratified": len(candidate_ratified),
        "candidate_website_labels_requiring_ratification": len(candidate_labeled) - len(candidate_ratified),
    }


def compute_metrics(rows: list[dict[str, object]], recoverable_buckets: set[str]) -> dict[str, object]:
    candidate_rows = [row for row in rows if clean(row.get("CandidateWebsite"))]
    website_labeled = [row for row in candidate_rows if parse_yes_no(row.get("GroundTruthCorrectWebsite")) is not None]
    website_correct = [row for row in website_labeled if parse_yes_no(row.get("GroundTruthCorrectWebsite")) is True]
    website_ratified = [row for row in website_labeled if human_ratified(row) is True]
    website_ratified_correct = [
        row for row in website_ratified if parse_yes_no(row.get("GroundTruthCorrectWebsite")) is True
    ]

    predicted_icp_rows = [row for row in rows if predicted_icp(row) == "ICP"]
    predicted_icp_labeled = [row for row in predicted_icp_rows if ground_truth_icp(row) is not None]
    predicted_icp_correct = [row for row in predicted_icp_labeled if ground_truth_icp(row) == "ICP"]
    predicted_icp_ratified = [row for row in predicted_icp_labeled if human_ratified(row) is True]
    predicted_icp_ratified_correct = [row for row in predicted_icp_ratified if ground_truth_icp(row) == "ICP"]

    website_recovery_by_bucket: dict[str, dict[str, object]] = {}
    icp_recovery_by_bucket: dict[str, dict[str, object]] = {}
    for bucket in sorted(recoverable_buckets):
        bucket_rows = [row for row in rows if clean(row.get("Bucket")) == bucket]
        recoverable_rows = [row for row in bucket_rows if ground_truth_icp(row) == "ICP"]
        website_recovered = [
            row for row in recoverable_rows if parse_yes_no(row.get("GroundTruthCorrectWebsite")) is True
        ]
        icp_recovered = [row for row in recoverable_rows if predicted_icp(row) == "ICP"]
        website_recovery_by_bucket[bucket] = rate(len(website_recovered), len(recoverable_rows))
        icp_recovery_by_bucket[bucket] = rate(len(icp_recovered), len(recoverable_rows))

    candidate_website_precision = rate(len(website_correct), len(website_labeled))
    icp_positive_precision = rate(len(predicted_icp_correct), len(predicted_icp_labeled))
    website_recovery_blended = blended_rate(website_recovery_by_bucket)

    return {
        "rows": len(rows),
        "labeler_provenance": labeler_provenance(rows, candidate_rows),
        "candidate_websites": len(candidate_rows),
        "ground_truth_website_labels_present": len(website_labeled),
        "ground_truth_website_labels_missing": len(candidate_rows) - len(website_labeled),
        "candidate_website_precision": candidate_website_precision,
        "human_ratified_candidate_website_precision": rate(
            len(website_ratified_correct), len(website_ratified)
        ),
        "predicted_icp_rows": len(predicted_icp_rows),
        "ground_truth_icp_labels_present_for_predicted_icp": len(predicted_icp_labeled),
        "ground_truth_icp_labels_missing_for_predicted_icp": len(predicted_icp_rows) - len(predicted_icp_labeled),
        "icp_positive_precision": icp_positive_precision,
        "human_ratified_icp_positive_precision": rate(
            len(predicted_icp_ratified_correct), len(predicted_icp_ratified)
        ),
        "website_recovery_denominator": "Rows in recoverable buckets where GroundTruthICPClass or ManualVSBRegisteredSettlementAgent confirms ICP; excludes non-ICP and unlabeled rows.",
        "website_recovery_by_bucket": website_recovery_by_bucket,
        "website_recovery_blended": website_recovery_blended,
        "icp_recovery_by_bucket": icp_recovery_by_bucket,
        "scc_city_cross_check_counts": dict(Counter(clean(row.get("SCCCityCrossCheck")) for row in rows)),
        "scc_city_not_found_candidate_rows": [
            {
                "Name": clean(row.get("Name")),
                "CandidateWebsite": clean(row.get("CandidateWebsite")),
                "SCCMatchedName": clean(row.get("SCCMatchedName")),
                "SCCMatchedCity": clean(row.get("SCCMatchedCity")),
                "ReviewPriority": clean(row.get("ReviewPriority")),
                "GroundTruthCorrectWebsite": clean(row.get("GroundTruthCorrectWebsite")),
            }
            for row in rows
            if clean(row.get("SCCCityCrossCheck")) == "scc_city_not_found_in_evidence"
        ],
        "recovery_label_counts": dict(Counter(clean(row.get("ProvisionalFirstPassRecoveryLabel")) for row in rows)),
        "invalid_controlled_label_counts": {
            "GroundTruthCorrectWebsite": sum(
                1
                for row in candidate_rows
                if yes_no_parse_status(row.get("GroundTruthCorrectWebsite")) == "invalid_uncontrolled_value"
            ),
            "GroundTruthICPClass": sum(
                1 for row in rows if icp_parse_status(row.get("GroundTruthICPClass")) == "invalid_uncontrolled_value"
            ),
            "ManualVSBRegisteredSettlementAgent": sum(
                1
                for row in rows
                if yes_no_parse_status(row.get("ManualVSBRegisteredSettlementAgent"))
                == "invalid_uncontrolled_value"
            ),
            "HumanRatified": sum(
                1 for row in rows if yes_no_parse_status(row.get("HumanRatified")) == "invalid_uncontrolled_value"
            ),
        },
        "ground_truth_conflict_counts": dict(
            Counter(icp_vsb_conflict(row) or "(none)" for row in rows)
        ),
        "ground_truth_icp_counts": dict(Counter(ground_truth_icp(row) or "(unlabeled)" for row in rows)),
        "ground_truth_website_counts": dict(
            Counter(
                "Correct"
                if parse_yes_no(row.get("GroundTruthCorrectWebsite")) is True
                else "Incorrect"
                if parse_yes_no(row.get("GroundTruthCorrectWebsite")) is False
                else "(unlabeled)"
                for row in candidate_rows
            )
        ),
        "targets": {
            "candidate_website_precision": ">=90%",
            "icp_class_precision": ">=90%",
            "recovery_rate": ">=60% on recoverable buckets",
        },
        "controlled_label_contract": {
            "GroundTruthCorrectWebsite": ["Yes", "No", "Pending"],
            "GroundTruthICPClass": [
                "ICP - Title/Settlement Agency",
                "ICP - Attorney Settlement Agent",
                "Non-ICP",
                "Pending",
            ],
            "ManualVSBRegisteredSettlementAgent": ["Yes", "No", "Pending"],
            "HumanRatified": ["Yes", "No", "Pending"],
        },
        "sensitivity": {
            "one_yes_to_no_flip_candidate_website_precision": one_yes_to_no_flip(candidate_website_precision),
            "website_recovery_blended": website_recovery_blended,
            "precision_threshold": PRECISION_THRESHOLD,
        },
    }


def annotate_rows(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    annotated = []
    for row in rows:
        check, evidence = scc_city_cross_check(row)
        annotated.append(
            {
                "SCCCityCrossCheck": check,
                "SCCCityCrossCheckEvidence": evidence,
                "GroundTruthCorrectWebsiteParseStatus": yes_no_parse_status(row.get("GroundTruthCorrectWebsite")),
                "GroundTruthICPClassParseStatus": icp_parse_status(row.get("GroundTruthICPClass")),
                "ManualVSBRegisteredSettlementAgentParseStatus": yes_no_parse_status(
                    row.get("ManualVSBRegisteredSettlementAgent")
                ),
                "HumanRatifiedParseStatus": yes_no_parse_status(row.get("HumanRatified")),
                "GroundTruthICPVSBConflict": icp_vsb_conflict(row),
                **row,
            }
        )
    return annotated


def format_rate(value: dict[str, object]) -> str:
    if value["status"] != "computed":
        return f"pending labels ({value['numerator']}/{value['denominator']})"
    return f"{value['rate']:.1%} ({value['numerator']}/{value['denominator']})"


def format_pass_fail(value: dict[str, object]) -> str:
    passes_gate = value.get("passes_gate")
    if passes_gate is None:
        return "pending"
    return "passes gate" if passes_gate else "fails gate"


def write_markdown(path: Path, metrics_data: dict[str, object]) -> None:
    lines = [
        "# Vertical Resolver Final Metrics",
        "",
        f"Status: {metrics_data['labeler_provenance']['status']}",
        f"Label provenance: {metrics_data['labeler_provenance']['labeler_line']}",
        "",
        f"- Rows: {metrics_data['rows']}",
        f"- Candidate websites: {metrics_data['candidate_websites']}",
        f"- Current website labels present: {metrics_data['ground_truth_website_labels_present']}",
        f"- Current website labels missing: {metrics_data['ground_truth_website_labels_missing']}",
        f"- Candidate website precision (current labels): {format_rate(metrics_data['candidate_website_precision'])}",
        f"- Human-ratified candidate website precision: {format_rate(metrics_data['human_ratified_candidate_website_precision'])}",
        f"- Predicted ICP rows: {metrics_data['predicted_icp_rows']}",
        f"- Current ICP labels present for predicted ICP rows: {metrics_data['ground_truth_icp_labels_present_for_predicted_icp']}",
        f"- Current ICP labels missing for predicted ICP rows: {metrics_data['ground_truth_icp_labels_missing_for_predicted_icp']}",
        f"- ICP positive precision (current labels): {format_rate(metrics_data['icp_positive_precision'])}",
        f"- Human-ratified ICP positive precision: {format_rate(metrics_data['human_ratified_icp_positive_precision'])}",
        f"- Human-ratified candidate website labels: {metrics_data['labeler_provenance']['candidate_website_labels_ratified']}",
        f"- Candidate website labels requiring ratification: {metrics_data['labeler_provenance']['candidate_website_labels_requiring_ratification']}",
        "",
        "## Recovery Denominator",
        "",
        metrics_data["website_recovery_denominator"],
        "",
        "## Website Recovery By Bucket",
        "",
    ]
    for bucket, bucket_rate in metrics_data["website_recovery_by_bucket"].items():
        lines.append(f"- {bucket}: {format_rate(bucket_rate)}")
    lines.append(f"- blended: {format_rate(metrics_data['website_recovery_blended'])}")

    sensitivity = metrics_data["sensitivity"]
    one_flip = sensitivity["one_yes_to_no_flip_candidate_website_precision"]
    blended = sensitivity["website_recovery_blended"]
    lines.extend(
        [
            "",
            "## Sensitivity",
            "",
            f"- One Yes->No flip in candidate website labels: {format_rate(one_flip)} ({format_pass_fail(one_flip)} against 90% precision gate)",
            f"- Blended website recovery: {format_rate(blended)}; sensitive to completed VSB lookups changing the recovery denominator.",
        ]
    )

    lines.extend(["", "## ICP Recovery By Bucket", ""])
    for bucket, bucket_rate in metrics_data["icp_recovery_by_bucket"].items():
        lines.append(f"- {bucket}: {format_rate(bucket_rate)}")

    lines.extend(["", "## SCC City Cross-Check Counts", ""])
    for key, count in metrics_data["scc_city_cross_check_counts"].items():
        lines.append(f"- {key}: {count}")

    missing_city_rows = metrics_data["scc_city_not_found_candidate_rows"]
    if missing_city_rows:
        lines.extend(["", "Automated SCC City Cross-Check Flags:", ""])
        for row in missing_city_rows:
            lines.append(
                "- "
                f"{row['Name']}: candidate `{row['CandidateWebsite']}`; "
                f"SCC match `{row['SCCMatchedName']}` in {row['SCCMatchedCity']}; "
                f"{row['ReviewPriority']}; "
                f"manual website label `{row['GroundTruthCorrectWebsite'] or 'Pending'}`"
            )

    lines.extend(["", "## Invalid Controlled Labels", ""])
    for key, count in metrics_data["invalid_controlled_label_counts"].items():
        lines.append(f"- {key}: {count}")

    lines.extend(["", "## Label/VSB Conflicts", ""])
    for key, count in metrics_data["ground_truth_conflict_counts"].items():
        lines.append(f"- {key}: {count}")

    lines.extend(
        [
            "",
            "## Required Label Values",
            "",
            "- `GroundTruthCorrectWebsite`: `Yes`, `No`, or `Pending`",
            "- `GroundTruthICPClass`: `ICP - Title/Settlement Agency`, `ICP - Attorney Settlement Agent`, `Non-ICP`, or `Pending`",
            "- `ManualVSBRegisteredSettlementAgent`: `Yes`, `No`, or `Pending`",
            "- `HumanRatified`: `Yes`, `No`, or `Pending`",
            "- Free-text explanations belong in `GroundTruthNotes` or `ManualVSBNotes`, not in controlled label fields.",
        ]
    )

    lines.extend(
        [
            "",
            "Important: if the precision lines say pending labels, do not quote the metric as model performance.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute final vertical resolver metrics from human labels.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--metrics-json", required=True, type=Path)
    parser.add_argument("--metrics-md", required=True, type=Path)
    parser.add_argument("--annotated-output", type=Path)
    parser.add_argument(
        "--recoverable-buckets",
        default=",".join(sorted(DEFAULT_RECOVERABLE_BUCKETS)),
        help="Comma-separated bucket names used for recovery-rate denominators.",
    )
    args = parser.parse_args()

    rows = read_csv(args.input)
    annotated = annotate_rows(rows)
    recoverable_buckets = {bucket.strip() for bucket in args.recoverable_buckets.split(",") if bucket.strip()}
    metrics_data = compute_metrics(annotated, recoverable_buckets)

    args.metrics_json.write_text(json.dumps(metrics_data, indent=2, sort_keys=True), encoding="utf-8")
    write_markdown(args.metrics_md, metrics_data)
    if args.annotated_output:
        input_columns = list(rows[0].keys()) if rows else []
        write_csv(
            args.annotated_output,
            annotated,
            [
                "SCCCityCrossCheck",
                "SCCCityCrossCheckEvidence",
                "GroundTruthCorrectWebsiteParseStatus",
                "GroundTruthICPClassParseStatus",
                "ManualVSBRegisteredSettlementAgentParseStatus",
                "HumanRatifiedParseStatus",
                "GroundTruthICPVSBConflict",
            ]
            + input_columns,
        )
    print(json.dumps(metrics_data, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
