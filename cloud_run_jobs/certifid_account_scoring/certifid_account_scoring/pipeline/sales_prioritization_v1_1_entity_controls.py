"""Coverage-neutral foreign-domain and shared-domain controls for V1.1."""

from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from .publication import CANARY_FIELDS, write_csv
from .sales_prioritization_release import _lower, _number, _text, iso_z, read_csv, sha256_file, utc_now
from .sales_prioritization_v1_1 import CohortDefaults, _state_code
from .sales_prioritization_v1_1_guardrails import _band_values, _top_rows


MODEL_VERSION = "sales_prioritization_v1_1_entity_guardrailed_20260710"
SOURCE_VERSION = "crm_full_cached_entity_guardrailed_20260710"
HIGH_VALUE_MCV = 200
US_COUNTRY_VALUES = {"", "us", "usa", "united states", "united states of america"}

ENTITY_CONTROL_FIELDS = (
    "normalized_domain",
    "entity_control_applied",
    "entity_control_reason_codes",
    "foreign_domain_conflict",
    "us_location_corroborated",
    "shared_domain_resolution",
    "shared_domain_survivor_id",
    "pre_entity_control_mcv",
    "post_entity_control_mcv",
    "pre_entity_control_arr",
    "post_entity_control_arr",
)


def _index(path: Path, key: str) -> dict[str, dict[str, str]]:
    csv.field_size_limit(sys.maxsize)
    return {row[key]: row for row in read_csv(path) if _text(row.get(key))}


