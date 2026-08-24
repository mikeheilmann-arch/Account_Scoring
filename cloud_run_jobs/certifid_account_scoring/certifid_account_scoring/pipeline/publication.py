"""No-write Salesforce desired-state and rollback planning.

The functions here only create/validate artifacts.  They contain no Salesforce
client and cannot execute a write.  V1 is accepted-only and no-clear: review,
suppression, winback, and held decisions never enter a write payload.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from .config import MCV_MODEL_VERSION, PUBLICATION_VERSION, SOURCE_VERSION
from .contracts import AccountDecision, Confidence


NUMERIC_FIELDS = (
    "AI_Prospect_Value_MCV_Point__c",
    "AI_Prospect_Value_MCV_Low__c",
    "AI_Prospect_Value_MCV_High__c",
    "AI_Prospect_Value_ARR_Point__c",
)

CANARY_FIELDS = (
    "Id",
    *NUMERIC_FIELDS,
    "AI_Prospect_Value_ARR_Range__c",
    "AI_Prospect_Value_Confidence__c",
    "AI_Prospect_Value_ICP__c",
    "AI_Prospect_Value_Action__c",
    "AI_Prospect_Value_URL_Status__c",
    "AI_Prospect_Value_Canonical_URL__c",
    "AI_Prospect_Value_Evidence__c",
    "AI_Prospect_Value_Components__c",
    "AI_Prospect_Value_Model_Version__c",
    "AI_Prospect_Value_Run_Id__c",
    "AI_Prospect_Value_Source__c",
    "AI_Prospect_Value_Updated_At__c",
)

PROHIBITED_FIELDS = frozenset(
    {
        "Website",
        "AI_Prospect_Value_Rank__c",
        "AI_Prospect_Value_Score__c",
        "AI_Prospect_Fit_Score__c",
        "AI_Prospect_Timing_Score__c",
        "AI_Prospect_Data_Confidence_Score__c",
    }
)

SOURCE_CONFLICT_FIELDS = (
    "SystemModstamp",
    "Website",
    "Active_Customer__c",
    "Account_Status__c",
    "ParentId",
    "Company_Type__c",
)
BACKUP_FIELDS = (*CANARY_FIELDS, *SOURCE_CONFLICT_FIELDS)
NULL_SENTINEL = "#N/A"

EXPECTED_TYPES = {
    "Id": {"id"},
    "AI_Prospect_Value_MCV_Point__c": {"double", "int"},
    "AI_Prospect_Value_MCV_Low__c": {"double", "int"},
    "AI_Prospect_Value_MCV_High__c": {"double", "int"},
    "AI_Prospect_Value_ARR_Point__c": {"currency", "double"},
    "AI_Prospect_Value_ARR_Range__c": {"string"},
    "AI_Prospect_Value_Confidence__c": {"picklist"},
    "AI_Prospect_Value_ICP__c": {"picklist"},
    "AI_Prospect_Value_Action__c": {"picklist"},
    "AI_Prospect_Value_URL_Status__c": {"picklist"},
    "AI_Prospect_Value_Canonical_URL__c": {"url", "string"},
    "AI_Prospect_Value_Evidence__c": {"textarea", "string"},
    "AI_Prospect_Value_Components__c": {"textarea", "string"},
    "AI_Prospect_Value_Model_Version__c": {"string"},
    "AI_Prospect_Value_Run_Id__c": {"string"},
    "AI_Prospect_Value_Source__c": {"string"},
    "AI_Prospect_Value_Updated_At__c": {"datetime"},
}

EXPECTED_LENGTHS = {
    "AI_Prospect_Value_ARR_Range__c": 50,
    "AI_Prospect_Value_Canonical_URL__c": 255,
    "AI_Prospect_Value_Evidence__c": 32768,
    "AI_Prospect_Value_Components__c": 32768,
    "AI_Prospect_Value_Model_Version__c": 80,
    "AI_Prospect_Value_Run_Id__c": 80,
    "AI_Prospect_Value_Source__c": 80,
}

EXPECTED_NUMERIC = {
    "AI_Prospect_Value_MCV_Point__c": (8, 0),
    "AI_Prospect_Value_MCV_Low__c": (8, 0),
    "AI_Prospect_Value_MCV_High__c": (8, 0),
    "AI_Prospect_Value_ARR_Point__c": (16, 0),
}


class PublicationValidationError(RuntimeError):
    """Raised if a proposed artifact could clear or mutate unsafe fields."""


@dataclass(frozen=True)
class SchemaValidation:
    valid: bool
    described_fields: int
    missing_fields: tuple[str, ...]
    non_updateable_fields: tuple[str, ...]
    type_mismatches: tuple[str, ...]
    invalid_picklist_values: tuple[str, ...]
    constraint_mismatches: tuple[str, ...]
    invalid_intended_values: tuple[str, ...]
    object_name: str
    describe_source: str
    publication_version: str = PUBLICATION_VERSION


def format_number(value: float | int | str | None) -> str:
    """Canonical Salesforce CSV number without a destructive blank sentinel."""

    if value is None or str(value).strip() == "":
        raise PublicationValidationError("Accepted numeric fields may not be null")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise PublicationValidationError(f"Invalid numeric value {value!r}") from exc
    if not number.is_finite():
        raise PublicationValidationError(f"Non-finite numeric value {value!r}")
    normalized = format(number.normalize(), "f")
    return normalized.rstrip("0").rstrip(".") if "." in normalized else normalized


def format_integer(value: float | int | str | None) -> str:
    number = Decimal(format_number(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return format(number, "f")


def format_arr_range(low: float | None, high: float | None) -> str:
    if low is None or high is None:
        raise PublicationValidationError("Accepted ARR range requires P50 and P90")
    return f"${int(round(low)):,}-${int(round(high)):,}"


def decision_to_desired_state(decision: AccountDecision) -> dict[str, str] | None:
    """Convert only a release-eligible accepted decision to Salesforce schema."""

    if not decision.accepted:
        return None
    if decision.desired_operation.value != "publish_value":
        raise PublicationValidationError(
            f"Accepted decision {decision.account_id} has operation {decision.desired_operation.value}"
        )
    if decision.lifecycle == "active_customer":
        raise PublicationValidationError(f"Active customer {decision.account_id} cannot publish net-new value")
    if decision.lifecycle != "net_new":
        raise PublicationValidationError(
            f"Non-net-new lifecycle {decision.lifecycle} cannot publish for {decision.account_id}"
        )
    if decision.binding_status not in {"bound", "confirmed", "correct_website"}:
        raise PublicationValidationError(f"Unbound website cannot publish for {decision.account_id}")
    if decision.confidence is not Confidence.HIGH:
        raise PublicationValidationError(f"Only High-confidence decisions may publish: {decision.account_id}")
    if decision.lane not in {"title_escrow", "legal"}:
        raise PublicationValidationError(f"Ineligible lane {decision.lane} cannot publish for {decision.account_id}")
    if decision.surviving_account_id != decision.account_id:
        raise PublicationValidationError(f"Duplicate/child loser {decision.account_id} cannot publish standalone")
    if decision.final_mcv is None or decision.final_mcv_low is None or decision.final_mcv_high is None:
        raise PublicationValidationError(f"Accepted decision {decision.account_id} has incomplete MCV interval")
    if not decision.final_mcv_low <= decision.final_mcv <= decision.final_mcv_high:
        raise PublicationValidationError(f"Accepted decision {decision.account_id} violates its MCV interval")
    if decision.final_mcv <= 0 or decision.final_mcv_low < 0 or decision.final_mcv_high <= 0:
        raise PublicationValidationError(f"Accepted decision {decision.account_id} has nonpositive MCV")
    if decision.final_arr is None or decision.final_arr_low is None or decision.final_arr_high is None:
        raise PublicationValidationError(f"Accepted decision {decision.account_id} has incomplete ARR interval")
    if not decision.final_arr_low <= decision.final_arr <= decision.final_arr_high:
        raise PublicationValidationError(f"Accepted decision {decision.account_id} violates its ARR interval")
    if decision.final_arr <= 0 or decision.final_arr_low < 0 or decision.final_arr_high <= 0:
        raise PublicationValidationError(f"Accepted decision {decision.account_id} has nonpositive ARR")

    components = {
        "contract_version": decision.contract_version,
        "resolver_version": decision.resolver_version,
        "lane_version": decision.lane_version,
        "feature_version": decision.feature_version,
        "mcv_model_version": decision.mcv_model_version,
        "arr_model_version": decision.arr_model_version,
        "source_version": decision.source_version,
        "input_fingerprint": decision.input_fingerprint,
        "evidence_hash": decision.evidence_hash,
        "sellable_unit_id": decision.sellable_unit_id,
        "pipeline_potential_semantics": "P75 point; P50-P90 range; not booked forecast",
    }
    row = {
        "Id": decision.account_id,
        "AI_Prospect_Value_MCV_Point__c": format_integer(decision.final_mcv),
        "AI_Prospect_Value_MCV_Low__c": format_integer(decision.final_mcv_low),
        "AI_Prospect_Value_MCV_High__c": format_integer(decision.final_mcv_high),
        "AI_Prospect_Value_ARR_Point__c": format_integer(decision.final_arr),
        "AI_Prospect_Value_ARR_Range__c": format_arr_range(decision.final_arr_low, decision.final_arr_high),
        "AI_Prospect_Value_Confidence__c": decision.confidence.value,
        "AI_Prospect_Value_ICP__c": "scorable",
        "AI_Prospect_Value_Action__c": "score_now",
        "AI_Prospect_Value_URL_Status__c": decision.url_status,
        "AI_Prospect_Value_Canonical_URL__c": decision.website,
        "AI_Prospect_Value_Evidence__c": decision.evidence_summary[:32768],
        "AI_Prospect_Value_Components__c": json.dumps(components, sort_keys=True)[:32768],
        "AI_Prospect_Value_Model_Version__c": MCV_MODEL_VERSION[:80],
        "AI_Prospect_Value_Run_Id__c": decision.run_id[:80],
        "AI_Prospect_Value_Source__c": SOURCE_VERSION[:80],
        "AI_Prospect_Value_Updated_At__c": decision.evaluated_at,
    }
    validate_payload_row(row)
    return row


def validate_payload_row(row: Mapping[str, object]) -> None:
    unexpected = set(row) - set(CANARY_FIELDS)
    missing = set(CANARY_FIELDS) - set(row)
    prohibited = set(row) & PROHIBITED_FIELDS
    if unexpected or missing or prohibited:
        raise PublicationValidationError(
            f"Unsafe payload schema: missing={sorted(missing)}, unexpected={sorted(unexpected)}, "
            f"prohibited={sorted(prohibited)}"
        )
    if not str(row.get("Id", "")).strip():
        raise PublicationValidationError("Payload row has blank Id")
    for field in NUMERIC_FIELDS:
        if str(row.get(field, "")).strip() == "":
            raise PublicationValidationError(f"Accepted payload has blank numeric field {field}")
    for field in (
        "AI_Prospect_Value_ARR_Range__c",
        "AI_Prospect_Value_Confidence__c",
        "AI_Prospect_Value_ICP__c",
        "AI_Prospect_Value_Action__c",
        "AI_Prospect_Value_URL_Status__c",
        "AI_Prospect_Value_Canonical_URL__c",
        "AI_Prospect_Value_Evidence__c",
        "AI_Prospect_Value_Components__c",
        "AI_Prospect_Value_Model_Version__c",
        "AI_Prospect_Value_Run_Id__c",
        "AI_Prospect_Value_Source__c",
        "AI_Prospect_Value_Updated_At__c",
    ):
        if str(row.get(field, "")).strip() == "":
            raise PublicationValidationError(f"Accepted payload has blank required field {field}")


def normalized_salesforce_value(field: str, value: object) -> str:
    """Normalize desired/readback values for exact semantic comparison."""

    if value is None:
        return ""
    text = str(value).strip()
    if field in NUMERIC_FIELDS and text:
        try:
            return format(Decimal(text).normalize(), "f")
        except InvalidOperation:
            return text
    if field == "AI_Prospect_Value_Updated_At__c":
        return text.replace(".000+0000", "Z").replace("+00:00", "Z")
    return text.replace("\r\n", "\n")


def changed_only_diff(
    desired_rows: Iterable[Mapping[str, object]],
    current_by_id: Mapping[str, Mapping[str, object]],
) -> list[dict[str, str]]:
    """Return only Accounts with at least one changed field.

    Current values are mandatory; absence is not interpreted as null because
    that would turn an unverified all-row export into a write payload.
    """

    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for desired in desired_rows:
        validate_payload_row(desired)
        account_id = str(desired["Id"])
        if account_id in seen:
            raise PublicationValidationError(f"Duplicate desired-state Id {account_id}")
        seen.add(account_id)
        if account_id not in current_by_id:
            raise PublicationValidationError(f"Missing current Salesforce state for {account_id}")
        current = current_by_id[account_id]
        if str(current.get("Id", "")).strip() != account_id:
            raise PublicationValidationError(f"Current-state mapping/row Id mismatch for {account_id}")
        missing_current_fields = set(CANARY_FIELDS) - set(current)
        if missing_current_fields:
            raise PublicationValidationError(
                f"Incomplete current Salesforce state for {account_id}: missing {sorted(missing_current_fields)}"
            )
        if any(
            normalized_salesforce_value(field, desired[field])
            != normalized_salesforce_value(field, current.get(field))
            for field in CANARY_FIELDS
            if field != "Id"
        ):
            output.append({field: str(desired[field]) for field in CANARY_FIELDS})
    return output


def _find_fields(value: object) -> list[Mapping[str, object]]:
    if isinstance(value, Mapping):
        fields = value.get("fields")
        if isinstance(fields, list) and fields:
            return [field for field in fields if isinstance(field, Mapping)]
        for nested in value.values():
            found = _find_fields(nested)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_fields(nested)
            if found:
                return found
    return []


def _find_object_name(value: object) -> str:
    if isinstance(value, Mapping):
        fields = value.get("fields")
        if isinstance(fields, list) and fields:
            return str(value.get("name") or "")
        for nested in value.values():
            found = _find_object_name(nested)
            if found:
                return found
    return ""


def validate_salesforce_describe(
    describe: Mapping[str, object],
    *,
    describe_source: str,
    intended_rows: Sequence[Mapping[str, object]] = (),
) -> SchemaValidation:
    fields = {str(field.get("name")): field for field in _find_fields(describe) if field.get("name")}
    missing: list[str] = []
    non_updateable: list[str] = []
    mismatches: list[str] = []
    invalid_picklists: list[str] = []
    constraint_mismatches: list[str] = []
    invalid_intended: list[str] = []
    object_name = _find_object_name(describe)
    if object_name != "Account":
        constraint_mismatches.append(f"describe object must be Account, got {object_name or '<blank>'}")
    intended_values = {
        field: {str(row.get(field, "")) for row in intended_rows if str(row.get(field, ""))}
        for field in CANARY_FIELDS
    }
    for name in CANARY_FIELDS:
        field = fields.get(name)
        if field is None:
            missing.append(name)
            continue
        if name != "Id" and field.get("updateable") is not True:
            non_updateable.append(name)
        if name != "Id" and field.get("nillable") is not True:
            constraint_mismatches.append(f"{name}: expected nillable=true")
        actual_type = str(field.get("type", "")).lower()
        if actual_type not in EXPECTED_TYPES[name]:
            mismatches.append(f"{name}: expected {sorted(EXPECTED_TYPES[name])}, got {actual_type or '<blank>'}")
        if actual_type == "picklist" and field.get("restrictedPicklist"):
            allowed = {
                str(item.get("value"))
                for item in field.get("picklistValues", [])  # type: ignore[union-attr]
                if isinstance(item, Mapping) and item.get("active", True)
            }
            for value in intended_values[name] - allowed:
                invalid_picklists.append(f"{name}={value}")
        expected_length = EXPECTED_LENGTHS.get(name)
        if expected_length is not None and int(field.get("length") or 0) != expected_length:
            constraint_mismatches.append(
                f"{name}: expected length {expected_length}, got {field.get('length')}"
            )
        expected_numeric = EXPECTED_NUMERIC.get(name)
        if expected_numeric is not None:
            actual_numeric = (int(field.get("precision") or 0), int(field.get("scale") or 0))
            if actual_numeric != expected_numeric:
                constraint_mismatches.append(
                    f"{name}: expected precision/scale {expected_numeric}, got {actual_numeric}"
                )
    for row in intended_rows:
        for name, limit in EXPECTED_LENGTHS.items():
            if len(str(row.get(name, ""))) > limit:
                invalid_intended.append(f"{row.get('Id')}:{name}:length>{limit}")
        for name, (precision, scale) in EXPECTED_NUMERIC.items():
            value = str(row.get(name, "")).strip()
            if not value:
                continue
            try:
                number = Decimal(value)
            except InvalidOperation:
                invalid_intended.append(f"{row.get('Id')}:{name}:invalid_number")
                continue
            sign, digits, exponent = number.as_tuple()
            decimals = max(0, -exponent)
            integer_digits = max(1, len(digits) - decimals)
            if decimals > scale or integer_digits + decimals > precision:
                invalid_intended.append(
                    f"{row.get('Id')}:{name}:exceeds_precision_{precision}_scale_{scale}"
                )
    return SchemaValidation(
        valid=not (
            missing
            or non_updateable
            or mismatches
            or invalid_picklists
            or constraint_mismatches
            or invalid_intended
        ),
        described_fields=len(fields),
        missing_fields=tuple(missing),
        non_updateable_fields=tuple(non_updateable),
        type_mismatches=tuple(mismatches),
        invalid_picklist_values=tuple(invalid_picklists),
        constraint_mismatches=tuple(constraint_mismatches),
        invalid_intended_values=tuple(invalid_intended),
        object_name=object_name,
        describe_source=describe_source,
    )


def assert_no_stale_conflicts(
    candidate_systemmodstamp: Mapping[str, str], current_systemmodstamp: Mapping[str, str]
) -> None:
    stale = sorted(
        account_id
        for account_id, expected in candidate_systemmodstamp.items()
        if normalized_salesforce_value("SystemModstamp", expected)
        != normalized_salesforce_value("SystemModstamp", current_systemmodstamp.get(account_id))
    )
    if stale:
        raise PublicationValidationError(f"SystemModstamp conflict for {len(stale)} Account(s): {stale[:10]}")


def exact_id_soql(account_ids: Sequence[str], fields: Sequence[str] = BACKUP_FIELDS) -> str:
    if not account_ids:
        raise PublicationValidationError("Exact-ID query requires at least one Account Id")
    if len(set(account_ids)) != len(account_ids):
        raise PublicationValidationError("Exact-ID query received duplicate Account Ids")
    quoted = ",".join(f"'{account_id}'" for account_id in account_ids)
    return f"SELECT {', '.join(fields)} FROM Account WHERE Id IN ({quoted}) ORDER BY Id"


def rollback_rows(
    backup_by_id: Mapping[str, Mapping[str, object]],
    success_ids: Iterable[str],
    *,
    expected_failed_run_id: str,
    successful_systemmodstamp_by_id: Mapping[str, str],
    current_by_id: Mapping[str, Mapping[str, object]],
) -> list[dict[str, str]]:
    """Build rollback values for successful IDs only, preserving true nulls.

    The explicit ``#N/A`` sentinel is the separately rehearsed Bulk API null
    semantic.  The success ledger is the authorization boundary for scope.
    """

    ids = list(success_ids)
    if len(ids) != len(set(ids)):
        raise PublicationValidationError("Success-ID ledger contains duplicates")
    if not expected_failed_run_id.strip():
        raise PublicationValidationError("Rollback requires the failed immutable Run Id")
    rows: list[dict[str, str]] = []
    for account_id in ids:
        if account_id not in backup_by_id:
            raise PublicationValidationError(f"Missing immutable backup for successful Id {account_id}")
        backup = backup_by_id[account_id]
        if str(backup.get("Id", "")).strip() != account_id:
            raise PublicationValidationError(f"Backup mapping/row Id mismatch for {account_id}")
        missing_fields = set(CANARY_FIELDS) - set(backup)
        if missing_fields:
            raise PublicationValidationError(
                f"Immutable backup for {account_id} is incomplete: missing {sorted(missing_fields)}"
            )
        current = current_by_id.get(account_id)
        if current is None:
            raise PublicationValidationError(f"Missing current compare-and-swap state for {account_id}")
        if str(current.get("Id", "")).strip() != account_id:
            raise PublicationValidationError(f"Current rollback mapping/row Id mismatch for {account_id}")
        current_run = normalized_salesforce_value(
            "AI_Prospect_Value_Run_Id__c", current.get("AI_Prospect_Value_Run_Id__c")
        )
        if current_run != expected_failed_run_id:
            raise PublicationValidationError(
                f"Rollback compare-and-swap failed for {account_id}: current Run Id is {current_run!r}"
            )
        expected_modstamp = successful_systemmodstamp_by_id.get(account_id)
        if not expected_modstamp:
            raise PublicationValidationError(
                f"Missing successful-write SystemModstamp for rollback Id {account_id}"
            )
        current_modstamp = normalized_salesforce_value("SystemModstamp", current.get("SystemModstamp"))
        if current_modstamp != normalized_salesforce_value("SystemModstamp", expected_modstamp):
            raise PublicationValidationError(
                f"Rollback SystemModstamp compare-and-swap failed for {account_id}"
            )
        rows.append(
            {
                field: account_id
                if field == "Id"
                else (NULL_SENTINEL if backup.get(field) is None else str(backup.get(field)))
                for field in CANARY_FIELDS
            }
        )
    return rows


def verify_normalized_readback(
    desired_rows: Sequence[Mapping[str, object]],
    readback_by_id: Mapping[str, Mapping[str, object]],
) -> dict[str, object]:
    """Compare an exact-ID Salesforce readback field by field."""

    desired_by_id: dict[str, Mapping[str, object]] = {}
    for row in desired_rows:
        validate_payload_row(row)
        account_id = str(row["Id"])
        if account_id in desired_by_id:
            raise PublicationValidationError(f"Duplicate desired readback Id {account_id}")
        desired_by_id[account_id] = row
    missing_ids = sorted(set(desired_by_id) - set(readback_by_id))
    unexpected_ids = sorted(set(readback_by_id) - set(desired_by_id))
    mismatches: list[dict[str, str]] = []
    for account_id in sorted(set(desired_by_id) & set(readback_by_id)):
        desired = desired_by_id[account_id]
        actual = readback_by_id[account_id]
        if str(actual.get("Id", "")).strip() != account_id:
            raise PublicationValidationError(f"Readback mapping/row Id mismatch for {account_id}")
        missing_fields = set(CANARY_FIELDS) - set(actual)
        if missing_fields:
            raise PublicationValidationError(
                f"Incomplete readback for {account_id}: missing {sorted(missing_fields)}"
            )
        for field in CANARY_FIELDS:
            if field == "Id":
                continue
            expected_value = normalized_salesforce_value(field, desired.get(field))
            actual_value = normalized_salesforce_value(field, actual.get(field))
            if expected_value != actual_value:
                mismatches.append(
                    {
                        "Id": account_id,
                        "field": field,
                        "expected": expected_value,
                        "actual": actual_value,
                    }
                )
    return {
        "desired_rows": len(desired_rows),
        "readback_rows": len(readback_by_id),
        "missing_ids": missing_ids,
        "unexpected_ids": unexpected_ids,
        "mismatches": mismatches,
        "passed": not (missing_ids or unexpected_ids or mismatches),
    }


def write_csv(path: str | Path, rows: Sequence[Mapping[str, object]], fields: Sequence[str]) -> None:
    """Write a deterministic artifact; callers must provide a new output path."""

    destination = Path(path)
    if destination.exists():
        raise FileExistsError(f"Refusing to overwrite immutable artifact: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fields), lineterminator="\n", extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)
