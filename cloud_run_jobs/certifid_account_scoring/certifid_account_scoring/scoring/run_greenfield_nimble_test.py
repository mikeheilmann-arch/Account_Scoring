#!/usr/bin/env python3
"""Run no-write greenfield account scoring with Nimble raw page retrieval.

The script intentionally keeps enrichment/scoring local and auditable:
- Nimble is used for site mapping and raw markdown retrieval.
- Deterministic parsing estimates location count, staff/team signal, website
  quality, ICP routing, MCV, and ARR.
- Outputs are CSV/JSON artifacts only; nothing is written to Salesforce.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from .legal_entity_scoring import LegalProfile, apply_legal_mcv_floor, classify_legal_entity
except ImportError:  # pragma: no cover - keeps direct script execution working.
    from legal_entity_scoring import LegalProfile, apply_legal_mcv_floor, classify_legal_entity


OUTPUT_COLUMNS = [
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
    "InputFinalMCV",
    "InputMCVSource",
    "InputHasAnyOpp",
    "InputLegacyTier",
    "RetrievalStatus",
    "MappedLinkCount",
    "ExtractedPageCount",
    "SelectedPages",
    "ICPDisposition",
    "ReviewAction",
    "EstimatedMCV",
    "EstimatedMCVLow",
    "EstimatedMCVHigh",
    "EstimatedARR",
    "ARRRange",
    "Score",
    "Confidence",
    "OfficeCount",
    "OfficeEvidence",
    "StaffCountVisible",
    "StaffEvidence",
    "WebsiteQualityScore",
    "LegalEntityRoute",
    "LegalMarketFit",
    "LegalEvidence",
    "ServiceSignals",
    "ToolSignals",
    "MarketSignals",
    "Evidence",
    "RawArtifactDir",
]

MCV_BANDS = [
    (0, 20, 2, 8_000, "$7K-$10K"),
    (20, 30, 4, 12_000, "$10K-$15K"),
    (30, 50, 6, 18_500, "$15K-$22K"),
    (50, 75, 8, 24_000, "$20K-$28K"),
    (75, 100, 10, 30_000, "$26K-$36K"),
    (100, 150, 12, 45_000, "$40K-$52K"),
    (150, 200, 15, 56_500, "$50K-$63K"),
    (200, 300, 18, 70_000, "$63K-$80K"),
    (300, 400, 20, 125_000, "$110K-$140K"),
    (400, 500, 23, 150_000, "$138K-$165K"),
    (500, 750, 25, 150_000, "$138K-$165K"),
    (750, 1250, 25, 150_000, "$138K-$165K"),
    (1250, 1750, 27, 150_000, "$150K+"),
    (1750, 2500, 29, 150_000, "$150K+"),
]

MCV_OVERFLOW_STEP = 500

TRUSTED_ACCOUNT_MCV_SOURCE_TOKENS = ("sales rep", "bdr", "ae", "rep")
ANCHOR_DOWNCAP_MULTIPLIER = 2
ANCHOR_UPCAP_MULTIPLIER = 2
CORROBORATED_HIGH_ANCHOR_OFFICE_THRESHOLD = 7
CORROBORATED_HIGH_ANCHOR_MAX_MCV = 800
NON_LEGAL_TITLE_HIGH_ANCHOR_MAX_MCV = 1500
SOURCE_ANCHOR_FALLBACK_MCV_CEILING = 750
ANCHOR_CORROBORATING_SERVICE_SIGNALS = {
    "abstract",
    "escrow",
    "lender services",
    "real estate closings",
    "refinance",
    "settlement",
    "title insurance",
}

US_STATE_ABBR = {
    "AL",
    "AK",
    "AZ",
    "AR",
    "CA",
    "CO",
    "CT",
    "DE",
    "FL",
    "GA",
    "HI",
    "ID",
    "IL",
    "IN",
    "IA",
    "KS",
    "KY",
    "LA",
    "ME",
    "MD",
    "MA",
    "MI",
    "MN",
    "MS",
    "MO",
    "MT",
    "NE",
    "NV",
    "NH",
    "NJ",
    "NM",
    "NY",
    "NC",
    "ND",
    "OH",
    "OK",
    "OR",
    "PA",
    "RI",
    "SC",
    "SD",
    "TN",
    "TX",
    "UT",
    "VT",
    "VA",
    "WA",
    "WV",
    "WI",
    "WY",
    "DC",
}

STATE_NAMES = {
    "Alabama",
    "Alaska",
    "Arizona",
    "Arkansas",
    "California",
    "Colorado",
    "Connecticut",
    "Delaware",
    "Florida",
    "Georgia",
    "Idaho",
    "Illinois",
    "Indiana",
    "Iowa",
    "Kansas",
    "Kentucky",
    "Louisiana",
    "Maine",
    "Maryland",
    "Massachusetts",
    "Michigan",
    "Minnesota",
    "Mississippi",
    "Missouri",
    "Montana",
    "Nebraska",
    "Nevada",
    "New Hampshire",
    "New Jersey",
    "New Mexico",
    "New York",
    "North Carolina",
    "North Dakota",
    "Ohio",
    "Oklahoma",
    "Oregon",
    "Pennsylvania",
    "Rhode Island",
    "South Carolina",
    "South Dakota",
    "Tennessee",
    "Texas",
    "Utah",
    "Vermont",
    "Virginia",
    "Washington",
    "West Virginia",
    "Wisconsin",
    "Wyoming",
}

SERVICE_KEYWORDS = {
    "title insurance": "title insurance",
    "real estate closing": "real estate closings",
    "real estate closings": "real estate closings",
    "settlement": "settlement",
    "escrow": "escrow",
    "abstract": "abstract",
    "1031": "1031 exchange",
    "commercial": "commercial",
    "residential": "residential",
    "builder": "builder/new construction",
    "new construction": "builder/new construction",
    "lender": "lender services",
    "notary": "notary",
    "refinance": "refinance",
}

TOOL_KEYWORDS = {
    "calculator": "calculator",
    "net sheet": "net sheet",
    "netsheet": "net sheet",
    "rate quote": "rate quote",
    "quote": "quote/order tool",
    "order title": "order title",
    "emd": "EMD",
    "earnest money": "earnest money",
    "portal": "portal",
    "remote online": "remote online closing",
    "wire": "wire/digital funds",
}

NON_ICP_PATTERNS = [
    r"\btitle boxing\b",
    r"\bauto title\b",
    r"\bmotor vehicle\b",
    r"\bcoldwell banker\b",
    r"\breal estate brokerage\b",
    r"title\.education",
]

DMV_MOTOR_VEHICLE_PATTERNS = [
    r"\bdepartment of motor vehicles\b",
    r"\bdmv\b.{0,80}\b(auto|automobile|vehicle|motor|registration|license|licensing|plates?|tags?|vin|driver|titling|transfer)\b",
    r"\b(auto|automobile|vehicle|motor|registration|license|licensing|plates?|tags?|vin|driver|titling|transfer)\b.{0,80}\bdmv\b",
]

ANCHOR_FALLBACK_OPERATOR_RE = re.compile(
    r"\b(title|escrow|settlement|abstract|closing|closings|lender services?|land title|national title)\b",
    re.I,
)

BAD_DOMAIN_PATTERNS = [
    "acmecorp.com",
    "aol.com",
    "firstam.com",
    "azmvdnow.gov",
    "coldwellbanker.com",
    "designatedlocalexpert.com",
]


@dataclass
class CliResult:
    ok: bool
    data: dict
    error: str = ""


def clean(value: object) -> str:
    return str(value or "").strip()


def first_present(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = clean(row.get(key))
        if value:
            return value
    return ""


def parse_int(value: object) -> int | None:
    text = clean(value).replace(",", "")
    if not text:
        return None
    try:
        return int(round(float(text)))
    except ValueError:
        return None


def slug(value: str, limit: int = 70) -> str:
    text = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return (text or "site")[:limit]


def normalize_url(value: str) -> str:
    text = clean(value)
    if not text:
        return text
    if not re.match(r"^https?://", text, flags=re.I):
        return f"https://{text}"
    return text


def raw_artifact_dir_name(account_id: str, account_name: str) -> str:
    source = f"{clean(account_id)}|{clean(account_name)}"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    return f"account_{digest}"


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8", errors="replace") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def coerce_nimble_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        return {key: coerce_nimble_payload(value) for key, value in payload.items()}
    if isinstance(payload, list):
        return [coerce_nimble_payload(item) for item in payload]
    if hasattr(payload, "model_dump"):
        return coerce_nimble_payload(payload.model_dump(mode="json", by_alias=True, exclude_none=True))
    if hasattr(payload, "to_dict"):
        return coerce_nimble_payload(payload.to_dict())
    return payload


def cli_arg(args: list[str], flag: str, default: str | None = None) -> str | None:
    try:
        index = args.index(flag)
    except ValueError:
        return default
    if index + 1 >= len(args):
        return default
    return args[index + 1]


def redact_configured_secret(text: str) -> str:
    redacted = str(text or "")
    for env_name in ("NIMBLEWAY_API_KEY", "NIMBLE_API_KEY"):
        value = os.getenv(env_name)
        if value and len(value) >= 8:
            redacted = redacted.replace(value, "[REDACTED]")
    return redacted


def run_nimble(args: list[str], cwd: Path, timeout: int) -> CliResult:
    del cwd
    api_key = os.getenv("NIMBLEWAY_API_KEY") or os.getenv("NIMBLE_API_KEY")
    if not api_key:
        return CliResult(ok=False, data={}, error="NIMBLEWAY_API_KEY is not configured")
    max_retries = int(os.getenv("NIMBLE_MAX_RETRIES", "1"))
    try:
        from nimble_python import Nimble

        client = Nimble(api_key=api_key, timeout=timeout, max_retries=max_retries)
        command = args[0] if args else ""
        if command == "map":
            response = client.map(
                url=cli_arg(args, "--url") or "",
                domain_filter=cli_arg(args, "--domain-filter"),
                limit=int(cli_arg(args, "--limit", "30") or "30"),
                sitemap=cli_arg(args, "--sitemap"),
            )
        elif command == "extract":
            requested_format = cli_arg(args, "--format")
            formats = [requested_format] if requested_format else None
            response = client.extract(
                url=cli_arg(args, "--url") or "",
                formats=formats,
                request_timeout=timeout * 1000,
            )
        else:
            return CliResult(ok=False, data={}, error=f"unsupported Nimble command: {command}")
        payload = coerce_nimble_payload(response)
        return CliResult(ok=True, data=payload if isinstance(payload, dict) else {"data": payload}, error="")
    except Exception as exc:  # noqa: BLE001 - keep row-level failures resumable.
        return CliResult(ok=False, data={}, error=redact_configured_secret(str(exc))[:1000])


def log_row_progress(index: int, total: int, result: dict[str, object]) -> None:
    account_id = clean(result.get("Id"))
    row_ref = account_id[-6:] if account_id else f"row-{index}"
    print(
        (
            f"[{index}/{total}] id_suffix={row_ref} "
            f"status={clean(result.get('RetrievalStatus')) or '(blank)'} "
            f"action={clean(result.get('ReviewAction')) or '(blank)'} "
            f"confidence={clean(result.get('Confidence')) or '(blank)'}"
        ),
        flush=True,
    )


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def page_score(link: dict[str, object]) -> int:
    url = clean(link.get("url")).lower()
    title = clean(link.get("title")).lower()
    description = clean(link.get("description")).lower()
    text = f"{url} {title} {description}"
    score = 0
    if any(token in text for token in ("contact", "location", "office", "find-us", "find_us", "directions")):
        score += 120
    if any(token in text for token in ("team", "staff", "people", "attorney", "professionals", "leadership")):
        score += 100
    if any(token in text for token in ("about", "company", "history", "who-we-are")):
        score += 80
    if any(token in text for token in ("service", "solution", "closing", "escrow", "settlement", "title")):
        score += 70
    if any(token in text for token in ("calculator", "resource", "emd", "earnest", "portal", "order")):
        score += 45
    if any(token in text for token in ("privacy", "terms", "login", "wp-content", ".pdf", "sitemap", ".xml", "/feed", "/blog/", "/post/")):
        score -= 80
    return score


def same_registered_domain(url_a: str, url_b: str) -> bool:
    host_a = urlparse(url_a).netloc.lower().replace("www.", "")
    host_b = urlparse(url_b).netloc.lower().replace("www.", "")
    if not host_a or not host_b:
        return False
    return host_a == host_b or host_a.endswith("." + host_b) or host_b.endswith("." + host_a)


def same_url(url_a: str, url_b: str) -> bool:
    return normalize_url(url_a).rstrip("/").lower() == normalize_url(url_b).rstrip("/").lower()


def choose_pages(base_url: str, map_payload: dict, max_pages: int) -> list[str]:
    base = normalize_url(base_url)
    parsed_base = urlparse(base)
    site_root = f"{parsed_base.scheme}://{parsed_base.netloc}" if parsed_base.scheme and parsed_base.netloc else base.rstrip("/")
    links = map_payload.get("links")
    if not isinstance(links, list):
        links = []
    candidates = []
    link_text = " ".join(clean(link.get("url")).lower() for link in links if isinstance(link, dict))
    for path in ("/location/", "/locations/"):
        if path.strip("/") in link_text:
            candidates.append((310, normalize_url(site_root.rstrip("/") + path)))
    for link in links:
        if not isinstance(link, dict):
            continue
        url = clean(link.get("url"))
        if not url or url.startswith(("mailto:", "tel:")):
            continue
        low_url = url.lower()
        parsed_url = urlparse(normalize_url(url))
        low_path = parsed_url.path.lower()
        if low_url.endswith((".pdf", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".xml", ".rss", ".atom")):
            continue
        if any(token in low_path for token in ("sitemap", "/feed", "/blog/", "/post/", "/news/", "/article/", "/attachment")):
            continue
        if not same_registered_domain(base, normalize_url(url)):
            continue
        candidates.append((page_score(link), normalize_url(url)))
    candidates.append((110, base))
    seen = set()
    ordered = []
    for _score, url in sorted(candidates, key=lambda item: item[0], reverse=True):
        key = url.rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(url)
        if len(ordered) >= max_pages:
            break
    return ordered


def markdown_from_extract(payload: dict) -> str:
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("markdown", "html", "text"):
            value = data.get(key)
            if isinstance(value, str):
                return value
    for key in ("markdown", "html", "text"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return ""


def extract_offices(text: str) -> tuple[int, list[str]]:
    lines = [re.sub(r"\s+", " ", line).strip(" -*#\t") for line in text.splitlines()]
    evidence: set[str] = set()
    state_abbr = "|".join(sorted(US_STATE_ABBR))
    state_names = "|".join(sorted((re.escape(s) for s in STATE_NAMES), key=len, reverse=True))
    zip_regex = re.compile(rf"\b((?:{state_abbr})|(?:{state_names}))\s+(\d{{5}}(?:-\d{{4}})?)\b", re.I)
    for idx, line in enumerate(lines):
        if not zip_regex.search(line):
            continue
        context_lines = [lines[i] for i in range(max(0, idx - 2), min(len(lines), idx + 1)) if lines[i]]
        context = " ".join(context_lines)
        context = re.sub(r"https?://\S+", "", context)
        context = re.sub(r"\bPhone:?\s*[\d().\-\s]+", "", context, flags=re.I)
        context = re.sub(r"\s+", " ", context).strip()
        if len(context) >= 12:
            evidence.add(context[:140])
    location_url_regex = re.compile(r"https?://[^\s)]+/(?:locations?|offices?)/([a-z0-9][a-z0-9-]{1,80})/?", re.I)
    blocked_slugs = {"location", "locations", "office", "offices", "contact", "about", "team"}
    for match in location_url_regex.finditer(text):
        slug_value = match.group(1).lower().strip("-")
        if not slug_value or slug_value in blocked_slugs or "." in slug_value:
            continue
        label = slug_value.replace("-", " ").title()
        evidence.add(f"location page: {label}")
    return len(evidence), sorted(evidence)[:12]


def extract_staff(text: str) -> tuple[int, list[str]]:
    names: set[str] = set()
    role_regex = re.compile(
        r"\b(attorney|counsel|closer|closing|escrow officer|processor|president|manager|owner|partner|"
        r"director|coordinator|examiner|sales|operations|officer|founder|ceo|cfo|vp|vice president)\b",
        re.I,
    )
    blocked_terms = {
        "abstract",
        "active",
        "advantage",
        "agency",
        "areas",
        "assurance",
        "bag",
        "board",
        "buyer",
        "calculator",
        "closing",
        "commercial",
        "community",
        "company",
        "competitive",
        "connect",
        "contact",
        "corporation",
        "county",
        "education",
        "efficiency",
        "escrow",
        "facts",
        "family",
        "foundation",
        "frequently",
        "history",
        "home",
        "insurance",
        "land",
        "locations",
        "mission",
        "mobile",
        "number",
        "office",
        "order",
        "policy",
        "price",
        "program",
        "questions",
        "rate",
        "realty",
        "recovery",
        "resources",
        "seller",
        "services",
        "settlement",
        "sign",
        "single",
        "sponsor",
        "summary",
        "title",
        "trust",
        "workout",
    }
    raw_lines = text.splitlines()
    for idx, raw_line in enumerate(raw_lines):
        line = re.sub(r"\s+", " ", raw_line).strip(" -*#\t")
        if not line or len(line) > 80:
            continue
        low = re.sub(r"[^a-z ]", "", line.lower()).strip()
        words = low.split()
        if any(word in blocked_terms for word in words):
            continue
        if any(word in low for word in ("street", "suite", "phone", "fax", "email", "copyright", "tel:", ".com")):
            continue
        if not (2 <= len(words) <= 4):
            continue
        context = " ".join(raw_lines[max(0, idx - 2) : min(len(raw_lines), idx + 3)])
        if not role_regex.search(context):
            continue
        if re.match(r"^[A-Z][a-zA-Z'.-]+(?:\s+[A-Z]\.)?(?:\s+[A-Z][a-zA-Z'.-]+){1,3}(?:,\s*(?:Esq\.?|Attorney|President|Manager|Closer|Processor|Counsel|CEO|Owner).*)?$", line):
            names.add(re.sub(r",.*$", "", line).strip())
    return len(names), sorted(names)[:25]


def keyword_signals(text: str, keywords: dict[str, str]) -> list[str]:
    low = text.lower()
    signals = set()
    for key, label in keywords.items():
        if re.search(rf"(?<![a-z0-9]){re.escape(key)}(?![a-z0-9])", low):
            signals.add(label)
    return sorted(signals)


def infer_website_quality(text: str, mapped_count: int, selected_pages: list[str], services: list[str], tools: list[str], office_count: int, staff_count: int) -> int:
    score = 0
    low = text.lower()
    if mapped_count >= 8:
        score += 1
    if any("contact" in url.lower() or "location" in url.lower() for url in selected_pages) or office_count > 0:
        score += 1
    if any("team" in url.lower() or "staff" in url.lower() or "about" in url.lower() for url in selected_pages) or staff_count > 0:
        score += 1
    if len(services) >= 3:
        score += 1
    if tools or any(token in low for token in ("alta", "underwriter", "best practices", "commercial", "builder", "lender")):
        score += 1
    return min(score, 5)


def is_non_icp(row: dict[str, str], text: str) -> bool:
    low = f"{clean(row.get('Name'))} {clean(row.get('Website'))} {text[:5000]}".lower()
    if "non-icp" in clean(row.get("Bucket")).lower():
        return True
    if any(re.search(pattern, low) for pattern in NON_ICP_PATTERNS):
        return True
    return any(re.search(pattern, low) for pattern in DMV_MOTOR_VEHICLE_PATTERNS)


def is_hygiene_control(row: dict[str, str], text: str) -> bool:
    low = f"{clean(row.get('Website'))} {text[:2000]}".lower()
    if "hygiene" in clean(row.get("Bucket")).lower():
        return True
    if clean(row.get("WebsiteHygiene")) and clean(row.get("WebsiteHygiene")) != "confirmed":
        return True
    return any(pattern in low for pattern in BAD_DOMAIN_PATTERNS)


def is_icp_likely(row: dict[str, str], text: str, services: list[str]) -> bool:
    low = f"{clean(row.get('Name'))} {clean(row.get('Website'))} {text[:10000]}".lower()
    if is_non_icp(row, text):
        return False
    if services:
        return True
    return any(token in low for token in ("title", "escrow", "settlement", "abstract", "closing", "closings"))


def account_mcv_anchor(row: dict[str, str]) -> tuple[int | None, str]:
    mcv = parse_int(first_present(row, "FinalMCV", "InputFinalMCV"))
    source = clean(first_present(row, "MCVSource", "InputMCVSource"))
    source_key = source.lower()
    if not mcv or not any(token in source_key for token in TRUSTED_ACCOUNT_MCV_SOURCE_TOKENS):
        return None, source
    return mcv, source


def customer_mcv_anchor(row: dict[str, str]) -> tuple[int | None, str]:
    status = clean(first_present(row, "HasAnyOpp", "InputHasAnyOpp")).lower()
    if status not in {"customer", "closed won", "closed_won", "closed-won"}:
        source = clean(first_present(row, "MCVSource", "InputMCVSource"))
        return None, source
    return account_mcv_anchor(row)


def has_title_or_settlement_context(row: dict[str, str], services: list[str] | None, legal_profile: LegalProfile | None) -> bool:
    if services and any(signal in ANCHOR_CORROBORATING_SERVICE_SIGNALS for signal in services):
        return True
    if legal_profile and legal_profile.is_law_firm and legal_profile.score_now_eligible and legal_profile.real_estate_hits:
        return True
    identity_text = f"{clean(row.get('Name'))} {clean(row.get('Website'))}"
    return bool(re.search(r"\b(title|escrow|settlement|abstract|closing|closings)\b", identity_text, re.I))


def corroborates_high_non_customer_anchor(
    account_mcv: int,
    website_mcv: int,
    row: dict[str, str],
    legal_profile: LegalProfile | None,
    office_count: int,
    staff_count: int,
    quality: int,
    services: list[str] | None,
) -> bool:
    if account_mcv <= website_mcv * ANCHOR_UPCAP_MULTIPLIER:
        return True
    if not has_title_or_settlement_context(row, services, legal_profile):
        return False
    if (not legal_profile or not legal_profile.is_law_firm) and account_mcv <= NON_LEGAL_TITLE_HIGH_ANCHOR_MAX_MCV:
        return True
    if office_count >= CORROBORATED_HIGH_ANCHOR_OFFICE_THRESHOLD and account_mcv <= CORROBORATED_HIGH_ANCHOR_MAX_MCV:
        return True
    if office_count >= 4 and (staff_count >= 20 or quality >= 4) and account_mcv <= max(website_mcv * 3, 500):
        return True
    if staff_count >= 25 and quality >= 4 and account_mcv <= max(website_mcv * 3, 500):
        return True
    return False


def apply_account_mcv_anchor(
    website_mcv: int,
    row: dict[str, str],
    legal_profile: LegalProfile | None = None,
    office_count: int = 0,
    staff_count: int = 0,
    quality: int = 0,
    services: list[str] | None = None,
) -> tuple[int, str]:
    account_mcv, source = account_mcv_anchor(row)
    if not account_mcv:
        return website_mcv, ""
    if account_mcv > website_mcv:
        if customer_mcv_anchor(row)[0]:
            return account_mcv, f"Sales Rep/BDR MCV anchor raised website estimate from {website_mcv} to {account_mcv}"
        if corroborates_high_non_customer_anchor(
            account_mcv,
            website_mcv,
            row,
            legal_profile,
            office_count,
            staff_count,
            quality,
            services,
        ):
            return account_mcv, f"Corroborated Sales Rep/BDR MCV anchor raised website estimate from {website_mcv} to {account_mcv}"
        if not has_title_or_settlement_context(row, services, legal_profile):
            return website_mcv, (
                f"Non-customer Sales Rep/BDR MCV anchor {account_mcv} ({source}) was not corroborated "
                f"by title/settlement/RE-closing evidence; kept website-supported estimate {website_mcv}"
            )
        capped = website_mcv * ANCHOR_UPCAP_MULTIPLIER
        return capped, (
            f"Non-customer Sales Rep/BDR MCV anchor {account_mcv} ({source}) exceeded corroborated website footprint; "
            f"capped website-supported estimate from {website_mcv} to {capped}"
        )
    if customer_mcv_anchor(row)[0] and website_mcv > account_mcv * ANCHOR_DOWNCAP_MULTIPLIER:
        capped = account_mcv * ANCHOR_DOWNCAP_MULTIPLIER
        return capped, f"Sales Rep/BDR MCV anchor capped website-only estimate from {website_mcv} to {capped}"
    if not customer_mcv_anchor(row)[0] and website_mcv > account_mcv * ANCHOR_DOWNCAP_MULTIPLIER:
        capped = account_mcv * ANCHOR_DOWNCAP_MULTIPLIER
        return capped, f"Non-customer Sales Rep/BDR MCV anchor capped website-only estimate from {website_mcv} to {capped}"
    return website_mcv, f"Sales Rep/BDR MCV anchor reviewed: {account_mcv} ({source})"


def apply_legal_small_firm_cap(
    mcv: int,
    row: dict[str, str],
    legal_profile: LegalProfile | None,
    office_count: int,
    staff_count: int,
    quality: int,
) -> tuple[int, str]:
    if not legal_profile or not legal_profile.is_law_firm or not legal_profile.score_now_eligible:
        return mcv, ""
    if account_mcv_anchor(row)[0]:
        return mcv, ""
    cap: int | None = None
    if office_count <= 1 and staff_count < 10:
        cap = 50
    elif office_count == 2 and staff_count < 10:
        cap = 100 if staff_count >= 6 and quality >= 4 else 75 if quality >= 3 else 50
    if cap is None or mcv <= cap:
        return mcv, ""
    return cap, (
        f"Small legal-entity footprint capped estimate from {mcv} to {cap} "
        f"({office_count} office(s), {staff_count} visible staff)"
    )


def trusted_anchor_score(row: dict[str, str]) -> tuple[int | None, int | None, int | None, int | None, str, str, str]:
    account_mcv, source = customer_mcv_anchor(row)
    if not account_mcv:
        return None, None, None, None, "", "", ""
    low, high, score, arr, arr_range = mcv_to_band(account_mcv)
    note = f"Trusted Sales Rep/BDR MCV anchor used as value: {account_mcv} ({source})"
    return account_mcv, low, high, arr, arr_range, str(score), note


def source_anchor_fallback_score(row: dict[str, str]) -> tuple[int | None, int | None, int | None, int | None, str, str, str]:
    account_mcv, source = account_mcv_anchor(row)
    if not account_mcv:
        return None, None, None, None, "", "", ""
    low, high, score, arr, arr_range = mcv_to_band(account_mcv)
    note = f"Trusted source-token MCV fallback used as value after limited retrieval: {account_mcv} ({source})"
    return account_mcv, low, high, arr, arr_range, str(score), note


def source_anchor_fallback_candidate(row: dict[str, str], text: str, legal_profile: LegalProfile) -> tuple[bool, int | None]:
    account_mcv, _source = account_mcv_anchor(row)
    if not account_mcv:
        return False, None
    if customer_mcv_anchor(row)[0]:
        return False, account_mcv
    if is_non_icp(row, text):
        return False, account_mcv
    if legal_profile.is_law_firm and (legal_profile.non_icp or legal_profile.review_needed):
        return False, account_mcv
    haystack = f"{clean(row.get('Name'))} {clean(row.get('Website'))} {text[:2000]}"
    return bool(ANCHOR_FALLBACK_OPERATOR_RE.search(haystack)), account_mcv


def source_anchor_fallback_allowed(row: dict[str, str], text: str, legal_profile: LegalProfile) -> bool:
    candidate, account_mcv = source_anchor_fallback_candidate(row, text, legal_profile)
    return bool(candidate and account_mcv is not None and account_mcv <= SOURCE_ANCHOR_FALLBACK_MCV_CEILING)


def source_anchor_fallback_review_reason(row: dict[str, str], text: str, legal_profile: LegalProfile) -> str:
    candidate, account_mcv = source_anchor_fallback_candidate(row, text, legal_profile)
    if not candidate or account_mcv is None or account_mcv <= SOURCE_ANCHOR_FALLBACK_MCV_CEILING:
        return ""
    source = clean(first_present(row, "MCVSource", "InputMCVSource"))
    return (
        f"Uncorroborated source-token MCV {account_mcv} ({source}) exceeds "
        f"fallback plausibility ceiling {SOURCE_ANCHOR_FALLBACK_MCV_CEILING}; verify SFDC MCV before scoring"
    )


def estimate_mcv(
    office_count: int,
    staff_count: int,
    quality: int,
    services: list[str],
    tools: list[str],
    row: dict[str, str],
    text: str,
    legal_profile: LegalProfile | None = None,
) -> tuple[int | None, int | None, int | None, int, str, str, str]:
    low_text = f"{clean(row.get('Name'))} {clean(row.get('Website'))} {text[:10000]}".lower()
    national_signal = any(token in low_text for token in ("national", "nationwide", "all 50 states", "multi-state", "multistate"))
    if office_count >= 20:
        mcv = 750
    elif office_count >= 15:
        mcv = 500
    elif office_count >= 10:
        mcv = 350
    elif office_count >= 7:
        mcv = 225
    elif office_count >= 4:
        mcv = 175 if staff_count >= 20 or quality >= 4 else 125
    elif office_count >= 2:
        mcv = 125 if staff_count >= 20 or quality >= 4 else 75
    elif office_count == 1:
        mcv = 75 if staff_count >= 20 or quality >= 4 or national_signal else 40
    elif national_signal and quality >= 3:
        mcv = 125
    elif staff_count >= 20:
        mcv = 100
    elif quality >= 4:
        mcv = 62
    elif len(services) >= 2:
        mcv = 40
    else:
        mcv = 25

    if tools and mcv < 62:
        mcv = 62
    if "Nationals" in clean(row.get("Segment")) and mcv < 100 and (national_signal or quality >= 4):
        mcv = 100

    legal_note = ""
    if legal_profile:
        mcv, legal_note = apply_legal_mcv_floor(mcv, legal_profile)

    mcv, mcv_note = apply_account_mcv_anchor(
        mcv,
        row,
        legal_profile=legal_profile,
        office_count=office_count,
        staff_count=staff_count,
        quality=quality,
        services=services,
    )
    mcv, legal_cap_note = apply_legal_small_firm_cap(mcv, row, legal_profile, office_count, staff_count, quality)
    mcv_note = "; ".join(note for note in (legal_note, mcv_note, legal_cap_note) if note)
    band_low, band_high, score, arr, arr_range = mcv_to_band(mcv)
    return mcv, band_low, band_high, arr, arr_range, str(score), mcv_note


def mcv_to_band(mcv: int) -> tuple[int, int, int, int, str]:
    for low, high, score, arr, arr_range in MCV_BANDS:
        if low <= mcv < high:
            return low, high, score, arr, arr_range
    low, high, score, arr, arr_range = MCV_BANDS[-1]
    if mcv >= high:
        low = (mcv // MCV_OVERFLOW_STEP) * MCV_OVERFLOW_STEP
        high = low + MCV_OVERFLOW_STEP
    return low, high, score, arr, arr_range


def confidence(
    review_action: str,
    office_count: int,
    staff_count: int,
    quality: int,
    pages: int,
    legal_profile: LegalProfile | None = None,
) -> str:
    if review_action != "score_now":
        if review_action == "insufficient_public_evidence" or pages == 0:
            return "Low"
        return "High" if review_action in {"non_icp_confirmed", "hygiene_review"} else "Medium"
    if office_count >= 2 and quality >= 3:
        return "High"
    if legal_profile and legal_profile.confidence_adjustment == "raise_to_medium":
        return "Medium"
    if office_count >= 1 or staff_count >= 5 or pages >= 2:
        return "Medium"
    return "Low"


def process_row(row: dict[str, str], args: argparse.Namespace) -> dict[str, object]:
    account_id = clean(row.get("Id"))
    account_name = clean(row.get("Name")) or account_id
    site_dir = args.raw_dir / raw_artifact_dir_name(account_id, account_name)
    site_dir.mkdir(parents=True, exist_ok=True)
    base_url = normalize_url(clean(row.get("Website")))

    output_base = {
        "SourceSet": clean(row.get("SourceSet")),
        "TestLane": clean(row.get("TestLane")),
        "Bucket": clean(row.get("Bucket")),
        "Id": account_id,
        "SalesforceUrl": clean(row.get("SalesforceUrl")),
        "Name": account_name,
        "Website": clean(row.get("Website")),
        "BillingState": clean(row.get("BillingState")),
        "Segment": clean(row.get("Segment")),
        "Owner": clean(row.get("Owner")),
        "InputFinalMCV": clean(row.get("FinalMCV")),
        "InputMCVSource": clean(row.get("MCVSource")),
        "InputHasAnyOpp": clean(row.get("HasAnyOpp")),
        "InputLegacyTier": clean(row.get("LegacyTier")),
        "RawArtifactDir": str(site_dir),
    }

    website_hygiene = clean(row.get("WebsiteHygiene"))
    customer_anchor_mcv, _customer_anchor_source = customer_mcv_anchor(row)
    if not base_url or (website_hygiene and website_hygiene != "confirmed"):
        reason = "No website available" if not base_url else f"Website hygiene status is {website_hygiene}"
        if customer_anchor_mcv:
            mcv, low, high, arr, arr_range, score, mcv_note = trusted_anchor_score(row)
            return {
                **output_base,
                "RetrievalStatus": "not_run",
                "ICPDisposition": "trusted_mcv_anchor",
                "ReviewAction": "score_now",
                "EstimatedMCV": mcv,
                "EstimatedMCVLow": low,
                "EstimatedMCVHigh": high,
                "EstimatedARR": arr,
                "ARRRange": arr_range,
                "Score": score,
                "Confidence": "High",
                "LegalEntityRoute": "not_evaluated",
                "LegalMarketFit": "",
                "LegalEvidence": "trusted MCV anchor scored before website classification",
                "Evidence": f"{reason}; {mcv_note}",
            }
        return {
            **output_base,
            "RetrievalStatus": "not_run",
            "ICPDisposition": "hygiene_needed",
            "ReviewAction": "hygiene_review",
            "Evidence": reason,
        }

    map_path = site_dir / "map.json"
    if map_path.exists() and args.resume:
        try:
            map_payload = json.loads(map_path.read_text(encoding="utf-8"))
            map_result = CliResult(ok=bool(map_payload), data=map_payload)
        except json.JSONDecodeError:
            map_result = CliResult(ok=False, data={}, error="existing map json invalid")
    else:
        map_result = run_nimble(
            ["map", "--url", base_url, "--domain-filter", "domain", "--limit", str(args.map_limit), "--sitemap", "include"],
            args.nimble_cwd,
            args.map_timeout,
        )
        if map_result.ok:
            save_json(map_path, map_result.data)
        else:
            (site_dir / "map_error.txt").write_text(map_result.error, encoding="utf-8")

    if not map_result.ok:
        if customer_anchor_mcv:
            mcv, low, high, arr, arr_range, score, mcv_note = trusted_anchor_score(row)
            return {
                **output_base,
                "RetrievalStatus": "map_failed",
                "ICPDisposition": "trusted_mcv_anchor",
                "ReviewAction": "score_now",
                "EstimatedMCV": mcv,
                "EstimatedMCVLow": low,
                "EstimatedMCVHigh": high,
                "EstimatedARR": arr,
                "ARRRange": arr_range,
                "Score": score,
                "Confidence": "High",
                "LegalEntityRoute": "not_evaluated",
                "LegalMarketFit": "",
                "LegalEvidence": "trusted MCV anchor scored before legal classification",
                "Evidence": f"Map failed: {map_result.error[:500]}; {mcv_note}",
            }
        legal_profile = classify_legal_entity(row, "", [], 0, 0, 0)
        if source_anchor_fallback_allowed(row, "", legal_profile):
            mcv, low, high, arr, arr_range, score, mcv_note = source_anchor_fallback_score(row)
            return {
                **output_base,
                "RetrievalStatus": "map_failed",
                "ICPDisposition": legal_profile.route if legal_profile.is_law_firm else "scorable_anchor_fallback",
                "ReviewAction": "score_now",
                "EstimatedMCV": mcv,
                "EstimatedMCVLow": low,
                "EstimatedMCVHigh": high,
                "EstimatedARR": arr,
                "ARRRange": arr_range,
                "Score": score,
                "Confidence": "Medium",
                "LegalEntityRoute": legal_profile.route,
                "LegalMarketFit": legal_profile.market_fit,
                "LegalEvidence": legal_profile.evidence or "non-law title/settlement operator fallback",
                "Evidence": f"Map failed: {map_result.error[:500]}; {mcv_note}; value requires backtest/rep validation",
            }
        fallback_review_reason = source_anchor_fallback_review_reason(row, "", legal_profile)
        if fallback_review_reason:
            return {
                **output_base,
                "RetrievalStatus": "map_failed",
                "ICPDisposition": "anchor_plausibility_review",
                "ReviewAction": "manual_review",
                "Confidence": "Medium",
                "LegalEntityRoute": legal_profile.route,
                "LegalMarketFit": legal_profile.market_fit,
                "LegalEvidence": legal_profile.evidence or "non-law title/settlement operator plausibility review",
                "Evidence": f"Map failed: {map_result.error[:500]}; {fallback_review_reason}",
            }
        return {
            **output_base,
            "RetrievalStatus": "map_failed",
            "ICPDisposition": "indeterminate",
            "ReviewAction": "insufficient_public_evidence",
            "Confidence": "Low",
            "LegalEntityRoute": "not_evaluated",
            "LegalMarketFit": "",
            "LegalEvidence": "website map failed before legal classification",
            "Evidence": f"Map failed: {map_result.error[:500]}",
        }

    selected_pages = choose_pages(base_url, map_result.data, args.max_pages)
    markdowns: list[str] = []
    extracted_urls: list[str] = []
    errors: list[str] = []
    for index, page_url in enumerate(selected_pages, start=1):
        page_path = site_dir / f"page_{index}_{slug(urlparse(page_url).path or 'home', 30)}.json"
        if page_path.exists() and args.resume:
            try:
                page_payload = json.loads(page_path.read_text(encoding="utf-8"))
                payload_url = clean(page_payload.get("url"))
                metadata = page_payload.get("metadata") if isinstance(page_payload.get("metadata"), dict) else {}
                response_parameters = metadata.get("response_parameters") if isinstance(metadata.get("response_parameters"), dict) else {}
                payload_url = payload_url or clean(response_parameters.get("input_url"))
                if payload_url and same_url(payload_url, page_url):
                    page_result = CliResult(ok=True, data=page_payload)
                else:
                    page_result = run_nimble(
                        ["extract", "--url", page_url, "--format", "markdown"],
                        args.nimble_cwd,
                        args.extract_timeout,
                    )
                    if page_result.ok:
                        save_json(page_path, page_result.data)
                    else:
                        (site_dir / f"page_{index}_error.txt").write_text(f"{page_url}\n{page_result.error}", encoding="utf-8")
            except json.JSONDecodeError:
                page_result = CliResult(ok=False, data={}, error="existing page json invalid")
        elif args.resume and (site_dir / f"page_{index}_error.txt").exists():
            error_text = (site_dir / f"page_{index}_error.txt").read_text(encoding="utf-8")
            error_url = clean(error_text.splitlines()[0] if error_text.splitlines() else "")
            if error_url and same_url(error_url, page_url):
                page_result = CliResult(ok=False, data={}, error=error_text[:500])
            else:
                page_result = run_nimble(
                    ["extract", "--url", page_url, "--format", "markdown"],
                    args.nimble_cwd,
                    args.extract_timeout,
                )
                if page_result.ok:
                    save_json(page_path, page_result.data)
                else:
                    (site_dir / f"page_{index}_error.txt").write_text(f"{page_url}\n{page_result.error}", encoding="utf-8")
        else:
            page_result = run_nimble(
                ["extract", "--url", page_url, "--format", "markdown"],
                args.nimble_cwd,
                args.extract_timeout,
            )
            if page_result.ok:
                save_json(page_path, page_result.data)
            else:
                (site_dir / f"page_{index}_error.txt").write_text(f"{page_url}\n{page_result.error}", encoding="utf-8")
        if page_result.ok:
            markdown = markdown_from_extract(page_result.data)
            if markdown:
                markdowns.append(markdown)
                extracted_urls.append(clean(page_result.data.get("url")) or page_url)
        else:
            errors.append(f"{page_url}: {page_result.error[:160]}")

    combined_text = "\n\n".join(markdowns)
    office_count, office_evidence = extract_offices(combined_text)
    staff_count, staff_evidence = extract_staff(combined_text)
    services = keyword_signals(combined_text, SERVICE_KEYWORDS)
    tools = keyword_signals(combined_text, TOOL_KEYWORDS)
    mapped_count = len(map_result.data.get("links") or [])
    quality = infer_website_quality(combined_text, mapped_count, extracted_urls, services, tools, office_count, staff_count)
    legal_profile = classify_legal_entity(row, combined_text, services, office_count, staff_count, quality)

    if customer_anchor_mcv:
        icp = legal_profile.route if legal_profile.is_law_firm else "scorable"
        action = "score_now"
        mcv, low, high, arr, arr_range, score, mcv_note = estimate_mcv(
            office_count,
            staff_count,
            quality,
            services,
            tools,
            row,
            combined_text,
            legal_profile,
        )
        if mcv_note:
            mcv_note = f"Trusted anchor made row score-eligible; {mcv_note}"
    elif is_hygiene_control(row, combined_text):
        icp = "hygiene_needed"
        action = "hygiene_review"
        mcv = low = high = arr = None
        arr_range = ""
        score = ""
        mcv_note = ""
    elif legal_profile.is_law_firm and legal_profile.score_now_eligible:
        icp = legal_profile.route
        action = "score_now"
        mcv, low, high, arr, arr_range, score, mcv_note = estimate_mcv(
            office_count,
            staff_count,
            quality,
            services,
            tools,
            row,
            combined_text,
            legal_profile,
        )
    elif (not markdowns or (errors and not services)) and source_anchor_fallback_allowed(row, combined_text, legal_profile):
        icp = legal_profile.route if legal_profile.is_law_firm else "scorable_anchor_fallback"
        action = "score_now"
        mcv, low, high, arr, arr_range, score, mcv_note = source_anchor_fallback_score(row)
    elif (not markdowns or (errors and not services)) and (
        fallback_review_reason := source_anchor_fallback_review_reason(row, combined_text, legal_profile)
    ):
        icp = "anchor_plausibility_review"
        action = "manual_review"
        mcv = low = high = arr = None
        arr_range = ""
        score = ""
        mcv_note = fallback_review_reason
    elif not markdowns:
        icp = "indeterminate"
        action = "insufficient_public_evidence"
        mcv = low = high = arr = None
        arr_range = ""
        score = ""
        mcv_note = ""
    elif legal_profile.non_icp:
        icp = legal_profile.route
        action = "non_icp_confirmed"
        mcv = low = high = arr = None
        arr_range = ""
        score = ""
        mcv_note = ""
    elif legal_profile.review_needed:
        icp = legal_profile.route
        action = "manual_review"
        mcv = low = high = arr = None
        arr_range = ""
        score = ""
        mcv_note = ""
    elif is_non_icp(row, combined_text):
        icp = "non_icp"
        action = "non_icp_confirmed"
        mcv = low = high = arr = None
        arr_range = ""
        score = ""
        mcv_note = ""
    elif not is_icp_likely(row, combined_text, services):
        icp = "indeterminate"
        action = "manual_review"
        mcv = low = high = arr = None
        arr_range = ""
        score = ""
        mcv_note = ""
    else:
        icp = legal_profile.route if legal_profile.is_law_firm else "scorable"
        action = "score_now"
        mcv, low, high, arr, arr_range, score, mcv_note = estimate_mcv(
            office_count,
            staff_count,
            quality,
            services,
            tools,
            row,
            combined_text,
            legal_profile,
        )

    conf = confidence(action, office_count, staff_count, quality, len(markdowns), legal_profile)
    if customer_anchor_mcv and action == "score_now":
        conf = "High"
    elif icp == "scorable_anchor_fallback" and action == "score_now":
        conf = "Medium"
    elif icp == "anchor_plausibility_review":
        conf = "Medium"
    status = "ok" if markdowns else "extract_failed"
    if errors and markdowns:
        status = "partial"

    evidence_parts = []
    if office_count:
        evidence_parts.append(f"{office_count} visible office/address signals")
    if staff_count:
        evidence_parts.append(f"{staff_count} visible staff/team name signals")
    if services:
        evidence_parts.append(f"services: {', '.join(services[:6])}")
    if tools:
        evidence_parts.append(f"tools/process: {', '.join(tools[:5])}")
    if quality:
        evidence_parts.append(f"website quality score {quality}/5")
    if legal_profile.evidence:
        evidence_parts.append(f"legal lane: {legal_profile.evidence}")
    if mcv_note:
        evidence_parts.append(mcv_note)
    if not evidence_parts:
        evidence_parts.append("limited usable public evidence")
    if errors:
        evidence_parts.append(f"retrieval warnings on {len(errors)} selected page(s)")

    structured = {
        "account": account_name,
        "id": account_id,
        "selected_pages": selected_pages,
        "extracted_urls": extracted_urls,
        "office_count": office_count,
        "office_evidence": office_evidence,
        "staff_count": staff_count,
        "staff_evidence": staff_evidence,
        "services": services,
        "tools": tools,
        "website_quality_score": quality,
        "legal_entity_route": legal_profile.route,
        "legal_market_fit": legal_profile.market_fit,
        "legal_evidence": legal_profile.evidence,
        "legal_real_estate_hits": list(legal_profile.real_estate_hits),
        "legal_general_practice_hits": list(legal_profile.general_practice_hits),
        "icp": icp,
        "action": action,
        "estimated_mcv": mcv,
        "confidence": conf,
        "errors": errors,
    }
    save_json(site_dir / "structured_summary.json", structured)

    return {
        **output_base,
        "RetrievalStatus": status,
        "MappedLinkCount": mapped_count,
        "ExtractedPageCount": len(markdowns),
        "SelectedPages": " | ".join(extracted_urls or selected_pages),
        "ICPDisposition": icp,
        "ReviewAction": action,
        "EstimatedMCV": "" if mcv is None else mcv,
        "EstimatedMCVLow": "" if low is None else low,
        "EstimatedMCVHigh": "" if high is None else high,
        "EstimatedARR": "" if arr is None else arr,
        "ARRRange": arr_range,
        "Score": score,
        "Confidence": conf,
        "OfficeCount": office_count,
        "OfficeEvidence": " | ".join(office_evidence),
        "StaffCountVisible": staff_count,
        "StaffEvidence": " | ".join(staff_evidence),
        "WebsiteQualityScore": quality,
        "LegalEntityRoute": legal_profile.route,
        "LegalMarketFit": legal_profile.market_fit,
        "LegalEvidence": legal_profile.evidence,
        "ServiceSignals": ", ".join(services),
        "ToolSignals": ", ".join(tools),
        "MarketSignals": "; ".join(
            signal
            for signal in (
                "national/multistate language present"
                if re.search(r"national|nationwide|all 50 states|multi-state|multistate", combined_text, flags=re.I)
                else "",
                f"legal_market_fit={legal_profile.market_fit}" if legal_profile.is_law_firm else "",
            )
            if signal
        ),
        "Evidence": "; ".join(evidence_parts),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run no-write greenfield Nimble scoring.")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--raw-dir", required=True, type=Path)
    parser.add_argument("--nimble-cwd", type=Path, default=Path.cwd(), help=argparse.SUPPRESS)
    parser.add_argument("--max-rows", type=int, default=0)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--max-pages", type=int, default=4)
    parser.add_argument("--map-limit", type=int, default=30)
    parser.add_argument("--map-timeout", type=int, default=45)
    parser.add_argument("--extract-timeout", type=int, default=60)
    parser.add_argument("--no-resume", action="store_true")
    args = parser.parse_args()
    args.resume = not args.no_resume
    args.raw_dir.mkdir(parents=True, exist_ok=True)

    rows = read_csv(args.input)
    if args.max_rows:
        rows = rows[: args.max_rows]

    started = time.time()
    outputs: list[dict[str, object]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(process_row, row, args): row for row in rows}
        for index, future in enumerate(as_completed(futures), start=1):
            row = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # noqa: BLE001 - keep long runs resilient.
                result = {
                    "Id": clean(row.get("Id")),
                    "Name": clean(row.get("Name")),
                    "Website": clean(row.get("Website")),
                    "RetrievalStatus": "script_error",
                    "ICPDisposition": "indeterminate",
                    "ReviewAction": "insufficient_public_evidence",
                    "Confidence": "Low",
                    "LegalEntityRoute": "not_evaluated",
                    "LegalMarketFit": "",
                    "LegalEvidence": "script error before legal classification",
                    "Evidence": repr(exc),
                }
            outputs.append(result)
            log_row_progress(index, len(rows), result)

    order = {clean(row.get("Id")): idx for idx, row in enumerate(rows)}
    outputs.sort(key=lambda item: order.get(clean(item.get("Id")), 999999))
    write_csv(args.output, outputs)

    summary = {
        "input": str(args.input),
        "output": str(args.output),
        "raw_dir": str(args.raw_dir),
        "rows": len(outputs),
        "elapsed_seconds": round(time.time() - started, 2),
        "retrieval_status_counts": {},
        "review_action_counts": {},
        "confidence_counts": {},
    }
    for key, summary_key in (
        ("RetrievalStatus", "retrieval_status_counts"),
        ("ReviewAction", "review_action_counts"),
        ("Confidence", "confidence_counts"),
    ):
        counts: dict[str, int] = {}
        for row in outputs:
            value = clean(row.get(key)) or "(blank)"
            counts[value] = counts.get(value, 0) + 1
        summary[summary_key] = counts
    summary_path = args.output.with_name(args.output.stem + "_summary.json")
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
