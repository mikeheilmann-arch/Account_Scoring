# Customer and Prospect Segmentation Strategy Review

Date: 2026-07-16
Scope: CertifID prospects and customers
Mode: analytical and read-only
Overall assessment: **Adopt the dual-axis design direction; do not operationalize the spreadsheet or exact thresholds as written.** The proposed thresholds are a coherent taxonomy for shadow evaluation, not an empirically optimized territory model. Automated territory, service, or ownership changes should wait for commercial-unit governance, business-owner approval, historical snapshots, and lane/entity validation.

**Technical QA passed; business thresholds and operating-grain validation remain pending.**

## Executive summary

The spreadsheet is a useful design sketch, but it is not an operational specification. Its three tabs contain three different closing-volume frameworks; `Summary` makes SMB 250-500 annual closings, while Scenario 2 and the rate-card tab make SMB 250-1,500. The rate-card tab adds Strategic, leaves 11,989-11,999 annual closings uncovered, applies a placeholder $28 rate that creates a $35,969 price cliff at 12,000 closings, and currently calculates a $0 portfolio total because account-count inputs are blank. Six `Supporting` cells are underlying dates that only display as ranges, and Scenario 2's monthly bands were not updated from Scenario 1.

The best operating model is:

1. one shared **potential-capacity tier** for prospects and customers, measured as annual closing capacity at a governed commercial unit;
2. lane-specific evidence and calibration for title/escrow versus real-estate law firms;
3. explicit evidence confidence and assignment status, especially for prospects;
4. customer-only overlays for **realized value**, **observed usage-to-capacity**, **health/risk**, and **service model**.

The proposed shadow-model bands are VSMB `<250`, SMB `250-1,500`, MM `1,501-3,500`, ENT `3,501-11,999`, and Strategic `12,000+` annual closings. These bands align with the rate-card economics and remove gaps, but they are a governance choice rather than a statistically proven optimum. The VSMB/SMB boundary is the least stable customer boundary: 167 active highest-parent units, 9.7% of units with known capacity, sit within +/-10% of 250 annual closings.

The hierarchy effect is material, but the operating grain is not yet proven. Salesforce has 2,379 active-customer Accounts; `COALESCE(highest_parent_id, account_id)` reduces them to 1,816 analytical highest-parent units. That is a useful economic-rollup sensitivity, not proof that all children are one contracting, territory, or CSM unit: only two of 136 active multi-Account roots have any explicitly parent-billed members. Twenty active roots have root/member MCV conflicts, and 12 change tier if maximum member MCV replaces root MCV. Using the same nominal service ratios yields 23.75 FTE at highest-parent potential-tier grain, 77.68 if every active Account inherits its root tier, and 28.89 at highest-parent grain after realized-value service escalations. These are workload sensitivities, not staffing requirements.

The current scoring run is valuable as an additive prospect-capacity prior, not as independent company-size truth. The live run reconciles exactly to 16,822 Accounts, but 10,420 are Low confidence; 76.0% use Tier 3/4 cohort fallbacks; only 1,826 meet the strict standalone sellable-unit rule; and only 846 meet strict lane-value eligibility. After lifecycle filtering, 16,323 are greenfield prospects, 485 are former customers, five are active customers, and nine are outside the prospect/customer scope. Under the proposed thresholds, 15,746 greenfield prospects, 96.5%, fall into SMB. That concentration reflects fallback mechanics, not a measured market distribution, so Low-confidence assignments should remain provisional or coarse.

The scoring run also created and populated `Account_Segment_V3__c`. V3 is a same-run, confidence-gated re-bucketing of the AI MCV output, not independent validation. It is populated on 6,116 current-run Accounts, including 5,842 greenfield prospects; 10,481 current-run greenfield prospects are null. It leaves every Low-confidence record blank, is blank on 2,378 of 2,379 active customers, and uses observed 10/100 monthly MCV breakpoints rather than the proposed rate-card-aligned breakpoints. Among populated greenfield prospects, 374 V3 Strategic records map to proposed SMB because 100-125 monthly closings still fall below 1,501 annual closings.

The customer screen identifies 83 active high-potential highest-parent units with positive billable usage below 25% of estimated capacity, plus 13 with zero or no matched usage that require billing-linkage validation. The confirmed 83 are 39 MM, 31 ENT, and 13 Strategic; their median observed usage-to-capacity proxy is 11.5%. This supports keeping potential separate from realized value, but it is not yet a Finance/CS-approved wallet-share measure. All 96 review candidates and their quality status are in `tmp/customer_prospect_segmentation_20260716/underpenetrated_customers.csv`.

## Decision table

| Decision | Recommended default | Accountable owner(s) | Operating impact |
|---|---|---|---|
| Segmentation architecture | Preserve one potential-capacity tier plus separate realized value, observed usage-to-capacity, health, and service dimensions. | RevOps | Creates a common lifecycle vocabulary without collapsing customer value or service needs into size. |
| Analytical hierarchy key | Use `analytical_highest_parent_unit_id` for shadow rollups only; retain direct Account, billing parent, and future governed commercial/service unit separately. | RevOps + Data/Analytics + Sales/CS Ops | Prevents highest parent from silently becoming a territory, contracting, or CSM assumption. |
| Potential thresholds | Keep <250, 250-1,500, 1,501-3,500, 3,501-11,999, and 12,000+ as versioned shadow defaults; no automated moves. | RevOps + Sales + Finance | Preserves rate-card alignment while empirical calibration remains pending. |
| Prospect operation | Treat 284 as the automation-readiness candidate ceiling; leave 14,682 provisional prospects unsegmented for territory/coverage, retain 1,357 review exceptions, and exclude 499 lifecycle rows. | RevOps + Sales Ops | Preserves useful directional prioritization without converting cohort priors into operating segments. |
| Realized value and service | Retain RV bands as descriptive policy bands; do not approve service ratios until Finance/CS validates contribution economics and actual workload. | Finance + CS Leadership | Avoids underfunded high-touch coverage and separates book coverage from profitability. |
| Activation | Continue shadow evaluation; require every measurable exit gate below plus sandbox/UAT signoff before production automation. | RevOps steering group | Makes launch reversible, auditable, and owner-approved. |

