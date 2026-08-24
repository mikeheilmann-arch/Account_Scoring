"""Legal-entity classification helpers for CertifID prospect value scoring."""

from __future__ import annotations

from dataclasses import dataclass
import re


STATE_ABBR_BY_NAME = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
    "district of columbia": "DC",
}

ATTORNEY_PRIMARY_STATES = {
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

ATTORNEY_RELEVANT_STATES = ATTORNEY_PRIMARY_STATES | {
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

LAW_NAME_RE = re.compile(
    r"\b("
    r"law(?:\s+(?:firm|office|offices|group))?|"
    r"attorneys?(?:\s+at\s+law)?|"
    r"counsel(?:ors?)?|"
    r"esq\.?|"
    r"llp|p\.?l\.?l\.?c\.?|p\.?c\.?|p\.?l\.?c\.?"
    r")\b",
    re.I,
)

TITLE_OPERATOR_RE = re.compile(r"\b(title|escrow|settlement|abstract|closing|closings|underwriter)\b", re.I)
TITLE_ESCROW_COMPANY_TYPE_RE = re.compile(r"\b(title|escrow|settlement|abstract)\b", re.I)
TITLE_COMPANY_LAW_OVERRIDE_RE = re.compile(
    r"\b("
    r"real\s+estate\s+law\s+(?:firm|practice)|"
    r"(?:we\s+are|our)\s+(?:a\s+)?law\s+firm|"
    r"law\s+firm\s+(?:focused|concentrating|specializing)\s+in\s+real\s+estate"
    r")",
    re.I,
)

REAL_ESTATE_CLOSING_TERMS = {
    "real estate closing": "real estate closing",
    "real estate closings": "real estate closings",
    "residential closing": "residential closing",
    "residential closings": "residential closings",
    "commercial closing": "commercial closing",
    "commercial closings": "commercial closings",
    "closing attorney": "closing attorney",
    "closing attorneys": "closing attorneys",
    "settlement agent": "settlement agent",
    "settlement agents": "settlement agents",
    "settlement services": "settlement services",
    "title services": "title services",
    "title insurance": "title insurance",
    "escrow services": "escrow services",
    "real estate law": "real estate law",
    "real estate attorney": "real estate attorney",
    "real estate attorneys": "real estate attorneys",
    "real estate transaction": "real estate transaction",
    "real estate transactions": "real estate transactions",
    "purchase and sale": "purchase and sale",
    "refinance closing": "refinance closing",
    "buyer representation": "buyer representation",
    "seller representation": "seller representation",
    "lender representation": "lender representation",
}

GENERAL_PRACTICE_TERMS = {
    "antitrust": "antitrust",
    "appellate": "appellate",
    "business litigation": "business litigation",
    "personal injury": "personal injury",
    "family law": "family law",
    "divorce": "divorce",
    "criminal": "criminal",
    "dui": "DUI",
    "immigration": "immigration",
    "bankruptcy": "bankruptcy",
    "class action": "class action",
    "corporate": "corporate",
    "data security": "data security",
    "estate planning": "estate planning",
    "probate": "probate",
    "insurance defense": "insurance defense",
    "intellectual property": "intellectual property",
    "litigation": "litigation",
    "mergers and acquisitions": "mergers and acquisitions",
    "privacy": "privacy",
    "securities": "securities",
    "employment law": "employment law",
    "workers compensation": "workers compensation",
    "medical malpractice": "medical malpractice",
    "tax law": "tax law",
    "elder law": "elder law",
}

OPERATIONAL_CLOSING_LABELS = {
    "closing attorney",
    "closing attorneys",
    "commercial closing",
    "commercial closings",
    "escrow services",
    "real estate closing",
    "real estate closings",
    "refinance closing",
    "residential closing",
    "residential closings",
    "settlement agent",
    "settlement agents",
    "settlement services",
    "title services",
}

WEAK_REAL_ESTATE_LABELS = {
    "buyer representation",
    "lender representation",
    "purchase and sale",
    "real estate attorney",
    "real estate attorneys",
    "real estate law",
    "real estate transaction",
    "real estate transactions",
    "seller representation",
    "title insurance",
}

TITLE_SERVICE_SIGNALS = {"abstract", "escrow", "real estate closings", "settlement", "title insurance"}
NATIONAL_LANGUAGE_RE = re.compile(r"\b(national|nationwide|all 50 states|multi-state|multistate)\b", re.I)

LAW_ROUTE_LABELS = {
    "not_legal_entity": "not legal entity",
    "legal_real_estate_closing_focused": "RE-closing focused law firm",
    "legal_real_estate_practice_unclear": "real estate practice unclear",
    "legal_general_practice_low_fit": "general-practice legal low fit",
    "legal_affiliated_title_entity": "law/title affiliated entity",
    "legal_market_review": "legal market/practice review",
}


@dataclass(frozen=True)
class LegalProfile:
    route: str
    market_fit: str
    state: str
    is_law_firm: bool
    score_now_eligible: bool
    non_icp: bool
    review_needed: bool
    mcv_floor: int | None
    confidence_adjustment: str
    evidence: str
    real_estate_hits: tuple[str, ...]
    general_practice_hits: tuple[str, ...]


def clean(value: object) -> str:
    return str(value or "").strip()


def normalize_state(value: object) -> str:
    text = clean(value)
    if not text:
        return ""
    upper = text.upper()
    if upper in STATE_ABBR_BY_NAME.values():
        return upper
    return STATE_ABBR_BY_NAME.get(text.lower(), "")


def keyword_hits(text: str, terms: dict[str, str]) -> tuple[str, ...]:
    low = text.lower()
    hits = {
        label
        for term, label in terms.items()
        if re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", low)
    }
    return tuple(sorted(hits))


def legal_market_fit(state: str) -> str:
    if state in ATTORNEY_PRIMARY_STATES:
        return "attorney_primary"
    if state in {"IL", "NJ"}:
        return "county_nuanced"
    if state in ATTORNEY_RELEVANT_STATES:
        return "attorney_relevant"
    return "standard"


def broad_general_practice(general_hit_count: int, office_count: int, text: str) -> bool:
    national = bool(NATIONAL_LANGUAGE_RE.search(text[:12000]))
    if general_hit_count >= 4:
        return True
    if general_hit_count >= 3 and (office_count >= 5 or national):
        return True
    if general_hit_count >= 1 and office_count >= 10 and national:
        return True
    return False


def detect_law_firm(row: dict[str, str], text: str) -> bool:
    name = clean(row.get("Name"))
    company_type = clean(row.get("CompanyType") or row.get("ReportCompanyType"))
    industry = clean(row.get("Industry"))
    if company_type.lower() == "law firm":
        return True
    if TITLE_ESCROW_COMPANY_TYPE_RE.search(company_type):
        return bool(TITLE_COMPANY_LAW_OVERRIDE_RE.search(text[:8000]))
    if "legal" in industry.lower() and not TITLE_OPERATOR_RE.search(name):
        return True
    if LAW_NAME_RE.search(name):
        return True
    if re.search(r"\b(law firm|law offices?|attorneys?\s+at\s+law|real estate law practice)\b", text[:8000], re.I):
        # Text-only detection must be stronger than title sites mentioning attorneys as customers.
        return True
    return False


def legal_mcv_floor(route: str, market_fit: str, office_count: int, staff_count: int, quality: int) -> int | None:
    if route not in {"legal_real_estate_closing_focused", "legal_affiliated_title_entity"}:
        return None
    strong_market = market_fit in {"attorney_primary", "attorney_relevant", "county_nuanced"}
    if office_count >= 4:
        return 150 if strong_market else 125
    if office_count >= 2:
        if staff_count >= 10 or quality >= 4:
            return 100 if strong_market else 75
        return 75 if strong_market else 50
    if office_count >= 1 and (staff_count >= 10 or quality >= 4):
        return 75 if strong_market else 50
    if strong_market and quality >= 3:
        return 50
    return None


def classify_legal_entity(
    row: dict[str, str],
    text: str,
    services: list[str],
    office_count: int,
    staff_count: int,
    quality: int,
) -> LegalProfile:
    state = normalize_state(row.get("BillingState"))
    market_fit = legal_market_fit(state)
    is_law = detect_law_firm(row, text)
    if not is_law:
        return LegalProfile(
            route="not_legal_entity",
            market_fit=market_fit,
            state=state,
            is_law_firm=False,
            score_now_eligible=True,
            non_icp=False,
            review_needed=False,
            mcv_floor=None,
            confidence_adjustment="",
            evidence="",
            real_estate_hits=(),
            general_practice_hits=(),
        )

    real_estate_hits = keyword_hits(text, REAL_ESTATE_CLOSING_TERMS)
    general_hits = keyword_hits(text, GENERAL_PRACTICE_TERMS)
    name = clean(row.get("Name"))
    title_in_name = bool(TITLE_OPERATOR_RE.search(name))
    closing_hit_count = len(real_estate_hits)
    general_hit_count = len(general_hits)
    operational_closing_hits = tuple(hit for hit in real_estate_hits if hit in OPERATIONAL_CLOSING_LABELS)
    weak_real_estate_hits = tuple(hit for hit in real_estate_hits if hit in WEAK_REAL_ESTATE_LABELS)
    title_service_hit_count = sum(1 for signal in services if signal in TITLE_SERVICE_SIGNALS)
    broad_practice = broad_general_practice(general_hit_count, office_count, text)

    if title_in_name and (closing_hit_count >= 1 or title_service_hit_count >= 1):
        route = "legal_affiliated_title_entity"
    elif not broad_practice and (
        len(operational_closing_hits) >= 2
        or (len(operational_closing_hits) >= 1 and title_service_hit_count >= 1)
    ):
        route = "legal_affiliated_title_entity"
    elif broad_practice and (closing_hit_count or title_service_hit_count):
        route = "legal_real_estate_practice_unclear"
    elif broad_practice:
        route = "legal_general_practice_low_fit"
    elif len(operational_closing_hits) >= 1 and market_fit in {"attorney_primary", "attorney_relevant", "county_nuanced"}:
        route = "legal_real_estate_closing_focused"
    elif closing_hit_count >= 3 and general_hit_count <= 2 and title_service_hit_count >= 1:
        route = "legal_real_estate_closing_focused"
    elif closing_hit_count >= 1 or title_service_hit_count >= 1:
        route = "legal_real_estate_practice_unclear"
    elif general_hit_count >= 1:
        route = "legal_general_practice_low_fit"
    elif market_fit in {"attorney_primary", "attorney_relevant", "county_nuanced"}:
        route = "legal_market_review"
    else:
        route = "legal_general_practice_low_fit"

    non_icp = route == "legal_general_practice_low_fit"
    review_needed = route in {"legal_real_estate_practice_unclear", "legal_market_review"}
    score_now_eligible = route in {"legal_real_estate_closing_focused", "legal_affiliated_title_entity"}
    mcv_floor = legal_mcv_floor(route, market_fit, office_count, staff_count, quality)

    evidence_parts = [LAW_ROUTE_LABELS[route], f"market={market_fit}"]
    if real_estate_hits:
        evidence_parts.append("RE/closing evidence: " + ", ".join(real_estate_hits[:6]))
    if operational_closing_hits:
        evidence_parts.append("operational closing evidence: " + ", ".join(operational_closing_hits[:6]))
    if weak_real_estate_hits and not operational_closing_hits:
        evidence_parts.append("weak RE evidence only: " + ", ".join(weak_real_estate_hits[:6]))
    if general_hits:
        evidence_parts.append("general-practice evidence: " + ", ".join(general_hits[:5]))
    if broad_practice:
        evidence_parts.append("broad/national general-practice review")
    if mcv_floor:
        evidence_parts.append(f"legal-market MCV floor={mcv_floor}")

    confidence_adjustment = ""
    if route == "legal_real_estate_closing_focused" and market_fit == "attorney_primary":
        confidence_adjustment = "raise_to_medium"
    elif route in {"legal_real_estate_practice_unclear", "legal_market_review"}:
        confidence_adjustment = "review"

    return LegalProfile(
        route=route,
        market_fit=market_fit,
        state=state,
        is_law_firm=True,
        score_now_eligible=score_now_eligible,
        non_icp=non_icp,
        review_needed=review_needed,
        mcv_floor=mcv_floor,
        confidence_adjustment=confidence_adjustment,
        evidence="; ".join(evidence_parts),
        real_estate_hits=real_estate_hits,
        general_practice_hits=general_hits,
    )


def apply_legal_mcv_floor(mcv: int, legal_profile: LegalProfile) -> tuple[int, str]:
    if not legal_profile.mcv_floor or legal_profile.mcv_floor <= mcv:
        return mcv, ""
    return legal_profile.mcv_floor, (
        f"Legal-entity lane raised website estimate from {mcv} to {legal_profile.mcv_floor} "
        f"({LAW_ROUTE_LABELS.get(legal_profile.route, legal_profile.route)}, {legal_profile.market_fit})"
    )
