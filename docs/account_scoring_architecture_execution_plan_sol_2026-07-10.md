# CertifID Account Scoring Architecture Execution Plan

Date: 2026-07-10
Status: production-critical V1 implemented and full cached shadow completed; **not CanaryReady**; no deployment or write authorized
Companion review: `docs/account_scoring_architecture_review_sol_2026-07-10.md`

## 2026-07-10 V1 implementation checkpoint

Authoritative immutable candidate: `tmp/account_scoring_v2_shadow_20260710_r3/`
Run ID: `account_scoring_v1_shadow_20260710T180322Z`
Code hash: `aa50111550757cfb61936bec358f975e876ea8162213b8b159e6d0bfbe396fb1`
Release state: **not CanaryReady**

The shortest production-critical V1 was implemented, tested, and run against the full cached July universe. No Salesforce write, Salesforce metadata deployment, Account.Website change, new Nimble extraction, GCP job/service execution, or database mutation occurred. The only external operations were read-only retrieval of the existing July GCS cache and a read-only current Account describe.

Full-universe reconciliation and routing:

- 21,993 input Accounts; 21,993 decisions; 21,993 unique IDs; zero missing, unexpected, blank, or duplicate IDs.
- 15,384 original extracted inputs; 9,772 have usable locally materialized cached pages; no recrawl occurred.
- 6,609 original preflight-held Accounts remain held/no-change.
- 0 accepted; 14,261 review; 1,123 other no-change; the four buckets reconcile to 21,993.
- 2,684 bound websites; 3,406 ALTA entity confirmations; zero ALTA-only website bindings.
- 10,291 legal-lane, 8,869 title/escrow-lane, 2,417 adjacent, and 416 review-lane decisions.
- Every one of the 9,933 CRM `Company_Type__c = Law firm` rows entered the legal lane.
- 850 lane-eligible analytical candidates (573 title/escrow and 277 legal) remain nonaccepted because release-level model, evidence, Finance, and approval gates are not complete.

Validation state:

- 87 package tests pass.
- Integrated external controls pass: 21/21 entity-binding cases, 17/17 lifecycle/sellable-unit cases, 12/12 lane cases, and the entity-safe feature control.
- Release gates: 16 PASS, 1 FAIL, 8 PENDING.
- The failed gate is `actual_schema_canary_25_to_50`: the actual Salesforce-schema file has zero rows because no candidate is release-eligible. It is intentionally not padded or manufactured.
- Pending gates: protected blind entity/ICP holdout; human acceptance-precision audit; top-100 outlier audit; point-in-time-valid MCV backtest; Finance-approved ARR formula/calibration; fresh exact-ID source/SystemModstamp conflict check; non-human integration-user FLS/permissions; and business/RevOps/model/Finance approvals.

Model readout (analysis only, not release-valid):

- MCV protected group/time partition: 745 holdout rows, zero group overlap; external anchor-free MAE 28.255, median absolute error 10.121, bias -9.633, bucket accuracy 51.8%, P10-P90 coverage 79.7%; 29.2% MAE lift versus the office-only baseline. Current July features are not point-in-time observations at historical label dates, and the history-assisted holdout has zero evaluable timestamped priors, so the MCV release gate remains pending.
- ARR: 18 current hierarchy-resolved clean-new-logo proxy comparables; Finance formula unavailable/unapproved; holdout has zero evaluable clean comparables; P75 and P50-P90 calibration therefore remain unavailable and provisional.
- Source coverage is 9,772/21,993 (44.4%) with fresh usable cached pages. Office-count PSI versus the permissive legacy feature is 2.220, correctly reported as material drift after entity-safe exclusions.

Workstream/phase status:

| Area | Status | Honest completion boundary |
|---|---|---|
| W0 / Phase 0 definitions and governance | **Pending** | Provisional defaults are encoded, but no signed Finance/legal/RevOps charter or release thresholds were manufactured. |
| W1 / Phase 1 canonical package and contracts | **Implemented for local V1 shadow** | Cloud package is canonical; local duplicate scorers are thin imports; typed/versioned contracts, snapshot validation, and hashes exist. The deployable default entrypoint still invokes the frozen legacy batch path, so Cloud V1 runtime cutover is pending. |
| W2 / Phase 2 entity and sellable unit | **Implemented; human calibration pending** | Generalized resolver, ALTA/registry corroboration, lifecycle, hierarchy, domain-cluster, customer/winback, and no-standalone rules run across all 21,993 rows. Protected human labels are pending. |
| W3 / Phase 3 explicit lanes | **Implemented; business policy approval pending** | Law-firm forced routing and explicit title/legal/adjacent subtypes are implemented and externally fixture-tested. |
| W4 / Phase 4 entity-safe evidence and MCV | **Implemented provisionally** | Entity-safe office/staff features, separate title/legal quantile models, intervals, group/time split, and baselines exist. Point-in-time release validation and usable history-assisted holdout remain pending. |
| W5 / Phase 5 potential ARR | **Implemented provisionally** | Clean-new-logo filtering, hierarchy resolution, P75/P50-P90 interface, shrinkage/fallback, and comparable references exist. Finance definition and evaluable calibration holdout are pending. |
| W6 / Phase 6 independent evaluation | **Full shadow complete; gate not passed** | Exact reconciliation and automated safety controls pass, but blind/human/model/Finance gates are pending and the canary-count gate fails. |
| W7 runtime/provenance | **Local no-write runtime implemented** | Immutable manifests, source/config/code/model versions, and artifact hashes exist. No GCP job/service was changed or executed. |
| W8 Salesforce publisher/rollback | **Safety library and non-executable package implemented** | Accepted-only/no-clear, changed-only enforcement, actual schema validation, explicit null semantics, success ledger, readback verifier, and RunId/SystemModstamp rollback CAS exist. Exact backup/readback/rollback artifacts cannot be executable until accepted IDs, a fresh live diff, and the non-human identity exist. |
| W9 canary/rollout | **Not started** | Canary approval is inappropriate. |

