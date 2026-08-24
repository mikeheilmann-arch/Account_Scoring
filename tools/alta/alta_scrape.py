from __future__ import annotations

import argparse
import csv
import io
import re
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup
from rapidfuzz import fuzz, process


ALTA_URL = "https://www.alta.org/membership/directory-results?State=CA&SearchType=STATE"
ALTA_EXPECTED_MEMBER_COUNT = 10_582
ALTA_ROW_TOLERANCE = 0.05
DEFAULT_OUTPUT_DIR = Path("artifacts/alta_enrichment")
DEFAULT_SFDC_TARGET_ORG = "CertifID"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36"
)
SFDC_SOQL = (
    "SELECT Id, Name, BillingState, BillingCity, Type, Active_Customer__c, "
    "Account_Segment__c, Industry "
    "FROM Account "
    "WHERE Type = 'Prospect' OR Active_Customer__c = true"
)
PRIORITY_STATES = ["FL", "TX", "NY", "PA", "CA", "OH"]
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
STATE_NAME_BY_CODE = {
    "AL": "Alabama",
    "AK": "Alaska",
    "AZ": "Arizona",
    "AR": "Arkansas",
    "CA": "California",
    "CO": "Colorado",
    "CT": "Connecticut",
    "DE": "Delaware",
    "DC": "District of Columbia",
    "FL": "Florida",
    "GA": "Georgia",
    "HI": "Hawaii",
    "ID": "Idaho",
    "IL": "Illinois",
    "IN": "Indiana",
    "IA": "Iowa",
    "KS": "Kansas",
    "KY": "Kentucky",
    "LA": "Louisiana",
    "ME": "Maine",
    "MD": "Maryland",
    "MA": "Massachusetts",
    "MI": "Michigan",
    "MN": "Minnesota",
    "MS": "Mississippi",
    "MO": "Missouri",
    "MT": "Montana",
    "NE": "Nebraska",
    "NV": "Nevada",
    "NH": "New Hampshire",
    "NJ": "New Jersey",
    "NM": "New Mexico",
    "NY": "New York",
    "NC": "North Carolina",
    "ND": "North Dakota",
    "OH": "Ohio",
    "OK": "Oklahoma",
    "OR": "Oregon",
    "PA": "Pennsylvania",
    "RI": "Rhode Island",
    "SC": "South Carolina",
    "SD": "South Dakota",
    "TN": "Tennessee",
    "TX": "Texas",
    "UT": "Utah",
    "VT": "Vermont",
    "VA": "Virginia",
    "WA": "Washington",
    "WV": "West Virginia",
    "WI": "Wisconsin",
    "WY": "Wyoming",
    "AB": "Alberta",
    "BC": "British Columbia",
    "MB": "Manitoba",
    "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador",
    "NS": "Nova Scotia",
    "NT": "Northwest Territories",
    "NU": "Nunavut",
    "ON": "Ontario",
    "PE": "Prince Edward Island",
    "QC": "Quebec",
    "SK": "Saskatchewan",
    "YT": "Yukon",
    "PR": "Puerto Rico",
    "VI": "U.S. Virgin Islands",
}


@dataclass(frozen=True)
class MatchResult:
    account_id: str
    alta_id: str
    confidence: str
    method: str
    score: int
    candidate_count: int
    candidate_alta_ids: str
    needs_human_review: bool


def boolish(value: object) -> bool:
    return str(value or "").strip().lower() == "true"


