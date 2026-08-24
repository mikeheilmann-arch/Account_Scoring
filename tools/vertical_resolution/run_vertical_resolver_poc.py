#!/usr/bin/env python3
"""Run a no-write vertical resolver POC for title/settlement Accounts.

The resolver is intentionally evidence-first. It uses Nimble web search to
collect candidate website and vertical-directory signals, then applies
deterministic classification rules. It does not write to Salesforce.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


OUTPUT_COLUMNS = [
    "Id",
    "Name",
    "Bucket",
    "CurrentWebsite",
    "WebsiteHygiene",
    "FinalAction",
    "RetrievalStatus",
    "CandidateWebsite",
    "CandidateWebsiteConfidence",
    "CandidateEntityMatch",
    "EntityMatchNotes",
    "AccountLawSignal",
    "ICPClassification",
    "ICPConfidence",
    "ResolverOutcome",
    "EvidenceSources",
    "DirectoryEvidenceCount",
    "WebsiteEvidenceCount",
    "NonICPEvidenceCount",
    "LawFirmEvidenceCount",
    "LocationEvidenceCount",
    "QueriesRun",
    "TopEvidence",
    "AllEvidence",
    "SalesforceUrl",
]

LABEL_COLUMNS = OUTPUT_COLUMNS + [
    "GroundTruthCorrectWebsite",
    "GroundTruthICPClass",
    "GroundTruthNotes",
    "Reviewer",
    "ReviewedAt",
]

COMMON_TOKENS = {
    "a",
    "and",
    "at",
    "co",
    "company",
    "com",
    "corp",
    "corporation",
    "closing",
    "closings",
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
    "net",
    "of",
    "online",
    "office",
    "offices",
    "org",
    "pc",
    "plc",
    "pllc",
    "service",
    "services",
    "settlement",
    "the",
    "title",
    "to",
    "va",
    "virginia",
    "www",
}

LEGAL_SUFFIX_TOKENS = {
    "a",
    "and",
    "at",
    "co",
    "company",
    "com",
    "corp",
    "corporation",
    "dba",
    "for",
    "group",
    "inc",
    "llc",
    "llp",
    "lp",
    "ltd",
    "net",
    "of",
    "online",
    "org",
    "pc",
    "plc",
    "pllc",
    "the",
    "to",
    "www",
}

DIRECTORY_HOSTS = {
    "alta.org": "ALTA",
    "altaidregistry.org": "ALTA",
    "firstam.com": "Underwriter",
    "firstamignite.com": "Underwriter",
    "fnf.com": "Underwriter",
    "chicagotitle.com": "Underwriter",
    "oldrepublictitle.com": "Underwriter",
    "stewart.com": "Underwriter",
    "wfgtitle.com": "Underwriter",
    "doma.com": "Underwriter",
    "scc.virginia.gov": "VA SCC",
    "scc.virginia.gov": "VA SCC",
    "nipr.com": "NIPR",
    "sircon.com": "Sircon",
    "vsb.org": "Virginia State Bar",
    "vba.org": "Virginia Bar",
    "avvo.com": "Legal Directory",
    "martindale.com": "Legal Directory",
    "lawyers.com": "Legal Directory",
    "justia.com": "Legal Directory",
    "findlaw.com": "Legal Directory",
    "bbb.org": "Business Directory",
    "birdeye.com": "Business Directory",
    "business.fauquierchamber.org": "Business Directory",
    "manta.com": "Business Directory",
    "chamberofcommerce.com": "Business Directory",
    "glassdoor.com": "Business Directory",
    "hub.biz": "Business Directory",
    "lawcrossing.com": "Legal Directory",
    "lawinfo.com": "Legal Directory",
    "mapquest.com": "Business Directory",
    "nextdoor.com": "Business Directory",
    "realtor.com": "Business Directory",
    "scribd.com": "Document Repository",
    "yelp.com": "Business Directory",
    "wikipedia.org": "Reference",
    "yellowpages.com": "Business Directory",
    "zoominfo.com": "Business Directory",
    "privco.com": "Business Directory",
    "system.privco.com": "Business Directory",
    "local.yahoo.com": "Business Directory",
    "yahoo.com": "Business Directory",
    "linkedin.com": "Social/Company Profile",
    "facebook.com": "Social/Company Profile",
    "instagram.com": "Social/Company Profile",
    "bcgsearch.com": "Legal Directory",
    "superlawyers.com": "Legal Directory",
    "attorneys.superlawyers.com": "Legal Directory",
    "fairfaxcounty.gov": "Government Directory",
    "health.ny.gov": "Government Directory",
    "register.dls.virginia.gov": "Government Directory",
    "rackcdn.com": "Document Repository",
    "cloudfront.net": "Document Repository",
    "groups.google.com": "Document Repository",
    "google.com": "Document Repository",
    "legacy.com": "Reference",
    "casemine.com": "Legal Directory",
    "bestlawfirms.com": "Legal Directory",
    "wtop.com": "News/Reference",
    "hrsd.com": "Government Directory",
    "wallsins.com": "Business Directory",
    "archive.org": "Document Repository",
    "plainsite.org": "Business Directory",
    "bankrupt.com": "Document Repository",
    "inclusiveva.org": "Reference",
    "veritaglobal.net": "Document Repository",
    "valawyersweekly.com": "News/Reference",
    "silo.tips": "Document Repository",
    "q4cdn.com": "Document Repository",
    "nnrha.net": "Reference",
    "hracre.org": "Reference",
    "beaconliteracy.org": "Reference",
    "state.va.us": "Government Directory",
    "hodcap.state.va.us": "Government Directory",
}

DIRECTORY_ONLY_HOSTS = {
    "alta.org",
    "altaidregistry.org",
    "scc.virginia.gov",
    "nipr.com",
    "sircon.com",
    "vsb.org",
    "vba.org",
    "avvo.com",
    "martindale.com",
    "lawyers.com",
    "justia.com",
    "findlaw.com",
    "bbb.org",
    "birdeye.com",
    "business.fauquierchamber.org",
    "manta.com",
    "chamberofcommerce.com",
    "glassdoor.com",
    "hub.biz",
    "lawcrossing.com",
    "lawinfo.com",
    "mapquest.com",
    "nextdoor.com",
    "realtor.com",
    "scribd.com",
    "yelp.com",
    "wikipedia.org",
    "yellowpages.com",
    "zoominfo.com",
    "privco.com",
    "system.privco.com",
    "local.yahoo.com",
    "yahoo.com",
    "linkedin.com",
    "facebook.com",
    "instagram.com",
    "bcgsearch.com",
    "superlawyers.com",
    "attorneys.superlawyers.com",
    "fairfaxcounty.gov",
    "health.ny.gov",
    "register.dls.virginia.gov",
    "rackcdn.com",
    "cloudfront.net",
    "groups.google.com",
    "google.com",
    "legacy.com",
    "casemine.com",
    "bestlawfirms.com",
    "wtop.com",
    "hrsd.com",
    "wallsins.com",
    "archive.org",
    "plainsite.org",
    "bankrupt.com",
    "inclusiveva.org",
    "veritaglobal.net",
    "valawyersweekly.com",
    "silo.tips",
    "q4cdn.com",
    "nnrha.net",
    "hracre.org",
    "beaconliteracy.org",
    "state.va.us",
    "hodcap.state.va.us",
}

TRUSTED_VERTICAL_SOURCES = {"ALTA", "Underwriter", "VA SCC", "NIPR", "Sircon"}

ICP_TERMS = {
    "abstract",
    "alta",
    "closing",
    "closings",
    "escrow",
    "notary",
    "settlement",
    "settlements",
    "title",
    "underwriter",
}

LAW_TERMS = {
    "attorney",
    "attorneys",
    "bar",
    "counsel",
    "law",
    "lawyer",
    "lawyers",
    "legal",
    "litigation",
}

NON_ICP_TERMS = {
    "brokerage",
    "coldwell banker",
    "mortgage",
    "nmls",
    "real estate agent",
    "real estate brokerage",
    "realtor",
    "remax",
}

LOCATION_TERMS = {
    "address",
    "contact",
    "directions",
    "location",
    "locations",
    "office",
    "offices",
}


@dataclass
class SearchResult:
    query: str
    title: str
    url: str
    description: str
    host: str
    source: str
    name_score: float
    name_hits: str
    name_hit_count: int
    name_token_count: int
    host_name_score: float
    exact_name_match: bool
    entity_match_level: str
    entity_match_reason: str
    has_icp: bool
    has_law: bool
    has_non_icp: bool
    has_location: bool


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


def slug(value: str, limit: int = 80) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()[:limit] or "row"


def host(url: str) -> str:
    return urlparse(url).netloc.lower().replace("www.", "")


def current_account_host(row: dict[str, str]) -> str:
    current = clean(row.get("Website"))
    return host(current if re.match(r"^https?://", current, re.I) else f"https://{current}") if current else ""


def root_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return url
    return f"{parsed.scheme}://{parsed.netloc}/"


def name_tokens(name: str) -> list[str]:
    raw = re.findall(r"[a-z0-9]+", name.lower())
    return [token for token in raw if token not in COMMON_TOKENS and len(token) > 1]


def name_token_stats(name: str, text: str) -> tuple[list[str], list[str], float]:
    tokens = sorted(set(name_tokens(name)))
    if not tokens:
        return [], [], 0.0
    normalized_text = " " + re.sub(r"[^a-z0-9]+", " ", text.lower()) + " "
    hits = [token for token in tokens if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", normalized_text)]
    return tokens, hits, len(hits) / max(1, len(tokens))


def normalized_name_phrase(value: str) -> str:
    words = [
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if token not in LEGAL_SUFFIX_TOKENS and len(token) > 1
    ]
    return " ".join(words)


def exact_name_phrase_match(name: str, text: str) -> bool:
    phrase = normalized_name_phrase(name)
    if not phrase or len(phrase.split()) < 2:
        return False
    normalized_text = normalized_name_phrase(text)
    return phrase in normalized_text


def token_score(name: str, text: str) -> float:
    return name_token_stats(name, text)[2]


def host_token_score(name: str, result_host: str) -> float:
    normalized_host = re.sub(r"[^a-z0-9]+", "", result_host.lower())
    tokens = name_tokens(name)
    strong_tokens = [token for token in tokens if len(token) >= 3]
    if not strong_tokens:
        return 0.0
    hits = sum(1 for token in set(strong_tokens) if token in normalized_host)
    return hits / max(1, len(set(strong_tokens)))


def account_law_signal(name: str) -> bool:
    low = f" {name.lower()} "
    has_title_operator_terms = any(term in low for term in (" title ", " escrow ", " settlement ", " closing ", " closings "))
    if re.search(r"\blaw\s+(firm|group|office|offices)\b", low):
        return True
    if re.search(r"\b(attorney|attorneys)\s+at\s+law\b", low):
        return True
    if re.search(r"\blegal\b", low) and not has_title_operator_terms:
        return True
    if re.search(r"\b(p\.?c\.?|plc|pllc|llp|professional corporation)\b", low) and not has_title_operator_terms:
        return True
    return False


def is_entity_relevant(row: dict[str, str], result: SearchResult) -> bool:
    if result.entity_match_level in {"strong", "medium"}:
        return True
    current_host = current_account_host(row)
    return bool(current_host and result.host == current_host and result.name_score >= 0.25)


def is_geo_relevant(row: dict[str, str], result: SearchResult) -> bool:
    current_host = current_account_host(row)
    if current_host and result.host == current_host:
        return True
    return state_match(row, result)


def state_match(row: dict[str, str], result: SearchResult) -> bool:
    state = clean(row.get("BillingState")).lower() or "virginia"
    state_code = clean(row.get("BillingStateCode")).lower() or "va"
    text = f"{result.title} {result.description} {result.url}".lower()
    return state in text or re.search(rf"(?<![a-z]){re.escape(state_code)}(?![a-z])", text) is not None


def source_for_host(result_host: str) -> str:
    if result_host.endswith(".gov"):
        return "Government Directory"
    for known, source in DIRECTORY_HOSTS.items():
        if result_host == known or result_host.endswith("." + known):
            return source
    return "Website/Search Result"


def is_directory_host(result_host: str) -> bool:
    if result_host.endswith(".gov"):
        return True
    return any(result_host == known or result_host.endswith("." + known) for known in DIRECTORY_ONLY_HOSTS)


def search_queries(row: dict[str, str]) -> list[str]:
    name = clean(row.get("Name"))
    state = clean(row.get("BillingState")) or "Virginia"
    current = clean(row.get("Website"))
    queries = [
        f'"{name}" "{state}" title settlement escrow',
        f'"{name}" "{state}" ALTA Registry',
        f'"{name}" "{state}" title insurance agency',
        f'"{name}" "{state}" underwriter title agent',
        f'"{name}" "{state}" real estate settlement attorney',
        f'"{name}" "{state}" location office',
    ]
    if current:
        current_host = host(current if re.match(r"^https?://", current, re.I) else f"https://{current}")
        if current_host:
            queries.append(f'"{name}" "{current_host}" title settlement')
    return queries


def run_nimble_search(query: str, args: argparse.Namespace) -> dict:
    command = [
        sys.executable,
        "-m",
        "g_gremlin.cli",
        "nimbleway",
        "search",
        "--query",
        query,
        "--max-results",
        str(args.max_results),
        "--search-depth",
        "lite",
        "--json",
    ]
    try:
        proc = subprocess.run(command, cwd=str(args.nimble_cwd), capture_output=True, text=True, timeout=args.search_timeout)
    except subprocess.TimeoutExpired:
        return {"error": f"timeout after {args.search_timeout}s", "results": []}
    if proc.returncode != 0:
        return {"error": (proc.stderr or proc.stdout).strip(), "results": []}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"error": f"invalid json: {proc.stdout[:500]}", "results": []}


def result_from_payload(query: str, item: dict[str, object], name: str) -> SearchResult:
    title = clean(item.get("title"))
    url = clean(item.get("url"))
    description = clean(item.get("description")) or clean(item.get("content"))
    result_host = host(url)
    text = f"{title} {description} {url}"
    low = text.lower()
    tokens, hits, name_score = name_token_stats(name, text)
    host_score = host_token_score(name, result_host)
    exact_match = exact_name_phrase_match(name, text)
    if exact_match:
        entity_level = "strong"
        entity_reason = "exact normalized account-name phrase"
    elif host_score >= 0.75 and name_score >= 0.40:
        entity_level = "strong"
        entity_reason = "candidate domain contains account token(s)"
    elif len(tokens) >= 2 and len(hits) >= 2 and name_score >= 0.50:
        entity_level = "medium"
        entity_reason = "multi-token distinctive account-name match"
    elif len(tokens) == 1 and len(hits) == 1 and host_score >= 0.75:
        entity_level = "medium"
        entity_reason = "single distinctive token matched in candidate domain"
    elif len(hits) == 1:
        entity_level = "weak"
        entity_reason = "single distinctive token only"
    else:
        entity_level = "weak"
        entity_reason = "no distinctive account-name match"
    return SearchResult(
        query=query,
        title=title,
        url=url,
        description=description,
        host=result_host,
        source=source_for_host(result_host),
        name_score=name_score,
        name_hits=", ".join(hits),
        name_hit_count=len(hits),
        name_token_count=len(tokens),
        host_name_score=host_score,
        exact_name_match=exact_match,
        entity_match_level=entity_level,
        entity_match_reason=entity_reason,
        has_icp=any(term in low for term in ICP_TERMS),
        has_law=any(term in low for term in LAW_TERMS),
        has_non_icp=any(term in low for term in NON_ICP_TERMS),
        has_location=any(term in low for term in LOCATION_TERMS),
    )


def candidate_website(row: dict[str, str], results: list[SearchResult]) -> tuple[str, str, str, str]:
    name = clean(row.get("Name"))
    current_host = current_account_host(row)
    candidates: list[tuple[float, str, str, str]] = []
    for result in results:
        if not result.url or is_directory_host(result.host):
            continue
        if result.has_non_icp and not result.has_icp:
            continue
        host_score = result.host_name_score
        same_current_host = bool(current_host and result.host == current_host)
        entity_level = result.entity_match_level
        entity_reason = result.entity_match_reason
        if same_current_host:
            host_score = max(host_score, 1.0)
            if result.name_score >= 0.25 and entity_level == "weak":
                entity_level = "medium"
                entity_reason = "current Account website host with account-name evidence"
        if entity_level == "weak":
            continue
        if not is_geo_relevant(row, result):
            continue
        if host_score < 0.25 and not result.exact_name_match:
            continue
        score = (result.name_score * 0.55) + (host_score * 0.45)
        if entity_level == "strong":
            score += 0.15
        if result.has_icp:
            score += 0.25
        if result.has_location:
            score += 0.05
        if score >= 0.45:
            candidates.append((score, root_url(result.url), entity_level, entity_reason))
    if not candidates:
        return "", "", "", ""
    candidates.sort(reverse=True)
    top_score, top_url, entity_level, entity_reason = candidates[0]
    if top_score >= 0.85:
        return top_url, "High", entity_level, entity_reason
    if top_score >= 0.60:
        return top_url, "Medium", entity_level, entity_reason
    return top_url, "Low", entity_level, entity_reason


def classify(
    row: dict[str, str],
    results: list[SearchResult],
    candidate_url: str,
    candidate_conf: str,
) -> tuple[str, str, str, str]:
    name = clean(row.get("Name"))
    account_is_law = account_law_signal(name)
    matched_results = [result for result in results if is_entity_relevant(row, result) and is_geo_relevant(row, result)]
    candidate_host = host(candidate_url) if candidate_url else ""
    candidate_results = [
        result
        for result in matched_results
        if candidate_host and result.host == candidate_host
    ]
    website_results = candidate_results or [result for result in matched_results if result.source == "Website/Search Result"]
    directory_hits = [result for result in matched_results if result.source != "Website/Search Result"]
    trusted_directory_hits = [
        result
        for result in directory_hits
        if result.source in TRUSTED_VERTICAL_SOURCES and result.entity_match_level == "strong"
    ]
    website_icp_hits = [result for result in website_results if result.has_icp]
    icp_hits = [result for result in matched_results if result.has_icp]
    non_icp_hits = [result for result in matched_results if result.has_non_icp and not result.has_icp]
    location_hits = [result for result in matched_results if result.has_location]

    evidence_sources = sorted({result.source for result in matched_results})
    source_text = "; ".join(evidence_sources)

    if account_is_law and (trusted_directory_hits or website_icp_hits):
        conf = "High" if trusted_directory_hits and candidate_conf == "High" else "Medium"
        return "Real estate law firm - title/settlement evidence", conf, "law_firm_review", source_text
    if account_is_law:
        return "Law firm - settlement evidence not confirmed", "Medium", "law_firm_review", source_text
    if non_icp_hits and not icp_hits:
        return "Non-ICP / brokerage or mortgage", "Medium", "non_icp_review", source_text
    if trusted_directory_hits and website_icp_hits and candidate_conf == "High":
        return "Title/settlement ICP supported by matched website and vertical evidence", "High", "high_confidence_recovery", source_text
    if trusted_directory_hits:
        return "Title/settlement ICP supported by matched vertical result", "Medium", "reviewer_assist", source_text
    if icp_hits and directory_hits:
        return "Title/settlement ICP supported by public directory", "Medium", "reviewer_assist", source_text
    if website_icp_hits and candidate_conf in {"High", "Medium"}:
        conf = "High" if candidate_conf == "High" else "Medium"
        outcome = "high_confidence_recovery" if conf == "High" else "reviewer_assist"
        return "Title/settlement ICP likely from matched website evidence", conf, outcome, source_text
    if website_icp_hits:
        conf = "Medium" if candidate_conf in {"High", "Medium"} else "Low"
        return "Title/settlement ICP likely from web evidence", conf, "reviewer_assist" if conf != "Low" else "needs_review", source_text
    if candidate_conf in {"High", "Medium"}:
        return "Website candidate found; ICP not fully confirmed", "Low", "needs_review", source_text
    if location_hits:
        return "Business presence found; ICP not confirmed", "Low", "needs_review", source_text
    return "Unresolved", "Low", "unresolved", source_text


def evidence_line(result: SearchResult) -> str:
    flags = []
    if result.has_icp:
        flags.append("ICP terms")
    if result.has_law:
        flags.append("law terms")
    if result.has_non_icp:
        flags.append("non-ICP terms")
    if result.has_location:
        flags.append("location terms")
    flag_text = ", ".join(flags) or "general match"
    return (
        f"{result.source} | entity={result.entity_match_level} ({result.entity_match_reason}) | "
        f"name_score={result.name_score:.2f} hits={result.name_hits or '-'} | {flag_text} | "
        f"{result.title} | {result.url} | {result.description[:180]}"
    )


def process_row(row: dict[str, str], args: argparse.Namespace) -> dict[str, object]:
    raw_dir = args.raw_dir / f"{slug(clean(row.get('Name')))}_{clean(row.get('Id'))[-6:]}"
    raw_dir.mkdir(parents=True, exist_ok=True)
    query_payloads = []
    results: list[SearchResult] = []
    queries = search_queries(row)[: args.max_queries]
    for query in queries:
        cache_path = raw_dir / f"search_{slug(query, 55)}.json"
        if cache_path.exists() and not args.no_resume:
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                payload = run_nimble_search(query, args)
                cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        else:
            payload = run_nimble_search(query, args)
            cache_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        query_payloads.append(payload)
        for item in payload.get("results") or []:
            if isinstance(item, dict):
                result = result_from_payload(query, item, clean(row.get("Name")))
                if result.url:
                    results.append(result)
        time.sleep(args.sleep)

    # Deduplicate by URL, preserving strongest score.
    by_url: dict[str, SearchResult] = {}
    for result in results:
        existing = by_url.get(result.url)
        if not existing or result.name_score > existing.name_score:
            by_url[result.url] = result
    entity_rank = {"strong": 2, "medium": 1, "weak": 0}
    results = sorted(
        by_url.values(),
        key=lambda item: (entity_rank.get(item.entity_match_level, 0), item.name_score, item.has_icp, item.has_location),
        reverse=True,
    )

    candidate, candidate_conf, candidate_entity_match, entity_notes = candidate_website(row, results)
    classification, icp_conf, outcome, source_text = classify(row, results, candidate, candidate_conf)
    matched = [result for result in results if is_entity_relevant(row, result)]
    top_evidence = [evidence_line(result) for result in matched[:5]]
    all_evidence = [evidence_line(result) for result in matched[:15]]

    return {
        "Id": clean(row.get("Id")),
        "Name": clean(row.get("Name")),
        "Bucket": clean(row.get("Bucket")),
        "CurrentWebsite": clean(row.get("Website")),
        "WebsiteHygiene": clean(row.get("WebsiteHygiene")),
        "FinalAction": clean(row.get("FinalAction")),
        "RetrievalStatus": clean(row.get("RetrievalStatus")),
        "CandidateWebsite": candidate,
        "CandidateWebsiteConfidence": candidate_conf,
        "CandidateEntityMatch": candidate_entity_match,
        "EntityMatchNotes": entity_notes,
        "AccountLawSignal": "Yes" if account_law_signal(clean(row.get("Name"))) else "No",
        "ICPClassification": classification,
        "ICPConfidence": icp_conf,
        "ResolverOutcome": outcome,
        "EvidenceSources": source_text,
        "DirectoryEvidenceCount": sum(1 for result in matched if result.source != "Website/Search Result"),
        "WebsiteEvidenceCount": sum(1 for result in matched if result.source == "Website/Search Result"),
        "NonICPEvidenceCount": sum(1 for result in matched if result.has_non_icp),
        "LawFirmEvidenceCount": sum(1 for result in matched if account_law_signal(clean(row.get("Name"))) and result.has_law),
        "LocationEvidenceCount": sum(1 for result in matched if result.has_location),
        "QueriesRun": " | ".join(queries),
        "TopEvidence": " || ".join(top_evidence),
        "AllEvidence": " || ".join(all_evidence),
        "SalesforceUrl": clean(row.get("SalesforceUrl")),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run vertical resolver POC with Nimble search.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    parser.add_argument("--label-template", type=Path)
    parser.add_argument("--raw-dir", required=True, type=Path)
    parser.add_argument(
        "--nimble-cwd",
        type=Path,
        default=Path(os.environ.get("NIMBLE_CLI_ROOT", Path.cwd())),
        help="Directory containing the configured Nimble CLI; defaults to NIMBLE_CLI_ROOT or the current directory.",
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-results", type=int, default=6)
    parser.add_argument("--max-queries", type=int, default=5)
    parser.add_argument("--search-timeout", type=int, default=45)
    parser.add_argument("--sleep", type=float, default=0.1)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    args.raw_dir.mkdir(parents=True, exist_ok=True)

    rows = read_csv(args.input)
    outputs: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(process_row, row, args): row for row in rows}
        for index, future in enumerate(as_completed(futures), start=1):
            row = futures[future]
            try:
                output = future.result()
            except Exception as exc:  # noqa: BLE001 - keep long POC runs resilient.
                output = {
                    "Id": clean(row.get("Id")),
                    "Name": clean(row.get("Name")),
                    "Bucket": clean(row.get("Bucket")),
                    "ResolverOutcome": "script_error",
                    "TopEvidence": repr(exc),
                }
            outputs.append(output)
            print(f"[{index}/{len(rows)}] {output.get('ResolverOutcome')} {output.get('Name')} -> {output.get('ICPClassification', '')}", flush=True)

    order = {clean(row.get("Id")): idx for idx, row in enumerate(rows)}
    outputs.sort(key=lambda item: order.get(clean(item.get("Id")), 999999))
    write_csv(args.output, outputs, OUTPUT_COLUMNS)
    if args.label_template:
        label_rows = []
        for row in outputs:
            label_row = dict(row)
            label_row.update(
                {
                    "GroundTruthCorrectWebsite": "",
                    "GroundTruthICPClass": "",
                    "GroundTruthNotes": "",
                    "Reviewer": "",
                    "ReviewedAt": "",
                }
            )
            label_rows.append(label_row)
        write_csv(args.label_template, label_rows, LABEL_COLUMNS)

    summary = {
        "rows": len(outputs),
        "input": str(args.input),
        "output": str(args.output),
        "raw_dir": str(args.raw_dir),
        "outcome_counts": dict(Counter(clean(row.get("ResolverOutcome")) or "(blank)" for row in outputs)),
        "classification_counts": dict(Counter(clean(row.get("ICPClassification")) or "(blank)" for row in outputs)),
        "candidate_confidence_counts": dict(Counter(clean(row.get("CandidateWebsiteConfidence")) or "(blank)" for row in outputs)),
        "bucket_outcome_counts": {},
    }
    for row in outputs:
        bucket = clean(row.get("Bucket")) or "(blank)"
        outcome = clean(row.get("ResolverOutcome")) or "(blank)"
        summary["bucket_outcome_counts"].setdefault(bucket, {})
        summary["bucket_outcome_counts"][bucket][outcome] = summary["bucket_outcome_counts"][bucket].get(outcome, 0) + 1
    args.summary.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
