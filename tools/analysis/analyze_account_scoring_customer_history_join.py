from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd


ROOT = Path(os.environ.get("CERTIFID_DEV_ROOT", Path(__file__).resolve().parents[2]))
RUN_DATE = "20260630"
OUT_DIR = ROOT / "tmp" / f"account_scoring_customer_history_{RUN_DATE}"

FEATURES_PATH = OUT_DIR / f"account_scoring_customer_history_features_{RUN_DATE}.csv"
OVERLAY_PATH = (
    ROOT
    / "tmp"
    / "icp_quality_agent_p0_rerun_20260626"
    / "icp_quality_agent_p0_all_scored_overlay.csv"
)

JOINED_PATH = OUT_DIR / f"scored_accounts_customer_history_join_{RUN_DATE}.csv"
SUMMARY_PATH = OUT_DIR / f"scored_accounts_customer_history_analysis_{RUN_DATE}.json"
READOUT_PATH = OUT_DIR / f"scored_accounts_customer_history_analysis_{RUN_DATE}.md"


NUMERIC_COLUMNS = [
    "OriginalEstimatedMCV",
    "OriginalEstimatedARR",
    "OriginalReviewRank",
    "customer_health_score_c",
    "churn_count_c",
    "active_subscription_revenue_c",
    "total_contract_value_c",
    "annual_license_c",
    "total_revenue_c",
    "number_of_offices_c",
    "number_of_employees_c",
    "number_of_users_c",
    "direct_txn_90d",
    "direct_txn_180d",
    "direct_txn_365d",
    "direct_billable_txn_180d",
    "direct_active_months_365d",
    "direct_product_types_180d",
    "direct_active_locations_365d",
    "billing_rollup_txn_90d",
    "billing_rollup_txn_180d",
    "billing_rollup_txn_365d",
    "billing_rollup_billable_txn_180d",
    "billing_rollup_operating_accounts_365d",
    "billing_rollup_product_types_180d",
    "billing_rollup_active_locations_365d",
    "billing_billable_count_12m",
    "billing_usage_price_12m",
    "billing_usage_fee_12m",
    "billing_active_months_12m",
    "closed_opp_count",
    "closed_won_opp_count",
    "renewal_opp_count",
    "max_closed_won_arr",
    "max_closed_won_mrr",
    "max_closed_won_mcv",
    "max_renewal_amount",
    "max_at_risk_amount",
]


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def to_numeric(df: pd.DataFrame) -> pd.DataFrame:
    for col in NUMERIC_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col].replace("", pd.NA), errors="coerce")
    return df


def top_counts(series: pd.Series, n: int = 20) -> dict[str, int]:
    return {
        str(k): int(v)
        for k, v in series.fillna("").astype(str).value_counts(dropna=False).head(n).items()
    }


def coverage(df: pd.DataFrame, columns: list[str]) -> dict[str, dict[str, float | int]]:
    out: dict[str, dict[str, float | int]] = {}
    total = len(df)
    for col in columns:
        if col not in df.columns:
            continue
        s = df[col]
        if pd.api.types.is_bool_dtype(s):
            present = int(s.sum())
        elif pd.api.types.is_numeric_dtype(s):
            present = int(s.notna().sum())
        else:
            present = int(s.fillna("").astype(str).str.len().gt(0).sum())
        out[col] = {
            "present": present,
            "coverage_pct": round((present / total * 100) if total else 0, 1),
        }
    return out


def numeric_stats(series: pd.Series) -> dict[str, float | int | None]:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return {"n": 0, "sum": 0, "p50": None, "p75": None, "p90": None, "max": None}
    return {
        "n": int(s.size),
        "sum": round(float(s.sum()), 2),
        "p50": round(float(s.quantile(0.50)), 2),
        "p75": round(float(s.quantile(0.75)), 2),
        "p90": round(float(s.quantile(0.90)), 2),
        "max": round(float(s.max()), 2),
    }


