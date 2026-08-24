# Account Scoring Legal Entity Notes

Date: 2026-06-15
Purpose: capture Amanda/Cam legal-entity guidance and translate it into account scoring model changes.

## Executive Summary

Legal entities should not be treated as a simple non-ICP bucket. CertifID has a meaningful law-firm customer base, and several states/markets rely on attorneys or attorney-affiliated entities for real estate closings.

The next model improvement should split law firms into at least three lanes:

1. **Real-estate-closing law firm - lean in.** The website shows real estate closings, settlement, escrow, title, or attorney-led closing services as a primary business line, especially in attorney-led states or counties.
2. **General-practice law firm with real estate arm - moderate/review.** The firm has a real estate practice, but real estate is one of many practices and the website does not clearly show closing/settlement volume.
3. **General legal practice / adjacent legal entity - low value or non-ICP.** Real estate is incidental, or "closing"/"transaction" language is not tied to residential/commercial real estate settlement.

The current model already routes many ambiguous law firms to review instead of forcing a score. The gap is that some law-firm accounts should be actively prioritized when they match the right state/website/customer-fingerprint profile.

## Inputs Reviewed

Google Sheet:

- Spreadsheet id: `1MkjJdRtGUC57uzXh7aq_qoYiG1HxRg2mGKvLNWJ8npM`
- Tabs pulled:
  - `Appendix`
  - `States`
  - `Updated Good Funds`

Local artifacts:

- `artifacts/legal_entities_sheet_states_2026-06-15.csv`
- `artifacts/legal_entities_sheet_appendix_2026-06-15.csv`
- `artifacts/legal_entities_sheet_updated_good_funds_2026-06-15.csv`
- `artifacts/account_minimal_company_type_2026-06-15.csv`
- `artifacts/active_law_firm_customers_2026-06-15.csv`
- `artifacts/closed_won_new_business_q2_2026.csv`
- `artifacts/legal_example_firms_sfdc_2026-06-15.csv`

Dust review:

- Live Dust workspace access was found in the repo-level `.env` and used on 2026-06-15 to pull redacted agent configurations.
- Primary live agent: `Pipeline_Value_Agent` (`tc8VC3hlZn`) - active, visible, description: "Estimates title companies' and law firms' ARR based on website signals."
- Related V2 pilots:
  - `Pipeline_Value_Agent_V2_API_Pilot` (`fce9JNO87h`)
  - `Pipeline_Value_Agent_V2_API_Pilot_Bounded` (`NYDFETulWa`)
  - `Pipeline_Value_Agent_V2_API_Pilot_NoTools` (`LUYRKjfJOX`)
- Supporting/high-velocity agent: `Account_Tiering` (`XCb1Wgofly`). This is useful for website-quality and high-velocity tiering, but it is not the ARR/MCV prospect value model.
- Other Will-authored legal-aware agent: `Day_Prep_Agent` (`3XdTf495MK`). This is a meeting-prep agent, not an ARR scoring agent, but it contains useful state/legal context:
  - resolves the company website from attendee domain
  - determines primary state from Salesforce first, then website
  - uses the CertifID State Information deck for closing structure: Attorney / Title / Split / Escrow
  - checks locations, underwriters, website quality, earnest-money tools, fraud/security signals, and nearby CertifID customers
- I do not see a separate visible legal-only / law-firm-only scoring agent in the Dust agent list available to this API key. If Will has one, it may be private, archived, in another workspace/space, or remembered as the law-firm portion of `Pipeline_Value_Agent` plus the state/legal logic in `Day_Prep_Agent`.
- Redacted local pulls:
  - `artifacts/dust_agent_configurations_light_2026-06-15.json`
  - `artifacts/dust_agent_config_summary_2026-06-15.json`
  - `artifacts/dust_agent_configs_full_2026-06-15/`
- The live `Pipeline_Value_Agent` instructions match the archived 2026-05-04 export exactly by instruction length and SHA-256 hash prefix, so the prior V2 design work is still grounded in the current Dust logic.

