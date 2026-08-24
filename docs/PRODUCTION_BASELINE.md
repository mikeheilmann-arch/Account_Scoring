# Production baseline

## Sales Prioritization V1.1

The first broad production publication completed on July 10, 2026.

- Final model/run: `sales_prioritization_v1_1_entity_guardrailed_20260710T220000Z`
- Final live run count at publication: 16,822 Accounts
- Canary: 50 submitted, 50 verified, 0 failed
- Remaining publication: 16,772 submitted, 16,772 successful, 0 failed
- Fresh source conflicts quarantined: 43
- Readback field mismatches: 0
- `Account.Website` writes or clears: 0
- Salesforce report: `Sales Prioritization V1.1 - Current Run`

The publication sequence was:

1. Retain previously accepted V1 values.
2. Recover directionally useful title/escrow and legal scores.
3. Apply top-rank legal, anchor, foreign-domain, and shared-domain controls.
4. Re-query candidate Accounts and quarantine source conflicts.
5. Capture exact current-state backup.
6. Publish a 50-row canary.
7. Verify exact Salesforce readback.
8. Publish the remaining changed-ID-only population.
9. Verify final counts and field equality.

Detailed contemporaneous evidence is retained in:

- `sales_prioritization_v1_1_population_audit_2026-07-10.md`
- `sales_prioritization_v1_1_top_rank_guardrail_review_2026-07-10.md`
- `sales_prioritization_v1_1_entity_controls_and_canary_2026-07-10.md`
- `sales_prioritization_v1_1_final_publication_2026-07-10.md`

Those documents describe a historical release. Before any future publication, refresh live source data and produce new immutable backup, conflict, canary, readback, and rollback artifacts. Never reuse the historical payloads or rollback guards.

## Current semantic contract

- MCV estimates potential monthly closing capacity.
- ARR estimates directional pipeline potential, not booked or forecast ARR.
- Confidence describes evidence quality, not intent or likelihood to buy.
- Only the surviving sellable Account should receive a score.
- Lifecycle contradictions, duplicate losers, child rollups, wrong websites, and non-ICP entities must be suppressed or routed for review.
- The publisher must not write `Account.Website`.
