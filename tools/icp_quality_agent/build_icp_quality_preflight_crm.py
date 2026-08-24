#!/usr/bin/env python3
"""Build a no-write ICP/entity-quality preflight for a full CRM scoring run.

The output is intentionally separate from the value scorer. It decides which
Salesforce Accounts are eligible to spend Nimble/scoring work on, and which
should be suppressed or routed to Ops review first.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ACCOUNT_EXPORT = ROOT / "tmp" / "certifid_scoring_gcp" / "crm_full_20260708" / "account_context_sfdc_prod_2026-07-08.csv"
DEFAULT_ALTA_MATCHES = ROOT / "artifacts" / "alta_enrichment" / "alta_sfdc_matches.csv"
DEFAULT_ALTA_FLAGS = ROOT / "artifacts" / "alta_enrichment" / "sfdc_alta_enrichment.csv"
DEFAULT_OUT_DIR = ROOT / "tmp" / "icp_quality_agent_crm_full_20260708"

OVERLAY_NAME = "icp_quality_agent_crm_full_overlay.csv"
REVIEW_NAME = "icp_quality_agent_crm_full_review_queue.csv"
SCOREABLE_INPUT_NAME = "account_scoring_crm_full_scoreable_input_2026-07-08.csv"
SUMMARY_NAME = "icp_quality_agent_crm_full_summary.json"
READOUT_NAME = "icp_quality_agent_crm_full_readout.md"

SCORING_INPUT_COLUMNS = [
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
    "FinalMCV",
    "MCVSource",
    "HasAnyOpp",
    "LegacyTier",
    "WebsiteHygiene",
]

OVERLAY_COLUMNS = [
    "AccountId",
    "AccountName",
    "Website",
    "WebsiteDomain",
    "BillingState",
    "CrmType",
    "CrmAccountStatus",
    "CrmCompanyType",
    "ActiveCustomer",
    "ParentId",
    "ParentName",
    "FinalMCV",
    "MCVSource",
    "WebsiteHygiene",
    "AltaMember",
    "AltaId",
    "AltaMatchConfidence",
    "AltaName",
    "AltaState",
    "DomainClusterSize",
    "DomainCustomerAccountIds",
    "DomainCustomerAccountNames",
    "QualityDisposition",
    "QualityAction",
    "QualityConfidence",
    "QualityReason",
    "QualityEvidence",
    "SourceFlags",
    "SuggestedSellableAccountId",
    "SuggestedSellableAccountName",
    "ScoreEligible",
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
    "alta.org": "ALTA directory / association domain",
}

ICP_COMPANY_TYPES = {"title company", "law firm", "escrow company"}
NON_ICP_COMPANY_TYPE_TOKENS = {
    "bank",
    "broker",
    "brokerage",
    "builder",
    "credit union",
    "insurance",
    "mortgage",
    "real estate agent",
    "software",
    "underwriter",
    "vendor",
}

HARD_STATUS_TOKENS = {
    "bad debt",
    "disqualified",
    "do not contact",
    "no business use case",
    "no longer in business",
    "not icp",
}

BAD_HYGIENE_STATUSES = {
    "blocked",
    "dns_error",
    "missing",
    "needs_review",
    "no_url",
    "parked_or_for_sale",
    "parked_or_placeholder",
    "redirects_unrelated",
    "server_error",
    "ssl_error",
    "suspended_or_inactive",
    "timeout",
}

BANK_DOMAIN_RE = re.compile(r"(?:^|[-.])(bank|banks|creditunion|creditunions|cu|mortgage|mortgages|lending|lender|lenders)(?:[-.]|$)", re.I)
BROKERAGE_DOMAIN_RE = re.compile(r"(?:^|[-.])(broker|brokerage|realtor|realty|properties|mls)(?:[-.]|$)", re.I)
GOV_DOMAIN_RE = re.compile(r"(?:\\.gov$|(?:^|[-.])(county|clerk|recorder|registerofdeeds|register-of-deeds|municipal)(?:[-.]|$))", re.I)
TITLE_OPERATOR_RE = re.compile(r"\b(title|escrow|settlement|closing|closings|abstract)\b", re.I)


def clean(value: object) -> str:
    return ("" if value is None else str(value)).strip()


def truthy(value: object) -> bool:
    return clean(value).lower() in {"1", "true", "yes", "y"}


def parse_int(value: object) -> int:
    text = clean(value).replace(",", "")
    if not text:
        return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def read_csv(path: Path, required: bool = True) -> list[dict]:
    if not path.exists():
        if required:
            raise FileNotFoundError(path)
        return []
    with path.open(newline="", encoding="utf-8-sig") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def normalize_domain(value: str) -> str:
    value = clean(value)
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


def registered_domain(value: str) -> str:
    domain = normalize_domain(value)
    parts = [part for part in domain.split(".") if part]
    if len(parts) <= 2:
        return domain
    if len(parts[-2]) <= 3 and parts[-1] in {"uk", "au", "ca", "nz"}:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def is_customer(row: dict) -> bool:
    return (
        clean(row.get("Type")).lower() == "customer"
        or truthy(row.get("Active_Customer__c"))
        or "active customer" in clean(row.get("Account_Status__c")).lower()
    )


def is_positive_icp_type(company_type: str) -> bool:
    return clean(company_type).lower() in ICP_COMPANY_TYPES


def is_non_icp_company_type(company_type: str) -> bool:
    low = clean(company_type).lower()
    return bool(low) and any(token in low for token in NON_ICP_COMPANY_TYPE_TOKENS)


def is_hard_status(status: str) -> bool:
    low = clean(status).lower()
    return any(token in low for token in HARD_STATUS_TOKENS)


def normalized_website(value: str) -> str:
    text = clean(value)
    if not text:
        return ""
    return text if text.startswith(("http://", "https://")) else text.strip("/ ")


def load_alta(matches_path: Path, flags_path: Path) -> dict[str, dict]:
    by_id: dict[str, dict] = {}
    for row in read_csv(matches_path, required=False):
        account_id = clean(row.get("Account_Id"))
        if not account_id:
            continue
        by_id[account_id] = {
            "AltaMember": "true",
            "AltaId": clean(row.get("ALTA_ID")),
            "AltaMatchConfidence": clean(row.get("Match_Confidence")),
            "AltaName": clean(row.get("ALTA_Company_Name")),
            "AltaState": clean(row.get("ALTA_State")),
        }
    for row in read_csv(flags_path, required=False):
        account_id = clean(row.get("Account_Id"))
        if not account_id or account_id in by_id:
            continue
        by_id[account_id] = {
            "AltaMember": clean(row.get("ALTA_Member")).lower(),
            "AltaId": clean(row.get("ALTA_ID")),
            "AltaMatchConfidence": clean(row.get("Match_Confidence")),
            "AltaName": "",
            "AltaState": "",
        }
    return by_id


def build_domain_context(rows: list[dict]) -> dict[str, dict]:
    by_domain: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        domain = registered_domain(row.get("Website", ""))
        if domain:
            by_domain[domain].append(row)

    context: dict[str, dict] = {}
    for domain, domain_rows in by_domain.items():
        customers = [r for r in domain_rows if is_customer(r)]
        top_level = [r for r in domain_rows if not clean(r.get("ParentId"))]
        suggested = (customers or top_level or domain_rows)[0] if domain_rows else {}
        context[domain] = {
            "rows": domain_rows,
            "customers": customers,
            "suggested": suggested,
        }
    return context


def classify(row: dict, domain_context: dict[str, dict], alta_by_id: dict[str, dict]) -> dict:
    account_id = clean(row.get("Id"))
    name = clean(row.get("Name"))
    website = normalized_website(row.get("Website", ""))
    domain = registered_domain(website)
    status = clean(row.get("Account_Status__c"))
    company_type = clean(row.get("Company_Type__c"))
    hygiene = clean(row.get("Website_Hygiene_Status__c"))
    parent_id = clean(row.get("ParentId"))
    parent_name = clean(row.get("Parent.Name"))
    alta = alta_by_id.get(account_id, {})
    ctx = domain_context.get(domain, {"rows": [], "customers": [], "suggested": {}})
    customers = ctx.get("customers", [])
    suggested = ctx.get("suggested", {})

    flags: set[str] = set()
    evidence: list[str] = []
    disposition = "scoreable_icp"
    action = "allow_score"
    confidence = "Medium"
    reason = "Passed deterministic CRM/domain preflight gates."
    suggested_id = ""
    suggested_name = ""

    if alta and clean(alta.get("AltaMember")).lower() == "true":
        flags.add("alta_member_match")
        evidence.append(
            f"ALTA match {alta.get('AltaMatchConfidence', '')}: {alta.get('AltaName') or alta.get('AltaId')}"
        )
    if is_positive_icp_type(company_type):
        flags.add("company_type_positive_icp")
        evidence.append(f"CRM Company Type={company_type}")
    if is_non_icp_company_type(company_type):
        flags.add("company_type_non_icp")
        evidence.append(f"CRM Company Type={company_type}")
    if is_customer(row):
        flags.add("existing_customer")
        evidence.append("Account is a customer or active customer in CRM.")
    if parent_id:
        flags.add("has_parent")
        evidence.append(f"Parent account present: {parent_name or parent_id}")
        suggested_id = parent_id
        suggested_name = parent_name
    if customers and not is_customer(row):
        flags.add("domain_customer_match")
        evidence.append(
            "Website domain is also attached to customer account(s): "
            + ", ".join(clean(r.get("Name")) for r in customers[:3])
        )
        suggested_id = clean(customers[0].get("Id"))
        suggested_name = clean(customers[0].get("Name"))
    if domain and len(ctx.get("rows", [])) > 1:
        flags.add("shared_domain_cluster")
        evidence.append(f"Domain cluster size in CRM export: {len(ctx.get('rows', []))}")
    if domain in UNDERWRITER_DOMAINS:
        flags.add("underwriter_domain")
        evidence.append(f"{domain}: {UNDERWRITER_DOMAINS[domain]}")
    if domain in OWNED_DIRECT_REVIEW_DOMAINS:
        flags.add("owned_direct_domain")
        evidence.append(f"{domain}: {OWNED_DIRECT_REVIEW_DOMAINS[domain]}")
    if domain in ROLLUP_REVIEW_DOMAINS:
        flags.add("known_rollup_domain")
        evidence.append(f"{domain}: {ROLLUP_REVIEW_DOMAINS[domain]}")
    if domain in ASSOCIATION_DIRECTORY_DOMAINS:
        flags.add("association_directory_domain")
        evidence.append(f"{domain}: {ASSOCIATION_DIRECTORY_DOMAINS[domain]}")
    if BANK_DOMAIN_RE.search(domain):
        flags.add("bank_domain")
        evidence.append(f"Bank/lender domain pattern: {domain}")
    if GOV_DOMAIN_RE.search(domain):
        flags.add("gov_county_domain")
        evidence.append(f"Government/county domain pattern: {domain}")
    if BROKERAGE_DOMAIN_RE.search(domain):
        flags.add("real_estate_brokerage_domain")
        evidence.append(f"Brokerage/real-estate domain pattern: {domain}")
    if hygiene and hygiene.lower() not in {"confirmed", "ok"}:
        flags.add("website_hygiene_not_confirmed")
        evidence.append(f"Website hygiene status={hygiene}")

    # First match wins. This ordering is intentionally conservative.
    if not website:
        disposition = "missing_website"
        action = "website_review"
        confidence = "High"
        reason = "No Account.Website available; cannot score website-derived value."
    elif is_customer(row):
        disposition = "existing_customer_review"
        action = "route_ops_review"
        confidence = "High"
        reason = "Existing/customer Account should not be included in net-new prospect scoring."
    elif is_hard_status(status):
        disposition = "crm_status_suppressed"
        action = "suppress_score"
        confidence = "High"
        reason = "CRM Account Status indicates the row is not currently scoreable."
    elif parent_id:
        disposition = "parent_child_rollup_review"
        action = "roll_to_parent_review"
        confidence = "High"
        reason = "Account has a parent; score should be reviewed at sellable-account grain."
    elif domain in OWNED_DIRECT_REVIEW_DOMAINS or domain in ROLLUP_REVIEW_DOMAINS:
        disposition = "owned_or_rollup_domain_review"
        action = "roll_to_parent_review"
        confidence = "High"
        reason = "Known owned/direct or rollup domain should not score as standalone child account."
        suggested_id = suggested_id or clean(suggested.get("Id"))
        suggested_name = suggested_name or clean(suggested.get("Name"))
    elif domain in UNDERWRITER_DOMAINS:
        disposition = "underwriter_or_direct_side_review"
        action = "route_ops_review"
        confidence = "High"
        reason = "Website domain belongs to title underwriter/direct-side ecosystem."
    elif domain in ASSOCIATION_DIRECTORY_DOMAINS:
        disposition = "website_mismatch_review"
        action = "website_review"
        confidence = "High"
        reason = "Website is an association/directory domain, not the sellable account site."
    elif "bank_domain" in flags or "gov_county_domain" in flags or "real_estate_brokerage_domain" in flags:
        disposition = "website_or_entity_review"
        action = "website_review" if is_positive_icp_type(company_type) or alta else "suppress_score"
        confidence = "High" if action == "suppress_score" else "Medium"
        reason = "Website/domain reads as bank, government, or brokerage rather than title/settlement ICP."
    elif "domain_customer_match" in flags:
        disposition = "duplicate_or_existing_customer_review"
        action = "route_ops_review"
        confidence = "High"
        reason = "Domain is already attached to customer account(s); avoid duplicate prospect TAM/scoring."
    elif hygiene and hygiene.lower() in BAD_HYGIENE_STATUSES:
        disposition = "website_hygiene_review"
        action = "website_review"
        confidence = "High"
        reason = "Existing website hygiene status is not trusted for automated score."
    elif is_non_icp_company_type(company_type):
        if alta:
            disposition = "crm_company_type_review"
            action = "route_ops_review"
            confidence = "Medium"
            reason = "CRM Company Type is non-ICP/adjacent; ALTA corroboration routes it to review, not auto-score."
        else:
            disposition = "crm_company_type_suppressed"
            action = "suppress_score"
            confidence = "High"
            reason = "CRM Company Type is non-ICP and no ALTA corroboration exists."
    elif not is_positive_icp_type(company_type) and not alta:
        disposition = "manual_ops_review"
        action = "route_ops_review"
        confidence = "Medium"
        reason = "CRM Company Type is not a clean title/law/escrow ICP; route before spending scoring calls."
    elif alta:
        confidence = "High"
        reason = "ALTA membership match plus no deterministic suppression signal."

    if not suggested_id and suggested:
        suggested_id = clean(suggested.get("Id"))
        suggested_name = clean(suggested.get("Name"))

    return {
        "QualityDisposition": disposition,
        "QualityAction": action,
        "QualityConfidence": confidence,
        "QualityReason": reason,
        "QualityEvidence": " | ".join(dict.fromkeys(evidence))[:3000],
        "SourceFlags": ";".join(sorted(flags)),
        "SuggestedSellableAccountId": suggested_id,
        "SuggestedSellableAccountName": suggested_name,
        "ScoreEligible": "true" if action == "allow_score" else "false",
    }


def scoring_input_row(account: dict, overlay: dict) -> dict:
    account_id = clean(account.get("Id"))
    website = normalized_website(account.get("Website", ""))
    acct_type = clean(account.get("Type")).lower()
    active = truthy(account.get("Active_Customer__c"))
    status = clean(account.get("Account_Status__c")).lower()
    has_any = "customer" if acct_type == "customer" or active or "active customer" in status else ""
    return {
        "SourceSet": "sfdc_crm_full_prod_2026-07-08",
        "TestLane": "crm_full_no_write_quality_gated",
        "Bucket": f"qa_{overlay['QualityDisposition']}",
        "Id": account_id,
        "SalesforceUrl": f"https://certifid2022.my.salesforce.com/lightning/r/Account/{account_id}/view",
        "Name": clean(account.get("Name")),
        "Website": website,
        "BillingState": clean(account.get("BillingState")),
        "Segment": clean(account.get("Account_Segment__c")),
        "Owner": clean(account.get("Owner.Name")),
        "FinalMCV": str(parse_int(account.get("Final_Monthly_Closing_Volume__c"))),
        "MCVSource": clean(account.get("Monthly_Closing_Volume_Source__c")) or "N/A",
        "HasAnyOpp": has_any,
        "LegacyTier": "",
        "WebsiteHygiene": clean(account.get("Website_Hygiene_Status__c")) or "confirmed",
    }


def build(args: argparse.Namespace) -> tuple[list[dict], list[dict], dict]:
    accounts = read_csv(args.account_export)
    alta_by_id = load_alta(args.alta_matches, args.alta_flags)
    domain_context = build_domain_context(accounts)

    overlay_rows: list[dict] = []
    scoreable_rows: list[dict] = []
    for account in accounts:
        account_id = clean(account.get("Id"))
        website = normalized_website(account.get("Website", ""))
        domain = registered_domain(website)
        ctx = domain_context.get(domain, {"rows": [], "customers": []})
        alta = alta_by_id.get(account_id, {})
        decision = classify(account, domain_context, alta_by_id)
        overlay = {
            "AccountId": account_id,
            "AccountName": clean(account.get("Name")),
            "Website": website,
            "WebsiteDomain": domain,
            "BillingState": clean(account.get("BillingState")),
            "CrmType": clean(account.get("Type")),
            "CrmAccountStatus": clean(account.get("Account_Status__c")),
            "CrmCompanyType": clean(account.get("Company_Type__c")),
            "ActiveCustomer": clean(account.get("Active_Customer__c")),
            "ParentId": clean(account.get("ParentId")),
            "ParentName": clean(account.get("Parent.Name")),
            "FinalMCV": str(parse_int(account.get("Final_Monthly_Closing_Volume__c"))),
            "MCVSource": clean(account.get("Monthly_Closing_Volume_Source__c")) or "N/A",
            "WebsiteHygiene": clean(account.get("Website_Hygiene_Status__c")),
            "AltaMember": clean(alta.get("AltaMember")),
            "AltaId": clean(alta.get("AltaId")),
            "AltaMatchConfidence": clean(alta.get("AltaMatchConfidence")),
            "AltaName": clean(alta.get("AltaName")),
            "AltaState": clean(alta.get("AltaState")),
            "DomainClusterSize": str(len(ctx.get("rows", []))) if domain else "0",
            "DomainCustomerAccountIds": ";".join(clean(r.get("Id")) for r in ctx.get("customers", [])),
            "DomainCustomerAccountNames": ";".join(clean(r.get("Name")) for r in ctx.get("customers", [])),
            **decision,
        }
        overlay_rows.append(overlay)
        if overlay["QualityAction"] == "allow_score":
            scoreable_rows.append(scoring_input_row(account, overlay))

    summary = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "inputs": {
            "account_export": str(args.account_export),
            "alta_matches": str(args.alta_matches),
            "alta_flags": str(args.alta_flags),
        },
        "counts": {
            "accounts": len(accounts),
            "scoreable_rows": len(scoreable_rows),
            "by_action": dict(Counter(r["QualityAction"] for r in overlay_rows)),
            "by_disposition": dict(Counter(r["QualityDisposition"] for r in overlay_rows)),
            "by_company_type": dict(Counter(r["CrmCompanyType"] or "<blank>" for r in overlay_rows).most_common(25)),
            "by_hygiene": dict(Counter(r["WebsiteHygiene"] or "<blank>" for r in overlay_rows).most_common(25)),
        },
        "validation": {
            "all_accounts_classified": len(overlay_rows) == len(accounts),
            "scoreable_subset_matches_allow_score": len(scoreable_rows)
            == sum(1 for r in overlay_rows if r["QualityAction"] == "allow_score"),
            "no_scoreable_non_allow_actions": all(r["QualityAction"] == "allow_score" for r in overlay_rows if r["ScoreEligible"] == "true"),
        },
    }
    return overlay_rows, scoreable_rows, summary


def write_readout(path: Path, summary: dict, overlay_rows: list[dict]) -> None:
    lines = [
        "# ICP Quality Agent CRM Full Preflight",
        "",
        f"Generated: {summary['generated_at_utc']}",
        "",
        "No Salesforce writes were performed. This is a pre-scoring quality gate.",
        "",
        "## Counts",
        "",
        f"- Accounts classified: {summary['counts']['accounts']}",
        f"- Scoreable / allowed for Nimble scoring: {summary['counts']['scoreable_rows']}",
        "",
        "## Actions",
        "",
        "| Action | Rows |",
        "|---|---:|",
    ]
    for key, value in sorted(summary["counts"]["by_action"].items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| {key} | {value} |")

    lines.extend(["", "## Top Dispositions", "", "| Disposition | Rows |", "|---|---:|"])
    for key, value in sorted(summary["counts"]["by_disposition"].items(), key=lambda item: (-item[1], item[0]))[:25]:
        lines.append(f"| {key} | {value} |")

    review_examples = [r for r in overlay_rows if r["QualityAction"] != "allow_score"][:20]
    lines.extend(["", "## Review Examples", "", "| Account | Domain | Action | Disposition | Reason |", "|---|---|---|---|---|"])
    for row in review_examples:
        reason = row["QualityReason"].replace("|", "/")
        lines.append(
            f"| {row['AccountName']} | {row['WebsiteDomain']} | {row['QualityAction']} | {row['QualityDisposition']} | {reason} |"
        )

    lines.extend(["", "## Validation", "", "| Check | Pass |", "|---|---:|"])
    for key, value in summary["validation"].items():
        lines.append(f"| {key} | {value} |")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-export", type=Path, default=DEFAULT_ACCOUNT_EXPORT)
    parser.add_argument("--alta-matches", type=Path, default=DEFAULT_ALTA_MATCHES)
    parser.add_argument("--alta-flags", type=Path, default=DEFAULT_ALTA_FLAGS)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    overlay_rows, scoreable_rows, summary = build(args)
    review_rows = [r for r in overlay_rows if r["QualityAction"] != "allow_score"]

    write_csv(args.out_dir / OVERLAY_NAME, overlay_rows, OVERLAY_COLUMNS)
    write_csv(args.out_dir / REVIEW_NAME, review_rows, OVERLAY_COLUMNS)
    write_csv(args.out_dir / SCOREABLE_INPUT_NAME, scoreable_rows, SCORING_INPUT_COLUMNS)
    (args.out_dir / SUMMARY_NAME).write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    write_readout(args.out_dir / READOUT_NAME, summary, overlay_rows)

    print(json.dumps(summary["counts"], indent=2, sort_keys=True))
    print(json.dumps(summary["validation"], indent=2, sort_keys=True))
    print(f"overlay={args.out_dir / OVERLAY_NAME}")
    print(f"review_queue={args.out_dir / REVIEW_NAME}")
    print(f"scoreable_input={args.out_dir / SCOREABLE_INPUT_NAME}")
    return 0 if all(summary["validation"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
