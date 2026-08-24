"""Guarded publisher for the authorized remaining V1.1 production population."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .publication import CANARY_FIELDS, normalized_salesforce_value, rollback_rows, write_csv
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
            if path.is_file() and path.name != "remaining_publication_manifest.json"
        ],
    }


def _aggregate_count(org: str, run_id: str) -> tuple[int, dict[str, Any]]:
    soql = f"SELECT COUNT() FROM Account WHERE AI_Prospect_Value_Run_Id__c = '{run_id}'"
    result = _run(["sf", "data", "query", "--target-org", org, "--query", soql, "--json"])
    payload = _require_command_success(result, "final run aggregate count")
    query_result = payload.get("result", {})
    records = query_result.get("records", [])
    count_value = query_result.get("totalSize") if not records else next(
        (value for key, value in records[0].items() if key != "attributes"), None
    )
    if count_value is None:
        raise RuntimeError("Aggregate count query returned no count value")
    return int(count_value), {"soql": soql, "command": result, "count": int(count_value)}


def finalize_completed_remaining_publication(
    *, publication_dir: Path, org: str, run_id: str
) -> dict[str, Any]:
    """Finalize after a completed write/readback when only aggregate parsing failed."""
    summary_path = publication_dir / "remaining_publication_summary.json"
    manifest_path = publication_dir / "remaining_publication_manifest.json"
    if summary_path.exists() or manifest_path.exists():
        raise FileExistsError("Remaining publication is already finalized")
    preflight = json.loads((publication_dir / "prewrite_reconciliation.json").read_text(encoding="utf-8-sig"))
    ledger = read_csv(publication_dir / "remaining_success_failure_ledger.csv")
    mismatches = read_csv(publication_dir / "remaining_readback_mismatches.csv")
    rollback = read_csv(publication_dir / "rollback_successful_remaining_ids_only.csv")
    bulk_command = json.loads((publication_dir / "remaining_v1_1_bulk_update_command.json").read_text(encoding="utf-8-sig"))
    bulk_result = bulk_command.get("json", {}).get("result", {})
    success_rows = sum(row.get("Success") == "true" for row in ledger)
    failed_rows = len(ledger) - success_rows
    if (
        len(ledger) != int(preflight["final_remaining_ids_to_submit"])
        or success_rows != len(ledger)
        or failed_rows != 0
        or mismatches
        or len(rollback) != success_rows
        or int(bulk_result.get("failedRecords", -1)) != 0
        or int(bulk_result.get("successfulRecords", -1)) != success_rows
    ):
        raise RuntimeError("Completed write/readback artifacts do not reconcile; refusing finalization")
    aggregate_count, aggregate_artifact = _aggregate_count(org, run_id)
    _write_json(publication_dir / "final_run_aggregate_query.json", aggregate_artifact)
    expected = 16_865 - int(preflight["newly_quarantined_ids"])
    summary = {
        "decision": "PASS" if aggregate_count == expected else "FAIL",
        "target_org": org,
        "run_id": run_id,
        "staged_changed_population": 16_865,
        "canary_success_ids_excluded": 50,
        "remaining_candidates_queried": 16_815,
        "newly_quarantined_ids": int(preflight["newly_quarantined_ids"]),
        "remaining_rows_submitted": len(ledger),
        "verified_success_rows": success_rows,
        "failed_rows": failed_rows,
        "missing_readback_ids": 0,
        "unexpected_readback_ids": 0,
        "value_mismatches": len(mismatches),
        "bulk_job_id": str(bulk_result.get("jobId", "")),
        "final_live_count": aggregate_count,
        "expected_final_live_count": expected,
        "rollback_rows": len(rollback),
        "website_writes": 0,
        "clear_transitions": 0,
        "completed_at": _iso_z(_utc_now()),
        "finalization_note": "Write/readback completed before aggregate COUNT totalSize parser correction; no data resubmission occurred.",
    }
    _write_json(summary_path, summary)
    manifest = _manifest(publication_dir)
    manifest.update({"run_id": run_id, "completed_at": summary["completed_at"]})
    _write_json(manifest_path, manifest)
    return {**summary, "output_dir": str(publication_dir)}


def publish_remaining_v1_1(
    *,
    staging_dir: Path,
    canary_dir: Path,
    accounts_snapshot_path: Path,
    output_dir: Path,
    org: str,
    execute: bool,
) -> dict[str, Any]:
    if not execute:
        raise PermissionError("Remaining production publication requires execute=True")
    if output_dir.exists():
        raise FileExistsError(f"Remaining publication output is immutable and exists: {output_dir}")
    output_dir.mkdir(parents=True)
    query_log: list[dict[str, Any]] = []

    staging_summary = json.loads((staging_dir / "staging_summary.json").read_text(encoding="utf-8-sig"))
    canary_summary = json.loads((canary_dir / "canary_execution_summary.json").read_text(encoding="utf-8-sig"))
    if canary_summary.get("decision") != "PASS" or int(canary_summary.get("verified_success_rows", 0)) != 50:
        raise RuntimeError("The authorized 50-row canary did not pass")
    run_id = str(canary_summary["run_id"])
    if run_id != staging_summary.get("run_id"):
        raise RuntimeError("Canary and staging run IDs differ")

    staging_manifest = json.loads((staging_dir / "staging_manifest.json").read_text(encoding="utf-8-sig"))
    canary_manifest = json.loads((canary_dir / "canary_execution_manifest.json").read_text(encoding="utf-8-sig"))
    staging_bad = [item["path"] for item in staging_manifest["artifacts"] if sha256_file(staging_dir / item["path"]) != item["sha256"]]
    canary_bad = [item["path"] for item in canary_manifest["artifacts"] if sha256_file(canary_dir / item["path"]) != item["sha256"]]
    if staging_bad or canary_bad:
        raise RuntimeError(f"Input artifact hashes failed: staging={staging_bad}, canary={canary_bad}")

    desired_rows = read_csv(staging_dir / "full_changed_id_only_payload.csv")
    if len(desired_rows) != 16_865:
        raise RuntimeError(f"Expected 16,865 staged changed IDs, found {len(desired_rows):,}")
    for row in desired_rows:
        validate_v1_1_payload(row)
    desired_by_id = {row["Id"]: row for row in desired_rows}
    if len(desired_by_id) != len(desired_rows):
        raise RuntimeError("Duplicate IDs in staged changed-only payload")

    canary_ledger = read_csv(canary_dir / "canary_success_id_ledger.csv")
    canary_ids = {row["Id"] for row in canary_ledger if row["Success"] == "true"}
    if len(canary_ids) != 50 or not canary_ids.issubset(desired_by_id):
        raise RuntimeError("Canary success-ID reconciliation failed")
    remaining_ids = sorted(set(desired_by_id) - canary_ids)
    if len(remaining_ids) != 16_815:
        raise RuntimeError(f"Expected exactly 16,815 remaining candidate IDs, found {len(remaining_ids):,}")

    snapshot = {row["Id"]: row for row in read_csv(accounts_snapshot_path)}
    staged_backup_rows = json.loads((staging_dir / "exact_current_state_backup.json").read_text(encoding="utf-8-sig"))
    staged_backup = {row["Id"]: row for row in staged_backup_rows}
    if not set(remaining_ids).issubset(staged_backup):
        raise RuntimeError("Staged immutable backup is incomplete for remaining IDs")

    org_result = _run(["sf", "org", "display", "--target-org", org, "--json"])
    _write_json(output_dir / "org_identity.json", _require_command_success(org_result, "remaining publication org identity"))

    current = query_exact(
        remaining_ids,
        org=org,
        query_label="remaining_exact_fresh_conflict_check",
        query_log=query_log,
    )
    modstamp_conflicts = {
        account_id
        for account_id in remaining_ids
        if normalized_salesforce_value("SystemModstamp", staged_backup[account_id].get("SystemModstamp"))
        != normalized_salesforce_value("SystemModstamp", current[account_id].get("SystemModstamp"))
    }
    source_conflict_rows = source_conflicts(current, snapshot)
    source_conflict_ids = {row["Id"] for row in source_conflict_rows}
    conflict_ids = modstamp_conflicts | source_conflict_ids
    conflict_rows = [
        {
            "Id": account_id,
            "field": "SystemModstamp",
            "staged_value": str(staged_backup[account_id].get("SystemModstamp") or ""),
            "fresh_value": str(current[account_id].get("SystemModstamp") or ""),
            "reason": "changed_after_staging",
        }
        for account_id in sorted(modstamp_conflicts)
    ]
    conflict_rows.extend(
        {
            "Id": row["Id"],
            "field": row["field"],
            "staged_value": row["snapshot_value"],
            "fresh_value": row["live_value"],
            "reason": "source_field_changed_after_snapshot",
        }
        for row in source_conflict_rows
    )
    write_csv(
        output_dir / "fresh_conflict_quarantine.csv",
        conflict_rows,
        ("Id", "field", "staged_value", "fresh_value", "reason"),
    )

    stable_ids = [account_id for account_id in remaining_ids if account_id not in conflict_ids]
    if len(stable_ids) + len(conflict_ids) != len(remaining_ids):
        raise RuntimeError("Fresh conflict partition reconciliation failed")

    final_payload: list[dict[str, str]] = []
    already_exact_ids: list[str] = []
    prevented_clear_cells: list[dict[str, str]] = []
    for account_id in stable_ids:
        desired = dict(desired_by_id[account_id])
        for field in CANARY_FIELDS[1:]:
            if str(desired.get(field, "")).strip() == "" and str(current[account_id].get(field) or "").strip() != "":
                prevented_clear_cells.append(
                    {"Id": account_id, "field": field, "preserved_value": str(current[account_id][field])}
                )
                desired[field] = str(current[account_id][field])
        validate_v1_1_payload(desired)
        if _exact_match(desired, current[account_id]):
            already_exact_ids.append(account_id)
        else:
            final_payload.append({field: desired[field] for field in CANARY_FIELDS})

    if set(canary_ids) & {row["Id"] for row in final_payload}:
        raise RuntimeError("Canary IDs leaked into remaining payload")
    if any("Website" in row for row in final_payload):
        raise RuntimeError("Account.Website appeared in remaining payload")
    clear_transitions = [
        (row["Id"], field)
        for row in final_payload
        for field in CANARY_FIELDS[1:]
        if str(current[row["Id"]].get(field) or "").strip() and not str(row.get(field, "")).strip()
    ]
    if clear_transitions:
        raise RuntimeError(f"Remaining payload contains clear transitions: {clear_transitions[:10]}")
    if len(final_payload) + len(already_exact_ids) + len(conflict_ids) != len(remaining_ids):
        raise RuntimeError("Final remaining payload reconciliation failed")

    final_ids = [row["Id"] for row in final_payload]
    _write_json(output_dir / "exact_final_remaining_prewrite_backup.json", _backup_rows(current, final_ids))
    write_csv(output_dir / "final_remaining_changed_id_only_payload.csv", final_payload, CANARY_FIELDS)
    write_csv(
        output_dir / "prevented_clear_cells.csv",
        prevented_clear_cells,
        ("Id", "field", "preserved_value"),
    )
    write_csv(output_dir / "already_exact_final_run_ids.csv", ({"Id": value} for value in already_exact_ids), ("Id",))
    preflight = {
        "staged_changed_ids": len(desired_rows),
        "canary_success_ids_excluded": len(canary_ids),
        "remaining_candidates_queried": len(remaining_ids),
        "newly_quarantined_ids": len(conflict_ids),
        "already_exact_ids": len(already_exact_ids),
        "final_remaining_ids_to_submit": len(final_payload),
        "account_website_present": False,
        "clear_transitions": 0,
        "prevented_clear_cells": len(prevented_clear_cells),
        "reconciliation_passed": True,
    }
    _write_json(output_dir / "prewrite_reconciliation.json", preflight)
    _write_json(output_dir / "exact_query_log_prewrite.json", query_log)

    bulk = run_bulk_update(
        output_dir / "final_remaining_changed_id_only_payload.csv",
        org=org,
        phase="remaining_v1_1",
        live_dir=output_dir,
    )
    readback = query_exact(
        final_ids,
        org=org,
        query_label="remaining_exact_postwrite_readback",
        query_log=query_log,
    )
    _write_json(output_dir / "remaining_exact_postwrite_readback.json", [readback[account_id] for account_id in sorted(readback)])

    ledger: list[dict[str, str]] = []
    success_ids: list[str] = []
    mismatch_rows: list[dict[str, str]] = []
    desired_final_by_id = {row["Id"]: row for row in final_payload}
    for account_id in final_ids:
        desired = desired_final_by_id[account_id]
        exact = _exact_match(desired, readback[account_id])
        if exact:
            success_ids.append(account_id)
        else:
            for field in CANARY_FIELDS[1:]:
                expected = normalized_salesforce_value(field, desired.get(field))
                actual = normalized_salesforce_value(field, readback[account_id].get(field))
                if expected != actual:
                    mismatch_rows.append({"Id": account_id, "field": field, "expected": expected, "actual": actual})
        ledger.append(
            {
                "Phase": "remaining_v1_1",
                "JobId": bulk["job_id"],
                "Id": account_id,
                "Success": "true" if exact else "false",
                "Error": "" if exact else "normalized_field_mismatch_or_write_failure",
                "CandidateRunId": run_id,
                "PostWriteSystemModstamp": str(readback[account_id].get("SystemModstamp") or ""),
            }
        )
    write_csv(
        output_dir / "remaining_success_failure_ledger.csv",
        ledger,
        ("Phase", "JobId", "Id", "Success", "Error", "CandidateRunId", "PostWriteSystemModstamp"),
    )
    write_csv(output_dir / "remaining_readback_mismatches.csv", mismatch_rows, ("Id", "field", "expected", "actual"))

    post_modstamps = {account_id: str(readback[account_id].get("SystemModstamp") or "") for account_id in success_ids}
    rollback = rollback_rows(
        current,
        success_ids,
        expected_failed_run_id=run_id,
        successful_systemmodstamp_by_id=post_modstamps,
        current_by_id=readback,
    )
    write_csv(output_dir / "rollback_successful_remaining_ids_only.csv", rollback, CANARY_FIELDS)
    _write_json(
        output_dir / "rollback_guard_and_command.json",
        {
            "target_org": org,
            "expected_run_id": run_id,
            "success_ids": len(success_ids),
            "compare_and_swap_fields": ["AI_Prospect_Value_Run_Id__c", "SystemModstamp"],
            "command": f"sf data update bulk --sobject Account --file rollback_successful_remaining_ids_only.csv --line-ending LF --wait 10 --target-org {org}",
            "warning": "Re-query exact success IDs and revalidate Run ID plus SystemModstamp before rollback.",
        },
    )

    aggregate_count, aggregate_artifact = _aggregate_count(org, run_id)
    _write_json(output_dir / "final_run_aggregate_query.json", aggregate_artifact)
    expected_live_count = 16_865 - len(conflict_ids)
    readback_pass = (
        len(success_ids) == len(final_payload)
        and not mismatch_rows
        and bulk["command_ok"]
        and bulk["results_ok"]
        and bulk["failure_count"] in {None, 0}
    )
    aggregate_pass = aggregate_count == expected_live_count
    summary = {
        "decision": "PASS" if readback_pass and aggregate_pass else "FAIL",
        "target_org": org,
        "run_id": run_id,
        "staged_changed_population": 16_865,
        "canary_success_ids_excluded": 50,
        "remaining_candidates_queried": 16_815,
        "newly_quarantined_ids": len(conflict_ids),
        "remaining_rows_submitted": len(final_payload),
        "verified_success_rows": len(success_ids),
        "failed_rows": len(final_payload) - len(success_ids),
        "missing_readback_ids": 0,
        "unexpected_readback_ids": 0,
        "value_mismatches": len(mismatch_rows),
        "bulk_job_id": bulk["job_id"],
        "final_live_count": aggregate_count,
        "expected_final_live_count": expected_live_count,
        "rollback_rows": len(rollback),
        "website_writes": 0,
        "clear_transitions": 0,
        "completed_at": _iso_z(_utc_now()),
    }
    _write_json(output_dir / "remaining_publication_summary.json", summary)
    _write_json(output_dir / "exact_query_log.json", query_log)
    manifest = _manifest(output_dir)
    manifest.update({"run_id": run_id, "completed_at": summary["completed_at"]})
    _write_json(output_dir / "remaining_publication_manifest.json", manifest)
    return {**summary, "output_dir": str(output_dir)}
