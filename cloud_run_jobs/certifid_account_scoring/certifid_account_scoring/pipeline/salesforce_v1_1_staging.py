"""Read-only Salesforce staging package for Sales Prioritization V1.1.

This module performs live reads only.  It validates the V1.1 candidate against
the current Account describe, captures exact current values, creates a
changed-ID-only payload and stratified canary, and prepares readback/rollback
assets.  It has no Bulk API update call.
"""

from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .publication import (
    CANARY_FIELDS,
    NULL_SENTINEL,
    NUMERIC_FIELDS,
    PROHIBITED_FIELDS,
    PublicationValidationError,
    normalized_salesforce_value,
    validate_salesforce_describe,
    write_csv,
)
from .sales_prioritization_release import _number, _text, parse_arr_range, read_csv, sha256_file
from .salesforce_live_release import (
    BACKUP_JSON_FIELDS,
    _backup_rows,
    _iso_z,
    _require_command_success,
    _run,
    _utc_now,
    _write_json,
    query_exact,
    source_conflicts,
)


def validate_v1_1_payload(row: Mapping[str, Any]) -> None:
    unexpected = set(row) - set(CANARY_FIELDS)
    missing = set(CANARY_FIELDS) - set(row)
    prohibited = set(row) & PROHIBITED_FIELDS
    if unexpected or missing or prohibited:
        raise PublicationValidationError(
            f"Unsafe V1.1 payload schema: missing={sorted(missing)}, "
            f"unexpected={sorted(unexpected)}, prohibited={sorted(prohibited)}"
        )
    if not _text(row.get("Id")):
        raise PublicationValidationError("V1.1 payload has blank Id")
    for field in NUMERIC_FIELDS:
        value = _number(row.get(field))
        minimum = Decimal("0") if field == "AI_Prospect_Value_MCV_Low__c" else Decimal("0.0000001")
        if value is None or value < minimum:
            raise PublicationValidationError(f"V1.1 payload has invalid numeric {field}")
    low = _number(row.get("AI_Prospect_Value_MCV_Low__c"))
    point = _number(row.get("AI_Prospect_Value_MCV_Point__c"))
    high = _number(row.get("AI_Prospect_Value_MCV_High__c"))
    if low is None or point is None or high is None or not (low <= point <= high):
        raise PublicationValidationError("V1.1 MCV point is outside its interval")
    arr = _number(row.get("AI_Prospect_Value_ARR_Point__c"))
    arr_text = _text(row.get("AI_Prospect_Value_ARR_Range__c"))
    parsed_arr = parse_arr_range(arr_text)
    if arr is None or not arr_text:
        raise PublicationValidationError("V1.1 ARR point/range is invalid")
    if parsed_arr is not None and not (parsed_arr[0] <= arr <= parsed_arr[1]):
        raise PublicationValidationError("V1.1 ARR point is outside its range")
    if parsed_arr is None and not arr_text.endswith("+"):
        raise PublicationValidationError("V1.1 ARR range is not an existing ladder value")

    required = set(CANARY_FIELDS[1:]) - {"AI_Prospect_Value_Canonical_URL__c"}
    blank = sorted(field for field in required if not _text(row.get(field)))
    if blank:
        raise PublicationValidationError(f"V1.1 payload has blank required fields: {blank}")
    if not _text(row.get("AI_Prospect_Value_Canonical_URL__c")) and _text(
        row.get("AI_Prospect_Value_URL_Status__c")
    ) not in {"no_url", "not_run", "error", "dns_error", "timeout", "ssl_error", "server_error", "blocked"}:
        raise PublicationValidationError("Blank canonical URL requires a non-usable URL status")


