#!/usr/bin/env python3
"""Audit and consolidate a sharded, no-write CertifID CRM scoring run."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


EXPECTED_ACTIONS = {
    "score_now",
    "manual_review",
    "non_icp_confirmed",
    "insufficient_public_evidence",
    "hygiene_review",
}
EXPECTED_RETRIEVAL_STATUSES = {
    "ok",
    "partial",
    "map_failed",
    "extract_failed",
    "not_run",
    "script_error",
}
EXPECTED_CONFIDENCE = {"High", "Medium", "Low"}
KNOWN_QUALITY_CONTROLS = {
    ("title companies in", "wlta.org"): "wrong association/directory website",
    ("sb titles", "yourstatebank.com"): "wrong bank website",
    ("northern new york title agency", "ctic.com"): "underwriter/direct-side review",
    ("master settlement services", "nextierbank.com"): "wrong bank website",
    ("greenridge title agency", "greenridge.com"): "brokerage/entity review",
    ("greco title agency", "atatitle.com"): "owned/direct-side review",
    ("denver land title", "ltgc.com"): "parent rollup review",
    ("texas capital title", "ctot.com"): "parent rollup review",
    ("real estate title service", "retitleservice.com"): "abstract-only review",
    ("ionia county title", "ioniacounty.org"): "wrong county website",
}
SCORED_FIELDS = (
    "EstimatedMCV",
    "EstimatedMCVLow",
    "EstimatedMCVHigh",
    "EstimatedARR",
    "ARRRange",
    "Score",
)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, str]], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def clean(value: object) -> str:
    return "" if value is None else str(value).strip()


def as_int(value: object) -> int | None:
    text = clean(value).replace(",", "").replace("$", "")
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def counts(rows: list[dict[str, str]], field: str) -> dict[str, int]:
    return dict(sorted(Counter(clean(row.get(field)) or "<blank>" for row in rows).items()))


def pct(numerator: int, denominator: int) -> float:
    return round(100 * numerator / denominator, 2) if denominator else 0.0


def task_index(path: Path) -> int:
    match = re.search(r"task-(\d+)_scores\.csv$", path.name)
    if not match:
        raise ValueError(f"Unexpected score filename: {path.name}")
    return int(match.group(1))


def normalized_name(value: object) -> str:
    tokens = re.findall(r"[a-z0-9]+", clean(value).lower())
    suffixes = {"co", "company", "corp", "corporation", "inc", "llc", "ltd"}
    return " ".join(token for token in tokens if token not in suffixes)


def normalized_domain(value: object) -> str:
    text = clean(value).lower()
    text = re.sub(r"^[a-z]+://", "", text)
    text = text.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    text = text.split(":", 1)[0].strip(".")
    return text[4:] if text.startswith("www.") else text


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--overlay", required=True, type=Path)
    parser.add_argument("--score-dir", required=True, type=Path)
    parser.add_argument("--summary-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--expected-task-count", type=int, default=320)
    parser.add_argument("--run-id", default="crm-full-quality-gated-v1-20260708")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    input_rows = read_csv(args.input)
    overlay_rows = read_csv(args.overlay)
    score_files = sorted(args.score_dir.glob("task-*_scores.csv"), key=task_index)
    summary_files = sorted(args.summary_dir.glob("task-*_scores_summary.json"))
    manifest_files = sorted(args.summary_dir.glob("task-*_manifest.json"))

    output_rows: list[dict[str, str]] = []
    output_task_by_id: dict[str, int] = {}
    header_variants: Counter[tuple[str, ...]] = Counter()
    per_task_rows: dict[int, int] = {}
    for path in score_files:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            header_variants[tuple(reader.fieldnames or [])] += 1
            rows = list(reader)
        index = task_index(path)
        per_task_rows[index] = len(rows)
        for row in rows:
            output_task_by_id[clean(row.get("Id"))] = index
        output_rows.extend(rows)

    input_ids = [clean(row.get("Id")) for row in input_rows]
    output_ids = [clean(row.get("Id")) for row in output_rows]
    overlay_by_id = {clean(row.get("AccountId")): row for row in overlay_rows}
    input_by_id = {clean(row.get("Id")): row for row in input_rows}
    input_id_counts = Counter(input_ids)
    output_id_counts = Counter(output_ids)

    missing_ids = sorted(set(input_ids) - set(output_ids))
    unexpected_ids = sorted(set(output_ids) - set(input_ids))
    duplicate_input_ids = sorted(key for key, count in input_id_counts.items() if key and count > 1)
    duplicate_output_ids = sorted(key for key, count in output_id_counts.items() if key and count > 1)

    shard_mismatches = []
    for index, row in enumerate(input_rows):
        account_id = clean(row.get("Id"))
        expected_task = index % args.expected_task_count
        actual_task = output_task_by_id.get(account_id)
        if actual_task != expected_task:
            shard_mismatches.append({"Id": account_id, "ExpectedTask": expected_task, "ActualTask": actual_task})

    input_output_mismatches: list[dict[str, str]] = []
    field_pairs = (
        ("Name", "Name"),
        ("Website", "Website"),
        ("BillingState", "BillingState"),
        ("Segment", "Segment"),
        ("Owner", "Owner"),
        ("FinalMCV", "InputFinalMCV"),
        ("MCVSource", "InputMCVSource"),
        ("HasAnyOpp", "InputHasAnyOpp"),
        ("LegacyTier", "InputLegacyTier"),
    )
    for row in output_rows:
        source = input_by_id.get(clean(row.get("Id")), {})
        for input_field, output_field in field_pairs:
            if clean(source.get(input_field)) != clean(row.get(output_field)):
                input_output_mismatches.append(
                    {
                        "Id": clean(row.get("Id")),
                        "Field": output_field,
                        "InputValue": clean(source.get(input_field)),
                        "OutputValue": clean(row.get(output_field)),
                    }
                )

    invalid_actions = sorted(set(clean(row.get("ReviewAction")) for row in output_rows) - EXPECTED_ACTIONS)
    invalid_retrieval = sorted(set(clean(row.get("RetrievalStatus")) for row in output_rows) - EXPECTED_RETRIEVAL_STATUSES)
    invalid_confidence = sorted(set(clean(row.get("Confidence")) for row in output_rows) - EXPECTED_CONFIDENCE)

    score_now_rows = [row for row in output_rows if clean(row.get("ReviewAction")) == "score_now"]
    non_score_rows = [row for row in output_rows if clean(row.get("ReviewAction")) != "score_now"]
    score_now_missing_values = [row for row in score_now_rows if any(not clean(row.get(field)) for field in SCORED_FIELDS)]
    non_score_with_values = [row for row in non_score_rows if any(clean(row.get(field)) for field in SCORED_FIELDS)]

    band_violations: list[dict[str, str]] = []
    numeric_parse_failures: list[dict[str, str]] = []
    for row in score_now_rows:
        values = {field: as_int(row.get(field)) for field in ("EstimatedMCV", "EstimatedMCVLow", "EstimatedMCVHigh", "EstimatedARR", "Score")}
        bad_fields = [field for field, value in values.items() if value is None]
        if bad_fields:
            numeric_parse_failures.append({"Id": clean(row.get("Id")), "Fields": ",".join(bad_fields)})
            continue
        if not values["EstimatedMCVLow"] <= values["EstimatedMCV"] <= values["EstimatedMCVHigh"]:
            band_violations.append(
                {
                    "Id": clean(row.get("Id")),
                    "EstimatedMCV": clean(row.get("EstimatedMCV")),
                    "EstimatedMCVLow": clean(row.get("EstimatedMCVLow")),
                    "EstimatedMCVHigh": clean(row.get("EstimatedMCVHigh")),
                }
            )

    preflight_leaks = []
    missing_overlay = []
    for row in output_rows:
        account_id = clean(row.get("Id"))
        overlay = overlay_by_id.get(account_id)
        if not overlay:
            missing_overlay.append(account_id)
        elif clean(overlay.get("QualityAction")) != "allow_score" or clean(overlay.get("ScoreEligible")).lower() not in {"true", "1", "yes"}:
            preflight_leaks.append(
                {
                    "Id": account_id,
                    "QualityAction": clean(overlay.get("QualityAction")),
                    "ScoreEligible": clean(overlay.get("ScoreEligible")),
                }
            )

    score_now_failed_retrieval = [
        row for row in score_now_rows if clean(row.get("RetrievalStatus")) in {"map_failed", "extract_failed", "script_error"}
    ]
    score_now_failed_without_trusted_input = [
        row
        for row in score_now_failed_retrieval
        if not clean(row.get("InputFinalMCV"))
        or not any(token in clean(row.get("InputMCVSource")).lower() for token in ("sales rep", "bdr", "ae", "rep"))
    ]

    score_now_icp_counts = counts(score_now_rows, "ICPDisposition")
    suspicious_score_now_dispositions = [
        row
        for row in score_now_rows
        if clean(row.get("ICPDisposition")) in {"non_icp", "indeterminate", "hygiene_needed", "manual_review"}
    ]

    law_firm_score_now_not_legal = []
    for row in score_now_rows:
        overlay = overlay_by_id.get(clean(row.get("Id")), {})
        if (
            clean(overlay.get("CrmCompanyType")).lower() == "law firm"
            and clean(row.get("LegalEntityRoute")) in {"not_legal_entity", "not_evaluated"}
        ):
            law_firm_score_now_not_legal.append(row)

    quality_control_results = []
    for (expected_name, expected_domain), expected_outcome in KNOWN_QUALITY_CONTROLS.items():
        matches = [
            row
            for row in output_rows
            if normalized_name(row.get("Name")) == expected_name
            and normalized_domain(row.get("Website")) == expected_domain
        ]
        quality_control_results.append(
            {
                "name": expected_name,
                "domain": expected_domain,
                "expected": expected_outcome,
                "present_in_score_input": bool(matches),
                "actions": sorted({clean(row.get("ReviewAction")) for row in matches}),
                "passed": not matches or all(clean(row.get("ReviewAction")) != "score_now" for row in matches),
            }
        )
    failed_quality_controls = [item for item in quality_control_results if not item["passed"]]

    manifests = [json.loads(path.read_text(encoding="utf-8")) for path in manifest_files]
    summaries = [json.loads(path.read_text(encoding="utf-8")) for path in summary_files]
    manifest_exit_codes = Counter(str(item.get("exit_code")) for item in manifests)
    manifest_run_ids = Counter(clean(item.get("run_id")) for item in manifests)
    summary_row_total = sum(int(item.get("rows", 0)) for item in summaries)

    checks = {
        "task_files_complete": len(score_files) == args.expected_task_count,
        "summary_files_complete": len(summary_files) == args.expected_task_count,
        "manifest_files_complete": len(manifest_files) == args.expected_task_count,
        "one_schema": len(header_variants) == 1,
        "row_count_matches_input": len(output_rows) == len(input_rows),
        "summary_rows_match_output": summary_row_total == len(output_rows),
        "input_ids_unique": not duplicate_input_ids,
        "output_ids_unique": not duplicate_output_ids,
        "no_missing_ids": not missing_ids,
        "no_unexpected_ids": not unexpected_ids,
        "shards_match_input_contract": not shard_mismatches,
        "input_fields_preserved": not input_output_mismatches,
        "all_rows_passed_preflight": not preflight_leaks and not missing_overlay,
        "accepted_actions_only": not invalid_actions,
        "accepted_retrieval_statuses_only": not invalid_retrieval,
        "accepted_confidence_only": not invalid_confidence,
        "score_now_fields_complete": not score_now_missing_values,
        "non_score_rows_have_no_values": not non_score_with_values,
        "score_points_inside_bands": not band_violations and not numeric_parse_failures,
        "no_script_errors": counts(output_rows, "RetrievalStatus").get("script_error", 0) == 0,
        "manifests_successful": set(manifest_exit_codes) == {"0"},
        "manifest_run_id_consistent": set(manifest_run_ids) == {args.run_id},
    }

    warnings = {
        "score_now_with_failed_retrieval": len(score_now_failed_retrieval),
        "score_now_failed_retrieval_without_trusted_input": len(score_now_failed_without_trusted_input),
        "score_now_with_suspicious_disposition": len(suspicious_score_now_dispositions),
        "manual_or_insufficient_rows": sum(
            1 for row in output_rows if clean(row.get("ReviewAction")) in {"manual_review", "insufficient_public_evidence"}
        ),
        "crm_law_firm_score_now_not_legal_entity": len(law_firm_score_now_not_legal),
        "failed_known_quality_controls": len(failed_quality_controls),
    }

    business_checks = {
        "crm_law_firms_use_legal_lane_before_score": not law_firm_score_now_not_legal,
        "known_quality_controls_not_scored": not failed_quality_controls,
    }

    audit = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "run_id": args.run_id,
        "dataset": {
            "input_rows": len(input_rows),
            "output_rows": len(output_rows),
            "score_files": len(score_files),
            "summary_files": len(summary_files),
            "manifest_files": len(manifest_files),
            "summary_row_total": summary_row_total,
            "score_now_rows": len(score_now_rows),
            "score_now_rate_pct": pct(len(score_now_rows), len(output_rows)),
        },
        "distributions": {
            "review_action": counts(output_rows, "ReviewAction"),
            "retrieval_status": counts(output_rows, "RetrievalStatus"),
            "confidence": counts(output_rows, "Confidence"),
            "icp_disposition": counts(output_rows, "ICPDisposition"),
            "score_now_icp_disposition": score_now_icp_counts,
            "legal_entity_route": counts(output_rows, "LegalEntityRoute"),
            "estimated_arr": counts(score_now_rows, "EstimatedARR"),
        },
        "checks": checks,
        "business_checks": business_checks,
        "quality_control_results": quality_control_results,
        "warnings": warnings,
        "issue_counts": {
            "missing_ids": len(missing_ids),
            "unexpected_ids": len(unexpected_ids),
            "duplicate_input_ids": len(duplicate_input_ids),
            "duplicate_output_ids": len(duplicate_output_ids),
            "shard_mismatches": len(shard_mismatches),
            "input_output_mismatches": len(input_output_mismatches),
            "missing_overlay": len(missing_overlay),
            "preflight_leaks": len(preflight_leaks),
            "invalid_actions": len(invalid_actions),
            "invalid_retrieval_statuses": len(invalid_retrieval),
            "invalid_confidence": len(invalid_confidence),
            "score_now_missing_values": len(score_now_missing_values),
            "non_score_with_values": len(non_score_with_values),
            "numeric_parse_failures": len(numeric_parse_failures),
            "band_violations": len(band_violations),
        },
        "manifest_exit_codes": dict(manifest_exit_codes),
        "manifest_run_ids": dict(manifest_run_ids),
        "per_task_row_counts": dict(Counter(per_task_rows.values())),
        "append_readiness": {
            "structural_checks_pass": all(checks.values()),
            "business_checks_pass": all(business_checks.values()),
            "ready_for_salesforce_writeback": all(checks.values()) and all(business_checks.values()),
            "recommended_scope": "score_now only; keep manual_review, insufficient_public_evidence, and non_icp_confirmed score fields blank",
            "requires_payload_build_and_live_prewrite_backup": True,
        },
    }

    combined_path = args.output_dir / "crm_full_quality_gated_scores_combined_2026-07-10.csv"
    audit_json_path = args.output_dir / "crm_full_quality_gated_audit_2026-07-10.json"
    audit_md_path = args.output_dir / "crm_full_quality_gated_audit_2026-07-10.md"
    issue_path = args.output_dir / "crm_full_quality_gated_audit_issue_samples_2026-07-10.json"
    write_csv(combined_path, output_rows, list(output_rows[0].keys()) if output_rows else [])
    audit_json_path.write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")

    issue_samples = {
        "missing_ids": missing_ids[:100],
        "unexpected_ids": unexpected_ids[:100],
        "duplicate_output_ids": duplicate_output_ids[:100],
        "shard_mismatches": shard_mismatches[:100],
        "input_output_mismatches": input_output_mismatches[:100],
        "preflight_leaks": preflight_leaks[:100],
        "score_now_failed_retrieval": [
            {key: clean(row.get(key)) for key in ("Id", "Name", "Website", "InputFinalMCV", "InputMCVSource", "RetrievalStatus", "EstimatedMCV", "Evidence")}
            for row in score_now_failed_retrieval[:100]
        ],
        "score_now_failed_retrieval_without_trusted_input": [
            {key: clean(row.get(key)) for key in ("Id", "Name", "Website", "InputFinalMCV", "InputMCVSource", "RetrievalStatus", "EstimatedMCV", "Evidence")}
            for row in score_now_failed_without_trusted_input[:100]
        ],
        "score_now_suspicious_dispositions": [
            {key: clean(row.get(key)) for key in ("Id", "Name", "Website", "ICPDisposition", "ReviewAction", "EstimatedMCV", "Evidence")}
            for row in suspicious_score_now_dispositions[:100]
        ],
        "law_firm_score_now_not_legal": [
            {key: clean(row.get(key)) for key in ("Id", "Name", "Website", "LegalEntityRoute", "EstimatedMCV", "EstimatedARR", "Evidence")}
            for row in sorted(law_firm_score_now_not_legal, key=lambda item: as_int(item.get("EstimatedMCV")) or 0, reverse=True)[:100]
        ],
        "failed_known_quality_controls": failed_quality_controls,
    }
    issue_path.write_text(json.dumps(issue_samples, indent=2), encoding="utf-8")

    failed_checks = [name for name, passed in checks.items() if not passed]
    action_lines = "\n".join(f"- `{name}`: {count:,}" for name, count in audit["distributions"]["review_action"].items())
    retrieval_lines = "\n".join(f"- `{name}`: {count:,}" for name, count in audit["distributions"]["retrieval_status"].items())
    md = f"""# CRM Full Quality-Gated Scoring Audit