## Direct answers to the business questions

| Question | Answer |
|---|---|
| Is the spreadsheet sound and operationally useful? | Directionally useful, but not operationally sound as written. Conflicting definitions, boundary defects, ambiguous units, weak controls, date coercions, and pricing cliffs prevent deterministic implementation. |
| Where will it misclassify or become unstable? | The Summary SMB/MM boundary moves 8,422 current-run greenfield prospects and 436 active-customer units from proposed SMB to MM. The 250 boundary is customer-sensitive; account hierarchies, low-confidence defaults, and revenue/location proxies create additional instability. |
| Should prospects and customers share potential tier? | Yes as a design vocabulary, if the tier means capacity at a governed commercial unit. Evidence and confidence rules must differ by lifecycle and lane. |
| Do customers need another dimension? | Yes. At minimum: realized value, penetration, health/risk, and service/engagement. Do not compress these into one segment. |
| Do title/escrow and legal need different logic? | Yes upstream. They may share output tier names and closing-capacity thresholds, but require different identity, feature, and calibration logic. Evidence is not yet sufficient to justify different public closing thresholds by lane. |
| What best supports territory, ownership, CSM, expansion, and planning? | Dual-axis lifecycle model, retaining direct Account, highest parent, billing parent, and governed commercial/service unit as distinct keys. Potential informs territory/planning; realized value and penetration inform expansion; service and health govern coverage. |
| Can the model identify large underpenetrated customers? | It can screen candidates: 83 active MM+ highest-parent units have positive observed usage below 25% of estimated capacity, and 13 more have zero/no matched usage requiring data-quality review. |

## Definitions that must be canonical

These terms should be written into field help, reporting definitions, and governance documentation.

| Term | Recommended definition | Must not mean |
|---|---|---|
| Annual closings / capacity | Estimated total annual real-estate closing capacity of the governed commercial unit, whether or not CertifID currently serves those closings. | CertifID transactions, committed volume, or closed-won MCV without source/as-of context. |
| Potential-capacity tier | Band derived from annual closing capacity at the governed commercial unit. | Current ARR, current usage, health, or CSM service level. |
| Estimated company revenue | Third-party or CRM estimate of the account's own corporate revenue; a weak secondary proxy. | CertifID booked ARR or pipeline-potential ARR. |
| Pipeline-potential ARR | Directional first-year recurring-revenue potential if CertifID wins the account under stated rate/penetration assumptions. | Booked ARR, probability-weighted pipeline, recognized revenue, or forecast. |
| Active subscription ARR | Booked recurring subscription ARR at a closed monthly snapshot. | Variable usage annualization, BNI opportunity ARR, or total mixed ARR. |
| Realized usage | Observed CertifID billable/product transactions over a defined closed period. | Account capacity. |
| Observed usage-to-capacity proxy | Observed billable usage divided by estimated annual closing capacity, with numerator/denominator quality flags. | Approved wallet share, health, retention risk, or product breadth. |
| Service model | Coverage intensity and engagement motion. | Account size or health. |

`Summary!Annual Revenue` should be relabeled **Estimated Company Revenue**. `rate-card!Annualized Revenue` should be relabeled **Full-wallet pipeline-potential ARR at illustrative rate**. `Summary!Committed volume` is not operational until it has a numeric threshold, time period, and source precedence.

## Source and analytical method

### Controlling sources

1. **Live Salesforce** controls field metadata, formulas, current record state, lifecycle fields, and the current scoring/V3 population.
2. **Warehouse/dbt** controls current customer hierarchy, billable usage, adoption, active subscription ARR, BNI, and variable/proxy ARR components.
3. **Live Google Sheet** controls the proposed design and rate assumptions, not observed customer or prospect truth.
4. **Scoring run artifacts and code** control score provenance, lane/entity decisions, confidence, and the directional meaning of MCV/ARR.

The live identities were verified before analysis: Google `mike.heilmann@certifid.com`, Salesforce org `00D4x000007sjwAEAQ`, and warehouse role `analyst` with SSL. No source-system writes, Salesforce metadata changes, dbt changes, Google Sheet changes, or production jobs were performed.

### Analytical grain

- Row-level audit file: one row per Salesforce Account ID, 21,995 rows.
- Analytical customer rollup: `COALESCE(highest_parent_id, account_id)`, 20,907 warehouse highest-parent units plus one unmatched live Salesforce Account retained as its own unit in the no-write output (20,908 total).
- Operating ownership/adoption grain: direct Salesforce Account ID.
- Active-customer sensitivity population: 1,816 highest-parent units from 2,379 active Account records. This is not yet a governed contracting or service grain.

