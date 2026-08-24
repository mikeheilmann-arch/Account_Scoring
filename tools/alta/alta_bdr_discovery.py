from __future__ import annotations

import argparse
import csv
import json
import random
import re
import shutil
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz


DEFAULT_UNMATCHED_CSV = Path("artifacts/alta_enrichment/alta_unmatched_members.csv")
DEFAULT_CA_MD = Path("artifacts/alta_enrichment/ca_gap_investigation.md")
DEFAULT_BDR_CSV = Path("artifacts/alta_enrichment/alta_bdr_discovery_list.csv")
DEFAULT_BDR_MD = Path("artifacts/alta_enrichment/alta_bdr_discovery_summary.md")
DEFAULT_TARGET_ORG = "CertifID"
CA_SAMPLE_SIZE = 30
CA_SAMPLE_SEED = 20260423
CA_FAIL_THRESHOLD = 5
CA_REAL_THRESHOLD = 21
WRONG_RECORD_TYPE_THRESHOLD = 3
STATE_TARGETS = ["FL", "TX", "NY", "CA", "PA", "GA"]
GOOD_TYPES = {"Prospect", "Customer", ""}
LEGAL_SUFFIXES = {
    "llc",
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "co",
    "company",
    "ltd",
    "limited",
    "lp",
    "llp",
    "pllc",
    "pa",
    "pc",
}
QUERY_STOPWORDS = {
    "title",
    "escrow",
    "closing",
    "settlement",
    "services",
    "service",
    "company",
    "co",
    "inc",
    "llc",
    "llp",
    "pllc",
    "pc",
    "corp",
    "corporation",
    "agency",
    "group",
    "solutions",
    "solution",
    "law",
    "attorney",
    "attorneys",
    "bank",
    "mortgage",
    "lending",
    "credit",
    "union",
    "land",
    "california",
    "member",
    "members",
    "honorary",
    "national",
}
STATE_NAME_BY_CODE = {
    "AK": "Alaska",
    "AL": "Alabama",
    "AR": "Arkansas",
    "AZ": "Arizona",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DC": "District of Columbia",
    "DE": "Delaware",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "IA": "Iowa",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "MA": "Massachusetts",
    "MD": "Maryland",
    "ME": "Maine",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MO": "Missouri",
    "MS": "Mississippi",
    "MT": "Montana",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "NE": "Nebraska",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NV": "Nevada",
    "NY": "New York",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "PR": "Puerto Rico",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VA": "Virginia",
    "VI": "U.S. Virgin Islands",
    "VT": "Vermont",
    "WA": "Washington",
    "WI": "Wisconsin",
    "WV": "West Virginia",
    "WY": "Wyoming",
}


def resolve_sf_executable() -> str:
    candidates = [
        shutil.which("sf"),
        shutil.which("sf.cmd"),
        r"C:\Program Files\sf\bin\sf.cmd",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    raise RuntimeError("Could not locate the Salesforce CLI executable.")


@dataclass
class SampleAssessment:
    alta_id: str
    company_name: str
    city: str
    state: str
    query_fragment: str
    fallback_fragment: str
    result_count: int
    category: str
    reason: str
    best_match_id: str
    best_match_name: str
    best_match_city: str
    best_match_state: str
    best_match_type: str
    best_match_industry: str
    best_match_segment: str


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, object]], headers: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def markdown_escape(value: object) -> str:
    return str(value).replace("|", "\\|")


def markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    if not rows:
        return "_No rows._"
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(markdown_escape(cell) for cell in row) + " |")
    return "\n".join(lines)


def base_company_name(name: str) -> str:
    text = re.sub(r"\s+", " ", (name or "").strip())
    if " - " in text:
        text = text.split(" - ", 1)[0].strip()
    return text.strip(" ,")