def error_stats(df: pd.DataFrame, pred_col: str, actual_col: str) -> dict[str, float | int | None]:
    mask = df[pred_col].notna() & df[actual_col].notna() & df[actual_col].gt(0)
    scoped = df.loc[mask, [pred_col, actual_col]].copy()
    if scoped.empty:
        return {
            "n": 0,
            "mae": None,
            "median_abs_error": None,
            "median_predicted": None,
            "median_actual": None,
            "median_predicted_to_actual": None,
        }
    err = (scoped[pred_col] - scoped[actual_col]).abs()
    ratio = scoped[pred_col] / scoped[actual_col]
    return {
        "n": int(scoped.shape[0]),
        "mae": round(float(err.mean()), 2),
        "median_abs_error": round(float(err.median()), 2),
        "median_predicted": round(float(scoped[pred_col].median()), 2),
        "median_actual": round(float(scoped[actual_col].median()), 2),
        "median_predicted_to_actual": round(float(ratio.median()), 2),
    }


def spearman_table(df: pd.DataFrame, target: str, features: list[str]) -> list[dict[str, float | int | str]]:
    rows = []
    for feature in features:
        if feature == target:
            continue
        if feature not in df.columns or target not in df.columns:
            continue
        scoped = df[[feature, target]].dropna()
        scoped = scoped[scoped[target].gt(0)]
        if scoped.shape[0] < 20:
            continue
        corr = scoped[feature].corr(scoped[target], method="spearman")
        if pd.isna(corr):
            continue
        rows.append(
            {
                "feature": feature,
                "target": target,
                "n": int(scoped.shape[0]),
                "spearman": round(float(corr), 3),
            }
        )
    return sorted(rows, key=lambda row: abs(float(row["spearman"])), reverse=True)


def segmented_error(df: pd.DataFrame, group_col: str, pred_col: str, actual_col: str) -> list[dict[str, object]]:
    rows = []
    if group_col not in df.columns:
        return rows
    for value, scoped in df.groupby(df[group_col].fillna("").astype(str), dropna=False):
        stats = error_stats(scoped, pred_col, actual_col)
        if stats["n"]:
            rows.append({"group": value or "(blank)", **stats})
    return sorted(rows, key=lambda row: int(row["n"]), reverse=True)


