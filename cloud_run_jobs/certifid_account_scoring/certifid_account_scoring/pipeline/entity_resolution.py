"""Deterministic V1 Account-to-website entity resolution.

The resolver consumes dictionaries so it can be applied directly to the July
Salesforce snapshot joined to cached website/registry evidence.  It performs
no I/O and never crawls a site.

Expected input fields (common aliases are accepted):

* Account identity: ``Id``, ``Name``, optional legal/DBA/alias names.
* Candidate site: ``Website`` plus cached ``SiteOrganizationName``,
  ``PageTitle``, ``AboutText``, ``ContactText``, ``SiteAddresses``,
  ``SiteCity``, ``SiteState``, ``SiteCountry`` and ``SitePhones``.
* CRM context: ``Company_Type__c``, lifecycle/type, billing address/phone,
  parent/customer/domain context and website-hygiene fields.
* Registry/ALTA: registry names, jurisdiction, website and source date;
  ``ALTA_Member``/``ALTA_Member__c``, match confidence, name and state.
* Provenance: ``EvidenceAsOf``/``ExtractionTimestamp`` and optional source
  versions supplied to :func:`resolve_website_binding`.

Missing evidence is not defaulted to positive.  ALTA can corroborate entity
identity only when membership, confidence, name and jurisdiction semantics
pass; it is never counted as the sole proof that a website is owned by the
Account.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable, Mapping, Sequence
from urllib.parse import urlparse

from .config import DEFAULT_POLICY, RESOLVER_VERSION, V1Policy
from .contracts import BindingStatus, Confidence, EvidenceCitation, WebsiteBinding

try:  # Prefer a complete packaged Public Suffix List without network access.
    import tldextract  # type: ignore

    _TLD_EXTRACT = tldextract.TLDExtract(suffix_list_urls=())
except ImportError:  # pragma: no cover - exercised in environments without it.
    _TLD_EXTRACT = None


ENTITY_RESOLUTION_VERSION = RESOLVER_VERSION

US_STATE_CODES = {
    "alabama": "al", "alaska": "ak", "arizona": "az", "arkansas": "ar",
    "california": "ca", "colorado": "co", "connecticut": "ct", "delaware": "de",
    "district of columbia": "dc", "florida": "fl", "georgia": "ga", "hawaii": "hi",
    "idaho": "id", "illinois": "il", "indiana": "in", "iowa": "ia", "kansas": "ks",
    "kentucky": "ky", "louisiana": "la", "maine": "me", "maryland": "md",
    "massachusetts": "ma", "michigan": "mi", "minnesota": "mn", "mississippi": "ms",
    "missouri": "mo", "montana": "mt", "nebraska": "ne", "nevada": "nv",
    "new hampshire": "nh", "new jersey": "nj", "new mexico": "nm", "new york": "ny",
    "north carolina": "nc", "north dakota": "nd", "ohio": "oh", "oklahoma": "ok",
    "oregon": "or", "pennsylvania": "pa", "rhode island": "ri",
    "south carolina": "sc", "south dakota": "sd", "tennessee": "tn", "texas": "tx",
    "utah": "ut", "vermont": "vt", "virginia": "va", "washington": "wa",
    "west virginia": "wv", "wisconsin": "wi", "wyoming": "wy",
}

LEGAL_SUFFIXES = {
    "co",
    "company",
    "corp",
    "corporation",
    "inc",
    "incorporated",
    "limited",
    "llc",
    "llp",
    "lp",
    "ltd",
    "pc",
    "plc",
    "pllc",
}
WEAK_NAME_TOKENS = LEGAL_SUFFIXES | {
    "a",
    "and",
    "at",
    "dba",
    "for",
    "of",
    "the",
}
INDUSTRY_NAME_TOKENS = {
    "abstract",
    "agency",
    "attorney",
    "closing",
    "escrow",
    "firm",
    "group",
    "insurance",
    "law",
    "legal",
    "realty",
    "settlement",
    "title",
}

# These are host *classes*, not customer/example controls.  A tenant path or
# subdomain on one of these services cannot establish entity identity by domain.
GENERIC_HOSTS = {
    "business.site",
    "carrd.co",
    "godaddysites.com",
    "google.com",
    "hub.biz",
    "notion.site",
    "sites.google.com",
    "squarespace.com",
    "webflow.io",
    "weebly.com",
    "wix.com",
    "wixsite.com",
    "wordpress.com",
}
FREE_EMAIL_HOSTS = {
    "aol.com",
    "gmail.com",
    "hotmail.com",
    "icloud.com",
    "outlook.com",
    "yahoo.com",
}
DIRECTORY_PROFILE_HOSTS = {
    "alignable.com",
    "avvo.com",
    "bbb.org",
    "chamberofcommerce.com",
    "facebook.com",
    "findlaw.com",
    "instagram.com",
    "justia.com",
    "lawyers.com",
    "linkedin.com",
    "manta.com",
    "mapquest.com",
    "martindale.com",
    "realtor.com",
    "yellowpages.com",
    "yelp.com",
    "zoominfo.com",
}

# Conservative fallback for common multi-label public suffixes.  The normal
# runtime uses tldextract; this prevents the old "last two labels" error when
# that optional library is unavailable.
MULTI_LABEL_PUBLIC_SUFFIXES = {
    "ac.uk",
    "co.in",
    "co.jp",
    "co.nz",
    "co.uk",
    "com.au",
    "com.br",
    "com.mx",
    "com.sg",
    "com.tr",
    "com.tw",
    "gov.uk",
    "net.au",
    "org.au",
    "org.uk",
}

US_COUNTRIES = {"united states", "united states of america", "us", "usa", "u s", "u s a"}


@dataclass(frozen=True)
class DomainParts:
    """Public-suffix-aware decomposition of a candidate URL or host."""

    host: str
    registered_domain: str
    public_suffix: str
    subdomain: str
    is_generic_host: bool
    is_directory_profile: bool


def _clean(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _first(row: Mapping[str, object], names: Sequence[str]) -> str:
    for name in names:
        if name in row:
            value = _clean(row[name])
            if value:
                return value
    return ""


def _values(value: object) -> list[str]:
    """Flatten JSON, iterables, and common delimited strings to text values."""

    if value is None:
        return []
    if isinstance(value, Mapping):
        result: list[str] = []
        for nested in value.values():
            result.extend(_values(nested))
        return result
    if isinstance(value, (list, tuple, set)):
        result = []
        for nested in value:
            result.extend(_values(nested))
        return result
    text = _clean(value)
    if not text:
        return []
    if text[:1] in "[{":
        try:
            return _values(json.loads(text))
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return [part.strip() for part in re.split(r"[|;\n]+", text) if part.strip()]


def _field_values(row: Mapping[str, object], names: Sequence[str]) -> list[str]:
    result: list[str] = []
    for name in names:
        if name in row:
            result.extend(_values(row[name]))
    return list(dict.fromkeys(result))


def _truth(value: object) -> bool:
    return _clean(value).casefold() in {"1", "true", "t", "yes", "y"}


def _number(value: object) -> float:
    try:
        return float(_clean(value).replace(",", "").replace("$", ""))
    except ValueError:
        return 0.0


def _ascii(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")


def _tokens(value: str) -> tuple[str, ...]:
    normalized = _ascii(value).casefold().replace("&", " and ")
    return tuple(re.findall(r"[a-z0-9]+", normalized))


def normalize_entity_name(value: object) -> str:
    """Normalize legal/DBA names while removing only legal-form noise."""

    tokens = [token for token in _tokens(_clean(value)) if token not in WEAK_NAME_TOKENS]
    return " ".join(tokens)


def _distinctive_tokens(value: str) -> set[str]:
    return {
        token
        for token in _tokens(value)
        if token not in WEAK_NAME_TOKENS
        and token not in INDUSTRY_NAME_TOKENS
        and len(token) >= 3
    }


def _name_similarity(left: str, right: str) -> float:
    left_norm = normalize_entity_name(left)
    right_norm = normalize_entity_name(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    if len(left_norm) >= 8 and re.search(rf"\b{re.escape(left_norm)}\b", right_norm):
        return 0.97
    if len(right_norm) >= 8 and re.search(rf"\b{re.escape(right_norm)}\b", left_norm):
        return 0.94
    left_core = _distinctive_tokens(left_norm)
    right_core = _distinctive_tokens(right_norm)
    if left_core and right_core:
        overlap = len(left_core & right_core)
        if not overlap:
            return 0.0
        jaccard = overlap / len(left_core | right_core)
        containment = overlap / min(len(left_core), len(right_core))
        return min(0.93, 0.55 * containment + 0.45 * jaccard)
    left_all, right_all = set(left_norm.split()), set(right_norm.split())
    return len(left_all & right_all) / max(1, len(left_all | right_all))


def _normalize_host(value: object) -> str:
    raw = _clean(value).lower()
    if not raw:
        return ""
    candidate = raw if "://" in raw else f"https://{raw}"
    try:
        host = (urlparse(candidate).hostname or "").rstrip(".").lower()
    except ValueError:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


@lru_cache(maxsize=65_536)
def _parse_domain_host(host: str) -> DomainParts:
    if not host or host == "localhost" or re.fullmatch(r"\d+(?:\.\d+){3}", host):
        return DomainParts(host, "", "", "", False, False)
    if _TLD_EXTRACT is not None:
        extracted = _TLD_EXTRACT(host)
        suffix = extracted.suffix
        if suffix:
            registered = ".".join(part for part in (extracted.domain, suffix) if part)
            subdomain = extracted.subdomain
        else:
            registered = host
            subdomain = ""
    else:  # pragma: no cover - normal test/runtime has tldextract.
        labels = [part for part in host.split(".") if part]
        two_label_suffix = ".".join(labels[-2:]) if len(labels) >= 2 else ""
        suffix_count = 2 if two_label_suffix in MULTI_LABEL_PUBLIC_SUFFIXES else 1
        suffix = ".".join(labels[-suffix_count:]) if labels else ""
        registered = ".".join(labels[-(suffix_count + 1) :]) if len(labels) > suffix_count else host
        subdomain = ".".join(labels[: -(suffix_count + 1)]) if len(labels) > suffix_count + 1 else ""
    generic = registered in GENERIC_HOSTS or registered in FREE_EMAIL_HOSTS or any(
        host == item or host.endswith(f".{item}") for item in GENERIC_HOSTS
    )
    directory = registered in DIRECTORY_PROFILE_HOSTS or any(
        host == item or host.endswith(f".{item}") for item in DIRECTORY_PROFILE_HOSTS
    )
    return DomainParts(host, registered, suffix, subdomain, generic, directory)


def parse_domain(value: object) -> DomainParts:
    """Return public-suffix-aware domain parts, with a cached offline-safe parser."""

    return _parse_domain_host(_normalize_host(value))


def registered_domain(value: object) -> str:
    """Convenience API used by hierarchy and fixture code."""

    return parse_domain(value).registered_domain


def _domain_name_similarity(account_names: Sequence[str], domain: DomainParts) -> float:
    if not domain.registered_domain:
        return 0.0
    label = domain.registered_domain[: -(len(domain.public_suffix) + 1)] if domain.public_suffix else domain.registered_domain
    label_tokens = set(re.findall(r"[a-z0-9]+", label.replace("-", " ")))
    compact_label = re.sub(r"[^a-z0-9]", "", label)
    best = 0.0
    for name in account_names:
        core = _distinctive_tokens(name)
        if not core:
            continue
        token_overlap = len(core & label_tokens) / len(core)
        compact_hits = sum(1 for token in core if len(token) >= 4 and token in compact_label) / len(core)
        best = max(best, token_overlap, compact_hits)
    return best


def _phone(value: str) -> str:
    digits = re.sub(r"\D", "", value)
    return digits[-10:] if len(digits) >= 10 else digits


def _address_match(account_address: str, site_addresses: Sequence[str]) -> bool:
    account_tokens = set(_tokens(account_address))
    account_numbers = set(re.findall(r"\d+", account_address))
    if len(account_tokens) < 3:
        return False
    for address in site_addresses:
        site_tokens = set(_tokens(address))
        site_numbers = set(re.findall(r"\d+", address))
        if account_numbers and site_numbers and not account_numbers.intersection(site_numbers):
            continue
        if len(account_tokens & site_tokens) / len(account_tokens) >= 0.72:
            return True
    return False


def _country(value: str) -> str:
    normalized = " ".join(_tokens(value))
    return "us" if normalized in US_COUNTRIES else normalized


def _state(value: object) -> str:
    normalized = " ".join(_tokens(_clean(value)))
    if len(normalized) == 2:
        return normalized
    return US_STATE_CODES.get(normalized, normalized)


def _citation(
    source: str,
    reference: str,
    value: object,
    observed_at: str,
    source_versions: Mapping[str, str],
) -> EvidenceCitation:
    source_label = source
    if source_versions.get(source):
        source_label = f"{source}@{source_versions[source]}"
    excerpt = _clean(value).replace("\n", " ")[:240]
    evidence_hash = hashlib.sha256(
        f"{source_label}\n{reference}\n{observed_at}\n{excerpt}".encode("utf-8")
    ).hexdigest()
    return EvidenceCitation(
        source=source_label,
        reference=reference,
        observed_at=observed_at,
        excerpt=excerpt,
        evidence_hash=evidence_hash,
    )


def _source_as_of(row: Mapping[str, object]) -> str:
    values = _field_values(
        row,
        (
            "EvidenceAsOf",
            "SourceAsOf",
            "SourceObservedAt",
            "ExtractionTimestamp",
            "ExtractedAt",
            "SnapshotTimestamp",
            "LastModifiedDate",
            "SystemModstamp",
        ),
    )
    return sorted(values)[-1] if values else ""


def _classify_text(entity_text: str, domain: DomainParts, explicit_classes: Sequence[str]) -> tuple[str, str]:
    """Classify the site's organization, independent of Account expectations."""

    explicit = " ".join(explicit_classes).casefold()
    text = f"{explicit} {entity_text.casefold()}"
    title_service = bool(re.search(r"\b(title (?:company|agency|insurance)|escrow|settlement|real estate closings?)\b", text))

    if domain.is_directory_profile or re.search(r"\b(member|business|attorney) directory\b|\bcompany profile\b", text):
        return "association_directory_profile", "site_is_directory_or_profile"
    if domain.public_suffix == "gov" or re.search(
        r"\b(county (?:clerk|recorder|government)|register of deeds|municipal government|state agency)\b", text
    ):
        return "government_county", "site_identifies_government_entity"
    if domain.public_suffix == "edu" or re.search(r"\b(university|college)\b.*\b(admissions|students?|campus|academic)\b", text):
        return "education", "site_identifies_educational_entity"
    if re.search(r"\bcredit union\b|\bmember fdic\b|\bpersonal (?:checking|banking)\b", text):
        return "bank_credit_union", "site_identifies_bank_or_credit_union"
    if re.search(r"\b(nmls|mortgage lender|home loan lender|mortgage company)\b", text):
        return "mortgage_lender", "site_identifies_mortgage_or_lending_entity"
    if re.search(r"\b(real estate brokerage|real estate agency|realtors?|mls listings?|homes for sale)\b", text) and not title_service:
        return "brokerage_real_estate_agency", "site_identifies_brokerage_or_real_estate_agency"
    if re.search(r"\b(public adjuster|insurance adjusting|claims adjuster)\b", text):
        return "insurance_public_adjuster", "site_identifies_insurance_or_public_adjuster"
    if re.search(r"\b(qualified intermediary|1031 exchange|section 1031)\b", text):
        return "1031_qi", "site_identifies_1031_or_qualified_intermediary"
    if re.search(r"\b(underwriter owned|direct operation|title insurance underwriter)\b", text):
        return "underwriter_owned_direct", "site_identifies_underwriter_or_owned_direct_operation"
    if re.search(r"\b(software (?:company|platform|vendor)|saas platform|technology vendor|competitor)\b", text) and not title_service:
        return "software_vendor_competitor", "site_identifies_software_vendor_or_competitor"
    if re.search(r"\b(abstracting|abstract company|title search)\b", text) and not re.search(
        r"\b(escrow|settlement|closing services?|conduct closings?)\b", text
    ):
        return "abstract_only", "site_identifies_abstract_only_operation"
    if re.search(r"\b(law firm|attorneys? at law|legal counsel)\b", text):
        return "legal", "site_identifies_legal_entity"
    if title_service:
        return "title_escrow", "site_identifies_title_escrow_or_settlement_entity"
    return "organization", "site_entity_class_not_determinative"


