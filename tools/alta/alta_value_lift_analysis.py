from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from scipy.stats import mannwhitneyu


DEFAULT_ENRICHMENT_PATH = Path("artifacts/alta_enrichment/sfdc_alta_enrichment.csv")
DEFAULT_SFDC_CSV_PATH = Path("artifacts/alta_enrichment/alta_value_universe.csv")
DEFAULT_OUTPUT_PATH = Path("artifacts/alta_enrichment/alta_value_lift_analysis.md")
DEFAULT_TARGET_ORG = "CertifID"
SEGMENTS = ["Strategic", "Core", "< 10", "Nationals"]
SOQL = (
    "SELECT Id, Name, Type, Active_Customer__c, Account_Segment__c, Plan_Tier__c, "
    "Per_File_Pricing__c, Active_Subscription_Revenue__c, Final_Monthly_Closing_Volume__c, "
    "BillingCity, BillingState, Industry "
    "FROM Account "
    "WHERE Type = 'Prospect' OR Active_Customer__c = true"
)


@dataclass
class SegmentResult:
    segment: str
    alta_count: int
    alta_avg_arr: float
    alta_median_arr: float
    non_count: int
    non_avg_arr: float
    non_median_arr: float
    lift_text: str
    significance_note: str


def boolish(series: pd.Series) -> pd.Series:
    return series.fillna("").astype(str).str.strip().str.lower() == "true"


def format_currency(value: float) -> str:
    return f"${value:,.0f}"


def format_pct(value: float) -> str:
    return f"{value:.1f}%"


def markdown_escape(value: object) -> str:
    return str(value).replace("|", "\\|")


def markdown_table(headers: list[str], rows: list[list[object]]) -> str:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(markdown_escape(cell) for cell in row) + " |")
    return "\n".join(lines)


def run_sfdc_query(target_org: str, output_path: Path) -> None:
    quoted_soql = SOQL.replace("'", "''")
    command = [
        "powershell.exe",
        "-NoProfile",
        "-Command",
        f"sf data query --query '{quoted_soql}' --target-org {target_org} --result-format csv",
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "SFDC query failed.\n"
            f"stdout:\n{completed.stdout.strip()}\n\n"
            f"stderr:\n{completed.stderr.strip()}"
        )
    output_path.write_text(completed.stdout, encoding="utf-16")


def read_csv_auto(path: Path) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "utf-16", "utf-8"):
        try:
            return pd.read_csv(path, encoding=encoding)
        except UnicodeError:
            continue
    raise RuntimeError(f"Could not read CSV with known encodings: {path}")


def prepare_dataframe(enrichment_path: Path, sfdc_csv_path: Path) -> pd.DataFrame:
    enrichment = pd.read_csv(enrichment_path)
    universe = read_csv_auto(sfdc_csv_path)

    low_confidence_ids = set(
        enrichment.loc[enrichment["Match_Confidence"].fillna("") == "low", "Account_Id"].astype(str)
    )
    enrichment = enrichment.loc[~enrichment["Account_Id"].astype(str).isin(low_confidence_ids)].copy()
    enrichment["ALTA_Member_Flag"] = (
        enrichment["ALTA_Member"].fillna("").astype(str).str.strip().str.lower() == "true"
    )

    merged = universe.merge(
        enrichment[["Account_Id", "ALTA_Member_Flag", "Match_Confidence"]],
        left_on="Id",
        right_on="Account_Id",
        how="left",
    )
    merged = merged.loc[~merged["Id"].astype(str).isin(low_confidence_ids)].copy()
    merged["ALTA_Member_Flag"] = merged["ALTA_Member_Flag"].astype("boolean").fillna(False).astype(bool)
    merged["Match_Confidence"] = merged["Match_Confidence"].fillna("no_match")
    merged["Is_Active_Customer"] = boolish(merged["Active_Customer__c"])
    merged["Is_Prospect"] = merged["Type"].fillna("").astype(str).str.strip() == "Prospect"
    merged["Is_V3"] = boolish(merged["Per_File_Pricing__c"])
    merged["ARR"] = pd.to_numeric(merged["Active_Subscription_Revenue__c"], errors="coerce").fillna(0.0)
    merged["MCV"] = pd.to_numeric(merged["Final_Monthly_Closing_Volume__c"], errors="coerce").fillna(0.0)
    merged["Account_Segment__c"] = merged["Account_Segment__c"].fillna("")
    merged["Plan_Tier__c"] = merged["Plan_Tier__c"].fillna("")
    return merged


