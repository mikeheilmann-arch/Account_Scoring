#!/usr/bin/env python3
"""Build the no-write ICP/entity-quality overlay for the 2026-06-19 2k run.

This is an analysis overlay only. It reads local CSV artifacts, emits review
outputs under tmp/, and performs no Salesforce API calls or writeback creation.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT_DIR = ROOT / "tmp" / "icp_quality_agent_2k_20260626"

SCORES_PATH = ROOT / "tmp" / "certifid_scoring_gcp" / "batch2k-20260619-1645" / "batch2k_scores_combined_2026-06-19.csv"
REVIEW_PATH = ROOT / "tmp" / "certifid_scoring_gcp" / "batch2k-20260619-1645" / "batch2k_salesforce_review_2026-06-19.csv"
WRITEBACK_PATH = ROOT / "tmp" / "certifid_scoring_gcp" / "batch2k-20260619-1645" / "batch2k_sfdc_writeback_2026-06-19.csv"
COMPANY_TYPE_PATH = ROOT / "artifacts" / "account_minimal_company_type_2026-06-15.csv"
SFDC_ACCOUNTS_PATH = ROOT / "artifacts" / "2026-04-16_dedupe" / "certifid_accounts.csv"
ALTA_MATCH_PATH = ROOT / "tmp" / "alta_quality_agent_match_20260625" / "alta_to_sfdc_match_candidates.csv"
ALTA_OPPORTUNITIES_PATH = ROOT / "tmp" / "alta_quality_agent_match_20260625" / "alta_quality_agent_opportunities.csv"

OVERLAY_NAME = "icp_quality_agent_overlay_2k.csv"
DO_NOT_TRUST_NAME = "icp_quality_agent_do_not_trust_2k.csv"
SUMMARY_NAME = "icp_quality_agent_summary.json"
READOUT_NAME = "icp_quality_agent_readout.md"

OUTPUT_COLUMNS = [
    "AccountId",
    "AccountName",
    "Website",
    "WebsiteDomain",
    "BillingState",
    "OriginalAction",
    "OriginalEstimatedMCV",
    "OriginalEstimatedARR",
    "OriginalReviewRank",
    "QualityDisposition",
    "QualityAction",
    "QualityConfidence",
    "QualityReason",
    "QualityEvidence",
    "SourceFlags",
    "SuggestedSellableAccountId",
    "SuggestedSellableAccountName",
    "AltaMatchConfidence",
    "AltaMembershipType",
    "AltaName",
    "AltaState",
    "CrmType",
    "CrmAccountStatus",
    "CrmCompanyType",
    "DedupeParentId",
    "DedupeParentName",
    "DomainClusterSize2k",
    "DomainClusterSizeSfdc",
    "DomainCustomerAccountIds",
    "DomainCustomerAccountNames",
]

DO_NOT_TRUST_COLUMNS = OUTPUT_COLUMNS + [
    "DoNotTrustReason",
]

UNDERWRITER_DOMAINS = {
    "ctic.com": "Chicago Title / FNF underwriter domain",
    "chicagotitle.com": "Chicago Title underwriter domain",
    "fnf.com": "Fidelity National Financial underwriter domain",
    "fntg.com": "Fidelity National Title Group underwriter domain",
    "fidelitydesktop.com": "Fidelity title underwriter domain",
    "firstam.com": "First American underwriter domain",
    "stewart.com": "Stewart underwriter domain",
    "oldrepublictitle.com": "Old Republic Title underwriter domain",
    "wfgtitle.com": "WFG underwriter domain",
    "doma.com": "Doma underwriter domain",
    "natic.com": "North American Title Insurance underwriter domain",
    "westcorlandtitle.com": "Westcor underwriter domain",
    "trgc.com": "Title Resources underwriter domain",
}

OWNED_DIRECT_REVIEW_DOMAINS = {
    "atatitle.com": "ATA National direct/owned operation domain",
}

ROLLUP_REVIEW_DOMAINS = {
    "ltgc.com": "Land Title Guarantee parent-domain rollup",
    "ctot.com": "Capital Title of Texas parent-domain rollup",
}

ASSOCIATION_DIRECTORY_DOMAINS = {
    "wlta.org": "Wisconsin Land Title Association member directory",
}

KNOWN_WEBSITE_MISMATCH_DOMAINS = {
    "tghawaii.com": "Hawaii title entity domain on non-Hawaii Account",
}

KNOWN_ABSTRACT_ONLY_DOMAINS = {
    "retitleservice.com": "review-plan abstract/title-search-only example",
}

BANK_DOMAIN_RE = re.compile(r"(?:^|[-.])(bank|banks|creditunion|creditunions|cu|mortgage|mortgages|lending|lender|lenders)(?:[-.]|$)", re.I)
GOV_EVIDENCE_RE = re.compile(r"clerk|recorder|register of deeds|registerofdeeds|municipal", re.I)
BROKERAGE_DOMAIN_RE = re.compile(r"(?:^|[-.])(broker|brokerage|realtor|realty|properties|mls)(?:[-.]|$)", re.I)
BROKERAGE_TEXT_RE = re.compile(r"\b(broker|brokerage|realtor|realty|mls)\b", re.I)
TITLE_OPERATOR_RE = re.compile(r"title|escrow|settlement|closing|closings|abstract", re.I)
TOKEN_RE = re.compile(r"[a-z0-9]+")

GENERIC_HOST_SUFFIXES = {
    "wixsite.com",
    "wordpress.com",
    "weebly.com",
    "webflow.io",
    "squarespace.com",
    "godaddysites.com",
    "business.site",
    "sites.google.com",
}

GENERIC_CONTEXT_BLOCKLIST = {
    "bbb.org",
    "buzzfile.com",
    "chamberofcommerce.com",
    "dnb.com",
    "facebook.com",
    "hub.biz",
    "linkedin.com",
    "manta.com",
    "mapquest.com",
    "yellowpages.com",
    "yelp.com",
    "zoominfo.com",
}

FREE_EMAIL_DOMAINS = {
    "aol.com",
    "gmail.com",
    "hotmail.com",
    "icloud.com",
    "live.com",
    "outlook.com",
    "protonmail.com",
    "yahoo.com",
}

ICP_COMPANY_TYPES = {"title company", "law firm", "escrow company"}
NON_ICP_COMPANY_TYPE_TOKENS = {
    "bank",
    "broker",
    "brokerage",
    "county",
    "government",
    "mortgage",
    "software",
    "underwriter",
}

GENERIC_NAME_TOKENS = {
    "a",
    "agency",
    "and",
    "assoc",
    "associates",
    "co",
    "company",
    "corp",
    "corporation",
    "abstract",
    "closing",
    "closings",
    "escrow",
    "for",
    "group",
    "inc",
    "insurance",
    "land",
    "law",
    "legal",
    "llc",
    "llp",
    "ltd",
    "of",
    "services",
    "service",
    "settlement",
    "the",
    "title",
    "titles",
}

POSITIVE_ALTA_TYPES = {
    "Title Insurance Agents",
    "Title Abstracters",
    "Real Estate Attorneys (Agents)",
}
UNDERWRITER_ALTA_TYPES = {"Underwriters"}
ATTORNEY_NOT_AGENT_ALTA_TYPES = {"Real Estate Attorneys (not Agents)"}
CONF_ORDER = {"high": 3, "medium": 2, "low": 1, "": 0}


@dataclass(frozen=True)
class DomainContext:
    domain: str
    rows_2k: list[dict]
    rows_sfdc: list[dict]
    customer_rows: list[dict]
    suggested_id: str = ""
    suggested_name: str = ""


def read_csv(path: Path, required: bool = True) -> tuple[list[dict], list[str]]:
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Required input missing: {path}")
        return [], [f"Optional input missing: {path}"]
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh)), []


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def normalize_domain(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)
    host = (parsed.netloc or parsed.path.split("/")[0]).lower().strip()
    if "@" in host:
        host = host.rsplit("@", 1)[-1]
    if ":" in host:
        host = host.split(":", 1)[0]
    host = host.strip(".")
    if host.startswith("www."):
        host = host[4:]
    return host


def registered_domain(domain: str) -> str:
    domain = normalize_domain(domain)
    for suffix in GENERIC_HOST_SUFFIXES:
        if domain == suffix or domain.endswith("." + suffix):
            return domain
    parts = [p for p in domain.split(".") if p]
    if len(parts) <= 2:
        return domain
    second_level_suffixes = {"co.uk", "com.au", "com.br", "co.nz", "com.mx"}
    tail = ".".join(parts[-2:])
    if tail in second_level_suffixes and len(parts) >= 3:
        return ".".join(parts[-3:])
    return tail


def is_generic_context_domain(domain: str) -> bool:
    domain = normalize_domain(domain)
    return domain in GENERIC_CONTEXT_BLOCKLIST


def is_free_email_domain(domain: str) -> bool:
    return normalize_domain(domain) in FREE_EMAIL_DOMAINS


def company_type_value(crm: dict, dedupe: dict) -> str:
    return (crm.get("Company_Type__c") or dedupe.get("Company_Type__c") or "").strip()


def is_positive_icp_company_type(company_type: str) -> bool:
    return company_type.strip().lower() in ICP_COMPANY_TYPES


def is_non_icp_company_type(company_type: str) -> bool:
    lowered = company_type.strip().lower()
    return bool(lowered and any(token in lowered for token in NON_ICP_COMPANY_TYPE_TOKENS))


def truthy(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def numeric(value: str) -> float:
    try:
        return float(str(value or "").replace("$", "").replace(",", "").strip())
    except ValueError:
        return 0.0


def account_id_from_url(url: str) -> str:
    match = re.search(r"/Account/([A-Za-z0-9]{15,18})/", url or "")
    return match.group(1) if match else ""


def account_id_for_row(row: dict) -> str:
    return row.get("Id", "") or account_id_from_url(row.get("SalesforceUrl", ""))


def clean_tokens(value: str, drop_generic: bool = True) -> set[str]:
    tokens = set(TOKEN_RE.findall((value or "").lower()))
    if drop_generic:
        tokens = {t for t in tokens if t not in GENERIC_NAME_TOKENS and len(t) > 1}
    return tokens


def has_strong_entity_overlap(left: str, right: str, domain: str = "") -> bool:
    left_tokens = clean_tokens(left)
    right_tokens = clean_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    if normalize_name_for_lookup(left) and normalize_name_for_lookup(left) == normalize_name_for_lookup(right):
        return True
    overlap = left_tokens & right_tokens
    if len(overlap) >= 2:
        return True
    if len(overlap) == 1:
        token = next(iter(overlap))
        return len(token) >= 4 and token in domain_tokens(domain)
    return False


def normalize_name_for_lookup(value: str) -> str:
    value = " ".join(TOKEN_RE.findall((value or "").lower()))
    suffixes = {"inc", "llc", "llp", "ltd", "co", "corp", "corporation", "company"}
    tokens = [token for token in value.split() if token not in suffixes]
    return " ".join(tokens)


def domain_tokens(domain: str) -> set[str]:
    base = registered_domain(domain).split(".")[0]
    tokens = set(TOKEN_RE.findall(base))
    pieces = re.split(
        r"(title|escrow|settlement|closing|closings|abstract|law|legal|bank|county|realty|homes|capital|land|state)",
        base,
    )
    tokens.update(p for p in pieces if p and p.isalnum())
    return {t for t in tokens if len(t) > 1}


def is_gov_county_domain(domain: str, evidence_text: str) -> bool:
    domain = registered_domain(domain)
    base = domain.split(".")[0] if domain else ""
    suffix = domain.rsplit(".", 1)[-1] if "." in domain else ""
    if domain.endswith(".gov"):
        return True
    if "county" in base and suffix in {"org", "us", "gov"}:
        return True
    return bool("county" in base and GOV_EVIDENCE_RE.search(evidence_text or ""))


def is_bank_domain(domain: str) -> bool:
    domain = registered_domain(domain)
    base = domain.split(".")[0] if domain else ""
    return bool(BANK_DOMAIN_RE.search(domain) or base.endswith("bank"))


def is_brokerage_signal(
    domain: str,
    text: str,
    service_signals: str,
    company_type: str = "",
    alta_positive: bool = False,
) -> bool:
    """Detect real-estate brokerage/builder sites without catching title-agent ICPs."""
    domain = registered_domain(domain)
    title_operator_domain = bool(TITLE_OPERATOR_RE.search(domain))
    title_operator_services = bool(TITLE_OPERATOR_RE.search(service_signals or ""))
    if title_operator_domain or title_operator_services:
        return False

    if BROKERAGE_DOMAIN_RE.search(domain):
        return True

    if re.search(r"(?:^|[-.])homes?(?:[-.]|$)", domain):
        return True

    if BROKERAGE_TEXT_RE.search(text or ""):
        return True

    return False


def pick_best_alta(rows: list[dict]) -> dict:
    if not rows:
        return {}
    return sorted(
        rows,
        key=lambda r: (
            CONF_ORDER.get((r.get("match_confidence") or "").lower(), 0),
            numeric(r.get("match_score")),
        ),
        reverse=True,
    )[0]


def is_customer(row: dict) -> bool:
    return (
        (row.get("Type") or "").strip().lower() == "customer"
        or truthy(row.get("Active_Customer__c"))
        or numeric(row.get("Active_Subscription_Revenue__c")) > 0
        or "active customer" in (row.get("Account_Status__c") or "").strip().lower()
    )


def build_domain_contexts(scores: list[dict], sfdc_rows: list[dict]) -> dict[str, DomainContext]:
    rows_2k_by_domain: dict[str, list[dict]] = defaultdict(list)
    rows_sfdc_by_domain: dict[str, list[dict]] = defaultdict(list)

    for row in scores:
        domain = registered_domain(row.get("Website"))
        if domain:
            rows_2k_by_domain[domain].append(row)

    for row in sfdc_rows:
        candidates = [
            row.get("Website_Domain__c", ""),
            row.get("Company_Domain_Name__c", ""),
            row.get("Email_Domain__c", ""),
            row.get("Website", ""),
        ]
        seen = set()
        for candidate in candidates:
            domain = registered_domain(candidate)
            if domain and not is_free_email_domain(domain) and not is_generic_context_domain(domain) and domain not in seen:
                rows_sfdc_by_domain[domain].append(row)
                seen.add(domain)

    contexts: dict[str, DomainContext] = {}
    for domain in sorted(set(rows_2k_by_domain) | set(rows_sfdc_by_domain)):
        sfdc = rows_sfdc_by_domain.get(domain, [])
        customers = [r for r in sfdc if is_customer(r)]
        suggested = choose_suggested_sellable(domain, sfdc, customers)
        contexts[domain] = DomainContext(
            domain=domain,
            rows_2k=rows_2k_by_domain.get(domain, []),
            rows_sfdc=sfdc,
            customer_rows=customers,
            suggested_id=suggested.get("Id", ""),
            suggested_name=suggested.get("Name", ""),
        )
    return contexts


def choose_suggested_sellable(domain: str, sfdc_rows: list[dict], customers: list[dict]) -> dict:
    if customers:
        return sorted(customers, key=lambda r: (r.get("Name") or "").lower())[0]
    top_level = [r for r in sfdc_rows if not r.get("ParentId")]
    if domain in ROLLUP_REVIEW_DOMAINS and sfdc_rows:
        return sorted(top_level or sfdc_rows, key=lambda r: (r.get("Name") or "").lower())[0]
    if domain in UNDERWRITER_DOMAINS and sfdc_rows:
        with_parent = [r for r in sfdc_rows if r.get("ParentId")]
        return sorted(with_parent or sfdc_rows, key=lambda r: (r.get("Name") or "").lower())[0]
    if len(sfdc_rows) > 1:
        return sorted(top_level or sfdc_rows, key=lambda r: (r.get("Name") or "").lower())[0]
    return {}


def build_alta_index(alta_rows: list[dict]) -> dict[str, list[dict]]:
    by_id: dict[str, list[dict]] = defaultdict(list)
    for row in alta_rows:
        account_id = row.get("matched_account_id", "")
        if account_id:
            by_id[account_id].append(row)
    return by_id


def source_flags(
    row: dict,
    crm: dict,
    dedupe: dict,
    domain: str,
    domain_ctx: DomainContext | None,
    alta: dict,
) -> tuple[set[str], list[str]]:
    flags: set[str] = set()
    evidence: list[str] = []
    name = row.get("Name", "")
    website = row.get("Website", "")
    company_type = company_type_value(crm, dedupe)
    text = " ".join(
        [
            name,
            website,
            row.get("Evidence", ""),
            row.get("ServiceSignals", ""),
            row.get("ToolSignals", ""),
            row.get("SelectedPages", ""),
        ]
    )
    non_service_text = " ".join(
        [
            name,
            website,
            row.get("Evidence", ""),
            row.get("ToolSignals", ""),
            row.get("SelectedPages", ""),
        ]
    )

    alta_type = alta.get("alta_membership_type", "")
    if alta:
        match_conf = (alta.get("match_confidence") or "").lower()
        if alta_type in POSITIVE_ALTA_TYPES and match_conf in {"high", "medium"}:
            flags.add("alta_positive_icp")
            evidence.append(f"ALTA {match_conf} match: {alta_type} / {alta.get('alta_name', '')}")
        if alta_type in UNDERWRITER_ALTA_TYPES and match_conf in {"high", "medium"}:
            flags.add("alta_underwriter")
            evidence.append(f"ALTA {match_conf} underwriter match: {alta.get('alta_name', '')}")
        if alta_type == "Real Estate Attorneys (Agents)":
            flags.add("alta_attorney_agent")
        if alta_type in ATTORNEY_NOT_AGENT_ALTA_TYPES:
            flags.add("alta_attorney_not_agent")
            evidence.append(f"ALTA attorney-not-agent match: {alta.get('alta_name', '')}")

    if domain_ctx:
        if domain_ctx.customer_rows:
            matched_customer_rows = [
                r for r in domain_ctx.customer_rows if has_strong_entity_overlap(name, r.get("Name", ""), domain)
            ]
            if matched_customer_rows:
                flags.add("sfdc_customer_domain_match")
                names = ", ".join(r.get("Name", "") for r in matched_customer_rows[:3])
                evidence.append(f"Domain {domain} also belongs to same-entity customer account(s): {names}")
            else:
                flags.add("sfdc_customer_domain_shared_no_name_match")
                names = ", ".join(r.get("Name", "") for r in domain_ctx.customer_rows[:3])
                evidence.append(f"Domain {domain} is present on customer account(s), but without strong name overlap: {names}")
        if len(domain_ctx.rows_2k) > 1 or len(domain_ctx.rows_sfdc) > 1:
            flags.add("shared_domain_cluster")
            evidence.append(
                f"Domain cluster: {len(domain_ctx.rows_2k)} rows in 2k, {len(domain_ctx.rows_sfdc)} rows in SFDC export"
            )

    if company_type and is_positive_icp_company_type(company_type):
        flags.add("company_type_positive_icp")
        evidence.append(f"CRM Company_Type__c={company_type}")
    elif company_type and company_type.lower() not in ICP_COMPANY_TYPES:
        flags.add("company_type_conflict")
        evidence.append(f"CRM Company_Type__c={company_type}")
    if "underwriter" in company_type.lower():
        flags.add("company_type_underwriter")
    elif is_non_icp_company_type(company_type):
        flags.add("company_type_non_icp")

    if domain in UNDERWRITER_DOMAINS:
        flags.add("underwriter_domain")
        evidence.append(f"{domain}: {UNDERWRITER_DOMAINS[domain]}")
    if domain in OWNED_DIRECT_REVIEW_DOMAINS:
        flags.add("underwriter_domain")
        flags.add("owned_direct_domain")
        evidence.append(f"{domain}: {OWNED_DIRECT_REVIEW_DOMAINS[domain]}")
    if domain in ROLLUP_REVIEW_DOMAINS:
        flags.add("parent_context_present")
        evidence.append(f"{domain}: {ROLLUP_REVIEW_DOMAINS[domain]}")
    if domain in ASSOCIATION_DIRECTORY_DOMAINS:
        flags.add("association_directory_domain")
        evidence.append(f"{domain}: {ASSOCIATION_DIRECTORY_DOMAINS[domain]}")
    if domain in KNOWN_WEBSITE_MISMATCH_DOMAINS:
        flags.add("website_account_name_mismatch")
        evidence.append(f"{domain}: {KNOWN_WEBSITE_MISMATCH_DOMAINS[domain]}")

    positive_icp_context = "alta_positive_icp" in flags or "company_type_positive_icp" in flags
    title_operator_domain_context = bool(TITLE_OPERATOR_RE.search(" ".join([domain, row.get("ServiceSignals", "") or ""])))
    if is_bank_domain(domain) and not title_operator_domain_context:
        flags.add("bank_domain")
        evidence.append(f"Bank/lender domain pattern: {domain}")
    if is_gov_county_domain(domain, text):
        flags.add("gov_county_domain")
        evidence.append(f"Government/county domain pattern: {domain}")
    if is_brokerage_signal(
        domain,
        non_service_text,
        row.get("ServiceSignals", ""),
        company_type=company_type,
        alta_positive="alta_positive_icp" in flags,
    ):
        flags.add("real_estate_brokerage_domain")
        evidence.append("Brokerage/real-estate site pattern in domain or scorer evidence")

    services = (row.get("ServiceSignals") or "").lower()
    selected = (row.get("SelectedPages") or "").lower()
    if domain in KNOWN_ABSTRACT_ONLY_DOMAINS or (
        "abstract" in services
        and not any(token in services for token in ["escrow", "closing", "settlement"])
        and "search" in (row.get("Evidence", "") or selected)
    ):
        flags.add("abstract_only_signal")
        evidence.append("Abstract/search-only signal; no strong closing/escrow counter-signal")

    account_tokens = clean_tokens(name)
    site_tokens = domain_tokens(domain)
    overlap = account_tokens & site_tokens
    if account_tokens and not overlap:
        flags.add("website_account_name_mismatch")
        evidence.append(f"No non-generic Account-name token overlap with website domain {domain}")

    if dedupe.get("ParentId") or dedupe.get("Parent.Name"):
        flags.add("parent_context_present")
        evidence.append(f"SFDC parent context: {dedupe.get('Parent.Name', '') or dedupe.get('ParentId', '')}")

    retrieval = (row.get("RetrievalStatus") or "").lower()
    if retrieval and retrieval != "ok":
        flags.add("retrieval_not_ok")
        evidence.append(f"RetrievalStatus={row.get('RetrievalStatus')}")

    return flags, evidence


def classify(row: dict, crm: dict, dedupe: dict, domain_ctx: DomainContext | None, alta: dict) -> dict:
    domain = registered_domain(row.get("Website"))
    flags, evidence = source_flags(row, crm, dedupe, domain, domain_ctx, alta)
    original_action = row.get("ReviewAction", "") or row.get("Action", "")
    retrieval = (row.get("RetrievalStatus") or "").lower()
    website_quality = numeric(row.get("WebsiteQualityScore"))
    office_count = numeric(row.get("OfficeCount"))
    positive_icp_context = "alta_positive_icp" in flags or "company_type_positive_icp" in flags

    disposition = "scoreable"
    action = "allow_score"
    confidence = "Medium"
    reason = "No deterministic suppress/review signal fired."
    suggested_id = ""
    suggested_name = ""

    if domain_ctx:
        suggested_id = domain_ctx.suggested_id
        suggested_name = domain_ctx.suggested_name

    if domain in OWNED_DIRECT_REVIEW_DOMAINS or "owned_direct_domain" in flags:
        disposition = "underwriter_or_direct_side_review"
        action = "route_ops_review"
        confidence = "High"
        reason = "Account is bound to an underwriter/direct-side domain; score should not be trusted as a standalone greenfield entity."
    elif domain in UNDERWRITER_DOMAINS or "alta_underwriter" in flags or "company_type_underwriter" in flags:
        disposition = "underwriter_or_direct_side_review" if positive_icp_context else "suppress_non_icp"
        action = "route_ops_review" if positive_icp_context else "suppress_score"
        confidence = "High"
        reason = "Underwriter/non-sellable entity signal."
    elif "company_type_non_icp" in flags and not positive_icp_context:
        disposition = "suppress_non_icp"
        action = "suppress_score"
        confidence = "High"
        reason = "CRM Company_Type__c indicates a non-sellable entity class."
    elif "sfdc_customer_domain_match" in flags:
        disposition = "existing_customer_duplicate_review"
        action = "route_ops_review"
        confidence = "High"
        reason = "Website domain is already attached to an active/customer SFDC account."
    elif dedupe.get("ParentId") or domain in ROLLUP_REVIEW_DOMAINS:
        disposition = "rollup_or_duplicate_review"
        action = "roll_to_parent_review"
        confidence = "High"
        reason = "Likely child/branch/domain-rollup account; score should be reviewed at sellable parent grain."
        if dedupe.get("ParentId"):
            suggested_id = dedupe.get("ParentId", suggested_id)
            suggested_name = dedupe.get("Parent.Name", suggested_name)
    elif "bank_domain" in flags:
        disposition = "website_mismatch_review" if positive_icp_context else "suppress_wrong_website"
        action = "website_review" if positive_icp_context else "suppress_score"
        confidence = "Medium" if positive_icp_context else "High"
        reason = "Account website is a bank/lender domain, so scorer evidence is bound to the wrong entity class."
    elif "gov_county_domain" in flags:
        disposition = "website_mismatch_review" if positive_icp_context else "suppress_wrong_website"
        action = "website_review" if positive_icp_context else "suppress_score"
        confidence = "Medium" if positive_icp_context else "High"
        reason = "Account website is a government/county domain, so scorer evidence is bound to the wrong entity."
    elif "association_directory_domain" in flags:
        disposition = "suppress_wrong_website"
        action = "suppress_score"
        confidence = "High"
        reason = "Account website is an association/directory domain, not the sellable Account."
    elif domain in KNOWN_WEBSITE_MISMATCH_DOMAINS:
        disposition = "website_mismatch_review"
        action = "website_review"
        confidence = "High"
        reason = "Known state/entity binding conflict from the approved 2k review plan."
    elif "real_estate_brokerage_domain" in flags:
        disposition = "ops_review" if positive_icp_context else "suppress_non_icp"
        action = "route_ops_review" if positive_icp_context else "suppress_score"
        confidence = "High" if "website_account_name_mismatch" in flags else "Medium"
        reason = "Website/scorer evidence reads as real-estate brokerage/builder rather than title/settlement ICP."
    elif "abstract_only_signal" in flags:
        disposition = "abstract_only_review"
        action = "route_ops_review"
        confidence = "Medium"
        reason = "Abstract/title-search-only signal should be reviewed before trusting score."
    elif original_action in {"insufficient_public_evidence", "search_fallback_needed", "url_enrichment_needed"}:
        disposition = "insufficient_public_evidence"
        action = "route_ops_review"
        confidence = "High"
        reason = f"Original scorer action was {original_action}; no new source-backed pass signal."
    elif original_action == "non_icp_confirmed":
        disposition = "suppress_non_icp"
        action = "suppress_score"
        confidence = "High"
        reason = "Original scorer already classified the account as non-ICP."
    elif retrieval and retrieval != "ok":
        disposition = "insufficient_public_evidence"
        action = "route_ops_review"
        confidence = "Medium"
        reason = "Retrieval did not produce enough public evidence."
    elif website_quality and website_quality <= 2 and office_count >= 20:
        disposition = "ops_review"
        action = "route_ops_review"
        confidence = "Medium"
        reason = "Thin/low-quality site with high office count; value estimate should be reviewed before trusting."
    elif "website_account_name_mismatch" in flags and office_count >= 20 and original_action == "score_now":
        disposition = "website_mismatch_review"
        action = "website_review"
        confidence = "Medium"
        reason = "High-value score has weak Account-name to website-domain binding."
    elif "alta_positive_icp" in flags:
        confidence = "High"
        reason = "ALTA high/medium match supports ICP, with no deterministic suppress/review signal."

    if not suggested_id and dedupe.get("Enterprise_ID__c") and dedupe.get("Enterprise_Name__c"):
        suggested_name = dedupe.get("Enterprise_Name__c", "")

    return {
        "QualityDisposition": disposition,
        "QualityAction": action,
        "QualityConfidence": confidence,
        "QualityReason": reason,
        "QualityEvidence": " | ".join(dict.fromkeys(evidence))[:3000],
        "SourceFlags": ";".join(sorted(flags)),
        "SuggestedSellableAccountId": suggested_id,
        "SuggestedSellableAccountName": suggested_name,
    }


def build_overlay(args: argparse.Namespace) -> tuple[list[dict], dict]:
    warnings: list[str] = []
    scores, new_warnings = read_csv(args.scores)
    warnings.extend(new_warnings)
    review_rows, new_warnings = read_csv(args.review, required=False)
    warnings.extend(new_warnings)
    company_rows, new_warnings = read_csv(args.company_type, required=False)
    warnings.extend(new_warnings)
    sfdc_rows, new_warnings = read_csv(args.sfdc_accounts, required=False)
    warnings.extend(new_warnings)
    alta_rows, new_warnings = read_csv(args.alta_matches, required=False)
    warnings.extend(new_warnings)
    opportunities, new_warnings = read_csv(args.alta_opportunities, required=False)
    warnings.extend(new_warnings)

    review_by_url = {r.get("SalesforceUrl", ""): r for r in review_rows if r.get("SalesforceUrl")}
    review_by_id = {account_id_for_row(r): r for r in review_rows if account_id_for_row(r)}
    company_by_id = {r.get("Id", ""): r for r in company_rows if r.get("Id")}
    sfdc_by_id = {r.get("Id", ""): r for r in sfdc_rows if r.get("Id")}
    alta_by_id = build_alta_index(alta_rows)
    domain_contexts = build_domain_contexts(scores, sfdc_rows)

    output: list[dict] = []
    for score in scores:
        account_id = account_id_for_row(score)
        review = review_by_url.get(score.get("SalesforceUrl", "")) or review_by_id.get(account_id, {})
        crm = company_by_id.get(account_id, {})
        dedupe = sfdc_by_id.get(account_id, {})
        domain = registered_domain(score.get("Website") or review.get("Website", ""))
        domain_ctx = domain_contexts.get(domain)
        alta = pick_best_alta(alta_by_id.get(account_id, []))
        classification = classify(score, crm, dedupe, domain_ctx, alta)

        row = {
            "AccountId": account_id,
            "AccountName": score.get("Name", "") or review.get("Name", ""),
            "Website": score.get("Website", "") or review.get("Website", ""),
            "WebsiteDomain": domain,
            "BillingState": score.get("BillingState", "") or score.get("State", "") or review.get("State", ""),
            "OriginalAction": score.get("ReviewAction", "") or score.get("Action", "") or review.get("Action", ""),
            "OriginalEstimatedMCV": score.get("EstimatedMCV", "") or review.get("EstimatedMCV", ""),
            "OriginalEstimatedARR": score.get("EstimatedARR", "") or review.get("EstimatedARR", ""),
            "OriginalReviewRank": review.get("ReviewRank", ""),
            "AltaMatchConfidence": alta.get("match_confidence", ""),
            "AltaMembershipType": alta.get("alta_membership_type", ""),
            "AltaName": alta.get("alta_name", ""),
            "AltaState": alta.get("alta_state", ""),
            "CrmType": crm.get("Type", "") or dedupe.get("Type", ""),
            "CrmAccountStatus": crm.get("Account_Status__c", "") or dedupe.get("Account_Status__c", ""),
            "CrmCompanyType": crm.get("Company_Type__c", "") or dedupe.get("Company_Type__c", ""),
            "DedupeParentId": dedupe.get("ParentId", ""),
            "DedupeParentName": dedupe.get("Parent.Name", ""),
            "DomainClusterSize2k": str(len(domain_ctx.rows_2k) if domain_ctx else 0),
            "DomainClusterSizeSfdc": str(len(domain_ctx.rows_sfdc) if domain_ctx else 0),
            "DomainCustomerAccountIds": ";".join(r.get("Id", "") for r in (domain_ctx.customer_rows if domain_ctx else [])),
            "DomainCustomerAccountNames": ";".join(r.get("Name", "") for r in (domain_ctx.customer_rows if domain_ctx else [])),
        }
        row.update(classification)
        output.append(row)

    summary = summarize(
        output,
        scores,
        review_rows,
        company_rows,
        sfdc_rows,
        alta_rows,
        opportunities,
        warnings,
        args.out_dir,
        args,
    )
    return output, summary


def summarize(
    rows: list[dict],
    scores: list[dict],
    review_rows: list[dict],
    company_rows: list[dict],
    sfdc_rows: list[dict],
    alta_rows: list[dict],
    opportunities: list[dict],
    warnings: list[str],
    out_dir: Path,
    args: argparse.Namespace,
) -> dict:
    ids = [r.get("AccountId", "") for r in rows]
    duplicate_ids = sorted(account_id for account_id, count in Counter(ids).items() if account_id and count > 1)
    missing_disposition = [r.get("AccountId", "") for r in rows if not r.get("QualityDisposition") or not r.get("QualityAction")]
    writeback_outputs = sorted(p.name for p in out_dir.glob("*writeback*")) if out_dir.exists() else []
    do_not_trust = [r for r in rows if is_do_not_trust(r)]
    score_now_rows = [r for r in rows if r.get("OriginalAction") == "score_now"]

    return {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "script": str(Path(__file__).relative_to(ROOT)),
        "inputs": {
            "scores": {"path": relative_or_abs(args.scores), "rows": len(scores)},
            "review": {"path": relative_or_abs(args.review), "rows": len(review_rows)},
            "company_type": {"path": relative_or_abs(args.company_type), "rows": len(company_rows)},
            "sfdc_accounts": {"path": relative_or_abs(args.sfdc_accounts), "rows": len(sfdc_rows)},
            "alta_matches": {"path": relative_or_abs(args.alta_matches), "rows": len(alta_rows)},
            "alta_opportunities": {"path": relative_or_abs(args.alta_opportunities), "rows": len(opportunities)},
        },
        "warnings": warnings,
        "row_counts": {
            "overlay_rows": len(rows),
            "score_now_rows": len(score_now_rows),
            "do_not_trust_rows": len(do_not_trust),
            "do_not_trust_score_now_rows": sum(1 for r in do_not_trust if r.get("OriginalAction") == "score_now"),
        },
        "counts": {
            "by_disposition": dict(Counter(r.get("QualityDisposition", "") for r in rows)),
            "by_action": dict(Counter(r.get("QualityAction", "") for r in rows)),
            "by_confidence": dict(Counter(r.get("QualityConfidence", "") for r in rows)),
            "source_flags": dict(Counter(flag for r in rows for flag in r.get("SourceFlags", "").split(";") if flag)),
            "do_not_trust_by_disposition": dict(Counter(r.get("QualityDisposition", "") for r in do_not_trust)),
        },
        "validation": {
            "row_count_reconciles_to_input": len(rows) == len(scores),
            "no_duplicate_account_ids": not duplicate_ids,
            "duplicate_account_ids": duplicate_ids,
            "all_rows_have_disposition_action": not missing_disposition,
            "missing_disposition_or_action_ids": missing_disposition,
            "no_unintended_writeback_output": not writeback_outputs,
            "writeback_named_outputs_in_out_dir": writeback_outputs,
        },
        "will_review_examples": will_examples(rows),
    }


def count_csv_rows(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return sum(1 for _ in csv.DictReader(fh))


def relative_or_abs(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def is_do_not_trust(row: dict) -> bool:
    return row.get("QualityAction") == "suppress_score"


WILL_EXAMPLE_NAMES = [
    "Title Companies In",
    "Tennessee Title",
    "SB. Titles",
    "Northern New York Title Agency",
    "Metro Title and Escrow",
    "Meridian Title and Research",
    "Master Settlement Services",
    "Greenridge Title Agency",
    "Greco Title Agency",
    "Global Title and Escrow Services",
    "Denver Land Title",
    "Clear Title Co.",
    "Apex Title and Settlement Services",
    "Absolute Title",
    "Title Guaranty",
    "Texas Capital Title",
    "Supreme Title Closings",
    "Real Estate Title Service",
    "Ionia County Title",
]

WILL_EXAMPLE_DOMAINS = {
    "Title Companies In": "wlta.org",
    "Tennessee Title": "tennesseetitle.com",
    "SB. Titles": "yourstatebank.com",
    "Northern New York Title Agency": "ctic.com",
    "Metro Title and Escrow": "metrotitleandescrow.com",
    "Meridian Title and Research": "meridiantitle.com",
    "Master Settlement Services": "nextierbank.com",
    "Greenridge Title Agency": "greenridge.com",
    "Greco Title Agency": "atatitle.com",
    "Global Title and Escrow Services": "gtes.exchange",
    "Denver Land Title": "ltgc.com",
    "Clear Title Co.": "cleartitlecompany.com",
    "Apex Title and Settlement Services": "apex-closings.com",
    "Absolute Title": "atatitle.com",
    "Title Guaranty": "tghawaii.com",
    "Texas Capital Title": "ctot.com",
    "Supreme Title Closings": "supremetitlellc.com",
    "Real Estate Title Service": "retitleservice.com",
    "Ionia County Title": "ioniacounty.org",
}


def will_examples(rows: list[dict]) -> list[dict]:
    examples: list[dict] = []
    for name in WILL_EXAMPLE_NAMES:
        name_norm = normalize_name_for_lookup(name)
        matches = []
        expected_domain = WILL_EXAMPLE_DOMAINS.get(name, "")
        for row in rows:
            row_name = row.get("AccountName", "")
            row_norm = normalize_name_for_lookup(row_name)
            if row_name == name or row_norm == name_norm or row_norm.startswith(name_norm + " "):
                matches.append(row)
        if expected_domain:
            domain_matches = [r for r in rows if r.get("WebsiteDomain") == expected_domain and normalize_name_for_lookup(r.get("AccountName", "")).startswith(name_norm)]
            matches = domain_matches or [r for r in matches if r.get("WebsiteDomain") == expected_domain]
        for row in matches[:2]:
            examples.append(
                {
                    "AccountName": row.get("AccountName", ""),
                    "AccountId": row.get("AccountId", ""),
                    "WebsiteDomain": row.get("WebsiteDomain", ""),
                    "OriginalReviewRank": row.get("OriginalReviewRank", ""),
                    "QualityDisposition": row.get("QualityDisposition", ""),
                    "QualityAction": row.get("QualityAction", ""),
                    "QualityConfidence": row.get("QualityConfidence", ""),
                    "SourceFlags": row.get("SourceFlags", ""),
                    "SuggestedSellableAccountName": row.get("SuggestedSellableAccountName", ""),
                }
            )
    return examples


def build_do_not_trust(rows: list[dict]) -> list[dict]:
    output: list[dict] = []
    for row in rows:
        if not is_do_not_trust(row):
            continue
        copy = dict(row)
        copy["DoNotTrustReason"] = f"{row.get('QualityDisposition')}: {row.get('QualityReason')}"
        output.append(copy)
    return sorted(
        output,
        key=lambda r: (
            0 if r.get("OriginalAction") == "score_now" else 1,
            int(r.get("OriginalReviewRank") or 999999),
            r.get("AccountName", ""),
        ),
    )


def write_summary(path: Path, summary: dict) -> None:
    path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")


def write_readout(path: Path, rows: list[dict], do_not_trust: list[dict], summary: dict) -> None:
    counts = summary["counts"]
    validation = summary["validation"]
    examples = summary["will_review_examples"]
    top_examples = [
        r
        for r in do_not_trust
        if r.get("OriginalAction") == "score_now" and int(r.get("OriginalReviewRank") or 999999) <= 50
    ][:12]

    lines = [
        "# ICP Quality Agent Overlay - 2k No-Write Run",
        "",
        f"Generated: {summary['generated_at_utc']}",
        "",
        "## Scope",
        "",
        "This overlay gates the 2026-06-19 2k scoring run before value-score trust. It reads only local artifacts and does not create Salesforce writeback files, call Salesforce APIs, deploy services, or change scorer behavior.",
        "",
        "## Headline Counts",
        "",
        f"- Overlay rows: {summary['row_counts']['overlay_rows']}",
        f"- Original score_now rows: {summary['row_counts']['score_now_rows']}",
        f"- Do-not-trust rows: {summary['row_counts']['do_not_trust_rows']}",
        f"- Do-not-trust rows from original score_now: {summary['row_counts']['do_not_trust_score_now_rows']}",
        "",
        "### Dispositions",
        "",
        "| Disposition | Rows |",
        "|---|---:|",
    ]
    for disposition, count in sorted(counts["by_disposition"].items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| {disposition} | {count} |")

    lines.extend(
        [
            "",
            "### Actions",
            "",
            "| Action | Rows |",
            "|---|---:|",
        ]
    )
    for action, count in sorted(counts["by_action"].items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| {action} | {count} |")

    lines.extend(
        [
            "",
            "### Source Flags",
            "",
            "| Flag | Rows |",
            "|---|---:|",
        ]
    )
    for flag, count in sorted(counts["source_flags"].items(), key=lambda item: (-item[1], item[0]))[:25]:
        lines.append(f"| {flag} | {count} |")

    lines.extend(
        [
            "",
            "## High-Priority Examples",
            "",
            "| Rank | Account | Domain | Disposition | Action | Evidence |",
            "|---:|---|---|---|---|---|",
        ]
    )
    for row in top_examples:
        evidence = (row.get("QualityEvidence") or row.get("QualityReason") or "").replace("|", "/")[:180]
        lines.append(
            f"| {row.get('OriginalReviewRank', '')} | {row.get('AccountName', '')} | {row.get('WebsiteDomain', '')} | "
            f"{row.get('QualityDisposition', '')} | {row.get('QualityAction', '')} | {evidence} |"
        )

    lines.extend(
        [
            "",
            "## Will-Reviewed Examples",
            "",
            "| Rank | Account | Domain | Disposition | Action | Confidence | Suggested sellable | Flags |",
            "|---:|---|---|---|---|---|---|---|",
        ]
    )
    for row in examples:
        flags = row.get("SourceFlags", "").replace("|", "/")
        lines.append(
            f"| {row.get('OriginalReviewRank', '')} | {row.get('AccountName', '')} | {row.get('WebsiteDomain', '')} | "
            f"{row.get('QualityDisposition', '')} | {row.get('QualityAction', '')} | {row.get('QualityConfidence', '')} | "
            f"{row.get('SuggestedSellableAccountName', '')} | {flags} |"
        )

    lines.extend(
        [
            "",
            "## Validation",
            "",
            "| Check | Pass |",
            "|---|---:|",
        ]
    )
    for check, passed in validation.items():
        if isinstance(passed, bool):
            lines.append(f"| {check} | {passed} |")

    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- This is deterministic and conservative; it favors suppress/review over allowing ambiguous high-value rows.",
            "- ALTA is used as source-backed ICP or review evidence only; it does not prove the Salesforce website is correct.",
            "- The SFDC/dedupe exports are local snapshots, so customer/domain/parent findings should be refreshed before any production action.",
            "- Website mismatch is based on local scorer evidence, domains, CRM context, ALTA match context, and approved review-plan patterns, not a fresh browser or portal review.",
            "- Shared-domain clusters are exposed as flags; only customer-domain, known rollup, parent, underwriter, or strong non-ICP signals change the disposition.",
            "",
            "## Recommended Next Steps",
            "",
            "1. Have Will/Amanda review the do-not-trust CSV, starting with original `score_now` rows in the top 50 ranks.",
            "2. Confirm the underwriter/direct-side policy for ATA/owned operations before turning review outcomes into scorer gates.",
            "3. Refresh read-only SFDC account/domain/parent/customer context before any later writeback or suppression action.",
            "4. Promote accepted deterministic rules into a pre-scoring module only after overlay review, keeping value scoring unchanged until the gate passes.",
        ]
    )

    if summary.get("warnings"):
        lines.extend(["", "## Input Warnings", ""])
        lines.extend(f"- {warning}" for warning in summary["warnings"])

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scores", type=Path, default=SCORES_PATH)
    parser.add_argument("--review", type=Path, default=REVIEW_PATH)
    parser.add_argument("--company-type", type=Path, default=COMPANY_TYPE_PATH)
    parser.add_argument("--sfdc-accounts", type=Path, default=SFDC_ACCOUNTS_PATH)
    parser.add_argument("--alta-matches", type=Path, default=ALTA_MATCH_PATH)
    parser.add_argument("--alta-opportunities", type=Path, default=ALTA_OPPORTUNITIES_PATH)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    rows, summary = build_overlay(args)
    do_not_trust = build_do_not_trust(rows)

    write_csv(args.out_dir / OVERLAY_NAME, rows, OUTPUT_COLUMNS)
    write_csv(args.out_dir / DO_NOT_TRUST_NAME, do_not_trust, DO_NOT_TRUST_COLUMNS)

    # Recompute validation after writing the allowed outputs to catch accidental files.
    summary = summarize(
        rows,
        read_csv(args.scores)[0],
        read_csv(args.review, required=False)[0],
        read_csv(args.company_type, required=False)[0],
        read_csv(args.sfdc_accounts, required=False)[0],
        read_csv(args.alta_matches, required=False)[0],
        read_csv(args.alta_opportunities, required=False)[0],
        summary.get("warnings", []),
        args.out_dir,
        args,
    )
    write_summary(args.out_dir / SUMMARY_NAME, summary)
    write_readout(args.out_dir / READOUT_NAME, rows, do_not_trust, summary)

    print(f"Wrote {len(rows)} overlay rows to {args.out_dir / OVERLAY_NAME}")
    print(f"Wrote {len(do_not_trust)} do-not-trust rows to {args.out_dir / DO_NOT_TRUST_NAME}")
    print(json.dumps(summary["validation"], indent=2, sort_keys=True))
    return 0 if all(v for v in summary["validation"].values() if isinstance(v, bool)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