Implementation disposition:

- **Retained:** isolated Cloud package/GCS foundation, July cache, Account/overlay/ALTA sources, Salesforce `AI_Prospect_*` namespace, existing reporting system, and frozen legacy scorer as a characterized baseline.
- **Replaced/refactored:** unsafe monolithic decision core with typed snapshot, binding, sellable-unit, lane, feature, MCV, ARR, evaluation, and publication modules.
- **Retired as an independent source:** local duplicate scorer logic; `scripts/run_greenfield_nimble_test.py` and `scripts/legal_entity_scoring.py` are compatibility imports only.
- **Not reproduced:** the unsafe all-row Salesforce payload. Desired-state diff and canary remain header-only because no accepted rows exist.

Earlier `tmp/account_scoring_v2_shadow_20260710/` and `_r2/` runs remain immutable superseded diagnostics. `_r3/` is the authoritative handoff candidate; no original run artifact was overwritten.

## Outcome

Refactor the existing Account Scoring system into a staged, entity-safe, independently evaluated pipeline without discarding the working Cloud Run/GCS extraction foundation, cached July evidence, Salesforce AI field namespace, registry assets, or customer-history work.

The first production objective is deliberately narrow:

> Publish only independently validated, high-confidence, net-new, entity-bound scores through an accepted-only/no-clear canary and progressive rollout.

Automated hard suppression/field clearing, scheduled publication, and broader review-state publication follow only after exact QA/run governance is in place.

## Non-negotiable constraints

- No Salesforce writes or metadata deployments until the canary phase is separately approved.
- No `Account.Website` updates.
- No new full Nimble extraction during the refactor and cached full-universe shadow.
- No GCP job execution until the relevant runtime phase is approved.
- Salesforce remains the canonical reporting and latest-approved publication surface.
- Production decision code and evaluation truth remain independent.
- No stage may infer missing required inputs from a permissive default.

## Planning assumptions and effort range

Effort assumes:

- one technical owner for pipeline/runtime;
- one data/model owner for labels, MCV, ARR, and evaluation;
- one RevOps/business owner for entity/lifecycle/legal policy and Salesforce acceptance;
- part-time Finance and legal-market subject-matter review; and
- prompt access to labelers and approvers.

Estimated effort:

- **accepted-only/no-clear canary:** approximately 16-24 person-weeks, or 6-8 calendar weeks with three coordinated contributors;
- **complete production runtime including governed review/suppression publication:** approximately 24-34 person-weeks, or 9-13 calendar weeks;
- add 2-4 weeks if golden-label turnaround, Finance ARR definition, legal-state policy, or Salesforce governance metadata is delayed.

These are architecture-level estimates, not commitments. They assume reuse of cached evidence and the existing GCP/Salesforce assets.

## Workstreams

| ID | Workstream | Primary owner | Main output |
|---|---|---|---|
| W0 | Business definitions and governance | Mike/RevOps + Finance + legal-market owner | Signed scoring/release charter |
| W1 | Source contracts and canonical entity/sellable-unit model | Data + engineering | Typed snapshot/entity/evidence/score contracts |
| W2 | Entity binding, hierarchy, customer, and duplicate resolution | Engineering + Ops labelers | Canonical resolver and review queue |
| W3 | Title/escrow, legal, and adjacent lane classifiers | Data/model + business SMEs | Lane-specific eligible populations |
| W4 | Evidence features and MCV estimation | Data/model + engineering | External and history-assisted MCV quantiles |
| W5 | Potential ARR and customer-quality calibration | Data/model + Finance | Comparable-based potential ARR |
| W6 | Independent evaluation and release gates | Independent reviewer/data QA | Blind holdout and population audit package |
| W7 | Runtime, provenance, observability, and cost | Platform/engineering | Immutable staged Cloud Run workflow |
| W8 | Salesforce desired-state publication and rollback | RevOps/Salesforce + engineering | Safe publisher, backup, readback, rollback |
| W9 | Canary, progressive rollout, and operations | Mike/RevOps + release owners | Approved production release and runbook |

## Dependency map