def normalize_name(name: str) -> str:
    base = base_company_name(name).lower().replace("&", " and ")
    tokens = re.findall(r"[a-z0-9]+", base)
    while tokens and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def choose_query_fragments(name: str) -> tuple[str, str]:
    base = base_company_name(name)
    tokens = re.findall(r"[A-Za-z0-9']+", base)
    cleaned = [token.strip("'") for token in tokens if token.strip("'")]
    if not cleaned:
        return base[:40], ""

    if len(cleaned) <= 4 and len(base) <= 45:
        primary = base
    else:
        primary = " ".join(cleaned[:3])

    distinctive = [
        token
        for token in cleaned
        if token.lower() not in QUERY_STOPWORDS and (len(token) >= 4 or any(ch.isdigit() for ch in token))
    ]
    if distinctive:
        fallback = distinctive[0]
    elif len(cleaned) >= 2:
        fallback = " ".join(cleaned[:2])
    else:
        fallback = cleaned[0]

    if primary.lower() == fallback.lower():
        fallback = ""
    return primary[:60], fallback[:60]


def run_sfdc_query(fragment: str, target_org: str) -> list[dict[str, Any]]:
    escaped_fragment = fragment.replace("'", "''")
    soql = (
        "SELECT Id, Name, BillingCity, BillingState, Type, Industry, Account_Segment__c "
        f"FROM Account WHERE Name LIKE '%{escaped_fragment}%' ORDER BY Name"
    )
    command = [
        resolve_sf_executable(),
        "data",
        "query",
        "--query",
        soql,
        "--target-org",
        target_org,
        "--result-format",
        "json",
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"SFDC query failed for fragment {fragment!r}.\n"
            f"stdout:\n{completed.stdout.strip()}\n\nstderr:\n{completed.stderr.strip()}"
        )
    payload = json.loads(completed.stdout)
    return payload.get("result", {}).get("records", [])


def score_record(sample: dict[str, str], record: dict[str, Any]) -> dict[str, Any]:
    sample_name = normalize_name(sample["Company_Name"])
    record_name = normalize_name(str(record.get("Name") or ""))
    similarity = fuzz.token_sort_ratio(sample_name, record_name)
    contains = sample_name in record_name or record_name in sample_name
    sample_city = re.sub(r"[^a-z0-9]+", " ", (sample.get("City") or "").lower()).strip()
    record_city = re.sub(r"[^a-z0-9]+", " ", str(record.get("BillingCity") or "").lower()).strip()
    city_match = bool(sample_city) and sample_city == record_city
    billing_state = (record.get("BillingState") or "").strip()
    state_match = billing_state in {"CA", "California"}
    type_value = (record.get("Type") or "").strip()
    type_good = type_value in GOOD_TYPES
    industry = (record.get("Industry") or "").strip().lower()
    industry_flag = any(marker in industry for marker in ("software", "internet", "media"))

    score = similarity
    if contains:
        score += 12
    if city_match:
        score += 15
    if state_match:
        score += 10
    if type_good:
        score += 4
    return {
        "record": record,
        "similarity": similarity,
        "contains": contains,
        "city_match": city_match,
        "state_match": state_match,
        "type_good": type_good,
        "industry_flag": industry_flag,
        "score": score,
    }