def v1_1_changed_only_diff(
    desired_rows: Sequence[Mapping[str, Any]],
    current_by_id: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, str]]:
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for desired in desired_rows:
        validate_v1_1_payload(desired)
        account_id = _text(desired.get("Id"))
        if account_id in seen:
            raise PublicationValidationError(f"Duplicate V1.1 desired Id {account_id}")
        seen.add(account_id)
        current = current_by_id.get(account_id)
        if current is None:
            raise PublicationValidationError(f"Missing live current state for {account_id}")
        missing = set(CANARY_FIELDS) - set(current)
        if missing:
            raise PublicationValidationError(f"Incomplete live current state for {account_id}: {sorted(missing)}")
        if any(
            normalized_salesforce_value(field, desired.get(field))
            != normalized_salesforce_value(field, current.get(field))
            for field in CANARY_FIELDS[1:]
        ):
            output.append({field: _text(desired.get(field)) for field in CANARY_FIELDS})
    return output


def _stable_hash(run_id: str, account_id: str) -> str:
    import hashlib

    return hashlib.sha256(f"{run_id}:{account_id}".encode("utf-8")).hexdigest()


def select_v1_1_canary(
    audit_by_id: Mapping[str, Mapping[str, str]],
    candidate_ids: Iterable[str],
    *,
    run_id: str,
    size: int = 50,
) -> list[str]:
    if size != 50:
        raise ValueError("V1.1 guardrailed canary is fixed at 50 rows (top 20 + stratified 30)")
    candidate_set = set(candidate_ids)
    population_top20 = sorted(
        audit_by_id,
        key=lambda account_id: (
            -float(audit_by_id[account_id]["AI_Prospect_Value_ARR_Point__c"]),
            -float(audit_by_id[account_id]["AI_Prospect_Value_MCV_Point__c"]),
            _text(audit_by_id[account_id].get("Name")),
            account_id,
        ),
    )[:20]
    unavailable = sorted(set(population_top20) - candidate_set)
    if unavailable:
        raise RuntimeError(
            "The exact post-guardrail top 20 must all be source-stable changed IDs; "
            f"unavailable={unavailable}"
        )

    groups: dict[tuple[str, str, str, str], list[str]] = defaultdict(list)
    for account_id in sorted(candidate_set - set(population_top20)):
        row = audit_by_id[account_id]
        key = (
            row["score_source_tier"],
            row["lane"],
            row["confidence"],
            row["mcv_band"],
        )
        groups[key].append(account_id)
    for ids in groups.values():
        ids.sort(key=lambda account_id: _stable_hash(run_id, account_id))
    selected_keys = set(groups)
    stratified_size = 30
    if len(selected_keys) > stratified_size:
        dimension_counts = [defaultdict(int) for _ in range(4)]
        for key in selected_keys:
            for index, value in enumerate(key):
                dimension_counts[index][value] += 1
        removable = sorted(
            (
                key
                for key in selected_keys
                if all(dimension_counts[index][value] > 1 for index, value in enumerate(key))
            ),
            key=lambda key: (len(groups[key]), key),
        )
        while len(selected_keys) > stratified_size and removable:
            key = removable.pop(0)
            selected_keys.remove(key)
            for index, value in enumerate(key):
                dimension_counts[index][value] -= 1
            removable = [
                candidate
                for candidate in removable
                if candidate in selected_keys
                and all(dimension_counts[index][value] > 1 for index, value in enumerate(candidate))
            ]
        if len(selected_keys) > stratified_size:
            raise RuntimeError(
                f"Cannot reduce {len(groups)} V1.1 strata to {stratified_size} without losing a dimension"
            )
    selected: list[str] = []
    offsets = {key: 0 for key in selected_keys}
    keys = sorted(selected_keys)
    while len(selected) < stratified_size:
        progressed = False
        for key in keys:
            offset = offsets[key]
            if offset < len(groups[key]):
                selected.append(groups[key][offset])
                offsets[key] += 1
                progressed = True
                if len(selected) == stratified_size:
                    break
        if not progressed:
            break
    if len(selected) != stratified_size:
        raise RuntimeError(f"Could select only {len(selected)} V1.1 stratified canary rows")
    return population_top20 + selected