| Workstream | Hard dependencies | Can overlap with |
|---|---|---|
| W0 definitions/governance | None | Initial fixture protocol and baseline inventory |
| W1 contracts/entity model | W0 canonical decisions | Runtime manifest design, Salesforce governance design |
| W2 entity/sellable resolver | W1 schemas; label protocol | Runtime foundations; point-in-time data preparation |
| W3 lane classifiers | W2 binding/sellable-unit contract; legal policy | Title and legal lanes in parallel |
| W4 MCV | W3 feature/lane freeze; dated MCV labels | Title/legal estimators in parallel; late W5 |
| W5 ARR/customer quality | W0 ARR definition; W2 hierarchy; Finance formula | W4 after feature grain stabilizes |
| W6 independent evaluation | Frozen outputs from W2-W5; protected labels | Population/runtime audit preparation |
| W7 runtime | W1 schemas/version registry | W2-W5 model work |
| W8 Salesforce publisher | W1 desired-state schema; W7 run identity; Salesforce governance choice | Late W4/W5 |
| W9 canary/rollout | W6 `CanaryReady`; W8 exact backup/readback/rollback | Nothing may bypass these dependencies |

## Phase plan

### Phase 0 — Freeze, definitions, and ownership

Estimated effort: 2-3 business days; 0.5-1 person-week.

Entry:

- Architecture review delivered.
- Current 5,170-row accepted population remains unapproved.

Steps:

1. Declare broad Account Scoring writes/clears frozen until Phase 6 exits.
2. Name one owner each for entity/hierarchy policy, legal-market policy, MCV labels, Finance ARR, Salesforce release, and GCP runtime.
3. Approve the canonical grain: independently sellable buying/contracting unit with one surviving Salesforce Account publication key.
4. Approve lifecycle lanes: net-new, winback, current-customer/expansion, partner, and excluded.
5. Approve title/legal/adjacent lane boundaries and owned/direct handling.
6. Approve potential ARR semantics: recommended P75 attainable first-year recurring revenue conditional on winning a standard new-logo deal; P50-P90 range; no win/timing probability.
7. Name the Finance-owned first-year ARR formula and acceptable product/pricing motions.
8. Approve the field-metadata wording that will replace the current ARR “ceiling” description before a P75 canary, or choose a ceiling-compatible definition.
9. Pre-commit blind-holdout and canary thresholds before tuning, including valid-green recall, auto-resolution coverage, and maximum review rate by lane/source-quality stratum.
10. Approve the evidence TTL/freshness policy and the conditions for a separately authorized bounded refresh.
11. Approve accepted-only/no-clear as the first canary posture, or explicitly choose to wait for additive governance metadata.

Exit gate:

- A short, versioned decision charter is signed by Mike, RevOps, Finance for ARR, and the legal-market owner.
- Every decision above has one accountable owner and no “to be inferred in code” item remains.
- Release thresholds are recorded before holdout results are visible.

Blocking dependencies:

- Mike's decisions listed at the end of this plan.
- Finance formula owner.
- Legal state/county policy owner.

### Phase 1 — Single source, contracts, and characterized baseline

Estimated effort: 1 calendar week; 1.5-2.5 person-weeks.

Entry:

- Phase 0 charter approved.
- July source snapshots and cached artifacts remain available and immutable.

Steps:

1. Select the Cloud package as the sole production scorer source and archive/hash the local script as a legacy comparison.
2. Define versioned schemas for:
   - source snapshot;
   - entity and aliases;
   - website binding;
   - sellable unit and lifecycle;
   - evidence snapshot;
   - lane classification;
   - MCV estimates;
   - potential ARR;
   - release evaluation; and
   - Salesforce desired state.
3. Make all load-bearing fields required or explicitly nullable. Ban implicit conversions such as blank hygiene to `confirmed`.
4. Define source freshness, as-of timestamps, joins, and field-level provenance.
5. Freeze a legacy baseline from the July full run: all input/output counts, source distributions, actions, MCV/ARR distributions, known failure examples, and runtime metrics.
6. Record SHA-256 hashes for raw inputs, overlays, combined scores, and cached evidence inventory.
7. Create an explicit version registry for resolver, feature, lane, MCV, ARR, config, code/container, and publication contracts.

Tests:

- required-column deletion tests fail closed;
- type/null/enum contract tests;
- duplicate/missing/unexpected ID tests;
- `Company_Type__c`, lifecycle, parent, and ALTA membership semantics tests;
- public-suffix and generic-host domain tests; and
- local-versus-Cloud scorer characterization tests.

Exit gate:

- One authoritative package path exists.
- A July baseline can be reproduced without vendor or Salesforce calls.
- Removing or renaming any required source field stops before extraction/scoring.
- No `ALTA_Member=false` row can be represented as positive ALTA evidence.
- All artifacts have hashes and schema versions.

Reuse:

- July Account snapshot, overlay, combined scorer output, raw cache, manifests, and audits.
- Cloud package and dedicated GCP isolation boundary.

Retire at exit:

- the local scorer as an independent production source;
- permissive defaulting in scorer input; and
- undocumented schema drift.

### Phase 2 — Independent fixtures plus entity/sellable-unit resolver

Estimated effort: 2-3 calendar weeks; 4-6 person-weeks.

Entry:

- Phase 1 schemas/version registry complete.
- Labeling protocol and label owners named.

Steps:

1. Build a 500-800-row entity/hierarchy labeled set stratified across:
   - correct entity/site;
   - unrelated same-name and wrong-site;
   - bank/credit union/mortgage/lender;
   - brokerage/builder/1031;
   - government/education/association/directory/generic host;
   - international lookalike;
   - underwriter/owned-direct;
   - title/escrow, legal, and adjacent positives;
   - parent/child/branch/DBA;
   - duplicate/shared domain; and
   - active customer/churned/winback.
