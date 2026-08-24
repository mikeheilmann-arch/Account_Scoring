-- Account scoring customer-history / retention feature pull
-- Purpose: no-write account-level feature table for calibrating prospect value scoring.
-- Grain: one row per Salesforce Account ID.
--
-- Live-warehouse note, 2026-06-30:
-- The local dbt repo contains dim_company/dim_contract models, but those relations
-- are not deployed in the warehouse schemas currently visible to the analyst role.
-- This query uses verified live relations instead:
--   analytics.salesforce_account
--   analytics.salesforce_opportunity
--   dbt_prep.prep_salesforce_account
--   dbt_mart.mart_transaction_charge
--   dbt_report.rpt_billing_usage_monthly

WITH account_base AS (
    SELECT
        LOWER(account.id) AS sfdc_account_id,
        account.name AS sfdc_account_name,
        account.type AS sfdc_account_type,
        account.customer_status_c,
        account.account_status_c,
        account.certif_id_customer_segment_c,
        account.customer_health_score_c,
        account.churn_count_c,
        account.churn_date_c,
        account.next_renewal_date_c,
        account.active_subscription_revenue_c,
        account.total_contract_value_c,
        account.annual_license_c,
        account.total_revenue_c,
        account.number_of_offices_c,
        account.number_of_employees_c,
        account.number_of_users_c,
        account.parent_id AS sfdc_parent_id
    FROM analytics.salesforce_account AS account
    WHERE NOT COALESCE(account.is_deleted, FALSE)
      AND NOT COALESCE(account._fivetran_deleted, FALSE)
),

hierarchy AS (
    SELECT
        LOWER(account_id) AS sfdc_account_id,
        LOWER(parent_id) AS direct_parent_id,
        LOWER(highest_parent_id) AS highest_parent_id,
        is_parent_billed,
        bill_parent_start_date,
        parent_name,
        paymints_identifier,
        csm_name,
        customer_segment,
        title_production_software,
        account_plan_tier,
        committed_transaction_billing_type,
        is_file_based_pricing,
        is_customer_migrated_to_new_pricing
    FROM dbt_prep.prep_salesforce_account
    WHERE account_id IS NOT NULL
),

transaction_base AS (
    SELECT
        LOWER(sfdc_account_id) AS direct_sfdc_account_id,
        LOWER(billing_id) AS billing_sfdc_account_id,
        company_id,
        tenant_id,
        location_id,
        office_id,
        transaction_type,
        is_billable,
        created_at,
        billable_at,
        usage_billable_at,
        completed_at,
        transaction_value,
        billing_type,
        subscription_id,
        contract_start_date,
        contract_end_date,
        _contract_match_type,
        _is_contract_match
    FROM dbt_mart.mart_transaction_charge
    WHERE COALESCE(billable_at, usage_billable_at, created_at) >= CURRENT_DATE - INTERVAL '24 months'
),

direct_usage_features AS (
    SELECT
        direct_sfdc_account_id AS sfdc_account_id,
        COUNT(*) FILTER (
            WHERE COALESCE(billable_at, usage_billable_at, created_at) >= CURRENT_DATE - INTERVAL '90 days'
        ) AS direct_txn_90d,
        COUNT(*) FILTER (
            WHERE COALESCE(billable_at, usage_billable_at, created_at) >= CURRENT_DATE - INTERVAL '180 days'
        ) AS direct_txn_180d,
        COUNT(*) FILTER (
            WHERE COALESCE(billable_at, usage_billable_at, created_at) >= CURRENT_DATE - INTERVAL '365 days'
        ) AS direct_txn_365d,
        COUNT(*) FILTER (
            WHERE is_billable
              AND COALESCE(billable_at, usage_billable_at, created_at) >= CURRENT_DATE - INTERVAL '180 days'
        ) AS direct_billable_txn_180d,
        COUNT(DISTINCT DATE_TRUNC('month', COALESCE(billable_at, usage_billable_at, created_at))) FILTER (
            WHERE COALESCE(billable_at, usage_billable_at, created_at) >= CURRENT_DATE - INTERVAL '365 days'
        ) AS direct_active_months_365d,
        COUNT(DISTINCT transaction_type) FILTER (
            WHERE COALESCE(billable_at, usage_billable_at, created_at) >= CURRENT_DATE - INTERVAL '180 days'
        ) AS direct_product_types_180d,
        COUNT(DISTINCT location_id) FILTER (
            WHERE COALESCE(billable_at, usage_billable_at, created_at) >= CURRENT_DATE - INTERVAL '365 days'
        ) AS direct_active_locations_365d,
        MAX(COALESCE(billable_at, usage_billable_at, completed_at, created_at)) AS direct_last_activity_at,
        MIN(COALESCE(contract_start_date, created_at::date)) AS direct_first_contract_or_activity_date,
        MAX(contract_end_date) AS direct_latest_contract_end_date,
        BOOL_OR(_contract_match_type IN ('inferred_latest_active_contract', 'inferred_contract_gap')) AS direct_has_inferred_contract_gap
    FROM transaction_base
    WHERE direct_sfdc_account_id IS NOT NULL
    GROUP BY 1
),

