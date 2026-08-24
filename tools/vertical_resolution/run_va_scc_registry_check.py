#!/usr/bin/env python3
"""Run a no-write Virginia SCC agency registry check for Account candidates.

This script queries the Virginia SCC Bureau of Insurance consumer inquiry
Agency search for active Settlement Agent and Title agency licenses. It writes
review artifacts only; it does not write to Salesforce.
"""

from __future__ import annotations

import argparse
import csv
import html
import http.cookiejar
import re
import time
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


SCC_SEARCH_URL = "https://www.scc.virginia.gov/boi/consumerinquiry/search.aspx?searchType=agency"

OUTPUT_COLUMNS = [
    "Id",
    "Name",
    "Bucket",
    "ResolverOutcome",
    "CandidateWebsite",
    "SCCRegistryStatus",
    "SCCMatchType",
    "SCCMatchedName",
    "SCCMatchedStatus",
    "SCCMatchedCity",
    "SCCMatchedState",
    "SCCMatchedZip",
    "SCCQueryUsed",
    "SCCLicenseTypeSearched",
    "SCCResultCount",
    "SCCAsOfDate",
    "SCCEvidence",
    "SCCSourceUrl",
]

COMMON_ENTITY_TOKENS = {
    "a",
    "and",
    "at",
    "co",
    "company",
    "corp",
    "corporation",
    "dba",
    "escrow",
    "firm",
    "for",
    "group",
    "inc",
    "insurance",
    "law",
    "llc",
    "llp",
    "lp",
    "ltd",
    "of",
    "pc",
    "plc",
    "pllc",
    "service",
    "services",
    "settlement",
    "the",
    "title",
    "to",
}

LEGAL_SUFFIX_RE = re.compile(
    r"\b(inc|incorporated|llc|l\.?l\.?c\.?|llp|l\.?l\.?p\.?|plc|p\.?l\.?c\.?|pllc|p\.?l\.?l\.?c\.?|pc|p\.?c\.?|corp|corporation|ltd|limited)\b",
    re.I,
)


@dataclass
class SccRow:
    name: str
    status: str
    city: str
    state: str
    zip_code: str


class AgencyTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_target = False
        self.in_row = False
        self.in_cell = False
        self.current_cell: list[str] = []
        self.current_row: list[str] = []
        self.rows: list[list[str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag == "table" and attrs_dict.get("id") == "ctl00_MainContent_gvAgyResults":
            self.in_target = True
        elif self.in_target and tag == "tr":
            self.in_row = True
            self.current_row = []
        elif self.in_target and self.in_row and tag in {"td", "th"}:
            self.in_cell = True
            self.current_cell = []

    def handle_endtag(self, tag: str) -> None:
        if self.in_target and self.in_cell and tag in {"td", "th"}:
            text = normalize_space("".join(self.current_cell))
            self.current_row.append(text)
            self.in_cell = False
        elif self.in_target and self.in_row and tag == "tr":
            if self.current_row:
                self.rows.append(self.current_row)
            self.in_row = False
        elif self.in_target and tag == "table":
            self.in_target = False

    def handle_data(self, data: str) -> None:
        if self.in_target and self.in_cell:
            self.current_cell.append(data)


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def slug(value: str, limit: int = 80) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()[:limit] or "row"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def clean_name_for_query(value: str) -> str:
    value = re.sub(r"\bd/?b/?a\b.*", "", value, flags=re.I)
    value = LEGAL_SUFFIX_RE.sub(" ", value)
    value = value.replace("&", " and ")
    value = re.sub(r"[^A-Za-z0-9 ]+", " ", value)
    return normalize_space(value)[:50]


def distinctive_tokens(value: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", value.lower())
    return [token for token in tokens if token not in COMMON_ENTITY_TOKENS and len(token) > 1]


def host_slug(value: str) -> str:
    if not value:
        return ""
    url = value if re.match(r"^https?://", value, re.I) else f"https://{value}"
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    first_label = host.split(".")[0]
    return re.sub(r"[^a-z0-9]+", "", first_label)


def domain_variants(value: str) -> list[str]:
    label = host_slug(value)
    if not label:
        return []
    variants = [label]
    if label.endswith("title") and len(label) > len("title"):
        prefix = label[: -len("title")]
        if 1 < len(prefix) <= 4:
            variants.append(f"{prefix.upper()} Title")
            variants.append(" ".join(prefix.upper()) + " Title")
    return variants


def initial_variants(name: str) -> list[str]:
    variants: list[str] = []
    match = re.search(r"\b([A-Za-z])\s*(?:and|&)\s*([A-Za-z])\b", name)
    if match and "title" in name.lower():
        initials = f"{match.group(1)}{match.group(2)}".upper()
        variants.append(f"{initials} Title")
        variants.append(f"{match.group(1).upper()} {match.group(2).upper()} Title")
    return variants


def query_variants(row: dict[str, str]) -> list[str]:
    name = normalize_space(row.get("Name", ""))
    cleaned = clean_name_for_query(name)
    variants = [cleaned]
    tokens = distinctive_tokens(cleaned)
    variants.extend(initial_variants(name))
    variants.extend(domain_variants(row.get("CandidateWebsite", "")))
    variants.extend(domain_variants(row.get("CurrentWebsite", "")))
    if "title" in cleaned.lower() and tokens:
        variants.append(f"{tokens[0]} Title")
    if len(tokens) >= 2:
        variants.append(" ".join(tokens[:2]))
    elif tokens and len(tokens[0]) >= 3:
        variants.append(tokens[0])
    if "law" in cleaned.lower() and tokens:
        variants.append(f"{tokens[0]} Law")
    deduped: list[str] = []
    for variant in variants:
        variant = normalize_space(re.sub(r"[^A-Za-z0-9 ]+", " ", variant))
        if len(variant) >= 3 and variant.lower() not in {item.lower() for item in deduped}:
            deduped.append(variant[:50])
    return deduped


def hidden_value(page: str, field_id: str) -> str:
    pattern = rf'<input[^>]+id="{re.escape(field_id)}"[^>]+value="([^"]*)"'
    match = re.search(pattern, page, re.S | re.I)
    return html.unescape(match.group(1)) if match else ""


def as_of_date(page: str) -> str:
    match = re.search(r"current as of\s*<span[^>]*>([^<]+)</span>", page, re.I)
    return normalize_space(match.group(1)) if match else ""


def parse_agency_rows(page: str) -> list[SccRow]:
    parser = AgencyTableParser()
    parser.feed(page)
    output: list[SccRow] = []
    for row in parser.rows:
        if len(row) < 6 or row[1].lower().startswith("name"):
            continue
        if not any(cell and cell != "\xa0" for cell in row):
            continue
        output.append(SccRow(name=row[1], status=row[2], city=row[3], state=row[4], zip_code=row[5]))
    return output


def post_agency_search(
    query: str,
    license_type: str,
    timeout: int,
    cache_path: Path | None = None,
    no_resume: bool = False,
) -> tuple[list[SccRow], str]:
    if cache_path and cache_path.exists() and not no_resume:
        result_page = cache_path.read_text(encoding="utf-8", errors="replace")
        return parse_agency_rows(result_page), as_of_date(result_page)

    cookie_jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
    request = urllib.request.Request(SCC_SEARCH_URL, headers={"User-Agent": "Mozilla/5.0"})
    with opener.open(request, timeout=timeout) as response:
        page = response.read().decode("utf-8", errors="replace")
    body = {
        "__LASTFOCUS": "",
        "__EVENTTARGET": "ctl00$MainContent$btnAgySearch",
        "__EVENTARGUMENT": "",
        "__VIEWSTATE": hidden_value(page, "__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": hidden_value(page, "__VIEWSTATEGENERATOR"),
        "__VIEWSTATEENCRYPTED": "",
        "__PREVIOUSPAGE": hidden_value(page, "__PREVIOUSPAGE"),
        "__EVENTVALIDATION": hidden_value(page, "__EVENTVALIDATION"),
        "ctl00$MainContent$rblAgyActive": "Y",
        "ctl00$MainContent$txtAgyLicenseNum": "",
        "ctl00$MainContent$txtAgyNPN": "",
        "ctl00$MainContent$rblAgyStartsWith": "N",
        "ctl00$MainContent$txtAgyName": query,
        "ctl00$MainContent$txtAgyCity": "",
        "ctl00$MainContent$ddlAgyState": "VA",
        "ctl00$MainContent$txtAgyZipcode": "",
        "ctl00$MainContent$ddlAgyInsuranceType": "",
        "ctl00$MainContent$ddlAgyLicenseType": license_type,
        "ctl00$MainContent$hfAgyPageNumber": "",
        "ctl00$MainContent$hfActiveTabIndex": "1",
        "ctl00$MainContent$hfLastView": "tpAgency",
    }
    encoded = urllib.parse.urlencode(body).encode("utf-8")
    post = urllib.request.Request(SCC_SEARCH_URL, data=encoded, headers={"User-Agent": "Mozilla/5.0"})
    with opener.open(post, timeout=timeout) as response:
        result_page = response.read().decode("utf-8", errors="replace")
    if cache_path:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(result_page, encoding="utf-8")
    return parse_agency_rows(result_page), as_of_date(result_page)


def entity_match_score(account_name: str, scc_name: str) -> tuple[float, str]:
    account_tokens = sorted(set(distinctive_tokens(account_name)))
    scc_tokens = set(distinctive_tokens(scc_name))
    if not account_tokens:
        return 0.0, ""
    hits = [token for token in account_tokens if token in scc_tokens]
    return len(hits) / len(account_tokens), ", ".join(hits)


def account_geo(row: dict[str, str]) -> tuple[str, str]:
    city = normalize_space(row.get("BillingCity") or row.get("AccountCity") or row.get("City") or "")
    zip_code = normalize_space(row.get("BillingPostalCode") or row.get("PostalCode") or row.get("Zip") or "")
    return city.lower(), zip_code[:5]


def geo_score(context_row: dict[str, str], scc_row: SccRow) -> int:
    city, zip_code = account_geo(context_row)
    score = 0
    if city and city == scc_row.city.lower():
        score += 2
    if zip_code and zip_code == scc_row.zip_code[:5]:
        score += 3
    return score


def choose_match(account_row: dict[str, str], rows: list[SccRow]) -> tuple[SccRow | None, float, str]:
    account_name = normalize_space(account_row.get("Name", ""))
    candidates: list[tuple[float, int, int, int, SccRow, str]] = []
    for row in rows:
        score, hits = entity_match_score(account_name, row.name)
        hit_count = len([item for item in hits.split(", ") if item])
        if score >= 0.50 and (hit_count >= 2 or score == 1.0):
            unstarred = 0 if "*" in row.name else 1
            candidates.append((score, hit_count, geo_score(account_row, row), unstarred, row, hits))
    if not candidates:
        return None, 0.0, ""
    candidates.sort(key=lambda item: (item[0], item[1], item[2], item[3]), reverse=True)
    score, _hit_count, _geo, _unstarred, row, hits = candidates[0]
    return row, score, hits


def registry_check(row: dict[str, str], args: argparse.Namespace) -> dict[str, object]:
    name = normalize_space(row.get("Name", ""))
    variants = query_variants(row)[: args.max_queries]
    all_evidence: list[str] = []
    last_as_of = ""
    row_cache_dir = args.raw_dir / f"{slug(name)}_{normalize_space(row.get('Id', ''))[-6:]}" if args.raw_dir else None
    for license_type, label in (("RECA", "Settlement Agent"), ("TI", "Title")):
        for query in variants:
            try:
                cache_path = row_cache_dir / f"{license_type}_{slug(query)}.html" if row_cache_dir else None
                rows, as_of = post_agency_search(query, license_type, args.timeout, cache_path, args.no_resume)
            except Exception as exc:  # noqa: BLE001 - public lookup can be flaky.
                all_evidence.append(f"{label} query `{query}` error: {exc!r}")
                continue
            last_as_of = as_of or last_as_of
            all_evidence.append(
                f"{label} query `{query}` returned {len(rows)} row(s): "
                + "; ".join(f"{item.name} | {item.status} | {item.city}, {item.state} {item.zip_code}" for item in rows[:5])
            )
            match, score, hits = choose_match(row, rows)
            if match:
                return {
                    "Id": row.get("Id", ""),
                    "Name": name,
                    "Bucket": row.get("Bucket", ""),
                    "ResolverOutcome": row.get("ResolverOutcome", ""),
                    "CandidateWebsite": row.get("CandidateWebsite", ""),
                    "SCCRegistryStatus": "matched_active_agency",
                    "SCCMatchType": label,
                    "SCCMatchedName": match.name,
                    "SCCMatchedStatus": match.status,
                    "SCCMatchedCity": match.city,
                    "SCCMatchedState": match.state,
                    "SCCMatchedZip": match.zip_code,
                    "SCCQueryUsed": query,
                    "SCCLicenseTypeSearched": license_type,
                    "SCCResultCount": len(rows),
                    "SCCAsOfDate": as_of,
                    "SCCEvidence": f"entity_score={score:.2f}; hits={hits}; " + " || ".join(all_evidence),
                    "SCCSourceUrl": SCC_SEARCH_URL,
                }
            time.sleep(args.sleep)
    return {
        "Id": row.get("Id", ""),
        "Name": name,
        "Bucket": row.get("Bucket", ""),
        "ResolverOutcome": row.get("ResolverOutcome", ""),
        "CandidateWebsite": row.get("CandidateWebsite", ""),
        "SCCRegistryStatus": "no_active_agency_match",
        "SCCMatchType": "",
        "SCCMatchedName": "",
        "SCCMatchedStatus": "",
        "SCCMatchedCity": "",
        "SCCMatchedState": "",
        "SCCMatchedZip": "",
        "SCCQueryUsed": " | ".join(variants),
        "SCCLicenseTypeSearched": "RECA | TI",
        "SCCResultCount": "",
        "SCCAsOfDate": last_as_of,
        "SCCEvidence": " || ".join(all_evidence),
        "SCCSourceUrl": SCC_SEARCH_URL,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run Virginia SCC agency registry checks.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--raw-dir", type=Path)
    parser.add_argument("--max-queries", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--sleep", type=float, default=0.25)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    if args.raw_dir:
        args.raw_dir.mkdir(parents=True, exist_ok=True)

    rows = read_csv(args.input)
    outputs: list[dict[str, object]] = []
    for index, row in enumerate(rows, start=1):
        output = registry_check(row, args)
        outputs.append(output)
        print(f"[{index}/{len(rows)}] {output['SCCRegistryStatus']} {output['Name']} -> {output['SCCMatchedName']}", flush=True)
        time.sleep(args.sleep)
    write_csv(args.output, outputs, OUTPUT_COLUMNS)
    summary = {
        "input": str(args.input),
        "output": str(args.output),
        "rows": len(outputs),
        "status_counts": dict(Counter(str(row.get("SCCRegistryStatus") or "(blank)") for row in outputs)),
        "match_type_counts": dict(Counter(str(row.get("SCCMatchType") or "(blank)") for row in outputs)),
    }
    args.summary.write_text(__import__("json").dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