2. Keep 20-30% as a protected blind holdout. Split by entity, root domain, brand/underwriter family, and duplicate cluster.
3. Place fixtures outside production config. Production code must not branch on fixture Account names.
4. Implement public-suffix-aware URL/domain normalization and generic/shared-host classification.
5. Resolve legal name, aliases/DBAs, site organization/name, contact/location, CRM, ALTA, registry, and hierarchy evidence into a `WebsiteBinding` decision.
6. Implement `SellableUnit` relationships with explicit relationship types and confidence.
7. Incorporate active subscription/current-customer evidence and separate churned/winback from net-new.
8. Emit deterministic high-confidence accept/reject and a bounded review lane. There is no residual default accept.
9. Store all evidence and conflicts with source and as-of date.

Tests:

- every named historical control as regression only, not production branch logic;
- unseen negative categories and same-name adversaries;
- ALTA true/false/low-confidence and name/state mismatch;
- shared generic host tenants;
- parent with independent subsidiary and parent with non-independent branch;
- active customer, former customer, parent-billed child, and duplicate survivor;
- public-suffix/domain edge cases; and
- metamorphic tests: irrelevant page text or row ordering cannot change identity.

Recommended exit gates on the blind holdout:

- high-confidence website-binding precision >=98%; lower 95% confidence bound >=95%;
- zero fatal wrong-site controls accepted;
- zero active customers, verified duplicates, or non-independent children retained as standalone net-new units;
- valid-green recall >=90% on evidence-complete positives;
- auto-decision coverage >=70% for the evidence-complete title/escrow stratum and >=50% for evidence-complete legal, with lane-specific review rates below their precommitted ceilings;
- 100% output traceability to source evidence;
- ambiguous cases route to review; and
- full July population reconciles to one resolver output per Account and one mapping per sellable unit.

Reuse:

- `run_vertical_resolver_poc.py` name/host/entity features;
- ALTA matches and enrichment;
- VA SCC/CA DFPI and legal discovery evidence;
- dedupe artifacts, ParentId, customer-history hierarchy, and website hygiene artifacts.

Replace/retire at exit:

- `post_retrieval_quality_gate.py` as entity-binding logic;
- named production controls and short domain denylists as the primary resolver;
- domain-only customer/duplicate proof; and
- `Account` row equals economic unit assumptions.

### Phase 3 — Explicit lanes and entity-bound evidence features

Estimated effort: 2-3 calendar weeks; 4-6 person-weeks. Title and legal work can proceed in parallel after shared contracts stabilize.

Entry:

- Phase 2 resolver meets blind binding gates.
- Versioned legal-state/county policy table approved.

Steps:

1. Build an explicit adjacent/exclusion taxonomy for bank/lender, brokerage/builder, 1031-only, government, education, insurance/public adjuster, vendor, competitor, association/directory, underwriter corporate, and international entities.
2. Build the title/escrow classifier: title agent, escrow/settlement operator, abstract-with-closing, abstract-only, underwriter, owned/direct, mixed, adjacent, ambiguous.
3. Refactor the legal classifier: all CRM law firms enter; closing-focused, affiliated title, unclear RE practice, general practice, market review, and non-ICP remain distinct.
4. Move state/county rules into owner-approved, effective-dated config.
5. Replace office extraction with normalized address entities and office-role classification. Deduplicate across pages and exclude service areas, property listings, courthouses, partner sites, and licensed-state lists.
6. Refactor staff features to relevant closing/title/legal roles with cross-page dedupe.
7. Separate service dominance/operational evidence from generic real-estate terms.
8. Use website quality only as extraction/evidence confidence, not positive volume.
9. Emit evidence citations and unknown/conflict states.

Tests:

- title-versus-abstract-only and underwriter/direct cases;
- valid escrow operators and false title-like service pages;
- legal closing-focused versus BigLaw/general practice;
- attorney-state/county cases and weak real-estate wording;
- adjacent entities with title/closing words;
- unique office precision/recall fixture;
- staff-role precision fixture; and
- all 9,933 CRM law firms pass through legal-lane classification before score eligibility.

Recommended exit gates:

- zero CRM law-firm bypass;
- high-confidence `score_now` lane precision >=97% on blind holdout;
- zero fatal adjacent controls eligible;
- valid-green recall >=90% on evidence-complete positives, with auto-score/review coverage meeting the Phase 0 lane-specific thresholds;
- office extraction precision >=95% and duplicate/service-area false counts zero on the critical top-value fixture;
- high-confidence legal auto-score precision >=95%; and
- every accepted capacity feature cites bound evidence.

Reuse:

- `legal_entity_scoring.py` vocabulary/state taxonomy/operational-evidence concepts;
- current service/tool keywords after source review; and
- cached July pages.

Replace/retire at exit:

- `is_icp_likely()` any-service fallback;
- current address-string/location-slug office count;
- unvalidated legal MCV floors; and
- website-quality-as-capacity behavior.

### Phase 4 — MCV, potential ARR, and customer-quality calibration