For hierarchy groups, pilot capacity is conservatively taken from the root when present, otherwise maximum member capacity. The sum of member capacity is retained as an upper bound. Among active units, 20 root/member capacity conflicts exist and 12 change tier under max-member sensitivity; those rows are flagged and are not suitable for automated decisions until reviewed. Highest parent, billing parent, direct Account, and the future governed service/commercial unit must remain separate keys.

### Time windows

- Usage/billing: trailing 12 closed months through June 2026.
- ARR/value: May 2026, the latest month common to all 12 ARR components.
- Descriptive retention: May 2025 to May 2026, using active-subscription sources and clipping negative account-month totals to zero.

June and July are excluded from the harmonized all-component snapshot because source coverage is incomplete. The three active-subscription sources support a closed-June sensitivity: June 2025-June 2026 yields 109.18% NRR and 85.32% GRR versus May's 110.58% and 86.45%. The raw ARR mart mixes active subscription ARR, BNI ARR, and variable/proxy annualizations; they remain separate throughout this analysis.

### Validation limits

- Current potential tiers are joined to May 2025-May 2026 retention because historical potential-tier snapshots do not exist. Retention results are descriptive, not causal or backtested tier performance.
- High/Medium/Low labels represent source/evidence confidence, not calibrated assignment accuracy; AI low/high values are lookup-band edges, not probabilistic intervals.
- The current customer-history pull is relative to current date and cannot be treated as an as-of historical feature set.
- Customer potential MCV and closed-won MCV share source fields with portions of the scoring model. No circular claim of predictive accuracy is made.
- `rpt_yoy_retention` is aggregate-only and has a May sign-transition defect; this analysis independently recomputes unit-level active-subscription retention.

## Spreadsheet stress test

### The Sheet contains incompatible models

| Source | VSMB | SMB | MM | ENT | Strategic |
|---|---|---|---|---|---|
| Summary | `<250` | `250-500` | `501-3,500` | `>3,500` | none |
| Supporting Scenario 1 | `<250` | `250-1,000` | `1,001-3,000` | `>3,000` | none |
| Supporting Scenario 2 | `<250` | `250-1,500` | `1,501-3,500` | `>3,500` | none |
| Rate card | `120-249` | `250-1,500` | `1,501-3,500` | `3,501-11,988` | `12,000-24,000` |

Other high-confidence defects:

- Supporting Scenario 2 repeats Scenario 1 monthly ranges instead of 21-125, 125.1-291.7, and >291.7.
- `Supporting!D3,D4,E4,D11,D12,E12` are stored as 2026 dates and only formatted to look like ranges.
- Employee counts 36 and 42 fall into gaps; location and revenue ranges overlap; 11,989-11,999 rate-card closings are unassigned.
- The `Expected annualized` value is an unweighted midpoint, not an expected value.
- The $28 Strategic rate is a placeholder. Applying it to all volume makes 12,000 closings worth $35,969 less than 11,999 closings.
- The documented blue-input convention is not implemented, input cells lack validation, and there are no cell notes.
- Strategic's existence is still an unresolved workbook comment.

## Model comparison

### Model 1: Spreadsheet as written

There is no single implementable Model 1, so both the Summary and rate-card versions were measured.

| Population | Summary distribution | Rate-card distribution as written |
|---|---|---|
| 16,323 current-run greenfield prospects | VSMB 296; SMB 7,324; MM 8,627; ENT 76 | Below card 99; VSMB 197; SMB 15,746; MM 205; ENT 75; Strategic 1 |
| 1,816 active highest-parent units | VSMB 620; SMB 430; MM 560; ENT 119; Unknown 87 | Below card 130; VSMB 490; SMB 866; MM 124; ENT 94; Strategic 19; above cap 6; Unknown 87 |

**Inputs and precedence:** annual closings primary; company revenue/locations secondary for new business; closings and undefined committed volume for post-sale. The Sheet has no source precedence or missing-data rule.

**Legal versus title:** same rules despite two listed verticals.

**Customer versus prospect:** uses different primary/secondary columns but conflates potential, commitment, and service model.

**Operational use:** cannot safely drive automation until definitions are reconciled.

**Advantages:** understandable labels; ties capacity to pricing and CSM intent.

**Failure modes:** contradictory thresholds, gaps/overlaps, fragile proxies, no hierarchy, no confidence, pricing discontinuity, and no separation of realized value/health/service.

### Model 2: Unified potential-capacity tier

Exact proposed bands:

| Tier | Annual closing capacity | Monthly equivalent | Typical planning meaning |
|---|---:|---:|---|
| VSMB | `<250` | `<20.83` | digital/low-complexity opportunity |
| SMB | `250-1,500` | `20.83-125` | core scaled motion |
| MM | `1,501-3,500` | `125.08-291.67` | named growth motion |
| ENT | `3,501-11,999` | `291.75-999.92` | high-complexity enterprise |
| Strategic | `12,000+` | `1,000+` | bespoke strategic planning/pricing |
| Unknown | insufficient defensible capacity evidence | n/a | enrichment/review, not a forced tier |

**Inputs and precedence:** resolve the governed commercial unit; use dated trusted rep/Opportunity/customer capacity; then lane-specific high-bound evidence/model; then provisional cohort prior. Usage, ARR, health, and service are prohibited capacity inputs. The current highest-parent implementation is a shadow-model assumption, not the final grain.

