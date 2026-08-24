"""Guarded live publisher for the directional Sales-prioritization V1.

The publisher is deliberately explicit and two-phase:

1. exact-ID current-state query and immutable backup;
2. stratified 50-row canary, exact readback, and a hard stop on failure;
3. fresh exact-ID conflict check, remaining changed-only publish, and readback;
4. success ledger plus compare-and-swap rollback payload.

It never writes Account.Website and never clears excluded Account values.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import subprocess
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .publication import (
    CANARY_FIELDS,
    PROHIBITED_FIELDS,
    PublicationValidationError,
    changed_only_diff,
    normalized_salesforce_value,
    rollback_rows,
    validate_payload_row,
    validate_salesforce_describe,
    verify_normalized_readback,
    write_csv,
)
from .sales_prioritization_release import MODEL_VERSION, read_csv, sha256_file


LIVE_SOURCE_FIELDS = (
    "Name",
    "Website",
    "BillingState",
    "Owner.Name",
    "Account_Segment__c",
    "Final_Monthly_Closing_Volume__c",
    "Monthly_Closing_Volume_Source__c",
    "Type",
    "Account_Status__c",
    "Company_Type__c",
    "Active_Customer__c",
    "ParentId",
)
QUERY_FIELDS = (
    "Id",
    *LIVE_SOURCE_FIELDS,
    "SystemModstamp",
    *CANARY_FIELDS[1:],
)
SOURCE_SNAPSHOT_FIELDS = {
    "Name": "Name",
    "Website": "Website",
    "BillingState": "BillingState",
    "Owner.Name": "Owner.Name",
    "Account_Segment__c": "Account_Segment__c",
    "Final_Monthly_Closing_Volume__c": "Final_Monthly_Closing_Volume__c",
    "Monthly_Closing_Volume_Source__c": "Monthly_Closing_Volume_Source__c",
    "Type": "Type",
    "Account_Status__c": "Account_Status__c",
    "Company_Type__c": "Company_Type__c",
    "Active_Customer__c": "Active_Customer__c",
    "ParentId": "ParentId",
}
BACKUP_JSON_FIELDS = ("Id", *LIVE_SOURCE_FIELDS, "SystemModstamp", *CANARY_FIELDS[1:])
CANARY_SIZE = 50


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, value: Any) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite immutable artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _run(command: Sequence[str], *, cwd: Path | None = None) -> dict[str, Any]:
    started = _iso_z(_utc_now())
    resolved = list(command)
    if resolved and resolved[0] == "sf":
        executable = shutil.which("sf")
        if not executable:
            raise FileNotFoundError("Salesforce CLI executable 'sf' was not found on PATH")
        resolved[0] = executable
    process = subprocess.run(
        resolved,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    result: dict[str, Any] = {
        "command": list(command),
        "started_at": started,
        "finished_at": _iso_z(_utc_now()),
        "returncode": process.returncode,
        "stdout": process.stdout,
        "stderr": process.stderr,
    }
    try:
        result["json"] = json.loads(process.stdout.lstrip("\ufeff"))
    except json.JSONDecodeError:
        result["json"] = None
    return result


def _require_command_success(result: Mapping[str, Any], label: str) -> Mapping[str, Any]:
    payload = result.get("json")
    status = payload.get("status") if isinstance(payload, Mapping) else None
    if int(result.get("returncode", 1)) != 0 or status not in {None, 0}:
        raise RuntimeError(
            f"{label} failed: returncode={result.get('returncode')} status={status} "
            f"stderr={str(result.get('stderr', ''))[-1000:]}"
        )
    if not isinstance(payload, Mapping):
        raise RuntimeError(f"{label} did not return parseable JSON")
    return payload


def _flatten_record(record: Mapping[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for key, value in record.items():
        if key == "attributes":
            continue
        if key == "Owner" and isinstance(value, Mapping):
            flattened["Owner.Name"] = value.get("Name")
        else:
            flattened[key] = value
    flattened.setdefault("Owner.Name", None)
    return flattened


def _result_records(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result")
    if isinstance(result, Mapping) and isinstance(result.get("records"), list):
        return [_flatten_record(row) for row in result["records"] if isinstance(row, Mapping)]
    raise RuntimeError("Salesforce query JSON did not contain result.records")


def _soql(account_ids: Sequence[str]) -> str:
    if not account_ids or len(account_ids) != len(set(account_ids)):
        raise PublicationValidationError("Exact-ID SOQL requires non-empty unique Ids")
    quoted = ",".join(f"'{account_id}'" for account_id in account_ids)
    return f"SELECT {', '.join(QUERY_FIELDS)} FROM Account WHERE Id IN ({quoted}) ORDER BY Id"


def query_exact(
    account_ids: Sequence[str],
    *,
    org: str,
    query_label: str,
    query_log: list[dict[str, Any]],
    chunk_size: int = 200,
    allow_missing: bool = False,
) -> dict[str, dict[str, Any]]:
    expected = list(account_ids)
    if not expected or len(expected) != len(set(expected)):
        raise PublicationValidationError(f"{query_label}: expected unique non-empty Ids")
    records: list[dict[str, Any]] = []
    for chunk_index in range(0, len(expected), chunk_size):
        chunk = expected[chunk_index : chunk_index + chunk_size]
        soql = _soql(chunk)
        result = _run(["sf", "data", "query", "--target-org", org, "--query", soql, "--json"])
        query_log.append(
            {
                "label": query_label,
                "chunk": chunk_index // chunk_size + 1,
                "ids": len(chunk),
                "soql": soql,
                "returncode": result["returncode"],
                "started_at": result["started_at"],
                "finished_at": result["finished_at"],
            }
        )
        records.extend(_result_records(_require_command_success(result, query_label)))
    by_id = {str(row.get("Id", "")): row for row in records if str(row.get("Id", ""))}
    missing = sorted(set(expected) - set(by_id))
    unexpected = sorted(set(by_id) - set(expected))
    if (missing and not allow_missing) or unexpected or len(records) != len(by_id):
        raise RuntimeError(
            f"{query_label}: exact-ID reconciliation failed; missing={missing[:10]} "
            f"unexpected={unexpected[:10]} duplicate_rows={len(records) - len(by_id)}"
        )
    return by_id


def _source_normalized(field: str, value: Any) -> str:
    if field == "Active_Customer__c":
        text = "" if value is None else str(value).strip()
        return "true" if text.lower() in {"true", "1", "yes", "y"} else "false"
    if value is None:
        return ""
    text = str(value).strip()
    if field == "Website":
        lowered = text.lower()
        lowered = re.sub(r"^https?://", "", lowered)
        lowered = re.sub(r"^www\.", "", lowered)
        return lowered.rstrip("/")
    if field == "Final_Monthly_Closing_Volume__c" and text:
        try:
            return format(Decimal(text.replace(",", "")).normalize(), "f")
        except InvalidOperation:
            return text.casefold()
    return " ".join(text.split()).casefold()


def source_conflicts(
    current_by_id: Mapping[str, Mapping[str, Any]],
    snapshot_by_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, str]]:
    conflicts: list[dict[str, str]] = []
    for account_id, current in current_by_id.items():
        snapshot = snapshot_by_id.get(account_id)
        if snapshot is None:
            conflicts.append(
                {"Id": account_id, "field": "<snapshot>", "snapshot_value": "<missing>", "live_value": "<present>"}
            )
            continue
        for live_field, snapshot_field in SOURCE_SNAPSHOT_FIELDS.items():
            expected = snapshot.get(snapshot_field)
            actual = current.get(live_field)
            if _source_normalized(live_field, expected) != _source_normalized(live_field, actual):
                conflicts.append(
                    {
                        "Id": account_id,
                        "field": live_field,
                        "snapshot_value": "" if expected is None else str(expected),
                        "live_value": "" if actual is None else str(actual),
                    }
                )
    return conflicts


def _modstamp_conflict_ids(
    baseline: Mapping[str, Mapping[str, Any]], current: Mapping[str, Mapping[str, Any]]
) -> set[str]:
    return {
        account_id
        for account_id, row in current.items()
        if normalized_salesforce_value("SystemModstamp", row.get("SystemModstamp"))
        != normalized_salesforce_value("SystemModstamp", baseline[account_id].get("SystemModstamp"))
    }


def _backup_rows(rows_by_id: Mapping[str, Mapping[str, Any]], ids: Sequence[str]) -> list[dict[str, Any]]:
    return [{field: rows_by_id[account_id].get(field) for field in BACKUP_JSON_FIELDS} for account_id in ids]


def _stable_hash(run_id: str, account_id: str) -> str:
    return hashlib.sha256(f"{run_id}:{account_id}".encode("utf-8")).hexdigest()


def select_stratified_canary(
    audit_by_id: Mapping[str, Mapping[str, str]],
    candidate_ids: Iterable[str],
    *,
    run_id: str,
    size: int = CANARY_SIZE,
) -> list[str]:
    groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for account_id in candidate_ids:
        row = audit_by_id[account_id]
        lane = row.get("lane", "")
        if lane not in {"title_escrow", "legal"}:
            continue
        key = (lane, row.get("evidence_confidence", ""), row.get("mcv_band", ""))
        groups[key].append(account_id)
    for values in groups.values():
        values.sort(key=lambda account_id: _stable_hash(run_id, account_id))
    if len(groups) < 6:
        raise RuntimeError(f"Canary has insufficient non-empty strata: {sorted(groups)}")
    selected: list[str] = []
    offsets = {key: 0 for key in groups}
    keys = sorted(groups)
    while len(selected) < size:
        progressed = False
        for key in keys:
            offset = offsets[key]
            if offset < len(groups[key]):
                selected.append(groups[key][offset])
                offsets[key] += 1
                progressed = True
                if len(selected) == size:
                    break
        if not progressed:
            break
    if len(selected) != size:
        raise RuntimeError(f"Could select only {len(selected)} of {size} canary rows")
    return selected


def _recursive_values(value: Any, key_names: set[str]) -> list[Any]:
    found: list[Any] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if str(key).lower() in key_names:
                found.append(nested)
            found.extend(_recursive_values(nested, key_names))
    elif isinstance(value, list):
        for nested in value:
            found.extend(_recursive_values(nested, key_names))
    return found


def _job_id(result: Mapping[str, Any]) -> str:
    payload = result.get("json")
    candidates = _recursive_values(payload, {"id", "jobid", "job_id"})
    candidates.extend(re.findall(r"750[A-Za-z0-9]{12,15}", str(result.get("stdout", ""))))
    return next((str(value) for value in candidates if str(value).startswith("750")), "")


def _bulk_failure_count(*results: Mapping[str, Any]) -> int | None:
    values: list[Any] = []
    for result in results:
        values.extend(
            _recursive_values(
                result.get("json"),
                {
                    "numberrecordsfailed",
                    "failedrecordcount",
                    "failedrecords",
                    "failed",
                    "recordsfailed",
                },
            )
        )
    numeric: list[int] = []
    for value in values:
        try:
            numeric.append(int(value))
        except (TypeError, ValueError):
            pass
    return max(numeric) if numeric else None


def run_bulk_update(
    payload_path: Path,
    *,
    org: str,
    phase: str,
    live_dir: Path,
) -> dict[str, Any]:
    phase_dir = live_dir / f"{phase}_bulk_results"
    phase_dir.mkdir(parents=True, exist_ok=False)
    update = _run(
        [
            "sf",
            "data",
            "update",
            "bulk",
            "--sobject",
            "Account",
            "--file",
            str(payload_path.resolve()),
            "--line-ending",
            "LF",
            "--wait",
            "10",
            "--target-org",
            org,
            "--json",
        ],
        cwd=phase_dir,
    )
    _write_json(live_dir / f"{phase}_bulk_update_command.json", update)
    job_id = _job_id(update)
    results: dict[str, Any] | None = None
    if job_id:
        results = _run(
            ["sf", "data", "bulk", "results", "--job-id", job_id, "--target-org", org, "--json"],
            cwd=phase_dir,
        )
        _write_json(live_dir / f"{phase}_bulk_results_command.json", results)
    command_ok = int(update.get("returncode", 1)) == 0
    update_json = update.get("json")
    if isinstance(update_json, Mapping) and update_json.get("status") not in {None, 0}:
        command_ok = False
    results_ok = True
    if results is not None:
        results_ok = int(results.get("returncode", 1)) == 0
        result_json = results.get("json")
        if isinstance(result_json, Mapping) and result_json.get("status") not in {None, 0}:
            results_ok = False
    return {
        "phase": phase,
        "job_id": job_id,
        "command_ok": command_ok,
        "results_ok": results_ok,
        "failure_count": _bulk_failure_count(update, results or {}),
        "update": update,
        "results": results,
    }


def _desired_by_id(rows: Sequence[Mapping[str, str]]) -> dict[str, dict[str, str]]:
    return {str(row["Id"]): dict(row) for row in rows}


def _row_exact_match(desired: Mapping[str, Any], current: Mapping[str, Any]) -> bool:
    return all(
        normalized_salesforce_value(field, desired.get(field))
        == normalized_salesforce_value(field, current.get(field))
        for field in CANARY_FIELDS
    )


def _ledger_rows(
    desired_rows: Sequence[Mapping[str, str]],
    current_by_id: Mapping[str, Mapping[str, Any]],
    *,
    phase: str,
    job_id: str,
    run_id: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for desired in desired_rows:
        account_id = desired["Id"]
        current = current_by_id.get(account_id)
        exact = current is not None and _row_exact_match(desired, current)
        current_run = "" if current is None else str(current.get("AI_Prospect_Value_Run_Id__c") or "")
        if current is None:
            error = "missing_readback"
        elif current_run != run_id:
            error = f"run_id_not_written:{current_run}"
        elif not exact:
            error = "normalized_field_mismatch"
        else:
            error = ""
        rows.append(
            {
                "Phase": phase,
                "JobId": job_id,
                "Id": account_id,
                "Success": "true" if exact else "false",
                "Error": error,
                "CandidateRunId": run_id,
                "PostWriteSystemModstamp": "" if current is None else str(current.get("SystemModstamp") or ""),
            }
        )
    return rows


def _readback_json(rows_by_id: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(rows_by_id[account_id]) for account_id in sorted(rows_by_id)]


def _manifest(live_dir: Path, *, exclude_name: str = "live_publication_manifest.json") -> dict[str, Any]:
    artifacts = []
    for path in sorted(item for item in live_dir.rglob("*") if item.is_file() and item.name != exclude_name):
        artifacts.append(
            {
                "path": str(path.relative_to(live_dir)).replace("\\", "/"),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    return {"immutable": True, "artifacts": artifacts}


def publish_live_release(
    *,
    accepted_payload_path: Path,
    accepted_audit_path: Path,
    accounts_snapshot_path: Path,
    live_dir: Path,
    org: str,
    execute: bool,
) -> dict[str, Any]:
    if not execute:
        raise PermissionError("Live publication requires execute=True")
    if live_dir.exists():
        raise FileExistsError(f"Live publication attempt is immutable and already exists: {live_dir}")
    live_dir.mkdir(parents=True)
    query_log: list[dict[str, Any]] = []

    desired_rows = read_csv(accepted_payload_path)
    audit_rows = read_csv(accepted_audit_path)
    snapshot_rows = read_csv(accounts_snapshot_path)
    if not desired_rows or len(desired_rows) != len(audit_rows):
        raise RuntimeError("Accepted payload/audit population mismatch")
    for row in desired_rows:
        validate_payload_row(row)
        if set(row) & PROHIBITED_FIELDS:
            raise PublicationValidationError(f"Prohibited write field present: {set(row) & PROHIBITED_FIELDS}")
    desired_map = _desired_by_id(desired_rows)
    audit_by_id = {row["Id"]: row for row in audit_rows}
    snapshot_by_id = {row["Id"]: row for row in snapshot_rows}
    if set(desired_map) != set(audit_by_id):
        raise RuntimeError("Accepted payload/audit Id mismatch")
    run_ids = {row["AI_Prospect_Value_Run_Id__c"] for row in desired_rows}
    models = {row["AI_Prospect_Value_Model_Version__c"] for row in desired_rows}
    if len(run_ids) != 1 or models != {MODEL_VERSION}:
        raise RuntimeError(f"Payload is not one immutable {MODEL_VERSION} run: run_ids={run_ids}, models={models}")
    run_id = next(iter(run_ids))

    org_result = _run(["sf", "org", "display", "--target-org", org, "--json"])
    org_payload = _require_command_success(org_result, "Salesforce org identity")
    _write_json(live_dir / "org_identity.json", org_payload)

    describe_result = _run(["sf", "sobject", "describe", "--sobject", "Account", "--target-org", org, "--json"])
    describe_payload = _require_command_success(describe_result, "fresh Account describe")
    _write_json(live_dir / "fresh_account_describe.json", describe_payload)
    schema = validate_salesforce_describe(
        describe_payload,
        describe_source=f"sf sobject describe --target-org {org}",
        intended_rows=desired_rows,
    )
    _write_json(live_dir / "fresh_schema_validation.json", asdict(schema))
    if not schema.valid:
        raise RuntimeError(f"Fresh Salesforce schema validation failed: {asdict(schema)}")

    desired_ids = sorted(desired_map)
    initial = query_exact(
        desired_ids,
        org=org,
        query_label="initial_exact_candidate_preflight",
        query_log=query_log,
    )
    _write_json(live_dir / "initial_exact_candidate_state.json", _backup_rows(initial, desired_ids))
    initial_source_conflicts = source_conflicts(initial, snapshot_by_id)
    conflict_ids = {row["Id"] for row in initial_source_conflicts}
    write_csv(
        live_dir / "source_conflict_queue.csv",
        initial_source_conflicts,
        ("Id", "field", "snapshot_value", "live_value"),
    )
    source_stable = [desired_map[account_id] for account_id in desired_ids if account_id not in conflict_ids]
    changed = changed_only_diff(source_stable, initial)
    changed_ids = [row["Id"] for row in changed]
    write_csv(live_dir / "full_changed_only_payload.csv", changed, CANARY_FIELDS)
    _write_json(live_dir / "exact_immutable_prewrite_backup.json", _backup_rows(initial, changed_ids))

    eligible_ids = list(changed_ids)
    canary_ids: list[str] = []
    canary_current: dict[str, dict[str, Any]] = {}
    canary_conflict_rows: list[dict[str, str]] = []
    for _attempt in range(3):
        canary_ids = select_stratified_canary(audit_by_id, eligible_ids, run_id=run_id)
        canary_current = query_exact(
            canary_ids,
            org=org,
            query_label="canary_immediate_prewrite",
            query_log=query_log,
        )
        stale = _modstamp_conflict_ids(initial, canary_current)
        source_rows = source_conflicts(canary_current, snapshot_by_id)
        source_ids = {row["Id"] for row in source_rows}
        bad = stale | source_ids
        if not bad:
            break
        for account_id in sorted(stale):
            canary_conflict_rows.append(
                {
                    "Id": account_id,
                    "phase": "canary",
                    "reason": "SystemModstamp_changed_after_initial_preflight",
                }
            )
        for account_id in sorted(source_ids):
            canary_conflict_rows.append({"Id": account_id, "phase": "canary", "reason": "source_field_changed"})
        eligible_ids = [account_id for account_id in eligible_ids if account_id not in bad]
    else:
        raise RuntimeError("Canary could not reach 50 conflict-free rows in three exact-query attempts")

    canary_payload = [desired_map[account_id] for account_id in canary_ids]
    canary_audit = [audit_by_id[account_id] for account_id in canary_ids]
    write_csv(live_dir / "canary_50_payload.csv", canary_payload, CANARY_FIELDS)
    write_csv(live_dir / "canary_50_stratified.csv", canary_audit, tuple(audit_rows[0].keys()))
    _write_json(live_dir / "canary_exact_prewrite_backup.json", _backup_rows(canary_current, canary_ids))

    canary_bulk = run_bulk_update(live_dir / "canary_50_payload.csv", org=org, phase="canary", live_dir=live_dir)
    canary_readback = query_exact(
        canary_ids,
        org=org,
        query_label="canary_exact_readback",
        query_log=query_log,
    )
    _write_json(live_dir / "canary_exact_readback.json", _readback_json(canary_readback))
    canary_verification = verify_normalized_readback(canary_payload, canary_readback)
    canary_zero_failures = (
        canary_bulk["command_ok"]
        and canary_bulk["results_ok"]
        and canary_bulk["job_id"] != ""
        and canary_bulk["failure_count"] in {None, 0}
        and canary_verification["passed"] is True
    )
    canary_verification["bulk"] = {
        key: canary_bulk[key]
        for key in ("job_id", "command_ok", "results_ok", "failure_count")
    }
    canary_verification["zero_operational_failures"] = canary_zero_failures
    _write_json(live_dir / "canary_verification.json", canary_verification)
    canary_ledger = _ledger_rows(
        canary_payload,
        canary_readback,
        phase="canary",
        job_id=canary_bulk["job_id"],
        run_id=run_id,
    )

    remaining_payload: list[dict[str, str]] = []
    remaining_current: dict[str, dict[str, Any]] = {}
    full_bulk: dict[str, Any] | None = None
    full_verification: dict[str, Any] = {
        "passed": False,
        "not_executed_reason": "canary operational failure",
    }
    full_ledger: list[dict[str, str]] = []
    runtime_conflicts = list(canary_conflict_rows)

    if canary_zero_failures:
        remaining_ids = [account_id for account_id in eligible_ids if account_id not in set(canary_ids)]
        remaining_current = query_exact(
            remaining_ids,
            org=org,
            query_label="remaining_immediate_prewrite",
            query_log=query_log,
        )
        stale_remaining = _modstamp_conflict_ids(initial, remaining_current)
        source_remaining_rows = source_conflicts(remaining_current, snapshot_by_id)
        source_remaining_ids = {row["Id"] for row in source_remaining_rows}
        for account_id in sorted(stale_remaining):
            runtime_conflicts.append(
                {
                    "Id": account_id,
                    "phase": "remaining",
                    "reason": "SystemModstamp_changed_after_initial_preflight",
                }
            )
        for account_id in sorted(source_remaining_ids):
            runtime_conflicts.append({"Id": account_id, "phase": "remaining", "reason": "source_field_changed"})
        stable_remaining_ids = [
            account_id
            for account_id in remaining_ids
            if account_id not in stale_remaining and account_id not in source_remaining_ids
        ]
        stable_remaining_desired = [desired_map[account_id] for account_id in stable_remaining_ids]
        remaining_payload = changed_only_diff(stable_remaining_desired, remaining_current)
        actual_remaining_ids = [row["Id"] for row in remaining_payload]
        _write_json(
            live_dir / "remaining_exact_prewrite_backup.json",
            _backup_rows(remaining_current, actual_remaining_ids),
        )
        write_csv(live_dir / "remaining_changed_only_payload.csv", remaining_payload, CANARY_FIELDS)
        executed_payload = canary_payload + remaining_payload
        write_csv(live_dir / "executed_changed_only_payload.csv", executed_payload, CANARY_FIELDS)
        if remaining_payload:
            full_bulk = run_bulk_update(
                live_dir / "remaining_changed_only_payload.csv",
                org=org,
                phase="remaining",
                live_dir=live_dir,
            )
        else:
            full_bulk = {
                "job_id": "",
                "command_ok": True,
                "results_ok": True,
                "failure_count": 0,
            }
        final_ids = [row["Id"] for row in executed_payload]
        final_readback = query_exact(
            final_ids,
            org=org,
            query_label="full_exact_readback",
            query_log=query_log,
        )
        _write_json(live_dir / "full_exact_readback.json", _readback_json(final_readback))
        full_verification = verify_normalized_readback(executed_payload, final_readback)
        full_verification["bulk"] = {
            key: full_bulk[key]
            for key in ("job_id", "command_ok", "results_ok", "failure_count")
        }
        full_verification["passed"] = bool(
            full_verification["passed"]
            and full_bulk["command_ok"]
            and full_bulk["results_ok"]
            and (not remaining_payload or full_bulk["job_id"] != "")
            and full_bulk["failure_count"] in {None, 0}
        )
        _write_json(live_dir / "full_verification.json", full_verification)
        full_ledger = _ledger_rows(
            remaining_payload,
            final_readback,
            phase="remaining",
            job_id=full_bulk["job_id"],
            run_id=run_id,
        )
        final_current = final_readback
        executed_rows = executed_payload
    else:
        _write_json(live_dir / "full_verification.json", full_verification)
        final_current = canary_readback
        executed_rows = canary_payload

    write_csv(
        live_dir / "runtime_conflict_queue.csv",
        runtime_conflicts,
        ("Id", "phase", "reason"),
    )
    ledger = canary_ledger + full_ledger
    write_csv(
        live_dir / "success_id_ledger.csv",
        ledger,
        ("Phase", "JobId", "Id", "Success", "Error", "CandidateRunId", "PostWriteSystemModstamp"),
    )

    initial_backup = initial
    rollback_ids = [
        row["Id"]
        for row in ledger
        if str(final_current.get(row["Id"], {}).get("AI_Prospect_Value_Run_Id__c") or "") == run_id
    ]
    post_modstamps = {
        account_id: str(final_current[account_id].get("SystemModstamp") or "") for account_id in rollback_ids
    }
    rollback = rollback_rows(
        initial_backup,
        rollback_ids,
        expected_failed_run_id=run_id,
        successful_systemmodstamp_by_id=post_modstamps,
        current_by_id=final_current,
    )
    write_csv(live_dir / "rollback_successful_ids_only.csv", rollback, CANARY_FIELDS)
    rollback_guard = {
        "target_org": org,
        "expected_failed_run_id": run_id,
        "success_ids": len(rollback_ids),
        "compare_and_swap_fields": ["AI_Prospect_Value_Run_Id__c", "SystemModstamp"],
        "command": (
            f"sf data update bulk --sobject Account --file rollback_successful_ids_only.csv "
            f"--line-ending LF --wait 10 --target-org {org}"
        ),
        "warning": "Execute only after re-querying these exact IDs and re-validating both compare-and-swap fields.",
    }
    _write_json(live_dir / "rollback_guard_and_command.json", rollback_guard)
    _write_json(live_dir / "exact_query_log.json", query_log)

    total_success = sum(row["Success"] == "true" for row in ledger)
    total_failed = len(ledger) - total_success
    decision = "GO" if canary_zero_failures and full_verification.get("passed") is True and total_failed == 0 else "NO-GO"
    summary = {
        "decision": decision,
        "target_org": org,
        "run_id": run_id,
        "model_version": MODEL_VERSION,
        "requested_population": len(desired_rows),
        "source_conflict_ids_excluded": len(conflict_ids),
        "initial_changed_only_population": len(changed),
        "runtime_conflict_ids_excluded": len({row["Id"] for row in runtime_conflicts}),
        "canary_rows": len(canary_payload),
        "canary_job_id": canary_bulk["job_id"],
        "canary_passed": canary_zero_failures,
        "remaining_rows": len(remaining_payload),
        "remaining_job_id": "" if full_bulk is None else full_bulk["job_id"],
        "executed_rows": len(executed_rows),
        "verified_success_rows": total_success,
        "failed_rows": total_failed,
        "rollback_rows": len(rollback),
        "website_writes": 0,
        "clears": 0,
        "completed_at": _iso_z(_utc_now()),
    }
    _write_json(live_dir / "publication_summary.json", summary)
    manifest = _manifest(live_dir)
    manifest.update({"run_id": run_id, "completed_at": summary["completed_at"]})
    _write_json(live_dir / "live_publication_manifest.json", manifest)
    return {**summary, "live_dir": str(live_dir)}