Generated: {audit['generated_at_utc']}

## Verdict

Structural audit: **{'PASS' if not failed_checks else 'FAIL'}**. Salesforce append readiness: **{'PASS' if all(checks.values()) and all(business_checks.values()) else 'FAIL'}**.

- Input/output rows: {len(input_rows):,} / {len(output_rows):,}
- Tasks completed: {len(score_files):,} / {args.expected_task_count:,}
- Unique output Account IDs: {len(set(output_ids)):,}
- Missing/unexpected/duplicate output IDs: {len(missing_ids):,} / {len(unexpected_ids):,} / {len(duplicate_output_ids):,}
- Score-now rows: {len(score_now_rows):,} ({pct(len(score_now_rows), len(output_rows)):.2f}%)
- Failed structural checks: {', '.join(failed_checks) if failed_checks else 'none'}

## Review Actions

{action_lines}

## Retrieval Status

{retrieval_lines}

## Writeback-Relevant Checks

- Score-now rows missing MCV/ARR/band/score values: {len(score_now_missing_values):,}
- Non-score rows carrying stray score values: {len(non_score_with_values):,}
- MCV points outside their low/high bands: {len(band_violations):,}
- Script errors: {audit['distributions']['retrieval_status'].get('script_error', 0):,}
- Rows that bypassed the ICP Quality preflight: {len(preflight_leaks):,}
- Input fields changed during scoring: {len(input_output_mismatches):,}