def _canary_strata_coverage(
    audit_by_id: Mapping[str, Mapping[str, str]],
    all_candidate_ids: Sequence[str],
    canary_ids: Sequence[str],
) -> dict[str, Any]:
    def key(account_id: str) -> tuple[str, str, str, str]:
        row = audit_by_id[account_id]
        return (
            row["score_source_tier"],
            row["lane"],
            row["confidence"],
            row["mcv_band"],
        )

    counts: dict[tuple[str, str, str, str], int] = defaultdict(int)
    for account_id in all_candidate_ids:
        counts[key(account_id)] += 1
    selected = {key(account_id) for account_id in canary_ids}
    omitted = sorted(set(counts) - selected)
    return {
        "all_nonempty_strata": len(counts),
        "represented_strata": len(selected),
        "omitted_strata": [
            {
                "score_source_tier": item[0],
                "lane": item[1],
                "confidence": item[2],
                "mcv_band": item[3],
                "population": counts[item],
                "dimension_coverage_preserved_elsewhere": True,
            }
            for item in omitted
        ],
        "all_source_tiers_represented": {audit_by_id[i]["score_source_tier"] for i in canary_ids}
        == {audit_by_id[i]["score_source_tier"] for i in all_candidate_ids},
        "all_lanes_represented": {audit_by_id[i]["lane"] for i in canary_ids}
        == {audit_by_id[i]["lane"] for i in all_candidate_ids},
        "all_confidences_represented": {audit_by_id[i]["confidence"] for i in canary_ids}
        == {audit_by_id[i]["confidence"] for i in all_candidate_ids},
        "all_mcv_bands_represented": {audit_by_id[i]["mcv_band"] for i in canary_ids}
        == {audit_by_id[i]["mcv_band"] for i in all_candidate_ids},
    }


def _rollback_values(
    current_by_id: Mapping[str, Mapping[str, Any]], account_ids: Sequence[str]
) -> list[dict[str, str]]:
    return [
        {
            field: account_id
            if field == "Id"
            else (NULL_SENTINEL if current_by_id[account_id].get(field) is None else str(current_by_id[account_id].get(field)))
            for field in CANARY_FIELDS
        }
        for account_id in account_ids
    ]