**Missing data:** keep Unknown or a provisional tier with source/confidence. Do not backfill capacity from observed usage as though usage were total wallet.

**Legal versus title:** shared output bands, separate features and calibration.

**Customer versus prospect:** same capacity construct; customer can use trusted contractual/rep MCV, prospect uses the scoring prior and confidence.

**Population:** current-run prospects concentrate in SMB: 15,746, or 96.5%. This does not provide a useful fine territory split; MCV 40 and 50 dominate because of scoring priors. Under the highest-parent sensitivity, active units distribute as VSMB 620, SMB 866, MM 124, ENT 94, Strategic 25, Unknown 87.

**Advantages:** consistent territory/planning vocabulary, rate-card alignment, simpler governance, and lifecycle continuity.

**Failure modes:** a unified tier can create false precision when evidence is default-heavy; hierarchy errors can duplicate capacity; one shared feature model would mis-handle legal versus title.

### Model 3: Dual-axis lifecycle model — recommended direction

Model 3 uses Model 2's potential tier and adds customer overlays.

#### Realized-value overlay

This pilot uses booked active-subscription ARR at May 2026:

| Tier | Active subscription ARR |
|---|---:|
| RV0 | no active subscription ARR |
| RV1 | `<$5K` |
| RV2 | `$5K-<$15K` |
| RV3 | `$15K-<$50K` |
| RV4 | `$50K-<$115K` |
| RV5 | `$115K+` |

These value thresholds are judgment calls for pilot/service escalation, not approved Finance bands. Variable usage/proxy value and BNI remain separate fields.

#### Empirical ARR quantiles and service-economics comparison

Of 1,816 active analytical highest-parent units, 1,653 have positive May 2026 active subscription ARR and 163 have no matched positive ARR. The positive-ARR distribution is strongly right-skewed: P25 is $3,033, median $5,804, P75 $11,053, P90 $22,233, P95 $35,176, and P99 $82,768.

| RV band | Units | ARR share | Boundary location among positive-ARR units | Nominal service mapping | ARR book implied by band and ratio |
|---|---:|---:|---:|---|---:|
| RV0 - no positive ARR | 163 | 0.00% | n/a | Digital / exception review | n/a |
| RV1 - <$5K | 734 | 8.77% | $5K = P44.40 | Digital / tech-touch | n/a |
| RV2 - $5K-<$15K | 644 | 25.85% | $15K = P83.36 | Scaled, 1:150 | $0.75M-<$2.25M |
| RV3 - $15K-<$50K | 228 | 27.03% | $50K = P97.16 | Named, 1:40 | $0.60M-<$2.00M |
| RV4 - $50K-<$115K | 36 | 11.53% | $115K = P99.33 | High-touch, 1:8 | $0.40M-<$0.92M |
| RV5 - $115K+ | 11 | 26.81% | top 0.67% by positive-unit count | Dedicated/strategic, 1:8 | $0.92M+ |

The thresholds are understandable policy bands, not empirical quantiles. RV5 is only 11 units but holds 26.81% of measured ARR, so its tail concentration must be explicitly approved. At the actual proposed service-overlay mix, active ARR per nominal FTE is approximately $939K Scaled, $802K Named, $257K high-touch, and $1.59M Dedicated. That non-monotonic pattern makes high-touch the priority economics review. These figures measure book coverage, not profitability: gross margin, fully loaded service cost, actual staffed FTE, complexity, and SLA outcomes are still missing.

#### Penetration overlay

| Tier | Billable usage / annual capacity |
|---|---:|
| P1 Underpenetrated | `<25%` |
| P2 Developing | `25%-<60%` |
| P3 Established | `60%-100%` |
| P4 Capacity check | `>100%` |
| Unknown | capacity or billable usage unavailable |

P4 does not mean unhealthy; it usually means capacity is underestimated, seasonality is present, or the measures are not comparable. Until Finance/CS validates that billable product transactions and closing capacity are comparable, call this an observed usage-to-capacity proxy rather than wallet penetration.

#### Service and health

- Potential VSMB -> digital/tech-touch with triggers.
- Potential SMB -> scaled/pooled CSM.
- Potential MM -> named CSM.
- Potential ENT -> named high-touch CSM.
- Potential Strategic -> dedicated/strategic review.
- Realized RV4/RV5 may escalate service; Low health or at-risk status triggers intervention but does not redefine potential.

**Advantages:** supports territory, service, expansion, retention, and planning without semantic collapse; directly surfaces high-potential/low-realization accounts.

**Failure modes:** capacity errors propagate into penetration; value bands need Finance/CS approval; service escalation can overload teams without explicit capacity budgets; current snapshots are not historical cohort assignments.

## Measured portfolio results under the recommended model

### Active customer highest-parent sensitivity

| Potential tier | Units | Share | Active subscription ARR | Share of measured ARR |
|---|---:|---:|---:|---:|
| VSMB | 620 | 34.1% | $2.32M | 10.8% |
| SMB | 866 | 47.7% | $7.96M | 37.2% |
| MM | 124 | 6.8% | $2.34M | 10.9% |
| ENT | 94 | 5.2% | $4.96M | 23.2% |
| Strategic | 25 | 1.4% | $3.58M | 16.7% |
| Unknown | 87 | 4.8% | $0.24M | 1.1% |

Measured active-subscription ARR on matched units is $21.40M. The ten largest units hold 26.2% and the top 25 hold 32.5%, so high-touch planning must consider concentration in addition to tier counts.