Estimated effort: 3-4 calendar weeks; 6-9 person-weeks. Title MCV, legal MCV, ARR cohort work, and point-in-time customer features can run in parallel after Phase 3 feature contracts.

Entry:

- Phase 3 lanes and entity-bound feature set frozen for the calibration version.
- Finance new-logo first-year ARR formula approved.
- Blind evaluation splits frozen.

Steps for MCV:

1. Build point-in-time Account/Opportunity MCV observations with source, observation date, motion, and confidence.
2. Build two estimators per lane:
   - `ExternalCapacityMCV`, with all Account/Opportunity anchors removed;
   - `HistoryAssistedMCV`, combining the external estimate with a dated prior.
3. Use robust monotonic quantile/ordinal baselines first; compare more complex models only on the protected holdout.
4. Estimate P10/P50/P90 and retain source conflicts.
5. Learn source reliability/recency weights; do not use `max()` or fixed 2x rails as the final estimator.
6. Route material independent-estimate conflict to review.

Steps for potential ARR:

1. Build a hierarchy-resolved Closed Won New Business cohort only.
2. Apply the Finance formula and normalize product/pricing era, pricing motion, subsidies/exceptions, and first-year period.
3. Exclude renewals, extensions, expansions, duplicates/children, bad joins, and tune/test overlap.
4. Derive lane/MCV/market/product comparable cohorts.
5. Estimate shrunk P75 point and P50-P90 range; record comparable count and shrinkage level.
6. Use healthy-retained and churn/quality cuts diagnostically, not as the only ARR cohort.

Steps for customer quality:

1. Rebuild customer-history features as point-in-time snapshots.
2. Correct chronological subscription status and motion-specific Opportunity aggregates.
3. Define separate outcomes such as 90-day activation, months 4-12 consumption realization, 12-month healthy/active status, and first-renewal retention.
4. Do not place post-sale outcomes in greenfield ICP/MCV/ARR features.
5. Treat `ExpectedCustomerQuality` and any later `QualityAdjustedPotential` as separate outputs.

Required baselines:

- current static MCV ladder;
- office-only model;
- anchor-only model;
- current static ARR ladder; and
- simple lane/MCV comparable median/quantile.

Tests and metrics:

- group/time split by sellable unit/domain/brand;
- strict anchor-scrubbed title and legal backtests;
- older-anchor-to-later-label history-assisted backtest;
- MCV median absolute log error, MAE/WAPE, exact/adjacent band, interval coverage/width, and bias;
- ARR quantile pinball loss, P50/P75/P90 coverage, top-quartile lift, and lane/state/vintage bias;
- confidence error ordering; and
- leakage audit of every feature against its prediction timestamp.

Exit gates:

- external MCV materially and statistically outperforms office-only on the protected holdout;
- history-assisted MCV outperforms anchor-only without systematic upward bias;
- P10-P90 coverage is within the precommitted acceptable band and high-confidence rows have lower error than medium/low;
- potential ARR passes precommitted quantile coverage/lift and bias limits;
- no renewal/expansion/post-outcome leakage;
- every prediction includes interval, provenance, and version; and
- Mike/Finance sign the P75 definition and output language.

Replace/retire at exit:

- current `estimate_mcv()` ladder as production estimator;
- `Score` as an MCV-band alias;
- current static ARR ladder as production calibration;
- headline anchor-contaminated legal backtest as validation evidence; and
- current-date customer-history SQL for historical training.

Retain as baselines only:

- MCV band table and anchor rails;
- existing legal backtest artifacts; and
- current ARR ladder.

### Phase 5 — Staged runtime and desired-state Salesforce publisher

Estimated effort: 2-3 calendar weeks; 4-7 person-weeks. Can run in parallel with late Phase 4 after schemas are frozen. No production execution in this phase.

Entry:

- Phase 1 contracts stable.
- Resolver/lane/score output schemas stable enough to integrate.
- Salesforce field governance choice approved.

Runtime steps:

1. Split snapshot, resolver, extraction/cache, lane/score, evaluation, publication-plan, publisher, and readback into approval-separated stages.
2. Use immutable `attempt-N` paths and object-generation preconditions.
3. Key evidence cache by normalized URL + input/content/extractor version; validate cached source URL; apply TTL/invalidation.
4. Add input/config/code/container/dependency/provider hashes to manifests.
5. Record request, cache, retry, failure, latency, bytes, and billed-unit/cost metrics.
6. Use stable key-based sharding rather than input ordinal modulo task count.
7. Add per-account and per-run budgets and abort rules.
8. Separate identities: extractor/scorer cannot write Salesforce; publisher has no Nimble secret, metadata deploy, or Website access.

Salesforce publication steps:

1. Define one desired-state row per target Account and an approved operation enum.
2. Add or stage the governance model decision:
   - `AI_Prospect_Value_Run__c` candidate/canary/approved lifecycle;
   - `Account.AI_Prospect_Value_Approved_Run__c` lookup to the run object;
   - exact QA disposition;
   - SellableUnitId/type;
   - input fingerprint; and
   - evidence hash/provisional-review location.