def stage_v1_1_release(
    *,
    candidate_payload_path: Path,
    scored_audit_path: Path,
    population_summary_path: Path,
    accounts_snapshot_path: Path,
    output_dir: Path,
    org: str,
) -> dict[str, Any]:
    if output_dir.exists():
        raise FileExistsError(f"V1.1 staging output is immutable and already exists: {output_dir}")
    output_dir.mkdir(parents=True)

    population_summary = json.loads(population_summary_path.read_text(encoding="utf-8-sig"))
    if population_summary.get("coverage_gate") != "PASS":
        raise RuntimeError("V1.1 coverage gate is not PASS; Salesforce staging is prohibited")
    if int(population_summary.get("scored_population", 0)) < 10_000:
        raise RuntimeError("V1.1 scored population is below the 10,000 hard stop")

    payload_rows = read_csv(candidate_payload_path)
    audit_rows = read_csv(scored_audit_path)
    snapshot_rows = read_csv(accounts_snapshot_path)
    if len(payload_rows) != int(population_summary["scored_population"]):
        raise RuntimeError("V1.1 candidate payload count does not match population audit")
    for row in payload_rows:
        validate_v1_1_payload(row)
    payload_by_id = {row["Id"]: row for row in payload_rows}
    audit_by_id = {row["Id"]: row for row in audit_rows}
    snapshot_by_id = {row["Id"]: row for row in snapshot_rows}
    if set(payload_by_id) != set(audit_by_id):
        raise RuntimeError("V1.1 payload/audit exact-ID reconciliation failed")
    run_ids = {row["AI_Prospect_Value_Run_Id__c"] for row in payload_rows}
    if len(run_ids) != 1:
        raise RuntimeError(f"V1.1 staging requires one immutable run Id, found {run_ids}")
    run_id = next(iter(run_ids))

    org_result = _run(["sf", "org", "display", "--target-org", org, "--json"])
    org_payload = _require_command_success(org_result, "V1.1 Salesforce identity")
    _write_json(output_dir / "org_identity.json", org_payload)

    describe_result = _run(["sf", "sobject", "describe", "--sobject", "Account", "--target-org", org, "--json"])
    describe_payload = _require_command_success(describe_result, "V1.1 fresh Account describe")
    _write_json(output_dir / "fresh_account_describe.json", describe_payload)
    schema = validate_salesforce_describe(
        describe_payload,
        describe_source=f"sf sobject describe --target-org {org}",
        intended_rows=payload_rows,
    )
    _write_json(output_dir / "fresh_schema_validation.json", schema.__dict__)
    if not schema.valid:
        raise RuntimeError(f"V1.1 live schema validation failed: {schema}")

    query_log: list[dict[str, Any]] = []
    candidate_ids = sorted(payload_by_id)
    current = query_exact(
        candidate_ids,
        org=org,
        query_label="v1_1_exact_candidate_staging_read",
        query_log=query_log,
        allow_missing=True,
    )
    missing_live_ids = sorted(set(candidate_ids) - set(current))
    source_conflict_rows = source_conflicts(current, snapshot_by_id)
    source_conflict_rows.extend(
        {
            "Id": account_id,
            "field": "<live_account>",
            "snapshot_value": "present",
            "live_value": "missing_or_deleted",
        }
        for account_id in missing_live_ids
    )
    source_conflict_ids = {row["Id"] for row in source_conflict_rows}
    write_csv(
        output_dir / "source_conflict_queue.csv",
        source_conflict_rows,
        ("Id", "field", "snapshot_value", "live_value"),
    )
    source_stable_payload = [row for row in payload_rows if row["Id"] not in source_conflict_ids]
    changed_rows = v1_1_changed_only_diff(source_stable_payload, current)
    changed_ids = [row["Id"] for row in changed_rows]
    if len(changed_rows) < 10_000:
        raise RuntimeError(
            f"V1.1 staging dropped below 10,000 after live source conflicts: {len(changed_rows):,}"
        )

    write_csv(output_dir / "full_changed_id_only_payload.csv", changed_rows, CANARY_FIELDS)
    _write_json(output_dir / "exact_current_state_backup.json", _backup_rows(current, changed_ids))
    rollback_values = _rollback_values(current, changed_ids)
    write_csv(
        output_dir / "rollback_values_all_changed_DO_NOT_EXECUTE.csv",
        rollback_values,
        CANARY_FIELDS,
    )

    canary_ids = select_v1_1_canary(audit_by_id, changed_ids, run_id=run_id)
    canary_payload = [payload_by_id[account_id] for account_id in canary_ids]
    canary_audit = [audit_by_id[account_id] for account_id in canary_ids]
    write_csv(output_dir / "canary_50_payload_DO_NOT_EXECUTE.csv", canary_payload, CANARY_FIELDS)
    write_csv(
        output_dir / "canary_50_stratified_audit.csv",
        canary_audit,
        tuple(audit_rows[0].keys()),
    )
    canary_roles = [
        {
            "canary_position": str(index),
            "canary_role": "top_20_by_arr" if index <= 20 else "stratified_30",
            "Id": account_id,
            "Name": audit_by_id[account_id]["Name"],
            "score_source_tier": audit_by_id[account_id]["score_source_tier"],
            "lane": audit_by_id[account_id]["lane"],
            "confidence": audit_by_id[account_id]["confidence"],
            "mcv_band": audit_by_id[account_id]["mcv_band"],
            "AI_Prospect_Value_MCV_Point__c": audit_by_id[account_id]["AI_Prospect_Value_MCV_Point__c"],
            "AI_Prospect_Value_ARR_Point__c": audit_by_id[account_id]["AI_Prospect_Value_ARR_Point__c"],
        }
        for index, account_id in enumerate(canary_ids, 1)
    ]
    write_csv(
        output_dir / "canary_50_selection_roles.csv",
        canary_roles,
        tuple(canary_roles[0].keys()),
    )
    write_csv(
        output_dir / "top_20_after_guardrails.csv",
        canary_roles[:20],
        tuple(canary_roles[0].keys()),
    )
    _write_json(
        output_dir / "canary_strata_coverage.json",
        _canary_strata_coverage(audit_by_id, changed_ids, canary_ids),
    )
    _write_json(output_dir / "canary_exact_current_backup.json", _backup_rows(current, canary_ids))

    write_csv(
        output_dir / "success_id_ledger_template.csv",
        [],
        ("Phase", "JobId", "Id", "Success", "Error", "CandidateRunId", "PostWriteSystemModstamp"),
    )
    readback_plan = {
        "status": "NOT_EXECUTED",
        "reason": "Revised top-20/canary review and explicit write authorization are pending",
        "target_org": org,
        "run_id": run_id,
        "fields": list(CANARY_FIELDS),
        "canary_exact_ids": canary_ids,
        "verification": "Use verify_normalized_readback after exact-ID query; require 0 missing, unexpected, or mismatched values.",
        "remaining_gate": "Do not submit remaining payload unless the 50-row canary has zero Bulk API failures and exact normalized readback passes.",
    }
    _write_json(output_dir / "readback_verification_plan.json", readback_plan)
    rollback_package = {
        "status": "STAGED_NOT_EXECUTABLE",
        "target_org": org,
        "failed_run_id": run_id,
        "backup": "exact_current_state_backup.json",
        "all_changed_restore_values": "rollback_values_all_changed_DO_NOT_EXECUTE.csv",
        "success_ledger": "success_id_ledger_template.csv",
        "required_filter": "Generate executable rollback for successful write IDs only.",
        "compare_and_swap_guards": ["AI_Prospect_Value_Run_Id__c", "SystemModstamp"],
        "warning": "Re-query exact successful IDs after any write. Never run the all-changed restore file directly.",
    }
    _write_json(output_dir / "rollback_package.json", rollback_package)
    _write_json(output_dir / "exact_query_log.json", query_log)

    staged_summary = {
        "no_write": True,
        "salesforce_writes": 0,
        "target_org": org,
        "run_id": run_id,
        "population_audit_scored": len(payload_rows),
        "source_conflict_ids_excluded": len(source_conflict_ids),
        "changed_id_only_population": len(changed_rows),
        "canary_rows": len(canary_payload),
        "canary_top_rank_rows": 20,
        "canary_stratified_rows": 30,
        "canary_status": "STAGED_NOT_EXECUTED",
        "readback_status": "NOT_EXECUTED",
        "rollback_status": "STAGED_REQUIRES_SUCCESS_LEDGER_AND_COMPARE_SWAP",
        "publication_allowed": False,
        "publication_hold_reason": "Revised top-20 and 50-row canary require review",
        "staged_at": _iso_z(_utc_now()),
    }
    _write_json(output_dir / "staging_summary.json", staged_summary)

    artifact_paths = [path for path in output_dir.rglob("*") if path.is_file()]
    manifest = {
        "run_id": run_id,
        "immutable": True,
        "no_write": True,
        "artifacts": [
            {
                "path": str(path.relative_to(output_dir)).replace("\\", "/"),
                "bytes": path.stat().st_size,
                "sha256": sha256_file(path),
            }
            for path in sorted(artifact_paths)
        ],
    }
    _write_json(output_dir / "staging_manifest.json", manifest)
    return {**staged_summary, "output_dir": str(output_dir)}
