"""Entity-safe feature extraction from cached website evidence.

This module performs no retrieval and has no provider/network dependencies.  It
accepts already cached page text, preserves source citations, and separates raw
candidates from the conservative values exposed to downstream value models.

Two fail-closed rules are intentional:

* capacity features are exposed only after a strong website binding and a
  strong, independently sellable-unit decision; and
* website sophistication can change evidence confidence, but never office or
  staff counts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import hashlib
import re
from typing import Any, Iterable, Sequence
from urllib.parse import urlparse

from .config import FEATURE_VERSION
from .contracts import Confidence, EvidenceCitation, EvidenceFeatures, SellableUnitDecision, WebsiteBinding


_SPACE_RE = re.compile(r"\s+")
_MARKDOWN_RE = re.compile(r"^[\s>*#_`~-]+|[\s*_`~-]+$")

_STATE = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|"
    "MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|"
    "WA|WV|WI|WY|DC"
)
_STREET_SUFFIX = (
    r"street|st\.?|avenue|ave\.?|road|rd\.?|boulevard|blvd\.?|drive|dr\.?|"
    r"lane|ln\.?|court|ct\.?|circle|cir\.?|parkway|pkwy\.?|highway|hwy\.?|"
    r"way|place|pl\.?|terrace|ter\.?|trail|trl\.?|plaza|square|sq\.?")
_ADDRESS_RE = re.compile(
    rf"\b\d{{1,6}}\s+[A-Za-z0-9.'&#/-]+(?:\s+[A-Za-z0-9.'&#/-]+){{0,8}}\s+"
    rf"(?:{_STREET_SUFFIX})\b"
    rf"(?:[\s,]+(?:suite|ste\.?|unit|#)\s*[A-Za-z0-9-]+)?"
    rf"[\s,]+[A-Za-z][A-Za-z .'-]{{1,40}}[\s,]+(?:{_STATE})[\s,]+"
    rf"\d{{5}}(?:-\d{{4}})?\b",
    re.IGNORECASE,
)

_OFFICE_PAGE_RE = re.compile(r"/(?:contact|locations?|offices?|find-us|directions)(?:/|$)", re.I)
_ATTORNEY_BIO_PAGE_RE = re.compile(r"/(?:attorneys?|lawyers?|people|team|bio|professionals?)(?:/|$)", re.I)
_OFFICE_CONTEXT_RE = re.compile(
    r"\b(our (?:main |branch |local )?office|office location|visit us|headquarters|"
    r"corporate office|branch office|closing office|escrow office|contact us)\b",
    re.I,
)

_OFFICE_EXCLUSIONS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "court_or_government",
        re.compile(r"\b(courthouse|county court|court (?:address|resources?|services?)|county clerk|recorder(?:'s)? office|register of deeds|government office)\b", re.I),
    ),
    (
        "customer_or_testimonial",
        re.compile(r"\b(testimonial|what (?:our )?clients say|customer story|client spotlight|case study)\b", re.I),
    ),
    (
        "service_area",
        re.compile(r"\b(service areas?|areas? (?:we )?serve|counties served|serving (?:all|the)|coverage area)\b", re.I),
    ),
    (
        "partner_office",
        re.compile(r"\b(partner (?:office|location)|affiliate(?:d)? (?:office|location)|network location|referral partner)\b", re.I),
    ),
    (
        "property_or_listing",
        re.compile(r"\b(property (?:address|listing)|subject property|mls|for sale|open house|listing address)\b", re.I),
    ),
)

_ROLE_SEPARATOR_RE = re.compile(r"\s*(?:\||\u2014|\u2013|-|,|:)\s*")
_PERSON_LINE_RE = re.compile(
    r"^(?P<name>[A-Z][A-Za-z'.-]+(?:\s+[A-Z][A-Za-z'.-]+){1,3})"
    r"(?:\s*(?:\||\u2014|\u2013|-|,|:)\s*(?P<role>[^|]{2,90}))?$"
)
_OPERATIONAL_ROLE_RE = re.compile(
    r"\b(escrow officer|escrow manager|closing attorney|settlement attorney|settlement agent|"
    r"closing coordinator|closing specialist|closer|processor|title examiner|title agent|"
    r"post[- ]closing|pre[- ]closing|branch manager|operations? manager|production manager|"
    r"transaction coordinator|disbursement|funding specialist|recording specialist)\b",
    re.I,
)
_LEGAL_ROLE_RE = re.compile(r"\b(attorney|lawyer|counsel|partner|associate|esq\.?)\b", re.I)
_GENERIC_ROLE_RE = re.compile(
    r"\b(president|owner|founder|chief|ceo|cfo|vice president|director|manager|sales|"
    r"marketing|receptionist|assistant|administrator)\b",
    re.I,
)
_NON_PERSON_WORDS = {
    "about",
    "abstract",
    "contact",
    "copyright",
    "escrow",
    "insurance",
    "national",
    "office",
    "privacy",
    "realty",
    "resources",
    "services",
    "settlement",
    "testimonials",
    "title",
}
_STAFF_EXCLUSION_RE = re.compile(
    r"\b(testimonials?|customer story|what (?:our )?clients say|client says|directory listing|member directory|speaker|guest|"
    r"property agent|listing agent|opposing counsel)\b",
    re.I,
)

_SERVICE_TERMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("abstracting", re.compile(r"\babstract(?:ing| services?| search)?\b", re.I)),
    ("commercial_closing", re.compile(r"\bcommercial (?:real estate )?closings?\b", re.I)),
    ("escrow", re.compile(r"\bescrow (?:services?|closings?|officer)\b", re.I)),
    ("lender_services", re.compile(r"\blender services?\b", re.I)),
    ("real_estate_closing", re.compile(r"\breal estate closings?\b", re.I)),
    ("refinance_closing", re.compile(r"\brefinanc(?:e|ing) closings?\b", re.I)),
    ("settlement", re.compile(r"\bsettlement (?:services?|agent|closings?)\b", re.I)),
    ("title_insurance", re.compile(r"\btitle insurance\b", re.I)),
)
_TOOL_TERMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("client_portal", re.compile(r"\b(client|customer|closing) portal\b", re.I)),
    ("online_ordering", re.compile(r"\b(order (?:title|services?) online|online ordering)\b", re.I)),
    ("rate_calculator", re.compile(r"\b(rate|fee|closing cost) calculator\b", re.I)),
    ("secure_upload", re.compile(r"\bsecure (?:document )?(?:upload|exchange)\b", re.I)),
)


class CandidateStatus(str, Enum):
    VERIFIED = "verified"
    UNCERTAIN = "uncertain"
    EXCLUDED = "excluded"


class StaffCategory(str, Enum):
    RELEVANT_OPERATIONAL = "relevant_operational"
    LEGAL_PROFESSIONAL = "legal_professional_non_operational"
    GENERIC_EMPLOYEE = "generic_employee_non_operational"
    UNVERIFIED_NAME = "unverified_name"
    EXCLUDED_CONTEXT = "excluded_context"


@dataclass(frozen=True)
class CachedEvidencePage:
    """A single immutable page from an existing extraction cache."""

    url: str
    text: str
    title: str = ""
    observed_at: str = ""
    evidence_hash: str = ""

    def citation(self, excerpt: str) -> EvidenceCitation:
        digest = self.evidence_hash or hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        return EvidenceCitation(
            source="cached_website",
            reference=self.url,
            observed_at=self.observed_at,
            excerpt=_clean_excerpt(excerpt),
            evidence_hash=digest,
        )


@dataclass(frozen=True)
class FeatureEligibility:
    """Upstream identity gates required before capacity features are usable."""

    binding_status: str
    binding_confidence: Confidence | str
    sellable_unit_eligible: bool
    sellable_unit_confidence: Confidence | str

    @classmethod
    def from_contracts(
        cls,
        binding: WebsiteBinding,
        sellable_unit: SellableUnitDecision,
    ) -> "FeatureEligibility":
        """Adapt the shared upstream contracts without weakening their gates."""

        return cls(
            binding_status=binding.status.value,
            binding_confidence=binding.confidence,
            sellable_unit_eligible=sellable_unit.standalone_score_eligible,
            sellable_unit_confidence=sellable_unit.confidence,
        )

    @property
    def is_strong(self) -> bool:
        return (
            self.binding_status.strip().lower() in {"bound", "correct_website", "confirmed"}
            and _confidence_value(self.binding_confidence) == Confidence.HIGH.value
            and self.sellable_unit_eligible
            and _confidence_value(self.sellable_unit_confidence) == Confidence.HIGH.value
        )


@dataclass(frozen=True)
class OfficeCandidate:
    normalized_address: str
    display_address: str
    status: CandidateStatus
    confidence: float
    reason_codes: tuple[str, ...]
    citations: tuple[EvidenceCitation, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["citations"] = [citation.to_dict() for citation in self.citations]
        return payload


@dataclass(frozen=True)
class StaffCandidate:
    normalized_name: str
    display_name: str
    role: str
    category: StaffCategory
    confidence: float
    citations: tuple[EvidenceCitation, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["category"] = self.category.value
        payload["citations"] = [citation.to_dict() for citation in self.citations]
        return payload


@dataclass(frozen=True)
class EvidenceExtractionResult:
    """Review detail plus the conservative shared pipeline contract."""

    features: EvidenceFeatures
    office_candidates: tuple[OfficeCandidate, ...]
    staff_candidates: tuple[StaffCandidate, ...]
    website_sophistication: str
    capacity_features_exposed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "features": self.features.to_dict(),
            "office_candidates": [item.to_dict() for item in self.office_candidates],
            "staff_candidates": [item.to_dict() for item in self.staff_candidates],
            "website_sophistication": self.website_sophistication,
            "capacity_features_exposed": self.capacity_features_exposed,
        }


def _confidence_value(value: Confidence | str) -> str:
    if isinstance(value, Confidence):
        return value.value
    raw = str(value or "").strip().lower()
    return {"high": "High", "medium": "Medium", "low": "Low"}.get(raw, str(value or ""))


def _clean_line(value: str) -> str:
    return _SPACE_RE.sub(" ", _MARKDOWN_RE.sub("", value or "")).strip()


def _clean_excerpt(value: str, limit: int = 280) -> str:
    clean = _clean_line(value)
    return clean[:limit]


def normalize_address(value: str) -> str:
    """Normalize a US address for deterministic candidate de-duplication."""

    text = value.lower().replace("#", " suite ")
    text = re.sub(r"\b(street)\b", "st", text)
    text = re.sub(r"\b(avenue)\b", "ave", text)
    text = re.sub(r"\b(road)\b", "rd", text)
    text = re.sub(r"\b(boulevard)\b", "blvd", text)
    text = re.sub(r"\b(drive)\b", "dr", text)
    text = re.sub(r"\b(lane)\b", "ln", text)
    text = re.sub(r"\b(court)\b", "ct", text)
    text = re.sub(r"\b(parkway)\b", "pkwy", text)
    text = re.sub(r"\b(highway)\b", "hwy", text)
    text = re.sub(r"\b(suite|ste\.?|unit)\b", "suite", text)
    text = re.sub(r"(\d{5})-\d{4}\b", r"\1", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return _SPACE_RE.sub(" ", text).strip()


def _page_lines(page: CachedEvidencePage) -> list[str]:
    return [_clean_line(line) for line in page.text.splitlines() if _clean_line(line)]


def _candidate_context(lines: Sequence[str], index: int) -> str:
    start = max(0, index - 2)
    end = min(len(lines), index + 3)
    return " ".join(lines[start:end])


def _raw_office_occurrences(pages: Sequence[CachedEvidencePage]) -> list[dict[str, Any]]:
    occurrences: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for page in pages:
        lines = _page_lines(page)
        for index in range(len(lines)):
            # Addresses frequently span two or three markdown lines.
            window = " ".join(lines[index : min(len(lines), index + 3)])
            scan_window = window.replace(".", "")
            context = _candidate_context(lines, index)
            for match in _ADDRESS_RE.finditer(scan_window):
                display = _SPACE_RE.sub(" ", match.group(0)).strip(" ,")
                normalized = normalize_address(display)
                key = (page.url.lower(), normalized, "")
                if not normalized or key in seen:
                    continue
                seen.add(key)
                reason = ""
                combined = f"{page.title} {page.url} {context}"
                for code, pattern in _OFFICE_EXCLUSIONS:
                    if pattern.search(combined):
                        reason = code
                        break
                if not reason and _ATTORNEY_BIO_PAGE_RE.search(urlparse(page.url).path):
                    reason = "attorney_bio"
                supportive = bool(_OFFICE_PAGE_RE.search(urlparse(page.url).path) or _OFFICE_CONTEXT_RE.search(context))
                occurrences.append(
                    {
                        "normalized": normalized,
                        "display": display,
                        "reason": reason,
                        "supportive": supportive,
                        "context": context,
                        "citation": page.citation(context),
                    }
                )
    return occurrences


def extract_office_candidates(pages: Sequence[CachedEvidencePage]) -> tuple[OfficeCandidate, ...]:
    """Extract, normalize, de-duplicate, and classify operating-office evidence."""

    grouped: dict[str, list[dict[str, Any]]] = {}
    for occurrence in _raw_office_occurrences(pages):
        grouped.setdefault(occurrence["normalized"], []).append(occurrence)

    candidates: list[OfficeCandidate] = []
    for normalized, occurrences in sorted(grouped.items()):
        non_excluded = [item for item in occurrences if not item["reason"]]
        supportive = [item for item in non_excluded if item["supportive"]]
        citations = _dedupe_citations(item["citation"] for item in occurrences)
        reasons = sorted({item["reason"] for item in occurrences if item["reason"]})
        unique_pages = {item["citation"].reference.lower() for item in occurrences}

        supportive_pages = {item["citation"].reference.lower() for item in supportive}
        if supportive:
            status = CandidateStatus.VERIFIED
            confidence = 0.97 if len(supportive_pages) >= 2 else 0.9
            reason_codes = ("operating_office_corroborated",) if len(supportive_pages) >= 2 else ("operating_office_page",)
        elif non_excluded and len(unique_pages) >= 3:
            # Repeated boilerplate without a contact/location assertion is not
            # allowed to inflate an office network.
            status = CandidateStatus.EXCLUDED
            confidence = 0.92
            reason_codes = ("repeated_footer_without_office_context",)
        elif non_excluded:
            status = CandidateStatus.UNCERTAIN
            confidence = 0.55
            reason_codes = ("address_without_operating_office_context",)
        else:
            status = CandidateStatus.EXCLUDED
            confidence = 0.96
            reason_codes = tuple(reasons) or ("excluded_non_operating_address",)

        candidates.append(
            OfficeCandidate(
                normalized_address=normalized,
                display_address=occurrences[0]["display"],
                status=status,
                confidence=confidence,
                reason_codes=reason_codes,
                citations=citations,
            )
        )
    return tuple(candidates)


def _staff_line_context(lines: Sequence[str], index: int) -> str:
    return " ".join(lines[max(0, index - 1) : min(len(lines), index + 2)])


def _normal_name(value: str) -> str:
    return re.sub(r"[^a-z]+", " ", value.lower()).strip()


def extract_staff_candidates(pages: Sequence[CachedEvidencePage]) -> tuple[StaffCandidate, ...]:
    """Classify named people without treating generic names as operating staff."""

    raw: dict[str, list[StaffCandidate]] = {}
    for page in pages:
        lines = _page_lines(page)
        for index, line in enumerate(lines):
            if len(line) > 120:
                continue
            match = _PERSON_LINE_RE.fullmatch(line)
            if not match:
                continue
            name = match.group("name").strip()
            if any(word.lower() in _NON_PERSON_WORDS for word in name.split()):
                continue
            role = (match.group("role") or "").strip()
            context = _staff_line_context(lines, index)
            role_context = role
            page_context = f"{page.title} {page.url} {context}"

            if _STAFF_EXCLUSION_RE.search(page_context):
                category = StaffCategory.EXCLUDED_CONTEXT
                confidence = 0.96
            elif role and _OPERATIONAL_ROLE_RE.search(role_context):
                category = StaffCategory.RELEVANT_OPERATIONAL
                confidence = 0.94 if role else 0.82
            elif role and _LEGAL_ROLE_RE.search(role_context):
                category = StaffCategory.LEGAL_PROFESSIONAL
                confidence = 0.92
            elif role and _GENERIC_ROLE_RE.search(role_context):
                category = StaffCategory.GENERIC_EMPLOYEE
                confidence = 0.85
            else:
                category = StaffCategory.UNVERIFIED_NAME
                confidence = 0.45

            normalized = _normal_name(name)
            candidate = StaffCandidate(
                normalized_name=normalized,
                display_name=name,
                role=role,
                category=category,
                confidence=confidence,
                citations=(page.citation(context),),
            )
            raw.setdefault(normalized, []).append(candidate)

    priority = {
        StaffCategory.RELEVANT_OPERATIONAL: 5,
        StaffCategory.LEGAL_PROFESSIONAL: 4,
        StaffCategory.GENERIC_EMPLOYEE: 3,
        StaffCategory.UNVERIFIED_NAME: 2,
        StaffCategory.EXCLUDED_CONTEXT: 1,
    }
    deduped: list[StaffCandidate] = []
    for normalized, occurrences in sorted(raw.items()):
        best = max(occurrences, key=lambda item: (priority[item.category], item.confidence, item.role))
        deduped.append(
            StaffCandidate(
                normalized_name=normalized,
                display_name=best.display_name,
                role=best.role,
                category=best.category,
                confidence=best.confidence,
                citations=_dedupe_citations(citation for item in occurrences for citation in item.citations),
            )
        )
    return tuple(deduped)


def _signals(
    pages: Sequence[CachedEvidencePage],
    definitions: Sequence[tuple[str, re.Pattern[str]]],
) -> tuple[tuple[str, ...], tuple[EvidenceCitation, ...]]:
    found: set[str] = set()
    citations: list[EvidenceCitation] = []
    for page in pages:
        for label, pattern in definitions:
            match = pattern.search(page.text)
            if not match:
                continue
            found.add(label)
            start = max(0, match.start() - 90)
            end = min(len(page.text), match.end() + 120)
            citations.append(page.citation(page.text[start:end]))
    return tuple(sorted(found)), _dedupe_citations(citations)


def _website_sophistication(pages: Sequence[CachedEvidencePage], mapped_link_count: int) -> str:
    # This label is deliberately absent from the capacity feature contract.
    distinct_paths = {urlparse(page.url).path.rstrip("/").lower() or "/" for page in pages}
    if mapped_link_count >= 20 or len(distinct_paths) >= 6:
        return "high"
    if mapped_link_count >= 6 or len(distinct_paths) >= 3:
        return "medium"
    return "low"


def _dedupe_citations(citations: Iterable[EvidenceCitation]) -> tuple[EvidenceCitation, ...]:
    unique: dict[tuple[str, str, str, str], EvidenceCitation] = {}
    for citation in citations:
        key = (citation.source, citation.reference, citation.observed_at, citation.excerpt)
        unique[key] = citation
    return tuple(unique[key] for key in sorted(unique))


def extract_entity_safe_features(
    account_id: str,
    pages: Sequence[CachedEvidencePage],
    eligibility: FeatureEligibility,
    *,
    mapped_link_count: int = 0,
) -> EvidenceExtractionResult:
    """Build deterministic, versioned features from existing cached pages.

    Raw candidates remain available for review when an upstream identity gate
    is weak, while the shared ``EvidenceFeatures`` capacity values are zeroed.
    No anchor input exists, so historical/rep MCV cannot bypass these gates.
    """

    ordered_pages = tuple(sorted(pages, key=lambda page: (page.url.lower(), page.observed_at, page.title)))
    office_candidates = extract_office_candidates(ordered_pages)
    staff_candidates = extract_staff_candidates(ordered_pages)
    service_signals, service_citations = _signals(ordered_pages, _SERVICE_TERMS)
    tool_signals, tool_citations = _signals(ordered_pages, _TOOL_TERMS)
    sophistication = _website_sophistication(ordered_pages, mapped_link_count)

    verified_offices = [item for item in office_candidates if item.status == CandidateStatus.VERIFIED]
    uncertain_offices = [item for item in office_candidates if item.status == CandidateStatus.UNCERTAIN]
    relevant_staff = [item for item in staff_candidates if item.category == StaffCategory.RELEVANT_OPERATIONAL]
    uncertain_staff = [item for item in staff_candidates if item.category == StaffCategory.UNVERIFIED_NAME]

    uncertainty: set[str] = set()
    for candidate in office_candidates:
        if candidate.status != CandidateStatus.VERIFIED:
            uncertainty.update(candidate.reason_codes)
    if any(item.category == StaffCategory.LEGAL_PROFESSIONAL for item in staff_candidates):
        uncertainty.add("attorneys_not_counted_as_operational_staff")
    if any(item.category == StaffCategory.GENERIC_EMPLOYEE for item in staff_candidates):
        uncertainty.add("generic_roles_not_counted_as_operational_staff")
    if any(item.category == StaffCategory.EXCLUDED_CONTEXT for item in staff_candidates):
        uncertainty.add("testimonial_or_listing_names_excluded")

    capacity_exposed = eligibility.is_strong
    if not capacity_exposed:
        uncertainty.add("upstream_identity_or_sellable_gate_not_strong")

    all_citations = _dedupe_citations(
        citation
        for citation in (
            [citation for candidate in office_candidates for citation in candidate.citations]
            + [citation for candidate in staff_candidates for citation in candidate.citations]
            + list(service_citations)
            + list(tool_citations)
        )
    )

    if not capacity_exposed:
        evidence_confidence = Confidence.LOW
    else:
        corroborated = sum(1 for item in verified_offices if len(item.citations) >= 2)
        substantive = len(verified_offices) + len(relevant_staff) + len(service_signals)
        confidence_points = substantive + corroborated
        # Sophistication gets at most one confidence point and never changes a
        # count, range, service signal, or eligibility decision.
        if sophistication == "high":
            confidence_points += 1
        if confidence_points >= 5 and len(all_citations) >= 3:
            evidence_confidence = Confidence.HIGH
        elif confidence_points >= 2:
            evidence_confidence = Confidence.MEDIUM
        else:
            evidence_confidence = Confidence.LOW

    if capacity_exposed:
        office_count = len(verified_offices)
        office_low = office_count
        office_high = office_count + len(uncertain_offices)
        staff_count = len(relevant_staff)
        staff_low = staff_count
        staff_high = staff_count + len(uncertain_staff)
        exposed_services = service_signals
        exposed_tools = tool_signals
    else:
        office_count = office_low = office_high = 0
        staff_count = staff_low = staff_high = 0
        exposed_services = ()
        exposed_tools = ()

    features = EvidenceFeatures(
        account_id=account_id,
        operating_office_count=office_count,
        office_count_low=office_low,
        office_count_high=office_high,
        relevant_staff_count=staff_count,
        staff_count_low=staff_low,
        staff_count_high=staff_high,
        operational_service_signals=tuple(exposed_services),
        tool_signals=tuple(exposed_tools),
        evidence_confidence=evidence_confidence,
        uncertainty_codes=tuple(sorted(uncertainty)),
        citations=all_citations,
        feature_version=FEATURE_VERSION,
    )
    return EvidenceExtractionResult(
        features=features,
        office_candidates=office_candidates,
        staff_candidates=staff_candidates,
        website_sophistication=sophistication,
        capacity_features_exposed=capacity_exposed,
    )


def build_evidence_features(
    account_id: str,
    pages: Sequence[CachedEvidencePage],
    eligibility: FeatureEligibility,
    *,
    mapped_link_count: int = 0,
) -> EvidenceFeatures:
    """Contract-only adapter for orchestrators that do not need review detail."""

    return extract_entity_safe_features(
        account_id,
        pages,
        eligibility,
        mapped_link_count=mapped_link_count,
    ).features


__all__ = [
    "CachedEvidencePage",
    "CandidateStatus",
    "EvidenceExtractionResult",
    "FeatureEligibility",
    "OfficeCandidate",
    "StaffCandidate",
    "StaffCategory",
    "build_evidence_features",
    "extract_entity_safe_features",
    "extract_office_candidates",
    "extract_staff_candidates",
    "normalize_address",
]