def assess_sample(sample: dict[str, str], records: list[dict[str, Any]], fragment_used: str, fallback: str) -> SampleAssessment:
    scored = sorted((score_record(sample, record) for record in records), key=lambda item: item["score"], reverse=True)
    plausible = [item for item in scored if item["similarity"] >= 84 or item["contains"]]

    if not plausible:
        return SampleAssessment(
            alta_id=sample["ALTA_ID"],
            company_name=sample["Company_Name"],
            city=sample["City"],
            state=sample["State"],
            query_fragment=fragment_used,
            fallback_fragment=fallback,
            result_count=len(records),
            category="not_in_sfdc",
            reason="No plausible same-brand SFDC account returned from the live name search.",
            best_match_id="",
            best_match_name="",
            best_match_city="",
            best_match_state="",
            best_match_type="",
            best_match_industry="",
            best_match_segment="",
        )

    best = plausible[0]["record"]
    best_state = (best.get("BillingState") or "").strip()
    best_type = (best.get("Type") or "").strip()
    best_industry = (best.get("Industry") or "").strip()

    category = "ambiguous"
    reason = "Live search returned plausible same-brand accounts, but the evidence is not clean enough to call it a clear miss."
    good_ca_matches = [
        item
        for item in plausible
        if item["state_match"] and item["type_good"] and (item["similarity"] >= 90 or item["contains"])
    ]
    good_brand_matches = [
        item for item in plausible if item["type_good"] and (item["similarity"] >= 92 or item["contains"])
    ]
    bad_brand_matches = [
        item for item in plausible if not item["type_good"] and (item["similarity"] >= 96 or item["contains"])
    ]

    if good_ca_matches:
        category = "legitimate_match_missed"
        reason = "A same-brand California SFDC account exists and should have been caught by the ALTA matching pass."
    elif good_brand_matches:
        category = "legitimate_match_missed"
        reason = "A same-brand SFDC account exists, but the current ALTA match pass missed the branch or corporate roll-up variant."
    elif bad_brand_matches:
        category = "wrong_record_type"
        reason = "A same-brand SFDC account exists, but the closest match is stored under a non-selling record type."
    elif any((item["similarity"] >= 90 or item["contains"]) and not item["type_good"] for item in plausible):
        category = "wrong_record_type"
        reason = "A same-brand SFDC account exists, but it is stored under a non-selling record type."
    elif any(item["industry_flag"] and (item["similarity"] >= 88 or item["contains"]) for item in plausible):
        category = "wrong_record_type"
        reason = "A same-brand SFDC account exists, but it is coded like a vendor/media/software record rather than a target selling record."

    return SampleAssessment(
        alta_id=sample["ALTA_ID"],
        company_name=sample["Company_Name"],
        city=sample["City"],
        state=sample["State"],
        query_fragment=fragment_used,
        fallback_fragment=fallback,
        result_count=len(records),
        category=category,
        reason=reason,
        best_match_id=str(best.get("Id") or ""),
        best_match_name=str(best.get("Name") or ""),
        best_match_city=str(best.get("BillingCity") or ""),
        best_match_state=best_state,
        best_match_type=best_type,
        best_match_industry=best_industry,
        best_match_segment=str(best.get("Account_Segment__c") or ""),
    )


def run_ca_spotcheck(unmatched_rows: list[dict[str, str]], target_org: str) -> list[SampleAssessment]:
    ca_rows = [row for row in unmatched_rows if (row.get("State") or "").strip() == "CA"]
    sample = random.Random(CA_SAMPLE_SEED).sample(ca_rows, CA_SAMPLE_SIZE)
    assessments: list[SampleAssessment] = []

    for row in sample:
        primary, fallback = choose_query_fragments(row["Company_Name"])
        records = run_sfdc_query(primary, target_org)
        fragment_used = primary
        if not records and fallback:
            records = run_sfdc_query(fallback, target_org)
            fragment_used = fallback
        assessments.append(assess_sample(row, records, fragment_used, fallback))

    assessments.sort(key=lambda item: (item.company_name.lower(), item.city.lower(), item.alta_id))
    return assessments