def summarize_arr_subset(segment: str, subset: pd.DataFrame) -> SegmentResult:
    alta = subset.loc[subset["ALTA_Member_Flag"], "ARR"]
    non = subset.loc[~subset["ALTA_Member_Flag"], "ARR"]

    alta_count = int(alta.shape[0])
    non_count = int(non.shape[0])
    alta_avg = float(alta.mean()) if alta_count else 0.0
    alta_median = float(alta.median()) if alta_count else 0.0
    non_avg = float(non.mean()) if non_count else 0.0
    non_median = float(non.median()) if non_count else 0.0

    if non_count == 0:
        lift_text = "n/a"
    elif non_median == 0:
        lift_text = "n/a (non-ALTA median = $0)"
    else:
        lift = ((alta_median / non_median) - 1.0) * 100.0
        lift_text = format_pct(lift)

    if alta_count < 20 or non_count < 20:
        significance_note = "small sample, directional only"
    else:
        p_value = mannwhitneyu(alta, non, alternative="two-sided").pvalue
        significance_note = f"Mann-Whitney p={p_value:.3g}"

    return SegmentResult(
        segment=segment,
        alta_count=alta_count,
        alta_avg_arr=alta_avg,
        alta_median_arr=alta_median,
        non_count=non_count,
        non_avg_arr=non_avg,
        non_median_arr=non_median,
        lift_text=lift_text,
        significance_note=significance_note,
    )


def build_arr_table(subset: pd.DataFrame) -> list[SegmentResult]:
    results: list[SegmentResult] = []
    for segment in SEGMENTS:
        segment_subset = subset.loc[subset["Account_Segment__c"] == segment].copy()
        results.append(summarize_arr_subset(segment, segment_subset))
    results.append(summarize_arr_subset("All", subset))
    return results


def arr_table_rows(results: list[SegmentResult]) -> list[list[object]]:
    rows: list[list[object]] = []
    for result in results:
        rows.append(
            [
                result.segment,
                f"{result.alta_count}; {format_currency(result.alta_avg_arr)}; {format_currency(result.alta_median_arr)}",
                f"{result.non_count}; {format_currency(result.non_avg_arr)}; {format_currency(result.non_median_arr)}",
                result.lift_text,
                result.significance_note,
            ]
        )
    return rows


def mcv_table_rows(df: pd.DataFrame) -> list[list[object]]:
    cohorts = [
        ("ALTA-matched customers", df.loc[df["Is_Active_Customer"] & df["ALTA_Member_Flag"]]),
        ("Non-ALTA customers", df.loc[df["Is_Active_Customer"] & ~df["ALTA_Member_Flag"]]),
        ("ALTA-matched prospects", df.loc[df["Is_Prospect"] & df["ALTA_Member_Flag"]]),
        ("Non-ALTA prospects", df.loc[df["Is_Prospect"] & ~df["ALTA_Member_Flag"]]),
    ]
    rows: list[list[object]] = []
    for label, subset in cohorts:
        pct = (subset["MCV"] > 0).mean() * 100 if not subset.empty else 0.0
        rows.append([label, int(subset.shape[0]), format_pct(pct)])
    return rows


