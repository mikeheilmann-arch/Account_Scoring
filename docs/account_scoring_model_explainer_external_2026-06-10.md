# Account Scoring Model Leadership Brief

Prepared by Mike Heilmann
Last updated: June 17, 2026
Audience: Finance and RevOps leadership, including leaders who are new to CertifID and this project

## Executive Summary

This model helps Sales and RevOps answer a practical question: which prospect accounts are most likely to be worth a rep's time?

It estimates an account's likely monthly closing volume (MCV), converts that estimate into a pipeline-potential ARR point/range, and writes the result into hidden Salesforce Account fields with confidence, routing action, URL status, and evidence. The goal is not to replace sales judgment. The goal is to make prospect prioritization more consistent, auditable, and easier to improve.

Two framing points that matter for Finance and RevOps:

- **MCV (closing volume) is the calibrated number.** In the legal-lane validation set, it has now been backtested against the actual known closing volume of real customers and lands on target. The title/escrow path is the next place to run the same blind validation.
- **ARR is "Pipeline Potential ARR," not booked or forecast ARR.** It is a prioritization and value-sizing signal (a ceiling), and against historical booked revenue it intentionally runs higher. Read it as "how big could this account be," not "what it will pay."

The work started from Will Marty's internal Dust scoring concept: use public business signals to estimate account value. The rebuilt version keeps that idea, but moves the durable output into Salesforce so the model can be reported on, tested, audited, refreshed, and eventually used in segmentation or territory planning if the business trusts it.

Current status:

- Production Salesforce hidden scoring fields are deployed and populated for controlled review cohorts.
- A **dedicated legal / law-firm lane is now built and validated** (real-estate law firms, attorney-closing markets, and title-company lookalikes are now handled instead of being a known gap).
- Legal-lane routing and MCV estimates have been **backtested against known customer closing volume** and are landing on target.
- Virginia, California, and a new **200-account legal/title review cohort** are live in Salesforce.
- No Account layouts have been changed, and no `Account.Website` values are being overwritten by the scoring runs.
- The model is being used for list prioritization and feedback, not as a fully automated segmentation cutover.
- The next build is a **title/escrow hardening pass**, which should land before any full-universe production run.

## What The Model Produces

Each scored Account receives:

- Estimated monthly closing volume (MCV)
- Pipeline Potential ARR point and range
- Confidence (`High`, `Medium`, `Low`)
- ICP/disposition, where ICP means ideal customer profile
- Recommended action
- Canonical URL used for scoring
- Evidence explaining the score (now including legal-entity route, attorney-market fit, and the reasoning behind the disposition)
- Model version, source, run id, and updated timestamp

The most important review fields are:

- `AI Prospect Value Action`
- `AI Prospect Pipeline Potential ARR Point` (API name `AI_Prospect_Value_ARR_Point__c`)
- `AI Prospect Pipeline Potential ARR Range` (API name `AI_Prospect_Value_ARR_Range__c`)
- `AI Prospect Value MCV Point`
- `AI Prospect Value Confidence`
- `AI Prospect Value ICP`
- `AI Prospect Value URL Status`
- `AI Prospect Value Evidence`
- `AI Prospect Value Run Id`

Important: `Account Segment v2` is the existing/legacy segmentation field. It is not the new scoring model's action field. If `Account Segment v2` says `Needs Review`, that does not mean the new model routed the account to review.

A note specifically for Finance: the ARR fields are a **pipeline-potential ceiling**, not booked ARR. An account can show a higher potential ARR than it actually pays today. Use MCV and Action/Fit as the calibrated prioritization signals; treat ARR as directional potential. To make this unambiguous, the production fields were relabeled on June 16, 2026 (deploy `0AfTP0000047ygH0AQ`) to **AI Prospect Pipeline Potential ARR Point / Range**, with help text stating the values are directional pipeline potential for prioritization, not booked or forecast ARR. The API names (`AI_Prospect_Value_ARR_Point__c` / `_Range__c`) are unchanged, so existing report filters and integrations are unaffected.