def _crm_entity_class(row: Mapping[str, object]) -> str:
    value = _first(row, ("Company_Type__c", "CompanyType", "CRMCompanyType", "Type"))
    low = value.casefold()
    mappings = (
        (r"bank|credit union", "bank_credit_union"),
        (r"mortgage|lender", "mortgage_lender"),
        (r"broker|real estate agent|builder", "brokerage_real_estate_agency"),
        (r"government|county", "government_county"),
        (r"college|university|education", "education"),
        (r"public adjuster|insurance agency", "insurance_public_adjuster"),
        (r"vendor|competitor|software", "software_vendor_competitor"),
        (r"1031|qualified intermediary", "1031_qi"),
        (r"underwriter|owned direct", "underwriter_owned_direct"),
        (r"law firm|legal", "legal"),
        (r"title|escrow|settlement", "title_escrow"),
    )
    for pattern, result in mappings:
        if re.search(pattern, low):
            return result
    return ""


def _alta_evidence(
    row: Mapping[str, object],
    account_names: Sequence[str],
    account_state: str,
    minimum_confidence: str,
) -> tuple[bool, tuple[str, ...]]:
    member_value = _first(row, ("ALTA_Member", "ALTA_Member__c", "AltaMember"))
    if not _truth(member_value):
        return False, (("alta_member_false_or_missing",) if member_value else ())
    confidence = _first(row, ("Match_Confidence", "ALTA_Match_Confidence", "AltaMatchConfidence")).casefold()
    confidence_rank = {"low": 1, "medium": 2, "high": 3}
    required_rank = confidence_rank.get(minimum_confidence.casefold(), 2)
    if confidence_rank.get(confidence, 0) < required_rank:
        return False, ("alta_match_confidence_below_threshold",)
    alta_name = _first(row, ("ALTA_Company_Name", "AltaName", "ALTAName"))
    name_match = max((_name_similarity(name, alta_name) for name in account_names), default=0.0)
    if alta_name and name_match < 0.68:
        return False, ("alta_name_mismatch",)
    alta_state = _first(row, ("ALTA_State", "AltaState"))
    if account_state and alta_state and _state(account_state) != _state(alta_state):
        return False, ("alta_state_mismatch",)
    return True, ("alta_entity_confirmed",)