def write_ca_markdown(path: Path, assessments: list[SampleAssessment]) -> tuple[Counter[str], bool]:
    counts: Counter[str] = Counter(item.category for item in assessments)
    ca_passes = counts["legitimate_match_missed"] <= CA_FAIL_THRESHOLD

    if counts["legitimate_match_missed"] > CA_FAIL_THRESHOLD:
        decision = (
            "CA gap does not pass the trust check. The sample shows enough clear missed matches that the CA unmatched "
            "list should not be handed to BDRs as-is."
        )
    elif counts["not_in_sfdc"] >= CA_REAL_THRESHOLD:
        decision = (
            "CA gap looks real. The sample is overwhelmingly net-new, so the CA unmatched list is trustworthy enough "
            "for BDR use."
        )
    else:
        decision = (
            "CA gap is mixed. The sample does not show enough clean missed matches to fail outright, but it is not "
            "overwhelmingly net-new either."
        )

    wrong_type_note = (
        "Wrong-record-type findings crossed the 10% threshold and should be treated as a separate SFDC hygiene issue."
        if counts["wrong_record_type"] >= WRONG_RECORD_TYPE_THRESHOLD
        else "Wrong-record-type findings stayed below the 10% threshold."
    )

    rows = [
        [
            item.alta_id,
            item.company_name,
            item.city,
            item.query_fragment,
            item.result_count,
            item.category,
            item.best_match_name or "(none)",
            item.best_match_city or "",
            item.best_match_state or "",
            item.best_match_type or "",
            item.reason,
        ]
        for item in assessments
    ]

    markdown = "\n".join(
        [
            "# California Gap Investigation",
            "",
            f"Random sample size: {CA_SAMPLE_SIZE}",
            f"Random seed: {CA_SAMPLE_SEED}",
            "",
            "## Sample Composition",
            "",
            markdown_table(
                [
                    "ALTA ID",
                    "ALTA Company",
                    "CA City",
                    "Search Fragment",
                    "SFDC Result Count",
                    "Category",
                    "Best SFDC Match",
                    "Best Match City",
                    "Best Match State",
                    "Best Match Type",
                    "Reason",
                ],
                rows,
            ),
            "",
            "## Summary Counts",
            "",
            markdown_table(
                ["Category", "Count"],
                [
                    ["legitimate_match_missed", counts["legitimate_match_missed"]],
                    ["not_in_sfdc", counts["not_in_sfdc"]],
                    ["wrong_record_type", counts["wrong_record_type"]],
                    ["ambiguous", counts["ambiguous"]],
                ],
            ),
            "",
            "## Decision",
            "",
            decision,
            "",
            wrong_type_note,
            "",
            (
                "CA status: `fail` for BDR inclusion. California should be excluded from the BDR discovery export until "
                "the ALTA matching logic gets a California-focused second pass."
                if not ca_passes
                else "CA status: `pass` for BDR inclusion."
            ),
            "",
        ]
    )
    path.write_text(markdown, encoding="utf-8")
    return counts, ca_passes


def classify_icp(name: str) -> tuple[str, str, str]:
    lower_name = (name or "").lower()

    title_tokens = [token for token in ("title", "escrow", "closing", "settlement", "abstract") if token in lower_name]
    law_tokens = [
        token
        for token in ("llp", "p.c.", " p.c ", " attorney", "attorneys", " law", "& associates", " law group")
        if token in f" {lower_name} "
    ]
    insurer_tokens = [token for token in ("insurance", "underwriter", "guaranty") if token in lower_name]
    bank_tokens = [
        token
        for token in ("bank", "mortgage", "lending", "lender", "credit union")
        if token in lower_name
    ]
    vendor_tokens = [
        token
        for token in (
            "solutions",
            "technology",
            "software",
            "consulting",
            "services",
            "technical",
            "association",
            "member",
            "warranty",
            "brief",
            "tour",
        )
        if token in lower_name
    ]

    if title_tokens and not law_tokens and not bank_tokens and not insurer_tokens:
        return "likely_title_agency", "High", ", ".join(sorted(set(title_tokens)))
    if law_tokens and not title_tokens:
        return "likely_law_firm", "Medium", ", ".join(sorted(set(token.strip() for token in law_tokens)))
    if insurer_tokens:
        return "likely_underwriter_or_insurer", "Skip", ", ".join(sorted(set(insurer_tokens)))
    if bank_tokens:
        return "likely_bank_or_lender", "Skip", ", ".join(sorted(set(bank_tokens)))
    if vendor_tokens and not title_tokens:
        return "likely_service_vendor", "Review", ", ".join(sorted(set(vendor_tokens)))
    return "unclear", "Review", ""


def build_brand_key(name: str) -> str:
    normalized = normalize_name(name)
    return normalized or base_company_name(name).lower()


