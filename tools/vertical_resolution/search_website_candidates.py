#!/usr/bin/env python3
"""Find proposed websites for suspect Accounts using web search plus preflight.

This is intentionally conservative. It writes proposed-field updates only and
does not update Account.Website.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
import sys
import time
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

from url_preflight import preflight_one


DUCKDUCKGO_SEARCH_URL = "https://duckduckgo.com/html/?"
YAHOO_SEARCH_URL = "https://search.yahoo.com/search?"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
)

COMMON_TOKENS = {
    "a",
    "and",
    "at",
    "abstract",
    "agency",
    "attorney",
    "attorneys",
    "closing",
    "closings",
    "co",
    "company",
    "companies",
    "corp",
    "corporation",
    "dba",
    "db",
    "enterprise",
    "enterprises",
    "escrow",
    "firm",
    "for",
    "group",
    "home",
    "homes",
    "holdings",
    "in",
    "inc",
    "insurance",
    "law",
    "legal",
    "llc",
    "llp",
    "local",
    "lp",
    "ltd",
    "mortgage",
    "national",
    "of",
    "office",
    "offices",
    "on",
    "pa",
    "partner",
    "partners",
    "pc",
    "pllc",
    "real",
    "realty",
    "service",
    "services",
    "settlement",
    "the",
    "title",
    "to",
}

DIRECTORY_HOSTS = {
    "allbiz.com",
    "allusinjurylawyers.com",
    "alignable.com",
    "bbb.org",
    "bisprofiles.com",
    "bizapedia.com",
    "bizprofile.net",
    "bloomberg.com",
    "buzzfile.com",
    "business.ct.gov",
    "cartersvillechamber.com",
    "chambermaster.com",
    "cgslawyer.net",
    "chamberofcommerce.com",
    "cience.com",
    "city-data.com",
    "corporationwiki.com",
    "crunchbase.com",
    "dandb.com",
    "datanyze.com",
    "direct.us",
    "esplawyers.com",
    "facebook.com",
    "findglocal.com",
    "glassdoor.com",
    "google.com",
    "hotfrog.com",
    "housingwire.com",
    "instagram.com",
    "ipaddress.com",
    "justia.com",
    "karbonhq.com",
    "lawcrossing.com",
    "lawyer.com",
    "lawyerdb.org",
    "lawyersattorneysguide.com",
    "lawyers.com",
    "linkedin.com",
    "local.gocommercially.com",
    "local.yahoo.com",
    "manta.com",
    "martindale.com",
    "mapquest.com",
    "mylocalservices.com",
    "naicslist.com",
    "opencorporates.com",
    "opengovny.com",
    "ptindirectory.com",
    "qdexx.com",
    "signalhire.com",
    "siteindices.com",
    "sur.ly",
    "texas-biz.com",
    "tiktok.com",
    "tydl.io",
    "uscourts.gov",
    "whereorg.com",
    "wiza.co",
    "yelp.com",
    "yellowpages.com",
    "zoominfo.com",
}

WEAK_HOST_TOKENS = {
    "alabama",
    "alaska",
    "arizona",
    "arkansas",
    "california",
    "carolina",
    "colorado",
    "connecticut",
    "dakota",
    "delaware",
    "east",
    "florida",
    "first",
    "georgia",
    "hawaii",
    "idaho",
    "illinois",
    "indiana",
    "iowa",
    "kansas",
    "kentucky",
    "louisiana",
    "maine",
    "maryland",
    "massachusetts",
    "michigan",
    "minnesota",
    "mississippi",
    "missouri",
    "montana",
    "nation",
    "nebraska",
    "nevada",
    "new",
    "north",
    "ohio",
    "oklahoma",
    "one",
    "oregon",
    "pennsylvania",
    "rhode",
    "south",
    "tennessee",
    "texas",
    "utah",
    "vermont",
    "virginia",
    "washington",
    "west",
    "wisconsin",
    "wyoming",
}

BAD_HOSTS = {
    "cloud01.titletapsites.com",
    "cloud02.titletapsites.com",
    "cloud03.titletapsites.com",
    "godaddy.com",
    "hugedomains.com",
    "localwebdesigncompany.com",
}

OUTPUT_FIELDS = [
    "Id",
    "Website_Hygiene_Review_Status__c",
    "Proposed_Website__c",
    "Proposed_Website_Source__c",
    "Proposed_Website_Confidence__c",
    "Proposed_Website_Evidence__c",
    "Proposed_Website_Checked_At__c",
    "Website_Hygiene_Notes__c",
]


def clean(value: str | None) -> str:
    return " ".join((value or "").split())


def tokens(value: str | None) -> list[str]:
    value = (value or "").lower().replace("&", " and ")
    raw_tokens = re.findall(r"[a-z0-9]+", value)
    return [token for token in raw_tokens if len(token) > 1 and token not in COMMON_TOKENS]


def primary_account_name(value: str | None) -> str:
    name = clean(value)
    for separator in (" - ", " — ", " d/b/a ", " dba "):
        parts = re.split(re.escape(separator), name, maxsplit=1, flags=re.IGNORECASE)
        if len(parts) > 1 and parts[0].strip():
            return parts[0].strip()
    return name


def host(url: str | None) -> str:
    parsed = urlparse(url or "")
    hostname = (parsed.netloc or parsed.path.split("/", 1)[0]).lower().split("@")[-1].split(":", 1)[0]
    return hostname[4:] if hostname.startswith("www.") else hostname


def root_domain(hostname: str) -> str:
    parts = hostname.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else hostname


def is_ignored_host(hostname: str) -> bool:
    root = root_domain(hostname)
    return (
        hostname in DIRECTORY_HOSTS
        or root in DIRECTORY_HOSTS
        or hostname in BAD_HOSTS
        or root in BAD_HOSTS
        or any(hostname.endswith(f".{known}") for known in DIRECTORY_HOSTS | BAD_HOSTS)
    )


def unwrap_duckduckgo_url(url: str) -> str:
    value = html.unescape(url)
    if value.startswith("//duckduckgo.com/l/") or value.startswith("https://duckduckgo.com/l/"):
        parsed = urlparse(value if value.startswith("http") else f"https:{value}")
        uddg = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(uddg)
    return value


def unwrap_yahoo_url(url: str) -> str:
    value = html.unescape(url)
    parsed = urlparse(value)
    if parsed.netloc == "r.search.yahoo.com":
        match = re.search(r"/RU=([^/]+)", parsed.path)
        if match:
            return unquote(match.group(1))
    return value


def strip_tags(value: str) -> str:
    return clean(re.sub(r"<[^>]+>", " ", value))


def search_duckduckgo(query: str, timeout: int) -> list[dict[str, str]]:
    url = DUCKDUCKGO_SEARCH_URL + urlencode({"q": query})
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=timeout) as response:
        text = response.read().decode("utf-8", errors="ignore")

    results: list[dict[str, str]] = []
    pattern = re.compile(
        r'<a rel="nofollow" class="result__a" href="([^"]+)"[^>]*>(.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(text):
        result_url = unwrap_duckduckgo_url(match.group(1))
        result_title = html.unescape(strip_tags(match.group(2)))
        if result_url.startswith("http"):
            results.append({"url": result_url, "title": result_title, "snippet": ""})
    return results


def search_yahoo(query: str, timeout: int) -> list[dict[str, str]]:
    url = YAHOO_SEARCH_URL + urlencode({"p": query})
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.9"})
    with urlopen(request, timeout=timeout) as response:
        text = response.read().decode("utf-8", errors="ignore")

    results: list[dict[str, str]] = []
    block_pattern = re.compile(r'<li><div class="dd algo.*?</li>', re.IGNORECASE | re.DOTALL)
    for block_match in block_pattern.finditer(text):
        block = block_match.group(0)
        href_match = re.search(r'href="([^"]+)"', block)
        title_match = re.search(r"<h3[^>]*>.*?</h3>", block, re.IGNORECASE | re.DOTALL)
        snippet_match = re.search(r'<div class="compText.*?<p[^>]*>(.*?)</p>', block, re.IGNORECASE | re.DOTALL)
        if not href_match or not title_match:
            continue
        result_url = unwrap_yahoo_url(href_match.group(1))
        result_title = html.unescape(strip_tags(title_match.group(0)))
        snippet = html.unescape(strip_tags(snippet_match.group(1))) if snippet_match else ""
        if result_url.startswith("http"):
            results.append({"url": result_url, "title": result_title, "snippet": snippet})
    return results


def search(query: str, timeout: int) -> list[dict[str, str]]:
    errors: list[str] = []
    for provider in (search_yahoo, search_duckduckgo):
        try:
            results = provider(query, timeout)
            if results:
                return results
        except Exception as exc:  # noqa: BLE001 - fall through to next provider.
            errors.append(f"{provider.__name__}: {exc}")
    if errors:
        raise RuntimeError("; ".join(errors))
    return []


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def load_existing(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    rows = read_csv(path)
    return {row["Id"]: row for row in rows if row.get("Id")}


def query_variants(name: str, state: str, existing_url: str) -> Iterable[str]:
    base = clean(name)
    if state:
        yield f'"{base}" {state} official website'
        yield f'"{base}" {state} title escrow law website'
    yield f'"{base}" official website'
    yield f'"{base}" title escrow law firm website'
    existing_host = host(existing_url)
    if existing_host:
        stem = existing_host.split(".", 1)[0]
        yield f'"{stem}" "{base}"'


def looks_like_domain(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9][a-z0-9-]{1,62}(\.[a-z0-9][a-z0-9-]{1,62})+", value.lower()))


def candidate_urls_from_result(result_url: str, result_title: str, result_snippet: str) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    result_host = host(result_url)
    if result_url.startswith("http") and not is_ignored_host(result_host):
        candidates.append((result_url, "search_result_url"))

    text = html.unescape(f"{result_title} {result_snippet}")
    for match in re.finditer(r"https?://[^\s<>()\"']+", text, re.IGNORECASE):
        url = match.group(0).rstrip(".,;:")
        if not is_ignored_host(host(url)):
            candidates.append((url, "extracted_url"))

    for match in re.finditer(r"\b(?:www\.)?[a-z0-9][a-z0-9-]{1,62}(?:\.[a-z0-9][a-z0-9-]{1,62})+\b", text, re.IGNORECASE):
        domain = match.group(0).lower().strip(".")
        if looks_like_domain(domain) and not is_ignored_host(host(domain)):
            candidates.append((f"https://{domain}", "extracted_domain"))

    parsed_path = urlparse(result_url).path
    for part in parsed_path.split("/"):
        part = unquote(part).lower()
        if looks_like_domain(part) and not is_ignored_host(host(part)):
            candidates.append((f"https://{part}", "path_domain"))

    deduped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for url, source in candidates:
        key = host(url)
        if key and key not in seen:
            seen.add(key)
            deduped.append((url, source))
    return deduped


def score_candidate(
    account_name: str,
    result_title: str,
    result_snippet: str,
    result_url: str,
    preflight_title: str,
    final_url: str,
) -> tuple[str, str, float]:
    name_tokens = tokens(primary_account_name(account_name))
    if not name_tokens:
        return "reject", "No usable account-name tokens.", 0.0

    candidate_host = host(final_url or result_url)
    if is_ignored_host(candidate_host):
        return "reject", f"Ignored directory/vendor/social host: {candidate_host}.", 0.0

    host_text = candidate_host.replace("-", " ").replace(".", " ")
    host_compact = re.sub(r"[^a-z0-9]+", "", candidate_host.lower())
    host_hits = sorted({token for token in name_tokens if token in set(tokens(host_text)) or token in host_compact})
    strong_host_hits = [token for token in host_hits if token not in WEAK_HOST_TOKENS]
    if not host_hits:
        return "reject", f"No account-name token in candidate host: {candidate_host}.", 0.0
    if not strong_host_hits:
        return "reject", f"Only weak/geographic token matched candidate host: {', '.join(host_hits)}.", 0.0

    evidence_text = f"{candidate_host} {urlparse(final_url or result_url).path} {result_title} {result_snippet} {preflight_title}"
    evidence_tokens = set(tokens(evidence_text))
    compact = re.sub(r"[^a-z0-9]+", "", evidence_text.lower())
    hits = sorted({token for token in name_tokens if token in evidence_tokens or token in compact})
    ratio = len(hits) / max(1, len(set(name_tokens)))
    host_ratio = len(host_hits) / max(1, len(set(name_tokens)))

    if len(strong_host_hits) >= 2 and host_ratio >= 0.5 and len(hits) >= 2 and ratio >= 0.67:
        return "high", f"Matched host tokens: {', '.join(host_hits)}; all evidence tokens: {', '.join(hits)}.", ratio
    if len(strong_host_hits) >= 1 and len(hits) >= 2 and ratio >= 0.5:
        return "medium", f"Matched host tokens: {', '.join(host_hits)}; all evidence tokens: {', '.join(hits)}.", ratio
    if len(strong_host_hits) >= 1 and len(set(name_tokens)) == 1 and len(hits) >= 1:
        return "medium", f"Matched host tokens: {', '.join(host_hits)}; all evidence tokens: {', '.join(hits)}.", ratio
    return "reject", f"Insufficient account-token match. Host hits: {', '.join(host_hits)}; all hits: {', '.join(hits) or 'none'}.", ratio


def find_candidate(
    row: dict[str, str],
    context: dict[str, str],
    search_timeout: int,
    preflight_timeout: int,
    sleep_seconds: float,
    max_results: int,
    max_queries: int,
    max_candidate_urls: int,
) -> tuple[dict[str, str] | None, list[dict[str, str]]]:
    account_id = clean(row.get("input_id"))
    name = clean(row.get("input_name"))
    state = clean(context.get("BillingState"))
    existing_url = clean(row.get("input_url"))
    checked_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    diagnostics: list[dict[str, str]] = []

    seen_hosts: set[str] = set()
    candidate_url_attempts = 0
    for query_index, query in enumerate(query_variants(name, state, existing_url), start=1):
        if query_index > max_queries:
            break
        try:
            results = search(query, search_timeout)
        except Exception as exc:  # noqa: BLE001 - preserve search failure.
            diagnostics.append({"Id": account_id, "query": query, "url": "", "decision": "search_error", "reason": str(exc)})
            continue

        time.sleep(sleep_seconds)
        for result in results[:max_results]:
            candidate_urls = candidate_urls_from_result(result["url"], result["title"], result.get("snippet", ""))
            for result_url, candidate_source in candidate_urls:
                if candidate_url_attempts >= max_candidate_urls:
                    return None, diagnostics
                result_host = host(result_url)
                if not result_host or result_host in seen_hosts or is_ignored_host(result_host):
                    continue
                seen_hosts.add(result_host)
                candidate_url_attempts += 1

                preflight = preflight_one("", account_id, name, result_url, preflight_timeout)
                if preflight.status != "ok":
                    diagnostics.append(
                        {
                            "Id": account_id,
                            "query": query,
                            "url": result_url,
                            "decision": "preflight_reject",
                            "reason": f"candidate_source={candidate_source}; preflight_status={preflight.status}",
                        }
                    )
                    continue

                confidence, reason, ratio = score_candidate(
                    name,
                    result["title"],
                    result.get("snippet", ""),
                    result_url,
                    preflight.title,
                    preflight.final_url,
                )
                diagnostics.append(
                    {
                        "Id": account_id,
                        "query": query,
                        "url": result_url,
                        "final_url": preflight.final_url,
                        "decision": confidence,
                        "reason": f"candidate_source={candidate_source}; {reason}",
                        "ratio": f"{ratio:.2f}",
                        "result_title": result["title"],
                        "preflight_title": preflight.title,
                    }
                )
                if confidence in {"high", "medium"}:
                    proposed = preflight.final_url or result_url
                    evidence = (
                        "Search-backed candidate. "
                        f"Query: {query}. Candidate source: {candidate_source}. Candidate URL: {result_url}. "
                        f"Final URL: {proposed}. Search result URL: {result['url']}. "
                        f"Search title: {clean(result['title'])}. Search snippet: {clean(result.get('snippet', ''))}. "
                        f"Page title: {clean(preflight.title)}. {reason}"
                    )
                    return (
                        {
                            "Id": account_id,
                            "Website_Hygiene_Review_Status__c": "auto_update_candidate" if confidence == "high" else "queued",
                            "Proposed_Website__c": proposed[:255],
                            "Proposed_Website_Source__c": "search",
                            "Proposed_Website_Confidence__c": confidence,
                            "Proposed_Website_Evidence__c": evidence[:32000],
                            "Proposed_Website_Checked_At__c": checked_at,
                            "Website_Hygiene_Notes__c": evidence[:32000],
                        },
                        diagnostics,
                    )
    return None, diagnostics


def main() -> int:
    parser = argparse.ArgumentParser(description="Search for website candidates for suspect Accounts.")
    parser.add_argument("--review-queue", required=True, type=Path)
    parser.add_argument("--account-export", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--diagnostics", required=True, type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--statuses", nargs="+", default=["no_url", "dns_error", "error", "url_error"])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--search-timeout", type=int, default=20)
    parser.add_argument("--preflight-timeout", type=int, default=8)
    parser.add_argument("--sleep", type=float, default=0.35)
    parser.add_argument("--max-results", type=int, default=5)
    parser.add_argument("--max-queries", type=int, default=3)
    parser.add_argument("--max-candidate-urls", type=int, default=8)
    parser.add_argument("--progress-every", type=int, default=25)
    args = parser.parse_args()

    context_by_id = {row["Id"]: row for row in read_csv(args.account_export) if row.get("Id")}
    review_rows = [row for row in read_csv(args.review_queue) if row.get("status") in set(args.statuses)]
    if args.limit:
        review_rows = review_rows[: args.limit]

    candidates_by_id = load_existing(args.output) if args.resume else {}
    existing_diagnostics = read_csv(args.diagnostics) if args.resume and args.diagnostics.exists() else []
    processed_ids = set(candidates_by_id)
    processed_ids.update(row.get("Id", "") for row in existing_diagnostics if row.get("Id"))

    diagnostics = existing_diagnostics
    for index, row in enumerate(review_rows, start=1):
        account_id = clean(row.get("input_id"))
        if account_id in processed_ids:
            continue
        candidate, row_diagnostics = find_candidate(
            row,
            context_by_id.get(account_id, {}),
            args.search_timeout,
            args.preflight_timeout,
            args.sleep,
            args.max_results,
            args.max_queries,
            args.max_candidate_urls,
        )
        diagnostics.extend(row_diagnostics)
        if candidate:
            candidates_by_id[account_id] = candidate
        processed_ids.add(account_id)
        if args.progress_every and index % args.progress_every == 0:
            counts = Counter(row["Proposed_Website_Confidence__c"] for row in candidates_by_id.values())
            print(
                f"processed={index}/{len(review_rows)} candidates={len(candidates_by_id)} "
                f"high={counts.get('high', 0)} medium={counts.get('medium', 0)}",
                file=sys.stderr,
                flush=True,
            )
            write_csv(args.output, OUTPUT_FIELDS, list(candidates_by_id.values()))
            write_csv(
                args.diagnostics,
                ["Id", "query", "url", "final_url", "decision", "reason", "ratio", "result_title", "preflight_title"],
                diagnostics,
            )

    candidates = list(candidates_by_id.values())
    write_csv(args.output, OUTPUT_FIELDS, candidates)
    write_csv(
        args.diagnostics,
        ["Id", "query", "url", "final_url", "decision", "reason", "ratio", "result_title", "preflight_title"],
        diagnostics,
    )
    counts = Counter(row["Proposed_Website_Confidence__c"] for row in candidates)
    print(f"processed={len(processed_ids)}")
    print(f"candidate_rows={len(candidates)}")
    print(f"high_confidence={counts.get('high', 0)}")
    print(f"medium_confidence={counts.get('medium', 0)}")
    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