def resolve_website_binding(
    row: Mapping[str, object],
    *,
    resolver_version: str = ENTITY_RESOLUTION_VERSION,
    source_versions: Mapping[str, str] | None = None,
    policy: V1Policy = DEFAULT_POLICY,
) -> WebsiteBinding:
    """Resolve one Account dictionary to one fail-closed WebsiteBinding.

    The function is pure and order-independent.  It raises ``ValueError`` only
    when there is no Account ID, because no one-per-Account decision can then be
    represented.  All missing/ambiguous evidence states otherwise return a
    non-``bound`` contract rather than raising or accepting by default.
    """

    source_versions = source_versions or {}
    account_id = _first(row, ("Id", "AccountId", "Account_Id", "account_id"))
    if not account_id:
        raise ValueError("Website resolution requires an Account Id")
    account_name = _first(row, ("Name", "AccountName", "Account_Name", "LegalName", "legal_name"))
    website = _first(row, ("Website", "CurrentWebsite", "CandidateWebsite", "website"))
    domain = parse_domain(website)
    observed_at = _source_as_of(row)
    citations: list[EvidenceCitation] = []
    reasons: list[str] = []
    reason_codes: list[str] = []

    aliases = _field_values(
        row,
        ("LegalName", "Legal_Name__c", "DBAName", "DBA_Name__c", "Aliases", "EntityAliases", "RegistryName"),
    )
    account_names = list(dict.fromkeys([name for name in (account_name, *aliases) if name]))
    account_state = _first(row, ("BillingState", "BillingStateCode", "State", "RegistryState"))

    alta_confirmed, alta_codes = _alta_evidence(row, account_names, account_state, policy.alta_min_confidence)
    reason_codes.extend(alta_codes)
    if account_name:
        citations.append(_citation("crm", "Name", account_name, observed_at, source_versions))
    alta_value = _first(row, ("ALTA_Member", "ALTA_Member__c", "AltaMember"))
    if alta_value:
        citations.append(_citation("alta", "ALTA_Member", alta_value, observed_at, source_versions))

    # Lifecycle and hierarchy assertions do not prove website ownership, but
    # they are preserved in this stage's provenance so downstream resolution
    # cannot silently lose the context or reinterpret a shared domain as proof.
    parent_id = _first(row, ("ParentId", "ParentAccountId", "Parent_Account__c"))
    if parent_id:
        reason_codes.append("crm_parent_context_present")
        citations.append(_citation("crm", "ParentId", parent_id, observed_at, source_versions))
    lifecycle_value = _first(
        row,
        ("Account_Status__c", "AccountStatus", "LifecycleStatus", "CustomerStatus", "Type"),
    )
    active_customer = any(
        _truth(row.get(name))
        for name in ("Active_Customer__c", "ActiveCustomer", "HasActiveSubscription", "HasActiveContract")
    ) or _number(row.get("Active_Subscription_Revenue__c")) > 0
    if lifecycle_value or active_customer:
        reason_codes.append("crm_lifecycle_context_present")
        citations.append(
            _citation(
                "crm",
                "Lifecycle",
                lifecycle_value or "active_customer_assertion",
                observed_at,
                source_versions,
            )
        )
    shared_domain_context = _field_values(
        row,
        ("SharedDomainAccountIds", "DomainCustomerAccountIds", "DomainAccountIds", "SharedWebsiteAccountIds"),
    )
    if shared_domain_context:
        reason_codes.append("shared_domain_context_not_binding_proof")
        citations.append(
            _citation("crm", "SharedDomainAccountIds", "|".join(shared_domain_context), observed_at, source_versions)
        )
    duplicate_context = _first(
        row,
        ("DuplicateSurvivorId", "Duplicate_Survivor_Id__c", "SuggestedSurvivorId", "DedupeSuggestedSurvivorId"),
    )
    if duplicate_context:
        reason_codes.append("duplicate_context_not_binding_proof")
        citations.append(_citation("dedupe", "DuplicateSurvivorId", duplicate_context, observed_at, source_versions))

    def build(
        status: BindingStatus,
        confidence: Confidence,
        entity_class: str,
        code: str,
        reason: str,
        non_alta_website_proofs: Sequence[str] = (),
    ) -> WebsiteBinding:
        codes = tuple(dict.fromkeys((*reason_codes, code)))
        explanation = tuple(dict.fromkeys((*reasons, reason)))
        # This is calculated from row-level provenance, not asserted.  Because
        # the resolver never binds without a non-ALTA proof, it must remain false.
        alta_sole = status is BindingStatus.BOUND and alta_confirmed and not non_alta_website_proofs
        if alta_sole:  # Defensive invariant: fail closed if later logic regresses.
            status = BindingStatus.AMBIGUOUS
            confidence = Confidence.LOW
            codes = tuple(dict.fromkeys((*codes, "alta_cannot_be_sole_website_proof")))
            explanation = tuple(dict.fromkeys((*explanation, "ALTA corroborates the entity but cannot prove website ownership.")))
            alta_sole = False
        return WebsiteBinding(
            account_id=account_id,
            account_name=account_name,
            website=website,
            registered_domain=domain.registered_domain,
            status=status,
            confidence=confidence,
            entity_class=entity_class,
            reason_codes=codes,
            reasons=explanation,
            citations=tuple(citations),
            source_as_of=observed_at,
            resolver_version=resolver_version,
            alta_entity_confirmed=alta_confirmed,
            alta_used_as_sole_website_proof=alta_sole,
        )

    if not website:
        return build(BindingStatus.NO_WEBSITE, Confidence.LOW, "unknown", "website_missing", "No candidate website was supplied.")
    if not domain.registered_domain:
        return build(
            BindingStatus.INSUFFICIENT_EVIDENCE,
            Confidence.LOW,
            "unknown",
            "website_host_invalid",
            "The candidate website did not contain a valid registrable host.",
        )
    citations.append(_citation("crm", "Website", website, observed_at, source_versions))

    site_names = _field_values(
        row,
        (
            "SiteOrganizationName",
            "SiteOrganization",
            "OrganizationName",
            "SiteName",
            "PageTitle",
            "HomePageTitle",
            "ContactOrganizationName",
        ),
    )
    page_text = " ".join(
        _field_values(row, ("AboutText", "ContactText", "OrganizationDescription", "CachedPageText", "ExtractedText"))
    )[:12000]
    explicit_classes = _field_values(
        row,
        ("SiteEntityClass", "WebsiteEntityClass", "WebsiteCategory", "OrganizationType", "SiteClassification"),
    )
    entity_text = " ".join((*site_names, page_text))
    entity_class, class_reason = _classify_text(entity_text, domain, explicit_classes)

    for field_name in ("SiteOrganizationName", "PageTitle", "AboutText", "ContactText", "SiteEntityClass"):
        if _clean(row.get(field_name)):
            citations.append(_citation("cached_website", field_name, row[field_name], observed_at, source_versions))

    if domain.is_directory_profile or entity_class == "association_directory_profile":
        reason_codes.append(class_reason)
        return build(
            BindingStatus.MISMATCH,
            Confidence.HIGH,
            "association_directory_profile",
            "directory_or_profile_not_owned_site",
            "The candidate URL is an association, directory, social, or company-profile page rather than an owned entity site.",
        )
    if domain.is_generic_host:
        return build(
            BindingStatus.HYGIENE_REVIEW,
            Confidence.LOW,
            "generic_free_host",
            "generic_host_requires_tenant_validation",
            "A generic, free-hosted, or shared-provider domain cannot establish ownership without tenant-level validation.",
        )

    hygiene = _first(row, ("Website_Hygiene_Status__c", "WebsiteHygiene", "HygieneStatus"))
    state_conflict_flag = _truth(row.get("StateConflict")) or _truth(row.get("WebsiteStateConflict"))
    if hygiene and re.search(r"wrong|mismatch|unrelated|redirected.*other", hygiene, re.I):
        citations.append(_citation("website_hygiene", "Website_Hygiene_Status__c", hygiene, observed_at, source_versions))
        return build(
            BindingStatus.MISMATCH,
            Confidence.HIGH,
            entity_class,
            "authoritative_hygiene_mismatch",
            "The supplied website-hygiene decision identifies the candidate site as a different entity.",
        )

    name_score = max(
        (_name_similarity(account_candidate, site_candidate) for account_candidate in account_names for site_candidate in site_names),
        default=0.0,
    )
    domain_score = _domain_name_similarity(account_names, domain)

    account_phone = _phone(_first(row, ("Phone", "AccountPhone", "BillingPhone")))
    site_phones = {
        _phone(value)
        for value in _field_values(row, ("SitePhones", "WebsitePhones", "ContactPhones", "PhoneEvidence", "SitePhone"))
        if _phone(value)
    }
    phone_match = bool(account_phone and account_phone in site_phones)
    phone_conflict = bool(account_phone and site_phones and account_phone not in site_phones)

    account_address = " ".join(
        value
        for value in (
            _first(row, ("BillingStreet", "Street")),
            _first(row, ("BillingCity", "City")),
            account_state,
            _first(row, ("BillingPostalCode", "PostalCode")),
            _first(row, ("BillingCountry", "Country")),
        )
        if value
    )
    site_addresses = _field_values(
        row,
        ("SiteAddresses", "WebsiteAddresses", "ContactAddresses", "OfficeAddresses", "AddressEvidence", "SiteAddress"),
    )
    address_match = _address_match(account_address, site_addresses)

    account_city = _first(row, ("BillingCity", "City")).casefold()
    site_cities = {value.casefold() for value in _field_values(row, ("SiteCity", "SiteCities", "WebsiteCity"))}
    site_states = {_state(value) for value in _field_values(row, ("SiteState", "SiteStates", "WebsiteState"))}
    account_state_normalized = _state(account_state)
    geo_match = bool(
        (account_city and account_city in site_cities and account_state_normalized in site_states)
        or (account_state_normalized in site_states and not account_city)
    )
    geo_conflict = bool(account_state_normalized and site_states and account_state_normalized not in site_states)
    if geo_match:
        citations.append(
            _citation(
                "cached_website",
                "SiteCityState",
                f"{'|'.join(sorted(site_cities))};{'|'.join(sorted(site_states))}",
                observed_at,
                source_versions,
            )
        )

    account_country = _country(_first(row, ("BillingCountry", "BillingCountryCode", "Country")))
    site_countries = {
        _country(value) for value in _field_values(row, ("SiteCountry", "SiteCountries", "WebsiteCountry")) if _country(value)
    }
    international_conflict = bool(account_country and site_countries and account_country not in site_countries)

    registry_domains = {
        registered_domain(value)
        for value in _field_values(row, ("RegistryWebsite", "Registry_Website", "StateRegistryWebsite"))
        if registered_domain(value)
    }
    registry_domain_match = domain.registered_domain in registry_domains

    if phone_match:
        citations.append(_citation("cached_website", "SitePhones", sorted(site_phones)[0], observed_at, source_versions))
    if address_match:
        citations.append(_citation("cached_website", "SiteAddresses", site_addresses[0], observed_at, source_versions))
    if registry_domain_match:
        citations.append(_citation("registry", "RegistryWebsite", domain.registered_domain, observed_at, source_versions))

    if international_conflict:
        citations.append(
            _citation("cached_website", "SiteCountry", "|".join(sorted(site_countries)), observed_at, source_versions)
        )
        return build(
            BindingStatus.MISMATCH,
            Confidence.HIGH,
            "international_lookalike",
            "international_country_conflict",
            "The site identifies an entity in a different country from the Account.",
        )
    if name_score >= 0.82 and (state_conflict_flag or (geo_conflict and phone_conflict)):
        return build(
            BindingStatus.MISMATCH,
            Confidence.HIGH,
            "same_name_unrelated_entity",
            "same_name_contact_or_geo_conflict",
            "The name is similar, but independent location/contact evidence identifies an unrelated same-name entity.",
        )

    crm_class = _crm_entity_class(row)
    adjacent_classes = {
        "bank_credit_union",
        "mortgage_lender",
        "brokerage_real_estate_agency",
        "government_county",
        "education",
        "insurance_public_adjuster",
        "software_vendor_competitor",
        "1031_qi",
        "underwriter_owned_direct",
        "abstract_only",
    }
    if entity_class == "organization" and crm_class in adjacent_classes:
        entity_class = crm_class
        class_reason = "crm_entity_class_used_with_neutral_site_class"
    class_conflict = bool(
        entity_class in adjacent_classes - {"abstract_only"}
        and crm_class
        and crm_class != entity_class
        and crm_class in {"title_escrow", "legal"}
    )
    reverse_class_conflict = bool(
        entity_class in {"title_escrow", "legal"}
        and crm_class in adjacent_classes - {"abstract_only"}
    )
    if class_conflict and (name_score < 0.97 or not (phone_match or address_match or registry_domain_match)):
        reason_codes.append(class_reason)
        return build(
            BindingStatus.MISMATCH,
            Confidence.HIGH,
            entity_class,
            "site_entity_class_conflicts_with_account",
            f"The site identifies a {entity_class.replace('_', ' ')}, conflicting with the Account entity class and identity evidence.",
        )
    if reverse_class_conflict:
        reason_codes.extend((class_reason, "crm_site_entity_class_conflict"))
        return build(
            BindingStatus.AMBIGUOUS,
            Confidence.MEDIUM,
            entity_class,
            "crm_site_entity_class_requires_review",
            "The cached site identity and CRM Company Type disagree on the entity class; the conflict must be reviewed before scoring.",
        )

    proofs: list[str] = []
    if name_score >= 0.82:
        proofs.append("site_name_match")
    if domain_score >= 0.50:
        proofs.append("domain_brand_match")
    if phone_match:
        proofs.append("phone_match")
    if address_match:
        proofs.append("address_match")
    if geo_match:
        proofs.append("city_state_match")
    if registry_domain_match:
        proofs.append("registry_domain_match")

    identity_score = (
        0.58 * name_score
        + 0.18 * domain_score
        + 0.20 * float(phone_match)
        + 0.16 * float(address_match)
        + 0.12 * float(geo_match)
        + 0.16 * float(registry_domain_match)
    )
    if name_score >= 0.82 and any(item in proofs for item in ("domain_brand_match", "phone_match", "address_match", "city_state_match", "registry_domain_match")):
        identity_score += 0.10
    if phone_conflict:
        identity_score -= 0.08
    if geo_conflict:
        identity_score -= 0.06
    identity_score = max(0.0, min(1.0, identity_score))

    can_bind = name_score >= 0.82 and len(proofs) >= 2 and identity_score >= policy.binding_high_threshold
    if can_bind:
        reason_codes.extend(("non_alta_website_identity_proof", *proofs, class_reason))
        return build(
            BindingStatus.BOUND,
            Confidence.HIGH,
            entity_class,
            "website_bound_to_account",
            "Cached site identity plus independent domain/contact/location evidence binds the website to the Account.",
            proofs,
        )

    if site_names and name_score < 0.25 and (phone_conflict or geo_conflict or class_conflict):
        reason_codes.append(class_reason)
        return build(
            BindingStatus.MISMATCH,
            Confidence.HIGH,
            entity_class,
            "site_self_identifies_as_different_entity",
            "The site's organization name and corroborating evidence identify a different entity.",
        )

    if identity_score >= policy.binding_medium_threshold or (name_score >= 0.82 and len(proofs) == 1):
        if alta_confirmed and len(proofs) <= 1:
            reason_codes.append("alta_corrobates_entity_not_website")
        return build(
            BindingStatus.AMBIGUOUS,
            Confidence.MEDIUM,
            entity_class,
            "website_binding_requires_review",
            "Some identity evidence agrees, but the independent website-binding proof threshold is not met.",
            proofs,
        )

    if alta_confirmed:
        return build(
            BindingStatus.INSUFFICIENT_EVIDENCE,
            Confidence.LOW,
            entity_class,
            "alta_cannot_be_sole_website_proof",
            "ALTA corroborates the Account entity, but no independent evidence binds the candidate website.",
            proofs,
        )
    if not site_names and not site_addresses and not site_phones:
        return build(
            BindingStatus.INSUFFICIENT_EVIDENCE,
            Confidence.LOW,
            entity_class,
            "cached_site_identity_evidence_missing",
            "The cached evidence contains too little organization/contact/location data to bind the website.",
            proofs,
        )
    return build(
        BindingStatus.AMBIGUOUS,
        Confidence.LOW,
        entity_class,
        "website_binding_unresolved",
        "Available identity evidence is weak or conflicting; the resolver does not accept by default.",
        proofs,
    )


def resolve_website_bindings(
    rows: Iterable[Mapping[str, object]],
    **kwargs: object,
) -> list[WebsiteBinding]:
    """Resolve a population, reject duplicate IDs, and return stable ID order."""

    decisions = [resolve_website_binding(row, **kwargs) for row in rows]
    ids = [decision.account_id for decision in decisions]
    if len(ids) != len(set(ids)):
        raise ValueError("Website binding input contains duplicate Account IDs")
    return sorted(decisions, key=lambda decision: decision.account_id)