def plan_tier_rows(active_customers: pd.DataFrame) -> list[list[object]]:
    grouped = (
        active_customers.groupby("Plan_Tier__c", dropna=False)
        .agg(
            total_customers=("Id", "count"),
            alta_matched=("ALTA_Member_Flag", "sum"),
        )
        .reset_index()
    )
    grouped["pct_alta_matched"] = (grouped["alta_matched"] / grouped["total_customers"]) * 100
    grouped = grouped.sort_values(["total_customers", "alta_matched"], ascending=[False, False])

    rows: list[list[object]] = []
    for row in grouped.itertuples(index=False):
        label = row.Plan_Tier__c if str(row.Plan_Tier__c).strip() else "(blank)"
        note = "small sample, directional only" if int(row.total_customers) < 20 else ""
        rows.append(
            [
                label,
                int(row.total_customers),
                int(row.alta_matched),
                format_pct(float(row.pct_alta_matched)),
                note,
            ]
        )
    return rows


def build_interpretation(
    blended_results: list[SegmentResult],
    v3_results: list[SegmentResult],
    df: pd.DataFrame,
) -> tuple[str, str]:
    blended_all = next(result for result in blended_results if result.segment == "All")
    v3_all = next(result for result in v3_results if result.segment == "All")
    v3_core = next(result for result in v3_results if result.segment == "Core")
    v3_strategic = next(result for result in v3_results if result.segment == "Strategic")

    active_customers = df.loc[df["Is_Active_Customer"]].copy()
    strategic_v3 = active_customers.loc[
        active_customers["Is_V3"] & (active_customers["Account_Segment__c"] == "Strategic")
    ].copy()
    strategic_alta_positive = strategic_v3.loc[
        strategic_v3["ALTA_Member_Flag"] & (strategic_v3["ARR"] > 0), "ARR"
    ]
    strategic_non_positive = strategic_v3.loc[
        ~strategic_v3["ALTA_Member_Flag"] & (strategic_v3["ARR"] > 0), "ARR"
    ]
    positive_strategic_note = (
        "Among positive-ARR Strategic v3 customers, ALTA median ARR does not exceed the non-ALTA median "
        f"({format_currency(float(strategic_alta_positive.median()))} vs. "
        f"{format_currency(float(strategic_non_positive.median()))})."
        if not strategic_alta_positive.empty and not strategic_non_positive.empty
        else "Strategic v3 positive-ARR cohorts are too sparse for a stable secondary comparison."
    )

    blank_segment_customers = int(
        active_customers.loc[active_customers["Account_Segment__c"] == ""].shape[0]
    )

    interpretation = (
        "The blended active-customer view shows a large all-up ALTA premium, but that does not hold up as a stable "
        "forward-looking pricing signal once the analysis is narrowed to the v3 (`Per_File_Pricing__c = true`) cohort. "
        f"In the v3 cohort, Core median ARR lift is only {v3_core.lift_text}, while Strategic lift is not interpretable "
        "because the non-ALTA Strategic median ARR is $0, driven by a heavy concentration of zero-ARR accounts. "
        f"{positive_strategic_note} ALTA still looks operationally useful because MCV coverage is materially higher for "
        "ALTA-matched accounts, but the ARR evidence is not stable enough to justify changing the dollar value curve. "
        f"{blank_segment_customers} active customers have blank `Account_Segment__c` and are included only in the `All` row."
    )

    recommendation = (
        "Recommendation: keep ALTA membership as a discovery and prioritization flag only; do not add it as a multiplier, "
        "tier modifier, or confidence-weight inside the prospect value curve. The decision rule is not met because the "
        f"v3 Core median lift is only {v3_core.lift_text} and the Strategic v3 median comparison is unstable. "
        f"Use ALTA for prospect legitimacy screening and MCV-enrichment triage instead of ARR estimation. "
        f"For reference, blended all-customer median lift is {blended_all.lift_text} and v3 all-customer median lift is "
        f"{v3_all.lift_text}, but neither overturns the segment-level rule."
    )

    return interpretation, recommendation