def canonical_token(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


STATE_CODE_LOOKUP: dict[str, str] = {}
for code, name in STATE_NAME_BY_CODE.items():
    STATE_CODE_LOOKUP[canonical_token(code)] = code
    STATE_CODE_LOOKUP[canonical_token(name)] = code


def normalize_state(value: str) -> str:
    key = canonical_token(value)
    return STATE_CODE_LOOKUP.get(key, "")


def display_state(value: str) -> str:
    code = normalize_state(value)
    return code or (value or "").strip()


def normalize_city(value: str) -> str:
    text = (value or "").lower().replace("&", " and ")
    text = text.replace(".", "")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def normalize_name(value: str) -> str:
    text = (value or "").lower().replace("&", " and ")
    text = text.replace(".", "")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    tokens = [token for token in text.split() if token]
    while tokens and tokens[-1] in LEGAL_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def parse_location(value: str) -> tuple[str, str]:
    text = re.sub(r"\s+", " ", (value or "").strip())
    if not text:
        return "", ""
    if "," not in text:
        return text, ""
    city, state = text.rsplit(",", 1)
    return city.strip(), normalize_state(state.strip())


def csv_rows_from_text(text: str) -> list[dict[str, str]]:
    normalized = text.lstrip("\ufeff")
    return list(csv.DictReader(io.StringIO(normalized)))


def write_csv(path: Path, rows: Iterable[dict[str, object]], headers: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


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


def percent(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0.0%"
    return f"{(numerator / denominator) * 100:.1f}%"


def fetch_alta_html(url: str) -> str:
    response = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=120,
    )
    response.raise_for_status()
    return response.text


def parse_alta_html(html: str) -> tuple[list[dict[str, str]], list[dict[str, str]], dict[str, int]]:
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if table is None:
        raise RuntimeError("ALTA page did not contain a directory table.")

    members: list[dict[str, str]] = []
    elite_rows: list[dict[str, str]] = []
    stats = Counter()

    for row in table.find_all("tr"):
        cells = row.find_all("td")
        texts = [cell.get_text(" ", strip=True) for cell in cells]
        if not texts:
            continue

        if texts[:2] == ["Company Name", "Location"]:
            stats["elite_header_rows"] += 1
            continue
        if texts[:3] == ["ALTA ID", "Company", "Location"]:
            stats["member_header_rows"] += 1
            continue

        if len(texts) == 3 and texts[0] == "Elite Profile":
            city, state = parse_location(texts[2])
            elite_rows.append(
                {
                    "ALTA_ID": "",
                    "Company_Name": texts[1],
                    "City": city,
                    "State": state,
                }
            )
            stats["elite_rows"] += 1
            continue

        if len(texts) == 3 and texts[0].isdigit():
            city, state = parse_location(texts[2])
            member = {
                "ALTA_ID": texts[0],
                "Company_Name": texts[1],
                "City": city,
                "State": state,
            }
            member["_normalized_name"] = normalize_name(member["Company_Name"])
            member["_normalized_city"] = normalize_city(member["City"])
            members.append(member)
            stats["member_rows"] += 1
            continue

        stats["ignored_rows"] += 1

    delta = abs(len(members) - ALTA_EXPECTED_MEMBER_COUNT) / ALTA_EXPECTED_MEMBER_COUNT
    if delta > ALTA_ROW_TOLERANCE:
        raise RuntimeError(
            "ALTA member row count guard failed: "
            f"parsed {len(members):,} member rows, expected about {ALTA_EXPECTED_MEMBER_COUNT:,}."
        )

    return members, elite_rows, dict(stats)


def load_sfdc_accounts_from_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def query_sfdc_accounts(target_org: str) -> list[dict[str, str]]:
    quoted_soql = SFDC_SOQL.replace("'", "''")
    command = [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        f"sf data query --query '{quoted_soql}' --target-org {target_org} --result-format csv",
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
            "SFDC query failed.\n"
            f"stdout:\n{completed.stdout.strip()}\n\n"
            f"stderr:\n{completed.stderr.strip()}"
        )
    rows = csv_rows_from_text(completed.stdout)
    if not rows:
        raise RuntimeError("SFDC query returned zero rows.")
    return rows


def prepare_sfdc_accounts(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    accounts: list[dict[str, str]] = []
    for row in rows:
        account = dict(row)
        account["BillingStateCode"] = normalize_state(account.get("BillingState", ""))
        account["_normalized_name"] = normalize_name(account.get("Name", ""))
        account["_normalized_city"] = normalize_city(account.get("BillingCity", ""))
        account["_is_prospect"] = str((account.get("Type") or "").strip() == "Prospect").lower()
        account["_is_active_customer"] = str(boolish(account.get("Active_Customer__c"))).lower()
        accounts.append(account)
    return accounts


def build_member_indexes(
    members: list[dict[str, str]]
) -> tuple[
    dict[tuple[str, str], list[dict[str, str]]],
    dict[str, dict[str, list[dict[str, str]]]],
    dict[str, list[dict[str, str]]],
]:
    exact_index: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    names_by_state: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(lambda: defaultdict(list))
    all_names: dict[str, list[dict[str, str]]] = defaultdict(list)

    for member in members:
        state = member["State"]
        normalized_name = member["_normalized_name"]
        if not normalized_name:
            continue
        exact_index[(state, normalized_name)].append(member)
        if state:
            names_by_state[state][normalized_name].append(member)
        all_names[normalized_name].append(member)

    return dict(exact_index), dict(names_by_state), dict(all_names)


def choose_candidate(
    account: dict[str, str],
    candidates: list[dict[str, str]],
    confidence: str,
    method: str,
    score: int,
) -> MatchResult:
    original_candidates = list(candidates)
    account_city = account.get("_normalized_city", "")

    if account_city:
        city_matches = [
            candidate
            for candidate in candidates
            if candidate.get("_normalized_city") and candidate["_normalized_city"] == account_city
        ]
        if city_matches:
            candidates = city_matches

    account_name_raw = (account.get("Name") or "").strip().casefold()
    exact_raw_matches = [
        candidate
        for candidate in candidates
        if (candidate.get("Company_Name") or "").strip().casefold() == account_name_raw
    ]
    if exact_raw_matches:
        candidates = exact_raw_matches

    chosen = sorted(
        candidates,
        key=lambda candidate: (
            0
            if account_city
            and candidate.get("_normalized_city")
            and candidate["_normalized_city"] == account_city
            else 1,
            candidate["ALTA_ID"] or "9999999",
            candidate["Company_Name"],
            candidate["City"],
        ),
    )[0]
    candidate_ids = ";".join(sorted(candidate["ALTA_ID"] for candidate in original_candidates if candidate["ALTA_ID"]))
    needs_review = confidence == "low" or len(original_candidates) > 1
    return MatchResult(
        account_id=account["Id"],
        alta_id=chosen["ALTA_ID"],
        confidence=confidence,
        method=method,
        score=int(round(score)),
        candidate_count=len(original_candidates),
        candidate_alta_ids=candidate_ids,
        needs_human_review=needs_review,
    )


def allow_low_confidence_match(
    account: dict[str, str],
    member: dict[str, str],
    tentative_match: MatchResult,
) -> bool:
    if account.get("BillingStateCode"):
        return False

    account_city = account.get("_normalized_city", "")
    member_city = member.get("_normalized_city", "")
    if account_city and member_city and account_city == member_city:
        return True

    token_count = len((account.get("_normalized_name") or "").split())
    return tentative_match.candidate_count == 1 and token_count >= 3


def match_accounts(
    accounts: list[dict[str, str]],
    members: list[dict[str, str]],
) -> tuple[list[dict[str, object]], list[dict[str, object]], set[str]]:
    exact_index, names_by_state, all_names = build_member_indexes(members)
    state_choices = {state: list(name_map.keys()) for state, name_map in names_by_state.items()}
    all_choices = list(all_names.keys())
    member_by_id = {member["ALTA_ID"]: member for member in members}

    matches: list[dict[str, object]] = []
    enrichment_rows: list[dict[str, object]] = []
    matched_alta_ids: set[str] = set()

    for account in accounts:
        match: MatchResult | None = None
        state = account.get("BillingStateCode", "")
        normalized_name = account.get("_normalized_name", "")

        if normalized_name and state:
            exact_candidates = exact_index.get((state, normalized_name), [])
            if exact_candidates:
                match = choose_candidate(account, exact_candidates, "high", "exact_name_state", 100)

        if match is None and normalized_name and state and state_choices.get(state):
            result = process.extractOne(
                normalized_name,
                state_choices[state],
                scorer=fuzz.ratio,
                score_cutoff=90,
                processor=None,
            )
            if result:
                matched_name, score, _ = result
                match = choose_candidate(
                    account,
                    names_by_state[state][matched_name],
                    "medium",
                    "fuzzy_name_state",
                    int(score),
                )

        if match is None and normalized_name and all_choices:
            result = process.extractOne(
                normalized_name,
                all_choices,
                scorer=fuzz.ratio,
                score_cutoff=95,
                processor=None,
            )
            if result:
                matched_name, score, _ = result
                tentative_match = choose_candidate(
                    account,
                    all_names[matched_name],
                    "low",
                    "fuzzy_name_only",
                    int(score),
                )
                tentative_member = member_by_id[tentative_match.alta_id]
                if allow_low_confidence_match(account, tentative_member, tentative_match):
                    match = tentative_match

        if match is None:
            enrichment_rows.append(
                {
                    "Account_Id": account["Id"],
                    "ALTA_Member": "false",
                    "ALTA_ID": "",
                    "Match_Confidence": "no_match",
                }
            )
            continue

        member = member_by_id[match.alta_id]
        matches.append(
            {
                "Account_Id": account["Id"],
                "Account_Name": account.get("Name", ""),
                "BillingCity": account.get("BillingCity", ""),
                "BillingState": account.get("BillingState", ""),
                "Type": account.get("Type", ""),
                "Active_Customer__c": account.get("Active_Customer__c", ""),
                "Account_Segment__c": account.get("Account_Segment__c", ""),
                "Industry": account.get("Industry", ""),
                "ALTA_ID": member["ALTA_ID"],
                "ALTA_Company_Name": member["Company_Name"],
                "ALTA_City": member["City"],
                "ALTA_State": member["State"],
                "Match_Confidence": match.confidence,
                "Match_Method": match.method,
                "Match_Score": match.score,
                "Candidate_Count": match.candidate_count,
                "Candidate_ALTA_IDs": match.candidate_alta_ids,
                "Needs_Human_Review": str(match.needs_human_review).lower(),
            }
        )
        enrichment_rows.append(
            {
                "Account_Id": account["Id"],
                "ALTA_Member": "true",
                "ALTA_ID": member["ALTA_ID"],
                "Match_Confidence": match.confidence,
            }
        )
        matched_alta_ids.add(member["ALTA_ID"])

    return matches, enrichment_rows, matched_alta_ids


def build_summary_markdown(
    output_path: Path,
    members: list[dict[str, str]],
    elite_rows: list[dict[str, str]],
    accounts: list[dict[str, str]],
    matches: list[dict[str, object]],
    unmatched_members: list[dict[str, str]],
    matched_alta_ids: set[str],
) -> str:
    matches_by_account_id = {row["Account_Id"]: row for row in matches}
    confidence_counts = Counter(row["Match_Confidence"] for row in matches)
    confidence_counts["no_match"] = len(accounts) - len(matches)

    prospects = [account for account in accounts if account.get("_is_prospect") == "true"]
    active_customers = [account for account in accounts if account.get("_is_active_customer") == "true"]
    matched_prospects = sum(1 for account in prospects if account["Id"] in matches_by_account_id)
    matched_customers = sum(1 for account in active_customers if account["Id"] in matches_by_account_id)

    alta_counts_by_state = Counter(member["State"] or "(blank)" for member in members)
    matched_alta_counts_by_state = Counter(
        member["State"] or "(blank)"
        for member in members
        if member["ALTA_ID"] in matched_alta_ids
    )
    unmatched_by_state = Counter(member["State"] or "(blank)" for member in unmatched_members)

    prospect_counts_by_state = Counter(account.get("BillingStateCode", "") or "(blank)" for account in prospects)
    matched_prospect_counts_by_state = Counter(
        account.get("BillingStateCode", "") or "(blank)"
        for account in prospects
        if account["Id"] in matches_by_account_id
    )

    top_states = [state for state, _ in prospect_counts_by_state.most_common(15)]
    state_overlap_rows = []
    for state in top_states:
        prospect_count = prospect_counts_by_state[state]
        matched_prospect_count = matched_prospect_counts_by_state[state]
        alta_count = alta_counts_by_state[state]
        alta_matched_count = matched_alta_counts_by_state[state]
        state_overlap_rows.append(
            [
                state,
                STATE_NAME_BY_CODE.get(state, state),
                prospect_count,
                matched_prospect_count,
                percent(matched_prospect_count, prospect_count),
                alta_count,
                alta_matched_count,
                percent(alta_matched_count, alta_count),
            ]
        )

    unmatched_state_rows = [
        [state, STATE_NAME_BY_CODE.get(state, state), count]
        for state, count in sorted(
            unmatched_by_state.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ]

    priority_rank = {state: index for index, state in enumerate(PRIORITY_STATES)}
    priority_state_buckets: dict[str, list[dict[str, str]]] = {}
    for state in PRIORITY_STATES:
        priority_state_buckets[state] = sorted(
            [member for member in unmatched_members if member["State"] == state],
            key=lambda member: (member["Company_Name"].lower(), member["City"].lower(), member["ALTA_ID"]),
        )

    priority_unmatched_rows: list[dict[str, str]] = []
    bucket_index = {state: 0 for state in PRIORITY_STATES}
    while len(priority_unmatched_rows) < 20:
        appended = False
        for state in PRIORITY_STATES:
            index = bucket_index[state]
            bucket = priority_state_buckets[state]
            if index >= len(bucket):
                continue
            priority_unmatched_rows.append(bucket[index])
            bucket_index[state] += 1
            appended = True
            if len(priority_unmatched_rows) == 20:
                break
        if not appended:
            break

    multi_branch_clusters = Counter(
        member["_normalized_name"]
        for member in members
        if member.get("_normalized_name")
    )
    large_clusters = []
    for normalized_name, count in multi_branch_clusters.most_common():
        if count < 10:
            break
        sample_member = next(member for member in members if member["_normalized_name"] == normalized_name)
        state_count = len({member["State"] for member in members if member["_normalized_name"] == normalized_name})
        large_clusters.append((sample_member["Company_Name"], count, state_count))
        if len(large_clusters) == 5:
            break

    blank_state_accounts = sum(1 for account in accounts if not account.get("BillingStateCode"))
    low_confidence_matches = confidence_counts["low"]

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines = [
        "# ALTA Match Summary",
        "",
        f"Generated: {generated_at}",
        "",
        "## Headline Metrics",
        "",
        f"- ALTA member rows parsed: {len(members):,}",
        f"- Elite Provider promo rows excluded: {len(elite_rows):,}",
        f"- SFDC accounts queried: {len(accounts):,}",
        f"- SFDC accounts matched to ALTA: {len(matches):,} ({percent(len(matches), len(accounts))})",
        f"- ALTA members found in SFDC: {len(matched_alta_ids):,} ({percent(len(matched_alta_ids), len(members))})",
        f"- CertifID prospects that are ALTA members: {matched_prospects:,} / {len(prospects):,} ({percent(matched_prospects, len(prospects))})",
        f"- CertifID active customers that are ALTA members: {matched_customers:,} / {len(active_customers):,} ({percent(matched_customers, len(active_customers))})",
        f"- Net-new ALTA members not found in SFDC: {len(unmatched_members):,}",
        "",
        "## Cohort Overlap",
        "",
        markdown_table(
            ["Cohort", "Total Accounts", "Matched to ALTA", "Match Rate"],
            [
                ["Prospect", len(prospects), matched_prospects, percent(matched_prospects, len(prospects))],
                ["Active Customer", len(active_customers), matched_customers, percent(matched_customers, len(active_customers))],
                ["Combined Query Universe", len(accounts), len(matches), percent(len(matches), len(accounts))],
            ],
        ),
        "",
        "## Match Confidence",
        "",
        markdown_table(
            ["Confidence", "Accounts"],
            [
                ["high", confidence_counts["high"]],
                ["medium", confidence_counts["medium"]],
                ["low", confidence_counts["low"]],
                ["no_match", confidence_counts["no_match"]],
            ],
        ),
        "",
        "## State-By-State Overlap (Top 15 States By Prospect Count)",
        "",
        markdown_table(
            [
                "State",
                "State Name",
                "Prospects",
                "Prospects Matched",
                "Prospect Match %",
                "ALTA Members",
                "ALTA Members In SFDC",
                "ALTA Coverage %",
            ],
            state_overlap_rows,
        ),
        "",
        "## Net-New ALTA Members By State",
        "",
        markdown_table(
            ["State", "State Name", "Unmatched ALTA Members"],
            unmatched_state_rows,
        ),
        "",
        "## Top 20 Unmatched ALTA Members In Priority States",
        "",
        markdown_table(
            ["Priority State", "ALTA ID", "Company", "City", "State"],
            [
                [
                    member["State"],
                    member["ALTA_ID"],
                    member["Company_Name"],
                    member["City"],
                    STATE_NAME_BY_CODE.get(member["State"], member["State"]),
                ]
                for member in priority_unmatched_rows
            ],
        ),
        "",
        "## Data Quality Observations",
        "",
        f"- The live ALTA response contains {len(elite_rows):,} Elite Provider promo rows ahead of the member directory. They were excluded because they do not carry ALTA IDs and include non-member vendors such as CertifID.",
        f"- {blank_state_accounts:,} SFDC accounts in the query universe have a blank or unmapped `BillingState`, which forces them into the name-only fuzzy fallback.",
        f"- {low_confidence_matches:,} account matches landed in the `low` confidence bucket and should be spot-checked before any SFDC writeback.",
        "- The ALTA member directory is not title-agent-only. It also contains attorneys, banks, and service providers, so the unmatched export is best treated as an ALTA-member discovery list that still needs fit review.",
    ]

    if large_clusters:
        cluster_text = ", ".join(
            f"{name} ({count} rows across {state_count} states)"
            for name, count, state_count in large_clusters
        )
        lines.append(
            f"- The ALTA directory includes large branch-heavy name clusters that can create one-to-many ambiguity on enterprise account names: {cluster_text}."
        )

    markdown = "\n".join(lines) + "\n"
    output_path.write_text(markdown, encoding="utf-8")
    return markdown


def run(output_dir: Path, target_org: str, html_input: Path | None, sfdc_csv: Path | None) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    if html_input is not None:
        html = html_input.read_text(encoding="utf-8")
    else:
        html = fetch_alta_html(ALTA_URL)

    members, elite_rows, alta_stats = parse_alta_html(html)
    raw_member_rows = [
        {
            "ALTA_ID": member["ALTA_ID"],
            "Company_Name": member["Company_Name"],
            "City": member["City"],
            "State": member["State"],
        }
        for member in members
    ]
    write_csv(
        output_dir / "alta_members_raw.csv",
        raw_member_rows,
        ["ALTA_ID", "Company_Name", "City", "State"],
    )

    if sfdc_csv is not None:
        sfdc_rows = load_sfdc_accounts_from_csv(sfdc_csv)
    else:
        sfdc_rows = query_sfdc_accounts(target_org)

    accounts = prepare_sfdc_accounts(sfdc_rows)
    matches, enrichment_rows, matched_alta_ids = match_accounts(accounts, members)

    sorted_matches = sorted(
        matches,
        key=lambda row: (
            {"high": 0, "medium": 1, "low": 2}.get(str(row["Match_Confidence"]), 9),
            str(row["Account_Name"]).lower(),
            str(row["Account_Id"]),
        ),
    )
    write_csv(
        output_dir / "alta_sfdc_matches.csv",
        sorted_matches,
        [
            "Account_Id",
            "Account_Name",
            "BillingCity",
            "BillingState",
            "Type",
            "Active_Customer__c",
            "Account_Segment__c",
            "Industry",
            "ALTA_ID",
            "ALTA_Company_Name",
            "ALTA_City",
            "ALTA_State",
            "Match_Confidence",
            "Match_Method",
            "Match_Score",
            "Candidate_Count",
            "Candidate_ALTA_IDs",
            "Needs_Human_Review",
        ],
    )

    unmatched_members = sorted(
        [
            {
                "ALTA_ID": member["ALTA_ID"],
                "Company_Name": member["Company_Name"],
                "City": member["City"],
                "State": member["State"],
            }
            for member in members
            if member["ALTA_ID"] not in matched_alta_ids
        ],
        key=lambda row: (row["State"], row["Company_Name"].lower(), row["ALTA_ID"]),
    )
    write_csv(
        output_dir / "alta_unmatched_members.csv",
        unmatched_members,
        ["ALTA_ID", "Company_Name", "City", "State"],
    )

    write_csv(
        output_dir / "sfdc_alta_enrichment.csv",
        enrichment_rows,
        ["Account_Id", "ALTA_Member", "ALTA_ID", "Match_Confidence"],
    )

    build_summary_markdown(
        output_dir / "alta_match_summary.md",
        members,
        elite_rows,
        accounts,
        matches,
        unmatched_members,
        matched_alta_ids,
    )

    print(f"ALTA member rows parsed: {len(members):,}")
    print(f"Elite Provider rows excluded: {len(elite_rows):,}")
    print(f"SFDC accounts queried: {len(accounts):,}")
    print(f"SFDC accounts matched: {len(matches):,}")
    print(f"ALTA members matched: {len(matched_alta_ids):,}")
    print(f"Unmatched ALTA members: {len(unmatched_members):,}")
    print(f"Output directory: {output_dir}")
    print(
        "ALTA parsing stats: "
        f"member_rows={alta_stats.get('member_rows', 0):,}, "
        f"elite_rows={alta_stats.get('elite_rows', 0):,}, "
        f"ignored_rows={alta_stats.get('ignored_rows', 0):,}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scrape the ALTA member directory and cross-match it to CertifID SFDC accounts."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for generated CSV and Markdown artifacts. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--target-org",
        default=DEFAULT_SFDC_TARGET_ORG,
        help=f"Salesforce target org alias. Default: {DEFAULT_SFDC_TARGET_ORG}",
    )
    parser.add_argument(
        "--html-input",
        type=Path,
        help="Use a local ALTA HTML file instead of fetching the live directory.",
    )
    parser.add_argument(
        "--sfdc-csv",
        type=Path,
        help="Use a local SFDC account CSV instead of running the live SFDC query.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        run(
            output_dir=args.output_dir,
            target_org=args.target_org,
            html_input=args.html_input,
            sfdc_csv=args.sfdc_csv,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