3. Build a publisher that re-reads live source fingerprint/current AI run and rejects stale rows.
4. Validate every payload field through describe: type, updateability, nillability, length, precision, and picklists.
5. Publish only changed IDs.
6. Omit numeric fields from review-only updates; use explicit, tested `#N/A` semantics only for separately approved hard clears.
7. Generate exact-ID live backup, hash, rollback payload, success-ID ledger, and field-normalized readback plan.
8. Update report governance design to Approved run + `action=score_now`.
9. Define the existing-field mapping: publish accepted MCV/ARR and confidence/action/provenance; omit Score, Fit, Timing, Data Confidence, and Rank from the first canary/report. Global Rank is either deprecated or recalculated/re-published only in a full Approved-snapshot rank release.
10. Require an approved ARR field-description/help-text change before publishing P75, because the current metadata says “ceiling.”

Tests:

- retry/collision/idempotency and partial-shard failure;
- changed Website invalidates old cache;
- same input/config/code produces identical desired state;
- stale SystemModstamp/current-run conflict rejects row;
- all field types/lengths/picklists and true null behavior;
- review operation cannot clear numerics;
- publisher cannot update Website or non-AI fields;
- partial-success rollback only touches success IDs whose current RunId matches the failed run; and
- exact readback normalized field by field.

Exit gate:

- end-to-end dry run completes locally/non-production with no external mutation;
- every run/shard/artifact is hash-addressable and attempts do not overwrite;
- source changes invalidate stale publication plans;
- backup/rollback/readback tests prove exact null and compare-and-swap behavior;
- dedicated identity permission design is approved; and
- no fixed-run public report is required for release selection.

Reuse:

- `batch_job.py` structure, GCS, Cloud Run task model, privacy-conscious logs, and current AI fields.

Replace/retire at exit:

- all-row payload;
- blank-string clearing;
- reused raw/post-gate run ID;
- placeholder backup query;
- human CLI alias as production publisher; and
- report filters tied to one historical run ID.

### Phase 6 — Full-universe decision shadow from available cached evidence and independent release audit

Estimated effort: 1-2 calendar weeks; 2-4 person-weeks.

Entry:

- Phases 2-5 pass component gates.
- All candidate versions/configs frozen.
- Cached July evidence inventory complete for the 15,384 scorer-input rows; the 6,609 preflight-held rows and any missing/stale evidence are explicitly routed, not assumed extracted or silently recrawled.

Steps:

1. Run a 21,993-row resolver/decision shadow. Reuse cached evidence where present; preflight-held, missing, unverifiable, or TTL-expired evidence remains held/no-change unless a separately approved bounded refresh exists.
2. Produce one resolver row per Account, one sellable-unit rollup, one score candidate per eligible unit, and one desired-state/no-change row per publication Account.
3. Run protected blind holdouts without changing config.
4. Audit the top 100 accepted Accounts manually.
5. Audit a stratified random accepted sample by lane, confidence, MCV/ARR band, state, anchor source, and evidence quality.
6. Audit all high-value outliers and all changed lifecycle/hierarchy decisions.
7. Compare population distributions and each changed decision to the July baseline.
8. Produce exact live-diff plan, but do not query fresh values or stage execution until final approval if not already authorized.

Required outputs:

- immutable run manifest and artifact hashes;
- entity/binding/sellable-unit decisions;
- accepted scores, review queues, suppressions, and no-change sets;
- blind-holdout results;
- population drift report;
- top-100 and stratified audit labels/findings;
- source freshness/coverage report;
- runtime/cost projection; and
- desired-state publication diff schema.

Exit gate — the **CanaryReady gate**:

- 21,993 input/output Account IDs reconcile exactly; zero duplicate/missing/unexpected IDs;
- zero fatal blind or known controls eligible;
- zero law bypass, active-customer net-new score, duplicate loser, or non-independent child publish;
- entity/lane/MCV/ARR component gates remain met on untouched blind data;
- top-100 accepted manual audit has zero wrong-entity/non-ICP cases;
- stratified high-confidence accepted precision is at least the precommitted threshold;
- all unexplained drift outside precommitted bounds is resolved or the run fails;
- source/evidence/version/hash lineage is complete;
- every accepted candidate's evidence is within the owner-approved TTL and has a verifiable source snapshot; expired evidence, changed redirect/content signals, or unverifiable snapshots are held/no-change unless a separately approved bounded refresh has completed;
- desired-state plan updates only changed IDs and contains no hard clears for the first canary;
- independent evaluator signs the run; and
- Mike, RevOps/Salesforce owner, model owner, and Finance (ARR semantics) mark the candidate `CanaryReady`.

**Salesforce canary approval is not appropriate before this exact gate is passed.**

### Phase 7 — Salesforce canary

Estimated effort: 3-5 business days plus observation; 0.5-1.5 person-weeks.

Entry:

- Phase 6 candidate is `CanaryReady` and unchanged.
- A fresh live source fingerprint/diff and exact backup have been generated under explicit read-only/write staging authorization.
- Backup, rollback, and readback artifacts are immutable and hashed.
- Dedicated publisher identity is ready.
- ARR field description/help text matches the approved P75/P50-P90 semantics, or the candidate uses the separately approved existing-contract-compatible definition.

Initial canary scope:

- 25-50 accepted, high-confidence rows;
- title and legal lanes;
- low/medium/high MCV and ARR bands;
- anchor-free and history-assisted cases;
- multiple states/evidence qualities;
- no hard suppression/clears; and
- no `Account.Website` field in the payload.

