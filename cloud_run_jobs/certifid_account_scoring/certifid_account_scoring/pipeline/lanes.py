"""Explicit, fail-closed ICP lane classification.

Lane classification is independent of value estimation.  The module has no
MCV/ARR or anchor input, so a historical or rep-provided value cannot bypass
identity, sellable-unit, lifecycle, or ICP eligibility.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping, Sequence

from .config import LANE_VERSION
from .contracts import (
    Confidence,
    EvidenceCitation,
    Lane,
    LaneDecision,
    SellableUnitDecision,
    WebsiteBinding,
)
from .evidence_features import CachedEvidencePage


_ATTORNEY_PRIMARY_STATES = {
    "CT",
    "DE",
    "GA",
    "IA",
    "KY",
    "MA",
    "MS",
    "NC",
    "NY",
    "SC",
    "VT",
    "WV",
}
_ATTORNEY_RELEVANT_STATES = _ATTORNEY_PRIMARY_STATES | {
    "AL",
    "FL",
    "IL",
    "LA",
    "ME",
    "NH",
    "NJ",
    "NV",
    "RI",
    "TN",
    "VA",
    "WA",
}

_LAW_NAME_RE = re.compile(
    r"\b(law (?:firm|office|offices|group)|attorneys? at law|lawyers?|counselors?|"
    r"llp|p\.?l\.?l\.?c\.?|p\.?c\.?)\b",
    re.I,
)
_LAW_SITE_RE = re.compile(
    r"\b(we are (?:a )?law firm|our law firm|law offices? of|attorneys? at law|"
    r"law firm (?:focused|concentrating|specializing))\b",
    re.I,
)

_LEGAL_OPERATIONAL_CLOSING: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("closing_attorney", re.compile(r"\bclosing attorneys?\b", re.I)),
    ("conducts_closings", re.compile(r"\b(?:conduct|handle|coordinate|perform)s? (?:real estate )?closings?\b", re.I)),
    ("purchase_refinance_closing", re.compile(r"\b(?:purchase|refinance|residential|commercial) closings?\b", re.I)),
    ("real_estate_closing", re.compile(r"\breal estate closings?\b", re.I)),
    ("settlement_agent", re.compile(r"\bregistered settlement agent|\bsettlement agents?\b", re.I)),
)
_LEGAL_WEAK_REAL_ESTATE: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("real_estate_law", re.compile(r"\breal estate law\b", re.I)),
    ("real_estate_transaction", re.compile(r"\breal estate transactions?\b", re.I)),
    ("buyer_or_seller_representation", re.compile(r"\b(?:buyer|seller|lender) representation\b", re.I)),
    ("property_law", re.compile(r"\bproperty law\b", re.I)),
)
_LEGAL_GENERAL_PRACTICE: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pattern, re.I))
    for label, pattern in (
        ("bankruptcy", r"\bbankruptcy\b"),
        ("criminal", r"\bcriminal (?:law|defense)\b"),
        ("corporate", r"\bcorporate law\b"),
        ("estate_planning", r"\bestate planning\b"),
        ("family_law", r"\bfamily law\b"),
        ("immigration", r"\bimmigration\b"),
        ("litigation", r"\b(?:civil|business|commercial)?\s*litigation\b"),
        ("personal_injury", r"\bpersonal injury\b"),
        ("probate", r"\bprobate\b"),
        ("tax", r"\btax law\b"),
    )
)
_AFFILIATED_TITLE_RE = re.compile(
    r"\b(our affiliated title (?:company|agency)|affiliated with .{0,40}\btitle\b|"
    r"law(?: firm)?[- ]owned title (?:company|agency)|title (?:company|agency) affiliate)\b",
    re.I,
)

_UNDERWRITER_RE = re.compile(
    r"\b(title insurance underwriter|national underwriter|underwriting company|"
    r"we underwrite title insurance|title underwriters?)\b",
    re.I,
)
_OWNED_DIRECT_RE = re.compile(
    r"\b(direct operation|direct office of|owned and operated by .{0,50}\bunderwriter\b|"
    r"underwriter[- ]owned|wholly owned (?:title )?subsidiary)\b",
    re.I,
)
_BROKERAGE_RE = re.compile(r"\b(real estate brokerage|realty|realtors?|brokerage services?|real estate agency)\b", re.I)
_BROKERAGE_AFFILIATION_RE = re.compile(
    r"\b(affiliated business arrangement|brokerage[- ]affiliated|owned by .{0,40}\b(?:realty|brokerage)\b|"
    r"our affiliated (?:title|escrow) (?:company|agency))\b",
    re.I,
)
_ABSTRACT_RE = re.compile(r"\b(abstract company|abstracting|title search(?:es)?|abstract and search)\b", re.I)
_OPERATOR_TERMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("escrow_services", re.compile(r"\bescrow (?:services?|closings?|company|agency)\b", re.I)),
    ("settlement_services", re.compile(r"\bsettlement (?:services?|closings?|company|agency|agents?)\b", re.I)),
    ("title_insurance", re.compile(r"\btitle insurance (?:agency|company|services?|policies|agent)?\b", re.I)),
    ("title_and_closing", re.compile(r"\btitle and (?:escrow|closing|settlement)\b", re.I)),
    ("closing_services", re.compile(r"\bclosing services?\b", re.I)),
)
_TITLE_NAME_RE = re.compile(r"\b(title|escrow|settlement|abstract)\b", re.I)

_ADJACENT_CLASSIFIERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("bank_credit_union", re.compile(r"\b(bank|credit union|depository institution)\b", re.I)),
    ("lender_mortgage", re.compile(r"\b(mortgage (?:lender|broker|company)|home loans?|loan origination|lending)\b", re.I)),
    ("brokerage_real_estate_agency", _BROKERAGE_RE),
    ("government_county", re.compile(r"\b(county government|county clerk|recorder of deeds|register of deeds|municipal|government agency)\b", re.I)),
    ("educational", re.compile(r"\b(university|college|school of law|educational institution)\b", re.I)),
    ("insurance_public_adjuster", re.compile(r"\b(public adjuster|claims adjusting|property and casualty insurance)\b", re.I)),
    ("software_vendor", re.compile(r"\b(software|saas|technology platform|workflow platform|vendor|data provider)\b", re.I)),
    ("exchange_1031_qi", re.compile(r"\b(1031 exchange|qualified intermediary)\b", re.I)),
)


@dataclass(frozen=True)
class LaneInput:
    account_id: str
    account_name: str
    crm_company_type: str
    billing_state: str
    pages: tuple[CachedEvidencePage, ...]
    binding_status: str
    binding_confidence: Confidence | str
    sellable_unit_eligible: bool
    sellable_unit_confidence: Confidence | str
    lifecycle: str = "net_new"
    crm_observed_at: str = ""

    @classmethod
    def from_contracts(
        cls,
        account_row: Mapping[str, Any],
        pages: Sequence[CachedEvidencePage],
        binding: WebsiteBinding,
        sellable_unit: SellableUnitDecision,
        *,
        crm_observed_at: str = "",
    ) -> "LaneInput":
        """Adapt canonical upstream contracts and the raw CRM Company Type.

        Reading ``Company_Type__c`` here prevents a law-firm row from being
        silently lost when a caller uses a legacy alias.
        """

        company_type = str(
            account_row.get("Company_Type__c")
            or account_row.get("CompanyType")
            or account_row.get("ReportCompanyType")
            or ""
        ).strip()
        return cls(
            account_id=str(account_row.get("Id") or binding.account_id or "").strip(),
            account_name=str(account_row.get("Name") or binding.account_name or "").strip(),
            crm_company_type=company_type,
            billing_state=str(account_row.get("BillingState") or account_row.get("BillingStateCode") or "").strip(),
            pages=tuple(pages),
            binding_status=binding.status.value,
            binding_confidence=binding.confidence,
            sellable_unit_eligible=sellable_unit.standalone_score_eligible,
            sellable_unit_confidence=sellable_unit.confidence,
            lifecycle=sellable_unit.lifecycle.value,
            crm_observed_at=crm_observed_at,
        )

    @property
    def strong_upstream_eligibility(self) -> bool:
        return (
            self.binding_status.strip().lower() in {"bound", "correct_website", "confirmed"}
            and _confidence_value(self.binding_confidence) == Confidence.HIGH.value
            and self.sellable_unit_eligible
            and _confidence_value(self.sellable_unit_confidence) == Confidence.HIGH.value
            and self.lifecycle.strip().lower() == "net_new"
        )


@dataclass(frozen=True)
class _Signal:
    label: str
    citation: EvidenceCitation


def _confidence_value(value: Confidence | str) -> str:
    if isinstance(value, Confidence):
        return value.value
    raw = str(value or "").strip().lower()
    return {"high": "High", "medium": "Medium", "low": "Low"}.get(raw, str(value or ""))


def _all_text(record: LaneInput) -> str:
    return "\n".join(f"{page.title}\n{page.text}" for page in record.pages)


def _excerpt(text: str, match: re.Match[str], radius: int = 100) -> str:
    value = text[max(0, match.start() - radius) : min(len(text), match.end() + radius)]
    return re.sub(r"\s+", " ", value).strip()[:280]


def _match_pages(
    pages: Sequence[CachedEvidencePage],
    definitions: Sequence[tuple[str, re.Pattern[str]]],
) -> tuple[_Signal, ...]:
    found: dict[tuple[str, str], _Signal] = {}
    for page in pages:
        page_text = f"{page.title}\n{page.text}"
        for label, pattern in definitions:
            match = pattern.search(page_text)
            if not match:
                continue
            signal = _Signal(label=label, citation=page.citation(_excerpt(page_text, match)))
            found[(label, page.url.lower())] = signal
    return tuple(found[key] for key in sorted(found))


def _match_one(pages: Sequence[CachedEvidencePage], label: str, pattern: re.Pattern[str]) -> tuple[_Signal, ...]:
    return _match_pages(pages, ((label, pattern),))


def _dedupe_citations(signals: Iterable[_Signal]) -> tuple[EvidenceCitation, ...]:
    unique: dict[tuple[str, str, str, str], EvidenceCitation] = {}
    for signal in signals:
        citation = signal.citation
        key = (citation.source, citation.reference, citation.observed_at, citation.excerpt)
        unique[key] = citation
    return tuple(unique[key] for key in sorted(unique))


def _crm_law_citation(record: LaneInput) -> EvidenceCitation:
    return EvidenceCitation(
        source="salesforce_account",
        reference=f"{record.account_id}#Company_Type__c",
        observed_at=record.crm_observed_at,
        excerpt=f"Company_Type__c={record.crm_company_type}",
        evidence_hash="",
    )


def _is_law_firm(record: LaneInput, text: str) -> tuple[bool, tuple[str, ...], tuple[EvidenceCitation, ...]]:
    reasons: list[str] = []
    citations: list[EvidenceCitation] = []
    if record.crm_company_type.strip().lower() == "law firm":
        reasons.append("crm_company_type_law_firm")
        citations.append(_crm_law_citation(record))
    if _LAW_NAME_RE.search(record.account_name):
        reasons.append("law_firm_name_signal")
    site_signals = _match_one(record.pages, "law_firm_site_assertion", _LAW_SITE_RE)
    if site_signals:
        reasons.append("law_firm_site_assertion")
        citations.extend(signal.citation for signal in site_signals)
    return bool(reasons), tuple(sorted(set(reasons))), tuple(citations)


def _upstream_blocks(record: LaneInput) -> tuple[tuple[str, ...], tuple[str, ...]]:
    codes: list[str] = []
    reasons: list[str] = []
    if record.binding_status.strip().lower() not in {"bound", "correct_website", "confirmed"}:
        codes.append("website_not_bound")
        reasons.append("Website binding is not confirmed.")
    if _confidence_value(record.binding_confidence) != Confidence.HIGH.value:
        codes.append("website_binding_not_high_confidence")
        reasons.append("Website binding confidence is not High.")
    if not record.sellable_unit_eligible:
        codes.append("sellable_unit_not_standalone_eligible")
        reasons.append("The Account is not an independently sellable standalone unit.")
    if _confidence_value(record.sellable_unit_confidence) != Confidence.HIGH.value:
        codes.append("sellable_unit_not_high_confidence")
        reasons.append("Sellable-unit confidence is not High.")
    if record.lifecycle.strip().lower() != "net_new":
        codes.append("lifecycle_not_net_new")
        reasons.append("Only net-new lifecycle rows are eligible for automatic value scoring.")
    return tuple(codes), tuple(reasons)


def _decision(
    record: LaneInput,
    *,
    lane: Lane,
    subtype: str,
    classification_eligible: bool,
    confidence: Confidence,
    reason_codes: Iterable[str],
    reasons: Iterable[str],
    signals: Iterable[_Signal] = (),
    extra_citations: Iterable[EvidenceCitation] = (),
) -> LaneDecision:
    upstream_codes, upstream_reasons = _upstream_blocks(record)
    eligible = classification_eligible and not upstream_codes
    citations = list(extra_citations)
    citations.extend(signal.citation for signal in signals)
    unique: dict[tuple[str, str, str, str], EvidenceCitation] = {}
    for citation in citations:
        key = (citation.source, citation.reference, citation.observed_at, citation.excerpt)
        unique[key] = citation
    return LaneDecision(
        account_id=record.account_id,
        lane=lane,
        subtype=subtype,
        eligible_for_value=eligible,
        confidence=confidence,
        reason_codes=tuple(sorted(set(reason_codes) | set(upstream_codes))),
        reasons=tuple(dict.fromkeys([*reasons, *upstream_reasons])),
        citations=tuple(unique[key] for key in sorted(unique)),
        lane_version=LANE_VERSION,
    )


def _classify_legal(
    record: LaneInput,
    law_reason_codes: Sequence[str],
    law_citations: Sequence[EvidenceCitation],
) -> LaneDecision:
    closing = _match_pages(record.pages, _LEGAL_OPERATIONAL_CLOSING)
    weak_re = _match_pages(record.pages, _LEGAL_WEAK_REAL_ESTATE)
    general = _match_pages(record.pages, _LEGAL_GENERAL_PRACTICE)
    affiliated = _match_one(record.pages, "affiliated_title_entity", _AFFILIATED_TITLE_RE)
    closing_labels = {signal.label for signal in closing}
    general_labels = {signal.label for signal in general}
    weak_labels = {signal.label for signal in weak_re}

    base_codes = list(law_reason_codes)
    base_reasons = ["Account is classified in the legal lane."]
    all_signals = tuple([*closing, *weak_re, *general, *affiliated])

    if affiliated and (closing or weak_re):
        subtype = "affiliated_title_entity"
        eligible = True
        confidence = Confidence.HIGH
        base_codes.extend(("legal_affiliated_title_evidence", "legal_closing_or_re_evidence"))
        base_reasons.append("The law firm has cited affiliated-title and real-estate-closing evidence.")
    elif closing_labels and len(general_labels) <= 1:
        subtype = "real_estate_closing_focused"
        eligible = True
        confidence = Confidence.HIGH if len(closing_labels) >= 2 else Confidence.MEDIUM
        base_codes.append("legal_operational_closing_evidence")
        base_reasons.append("Cited evidence describes operational real-estate closing work.")
    elif closing_labels and len(general_labels) >= 2:
        subtype = "general_practice_meaningful_re_closing_arm"
        eligible = True
        confidence = Confidence.MEDIUM
        base_codes.extend(("legal_operational_closing_evidence", "legal_broad_practice_evidence"))
        base_reasons.append("A broad-practice firm has a cited, meaningful real-estate-closing arm.")
    elif weak_labels or (general_labels and closing_labels):
        subtype = "unclear_broad_practice"
        eligible = False
        confidence = Confidence.MEDIUM
        base_codes.append("legal_real_estate_practice_not_operationally_proven")
        base_reasons.append("Real-estate practice evidence does not prove a closing operation.")
    elif general_labels:
        subtype = "non_closing_legal"
        eligible = False
        confidence = Confidence.HIGH if len(general_labels) >= 2 else Confidence.MEDIUM
        base_codes.append("legal_non_closing_practice_evidence")
        base_reasons.append("Cited practice evidence is legal but not real-estate-closing focused.")
    elif record.billing_state.strip().upper() in _ATTORNEY_RELEVANT_STATES:
        subtype = "state_market_review"
        eligible = False
        confidence = Confidence.MEDIUM
        base_codes.append("legal_state_market_review_required")
        base_reasons.append("Attorney participation may be market-relevant, but closing evidence is absent.")
    else:
        subtype = "unclear_broad_practice"
        eligible = False
        confidence = Confidence.LOW
        base_codes.append("legal_practice_evidence_insufficient")
        base_reasons.append("The legal practice mix and closing relevance are unresolved.")

    return _decision(
        record,
        lane=Lane.LEGAL,
        subtype=subtype,
        classification_eligible=eligible,
        confidence=confidence,
        reason_codes=base_codes,
        reasons=base_reasons,
        signals=all_signals,
        extra_citations=law_citations,
    )


def _adjacent_match(record: LaneInput) -> tuple[str, tuple[_Signal, ...]] | None:
    for subtype, pattern in _ADJACENT_CLASSIFIERS:
        signals = _match_one(record.pages, f"adjacent_{subtype}", pattern)
        name_match = pattern.search(f"{record.account_name} {record.crm_company_type}")
        if signals or name_match:
            return subtype, signals
    return None


def classify_icp_lane(record: LaneInput) -> LaneDecision:
    """Classify one Account into explicit title/escrow, legal, or adjacent lanes."""

    pages = tuple(sorted(record.pages, key=lambda page: (page.url.lower(), page.observed_at, page.title)))
    if pages != record.pages:
        record = LaneInput(
            account_id=record.account_id,
            account_name=record.account_name,
            crm_company_type=record.crm_company_type,
            billing_state=record.billing_state,
            pages=pages,
            binding_status=record.binding_status,
            binding_confidence=record.binding_confidence,
            sellable_unit_eligible=record.sellable_unit_eligible,
            sellable_unit_confidence=record.sellable_unit_confidence,
            lifecycle=record.lifecycle,
            crm_observed_at=record.crm_observed_at,
        )

    text = f"{record.account_name}\n{record.crm_company_type}\n{_all_text(record)}"
    is_law, law_codes, law_citations = _is_law_firm(record, text)
    if is_law:
        return _classify_legal(record, law_codes, law_citations)

    owned_direct = _match_one(record.pages, "owned_direct_operation", _OWNED_DIRECT_RE)
    if owned_direct:
        return _decision(
            record,
            lane=Lane.TITLE_ESCROW,
            subtype="owned_direct",
            classification_eligible=False,
            confidence=Confidence.HIGH,
            reason_codes=("owned_direct_relationship_not_standalone",),
            reasons=("Cited evidence identifies an owned/direct operation requiring hierarchy handling.",),
            signals=owned_direct,
        )

    underwriter = _match_one(record.pages, "title_underwriter", _UNDERWRITER_RE)
    if underwriter or _UNDERWRITER_RE.search(record.account_name):
        return _decision(
            record,
            lane=Lane.TITLE_ESCROW,
            subtype="underwriter",
            classification_eligible=False,
            confidence=Confidence.HIGH,
            reason_codes=("title_underwriter_not_standalone_prospect",),
            reasons=("Entity is a title-insurance underwriter, not an independent agent operation.",),
            signals=underwriter,
        )

    brokerage = _match_one(record.pages, "brokerage_affiliation", _BROKERAGE_AFFILIATION_RE)
    operator = _match_pages(record.pages, _OPERATOR_TERMS)
    if brokerage and operator:
        return _decision(
            record,
            lane=Lane.TITLE_ESCROW,
            subtype="brokerage_affiliated",
            classification_eligible=False,
            confidence=Confidence.HIGH,
            reason_codes=("brokerage_affiliation_requires_sellable_unit_review",),
            reasons=("The closing operation is brokerage-affiliated and cannot auto-score standalone.",),
            signals=tuple([*brokerage, *operator]),
        )

    adjacent = _adjacent_match(record)
    if adjacent:
        adjacent_subtype, adjacent_signals = adjacent
        return _decision(
            record,
            lane=Lane.ADJACENT,
            subtype=f"adjacent_{adjacent_subtype}",
            classification_eligible=False,
            confidence=Confidence.HIGH if adjacent_signals else Confidence.MEDIUM,
            reason_codes=(f"adjacent_{adjacent_subtype}",),
            reasons=("Entity is adjacent to the title/escrow/legal ICP and is not auto-score eligible.",),
            signals=adjacent_signals,
        )

    abstract = _match_one(record.pages, "abstract_services", _ABSTRACT_RE)
    if (abstract or _ABSTRACT_RE.search(record.account_name)) and not operator:
        return _decision(
            record,
            lane=Lane.TITLE_ESCROW,
            subtype="abstract_only",
            classification_eligible=False,
            confidence=Confidence.MEDIUM,
            reason_codes=("abstract_only_closing_operation_unproven",),
            reasons=("Abstract/search evidence is present without a verified closing, settlement, escrow, or title-insurance operation.",),
            signals=abstract,
        )

    if operator:
        return _decision(
            record,
            lane=Lane.TITLE_ESCROW,
            subtype="title_escrow_settlement_operator",
            classification_eligible=True,
            confidence=Confidence.HIGH if len({signal.label for signal in operator}) >= 2 else Confidence.MEDIUM,
            reason_codes=("title_escrow_operational_evidence",),
            reasons=("Cited evidence identifies an operating title, escrow, settlement, or closing business.",),
            signals=operator,
        )

    if _TITLE_NAME_RE.search(record.account_name) or _TITLE_NAME_RE.search(text):
        title_signal = _match_one(record.pages, "ambiguous_title_signal", _TITLE_NAME_RE)
        return _decision(
            record,
            lane=Lane.TITLE_ESCROW,
            subtype="ambiguous",
            classification_eligible=False,
            confidence=Confidence.LOW,
            reason_codes=("title_lane_operating_evidence_insufficient",),
            reasons=("Title/escrow-adjacent wording is present, but operating ICP evidence is insufficient.",),
            signals=title_signal,
        )

    return _decision(
        record,
        lane=Lane.REVIEW,
        subtype="ambiguous",
        classification_eligible=False,
        confidence=Confidence.LOW,
        reason_codes=("no_explicit_icp_lane_evidence",),
        reasons=("Cached evidence does not establish a title/escrow operator, closing-relevant legal entity, or known adjacent class.",),
    )


def classify_lane(record: LaneInput) -> LaneDecision:
    """Short adapter name for orchestration code."""

    return classify_icp_lane(record)


__all__ = ["LaneInput", "classify_icp_lane", "classify_lane"]