## The Core Logic

The model has three layers.

### 1. Value

This answers: how valuable could this account be?

The model estimates account value from:

- Known monthly closing volume in Salesforce
- Prior New Business opportunity data
- Trusted account-side MCV fields
- Number of offices/locations
- Visible staff/team size
- Title, escrow, settlement, closing, and real-estate transaction language
- Website depth and quality
- Digital closing/process signals such as calculators, EMD tools, portals, order forms, or service pages
- Registry/licensing context where available

### 2. Confidence

This answers: how much should we trust the score?

Confidence is higher when Salesforce has a reliable volume anchor, the website clearly belongs to the company, registry/licensing data confirms the account is in-market, and the website exposes strong location/team/service evidence.

Confidence is lower when the website is missing, thin, blocked, or likely wrong; when the company has same-name lookalikes; or when the account may be a brokerage, parent company, duplicate, or adjacent business.

### 3. Action

This answers: what should Sales or RevOps do next?

Common actions:

- `score_now`: enough evidence exists to prioritize the account.
- `url_enrichment_needed`: the website is missing or likely wrong.
- `search_fallback_needed`: basic enrichment did not work, but targeted search may help.
- `insufficient_public_evidence`: the account may be real, but public evidence is too thin to trust a confident score.
- `non_icp_confirmed`: evidence suggests the account is not a title, escrow, settlement, or closing-related fit.

The model is intentionally allowed to route accounts instead of forcing a score. A wrong score that looks confident is more damaging than an honest review flag.

## Legal / Law-Firm Handling

This is the newest and most heavily tested capability. Previously, law firms were a known weak spot. They are now handled by a dedicated lane, because CertifID has a meaningful law-firm customer base and many states rely on attorneys, rather than title companies, to close real estate.

In plain terms, the model now:

- **Distinguishes a real-estate-closing law firm from a general-practice firm.** A firm whose business is real estate closings/settlement can score as a target. A broad general-practice or "BigLaw" firm that merely mentions real estate routes to review or to out-of-ICP, so AEs are not pointed at a litigation or general-practice shop that happens to use the word "closing."
- **Is attorney-market aware.** It prioritizes law firms in attorney-closing states (Georgia, North Carolina, South Carolina, New York, and similar), aligned to CertifID's internal state / good-funds reference, where the law firm is the closing vehicle.
- **Protects existing customers.** A current customer with a trusted, rep-provided closing volume stays scoreable on its real number and is not buried in review by a website-classification quirk.
- **Is conservative by design.** A firm cannot be promoted to `score_now` on a single stray "real estate" mention; it needs stronger title/settlement/closing evidence.
- **Will not mislabel title companies.** A title or escrow company whose site merely mentions attorneys is not pulled into the legal lane.

The model records its legal reasoning in the Evidence field (the legal route, the attorney-market fit, and the supporting signals), so any law-firm score or review flag is auditable. The detailed policy, state logic, and customer examples behind this lane live in `account_scoring_legal_entity_notes_2026-06-15.md`.

## Validation And Backtesting

The model is no longer validated only by eyeballing campaign lists. The legal lane now has a formal backtest against ground truth, and the same validation pattern will be applied to the title/escrow path next.

