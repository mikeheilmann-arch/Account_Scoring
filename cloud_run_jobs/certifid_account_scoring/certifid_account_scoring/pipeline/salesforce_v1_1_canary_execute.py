"""Execute only the authorized 50-row V1.1 production canary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .publication import (
    CANARY_FIELDS,
    normalized_salesforce_value,
    rollback_rows,
    validate_salesforce_describe,
    write_csv,
)
from .sales_prioritization_release import read_csv, sha256_file
from .salesforce_live_release import (
    _backup_rows,
    _iso_z,
    _require_command_success,
    _run,
    _utc_now,
    _write_json,
    query_exact,
    run_bulk_update,
    source_conflicts,
)
from .salesforce_v1_1_staging import validate_v1_1_payload


def _exact_match(desired: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    return all(
        normalized_salesforce_value(field, desired.get(field))
        == normalized_salesforce_value(field, current.get(field))
        for field in CANARY_FIELDS
    )


def _verify(desired: Sequence[Mapping[str, str]], current: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    desired_by_id = {row["Id"]: row for row in desired}
    missing = sorted(set(desired_by_id) - set(current))
    unexpected = sorted(set(current) - set(desired_by_id))
    mismatches: list[dict[str, str]] = []
    for account_id in sorted(set(desired_by_id) & set(current)):
        for field in CANARY_FIELDS[1:]:
            expected = normalized_salesforce_value(field, desired_by_id[account_id].get(field))
            actual = normalized_salesforce_value(field, current[account_id].get(field))
            if expected != actual:
                mismatches.append({"Id": account_id, "field": field, "expected": expected, "actual": actual})
    return {
        "passed": not missing and not unexpected and not mismatches,
        "missing_ids": missing,
        "unexpected_ids": unexpected,
        "mismatches": mismatches,
        "verified_rows": len(desired_by_id) - len(missing),
    }


def _manifest(directory: Path) -> dict[str, Any]:
    return {
        "immutable": True,
        "artifacts": [
            {
                "path": str(path.relative_to(directory)).replace("\\", "/"),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in sorted(directory.rglob("*"))
            if path.is_file() and path.name != "canary_execution_manifest.json"
        ],
    }


def execute_v1_1_canary(
    *,
    staging_dir: Path,
    entity_controls_path: Path,
    accounts_snapshot_path: Path,
    output_dir: Path,
    org: str,
    execute: bool,
) -> dict[str, Any]:
    if not execute:
        raise PermissionError("Production canary requires execute=True")
    if output_dir.exists():
        raise FileExistsError(f"Canary execution output is immutable and exists: {output_dir}")
    output_dir.mkdir(parents=True)
    query_log: list[dict[str, Any]] = []

    controls = json.loads(entity_controls_path.read_text(encoding="utf-8-sig"))
    if controls.get("all_controls_passed") is not True:
        raise RuntimeError("Entity controls did not pass; canary write prohibited")
    staging_summary = json.loads((staging_dir / "staging_summary.json").read_text(encoding="utf-8-sig"))
    if staging_summary.get("canary_status") != "STAGED_NOT_EXECUTED" or staging_summary.get("salesforce_writes") != 0:
        raise RuntimeError("Staging package is not an unexecuted canary")

    staging_manifest = json.loads((staging_dir / "staging_manifest.json").read_text(encoding="utf-8-sig"))
    bad_hashes = [
        item["path"]
        for item in staging_manifest["artifacts"]
        if sha256_file(staging_dir / item["path"]) != item["sha256"]
    ]
    if bad_hashes:
        raise RuntimeError(f"Staging artifact hash mismatch: {bad_hashes}")

    payload = read_csv(staging_dir / "canary_50_payload_DO_NOT_EXECUTE.csv")
    audit = read_csv(staging_dir / "canary_50_stratified_audit.csv")
    roles = read_csv(staging_dir / "canary_50_selection_roles.csv")
    snapshot = {row["Id"]: row for row in read_csv(accounts_snapshot_path)}
    if len(payload) != len(audit) != 50:
        raise RuntimeError("Canary must contain exactly 50 payload/audit rows")
    if len(payload) != 50 or len(audit) != 50 or len(roles) != 50:
        raise RuntimeError("Canary must contain exactly 50 payload/audit/role rows")
    for row in payload:
        validate_v1_1_payload(row)
    if [row["Id"] for row in payload] != [row["Id"] for row in roles]:
        raise RuntimeError("Canary payload order does not match selection-role ledger")
    if sum(row["canary_role"] == "top_20_by_arr" for row in roles) != 20:
        raise RuntimeError("Canary does not contain the exact 20 top-rank rows")
    audit_by_id = {row["Id"]: row for row in audit}
    top_ids = [row["Id"] for row in roles[:20]]
    if any(audit_by_id[account_id].get("foreign_domain_conflict") == "true" for account_id in top_ids):
        raise RuntimeError("Top 20 contains a foreign-domain conflict")
    top_domains = [audit_by_id[account_id].get("normalized_domain", "") for account_id in top_ids]
    if len([domain for domain in top_domains if domain]) != len(set(domain for domain in top_domains if domain)):
        raise RuntimeError("Top 20 contains a repeated unresolved domain")

    org_result = _run(["sf", "org", "display", "--target-org", org, "--json"])
    _write_json(output_dir / "org_identity.json", _require_command_success(org_result, "canary org identity"))
    describe_result = _run(["sf", "sobject", "describe", "--sobject", "Account", "--target-org", org, "--json"])
    describe = _require_command_success(describe_result, "canary Account describe")
    _write_json(output_dir / "fresh_account_describe.json", describe)
    schema = validate_salesforce_describe(describe, describe_source=f"sf describe {org}", intended_rows=payload)
    _write_json(output_dir / "fresh_schema_validation.json", schema.__dict__)
    if not schema.valid:
        raise RuntimeError("Fresh Account schema validation failed")

    ids = [row["Id"] for row in payload]
    staged_backup_rows = json.loads((staging_dir / "canary_exact_current_backup.json").read_text(encoding="utf-8-sig"))
    staged_backup = {row["Id"]: row for row in staged_backup_rows}
    immediate = query_exact(ids, org=org, query_label="authorized_canary_immediate_prewrite", query_log=query_log)
    _write_json(output_dir / "canary_exact_prewrite_backup.json", _backup_rows(immediate, ids))
    modstamp_conflicts = [
        account_id
        for account_id in ids
        if normalized_salesforce_value("SystemModstamp", staged_backup[account_id].get("SystemModstamp"))
        != normalized_salesforce_value("SystemModstamp", immediate[account_id].get("SystemModstamp"))
    ]
    source_rows = source_conflicts(immediate, snapshot)
    write_csv(
        output_dir / "prewrite_conflict_queue.csv",
        [
            *({"Id": account_id, "field": "SystemModstamp", "snapshot_value": str(staged_backup[account_id].get("SystemModstamp") or ""), "live_value": str(immediate[account_id].get("SystemModstamp") or "")} for account_id in modstamp_conflicts),
            *source_rows,
        ],
        ("Id", "field", "snapshot_value", "live_value"),
    )
    if modstamp_conflicts or source_rows:
        raise RuntimeError(
            f"Canary immediate prewrite conflict: modstamp={modstamp_conflicts}, source_ids={sorted({r['Id'] for r in source_rows})}"
        )

    write_csv(output_dir / "authorized_canary_50_payload.csv", payload, CANARY_FIELDS)
    bulk = run_bulk_update(
        output_dir / "authorized_canary_50_payload.csv",
        org=org,
        phase="authorized_canary",
        live_dir=output_dir,
    )
    readback = query_exact(ids, org=org, query_label="authorized_canary_exact_readback", query_log=query_log)
    _write_json(output_dir / "canary_exact_readback.json", [readback[account_id] for account_id in sorted(readback)])
    verification = _verify(payload, readback)
    verification["bulk"] = {
        key: bulk[key] for key in ("job_id", "command_ok", "results_ok", "failure_count")
    }
    verification["passed"] = bool(
        verification["passed"]
        and bulk["command_ok"]
        and bulk["results_ok"]
        and bulk["job_id"]
        and bulk["failure_count"] in {None, 0}
    )
    _write_json(output_dir / "canary_verification.json", verification)

    run_id = payload[0]["AI_Prospect_Value_Run_Id__c"]
    ledger = []
    success_ids = []
    for desired in payload:
        account_id = desired["Id"]
        success = _exact_match(desired, readback[account_id])
        if success:
            success_ids.append(account_id)
        ledger.append(
            {
                "Phase": "authorized_canary",
                "JobId": bulk["job_id"],
                "Id": account_id,
                "Success": "true" if success else "false",
                "Error": "" if success else "normalized_field_mismatch",
                "CandidateRunId": run_id,
                "PostWriteSystemModstamp": str(readback[account_id].get("SystemModstamp") or ""),
            }
        )
    write_csv(
        output_dir / "canary_success_id_ledger.csv",
        ledger,
        ("Phase", "JobId", "Id", "Success", "Error", "CandidateRunId", "PostWriteSystemModstamp"),
    )
    post_modstamps = {account_id: str(readback[account_id].get("SystemModstamp") or "") for account_id in success_ids}
    rollback = rollback_rows(
        immediate,
        success_ids,
        expected_failed_run_id=run_id,
        successful_systemmodstamp_by_id=post_modstamps,
        current_by_id=readback,
    )
    write_csv(output_dir / "rollback_successful_canary_ids_only.csv", rollback, CANARY_FIELDS)
    _write_json(
        output_dir / "rollback_guard_and_command.json",
        {
            "target_org": org,
            "expected_run_id": run_id,
            "success_ids": len(success_ids),
            "compare_and_swap_fields": ["AI_Prospect_Value_Run_Id__c", "SystemModstamp"],
            "command": f"sf data update bulk --sobject Account --file rollback_successful_canary_ids_only.csv --line-ending LF --wait 10 --target-org {org}",
            "warning": "Re-query exact IDs and revalidate both compare-and-swap fields before rollback.",
        },
    )
    _write_json(output_dir / "exact_query_log.json", query_log)
    summary = {
        "decision": "PASS" if verification["passed"] and len(success_ids) == 50 else "FAIL",
        "target_org": org,
        "run_id": run_id,
        "canary_rows_submitted": 50,
        "bulk_job_id": bulk["job_id"],
        "verified_success_rows": len(success_ids),
        "failed_rows": 50 - len(success_ids),
        "rollback_rows": len(rollback),
        "remaining_population_written": 0,
        "website_writes": 0,
        "clears": 0,
        "completed_at": _iso_z(_utc_now()),
    }
    _write_json(output_dir / "canary_execution_summary.json", summary)
    manifest = _manifest(output_dir)
    manifest.update({"run_id": run_id, "completed_at": summary["completed_at"]})
    _write_json(output_dir / "canary_execution_manifest.json", manifest)
    return {**summary, "output_dir": str(output_dir)}