### Boundary and threshold sensitivity

| Test | Current-run prospects | Active-customer units |
|---|---:|---:|
| Within +/-10% of 250 | 78 | 167 |
| Within +/-10% of 1,501 | 374 | 36 |
| Within +/-10% of 3,501 | 14 | 29 |
| Within +/-10% of 12,000 | 1 | 9 |
| AI low/high range crosses any proposed boundary | 1,263 (7.7%) | n/a |

Moving the SMB/MM cutoff from 1,501 to 1,000 increases greenfield prospect MM from 205 to 660 and active-customer MM from 124 to 266. The sharp prospect shift is partly model-value concentration, not observed natural clustering. Use hysteresis and evidence ranges rather than recutting ownership every time a point crosses a boundary.

### CSM capacity and grain sensitivity

Assumptions: VSMB is tech-touch, SMB midpoint is 1:150, MM is 1:40, ENT and Strategic are 1:8.

| Model | Implied CSM FTE |
|---|---:|
| Spreadsheet Summary thresholds | 31.7 |
| Recommended thresholds, SMB 1:100 | 26.6 |
| Highest-parent potential tier, SMB 1:150 | 23.75 |
| Recommended thresholds, SMB 1:200 | 22.3 |
| Highest-parent service overlay, including realized-value escalations | 28.89 |
| Active Accounts inheriting highest-parent tier | 77.68 |

The Summary model places 436 highest-parent units with 501-1,500 annual closings into MM instead of SMB. The much larger 23.75-to-77.68 range comes from the unresolved service grain, not threshold choice. The 28.89 overlay shows that potential-only staffing omits realized-value escalations. Digital/tech-touch, Unknown, health, onboarding, complexity, and product workload are excluded, so these are workload sensitivities rather than staffing requirements.

### Descriptive retention

Using active-subscription ARR for May 2025 to May 2026, 1,328 beginning units produce 110.6% NRR and 86.5% GRR in aggregate. Current potential tiers show:

| Current potential tier | Beginning units | NRR | GRR | Churned units |
|---|---:|---:|---:|---:|
| VSMB | 375 | 98.7% | 81.6% | 43 |
| SMB | 669 | 102.3% | 78.8% | 111 |
| MM | 102 | 120.5% | 87.8% | 12 |
| ENT | 83 | 127.2% | 92.7% | 12 |
| Strategic | 24 | 112.4% | 96.4% | 3 |
| Unknown | 75 | 63.2% | 62.4% | 5 |

These figures are a descriptive current-portfolio slice only. They do not establish that current potential predicts retention: tiers and lanes were not snapshotted at cohort start, capacity fields can be updated after the outcome window, and unknownness is not random. A closed-June active-subscription sensitivity is similar but slightly lower at 109.18% NRR and 85.32% GRR.

### Underpenetrated customers

There are 83 active MM+ highest-parent units with positive billable usage below 25% of estimated capacity, plus 13 zero/no-match usage cases requiring data-quality review:

- 39 MM, 31 ENT, and 13 Strategic;
- median penetration 11.5%;
- 61 title/escrow, 17 legal, and five other;
- $1.40M active subscription ARR and 50,079 observed billable transactions;
- 19 of the confirmed 83 are multi-account hierarchies requiring manual unit review.

This is an expansion-screening queue, not a health, renewal-risk, or proven wallet-share queue. Each record retains capacity source/confidence, hierarchy review, ARR components, usage, and a candidate-quality status. The 13 zero/no-match rows must be validated before action.

## Prospect strategy and V3

### Current scoring evidence

For the 16,323 current-run greenfield prospects:

- 10,223 are Low confidence, 6,009 Medium, and 91 High.
- 12,726, 78.0%, use Tier 3/4 fallbacks.
- MCV 40 and 50 dominate the run because they are cohort priors, not because most firms have measured capacity at those values.
- The status Operationally usable is not evidence-backed precision: 4,938 of its 5,222 rows are retained V1 or Tier 3 fallback records.

Use this mutually exclusive prospect operating model:

| Operating population | Accounts | Share of 16,323 greenfield | Recommended action |
|---|---:|---:|---|
| Source-backed five-tier candidate | 284 | 1.74% | Show the exact shadow tier only after explicit entity/lane review; preserve source, confidence, as-of date, and run version. |
| Provisional - no operating segment | 14,682 | 89.95% | Leave the operating segment unassigned. Continue directional prioritization with rank, confidence, MCV point/range, and source; do not drive territory, coverage, or compensation from the shadow tier. |
| Boundary/hierarchy review exception | 1,357 | 8.31% | Hold current routing and resolve the evidence or entity conflict before tier-dependent action. |

The 284 is the **automation-ready candidate ceiling**, not the total population with useful directional scoring. It is an upper bound: 227 use Tier 1 trusted anchors and 57 use Tier 2 usable website scores, but strict commercial-unit and lane/subtype eligibility can reduce the authorized set further.

The 14,682 provisional prospects are deterministic: current-run greenfield records that are neither source-backed five-tier candidates nor boundary/hierarchy exceptions. Leave their operating segment null/unassigned. For prioritization only, use this stable sort: published priority rank present first and ascending; confidence High, then Medium, then Low; MCV point descending; Account ID as the final tie-breaker. Show MCV low/high, source tier, and confidence to sellers. The analytical shadow tier may remain in reporting, but it must not drive territory, coverage, compensation, or named-account status.