## What Amanda/Cam Guidance Adds

Raw guidance from the call notes:

- Legal entities are regionally sensitive.
- Georgia is an attorney-based state, and AEs should prioritize real estate law firms there.
- Nevada may be a market where law firms are a primary sell-to motion.
- Illinois has county-level nuance, especially Cook County attorney-only behavior.
- There is a difference between:
  - law firms dedicated to real estate, and
  - general practice firms that merely have a real estate arm.
- Real estate-dedicated law firms should not be downgraded just because they are legal entities.
- Current examples of strong real-estate legal focus:
  - `stslaw.com`
  - `berlinpatten.com`
  - `nhannalaw.com`

## State Policy Layer From Sheet

The sheet has useful state-market context:

- `State Type`
- `Good Funds Law`
- `Pass Through Ratings`
- `Most Common Pass Through`
- `Rate Filing`
- state-specific notes

Attorney or attorney-relevant states/markets in the sheet include:

- Attorney: Connecticut, Delaware, Georgia, Iowa, Kentucky, Massachusetts, Mississippi, New York, North Carolina, South Carolina, Vermont, West Virginia
- Title Company + Attorney / mixed: Alabama, Florida, Louisiana, Maine, New Hampshire, Rhode Island, Tennessee, Virginia, Washington
- Regional nuance:
  - New Jersey: North NJ attorney, South NJ title company
  - Illinois: often treated as attorney-involved even though not legally mandatory statewide
  - California: North CA title company, South CA escrow company

Important caveat:

- The sheet is state-level. Amanda/Cam notes introduce county-level nuance, especially Illinois/Cook County. The model needs a county/market override layer for states where state-level classification is too blunt.

## Dust Logic Review

The original Dust model is directionally valuable and should be treated as the seed rubric, not discarded.

What the original `Pipeline_Value_Agent` does:

- Scores title companies and real estate law firms across 10 observable website signals.
- Converts the total score into an MCV estimate and ARR range.
- Uses public website evidence as the MCV proxy.
- Includes a dedicated law-firm signal: `S10 - Attorney-State Multiplier`.
- Includes an ICP check for title agencies, escrow companies, settlement companies, abstract companies with closing/escrow services, and real estate law firms in attorney-closing states.
- Flags general-practice law firms as out-of-ICP when real estate is minor and no closing-focused content is visible.

The 10 Dust signals are:

1. Location count
2. Physical geographic reach
3. Team/staff size
4. Underwriter affiliations
5. Digital tool sophistication
6. Entity/structure complexity
7. Service breadth
8. Market establishment
9. Builder/institutional partnerships
10. Attorney-state multiplier for real estate law firms

The strongest part of the Dust rubric for legal entities:

- It already distinguishes real estate law firms from general practice law firms.
- It already recognizes that attorney-closing states change law-firm value.
- It correctly warns that unrelated practice areas should reduce the law-firm multiplier.
- It correctly centers locations, physical market reach, and staff size as the primary capacity signals.
- It has a "primary capacity check" that prevents supporting signals like underwriters or digital tools from pushing a small single-office company into Strategic-level estimates.

The V2 Dust pilot work improved the original model by splitting three concerns:

1. Website and CRM hygiene routing
2. ICP/scorability disposition
3. MCV and pipeline-potential ARR estimation

That V2 split is the right shape for Salesforce production because it avoids turning every ambiguous account into either a score or an error. It supports review actions such as:

- `score_now`
- `url_enrichment_needed`
- `search_fallback_needed`
- `manual_parent_child_review`
- `enterprise_sales_review`
- `duplicate_review`
- `non_icp_confirmed`
- `insufficient_public_evidence`

## Dust Gaps To Fix In The Salesforce Model

Dust is a strong conceptual starting point, but it should not be the production runtime by itself.

Key gaps:

- It is website-first. It does not reliably use Salesforce's MCV source hierarchy, prior Closed Won/Closed Lost opportunity MCV, or sales-rep-sourced MCV anchors.
- It does not use registry/licensing overlays such as VA SCC, CA DFPI, ALTA, NIPR, or state bar settlement-agent sources.
- It has a hardcoded attorney-state list that needs to be reconciled with Amanda/Cam's state sheet and county-level rules.
- It does not handle county-level nuance such as Illinois/Cook County or north/south New Jersey.
- It does not explicitly model Nevada as a law-firm-forward market, which Amanda/Cam called out.
- It does not use the active legal customer fingerprint from Salesforce: revenue, MCV, state, website focus, retention/churn, and product fit.
- It can browse and reason, but its outputs are less auditable than a staged Salesforce pipeline with evidence fields, run IDs, confidence, and review routes.

State-policy reconciliation needed:

- Dust attorney-required list includes: AL, CT, DE, GA, KY, MA, ME, MS, NC, NH, NJ, NY, RI, SC, VT, WV.
- The legal-entity sheet also calls out Iowa as attorney and adds several mixed states.
- The production model should not rely on one hardcoded list. It should use the sheet as the policy layer and allow county/market overrides.

Recommended interpretation:

- Keep Dust's core website scoring logic.
- Keep Dust's S10 concept, but replace it with a more granular legal-market-fit layer.
- Keep V2's separation of routing vs scoring.
- Add Salesforce MCV hierarchy, legal customer calibration, and state/registry data before treating legal-entity scores as production-ready.

## Salesforce Evidence

Current Salesforce Account universe as pulled on 2026-06-15:

- Total Accounts: 21,747
- `Company_Type__c = Law firm`: 9,765
- Law firm Accounts with website populated: 9,634
- Law firm prospects: 9,076
- Law firm customers: 682
- Law firm active customers: 670 by `Account_Status__c = Active Customer`

Top law-firm customer states:

- North Carolina: 117
- Florida: 116
- Georgia: 87
- South Carolina: 56
- Massachusetts: 45
- Connecticut: 30
- Tennessee: 24
- Virginia: 24
- Alabama: 22
- Vermont: 19
- New York: 19

Active law-firm customer metrics from Salesforce:

- `Active_Subscription_Revenue__c`: 639 numeric rows, total about `$5.2M`, median about `$5.6K`, max `$135K`
- `Annual_License__c`: 637 numeric rows, median about `$1.5K`, max `$15K`
- `Final_Monthly_Closing_Volume__c`: 682 rows, median `25`, p75 `50`, max `2,000`

Examples of high-value active law-firm customers:

- Smith Thompson Shaw Colon and Power, P.A. - GA - `stslaw.com` - 250 MCV - about `$79.8K` active subscription revenue
- O'Kelley and Sorohan Attorneys at Law, LLC - GA - `okelleyandsorohan.com` - 1,300 MCV
- McMichael and Gray, PC - GA - `mcmichaelandgray.com` - 2,000 MCV
- Weeks and Irvine, LLC - SC - `weekslawfirm.net` - 800 MCV - about `$135K` active subscription revenue
- Harvey and Vallini, LLC - SC - `hvlawsc.com` - 600 MCV
- Ragsdale Liggett PLLC - NC - `rl-law.com` - 140 MCV
- Law Office of Natasha M. Hanna, P.C. - SC - `nhannalaw.com` - 60 MCV - about `$28.8K` active subscription revenue

Example nuance:

- Berlin Patten Ebling is an active customer at `berlinpatten.com`, but Salesforce currently has `Company_Type__c = Title company`, not `Law firm`. This means legal-entity scoring should not rely only on `Company_Type__c`; the website/legal-service fingerprint matters too.

## Q2 Closed Won Note

A broad Salesforce query for Q2 2026 Closed Won New Business returned:

- 120 Closed Won New Business Opportunities
- 69 Title company
- 47 Law firm

This does not match the raw call note that "this quarter they have closed 89 opps and about 3-4 law firms."

Likely explanation:

- Amanda's number may refer to a narrower product, pricing model, AE team, campaign cohort, or qualified-opportunity definition.

Action:

- Before using Q2 win mix as a calibration label, define the exact opportunity filter:
  - product/pricing model
  - New Business only vs all Closed Won
  - per-file vs other pricing
  - amount populated/nonzero
  - team/owner scope
  - whether renewals/extensions/cross-sells are excluded

## Current Model Handling Of Legal Entities

Current behavior:

- Law-named or legal-looking accounts are not blindly treated as score-ready.
- General law firms with weak/no closing-settlement evidence often route to `insufficient_public_evidence`, `non_icp_confirmed`, or law-firm/manual review.
- Real estate/title/settlement evidence can keep a legal entity scoreable.
- Registry overlays can confirm title/settlement ICP for non-law title entities.
- Virginia SCC non-match is not treated as a law-firm disqualifier, because attorney settlement-agent registration may sit outside SCC.

Known gap:

- The model is currently more defensive than nuanced. It avoids many false positives, but it may under-score law firms that are primary real estate closing businesses in attorney-driven markets.

## Proposed V3 Legal Entity Logic

### 1. Add A Legal Entity Lane

Create a dedicated scoring/routing path for legal entities instead of forcing them into the same title/escrow route.

Suggested normalized legal routes:

- `legal_real_estate_closing_focused`
- `legal_real_estate_practice_unclear`
- `legal_general_practice_low_fit`
- `legal_registry_or_market_confirmed`
- `legal_affiliated_title_entity`
- `legal_county_state_review`

### 2. Website Primary-Business Classifier

The website should answer whether real estate closings are the firm's primary business or a side practice.

Lean-in evidence:

- dedicated real estate closing page
- residential closing / commercial closing services
- settlement agent language
- title insurance / title services
- escrow or trust-account closing services
- buyer/seller/lender closing representation
- multiple attorneys/staff focused on real estate closings
- office/location footprint in attorney-led states
- strong examples that resemble current legal customers

Downgrade evidence:

- broad practice menu with real estate as one of many services
- personal injury, family, criminal, estate planning, litigation, business law as dominant practices
- "real estate transactions" with no settlement/closing/title language
- "closing" used for corporate transactions, litigation settlement, IPO/deal closing, or generic legal matter completion
- "works with title companies" as a counterparty, not as the settlement provider

### 3. State/County Market Weighting

Legal entities should get a state/market fit boost where attorneys are a primary closing vehicle.

State-level boost candidates from the sheet:

- Georgia
- North Carolina
- South Carolina
- Massachusetts
- Connecticut
- New York
- Delaware
- Kentucky
- Mississippi
- Vermont
- West Virginia
- Iowa

Mixed/nuanced states:

- Florida
- Alabama
- Louisiana
- Maine
- New Hampshire
- Rhode Island
- Tennessee
- Virginia
- Washington
- New Jersey
- Illinois
- Nevada

Needed refinement:

- Add county/market overrides for Illinois/Cook County, New Jersey north/south, and any Nevada-specific rule Amanda/Cam confirm.
- Reconcile the Dust attorney-state list with the internal state sheet instead of embedding one static list in scoring code.
- Treat attorney/bar/settlement-agent registry confirmation as a positive legal-ICP signal where available; do not treat absence from a title-agency registry as a law-firm disqualifier.

### 4. Customer-Fingerprint Calibration

Use current legal customers as the calibration set.

Suggested feature set:

- Account state/county
- website real-estate-closing focus
- MCV / `Final_Monthly_Closing_Volume__c`
- active subscription revenue
- annual license
- total revenue where available
- retention/churn status
- website staff/attorney count
- office count
- company type and title-affiliate signals
- legal real-estate-primary classifier from website evidence
- attorney-market/state/county fit
- registry/bar confirmation where available

Near-term Salesforce-only path:

- Use `Active_Subscription_Revenue__c`, `Annual_License__c`, `Total_Revenue__c`, `Final_Monthly_Closing_Volume__c`, `Account_Status__c`, and `Churn_Date__c`.

Possible later path:

- Pull product/usage actuals from Supabase if Salesforce revenue fields are not enough. The note from the call was that V3 billing may not contain actuals, so this needs validation before it becomes a dependency.

### 5. Account Sourcing

The same logic can be used not just to score existing Accounts, but to source new legal prospects.

Search/source patterns:

- "real estate closing attorney" + state/city/county
- "settlement attorney" + state/city/county
- "real estate law firm closings" + state/city/county
- "title attorney" + state/city/county
- "residential closing attorney" + state/city/county

Prioritize:

- attorney-led states and confirmed county-level attorney markets
- markets where current legal customers are strong
- websites with primary real estate closing focus

## Recommended Next Steps

1. Confirm legal-entity policy with Amanda/Cam:
   - What should count as real estate-primary?
   - Which states/counties should boost legal entities?
   - Where should legal entities remain low-value?
   - Should Nevada be treated as a law-firm-forward market even if it is not in the classic attorney-closing-state list?

2. Define the Q2 opportunity calibration filter:
   - reconcile broad SFDC 47 law-firm CW New Business count vs call-note 3-4 count.

3. Build a 50-100 account legal validation set:
   - high-value active legal customers
   - low-value/churned legal customers
   - current prospects in attorney-led states
   - examples Amanda/Will/Cam already trust

4. Add legal-entity classifier fields to the scoring output/evidence:
   - website real-estate primary/secondary/general
   - attorney-state boost
   - county override if available
   - legal-customer fingerprint match
   - legal disposition route
   - registry/bar/license confirmation source and date where available

5. Re-run a bounded legal prospect sample before full-universe scoring.

6. Productize the Dust logic as Salesforce pipeline logic:
   - deterministic Salesforce MCV/source hierarchy first
   - website/Nimble evidence extraction second
   - state/county/legal-market policy third
   - AI adjudication only for ambiguous website/legal-focus cases
   - writeback to Salesforce with evidence, confidence, model version, and run ID

## Implementation Update - 2026-06-15

Initial specialized law-firm handling has been added to the local scoring pipeline.

Changed files:

- `scripts/legal_entity_scoring.py`
- `scripts/run_greenfield_nimble_test.py`
- `scripts/build_greenfield_nimble_review_writeback.py`

What changed:

- Added a reusable legal-entity classifier instead of burying law-firm policy inside the Nimble runner.
- Added normalized legal routes:
  - `legal_real_estate_closing_focused`
  - `legal_real_estate_practice_unclear`
  - `legal_general_practice_low_fit`
  - `legal_affiliated_title_entity`
  - `legal_market_review`
  - `not_legal_entity`
- Added state/market fit classification:
  - `attorney_primary`
  - `attorney_relevant`
  - `county_nuanced`
  - `standard`
- Added output columns to scoring/review artifacts:
  - `LegalEntityRoute`
  - `LegalMarketFit`
  - `LegalEvidence`
- Added conservative legal-market MCV floors only when the law firm has explicit real-estate closing/title/settlement evidence.
- Routed broad/general-practice law firms without real-estate closing evidence to `non_icp_confirmed`.
- Routed ambiguous law firms in attorney-relevant markets to review instead of forcing a score.
- Preserved title-agency behavior: title companies that merely mention attorneys are not treated as law firms.
- Added legal route/market/evidence into Salesforce writeback evidence and JSON components.
- ARR is treated as pipeline-potential ARR based on similar closed-account lookbacks, not booked/forecast ARR. Booked ARR is retained as a sanity/backtest reference rather than the direct optimization target.

Smoke-test result:

- A Georgia real-estate-closing law-firm fixture scored as `score_now` with `legal_affiliated_title_entity` and `attorney_primary` evidence.
- A Texas personal-injury/general-practice law-firm fixture routed to `non_icp_confirmed`.
- A title-agency fixture that mentions attorneys stayed `not_legal_entity` and scored through the normal title route.