def build_bdr_rows(unmatched_rows: list[dict[str, str]], include_ca: bool) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for row in unmatched_rows:
        state = (row.get("State") or "").strip()
        if not include_ca and state == "CA":
            continue
        category, priority, tokens = classify_icp(row["Company_Name"])
        rows.append(
            {
                "ALTA_ID": row["ALTA_ID"],
                "Company_Name": row["Company_Name"],
                "City": row["City"],
                "State": state,
                "ICP_Category": category,
                "BDR_Priority": priority,
                "Name_Tokens": tokens,
                "_Brand_Key": build_brand_key(row["Company_Name"]),
            }
        )
    return rows


def write_bdr_csv(path: Path, rows: list[dict[str, object]]) -> None:
    public_rows = [
        {
            "ALTA_ID": row["ALTA_ID"],
            "Company_Name": row["Company_Name"],
            "City": row["City"],
            "State": row["State"],
            "ICP_Category": row["ICP_Category"],
            "BDR_Priority": row["BDR_Priority"],
            "Name_Tokens": row["Name_Tokens"],
        }
        for row in rows
    ]
    write_csv(
        path,
        public_rows,
        ["ALTA_ID", "Company_Name", "City", "State", "ICP_Category", "BDR_Priority", "Name_Tokens"],
    )