def write_markdown(output_path: Path, df: pd.DataFrame) -> tuple[list[SegmentResult], list[SegmentResult], str]:
    active_customers = df.loc[df["Is_Active_Customer"]].copy()
    v3_customers = active_customers.loc[active_customers["Is_V3"]].copy()

    blended_results = build_arr_table(active_customers)
    v3_results = build_arr_table(v3_customers)
    interpretation, recommendation = build_interpretation(blended_results, v3_results, df)

    markdown = "\n".join(
        [
            "# ALTA Value Lift Analysis",
            "",
            "This analysis uses the April 23, 2026 live SFDC extract joined to `sfdc_alta_enrichment.csv`. "
            "Low-confidence ALTA matches (`n=3`) are excluded from every table. ARR is `Active_Subscription_Revenue__c`; "
            "verified MCV coverage is defined as `Final_Monthly_Closing_Volume__c > 0`.",
            "",
            "## Table 1: Blended ARR Lift (All Active Customers)",
            "",
            markdown_table(
                [
                    "Segment",
                    "ALTA Members (Count; Avg ARR; Median ARR)",
                    "Non-ALTA (Count; Avg ARR; Median ARR)",
                    "ARR Lift %",
                    "Significance Note",
                ],
                arr_table_rows(blended_results),
            ),
            "",
            "## Table 2: v3-Only ARR Lift (`Per_File_Pricing__c = true`)",
            "",
            markdown_table(
                [
                    "Segment",
                    "ALTA Members (Count; Avg ARR; Median ARR)",
                    "Non-ALTA (Count; Avg ARR; Median ARR)",
                    "ARR Lift %",
                    "Significance Note",
                ],
                arr_table_rows(v3_results),
            ),
            "",
            "## Table 3: MCV Coverage Lift",
            "",
            markdown_table(
                ["Cohort", "Count", "% with Verified MCV > 0"],
                mcv_table_rows(df),
            ),
            "",
            "## Table 4: Plan Tier Distribution (Active Customers)",
            "",
            markdown_table(
                ["Plan Tier", "Total Active Customers", "ALTA-Matched Customers", "% of Tier ALTA-Matched", "Note"],
                plan_tier_rows(active_customers),
            ),
            "",
            "## Interpretation",
            "",
            interpretation,
            "",
            "## Recommendation",
            "",
            recommendation,
            "",
            "## Workstream 2 Decision",
            "",
            "California gap investigation deferred. The v3 ARR-lift rule for model integration was not met, so the prompt's "
            "instruction is to stop after Workstream 1 rather than spend more time on the CA coverage spot-check.",
            "",
        ]
    )
    output_path.write_text(markdown, encoding="utf-8")
    return blended_results, v3_results, recommendation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build the ALTA value-lift analysis markdown.")
    parser.add_argument("--enrichment-csv", type=Path, default=DEFAULT_ENRICHMENT_PATH)
    parser.add_argument("--sfdc-csv", type=Path, default=DEFAULT_SFDC_CSV_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--target-org", default=DEFAULT_TARGET_ORG)
    parser.add_argument(
        "--refresh-sfdc",
        action="store_true",
        help="Refresh the SFDC extract before computing the markdown.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.refresh_sfdc or not args.sfdc_csv.exists():
            run_sfdc_query(args.target_org, args.sfdc_csv)
        df = prepare_dataframe(args.enrichment_csv, args.sfdc_csv)
        blended_results, v3_results, recommendation = write_markdown(args.output, df)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    blended_all = next(result for result in blended_results if result.segment == "All")
    v3_all = next(result for result in v3_results if result.segment == "All")
    v3_core = next(result for result in v3_results if result.segment == "Core")
    v3_strategic = next(result for result in v3_results if result.segment == "Strategic")

    print(f"Blended all-customer median ARR lift: {blended_all.lift_text}")
    print(f"V3 all-customer median ARR lift: {v3_all.lift_text}")
    print(f"V3 Core median ARR lift: {v3_core.lift_text}")
    print(f"V3 Strategic median ARR lift: {v3_strategic.lift_text}")
    print(recommendation)
    print(f"Markdown written to: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
