# Sales Prioritization V1.1 — No-Write Population Audit

## Review decision

**REVIEW REQUIRED — PUBLICATION HOLD.** V1.1 passes the 10,000-row minimum but produces 16,924 scored Accounts, which is 2,924 above the expected 12,000–14,000 range. No Salesforce write was executed. Oversight should review the expanded coverage before authorizing a canary.

- Run ID: `sales_prioritization_v1_1_20260710T202841Z`
- Model version: `sales_prioritization_v1_1_20260710`
- Full snapshot population: 21,993
- V1.1 scored population: 16,924
- Excluded population: 5,069
- Existing production V1 rows retained exactly: 3,001
- Newly recovered rows: 13,923
- Salesforce writes during V1.1 audit/staging: 0

## Why coverage is above the expected range

The required rules recover rows from several additive populations. The model did not tune eligible rows away merely to land inside the expected range.

- 4,982 of 5,426 prior `insufficient_public_evidence` rows recovered.
- 2,725 of 2,796 prior `manual_review` rows recovered.
- 1,057 of 1,187 prior website-derived `non_icp_confirmed` rows recovered after CRM/entity fallback.
- 2,931 preflight rows without a scorer result recovered from CRM/ALTA/name/attorney-state context.
- 2,184 prior `score_now` rows outside the current 3,001 recovered under V1.1.
- 44 prior hygiene-review rows recovered without changing `Account.Website`.

V1.1 also reverses unsupported V1 suppressions:

| Prior V1 primary exclusion | Prior rows | Recovered | Still excluded |
|---|---:|---:|---:|
| Confirmed website/entity mismatch | 878 | 854 | 24 |
| Confirmed adjacent/non-ICP entity | 1,488 | 783 | 705 |
| Non-closing legal | 145 | 145 | 0 |
| Abstract-only | 124 | 124 | 0 |
| Underwriter label | 32 | 32 | 0 |
| Owned-direct label | 9 | 9 | 0 |
| Brokerage-affiliated label | 5 | 5 | 0 |
| Active customer | 2 | 0 | 2 |

The underwriter, owned-direct, brokerage-affiliated, abstract-only, and non-closing labels were recovered only when the old classification was not backed by a verified non-independent relationship or a High-confidence correct entity binding. Explicit CRM non-ICP classifications remain excluded.

## Score-source hierarchy

| Source tier | Accounts | Confidence behavior | MCV median | MCV P90 |
|---|---:|---|---:|---:|
| Tier 0 — retained current V1 | 3,001 | Preserve existing | 62 | 125 |
| Tier 1 — trusted anchor | 952 | High for recent Opportunity; Medium for trusted Final MCV | 30 | 200 |
| Tier 2 — usable bound website score | 99 | Preserve scorer evidence confidence | 62 | 125 |
| Tier 3 — CRM/ALTA/entity fallback | 5,301 | Medium with multiple signals; otherwise Low | 50 | 65 |
| Tier 4 — retrieval/preflight cohort default | 7,571 | Low | 40 | 50 |

Tier 1 includes 96 recent closed-won Opportunity anchors, 833 trusted Final MCV anchors, and 23 anchors capped by the 750-MCV title-lane plausibility rail. Tier 3 uses the retained V1 cohort's 40th percentile. Tier 4 uses the lower quartile. ARR uses the existing directional pipeline-potential ladder.

## Lane and confidence distribution

| Dimension | Value | Accounts |
|---|---|---:|
| Lane | Title/escrow | 8,547 |
| Lane | Legal | 8,377 |
| Confidence | High | 191 |
| Confidence | Medium | 6,328 |
| Confidence | Low | 10,405 |

The confidence mix is intentionally conservative: 61.5% of V1.1 rows are Low confidence, primarily retrieval/preflight defaults.

## Exclusions retained

| Exclusion reason | Accounts |
|---|---:|
| Active customer | 2,539 |
| Explicit CRM non-ICP status | 760 |
| High-confidence bound non-ICP website | 703 |
| Insufficient positive fallback context | 849 |
| Explicit CRM non-ICP company type | 181 |
| Confirmed partner | 37 |

No ambiguous, insufficient, or mismatched website directly produces a non-ICP exclusion. Website-derived non-ICP exclusion requires both High-confidence `bound` status and a High-confidence adjacent classification.

## Salesforce staging

The read-only staging sweep queried current production Account state and created a changed-ID-only package.

- Population audit candidates: 16,924
- Current source-conflict IDs excluded from staging: 59
- Changed-ID-only staged population: 16,865
- Canary: 50 rows, staged but not executed
- Readback: not executed
- Rollback values: staged but deliberately non-executable until filtered to successful write IDs and protected by Run ID plus `SystemModstamp` compare-and-swap

The 59 source conflicts include one deleted/missing Salesforce Account and one retained V1 Account whose Billing State changed from Georgia to California and Company Type changed from Law firm to Escrow company. The audit still retains all 3,001 existing scores; the current staging package includes 3,000 and routes the changed retained Account for re-evaluation.

V1.1 has 51 non-empty source-tier × lane × confidence × MCV-band cells. The 50-row canary represents 50 cells and omits one one-row retained-legal-High/high-MCV cell; High confidence, legal lane, retained source tier, and high MCV are each represented elsewhere in the canary.

## Controls

- Exact 21,993-row decision reconciliation: passed
- Exact current 3,001 numeric retention: passed; zero numeric mismatches
- Minimum 10,000-row coverage gate: passed
- Account Website excluded from payload: passed
- No clear rows: passed
- Positive MCV and ARR point for every scored row: passed
- Fresh Salesforce schema/picklist validation: passed
- Test suite: 94 passed
- No-write audit manifest and staging manifest hash verification: passed
- Publication authorization: false