Separately, 499 current-run Accounts are excluded from greenfield routing: 485 former customers go to winback, five active customers go to the customer motion, and nine remain out of scope.

### V3 interpretation

`Account_Segment_V3__c` came from the same scoring run used for the proposed prospect MCV. Its comparison is lineage and threshold re-bucketing, not independent validation. Its semantics differ from the proposed model:

- observed V3 `<10` = AI MCV 1-10;
- V3 `Core` = 11-99;
- V3 `Strategic` = 100+;
- all 10,420 Low-confidence run rows are blank;
- 286 Medium/High rows are also blank without an exposed reason;
- 270 churn-status records have a V3 value.
- of current-run greenfield prospects, 5,842 are populated and 10,481 are null.

For current greenfield prospects with V3:

| V3 | Recommended VSMB | SMB | MM | ENT | Strategic |
|---|---:|---:|---:|---:|---:|
| `<10` | 162 | 0 | 0 | 0 | 0 |
| Core | 124 | 4,967 | 0 | 0 | 0 |
| Strategic | 0 | 374 | 154 | 60 | 1 |

Keep V3 as a controlled same-run test/crosswalk until its thresholds, blank logic, lifecycle filter, source/confidence fields, history tracking, and owner are governed. Agreement with the proposed tier is not evidence of accuracy. Do not use it for active-customer service segmentation.

## Title/escrow versus legal

Use common audience-facing tier labels only after separate lane logic.

### Title/escrow evidence

Prioritize verified operating title/escrow/settlement status, registry/licensing footprint, owned/direct versus underwriter/adjacent relationships, office/escrow staff footprint, recent rep/Opportunity capacity, title-production evidence, and High-bound public evidence. Do not treat a title-adjacent name or ALTA identity as an independently sellable capacity measure.

### Legal evidence

Prioritize closing-focused real-estate practice, attorney/partner and closing-staff counts, evidence of operational closing services, state/county closing regime, recent rep/Opportunity capacity, and High-bound site evidence. Generic employee count, company revenue, or being a law firm in an attorney-relevant state is not enough.

### Measured differences

| Population | Legal | Title/escrow |
|---|---:|---:|
| Current-run prospects | 8,125 | 8,198 |
| Prospect Low confidence | 62.7% | 62.6% |
| Active customer highest-parent units | 664 | 1,095 |
| Active-unit capacity coverage | 97.7% | 93.8% |
| Median customer MCV | 25 | 40 |
| Median customer penetration | 30.0% | 36.7% |
| Descriptive May 2025-May 2026 NRR | 98.2% | 120.9% |
| Descriptive GRR | 79.4% | 82.4% |

The architecture and source semantics require separate title and legal features. The observed differences motivate future lane-specific calibration, but do not establish different annual-closing cutpoints or predictive lift; the retention comparison uses current lane and is not a historical lane snapshot.

## Current-to-proposed customer crosswalk

At Account grain, the existing customer segment is not identical to the proposed highest-parent potential tier:

- Current SMB contains 617 active Accounts mapped to proposed VSMB and 724 mapped to SMB.
- Current Mid Market contains 123 active Accounts mapped to proposed SMB and 102 mapped to MM.
- Current Enterprise contains 11 active Accounts mapped to SMB, 11 MM, 79 ENT, and 22 Strategic.
- Current Strategic contains one active Account mapped to SMB, 19 ENT, 131 Strategic, and three Unknown.
- Engagement `Scaled` spans VSMB through Strategic and Unknown, which confirms it is a service dimension rather than a size dimension.

The full crosswalk, including V2 and V3, is in `tmp/customer_prospect_segmentation_20260716/current_to_proposed_crosswalk.csv`. It is intentionally Account-count only. Unit ARR and usage are excluded because those attributes repeat on member Account rows and are not additive at this grain.

## Salesforce field and operating-model recommendation

Do not overwrite or repurpose production fields during the design phase. After approval and sandbox rehearsal, use separate governed dimensions:

| Dimension | Recommended field/contract | Notes |
|---|---|---|
| Lifecycle | Greenfield Prospect, Open Opportunity, Active Customer, Former Customer/Winback, Partner/Other, Excluded | Must precede segment assignment; `Type` alone is insufficient. |
| Commercial/service grain | direct Account, highest parent, billing parent, and governed commercial/service-unit ID | Highest parent is a shadow-model sensitivity, not the default production service key; manual exceptions must be versioned. |
| Potential capacity | tier, MCV low/point/high, source, confidence, lane/subtype, as-of date, model/run version | New governed analytics/CRM contract; do not make users parse JSON evidence. |
| Territory assignment | keep `Account_Segment__c` as an operating output | It is editable and explicitly used for territory; never validate the model against it as neutral truth. |
| Prospect test | keep V3 as an experiment until governed | Add explicit assignment status/reason and history before production use. |
| Customer realized value | separate tier from active subscription ARR; preserve BNI and variable/proxy value separately | Finance definition required. |
| Penetration | separate ratio/tier with numerator, denominator, period, and quality flag | Never use as health. |
| Service/engagement | governed use of `Engagement_Customer_Segment__c` or a new service field | Capacity and value may escalate service; health triggers intervention separately. |
| Health/risk | retain health, renewal, at-risk, churn fields | Do not roll into size. |

Specific cautions:

- `Account_Segment_v2__c` is formula-only and uses rep/marketing MCV plus Clay fallback; it does not use AI scoring, usage, ARR, or hierarchy.
- `Segmentation_Tier__c` metadata defines confidence, not size.
- `Company_Type_Code__c` has a trailing-tab defect that maps all 452 Escrow company rows to 0.
- Salesforce has no general billing-parent/highest-parent lookup; a warehouse-resolved key is required.
- Annual revenue, office, and employee fields contain severe outliers and should not be deterministic primary tier inputs.

## Implementation and governance plan

### Phase 0 — business decisions

1. Approve the canonical meaning of annual capacity and whether Strategic begins at 12,000.
2. Approve active subscription ARR versus usage-based value treatment and RV thresholds.
3. Approve service ratios and whether RV4/RV5 or BNI escalates coverage before implementation.
4. Approve hierarchy override ownership and the handling of parent-billed versus highest-parent structures.
5. Decide whether V3 remains an experiment, becomes a prospect view, or is superseded by a governed potential field.

### Phase 1 — analytical shadow model

1. Persist monthly governed commercial-unit snapshots with operating Account, highest parent, billing parent, service unit, capacity low/point/high, source, confidence, lane, lifecycle, ARR components, usage, adoption, health, and service.
2. Build lane-specific, anchor-scrubbed title and legal validation sets.
3. Calibrate historical capacity and potential ARR without post-outcome leakage; isolate entity/domain families across train/test.
4. Finance approves the first-year potential ARR definition and realized-value contract.

### Phase 2 — sandbox/UAT

1. Add or stage governed fields in `CertifID-Sandbox` only after metadata review.
2. Load the no-write crosswalk to a review surface, not Salesforce production.
3. Validate ownership, territory balance, CSM capacity, the 83 confirmed expansion candidates, and the 13 zero/no-match exceptions with Sales, CS, Finance, and RevOps owners.
4. Test parent/child exceptions and ensure one commercial unit is not assigned multiple territories or CSMs unintentionally.

### Measurable shadow-pilot exit criteria

These are blocker gates, not aspirational KPIs. Broad production automation remains a no-go until every gate passes.

| Gate | Minimum exit criterion | Owner |
|---|---|---|
| Lifecycle integrity | 100% of the 499 non-greenfield run rows remain excluded from net-new; former customers route to winback. | RevOps + Sales Ops |
| Fine-tier eligibility | 100% of automated five-tier rows have governed commercial-unit membership, strict lane eligibility, dated evidence, confidence/version, and no open boundary or hierarchy conflict. | Data/Analytics + RevOps |
| Capacity validation | Pass the pre-registered holdout specification below: minimum sample and coverage, 70% exact/90% within-one targets, baseline lift, label reliability, and confidence-bound requirements. | Data/Analytics + Sales |
| Hierarchy resolution | All 20 root/member MCV conflicts and all 12 tier-changing cases are adjudicated before automated moves. | RevOps + Sales Ops + CS Ops |
| Stability | Three monthly snapshots show <5% unexplained tier movement after hysteresis; 100% of moves trace to evidence, hierarchy, or a threshold version. | Data/Analytics |
| RV definition | Finance approves ARR inputs, missing-ARR handling, and the $5K/$15K/$50K/$115K bands, explicitly accepting or revising RV5 concentration. | Finance |
| Service economics | Contribution margin per staffed FTE covers fully loaded service cost by >=1.5x and approved SLA/retention standards are met. | Finance + CS Leadership |
| Exception operations | >=90% of boundary, lifecycle, and hierarchy exceptions are dispositioned within 10 business days; unresolved backlog is <5% of eligible population. | RevOps + Sales Ops |
| Expansion screen | At least 50 candidates reviewed; >=70% accepted as actionable and billing-linkage false positives are <5%. | CS + Account Management + Data |
| Launch gate | All blockers pass for three monthly snapshots plus one planning-cycle simulation; sandbox/UAT, rollback, history, and named-owner signoff are complete. | RevOps steering group |

#### Capacity-validation statistical specification

| Requirement | Pre-registered minimum |
|---|---|
| Independent holdout | Human-adjudicated, anchor-scrubbed, time-separated, and entity-family-disjoint from training/calibration. Reviewers are blinded to model tier and existing Salesforce segment. |
| Total sample | At least 600 adjudicated Accounts. Oversampling is allowed for rare tiers, but overall metrics must be prevalence-weighted back to the production lane/tier mix. |
| Lane coverage | At least 250 title/escrow and 250 legal Accounts. Both lanes must independently pass the point-estimate and confidence-bound gates. |
| Tier coverage | At least 60 examples per VSMB, SMB, MM, ENT, and Strategic overall, and at least 30 in every lane-by-tier cell. A cell below 30 is explicitly insufficient and remains ineligible for automation. |
| Label reliability | At least 20% double-coded by independent reviewers, with weighted Cohen's kappa >=0.75; blinded adjudication resolves disagreements before scoring. |
| Primary targets | Overall and within each lane: exact-tier agreement >=70% and within-one-adjacent-tier agreement >=90%. Also report per-tier precision, recall, and macro-F1 so SMB concentration cannot hide weak tail performance. |
| Baselines | Compare with (1) the lane-specific majority-tier classifier and (2) the legacy/current cohort-prior tiering on the same holdout. V3 is excluded as an independent baseline because it comes from the same scoring run. |
| Required lift | Proposed logic must beat both baselines by at least 5 percentage points in exact-tier agreement and 0.05 macro-F1; the 95% paired-bootstrap interval for each improvement must exclude zero. |
| Uncertainty | Publish 95% Wilson intervals for exact and within-one agreement overall and by lane. Pass requires the point targets above plus lower bounds >=65% exact and >=85% within-one. Publish stratified-bootstrap 95% intervals for macro-F1 and all baseline deltas. |
| Decision rule | No post-hoc threshold tuning on the holdout. Any lane/tier cell missing sample, coverage, label reliability, target, lift, or uncertainty requirements remains unautomated and returns to shadow/enrichment. |