## Warnings

- Score-now rows with failed retrieval: {len(score_now_failed_retrieval):,}
- Of those, rows without a trusted rep/BDR/AE MCV input: {len(score_now_failed_without_trusted_input):,}
- Score-now rows with a suspicious ICP disposition: {len(suspicious_score_now_dispositions):,}
- Manual-review or insufficient-evidence rows: {warnings['manual_or_insufficient_rows']:,}
- CRM law-firm rows scored outside the legal lane: {len(law_firm_score_now_not_legal):,}
- Known Will-review controls that still reached `score_now`: {len(failed_quality_controls):,}

## Business-Readiness Blockers

- The scoring input omitted CRM `Company_Type__c`; {len(law_firm_score_now_not_legal):,} CRM law-firm rows reached `score_now` while the scorer classified them as `not_legal_entity` or `not_evaluated`.
- ALTA membership supports ICP identity but does not prove that `Account.Website` belongs to that entity. {len(failed_quality_controls):,} known wrong-site/entity controls still reached `score_now`.
- Known failed controls: {', '.join(item['name'] + ' -> ' + item['domain'] for item in failed_quality_controls) if failed_quality_controls else 'none'}.

## Recommendation

Do not write this score population to Salesforce yet. First run a post-retrieval ICP/entity-binding gate that carries CRM Company Type into legal routing, treats ALTA as entity/ICP evidence rather than website proof, and routes wrong-site, non-ICP, and hierarchy controls before value acceptance. Re-audit the resulting accepted subset, then build a dedicated Salesforce payload, take a live pre-write backup, canary it, and verify every written and cleared field after the bulk job.
"""
    audit_md_path.write_text(md, encoding="utf-8")
    print(json.dumps({"audit": str(audit_json_path), "combined": str(combined_path), "structural_pass": not failed_checks, "failed_checks": failed_checks}, indent=2))


if __name__ == "__main__":
    main()