billing_rollup_usage_features AS (
    SELECT
        billing_sfdc_account_id AS sfdc_account_id,
        COUNT(*) FILTER (
            WHERE COALESCE(billable_at, usage_billable_at, created_at) >= CURRENT_DATE - INTERVAL '90 days'
        ) AS billing_rollup_txn_90d,
        COUNT(*) FILTER (
            WHERE COALESCE(billable_at, usage_billable_at, created_at) >= CURRENT_DATE - INTERVAL '180 days'
        ) AS billing_rollup_txn_180d,
        COUNT(*) FILTER (
            WHERE COALESCE(billable_at, usage_billable_at, created_at) >= CURRENT_DATE - INTERVAL '365 days'
        ) AS billing_rollup_txn_365d,
        COUNT(*) FILTER (
            WHERE is_billable
              AND COALESCE(billable_at, usage_billable_at, created_at) >= CURRENT_DATE - INTERVAL '180 days'
        ) AS billing_rollup_billable_txn_180d,
        COUNT(DISTINCT direct_sfdc_account_id) FILTER (
            WHERE COALESCE(billable_at, usage_billable_at, created_at) >= CURRENT_DATE - INTERVAL '365 days'
        ) AS billing_rollup_operating_accounts_365d,
        COUNT(DISTINCT transaction_type) FILTER (
            WHERE COALESCE(billable_at, usage_billable_at, created_at) >= CURRENT_DATE - INTERVAL '180 days'
        ) AS billing_rollup_product_types_180d,
        COUNT(DISTINCT location_id) FILTER (
            WHERE COALESCE(billable_at, usage_billable_at, created_at) >= CURRENT_DATE - INTERVAL '365 days'
        ) AS billing_rollup_active_locations_365d,
        MAX(COALESCE(billable_at, usage_billable_at, completed_at, created_at)) AS billing_rollup_last_activity_at
    FROM transaction_base
    WHERE billing_sfdc_account_id IS NOT NULL
    GROUP BY 1
),

billing_features AS (
    SELECT
        LOWER(billing_id) AS sfdc_account_id,
        SUM(billable_count) FILTER (
            WHERE billable_month >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '12 months'
        ) AS billing_billable_count_12m,
        SUM(total_price) FILTER (
            WHERE billable_month >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '12 months'
        ) AS billing_usage_price_12m,
        SUM(total_fee) FILTER (
            WHERE billable_month >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '12 months'
        ) AS billing_usage_fee_12m,
        COUNT(DISTINCT billable_month) FILTER (
            WHERE COALESCE(billable_count, 0) > 0
              AND billable_month >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '12 months'
        ) AS billing_active_months_12m,
        BOOL_OR(customer_exception_type = 'churned') AS billing_has_churned_exception_24m,
        BOOL_OR(customer_exception_type = 'churned_no_billable') AS billing_has_churned_no_billable_exception_24m,
        BOOL_OR(customer_exception_type = 'failed_contract_join') AS billing_has_failed_contract_join_24m,
        BOOL_OR(customer_exception_type = 'missing_plan_charge') AS billing_has_missing_plan_charge_24m,
        MAX(subscription_status) AS latest_subscription_status,
        MAX(current_term_start_date) AS latest_current_term_start_date,
        MAX(current_term_end_date) AS latest_current_term_end_date
    FROM dbt_report.rpt_billing_usage_monthly
    WHERE billing_id IS NOT NULL
      AND billable_month >= DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '24 months'
    GROUP BY 1
),