Negative controls and review cases remain in the dry-run evaluator. If additive QA/run metadata is already deployed and approved, a small number may receive metadata-only review updates, but still no numeric clear.

Pre-write checks:

- full field describe revalidated;
- live source fingerprint/current AI run still matches the plan;
- exact before/after diff reviewed row by row;
- canary CSV is the actual Salesforce payload schema;
- backup contains every current AI field plus source fingerprint/SystemModstamp;
- rollback targets only proposed IDs and uses explicit null semantics; and
- bulk line endings/null behavior tested.

Exit gate:

- exactly the intended success IDs updated and no unexpected ID;
- exact readback match for every field;
- zero non-AI or Website changes;
- RunId/model/version/evidence hashes match candidate;
- report/list view shows only the intended canary rows;
- if Approved-run metadata is deferred, a private report filtered to the unique immutable canary Run ID shows only intended canary rows and no public report is repointed;
- no wrong-entity or business-semantic issue in reviewer inspection;
- rollback is not needed, or if exercised, restores every success ID exactly; and
- at least two business days of observation complete with Mike/RevOps sign-off.

Any mismatch, wrong-entity result, source conflict written, unexpected clear, Website/non-AI mutation, or report leakage fails the canary and triggers rollback.

### Phase 8 — Progressive rollout and scheduled operation

Estimated effort: 1-2 calendar weeks for initial rollout; 2-3 person-weeks, followed by ongoing ownership.

Entry:

- Phase 7 canary passed.
- No candidate config/code/source change; otherwise return to Phase 6.
- `AI_Prospect_Value_Run__c` plus `Account.AI_Prospect_Value_Approved_Run__c` and the Approved-run report filter are deployed and verified; governance may be deferred for the private first canary, but not for progressive rollout.

Rollout waves:

1. up to 250 rows or 5% of accepted population, whichever is smaller;
2. 25% of accepted population;
3. remaining accepted population.

Each wave requires fresh fingerprint conflict checks, exact backup, field describe, write, success-ID ledger, full readback, population/report validation, and observation. Stop on any canary trigger.

Phase 8 setup (completed before entry if governance was deferred for the canary):

1. deploy/approve exact QA/run/provisional governance and the Account Approved-run lookup;
2. verify reports filter Approved run state plus `action=score_now`.

After accepted-only rollout:

1. enable review metadata publication without numeric clears;
2. validate operator queues and SLAs;
3. separately approve high-confidence hard-suppression/clear policy;
4. canary explicit clears with proven `#N/A` behavior;
5. enable nightly changed-record candidates, weekly cheap preflight, TTL refresh, and periodic cached full re-evaluation; and
6. publish operating dashboards for freshness, drift, quality, cost, review backlog, failures, and last Approved run.

Exit gate:

- all accepted waves pass exact readback;
- run status is Approved and reports point only to Approved state;
- on-call/owner/runbook/SLA/cost budgets are named;
- scheduled candidates cannot self-approve or self-publish; and
- rollback to the previous Approved run is rehearsed and documented.

## Parallelization plan

| Parallel work | Can start | Must converge before |
|---|---|---|
| Golden-label protocol and first labeling batch | Phase 0 | Phase 2 blind split freeze |
| Source/schema contracts and scorer consolidation | Phase 0 approval | Phase 2 implementation |
| Runtime manifest/idempotency design | Phase 1 schemas | Phase 5 integration |
| Salesforce run/QA metadata design | Phase 1 | Phase 5 publication contract |
| Title lane and legal lane | Phase 2 shared resolver contract | Phase 3 exit |
| Point-in-time customer feature work and ARR cohort cleanup | Phase 2 | Phase 4 calibration |
| External MCV title and legal models | Phase 3 feature freeze | Phase 4 exit |
| Desired-state publisher and evaluator harness | Late Phase 2 schemas | Phase 5 exit |
| Ops review workflow/list-view design | Phase 3 dispositions stable | Phase 8 |

Work that cannot safely be parallelized past a dependency:

- MCV cannot be finalized before entity-bound feature definitions.
- ARR cannot be finalized before Finance formula and hierarchy-resolved clean new-logo cohort.
- population evaluation cannot begin before all candidate versions/configs freeze.
- publisher canary cannot begin before Phase 6 `CanaryReady` sign-off.
- automated hard clears cannot begin before exact QA/provisional/run-state governance and a separate clear canary.

## Test program summary

| Layer | Required test families | Release-critical outputs |
|---|---|---|
| Source contract | missing/renamed fields, type/null/enum, freshness, duplicate IDs, join coverage | fail-closed schema report |
| Entity binding | correct/wrong site, same name, generic host, ALTA semantics, public suffix, geo/contact conflicts | precision/recall/confidence |
| Sellable unit | parent/child, DBA, branch, duplicate, customer, churned, parent billing, owned/direct | standalone leak count, rollup reconciliation |
| ICP lanes | title/escrow/abstract, legal dominance, adjacent categories, state policy | per-lane precision/recall |
| Evidence features | address/office role, staff role, page dedupe, citations, missingness | feature precision and provenance |
| MCV | anchor-scrubbed, history-assisted, time/group holdout, interval calibration | error, coverage, bias, lift |
| Potential ARR | clean new-logo motions, Finance formula, quantile calibration, shrinkage | pinball/coverage/lift/bias |
| Customer quality | point-in-time features, activation/retention outcomes, leakage | Brier/calibration/lift; separate from ICP |
| Population | drift, top 100, stratified accepted, outliers, source coverage | release audit |
| Runtime | retries, immutable attempts, cache invalidation, budgets, hash reproduction | manifest/reducer tests |
| Salesforce | full describe, diff-only, stale conflict, nulls, permissions, backup/readback/rollback | exact mutation ledger |

