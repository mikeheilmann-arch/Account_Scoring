#!/usr/bin/env python3
"""Backtest Account value predictions against local calibration labels.

This script is intentionally no-write. It accepts a predictions CSV from a
Dust/V2 run, a future sandbox export, or a generated scorer output, joins it to
the local Account-level calibration labels, and writes repeatable evaluation
artifacts.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Iterable


DEFAULT_LABELS = Path(
    "artifacts/prospect_value_research/calibration_2026-05-12/calibration_account_level_labels.csv"
)

OUTPUT_COLUMNS = [
    "AccountId",
    "AccountName",
    "Website",
    "BillingState",
    "Type",
    "Industry",
    "WebsiteHygieneStatus",
    "WebsiteStaffConfidence",
    "PredictionAccountName",
    "PredictionSourceRow",
    "PredictionAction",
    "PredictionConfidence",
    "PredictionEntityType",
    "PredictionScore",
    "PredictedMcvLow",
    "PredictedMcvHigh",
    "PredictedMcvPoint",
    "PredictedArrPoint",
    "McvLabel",
    "ArrLabel",
    "McvAbsError",
    "McvPctError",
    "McvBucketHit",
    "ArrAbsError",
    "ArrPctError",
    "LabelFlags",
    "PredictionNotes",
]

NUMERIC_RE = re.compile(r"[-+]?\d+(?:,\d{3})*(?:\.\d+)?")
UNSIGNED_NUMERIC_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")


def clean(value: object) -> str:
    if value is None:
        return ""
    return str(value).strip()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: Iterable[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parse_float(value: object) -> float | None:
    text = clean(value)
    if not text:
        return None
    match = NUMERIC_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_money(value: object) -> float | None:
    text = clean(value)
    if not text:
        return None
    matches = UNSIGNED_NUMERIC_RE.findall(text)
    if not matches:
        return None
    values: list[float] = []
    multiplier = 1
    if re.search(r"\d\s*[kK]\b|\d[kK]\b", text):
        multiplier = 1_000
    for match in matches:
        values.append(float(match.replace(",", "")) * multiplier)
    return sum(values) / len(values)


def parse_range_point(value: object) -> tuple[float | None, float | None, float | None]:
    text = clean(value)
    if not text:
        return None, None, None
    matches = UNSIGNED_NUMERIC_RE.findall(text)
    if not matches:
        return None, None, None
    values = [float(match.replace(",", "")) for match in matches]
    if len(values) == 1:
        return values[0], values[0], values[0]
    low = min(values[0], values[1])
    high = max(values[0], values[1])
    return low, high, (low + high) / 2


def first_present(row: dict[str, str], *keys: str) -> str:
    for key in keys:
        value = clean(row.get(key, ""))
        if value:
            return value
    return ""


def normalized_account_id(row: dict[str, str]) -> str:
    return first_present(row, "AccountId", "Id", "SFDCAccountId", "ACCOUNTID", "ACCOUNT_ID")


def normalize_action(row: dict[str, str]) -> str:
    return first_present(row, "ReviewAction", "AI_Prospect_Value_Action__c", "Action", "PredictionAction")


def normalize_confidence(row: dict[str, str]) -> str:
    return first_present(row, "Confidence", "AI_Prospect_Value_Confidence__c", "PredictionConfidence")


def normalize_entity_type(row: dict[str, str]) -> str:
    return first_present(row, "EntityType", "PredictionEntityType", "ICPDisposition", "AI_Prospect_Value_ICP__c")


def extract_prediction(row: dict[str, str], source_row: int) -> dict[str, object] | None:
    account_id = normalized_account_id(row)
    if not account_id:
        return None

    mcv_low = parse_float(first_present(row, "AI_Prospect_Value_MCV_Low__c", "PredictedMcvLow", "EstimatedMCVLow"))
    mcv_high = parse_float(first_present(row, "AI_Prospect_Value_MCV_High__c", "PredictedMcvHigh", "EstimatedMCVHigh"))
    mcv_point = parse_float(first_present(row, "AI_Prospect_Value_MCV_Point__c", "PredictedMcvPoint", "EstimatedMCV"))

    if mcv_point is None:
        range_low, range_high, range_point = parse_range_point(first_present(row, "MCVEstimate", "MCV Est.", "McvEstimate"))
        mcv_low = mcv_low if mcv_low is not None else range_low
        mcv_high = mcv_high if mcv_high is not None else range_high
        mcv_point = range_point
    if mcv_point is None and mcv_low is not None and mcv_high is not None:
        mcv_point = (mcv_low + mcv_high) / 2

    arr_point = parse_float(
        first_present(row, "AI_Prospect_Value_ARR_Point__c", "ARR Point Estimate", "PredictedArrPoint", "EstimatedARR")
    )
    if arr_point is None:
        arr_point = parse_money(first_present(row, "ARRRange", "ARR Range", "ARR Range Raw", "AI_Prospect_Value_ARR_Range__c"))

    return {
        "AccountId": account_id,
        "PredictionAccountName": first_present(row, "AccountName", "Account Name", "Name"),
        "PredictionSourceRow": source_row,
        "PredictionAction": normalize_action(row),
        "PredictionConfidence": normalize_confidence(row),
        "PredictionEntityType": normalize_entity_type(row),
        "PredictionScore": parse_float(first_present(row, "TotalScore", "Total", "AI_Prospect_Value_Score__c", "Score")),
        "PredictedMcvLow": mcv_low,
        "PredictedMcvHigh": mcv_high,
        "PredictedMcvPoint": mcv_point,
        "PredictedArrPoint": arr_point,
        "PredictionNotes": first_present(row, "EvidenceNotes", "Notes", "AI_Prospect_Value_Evidence__c", "Evidence"),
    }


def load_predictions(path: Path) -> tuple[dict[str, dict[str, object]], dict[str, object]]:
    rows = read_csv(path)
    predictions: dict[str, dict[str, object]] = {}
    duplicate_count = 0
    missing_account_id_count = 0
    for index, row in enumerate(rows, start=2):
        prediction = extract_prediction(row, index)
        if not prediction:
            missing_account_id_count += 1
            continue
        account_id = str(prediction["AccountId"])
        if account_id in predictions:
            duplicate_count += 1
            # Keep the highest value/most scored row as the primary prediction.
            current_arr = predictions[account_id].get("PredictedArrPoint")
            next_arr = prediction.get("PredictedArrPoint")
            if (next_arr or 0) <= (current_arr or 0):
                continue
        predictions[account_id] = prediction
    metadata = {
        "prediction_input_rows": len(rows),
        "prediction_rows_with_account_id": len(rows) - missing_account_id_count,
        "unique_prediction_accounts": len(predictions),
        "duplicate_prediction_accounts": duplicate_count,
        "missing_account_id_rows": missing_account_id_count,
    }
    return predictions, metadata


def pct_error(predicted: float | None, actual: float | None) -> float | None:
    if predicted is None or actual is None or actual == 0:
        return None
    return (predicted - actual) / actual


def abs_error(predicted: float | None, actual: float | None) -> float | None:
    if predicted is None or actual is None:
        return None
    return abs(predicted - actual)


def bool_bucket_hit(low: float | None, high: float | None, actual: float | None) -> str:
    if low is None or high is None or actual is None:
        return ""
    return "true" if low <= actual <= high else "false"


def rounded(value: float | None, digits: int = 2) -> float | str:
    if value is None or math.isnan(value):
        return ""
    return round(value, digits)


def build_joined_rows(labels: list[dict[str, str]], predictions: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    joined: list[dict[str, object]] = []
    for label in labels:
        account_id = clean(label.get("AccountId"))
        prediction = predictions.get(account_id)
        if not prediction:
            continue

        mcv_label = parse_float(label.get("McvLabel"))
        arr_label = parse_float(label.get("ArrLabel"))
        mcv_point = prediction.get("PredictedMcvPoint")
        mcv_low = prediction.get("PredictedMcvLow")
        mcv_high = prediction.get("PredictedMcvHigh")
        arr_point = prediction.get("PredictedArrPoint")

        row = {
            "AccountId": account_id,
            "AccountName": label.get("AccountName", ""),
            "Website": label.get("Website", ""),
            "BillingState": label.get("BillingState", ""),
            "Type": label.get("Type", ""),
            "Industry": label.get("Industry", ""),
            "WebsiteHygieneStatus": label.get("WebsiteHygieneStatus", ""),
            "WebsiteStaffConfidence": label.get("WebsiteStaffConfidence", ""),
            **prediction,
            "PredictedMcvLow": rounded(mcv_low),
            "PredictedMcvHigh": rounded(mcv_high),
            "PredictedMcvPoint": rounded(mcv_point),
            "PredictedArrPoint": rounded(arr_point),
            "McvLabel": rounded(mcv_label),
            "ArrLabel": rounded(arr_label),
            "McvAbsError": rounded(abs_error(mcv_point, mcv_label)),
            "McvPctError": rounded(pct_error(mcv_point, mcv_label), 4),
            "McvBucketHit": bool_bucket_hit(mcv_low, mcv_high, mcv_label),
            "ArrAbsError": rounded(abs_error(arr_point, arr_label)),
            "ArrPctError": rounded(pct_error(arr_point, arr_label), 4),
            "LabelFlags": label.get("LabelFlags", ""),
        }
        joined.append(row)
    return joined


def values(rows: Iterable[dict[str, object]], key: str) -> list[float]:
    nums: list[float] = []
    for row in rows:
        parsed = parse_float(row.get(key))
        if parsed is not None:
            nums.append(parsed)
    return nums


def mean(nums: list[float]) -> float | None:
    if not nums:
        return None
    return sum(nums) / len(nums)


def metric_block(rows: list[dict[str, object]]) -> dict[str, object]:
    mcv_rows = [row for row in rows if clean(row.get("McvLabel")) and clean(row.get("PredictedMcvPoint"))]
    arr_rows = [row for row in rows if clean(row.get("ArrLabel")) and clean(row.get("PredictedArrPoint"))]
    bucket_rows = [row for row in rows if clean(row.get("McvBucketHit"))]
    mcv_abs = values(mcv_rows, "McvAbsError")
    arr_abs = values(arr_rows, "ArrAbsError")
    mcv_abs_pct = [abs(num) for num in values(mcv_rows, "McvPctError")]
    arr_abs_pct = [abs(num) for num in values(arr_rows, "ArrPctError")]

    return {
        "rows": len(rows),
        "mcv_eval_rows": len(mcv_rows),
        "arr_eval_rows": len(arr_rows),
        "mcv_mae": rounded(mean(mcv_abs)),
        "mcv_median_abs_error": rounded(median(mcv_abs) if mcv_abs else None),
        "mcv_mape": rounded(mean(mcv_abs_pct), 4),
        "arr_mae": rounded(mean(arr_abs)),
        "arr_median_abs_error": rounded(median(arr_abs) if arr_abs else None),
        "arr_mape": rounded(mean(arr_abs_pct), 4),
        "mcv_bucket_hit_rate": rounded(
            sum(1 for row in bucket_rows if row.get("McvBucketHit") == "true") / len(bucket_rows)
            if bucket_rows
            else None,
            4,
        ),
    }


def grouped_metrics(rows: list[dict[str, object]], key: str) -> list[dict[str, object]]:
    grouped: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        grouped[clean(row.get(key)) or "blank"].append(row)
    output: list[dict[str, object]] = []
    for group_key, group_rows in sorted(grouped.items(), key=lambda item: (-len(item[1]), item[0])):
        output.append({"group": group_key, **metric_block(group_rows)})
    return output


def write_report(path: Path, *, predictions_path: Path, summary: dict[str, object], group_files: dict[str, Path]) -> None:
    lines = [
        "# Account Value Calibration Backtest",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        f"Predictions: `{predictions_path}`",
        "",
        "## Summary",
        "",
    ]
    for key, value in summary.items():
        if isinstance(value, (dict, list)):
            continue
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Grouped Reports", ""])
    for label, file_path in group_files.items():
        lines.append(f"- {label}: `{file_path}`")
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This is a no-write local evaluation artifact.",
            "- Rows without AccountId in the prediction file are excluded.",
            "- MCV range hit rate only counts rows with predicted low/high and an MCV label.",
            "- ARR evaluation only counts rows with both predicted ARR and ARR label.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest Account value predictions against calibration labels.")
    parser.add_argument("--predictions", required=True, type=Path)
    parser.add_argument("--labels", default=DEFAULT_LABELS, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    labels = read_csv(args.labels)
    predictions, prediction_metadata = load_predictions(args.predictions)
    joined_rows = build_joined_rows(labels, predictions)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    joined_path = args.output_dir / "calibration_predictions_joined.csv"
    summary_path = args.output_dir / "calibration_summary.json"
    report_path = args.output_dir / "calibration_report.md"

    write_csv(joined_path, joined_rows, OUTPUT_COLUMNS)

    group_specs = {
        "confidence": "PredictionConfidence",
        "action": "PredictionAction",
        "state": "BillingState",
        "entity_type": "PredictionEntityType",
        "website_hygiene_status": "WebsiteHygieneStatus",
    }
    group_files: dict[str, Path] = {}
    group_summaries: dict[str, list[dict[str, object]]] = {}
    for label, column in group_specs.items():
        rows = grouped_metrics(joined_rows, column)
        file_path = args.output_dir / f"calibration_error_by_{label}.csv"
        write_csv(file_path, rows, list(rows[0].keys()) if rows else ["group"])
        group_files[label] = file_path
        group_summaries[label] = rows[:20]

    summary = {
        **prediction_metadata,
        "label_rows": len(labels),
        "joined_rows": len(joined_rows),
        "predictions_with_mcv": sum(1 for row in joined_rows if clean(row.get("PredictedMcvPoint"))),
        "predictions_with_arr": sum(1 for row in joined_rows if clean(row.get("PredictedArrPoint"))),
        **metric_block(joined_rows),
        "prediction_action_counts": dict(Counter(clean(row.get("PredictionAction")) or "blank" for row in joined_rows)),
        "prediction_confidence_counts": dict(Counter(clean(row.get("PredictionConfidence")) or "blank" for row in joined_rows)),
        "top_group_summaries": group_summaries,
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_report(report_path, predictions_path=args.predictions, summary=summary, group_files=group_files)

    print(f"joined_rows={len(joined_rows)}")
    print(f"mcv_eval_rows={summary['mcv_eval_rows']}")
    print(f"arr_eval_rows={summary['arr_eval_rows']}")
    print(f"mcv_mae={summary['mcv_mae']}")
    print(f"arr_mae={summary['arr_mae']}")
    print(f"output_dir={args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