opportunity_features AS (
    SELECT
        LOWER(account_id) AS sfdc_account_id,
        COUNT(*) FILTER (WHERE is_closed) AS closed_opp_count,
        COUNT(*) FILTER (WHERE is_won) AS closed_won_opp_count,
        COUNT(*) FILTER (WHERE type ILIKE '%renewal%') AS renewal_opp_count,
        MAX(close_date) FILTER (WHERE is_won) AS last_closed_won_date,
        MAX(annual_recurring_revenue_c) FILTER (WHERE is_won) AS max_closed_won_arr,
        MAX(monthly_recurring_revenue_c) FILTER (WHERE is_won) AS max_closed_won_mrr,
        MAX(monthly_closing_volume_c) FILTER (WHERE is_won) AS max_closed_won_mcv,
        MAX(renewal_amount_c) AS max_renewal_amount,
        BOOL_OR(at_risk_c) AS has_at_risk_opp,
        MAX(at_risk_amount_c) AS max_at_risk_amount,
        MAX(notice_to_not_renew_c) AS latest_notice_to_not_renew_date,
        STRING_AGG(DISTINCT primary_churn_reason_c, '; ' ORDER BY primary_churn_reason_c)
            FILTER (WHERE primary_churn_reason_c IS NOT NULL) AS churn_reasons
    FROM analytics.salesforce_opportunity
    WHERE account_id IS NOT NULL
      AND NOT COALESCE(is_deleted, FALSE)
    GROUP BY 1
)

SELECT
    account.sfdc_account_id,
    account.sfdc_account_name,
    account.sfdc_account_type,
    account.customer_status_c,
    account.account_status_c,
    account.certif_id_customer_segment_c,
    account.customer_health_score_c,
    account.churn_count_c,
    account.churn_date_c,
    account.next_renewal_date_c,
    account.active_subscription_revenue_c,
    account.total_contract_value_c,
    account.annual_license_c,
    account.total_revenue_c,
    account.number_of_offices_c,
    account.number_of_employees_c,
    account.number_of_users_c,
    account.sfdc_parent_id,
    hierarchy.direct_parent_id,
    hierarchy.highest_parent_id,
    hierarchy.is_parent_billed,
    hierarchy.bill_parent_start_date,
    hierarchy.parent_name,
    hierarchy.paymints_identifier,
    hierarchy.csm_name,
    hierarchy.customer_segment AS prep_customer_segment,
    hierarchy.title_production_software,
    hierarchy.account_plan_tier,
    hierarchy.committed_transaction_billing_type,
    hierarchy.is_file_based_pricing,
    hierarchy.is_customer_migrated_to_new_pricing,
    direct.direct_txn_90d,
    direct.direct_txn_180d,
    direct.direct_txn_365d,
    direct.direct_billable_txn_180d,
    direct.direct_active_months_365d,
    direct.direct_product_types_180d,
    direct.direct_active_locations_365d,
    direct.direct_last_activity_at,
    direct.direct_first_contract_or_activity_date,
    direct.direct_latest_contract_end_date,
    direct.direct_has_inferred_contract_gap,
    billing_rollup.billing_rollup_txn_90d,
    billing_rollup.billing_rollup_txn_180d,
    billing_rollup.billing_rollup_txn_365d,
    billing_rollup.billing_rollup_billable_txn_180d,
    billing_rollup.billing_rollup_operating_accounts_365d,
    billing_rollup.billing_rollup_product_types_180d,
    billing_rollup.billing_rollup_active_locations_365d,
    billing_rollup.billing_rollup_last_activity_at,
    billing.billing_billable_count_12m,
    billing.billing_usage_price_12m,
    billing.billing_usage_fee_12m,
    billing.billing_active_months_12m,
    billing.billing_has_churned_exception_24m,
    billing.billing_has_churned_no_billable_exception_24m,
    billing.billing_has_failed_contract_join_24m,
    billing.billing_has_missing_plan_charge_24m,
    billing.latest_subscription_status,
    billing.latest_current_term_start_date,
    billing.latest_current_term_end_date,
    opp.closed_opp_count,
    opp.closed_won_opp_count,
    opp.renewal_opp_count,
    opp.last_closed_won_date,
    opp.max_closed_won_arr,
    opp.max_closed_won_mrr,
    opp.max_closed_won_mcv,
    opp.max_renewal_amount,
    opp.has_at_risk_opp,
    opp.max_at_risk_amount,
    opp.latest_notice_to_not_renew_date,
    opp.churn_reasons
FROM account_base AS account
LEFT JOIN hierarchy
    ON account.sfdc_account_id = hierarchy.sfdc_account_id
LEFT JOIN direct_usage_features AS direct
    ON account.sfdc_account_id = direct.sfdc_account_id
LEFT JOIN billing_rollup_usage_features AS billing_rollup
    ON account.sfdc_account_id = billing_rollup.sfdc_account_id
LEFT JOIN billing_features AS billing
    ON account.sfdc_account_id = billing.sfdc_account_id
LEFT JOIN opportunity_features AS opp
    ON account.sfdc_account_id = opp.sfdc_account_id