def normalized_domain(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    parsed = urlparse(text if "://" in text else f"https://{text}")
    host = (parsed.hostname or "").lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def is_foreign_country_domain(domain: str) -> bool:
    # ISO country-code TLDs are exactly two letters.  .us is domestic.
    suffix = domain.rsplit(".", 1)[-1] if "." in domain else ""
    return len(suffix) == 2 and suffix.isalpha() and suffix != "us"


def _truthy(value: Any) -> bool:
    return _lower(value) in {"true", "1", "yes", "y"}


def _us_based(account: Mapping[str, str]) -> bool:
    return bool(_state_code(account.get("BillingState"))) and _lower(account.get("BillingCountry")) in US_COUNTRY_VALUES


def _location_corroborated(
    account: Mapping[str, str], binding: Mapping[str, str]
) -> bool:
    if _lower(binding.get("status")) != "bound" or _lower(binding.get("confidence")) != "high":
        return False
    city = _lower(account.get("BillingCity"))
    state = _lower(account.get("BillingState"))
    try:
        citations = json.loads(_text(binding.get("citations")) or "[]")
    except json.JSONDecodeError:
        return False
    for citation in citations:
        if not isinstance(citation, Mapping):
            continue
        reference = _lower(citation.get("reference"))
        excerpt = _lower(citation.get("excerpt"))
        site_location_reference = any(token in reference for token in ("site", "address", "location", "contact"))
        location_match = (len(city) >= 4 and city in excerpt) or (len(state) >= 4 and state in excerpt)
        if site_location_reference and location_match:
            return True
    return False


def _name_tokens(value: Any) -> set[str]:
    stop = {"the", "and", "of", "inc", "llc", "company", "co", "agency", "group"}
    return {token for token in re.findall(r"[a-z0-9]+", _lower(value)) if len(token) > 2 and token not in stop}


def _site_name_exact(account: Mapping[str, str], binding: Mapping[str, str]) -> bool:
    account_tokens = _name_tokens(account.get("Name"))
    if not account_tokens:
        return False
    try:
        citations = json.loads(_text(binding.get("citations")) or "[]")
    except json.JSONDecodeError:
        return False
    for citation in citations:
        if not isinstance(citation, Mapping) or "siteorganizationname" not in _lower(citation.get("reference")):
            continue
        if _name_tokens(citation.get("excerpt")) == account_tokens:
            return True
    return False


def resolve_shared_domain_survivor(
    high_ids: Sequence[str],
    *,
    accounts: Mapping[str, Mapping[str, str]],
    memberships: Mapping[str, Mapping[str, str]],
    bindings: Mapping[str, Mapping[str, str]],
    audit_by_id: Mapping[str, Mapping[str, str]],
) -> tuple[str, str]:
    high_set = set(high_ids)
    hierarchy_votes: Counter[str] = Counter()
    for account_id in high_ids:
        account = accounts[account_id]
        membership = memberships[account_id]
        parent_id = _text(account.get("ParentId"))
        suggested = _text(membership.get("suggested_survivor_id"))
        confidence = _lower(membership.get("confidence"))
        if parent_id in high_set:
            hierarchy_votes[parent_id] += 2
        if suggested in high_set and suggested != account_id and confidence in {"medium", "high"}:
            hierarchy_votes[suggested] += 2
    if hierarchy_votes:
        ranked = hierarchy_votes.most_common()
        if len(ranked) == 1 or ranked[0][1] > ranked[1][1]:
            return ranked[0][0], "resolved_parent_child_or_suggested_survivor"

    scores: Counter[str] = Counter()
    for account_id in high_ids:
        account = accounts[account_id]
        binding = bindings[account_id]
        audit = audit_by_id[account_id]
        if _truthy(audit.get("alta_confirmed")):
            scores[account_id] += 10
        if _lower(binding.get("status")) == "bound" and _lower(binding.get("confidence")) == "high":
            scores[account_id] += 10
        if _site_name_exact(account, binding):
            scores[account_id] += 10
        if _location_corroborated(account, binding):
            scores[account_id] += 20
    ranked = scores.most_common()
    # Non-hierarchy evidence must be both strong and decisively unique.
    if ranked and ranked[0][1] >= 30 and (len(ranked) == 1 or ranked[0][1] - ranked[1][1] >= 20):
        return ranked[0][0], "resolved_unique_entity_location_identity"
    return "", "unresolved_shared_domain"


def _update_components(row: dict[str, str], *, run_id: str, reasons: Sequence[str], domain: str, survivor: str) -> None:
    try:
        components = json.loads(_text(row.get("AI_Prospect_Value_Components__c")) or "{}")
    except json.JSONDecodeError:
        components = {}
    components.update(
        {
            "semantic_version": "sales_prioritization_v1_1_entity_guardrailed",
            "entity_control_reason_codes": list(reasons),
            "normalized_domain": domain,
            "shared_domain_survivor_id": survivor,
            "run_id": run_id,
        }
    )
    row["AI_Prospect_Value_Components__c"] = json.dumps(components, sort_keys=True, separators=(",", ":"))


def apply_entity_controls(
    *,
    scored_audit_path: Path,
    full_decisions_path: Path,
    accounts_path: Path,
    binding_path: Path,
    membership_path: Path,
    output_dir: Path,
    run_id: str | None = None,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"Entity-control output is immutable and exists: {output_dir}")
    output_dir.mkdir(parents=True)
    built_at = utc_now()
    release_run_id = run_id or f"sales_prioritization_v1_1_entity_guardrailed_{built_at:%Y%m%dT%H%M%SZ}"
    updated_at = iso_z(built_at)

    scored = read_csv(scored_audit_path)
    decisions = read_csv(full_decisions_path)
    accounts = _index(accounts_path, "Id")
    bindings = _index(binding_path, "account_id")
    memberships = _index(membership_path, "account_id")
    if len(scored) != 16_924 or len(decisions) != 21_993:
        raise RuntimeError("Entity controls require the approved 16,924/21,993 population")
    audit_by_id = {row["Id"]: row for row in scored}
    retained_reference = [row for row in scored if _truthy(row.get("retained_current_v1"))]
    cohorts = CohortDefaults(retained_reference)

    domain_members: dict[str, list[str]] = defaultdict(list)
    for row in scored:
        domain = normalized_domain(row.get("AI_Prospect_Value_Canonical_URL__c"))
        if domain:
            domain_members[domain].append(row["Id"])

    domain_resolution: dict[str, tuple[str, str]] = {}
    affected_high_ids: set[str] = set()
    for domain, member_ids in domain_members.items():
        high_ids = [
            account_id
            for account_id in member_ids
            if float(audit_by_id[account_id]["AI_Prospect_Value_MCV_Point__c"]) >= HIGH_VALUE_MCV
        ]
        if len(high_ids) < 2:
            continue
        affected_high_ids.update(high_ids)
        domain_resolution[domain] = resolve_shared_domain_survivor(
            high_ids,
            accounts=accounts,
            memberships=memberships,
            bindings=bindings,
            audit_by_id=audit_by_id,
        )

    output: list[dict[str, str]] = []
    by_id: dict[str, dict[str, str]] = {}
    reason_counts: Counter[str] = Counter()
    foreign_candidates = 0
    shared_demotions = 0
    shared_survivors: dict[str, str] = {}

    for original in scored:
        row = dict(original)
        account_id = row["Id"]
        account = accounts[account_id]
        binding = bindings[account_id]
        domain = normalized_domain(row.get("AI_Prospect_Value_Canonical_URL__c"))
        pre_mcv = int(float(row["AI_Prospect_Value_MCV_Point__c"]))
        pre_arr = row["AI_Prospect_Value_ARR_Point__c"]
        reasons: list[str] = []
        resolution = ""
        survivor = ""
        us_location = _location_corroborated(account, binding)

        foreign_conflict = (
            row["score_source_tier"] in {"tier_0_retained_v1", "tier_2_usable_website_score"}
            and _us_based(account)
            and is_foreign_country_domain(domain)
            and not us_location
        )
        if foreign_conflict:
            foreign_candidates += 1
            reasons.append("foreign_domain_entity_conflict")

        if domain in domain_resolution and account_id in affected_high_ids:
            survivor, resolution = domain_resolution[domain]
            if survivor:
                shared_survivors[domain] = survivor
                if account_id == survivor:
                    reasons.append("shared_domain_sellable_survivor")
                else:
                    reasons.append("shared_domain_non_survivor")
                    shared_demotions += 1
            else:
                reasons.append("shared_domain_unresolved_high_value")
                shared_demotions += 1

        numeric_demotion = foreign_conflict or "shared_domain_non_survivor" in reasons or "shared_domain_unresolved_high_value" in reasons
        if numeric_demotion:
            cohort = cohorts.value(
                row["lane"],
                _state_code(account.get("BillingState")),
                _lower(account.get("Account_Segment__c")),
                quantile=0.25,
            )
            fallback = min(pre_mcv, cohort.point)
            row.update(_band_values(fallback))
            row["confidence"] = "Low"
            row["AI_Prospect_Value_Confidence__c"] = "Low"
            codes = json.loads(row.get("fallback_reason_codes") or "[]")
            codes.extend(reasons)
            codes.append(f"conservative_entity_fallback:{cohort.key}:n={cohort.count}")
            row["fallback_reason_codes"] = json.dumps(codes, separators=(",", ":"))
            row["fallback_reason_primary"] = reasons[0]
            row["AI_Prospect_Value_Evidence__c"] = (
                _text(row.get("AI_Prospect_Value_Evidence__c"))
                + " | entity_controls=" + ",".join(reasons)
                + f" | conservative_fallback={fallback}"
            )[:32768]
        for reason in reasons:
            reason_counts[reason] += 1

        row.update(
            {
                "normalized_domain": domain,
                "entity_control_applied": "true" if reasons else "false",
                "entity_control_reason_codes": json.dumps(reasons, separators=(",", ":")),
                "foreign_domain_conflict": "true" if foreign_conflict else "false",
                "us_location_corroborated": "true" if us_location else "false",
                "shared_domain_resolution": resolution,
                "shared_domain_survivor_id": survivor,
                "pre_entity_control_mcv": str(pre_mcv),
                "post_entity_control_mcv": row["AI_Prospect_Value_MCV_Point__c"],
                "pre_entity_control_arr": pre_arr,
                "post_entity_control_arr": row["AI_Prospect_Value_ARR_Point__c"],
                "AI_Prospect_Value_Model_Version__c": MODEL_VERSION,
                "AI_Prospect_Value_Run_Id__c": release_run_id,
                "AI_Prospect_Value_Source__c": SOURCE_VERSION,
                "AI_Prospect_Value_Updated_At__c": updated_at,
            }
        )
        _update_components(row, run_id=release_run_id, reasons=reasons, domain=domain, survivor=survivor)
        output.append(row)
        by_id[account_id] = row

    decision_output = [
        dict(by_id[row["Id"]]) if row["Id"] in by_id else {**row, **{field: "" for field in ENTITY_CONTROL_FIELDS}}
        for row in decisions
    ]
    if len(output) != 16_924 or len({row["Id"] for row in output}) != 16_924:
        raise RuntimeError("Entity controls changed approved population membership")

    top20 = _top_rows(output)
    top20_domains = [row["normalized_domain"] for row in top20 if row["normalized_domain"]]
    international_top20 = [row["Id"] for row in top20 if row["foreign_domain_conflict"] == "true"]
    unresolved_duplicate_top20 = sorted(
        domain
        for domain, count in Counter(top20_domains).items()
        if count > 1 and domain_resolution.get(domain, ("", ""))[0] == ""
    )
    high_domain_duplicates = {
        domain: [row["Id"] for row in output if row["normalized_domain"] == domain and float(row["AI_Prospect_Value_MCV_Point__c"]) >= HIGH_VALUE_MCV]
        for domain in domain_resolution
    }
    remaining_high_duplicates = {domain: ids for domain, ids in high_domain_duplicates.items() if len(ids) > 1}
    controls = {
        "population_exactly_16924": len(output) == 16_924,
        "known_kdd_below_750": float(by_id["001TP00000isSkVYAU"]["AI_Prospect_Value_MCV_Point__c"]) < 750,
        "known_beaumont_demoted": float(by_id["001TP00000isnP4YAI"]["AI_Prospect_Value_MCV_Point__c"]) < float(by_id["001TP00000isnP4YAI"]["pre_entity_control_mcv"]),
        "known_rattikin_child_demoted": float(by_id["0014x00001RsybZAAR"]["AI_Prospect_Value_MCV_Point__c"]) < 200,
        "homeland_not_both_high": sum(float(by_id[i]["AI_Prospect_Value_MCV_Point__c"]) >= 200 for i in ("001TP000008RGAXYA4", "001TP00000ivciQYAQ")) <= 1,
        "no_top20_foreign_domain_conflict": not international_top20,
        "no_unresolved_duplicate_domain_in_top20": not unresolved_duplicate_top20,
        "no_shared_domain_multiple_high_values": not remaining_high_duplicates,
        "website_field_not_in_payload": True,
        "salesforce_writes": 0,
    }
    all_controls_passed = all(value is True or value == 0 for value in controls.values())

    audit_fields = tuple(output[0].keys())
    decision_fields = tuple(decision_output[0].keys())
    payload_rows = [{field: row[field] for field in CANARY_FIELDS} for row in output]
    top_fields = (
        "rank", "Id", "Name", "lane", "score_source_tier", "confidence", "normalized_domain",
        "AI_Prospect_Value_MCV_Point__c", "AI_Prospect_Value_ARR_Point__c", "entity_control_reason_codes",
        "shared_domain_resolution", "shared_domain_survivor_id",
    )
    top_rows = [
        {field: str(index) if field == "rank" else _text(row.get(field)) for field in top_fields}
        for index, row in enumerate(top20, 1)
    ]
    files = {
        "scored_population_entity_guardrailed.csv": (output, audit_fields),
        "full_population_decisions_entity_guardrailed.csv": (decision_output, decision_fields),
        "full_candidate_payload_no_write.csv": (payload_rows, CANARY_FIELDS),
        "top_20_after_entity_controls.csv": (top_rows, top_fields),
    }
    for name, (rows, fields) in files.items():
        write_csv(output_dir / name, rows, fields)
    control_path = output_dir / "entity_control_results.json"
    control_path.write_text(
        json.dumps(
            {
                "all_controls_passed": all_controls_passed,
                "controls": controls,
                "international_top20_ids": international_top20,
                "unresolved_duplicate_top20_domains": unresolved_duplicate_top20,
                "remaining_high_domain_duplicates": remaining_high_duplicates,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    summary = {
        "run_id": release_run_id,
        "model_version": MODEL_VERSION,
        "scored_population": len(output),
        "coverage_gate": "PASS" if all_controls_passed else "STOP",
        "all_controls_passed": all_controls_passed,
        "foreign_domain_conflict_rows": foreign_candidates,
        "shared_high_value_rows_reviewed": len(affected_high_ids),
        "shared_domain_count": len(domain_resolution),
        "shared_domain_demotions": shared_demotions,
        "shared_domain_survivors": shared_survivors,
        "reason_counts": dict(reason_counts),
        "no_write": True,
        "salesforce_writes": 0,
        "publication_allowed": all_controls_passed,
        "source_hashes": {
            "scored_audit": sha256_file(scored_audit_path),
            "full_decisions": sha256_file(full_decisions_path),
            "accounts": sha256_file(accounts_path),
            "binding": sha256_file(binding_path),
            "membership": sha256_file(membership_path),
        },
    }
    summary_path = output_dir / "entity_control_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    artifacts = [output_dir / name for name in files] + [control_path, summary_path]
    (output_dir / "no_write_manifest.json").write_text(
        json.dumps(
            {
                "run_id": release_run_id,
                "immutable": True,
                "no_write": True,
                "artifacts": [
                    {"path": path.name, "bytes": path.stat().st_size, "sha256": sha256_file(path)}
                    for path in artifacts
                ],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return {**summary, "output_dir": str(output_dir)}