- **Legal-lane MCV is backtested against known customer closing volume.** On labeled legal/law-firm accounts, the monthly-closing-volume estimate lands on target (typical error is a few closings per month). Legal routing and MCV are the calibrated, trustworthy part of the model today.
- **ARR is validated as a potential ceiling, not a forecast.** Against booked revenue, predicted ARR runs higher (roughly ~2x at the top end) by design, because ARR is defined as pipeline potential. The next refinement is calibrating those potential bands against comparable closed accounts so the ceiling is grounded rather than arbitrary.
- **A data-quality guardrail is in place.** If a Salesforce volume input is implausible or cannot be corroborated by public evidence (for example, a mis-keyed closing-volume figure that is far larger than the firm's real footprint), the account is flagged for verification instead of writing a bad value.

The honest read: the legal lane and legal-lane MCV estimate are now the most rigorously validated parts of the model. The title/escrow value side is directionally useful and commercially relevant, but it has not yet had the same blind, anchor-held-out backtest. That is the focus of the next build (see "What's Being Built Next").

## Source Hierarchy

The most important design decision is that the model does not treat all data sources equally.

The model trusts sources in this order:

1. Latest closed New Business opportunity monthly closing volume
2. Trusted Sales Rep or BDR account-side monthly closing volume
3. Latest/open New Business opportunity volume formulas
4. Marketing-sourced account monthly closing volume
5. Website-implied volume when no reliable Salesforce volume exists

In plain English: if Salesforce already has reliable sales history, believe that first. If Salesforce does not have a useful volume anchor, use public evidence to estimate capacity.

This source hierarchy came directly from Will's feedback. Early versions sometimes let weak website estimates override stronger volume history, which undervalued accounts with known historical closing volume. The current model uses opportunity/account MCV as the anchor and treats website evidence as the fallback, with the data-quality guardrail above so an implausible anchor is verified rather than trusted blindly.

## Website And Enrichment Logic

When there is no trusted Salesforce volume anchor, the model estimates capacity from public footprint signals:

- Office count and geographic footprint
- Staff/team visibility
- Title, escrow, settlement, closing, and underwriting services
- Residential, commercial, builder, lender, or specialty transaction evidence
- Digital workflow signals
- Overall site depth and professionalism

Nimble-powered website extraction is currently used to pull structured public-site evidence at scale. The extraction tool is not the strategic core of the model; the strategic core is the source hierarchy, scoring rubric, registry/licensing overlays, confidence logic, legal-entity lane, and Salesforce writeback/reporting layer.

Generic enrichment is useful, but it is not enough by itself in this vertical. Title, escrow, settlement, and real-estate law accounts often require industry-specific data sources to confirm ICP and avoid false positives.

## Registry And Licensing Overlays

The Virginia and California tests showed that licensing/registry data is the clearest way to answer the ICP question.

What the registry layer is good for:

- Confirming whether an account is a licensed title/settlement/escrow business
- Correcting cases that generic scoring might treat as questionable or non-ICP
- Separating "is this in-market?" from "how big is it?"

What the registry layer does not fully solve:

- It does not usually provide closing volume.
- It does not always solve law-firm/attorney settlement-agent cases (the legal lane handles those on website + state-market evidence; bar/settlement-agent registries are a future enhancement).
- It varies by state, so the model needs state-specific adapters or national sources where available.

Virginia proved the pattern. The Virginia State Corporation Commission (SCC) Bureau of Insurance title/settlement agency lookup is a strong positive ICP signal for title/settlement agencies. For law firms, SCC non-match is not enough because attorney settlement-agent registration can sit with the Virginia State Bar instead.

California used a different pattern. Amanda's California source report was already filtered to `CA_DFPI_Licensed__c = True`, so California Department of Financial Protection and Innovation (DFPI) licensing was treated as an ICP/source context signal for that campaign.

This likely becomes a repeatable operating model:

- Use Salesforce history first for volume.
- Use state or national registry/licensing data for ICP confirmation.
- Use websites for capacity and evidence.
- Route ambiguous accounts instead of pretending every account can be scored perfectly.

## Current Salesforce Runs

### Virginia ID Outreach

Report: [AI Virginia ID Outreach Scoring](https://certifid2022.lightning.force.com/lightning/r/Report/00OTP00000ESo8L2AT/view)
Original run id: `virginia_id_outreach_v12_20260609`
Accepted resolver refresh run id: `va_resolver_overlay_accepted_20260610`

The Virginia campaign started from a real Sales/BDR use case: prioritize a Virginia outreach list where the team did not know which accounts were likely to be large.

Original run:

- 726 contact rows deduped to 236 unique Accounts
- 236 Accounts written to hidden production scoring fields
- 126 `score_now`
- 54 `insufficient_public_evidence`
- 34 `url_enrichment_needed`
- 16 `search_fallback_needed`
- 6 `non_icp_confirmed`
- 0 Salesforce writeback failures

Virginia resolver follow-up:

- 45 unresolved/problem Accounts sampled for vertical-specific resolution
- VA SCC licensing added as an authoritative title/settlement ICP signal
- Candidate website precision on current labels: 24 of 26, or 92.3%
- Accepted-only refresh wrote 14 improved rows back to hidden scoring fields
- 4 rows were intentionally parked for law-firm/manual review
- The Salesforce report now includes both the original Virginia run and the accepted refresh run
- No `Account.Website` values were changed

Interpretation: the resolver is precise when it promotes a candidate website, and registry data materially improves ICP confidence. It does not magically fix every bad or missing website.

### California DFPI

Report: [AI California DFPI Scoring](https://certifid2022.lightning.force.com/lightning/r/Report/00OTP00000EbmdV2AR/view)
Run id: `california_dfpi_v12_20260611`

The California run came from Amanda's request to apply the model to a California DFPI account population.

Run summary:

- Source report: `DFPI Escrow Contacts - CA`
- 1,907 contact rows deduped to 477 unique Accounts
- Source report already required `CA_DFPI_Licensed__c = True`
- 477 Accounts written to hidden production scoring fields
- 422 `score_now`, 39 `insufficient_public_evidence`, 6 `url_enrichment_needed`, 5 `search_fallback_needed`, 5 `non_icp_confirmed`
- 0 Salesforce writeback failures; report verification returned 477 rows

Interpretation: California is a strong campaign-prioritization test because the list is already DFPI-licensed, so most accounts are in-market and the model's main job is to estimate relative value. The biggest limitation is that most records do not have Salesforce MCV anchors, so the run is heavily dependent on website evidence.

### Legal / Title Validation Cohort

Report: [AI Legal Title Law Scoring](https://certifid2022.lightning.force.com/lightning/r/Report/00OTP00000Enjrx2AB/view)
Model version: `account_value_legal_lane_anchor_protected_v1_20260616`

This is a validation/review cohort, not a campaign list. It is an intentionally diverse 200-account set: law firms across attorney, title, and mixed states, plus title-company controls and deliberately hard edge cases. It was built to stress-test the new legal lane. It was scored with no Salesforce writes across multiple test-and-fix rounds, then pushed live so reviewers can see how the model handles difficult cases.

Run summary (200 accounts):

- 77 `score_now`
- 62 `search_fallback_needed`
- 39 `non_icp_confirmed`
- 21 `insufficient_public_evidence`
- 1 `url_enrichment_needed`
- No Account layouts changed; no `Account.Website` values overwritten

Interpretation: this cohort is curated and adversarial, so the higher review/non-ICP share is expected and healthy. Broad general-practice firms are being filtered out rather than force-scored, and real-estate-closing firms and existing customers are surfaced correctly. It validates legal-lane routing and MCV; ARR is a pipeline-potential ceiling pending band calibration.

## How To Read The Reports

A practical review workflow:

1. Filter to `AI Prospect Value Action = score_now`.
2. Sort by `AI Prospect Value MCV Point` (the calibrated signal) descending; use `AI Prospect Pipeline Potential ARR Point` as directional potential, not as expected revenue.
3. Use `AI Prospect Value Confidence` to separate high-confidence priorities from accounts that need more scrutiny.
4. Read `AI Prospect Value Evidence` for any account that looks surprising (it now includes the legal route and market-fit reasoning for law firms).
5. Flag misses: wrong website, missing location count, ignored opportunity history, law-firm ambiguity, or over/under-estimated potential ARR.

For cross-run reports, do not over-index on rank alone. Rank is scoped to the model run/population. Potential ARR point, MCV point, action, confidence, and evidence are the better review fields.

## Where The Model Is Strong Today

The model is strongest when:

- There is reliable Salesforce MCV history.
- The account has a reachable official website.
- The website exposes office, team, service, or transaction evidence.
- Licensing/registry data confirms the account is in-market.
- The account is a straightforward title, escrow, settlement, or closing business, or a real-estate-closing law firm in an attorney market (now a validated lane).

## Where The Model Still Needs Work

The model still needs refinement for:

- Brokerages and adjacent real-estate businesses
- Parent/child account structures and duplicate Accounts
- Missing, dead, thin, blocked, or wrong websites
- Same-name companies across different cities or states
- Pipeline-potential ARR bands, which should be calibrated against comparable closed accounts so "potential" remains grounded
- The title/escrow value side specifically, which has not yet had the same blind, anchor-held-out ground-truth backtest as the legal lane (the next build; see below)
- Broad full-universe scoring, where state-specific licensing sources and account-typing edge cases become the operational work

The most important limitation for leadership: scoring the full Account universe is mechanically close, but the interpretation and routing buckets become the operational work. A full run can produce scores and review lanes, but some portion of the universe will need state-specific registry handling, website correction, or Sales/RevOps review.

## What's Being Built Next: Title/Escrow Hardening

The legal lane is now the most rigorously tested part of the scorer. The next project applies that same rigor to the core title/escrow path, before any broad rollout. Four steps:

1. **A blind title/escrow backtest.** Score known title/escrow customers from public evidence only, setting their known closing volume aside, and compare to the actual figure. This honestly measures the website model (the part that must work on net-new prospects). The label data already exists in Salesforce (roughly 990 title/escrow customers with a known closing volume), so this is a measurement run, not a data-collection problem.
2. **Controlled title-side categories.** Clean, consistent account types (title/escrow operator, settlement agency, abstract company, underwriter, vendor, brokerage, non-real-estate "title"), mirroring what was done for law firms, so every account is typed the same way and the model is reportable by segment.
3. **Tighter office/location counting,** so a firm's branch count is not over-stated (which can inflate the largest accounts).
4. **Calibrating the Pipeline Potential ARR bands** against comparable closed accounts, so potential is grounded and falsifiable rather than an arbitrary multiplier.

Sequencing decision: **hold any full-universe production run until the title backtest and account-typing land.** A curated cohort hides the messy edges (adjacent vendors, mis-typed accounts); a full-universe run is exactly where those show up at scale, so the validation should land right before the exposure does.

## What This Means For RevOps And Finance

This should be treated as a prioritization and data-quality operating system, not just a score.

Near-term use cases:

- Prioritize campaign lists for BDRs/AEs (including real-estate-closing law firms in attorney markets)
- Compare model estimates against Sales judgment
- Surface high-value greenfield accounts with no opportunity history
- Identify bad/missing website issues
- Separate score-ready accounts from enrichment/review accounts
- Provide inputs to future segmentation, without immediately replacing current segmentation

Recommended leadership review questions:

- Do the top 25-50 accounts in each campaign report look directionally right?
- Are the misses mostly scoring logic, bad source data, or true market ambiguity?
- Does Finance also want a separate booked/expected ARR estimate in addition to Pipeline Potential ARR? (Current decision: ARR in this model means potential, used for prioritization.)
- Should this become an input to account segmentation?
- Who owns feedback loops from Sales back to RevOps?
- Which data sources are worth institutionalizing: ALTA, NIPR, state registries, DFPI/SCC-style sources, state bar/settlement-agent sources, or a licensed data provider?
- Where should the recurring scoring runtime live if this becomes operational?

## Bottom Line

The model is no longer just a rubric. It is a Salesforce-native scoring pipeline with hidden production fields, live review reports, a source hierarchy, confidence routing, evidence, a validated legal/law-firm lane, a backtested legal-lane MCV estimate, and a data-quality guardrail, with early campaign validation in Virginia and California and a diverse legal/title review cohort now live.

The current posture is right: use the reports to prove value, collect Sales/RevOps feedback, and expand carefully. Salesforce remains the system of record, public evidence is auditable, ARR is treated as pipeline potential rather than booked revenue, and ambiguous accounts are routed instead of forced into false precision. The immediate next step is the title/escrow hardening pass before any full-universe production run.