## Reuse inventory

Reuse immediately:

- July 21,993 Account snapshot and 15,384-row cached scoring evidence;
- 320-task manifests/summaries and full structural audit;
- dedicated GCP project, bucket, runtime identity, Cloud Run/GCS package, and privacy-conscious logging pattern;
- existing Salesforce `AI_Prospect_*` namespace and current field descriptions;
- ALTA match/enrichment artifacts, with corrected membership/confidence semantics;
- VA SCC/CA DFPI/vertical resolver/legal discovery evidence and adapters;
- `legal_entity_scoring.py` route vocabulary and operational-versus-weak evidence work;
- dedupe/ParentId/customer-domain artifacts as candidate evidence;
- customer-history direct and billing-rollup feature families;
- full-run audit reconciliation logic; and
- prior backups/canary/readback lessons, especially explicit null handling.

Reuse only as frozen baselines/regressions:

- current MCV bands/anchor rails;
- static ARR ladder;
- previous legal calibration reports;
- 2k overlay incidents and named Will controls;
- July post-gate output and rejected 5,170 accepted population; and
- existing fixed-run reports.

## Retirement inventory

Retire before any canary:

- `scripts/icp_quality_agent/post_retrieval_quality_gate.py` as a production component;
- `KNOWN_CONTROLS` inside production decision logic;
- current seven-test suite as release proof;
- default residual `score_now -> scoreable_icp` behavior;
- ALTA dictionary truthiness and blank hygiene -> confirmed defaults;
- the local scorer as a second authoritative copy;
- all-row 21,993 payload and blank-string numeric clearing;
- reuse of the raw scorer run ID for a post-gate release;
- placeholder backup manifest; and
- any publication using a human Salesforce CLI alias.

Retire after calibrated replacements pass Phase 4:

- current office-count MCV ladder as production value logic;
- `Score` as an MCV band alias;
- legal state floors not supported by independent labels;
- static MCV-to-ARR ladder as production potential ARR;
- anchor-contaminated backtests as validation claims; and
- current-date customer-history SQL for historical model features.

Retire after governance rollout:

- fixed-run Salesforce report filters;
- review/hard-suppress conflation;
- Sales-readable raw Components JSON; and
- score release without Approved run status.

## Exact canary approval point

Canary approval becomes appropriate **only after Phase 6 completes and the frozen candidate is marked `CanaryReady`**.

That means, at the same time:

1. entity binding, sellable-unit resolution, title/legal lane, MCV, and ARR blind gates have passed;
2. the full 21,993-row cached shadow reconciles exactly;
3. zero fatal negatives, law bypasses, customer/duplicate/child standalone publishes, or top-100 wrong entities remain;
4. independent evaluator and business/RevOps/Finance owners sign the unchanged run;
5. the desired-state plan contains changed IDs only and no first-canary hard clears;
6. every artifact/version/source is hashed and pinned;
7. a fresh live diff, exact backup, actual Salesforce-schema canary, explicit rollback payload, and field-by-field verifier are ready; and
8. the publisher identity cannot change `Account.Website`, metadata, or unrelated fields.

Passing unit tests, reproducing 5,170 accepted rows, or removing the 750 law bypass alone is not sufficient.

## Decisions required from Mike before implementation

1. Confirm the independently sellable buying/contracting unit and parent/subsidiary exceptions.
2. Confirm active-customer exclusion, churned/winback separation, and partner/adjacent treatment.
3. Approve title versus legal lane boundaries and the owner of state/county and owned/direct policy.
4. Approve P75 potential point and P50-P90 range, or select another explicit percentile; approve the pre-canary ARR field-description/help-text change needed to replace the current “ceiling” wording.
5. Name the Finance-approved first-year ARR formula and valid new-logo product/pricing motions.
6. Confirm which product events, if any, can become an independently observed closing-volume label.
7. Pre-approve quantitative entity/lane/MCV/ARR/population gates and their owners, including valid-green recall, auto-resolution coverage, and maximum review rate by lane/source-quality stratum.
8. Approve accepted-only/no-clear as the first canary posture.
9. Approve additive Salesforce run/QA/sellable-unit/fingerprint/evidence-hash governance and provisional-value location.
10. Approve a dedicated publisher identity, Sales evidence visibility, GCS retention, evidence TTL, cadence, and cost budgets.

## Completion definition

The architecture migration is complete when Salesforce exposes only scores from an Approved, reproducible run at the correct sellable-unit grain; every accepted value has bound entity evidence and lane-specific calibrated MCV/ARR; review and suppression are distinct; all writes are diff-only, backed up, read back, and reversible; and scheduled operation has named quality, cost, and incident owners.