### Phase 3 — controlled production launch

1. Shadow for at least one full planning cycle.
2. Freeze territory tier within the fiscal period unless a reviewed material event occurs.
3. Use hysteresis: do not move a tier when the evidence range crosses the boundary; require either two consecutive snapshots or a point at least 20% beyond the boundary for automatic movement.
4. Require source/confidence/assignment reason and effective timestamp on every automated update.
5. Never clear a trusted manual/contractual capacity value without an explicit conflict workflow.

### Ongoing governance

- Monthly: source freshness, missingness, hierarchy changes, join coverage, boundary counts, negative/corrective ARR, and assignment drift.
- Quarterly: lane-specific calibration, tier distribution, territory/CSM balance, underpenetration conversion, GRR/NRR by start-of-period snapshot, and false-positive review.
- Semiannual: threshold and service-ratio review with Finance/Sales/CS approval.
- Every release: version thresholds, code, source precedence, cohort, and effective date; preserve historical snapshots and rollback crosswalk.

## Open business decisions

1. Does Strategic exist as a distinct potential tier, and is 12,000 annual closings the correct start?
2. Does annual capacity mean the entire highest-parent wallet, the independently sellable contracting unit, or a reviewed hybrid?
3. For hierarchy groups, when should member capacity be summed instead of using the root/max conservative value?
4. Should realized value use active subscription ARR only, or active ARR plus a trailing median/average of variable usage?
5. Should BNI escalate service before implementation, or remain a sales-planning overlay only?
6. Are the provisional RV and penetration thresholds acceptable for UAT?
7. What confidence/entity rules authorize automated territory moves for prospects?
8. Should Low-confidence prospects receive the five-tier label at all, or only a coarse Core/Emerging/Enterprise-potential label?
9. Who owns the V3 blank logic, field history, lifecycle exclusion, and review queue?
10. What is the approved CSM capacity model, including onboarding, health, complexity, geography, and multi-product workload?

## Slack-ready update

Completed the documentation/governance refinement without rerunning the core analysis. Recommendation remains a dual-axis model. Technical QA passed; business thresholds and operating-grain validation remain pending. The 284 source-backed records are the ceiling for automation-readiness review, not the total useful directional-scoring population, and strict entity/lane review may reduce that set. Leave the 14,682 provisional prospects unsegmented for territory and coverage while continuing deterministic prioritization from published rank, confidence, MCV point/range, source, and enrichment need. The capacity-validation gate now requires at least 600 adjudicated holdout Accounts, 250 per primary lane, 60 per tier, 30 per lane-by-tier cell, label-reliability checks, two explicit baselines, required lift, and pre-registered 95% uncertainty bounds. No Salesforce, Sheet, warehouse, dbt, core-model, or production changes were made.

## Deliverables and reproducibility

Supporting artifacts are under `tmp/customer_prospect_segmentation_20260716/`.

Key files:

- `row_level_assignments_no_write.csv`: 21,995 Account-level assignments with reason/confidence.
- `analytical_highest_parent_unit_assignments_no_write.csv`: 20,908 highest-parent analytical assignments and overlays, with root/member conflict fields; this includes the one live Salesforce Account missing from the warehouse.
- `current_to_proposed_crosswalk.csv`: Account-count-only live field-to-proposed mappings; unit economics are intentionally excluded.
- `population_distribution.csv`, `cohort_metrics.csv`, `threshold_sensitivity.csv`, `boundary_sensitivity.csv`, and `model_comparison_metrics.csv`.
- `arr_retention_by_current_potential_tier.csv`, `arr_retention_by_current_lane.csv`, and `retention_period_sensitivity.csv`.
- `csm_capacity.csv`: highest-parent, Account-grain, service-overlay, and ratio sensitivities.
- `underpenetrated_customers.csv`: 83 positive-usage candidates plus 13 zero/no-match review cases.
- `salesforce_account_segmentation_query.soql`, `warehouse_customer_spine.sql`, `warehouse_dbt_profile.sql`, and the existing `scripts/account_scoring_customer_history_feature_pull.sql`.
- `analyze_segmentation.py`: reproducible core local analysis; `refine_segmentation_package.py`: local-only post-validation refinement.
- `spreadsheet_review.md`, `salesforce_review.md`, `scoring_model_review.md`, `warehouse_dbt_review.md`, `artifact_inventory.md`, and `adversarial_review.md`.
- `data_dictionary.csv` and `data_quality_checks.csv`.
- `prospect_operating_recommendation.csv`, `active_subscription_arr_quantiles.csv`, `realized_value_band_comparison.csv`, and `service_economics_comparison.csv`.
- `shadow_pilot_exit_criteria.csv`, `artifact_rename_manifest.csv`, and `refinement_independent_checks.md`.

All outputs are no-write analytical artifacts. No production field or ownership recommendation should be applied without the reviewed migration/implementation plan and explicit approval.
