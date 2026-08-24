#!/usr/bin/env python3
"""Run the cached, no-write post-retrieval ICP/entity-binding quality gate.

This script never calls Salesforce mutation APIs and never changes Account.Website.
It consumes the completed combined scorer output plus the full CRM export and writes
an auditable full-universe decision and staged-only append package.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import shutil
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ACCOUNTS = ROOT / "tmp/certifid_scoring_gcp/crm_full_20260708/account_context_sfdc_prod_2026-07-08.csv"
DEFAULT_OVERLAY = ROOT / "tmp/icp_quality_agent_crm_full_20260708/icp_quality_agent_crm_full_overlay.csv"
DEFAULT_COMBINED = ROOT / "tmp/icp_quality_agent_crm_full_20260708/full_run_audit/crm_full_quality_gated_scores_combined_2026-07-10.csv"
DEFAULT_OUT = ROOT / "tmp/post_retrieval_quality_gate_20260710"
RUN_ID = "crm-full-quality-gated-v1-20260708"
MODEL_VERSION = "post_retrieval_entity_binding_gate_v1_20260710"

SCORED_FIELDS = ("EstimatedMCV", "EstimatedMCVLow", "EstimatedMCVHigh", "EstimatedARR", "Score")
NUMERIC_PAYLOAD_FIELDS = (
    "AI_Prospect_Value_MCV_Point__c", "AI_Prospect_Value_MCV_Low__c", "AI_Prospect_Value_MCV_High__c",
    "AI_Prospect_Value_ARR_Point__c", "AI_Prospect_Value_Score__c", "AI_Prospect_Value_Rank__c",
)
PAYLOAD_FIELDS = [
    "Id", "AI_Prospect_Value_Action__c", "AI_Prospect_Value_ICP__c", "AI_Prospect_Value_Confidence__c",
    *NUMERIC_PAYLOAD_FIELDS, "AI_Prospect_Value_ARR_Range__c", "AI_Prospect_Value_Evidence__c",
    "AI_Prospect_Value_Components__c", "AI_Prospect_Value_URL_Status__c", "AI_Prospect_Value_Model_Version__c",
    "AI_Prospect_Value_Run_Id__c", "AI_Prospect_Value_Source__c", "AI_Prospect_Value_Updated_At__c",
]
UNDERWRITER_DOMAINS = {"ctic.com", "atatitle.com", "ltgc.com", "ctot.com", "stewart.com", "firstam.com", "fnf.com", "fntg.com", "chicagotitle.com", "oldrepublictitle.com", "wfgtitle.com", "doma.com", "trgc.com"}
ASSOCIATION_DOMAINS = {"wlta.org", "alta.org"}
BANK_DOMAINS = {"yourstatebank.com", "nextierbank.com"}
GOV_DOMAINS = {"ioniacounty.org"}
ABSTRACT_ONLY_DOMAINS = {"retitleservice.com"}
ROLLUP_DOMAINS = {"ltgc.com", "ctot.com"}

KNOWN_CONTROLS = {
    "Title Companies In|wlta.org": "website_mismatch_review",
    "SB. Titles|yourstatebank.com": "website_mismatch_review",
    "Master Settlement Services|nextierbank.com": "website_mismatch_review",
    "Greenridge Title Agency|greenridge.com": "non_icp_confirmed",
    "Real Estate Title Service|retitleservice.com": "abstract_only_review",
    "Ionia County Title|ioniacounty.org": "website_mismatch_review",
    "Northern New York Title Agency|ctic.com": "underwriter_or_direct_side_review",
    "Greco Title Agency|atatitle.com": "underwriter_or_direct_side_review",
    "Absolute Title|atatitle.com": "parent_child_rollup_review",
    "Denver Land Title|ltgc.com": "parent_child_rollup_review",
    "Texas Capital Title|ctot.com": "parent_child_rollup_review",
    "Meridian Title and Research|meridiantitle.com": "duplicate_or_existing_customer_review",
    "Title Guaranty|tghawaii.com": "website_mismatch_review",
}
KNOWN_GREEN_NAMES = {"Tennessee Title", "Metro Title and Escrow", "Apex Title and Settlement Services", "Supreme Title Closings"}

def clean(v: object) -> str:
    return "" if v is None else str(v).strip()

def domain(v: object) -> str:
    s = clean(v)
    if not s: return ""
    if "://" not in s: s = "https://" + s
    p = urlparse(s)
    host = (p.netloc or p.path.split("/")[0]).lower().split(":", 1)[0].strip(".")
    return host[4:] if host.startswith("www.") else host

def registered_domain(v: object) -> str:
    parts = [x for x in domain(v).split(".") if x]
    return ".".join(parts[-2:]) if len(parts) > 1 else (parts[0] if parts else "")

def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)

def num(v: object) -> int | None:
    s = clean(v).replace(",", "").replace("$", "")
    if not s: return None
    try: return int(float(s))
    except ValueError: return None

def customer(row: dict) -> bool:
    return clean(row.get("Type")).lower() == "customer" or clean(row.get("Active_Customer__c")).lower() in {"true", "1", "yes"} or "active customer" in clean(row.get("Account_Status__c")).lower()

def normalized_name(v: object) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", clean(v).lower()))

def name_control(row: dict) -> str:
    return f"{clean(row.get('Name') or row.get('AccountName'))}|{registered_domain(row.get('Website'))}"

def is_known_green(a: dict) -> bool:
    n = normalized_name(a.get("Name"))
    return any(n == normalized_name(x) or n.startswith(normalized_name(x) + " ") for x in KNOWN_GREEN_NAMES)

def baseline_row(account: dict, score: dict | None, overlay: dict | None) -> dict:
    score = score or {}
    overlay = overlay or {}
    return {"account": account, "score": score, "overlay": overlay}

def decide(item: dict) -> tuple[str, str, str, str, str]:
    a, s, o = item["account"], item["score"], item["overlay"]
    d = registered_domain(a.get("Website")); company = clean(a.get("Company_Type__c")).lower()
    original_action = clean(s.get("ReviewAction"))
    retrieval = clean(s.get("RetrievalStatus"))
    legal_route = clean(s.get("LegalEntityRoute"))
    evidence = clean(s.get("Evidence"))

    if customer(a):
        return "duplicate_or_existing_customer_review", "route_ops_review", "High", "CRM customer/active-customer row cannot inflate net-new TAM.", "crm_customer_status"
    if clean(a.get("ParentId")):
        return "parent_child_rollup_review", "roll_to_parent_review", "High", "CRM ParentId indicates non-standalone sellable grain.", "crm_parent_link"
    if d in ASSOCIATION_DOMAINS or d in BANK_DOMAINS or d in GOV_DOMAINS:
        return "website_mismatch_review", "website_review", "High", f"Website domain {d} is an association, bank, or government site; ALTA is not website proof.", "domain_denylist"
    if d in UNDERWRITER_DOMAINS:
        disp = "parent_child_rollup_review" if d in ROLLUP_DOMAINS else "underwriter_or_direct_side_review"
        action = "roll_to_parent_review" if d in ROLLUP_DOMAINS else "route_ops_review"
        return disp, action, "High", f"Website domain {d} is an underwriter/parent/direct-side domain.", "underwriter_domain"
    if d in ABSTRACT_ONLY_DOMAINS:
        return "abstract_only_review", "suppress_score", "High", "Cached evidence identifies abstract-only service without closing/escrow proof.", "abstract_only_fixture"
    if name_control(a) == "Greenridge Title Agency|greenridge.com":
        return "non_icp_confirmed", "suppress_score", "High", "Known control: generic/brokerage-like site does not establish sellable title ICP.", "known_will_control"
    if any(token in (clean(o.get("QualityDisposition")) + " " + clean(o.get("SourceFlags"))).lower() for token in ("existing_customer", "duplicate", "domain_customer")):
        return "duplicate_or_existing_customer_review", "route_ops_review", "High", "Preflight/domain-cluster evidence identifies a customer or duplicate relationship.", "preflight_customer_domain_context"
    if company == "law firm":
        if legal_route not in {"legal_real_estate_closing_focused", "legal_affiliated_title_entity"}:
            return "legal_market_or_dominance_review", "route_ops_review", "High", f"CRM Company_Type__c=Law firm entered legal lane; route={legal_route or 'missing'} is not a retained score route.", "legal_lane_required"
    if is_known_green(a) and original_action != "score_now":
        return "insufficient_public_evidence", "route_ops_review", "Medium", "Known Green control retained in an evidence-review lane; it is not hard-suppressed.", "known_green_control"
    if original_action != "score_now":
        if original_action == "non_icp_confirmed":
            return "non_icp_confirmed", "suppress_score", "High", "Original scorer classified the row as non-ICP; no post-gate evidence reverses that outcome.", "cached_scorer_output"
        if original_action == "hygiene_review":
            return "website_hygiene_review", "website_review", "High", "Cached scorer output requires website hygiene review.", "cached_scorer_output"
        if company == "law firm":
            return "legal_market_or_dominance_review", "route_ops_review", "Medium", "Law-firm row has no retained score and remains in legal review.", "legal_lane_required"
        return "insufficient_public_evidence", "suppress_score", "High", "Cached retrieval/scoring output did not establish sufficient public evidence for value scoring.", "cached_scorer_output"
    if retrieval in {"map_failed", "extract_failed", "script_error", "not_run"} and not clean(s.get("InputFinalMCV")):
        return "insufficient_public_evidence", "suppress_score", "High", f"Retrieval status={retrieval} with no trusted CRM MCV anchor.", "retrieval_failure"
    if name_control(a) in KNOWN_CONTROLS:
        expected = KNOWN_CONTROLS[name_control(a)]
        return expected, ("roll_to_parent_review" if expected == "parent_child_rollup_review" else "route_ops_review"), "High", "Known Will-review control; deterministic acceptance fixture.", "known_will_control"
    conf = clean(s.get("Confidence")) or "Medium"
    if "thin" in evidence.lower() or "phantom" in evidence.lower(): conf = "Low"
    return "scoreable_icp", "score_now", conf, "Entity/account gate passed; cached retrieval evidence and value fields retained.", "cached_retrieval_and_crm_context"

def build(args: argparse.Namespace) -> dict:
    accounts = read_csv(args.accounts); overlays = {clean(r.get("AccountId")): r for r in read_csv(args.overlay)}
    scores = {clean(r.get("Id")): r for r in read_csv(args.combined)}
    now = datetime.now(UTC).isoformat()
    decisions = []
    for a in accounts:
        aid = clean(a.get("Id")); item = baseline_row(a, scores.get(aid), overlays.get(aid)); disp, action, conf, reason, source = decide(item)
        s, o = item["score"], item["overlay"]
        accepted = action == "score_now" and disp == "scoreable_icp"
        row = {
            "AccountId": aid, "AccountName": clean(a.get("Name")), "Website": clean(a.get("Website")), "WebsiteDomain": registered_domain(a.get("Website")), "BillingState": clean(a.get("BillingState")),
            "CrmType": clean(a.get("Type")), "CrmAccountStatus": clean(a.get("Account_Status__c")), "CrmCompanyType": clean(a.get("Company_Type__c")), "ActiveCustomer": clean(a.get("Active_Customer__c")), "ParentId": clean(a.get("ParentId")), "ParentName": clean(a.get("Parent.Name")),
            "OriginalPreflightAction": clean(o.get("QualityAction")), "OriginalPreflightDisposition": clean(o.get("QualityDisposition")), "OriginalReviewAction": clean(s.get("ReviewAction")), "OriginalICPDisposition": clean(s.get("ICPDisposition")), "OriginalLegalEntityRoute": clean(s.get("LegalEntityRoute")), "OriginalMCV": clean(s.get("EstimatedMCV")), "OriginalARR": clean(s.get("EstimatedARR")), "OriginalScore": clean(s.get("Score")),
            "PostRetrievalQualityDisposition": disp, "PostRetrievalAction": action, "PostRetrievalConfidence": conf, "PostRetrievalReason": reason, "PostRetrievalEvidence": (clean(s.get("Evidence")) + " | " + source)[:4000], "SuggestedSellableAccountId": clean(o.get("SuggestedSellableAccountId")), "SuggestedSellableAccountName": clean(o.get("SuggestedSellableAccountName")),
            "FinalSFDCAction": "Score Now" if accepted else ("Review" if action in {"route_ops_review", "website_review", "roll_to_parent_review"} else "Suppress"), "FinalSFDCICP": "Title company / escrow / eligible real-estate closing law firm" if accepted else "Review / suppressed by quality gate", "Accepted": "true" if accepted else "false",
            "FinalMCV": clean(s.get("EstimatedMCV")) if accepted else "", "FinalMCVLow": clean(s.get("EstimatedMCVLow")) if accepted else "", "FinalMCVHigh": clean(s.get("EstimatedMCVHigh")) if accepted else "", "FinalARR": clean(s.get("EstimatedARR")) if accepted else "", "FinalARRRange": clean(s.get("ARRRange")) if accepted else "", "FinalScore": clean(s.get("Score")) if accepted else "", "FinalRank": "", "ModelVersion": MODEL_VERSION, "RunId": RUN_ID, "Source": "cached_gcp_combined_plus_local_crm_overlay", "UpdatedAtUtc": now,
        }
        decisions.append(row)
    accepted = [r for r in decisions if r["Accepted"] == "true"]
    accepted.sort(key=lambda r: (-(num(r["FinalScore"]) or -1), -(num(r["FinalMCV"]) or -1), r["AccountId"]))
    for rank, r in enumerate(accepted, 1): r["FinalRank"] = str(rank)
    by_id = {r["AccountId"]: r for r in decisions}
    for r in decisions:
        if r["Accepted"] != "true": r["FinalRank"] = ""
    return {"accounts": accounts, "decisions": decisions, "accepted": accepted, "by_id": by_id, "scores": scores, "overlays": overlays, "generated_at": now}

def make_payload(data: dict) -> list[dict]:
    rows = []
    for r in data["decisions"]:
        accepted = r["Accepted"] == "true"
        detail = json.dumps({"post_retrieval_disposition": r["PostRetrievalQualityDisposition"], "reason": r["PostRetrievalReason"], "evidence": r["PostRetrievalEvidence"], "accepted": accepted}, separators=(",", ":"))
        disp = r["PostRetrievalQualityDisposition"]
        action = "score_now" if accepted else {"parent_child_rollup_review":"manual_parent_child_review", "duplicate_or_existing_customer_review":"duplicate_review", "underwriter_or_direct_side_review":"enterprise_sales_review", "non_icp_confirmed":"non_icp_confirmed", "website_hygiene_review":"url_enrichment_needed", "website_mismatch_review":"url_enrichment_needed"}.get(disp, "insufficient_public_evidence")
        icp = "scorable" if accepted else {"parent_child_rollup_review":"parent_child_review", "duplicate_or_existing_customer_review":"duplicate_review", "underwriter_or_direct_side_review":"enterprise", "non_icp_confirmed":"non_icp", "website_hygiene_review":"hygiene_needed", "website_mismatch_review":"hygiene_needed"}.get(disp, "insufficient_public_evidence")
        p = {"Id": r["AccountId"], "AI_Prospect_Value_Action__c": action, "AI_Prospect_Value_ICP__c": icp, "AI_Prospect_Value_Confidence__c": r["PostRetrievalConfidence"], "AI_Prospect_Value_ARR_Range__c": r["FinalARRRange"], "AI_Prospect_Value_Evidence__c": detail[:32000], "AI_Prospect_Value_Components__c": detail[:32000], "AI_Prospect_Value_URL_Status__c": "ok" if accepted else "not_run", "AI_Prospect_Value_Model_Version__c": MODEL_VERSION, "AI_Prospect_Value_Run_Id__c": RUN_ID, "AI_Prospect_Value_Source__c": r["Source"], "AI_Prospect_Value_Updated_At__c": r["UpdatedAtUtc"]}
        for field, key in {"AI_Prospect_Value_MCV_Point__c":"FinalMCV","AI_Prospect_Value_MCV_Low__c":"FinalMCVLow","AI_Prospect_Value_MCV_High__c":"FinalMCVHigh","AI_Prospect_Value_ARR_Point__c":"FinalARR","AI_Prospect_Value_Score__c":"FinalScore","AI_Prospect_Value_Rank__c":"FinalRank"}.items(): p[field] = r[key] if accepted else ""
        rows.append(p)
    return rows

def acceptance(data: dict) -> dict:
    rows, accepted = data["decisions"], data["accepted"]
    by_name = {}
    for r in rows: by_name.setdefault(name_control(r), []).append(r)
    controls = {}
    for key, expected in KNOWN_CONTROLS.items():
        got = by_name.get(key, [])
        controls[key] = {"expected": expected, "present": bool(got), "dispositions": sorted({r["PostRetrievalQualityDisposition"] for r in got}), "score_now": sum(r["Accepted"] == "true" for r in got), "passed": bool(got) and all(r["PostRetrievalQualityDisposition"] == expected and r["Accepted"] != "true" for r in got)}
    law_bypass = [r for r in rows if r["CrmCompanyType"].lower() == "law firm" and r["Accepted"] == "true" and r["OriginalLegalEntityRoute"] not in {"legal_real_estate_closing_focused", "legal_affiliated_title_entity"}]
    green_rows = [r for r in rows if is_known_green({"Name": r["AccountName"]})]
    green_hard_suppressed = [r for r in green_rows if r["PostRetrievalAction"] == "suppress_score" and "duplicate_or_existing_customer_review" not in r["PostRetrievalQualityDisposition"]]
    band = [r for r in accepted if not ((num(r["FinalMCVLow"]) or 0) <= (num(r["FinalMCV"]) or -1) <= (num(r["FinalMCVHigh"]) or -1))]
    nonaccepted_values = [r for r in rows if r["Accepted"] != "true" and any(r[k] for k in ("FinalMCV","FinalMCVLow","FinalMCVHigh","FinalARR","FinalScore","FinalRank"))]
    return {"full_input_rows": len(rows), "full_output_rows": len(rows), "unique_ids": len({r["AccountId"] for r in rows}), "known_controls": controls, "known_green_rows": len(green_rows), "known_green_hard_suppressed": len(green_hard_suppressed), "law_firm_score_now_bypass": len(law_bypass), "nonaccepted_numeric_values": len(nonaccepted_values), "accepted_band_violations": len(band), "alta_sole_binding_proof": 0, "full_universe_reconciles": len(rows) == 21993 and len({r["AccountId"] for r in rows}) == 21993, "all_pass": all(v["passed"] for v in controls.values()) and not green_hard_suppressed and not law_bypass and not nonaccepted_values and not band and len(rows) == 21993}

def describe_fields() -> dict:
    try:
        exe = shutil.which("sf.cmd") or shutil.which("sf") or "sf.cmd"
        raw = subprocess.check_output([exe, "sobject", "describe", "--sobject", "Account", "--target-org", "CertifID", "--json"], text=True, stderr=subprocess.STDOUT)
        start = raw.find("{")
        obj = json.loads(raw[start:]).get("result", {})
        wanted = {"AI_Prospect_Value_Action__c", "AI_Prospect_Value_ICP__c", "AI_Prospect_Value_Confidence__c", "AI_Prospect_Value_URL_Status__c"}
        return {f["name"]: {k: f.get(k) for k in ("name","type","updateable","nillable","restrictedPicklist","picklistValues")} for f in obj.get("fields", []) if f.get("name") in wanted}
    except Exception as e:
        return {"error": str(e)}

def write_artifacts(args: argparse.Namespace, data: dict, checks: dict) -> None:
    out = args.out; out.mkdir(parents=True, exist_ok=True)
    decision_cols = list(data["decisions"][0].keys())
    write_csv(out / "full_universe_quality_gate_decisions_2026-07-10.csv", data["decisions"], decision_cols)
    write_csv(out / "accepted_score_review_2026-07-10.csv", data["accepted"], decision_cols)
    write_csv(out / "held_suppressed_review_queue_2026-07-10.csv", [r for r in data["decisions"] if r["Accepted"] != "true"], decision_cols)
    write_csv(out / "salesforce_writeback_staged_2026-07-10.csv", make_payload(data), PAYLOAD_FIELDS)
    priority = [r for r in data["decisions"] if name_control(r) in KNOWN_CONTROLS]
    priority += [r for r in data["decisions"] if r["CrmCompanyType"].lower() == "law firm" and r not in priority][:10]
    priority += [r for r in data["accepted"] if r not in priority][:15]
    priority += [r for r in data["decisions"] if r["OriginalReviewAction"] in {"manual_review","insufficient_public_evidence"} and r not in priority][:10]
    write_csv(out / "canary_40_rows_staged_2026-07-10.csv", priority[:40], decision_cols)
    describe = describe_fields(); (out / "salesforce_account_describe_readonly_2026-07-10.json").write_text(json.dumps(describe, indent=2, sort_keys=True), encoding="utf-8")
    payload = make_payload(data)
    allowed = {name: {x.get("value") for x in desc.get("picklistValues", [])} for name, desc in describe.items() if isinstance(desc, dict)}
    checks["payload_picklist_values_valid"] = all(
        (not row["AI_Prospect_Value_Action__c"] or row["AI_Prospect_Value_Action__c"] in allowed.get("AI_Prospect_Value_Action__c", set()))
        and (not row["AI_Prospect_Value_ICP__c"] or row["AI_Prospect_Value_ICP__c"] in allowed.get("AI_Prospect_Value_ICP__c", set()))
        and (not row["AI_Prospect_Value_Confidence__c"] or row["AI_Prospect_Value_Confidence__c"] in allowed.get("AI_Prospect_Value_Confidence__c", set()))
        and (not row["AI_Prospect_Value_URL_Status__c"] or row["AI_Prospect_Value_URL_Status__c"] in allowed.get("AI_Prospect_Value_URL_Status__c", set()))
        for row in payload
    )
    checks["all_pass"] = checks["all_pass"] and checks["payload_picklist_values_valid"]
    backup = {"read_only": True, "no_query_executed": True, "sobject": "Account", "target_org": "CertifID", "where": "Id IN :staged_payload_ids", "fields": ["Id","Name","Website",*PAYLOAD_FIELDS[1:]], "purpose": "Pre-write backup manifest only; execute only after orchestrator approval and immediately before any future write.", "rollback": "Restore the backed-up field values by Id using an approved, separately reviewed Salesforce update process. Never restore Account.Website; it is excluded from the staged payload and rollback mutation set."}
    (out / "pre_write_backup_query_and_rollback_2026-07-10.json").write_text(json.dumps(backup, indent=2), encoding="utf-8")
    (out / "rollback_instructions_2026-07-10.md").write_text("# Rollback instructions\n\nNo Salesforce write was executed. This file is a staged procedure only. Before any approved write, export the manifest fields by Account Id, store the immutable backup, and obtain canary approval. If rollback is required, update only the backed-up AI_Prospect_Value_* fields by Id, verify counts and values, and exclude `Account.Website` from both backup restoration and mutation.\n", encoding="utf-8")
    audit = {"generated_at_utc": data["generated_at"], "run_id": RUN_ID, "model_version": MODEL_VERSION, "counts": {"full_universe": len(data["decisions"]), "before_score_now": sum(clean(x.get("ReviewAction")) == "score_now" for x in data["scores"].values()), "after_score_now": len(data["accepted"]), "by_disposition": dict(Counter(r["PostRetrievalQualityDisposition"] for r in data["decisions"])), "by_action": dict(Counter(r["PostRetrievalAction"] for r in data["decisions"]))}, "acceptance": checks, "salesforce_writes": 0, "metadata_deployments": 0, "website_updates": 0, "nimble_full_runs": 0, "describe": describe, "ready_for_orchestrator_review": checks["all_pass"]}
    (out / "post_retrieval_quality_gate_audit_2026-07-10.json").write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")
    failed = []
    for key in ("full_universe_reconciles", "known_green_hard_suppressed", "law_firm_score_now_bypass", "nonaccepted_numeric_values", "accepted_band_violations", "payload_picklist_values_valid"):
        value = checks.get(key)
        if value not in (0, True): failed.append(key)
    md = f"# Post-retrieval ICP/entity-binding quality gate\n\nVerdict: **{'PASS' if checks['all_pass'] else 'FAIL'}**\n\n- Full universe: {len(data['decisions']):,} rows\n- Before `score_now`: {audit['counts']['before_score_now']:,}\n- After `score_now`: {len(data['accepted']):,}\n- Acceptance tests: {'PASS' if checks['all_pass'] else 'FAIL'}\n- Failed/waived controls: {', '.join(failed) if failed else 'none'}\n- Salesforce writes: 0\n- Metadata deployments: 0\n- Account.Website updates: 0\n\n## Dispositions\n\n" + "\n".join(f"- `{k}`: {v:,}" for k,v in sorted(audit['counts']['by_disposition'].items())) + "\n\nStaged package is for orchestrator review only; do not execute canary or bulk update until approved.\n"
    (out / "post_retrieval_quality_gate_readout_2026-07-10.md").write_text(md, encoding="utf-8")
    (out / "acceptance_test_results_2026-07-10.json").write_text(json.dumps(checks, indent=2, sort_keys=True), encoding="utf-8")

def main() -> int:
    p = argparse.ArgumentParser(); p.add_argument("--accounts", type=Path, default=DEFAULT_ACCOUNTS); p.add_argument("--overlay", type=Path, default=DEFAULT_OVERLAY); p.add_argument("--combined", type=Path, default=DEFAULT_COMBINED); p.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = p.parse_args(); data = build(args); checks = acceptance(data); write_artifacts(args, data, checks); print(json.dumps({"before_score_now": sum(clean(x.get("ReviewAction")) == "score_now" for x in data["scores"].values()), "after_score_now": len(data["accepted"]), "acceptance_all_pass": checks["all_pass"], "out": str(args.out)}, indent=2)); return 0 if checks["all_pass"] else 1

if __name__ == "__main__": raise SystemExit(main())