def write_bdr_summary(path: Path, rows: list[dict[str, object]], include_ca: bool, ca_counts: Counter[str]) -> None:
    category_counts = Counter(row["ICP_Category"] for row in rows)
    priority_counts = Counter(row["BDR_Priority"] for row in rows)
    high_rows = [row for row in rows if row["BDR_Priority"] == "High"]
    high_state_counts = Counter(row["State"] for row in high_rows)

    cluster_buckets: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        cluster_buckets[str(row["_Brand_Key"])].append(row)

    cluster_notes = []
    for brand_key, members in sorted(cluster_buckets.items(), key=lambda item: (-len(item[1]), item[0])):
        if len(members) < 5:
            break
        representative = sorted(members, key=lambda item: (str(item["Company_Name"]).lower(), str(item["City"]).lower()))[0]
        states = sorted({str(item["State"]) or "(blank)" for item in members})
        cluster_notes.append(
            f"{representative['Company_Name']} ({len(members)} rows across {len(states)} states: {', '.join(states[:8])})"
        )
        if len(cluster_notes) == 10:
            break

    top_state_sections: list[str] = []
    summary_states = ["FL", "TX", "NY", "PA", "GA"]
    if include_ca:
        summary_states.insert(3, "CA")

    for state in summary_states:
        state_high_rows = [row for row in high_rows if row["State"] == state]
        brand_groups: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in state_high_rows:
            brand_groups[str(row["_Brand_Key"])].append(row)
        ranked = sorted(
            brand_groups.values(),
            key=lambda members: (-len(members), str(members[0]["Company_Name"]).lower(), str(members[0]["City"]).lower()),
        )[:20]
        table_rows = []
        for members in ranked:
            representative = sorted(members, key=lambda item: (str(item["Company_Name"]).lower(), str(item["City"]).lower(), str(item["ALTA_ID"])))[0]
            table_rows.append(
                [
                    representative["Company_Name"],
                    representative["City"],
                    representative["ALTA_ID"],
                    len(members),
                    representative["ICP_Category"],
                ]
            )
        top_state_sections.extend(
            [
                f"### {STATE_NAME_BY_CODE.get(state, state)}",
                "",
                markdown_table(
                    ["Company", "City", "Representative ALTA ID", "Branch Count In State", "ICP Category"],
                    table_rows,
                ),
                "",
            ]
        )

    summary_lines = [
        "# ALTA BDR Discovery Summary",
        "",
        f"Final export rows: {len(rows):,}",
        f"High-priority rows: {priority_counts['High']:,}",
        f"California included: {'yes' if include_ca else 'no'}",
        "",
        (
            "California was excluded because the CA spot-check exceeded the legitimate-match-missed threshold, so the "
            "CA unmatched list is not trustworthy enough for direct BDR use."
            if not include_ca
            else "California passed the CA spot-check and remains in the final BDR export."
        ),
        "",
        "## Counts By ICP Category",
        "",
        markdown_table(
            ["ICP Category", "Count"],
            [[category, count] for category, count in sorted(category_counts.items(), key=lambda item: (-item[1], item[0]))],
        ),
        "",
        "## Counts By BDR Priority",
        "",
        markdown_table(
            ["BDR Priority", "Count"],
            [[priority, count] for priority, count in sorted(priority_counts.items(), key=lambda item: (-item[1], item[0]))],
        ),
        "",
        "## High-Priority State Breakdown",
        "",
        markdown_table(
            ["State", "State Name", "High-Priority Rows"],
            [
                [state or "(blank)", STATE_NAME_BY_CODE.get(state, state or "(blank)"), count]
                for state, count in sorted(high_state_counts.items(), key=lambda item: (-item[1], item[0]))
            ],
        ),
        "",
        "## Top High-Priority Accounts By State",
        "",
    ]
    summary_lines.extend(top_state_sections)
    summary_lines.extend(
        [
            "## Branch Duplicate Notes",
            "",
            *([f"- {note}" for note in cluster_notes] or ["- No large branch duplicate clusters (5+ rows) were present in the final export."]),
            "",
            "## CA Spot-Check Reference",
            "",
            markdown_table(
                ["Category", "Count"],
                [
                    ["legitimate_match_missed", ca_counts["legitimate_match_missed"]],
                    ["not_in_sfdc", ca_counts["not_in_sfdc"]],
                    ["wrong_record_type", ca_counts["wrong_record_type"]],
                    ["ambiguous", ca_counts["ambiguous"]],
                ],
            ),
            "",
        ]
    )
    path.write_text("\n".join(summary_lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the ALTA BDR discovery outputs.")
    parser.add_argument("--unmatched-csv", type=Path, default=DEFAULT_UNMATCHED_CSV)
    parser.add_argument("--ca-output", type=Path, default=DEFAULT_CA_MD)
    parser.add_argument("--bdr-csv-output", type=Path, default=DEFAULT_BDR_CSV)
    parser.add_argument("--bdr-md-output", type=Path, default=DEFAULT_BDR_MD)
    parser.add_argument("--target-org", default=DEFAULT_TARGET_ORG)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        unmatched_rows = read_csv_rows(args.unmatched_csv)
        assessments = run_ca_spotcheck(unmatched_rows, args.target_org)
        ca_counts, ca_passes = write_ca_markdown(args.ca_output, assessments)
        bdr_rows = build_bdr_rows(unmatched_rows, include_ca=ca_passes)
        write_bdr_csv(args.bdr_csv_output, bdr_rows)
        write_bdr_summary(args.bdr_md_output, bdr_rows, include_ca=ca_passes, ca_counts=ca_counts)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    high_priority_rows = [row for row in bdr_rows if row["BDR_Priority"] == "High"]
    print(f"CA pass: {str(ca_passes).lower()}")
    print(
        "CA sample counts: "
        f"missed={ca_counts['legitimate_match_missed']}, "
        f"net_new={ca_counts['not_in_sfdc']}, "
        f"wrong_type={ca_counts['wrong_record_type']}, "
        f"ambiguous={ca_counts['ambiguous']}"
    )
    print(f"Final BDR export rows: {len(bdr_rows):,}")
    print(f"High-priority rows: {len(high_priority_rows):,}")
    print(
        "Top high-priority states: "
        + ", ".join(
            f"{state}:{count}"
            for state, count in sorted(Counter(row["State"] for row in high_priority_rows).items(), key=lambda item: (-item[1], item[0]))[:10]
        )
    )
    print(f"Outputs written to: {args.ca_output}, {args.bdr_csv_output}, {args.bdr_md_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