def write_md(summary: dict) -> None:
    lines: list[str] = []
    lines.append("# Account Scoring Customer History Analysis")
    lines.append("")
    lines.append("No Salesforce writes, database writes, or deploys were performed. This joins the warehouse customer-history feature pull to the scored-account / ICP Quality Agent overlay.")
    lines.append("")
    lines.append("## Input Files")
    lines.append("")
    lines.append(f"- Customer-history features: `{FEATURES_PATH}`")
    lines.append(f"- Scored-account overlay: `{OVERLAY_PATH}`")
    lines.append("")
    lines.append("## Join Result")
    lines.append("")
    lines.append(f"- Scored overlay rows: {summary['row_counts']['overlay_rows']:,}")
    lines.append(f"- Unique scored accounts: {summary['row_counts']['unique_scored_accounts']:,}")
    lines.append(f"- Rows matched to warehouse feature file: {summary['row_counts']['joined_feature_rows']:,} ({summary['row_counts']['joined_feature_pct']}%)")
    lines.append(f"- Distinct scoring runs represented: {summary['row_counts']['scoring_runs']}")
    lines.append("")
    lines.append("## Coverage On Scored Accounts")
    lines.append("")
    for col, stats in summary["scored_account_coverage"].items():
        lines.append(f"- `{col}`: {stats['present']:,} rows ({stats['coverage_pct']}%)")
    lines.append("")
    lines.append("## Coverage In Full Warehouse Feature Pull")
    lines.append("")
    lines.append("The scored overlay is mostly prospect/account-list scoring, so usage and retention coverage is intentionally sparse there. The full warehouse pull is more useful for calibration because it includes the broader customer/account universe.")
    lines.append("")
    for col, stats in summary["warehouse_universe_coverage"].items():
        lines.append(f"- `{col}`: {stats['present']:,} accounts ({stats['coverage_pct']}%)")
    lines.append("")
    lines.append("## Label / Error Read")
    lines.append("")
    lines.append("These are directional because many scored rows are prospects with no customer-history labels. Customer/deal-history fields are most useful for calibration and suppression, not for scoring truly net-new prospects directly.")
    lines.append("")
    mcv = summary["prediction_error"]["mcv_vs_max_closed_won_mcv"]
    arr = summary["prediction_error"]["arr_vs_max_closed_won_arr"]
    lines.append(f"- MCV labels available on scored rows: {mcv['n']:,}; MAE {mcv['mae']}, median abs error {mcv['median_abs_error']}, median predicted/actual {mcv['median_predicted_to_actual']}x.")
    lines.append(f"- ARR labels available on scored rows: {arr['n']:,}; MAE {arr['mae']}, median abs error {arr['median_abs_error']}, median predicted/actual {arr['median_predicted_to_actual']}x.")
    lines.append("- ARR read is not a defect call: the scorer's ARR field is pipeline-potential ARR, while warehouse closed-won ARR / subscription revenue are realized commercial outcomes. Use them to shape and label potential bands, not to force a one-to-one realized ARR target.")
    lines.append("")
    lines.append("## Strongest Warehouse Signals")
    lines.append("")
    lines.append("Spearman correlations are calculated only where both the feature and the target are present. They should be used as prioritization signals for feature engineering, not as final model coefficients.")
    lines.append("")
    lines.append("### MCV Target")
    for row in summary["correlations"]["max_closed_won_mcv"][:10]:
        lines.append(f"- `{row['feature']}`: rho {row['spearman']} on {row['n']:,} rows")
    lines.append("")
    lines.append("### ARR Target")
    for row in summary["correlations"]["max_closed_won_arr"][:10]:
        lines.append(f"- `{row['feature']}`: rho {row['spearman']} on {row['n']:,} rows")
    lines.append("")
    lines.append("## Full-Warehouse Calibration Signal")
    lines.append("")
    lines.append("This is the better evidence base for future calibration because it uses the broader customer/account universe, not only the accounts already scored in prospect tests.")
    lines.append("")
    lines.append("### MCV Target")
    for row in summary["warehouse_correlations"]["max_closed_won_mcv"][:10]:
        lines.append(f"- `{row['feature']}`: rho {row['spearman']} on {row['n']:,} rows")
    lines.append("")
    lines.append("### ARR Target")
    for row in summary["warehouse_correlations"]["max_closed_won_arr"][:10]:
        lines.append(f"- `{row['feature']}`: rho {row['spearman']} on {row['n']:,} rows")
    lines.append("")
    lines.append("## Interpretation")
    lines.append("")
    lines.append("- Mark's instinct is right: customer history can improve the model, but mostly as a calibration and QA layer rather than a raw prospect feature. Most prospects do not have usage or retention history.")
    lines.append("- The useful pattern is: use closed-won MCV/ARR, active subscription revenue, real transaction consumption, churn/health, and parent-billing context to decide which historical customers are good calibration examples.")
    lines.append("- Do not blindly train on every closed-won customer. Churned, inactive, billing-exception, failed-contract-join, and bad-anchor accounts should be downweighted or excluded from calibration.")
    lines.append("- Parent billing matters. The usage rollup fields help avoid judging child/location accounts as if they were independent sellable entities.")
    lines.append("- Current ARR remains a pipeline-potential field. Booked ARR and subscription revenue should anchor potential bands, but should not automatically force the prospect score down to realized ARR.")
    lines.append("")
    lines.append("## Recommended Model Changes")
    lines.append("")
    lines.append("1. Add a `customer_history_quality` feature set used only for customers / prior-opportunity accounts: active, retained, churned, at-risk, parent-billed, usage-observed, billing-exception.")
    lines.append("2. Use healthy retained customers as the calibration backbone for MCV and pipeline-potential ARR bands; exclude or separately tag churned / failed-billing / bad-anchor records.")
    lines.append("3. Feed `billing_rollup_txn_365d`, `direct_txn_365d`, `billing_billable_count_12m`, `active_subscription_revenue_c`, and `customer_health_score_c` into backtesting dashboards for model validation.")
    lines.append("4. Keep these signals out of greenfield prospect scoring unless the account is actually an existing customer, child account, duplicate, or prior-opportunity account; otherwise they create false precision.")
    lines.append("5. Use hierarchy fields from `prep_salesforce_account` and transaction rollups to strengthen the ICP Quality Agent's sellable-account gate.")
    lines.append("")
    lines.append("## Output Files")
    lines.append("")
    lines.append(f"- Joined scored/customer-history file: `{JOINED_PATH}`")
    lines.append(f"- JSON summary: `{SUMMARY_PATH}`")
    lines.append("")
    READOUT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    overlay = read_csv(OVERLAY_PATH)
    features = read_csv(FEATURES_PATH)

    overlay["join_account_id"] = overlay["AccountId"].str.strip().str.lower()
    features["join_account_id"] = features["sfdc_account_id"].str.strip().str.lower()

    features = to_numeric(features)
    joined = overlay.merge(features, on="join_account_id", how="left", suffixes=("", "_warehouse"))
    joined = to_numeric(joined)

    joined["has_warehouse_match"] = joined["sfdc_account_id"].fillna("").astype(str).str.len().gt(0)
    joined["has_direct_usage"] = joined["direct_txn_365d"].fillna(0).gt(0)
    joined["has_billing_usage"] = joined["billing_rollup_txn_365d"].fillna(0).gt(0)
    joined["has_billing_report"] = joined["billing_billable_count_12m"].fillna(0).gt(0)
    joined["has_opportunity_history"] = joined["closed_opp_count"].fillna(0).gt(0)
    joined["has_closed_won_label"] = joined["closed_won_opp_count"].fillna(0).gt(0)
    joined["has_mcv_label"] = joined["max_closed_won_mcv"].notna() & joined["max_closed_won_mcv"].gt(0)
    joined["has_arr_label"] = joined["max_closed_won_arr"].notna() & joined["max_closed_won_arr"].gt(0)
    joined["has_retention_risk"] = (
        joined["churn_count_c"].fillna(0).gt(0)
        | joined["churn_date_c"].fillna("").astype(str).str.len().gt(0)
        | joined["billing_has_churned_exception_24m"].fillna("").astype(str).str.lower().isin(["true", "t", "1", "yes"])
        | joined["billing_has_churned_no_billable_exception_24m"].fillna("").astype(str).str.lower().isin(["true", "t", "1", "yes"])
        | joined["latest_subscription_status"].fillna("").astype(str).str.lower().isin(["cancelled", "expired"])
    )
    features["has_direct_usage"] = features["direct_txn_365d"].fillna(0).gt(0)
    features["has_billing_usage"] = features["billing_rollup_txn_365d"].fillna(0).gt(0)
    features["has_billing_report"] = features["billing_billable_count_12m"].fillna(0).gt(0)
    features["has_opportunity_history"] = features["closed_opp_count"].fillna(0).gt(0)
    features["has_closed_won_history"] = features["closed_won_opp_count"].fillna(0).gt(0)
    features["has_mcv_label"] = features["max_closed_won_mcv"].notna() & features["max_closed_won_mcv"].gt(0)
    features["has_arr_label"] = features["max_closed_won_arr"].notna() & features["max_closed_won_arr"].gt(0)
    features["has_retention_risk"] = (
        features["churn_count_c"].fillna(0).gt(0)
        | features["churn_date_c"].fillna("").astype(str).str.len().gt(0)
        | features["billing_has_churned_exception_24m"].fillna("").astype(str).str.lower().isin(["true", "t", "1", "yes"])
        | features["billing_has_churned_no_billable_exception_24m"].fillna("").astype(str).str.lower().isin(["true", "t", "1", "yes"])
        | features["latest_subscription_status"].fillna("").astype(str).str.lower().isin(["cancelled", "expired"])
    )

    joined.to_csv(JOINED_PATH, index=False)

    score_now = joined[joined["OriginalAction"].eq("score_now")].copy()
    allow_score = joined[joined["QualityAction"].eq("allow_score")].copy()

    candidate_features = [
        "active_subscription_revenue_c",
        "total_contract_value_c",
        "annual_license_c",
        "total_revenue_c",
        "number_of_offices_c",
        "number_of_employees_c",
        "number_of_users_c",
        "customer_health_score_c",
        "direct_txn_90d",
        "direct_txn_180d",
        "direct_txn_365d",
        "direct_billable_txn_180d",
        "direct_active_months_365d",
        "direct_product_types_180d",
        "direct_active_locations_365d",
        "billing_rollup_txn_90d",
        "billing_rollup_txn_180d",
        "billing_rollup_txn_365d",
        "billing_rollup_billable_txn_180d",
        "billing_rollup_operating_accounts_365d",
        "billing_rollup_product_types_180d",
        "billing_rollup_active_locations_365d",
        "billing_billable_count_12m",
        "billing_usage_price_12m",
        "billing_usage_fee_12m",
        "billing_active_months_12m",
    ]

    summary = {
        "inputs": {
            "features_path": str(FEATURES_PATH),
            "overlay_path": str(OVERLAY_PATH),
        },
        "outputs": {
            "joined_path": str(JOINED_PATH),
            "summary_path": str(SUMMARY_PATH),
            "readout_path": str(READOUT_PATH),
        },
        "row_counts": {
            "overlay_rows": int(len(overlay)),
            "unique_scored_accounts": int(overlay["AccountId"].nunique()),
            "warehouse_feature_accounts": int(features["sfdc_account_id"].nunique()),
            "joined_rows": int(len(joined)),
            "joined_feature_rows": int(joined["has_warehouse_match"].sum()),
            "joined_feature_pct": round(float(joined["has_warehouse_match"].mean() * 100), 1),
            "scoring_runs": int(joined["ScoringRunLabel"].nunique()),
            "score_now_rows": int(joined["OriginalAction"].eq("score_now").sum()),
            "allow_score_rows": int(joined["QualityAction"].eq("allow_score").sum()),
        },
        "counts": {
            "scoring_run": top_counts(joined["ScoringRunLabel"]),
            "original_action": top_counts(joined["OriginalAction"]),
            "quality_action": top_counts(joined["QualityAction"]),
            "quality_disposition": top_counts(joined["QualityDisposition"]),
            "crm_type": top_counts(joined["CrmType"]),
            "warehouse_type": top_counts(joined["sfdc_account_type"]),
            "warehouse_account_status": top_counts(joined["account_status_c"]),
            "subscription_status": top_counts(joined["latest_subscription_status"]),
            "company_type": top_counts(joined["CrmCompanyType"]),
        },
        "scored_account_coverage": coverage(
            joined,
            [
                "customer_health_score_c",
                "active_subscription_revenue_c",
                "total_contract_value_c",
                "direct_txn_365d",
                "billing_rollup_txn_365d",
                "billing_billable_count_12m",
                "closed_opp_count",
                "closed_won_opp_count",
                "max_closed_won_mcv",
                "max_closed_won_arr",
                "has_retention_risk",
            ],
        ),
        "score_now_coverage": coverage(
            score_now,
            [
                "customer_health_score_c",
                "active_subscription_revenue_c",
                "direct_txn_365d",
                "billing_rollup_txn_365d",
                "billing_billable_count_12m",
                "closed_opp_count",
                "max_closed_won_mcv",
                "max_closed_won_arr",
                "has_retention_risk",
            ],
        ),
        "warehouse_universe_coverage": coverage(
            features,
            [
                "customer_health_score_c",
                "active_subscription_revenue_c",
                "total_contract_value_c",
                "direct_txn_365d",
                "billing_rollup_txn_365d",
                "billing_billable_count_12m",
                "closed_opp_count",
                "closed_won_opp_count",
                "max_closed_won_mcv",
                "max_closed_won_arr",
                "has_direct_usage",
                "has_billing_usage",
                "has_billing_report",
                "has_opportunity_history",
                "has_closed_won_history",
                "has_mcv_label",
                "has_arr_label",
                "has_retention_risk",
            ],
        ),
        "numeric_stats_all_scored": {
            col: numeric_stats(joined[col])
            for col in [
                "direct_txn_365d",
                "billing_rollup_txn_365d",
                "billing_billable_count_12m",
                "active_subscription_revenue_c",
                "max_closed_won_mcv",
                "max_closed_won_arr",
                "OriginalEstimatedMCV",
                "OriginalEstimatedARR",
            ]
            if col in joined.columns
        },
        "prediction_error": {
            "mcv_vs_max_closed_won_mcv": error_stats(joined, "OriginalEstimatedMCV", "max_closed_won_mcv"),
            "arr_vs_max_closed_won_arr": error_stats(joined, "OriginalEstimatedARR", "max_closed_won_arr"),
            "score_now_mcv_vs_max_closed_won_mcv": error_stats(score_now, "OriginalEstimatedMCV", "max_closed_won_mcv"),
            "score_now_arr_vs_max_closed_won_arr": error_stats(score_now, "OriginalEstimatedARR", "max_closed_won_arr"),
            "allow_score_mcv_vs_max_closed_won_mcv": error_stats(allow_score, "OriginalEstimatedMCV", "max_closed_won_mcv"),
            "allow_score_arr_vs_max_closed_won_arr": error_stats(allow_score, "OriginalEstimatedARR", "max_closed_won_arr"),
        },
        "segmented_error": {
            "mcv_by_company_type": segmented_error(joined, "CrmCompanyType", "OriginalEstimatedMCV", "max_closed_won_mcv"),
            "mcv_by_quality_action": segmented_error(joined, "QualityAction", "OriginalEstimatedMCV", "max_closed_won_mcv"),
            "arr_by_company_type": segmented_error(joined, "CrmCompanyType", "OriginalEstimatedARR", "max_closed_won_arr"),
            "arr_by_quality_action": segmented_error(joined, "QualityAction", "OriginalEstimatedARR", "max_closed_won_arr"),
        },
        "correlations": {
            "max_closed_won_mcv": spearman_table(joined, "max_closed_won_mcv", candidate_features),
            "max_closed_won_arr": spearman_table(joined, "max_closed_won_arr", candidate_features),
            "active_subscription_revenue_c": spearman_table(joined, "active_subscription_revenue_c", candidate_features),
        },
        "warehouse_correlations": {
            "max_closed_won_mcv": spearman_table(features, "max_closed_won_mcv", candidate_features),
            "max_closed_won_arr": spearman_table(features, "max_closed_won_arr", candidate_features),
            "active_subscription_revenue_c": spearman_table(features, "active_subscription_revenue_c", candidate_features),
        },
    }

    SUMMARY_PATH.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    write_md(summary)

    print(f"Wrote {JOINED_PATH}")
    print(f"Wrote {SUMMARY_PATH}")
    print(f"Wrote {READOUT_PATH}")


if __name__ == "__main__":
    main()