Remaining before production use:

- Run the updated pipeline against a real legal validation cohort.
- Review high-value active legal customers and low-fit/general-practice legal prospects.
- Add county-level overrides once Amanda/Cam confirm specific markets such as Cook County and north/south New Jersey.
- If desired, add state-bar/settlement-agent source checks for attorney-heavy states where public registries exist.

## Implementation Update - 2026-06-16

The initial 2026-06-15 legal lane was hardened across several test-and-fix rounds and is now converged and validated (no Salesforce writes during testing; the review cohort was pushed live afterward).

Fixes made after the initial build:

- **Conservative routing.** Broad/general-practice firms no longer score from a single incidental closing/title keyword. The classifier now separates operational closing evidence (e.g., "closing attorney", "real estate closings", "settlement agent") from weak RE evidence (e.g., a lone "real estate" mention), and only the former can drive a score.
- **State-independent suppression.** General-practice firms with no closing evidence can route to `non_icp_confirmed` regardless of state. This fixed an earlier gap where general-practice BigLaw in attorney-market states (e.g., Willkie Farr, Holland and Knight, Baker Donelson) could not be suppressed and instead clogged manual review.
- **Customer-anchor protection, correctly gated.** A confirmed customer (`HasAnyOpp = customer`) with a trusted rep-provided MCV always stays scoreable on its real number and is never buried by website classification. Non-customer rows with a trusted source token no longer bypass routing on the anchor alone.
- **Anchor plausibility rail.** An uncorroborated trusted-source fallback above ~750 MCV now routes to review with a verify flag instead of writing an inflated value. This caught a mis-keyed input (Summit Settlement, anchor 6,000 vs. a booked label of ~101) before it reached the writeback.
- **Sub-75 anchor cap.** Sub-75 anchors cap the website estimate at 2x to prevent small/stale anchors from being inflated by website signals.
- Fixed an MCV band gap and cleaned writeback provenance (no stale run names).

Validation:

- Combined 200-account legal/title cohort outcome: 77 `score_now`, 62 `manual_review` / `search_fallback_needed`, 39 `non_icp_confirmed`, 21 `insufficient_public_evidence`, 1 `hygiene_review`.
- Pushed live to a Salesforce review report (`00OTP00000Enjrx2AB`); model version `account_value_legal_lane_anchor_protected_v1_20260616`.
- Value backtest against known customer MCV/ARR labels: **MCV lands on target** (median absolute error ~5; ~40 excluding the one mis-keyed anchor). The recovered genuine-ICP customers all score correctly (Smith Thompson Shaw/`stslaw.com`, Natasha Hanna, O'Kelley and Sorohan, McMichael and Gray, Weeks and Irvine, Sterling Title, Harvey and Vallini, Ragsdale Liggett, Hunter and Chandler).
- The 7 prior leakage cases (Kaufman Dolowich, Parker Poe, Steptoe and Johnson, Hunton Andrews Kurth, Kean Miller, Hutchens, Law Office of Maria C Rogers) are correctly held out of `score_now`.

Decision settled:

- **ARR is Pipeline Potential ARR, not booked.** The backtest confirms predicted ARR runs above booked by design (~1.8-2.5x, and the gap grows by tier as booked ARR flattens at the top). Booked ARR is a backtest/sanity reference. The remaining ARR work is calibrating the potential bands from comparable closed accounts (e.g., a high percentile of booked ARR at each MCV/segment), not pulling ARR down to booked.

Open / next:

- Confirm with Amanda/Cam: Nevada treatment, and county overrides for Cook County (IL) and north/south New Jersey.
- Title/escrow hardening pass (blind backtest with anchors held out, controlled title-side entity routes, office/location dedup, potential-ARR band calibration) before any full-universe production run.
- Leadership-facing summary of the full system lives in `account_scoring_model_explainer_external_2026-06-10.md` (updated 2026-06-16).
