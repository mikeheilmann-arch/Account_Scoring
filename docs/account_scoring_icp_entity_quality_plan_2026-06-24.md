# CertifID Account Scoring — ICP / Entity-Quality Pre-Gate Plan

Date: 2026-06-24
Status: Planning + audit only. No production changes. For Codex / Will / Amanda review before any build.
Scope of run reviewed: the ~2k greenfield scoring run `greenfield_gcp_2k_v1_20260619` (model `account_value_nimble_v1_2_gcp_greenfield_2k_20260619`), written to production hidden `AI_Prospect_Value_*` fields on 2026-06-19.

Constraints honored throughout: no Salesforce writes, no `Account.Website` updates, no service deploys, no Nimble/GCP jobs, no secrets printed. Everything below is derived from local artifacts and read-only static analysis. All headline numbers were re-verified locally this session (see Appendix C).

---

## 1. Executive summary (Slack-ready)

Will's read of the 2k run is correct, and the evidence is unambiguous: **the value model is largely fine; the CRM/entity structure feeding it is the bottleneck.** Of the 14 accounts Will flagged Red/Orange, **12 (~86%) fail upstream of the value model** — wrong website, non-ICP entity, parent/child rollup, or already-a-customer — and **only 2 are genuine value-model overestimates** (and even those are thin-site office-count artifacts, not value math).

Root cause: the 2k run was fed only `Name, Website, State, Segment, MCV-source, hygiene`. It was **structurally blind** to `ParentId`, `Account.Type`, `Company_Type__c`, and any duplicate/domain key — so it had no way to disqualify the entities Will flagged. The good news: **all of that data is already on local disk** (covers 2000/2000 and 1937/2000 of the cohort), so we can build and validate a fix **now, with no new Salesforce dependency.**

The fix is Will's own framing: **gate first, score second.** Add an ICP/entity-quality gate with four layers — (1) website↔account match, (2) ICP classification, (3) parent/child/sellable-account resolution, (4) value scoring only after the gates pass — driven by a controlled 15-value disposition taxonomy.

**Recommended path: build a no-write QC overlay on the 2k run first** (validates the gate against Will's labels and doubles as a do-not-trust list for the bad rows already live in prod), **then** harden it into a pre-scoring module in the scorer, **then** rerun with suppression. ~70% of the gate is assembly of assets we already have (vertical resolver, dedupe clusters, ALTA/SCC/DFPI overlays, `Company_Type__c`, legal lane); the one real new build is a consolidated underwriter/bank/county/brokerage/abstract suppressor.

**Immediate next step:** Phase 0 — join the local CRM-structure files onto the 2k review file and publish the per-account disposition for Will's 19 plus the full score_now cohort. Zero code change, zero risk, this week.

---

## 2. Diagnosis from the 2k run

### 2.1 Cohort facts (verified)

| Field | Value |
|---|---|
| run_id / model | `greenfield_gcp_2k_v1_20260619` / `account_value_nimble_v1_2_gcp_greenfield_2k_20260619` |
| rows | 2000 — written to prod hidden Account fields 2026-06-19 (live now) |
| SourceSet / TestLane | uniform: `sfdc_greenfield_2k_2026-06-19` / `gcp_2k_validation` |
| Account.Type (all 2000) | **Prospect** (greenfield pull; 0 Customers) |
| Company_Type__c (all 2000) | **Title company 1401 · Law firm 599** (cohort pre-filtered to ICP class) |
| Action split | **score_now 1295** · insufficient_public_evidence 293 · search_fallback_needed 281 · non_icp_confirmed 118 · url_enrichment_needed 13 |
| Confidence | High 478 · Medium 945 · Low 577 |
| Segment | Core 1099 · Strategic 307 · blank 585 · `< 10` 9 |
| InputFinalMCV on score_now | **0 / blank for 100% (1295/1295)** — pure greenfield; every estimate is website-derived only |

**Structural artifact that hides the failures:** score_now collapses onto a few discrete MCV rungs, and `EstimatedARR` clamps to **$150K for the entire MCV 500/750 tier**. A mis-bound underwriter site and a genuine Strategic title therefore get an *identical* top-of-list score — which is exactly why the worst wrong-website cases sit at ReviewRank 1–20 where Will loses confidence.

### 2.2 The 19 Will-reviewed accounts vs the model (corrected and verified)

R = Red/bad, O = Orange/caution, G = Green/good. All located in `batch2k_salesforce_review_2026-06-19.csv`. Office counts and ranks re-verified locally.

| # | Account (state) | Will | Model | MCV | ARR | Model website | Error category | Diagnosis |
|---|---|---|---|---|---|---|---|---|
| 1 | Title Companies In (WI) | R | score_now | 750 | 150K | `wlta.org` | wrong_website | Bound to the WI Land Title **Association**; the 85 "offices" are member firms. Rank 1. |
| 2 | Tennessee Title (TN) | G | score_now | 750 | 150K | tennesseetitle.com | true_positive | Correct site, real multi-office title co. |
| 3 | SB. Titles (CT) | R | score_now | 750 | 150K | `yourstatebank.com` | wrong_website | Bound to a **bank**; 36 "offices" = branches. Rank 3. |
| 4 | Northern New York Title Agency (NY) | R | score_now | 750 | 150K | `northernny.ctic.com` | wrong_website / non_icp | `*.ctic.com` = Chicago Title (underwriter) subdomain; can't sell. OfficeCount **22**. Rank 4. |
| 5 | Metro Title and Escrow (MD) | G | score_now | 750 | 150K | metrotitleandescrow.com | true_positive | Correct, 26 offices, WebQuality 5. |
| 6 | Meridian Title and Research (MA) | R | score_now | 750 | 150K | meridiantitle.com | already_customer/dup | Domain belongs to a current large customer (Meridian Title Corp.); this is the small MA "and Research" record. |
| 7 | Master Settlement Services (PA) | R | score_now | 750 | 150K | `nextierbank.com` | wrong_website | Bound to NexTier **Bank**; 64 "offices" = branches. |
| 8 | Greenridge Title Agency (MI) | R | score_now | 750 | 150K | greenridge.com | non_icp | Generic domain reads as brokerage/builder; title ICP not confirmable. |
| 9 | Greco Title Agency (IL) | O | score_now | 750 | 150K | `atatitle.com` | hierarchy | Underwriter ATA National domain; 44 offices. Rank 9. |
| 10 | Global Title and Escrow Services (FL) | R | score_now | 750 | 150K | gtes.exchange | value_overestimate | Correct domain but a thin `.exchange` 1031 shell; model counted 143 phantom offices → ceiling. |
| 11 | Denver Land Title (CO) | O | score_now | 750 | 150K | `ltgc.com` | hierarchy | Reads parent **Land Title Guarantee** (LTGA); 92 offices are the parent's. Roll up. |
| 12 | Clear Title Co. (TX) | R | score_now | 750 | 150K | cleartitlecompany.com | value_overestimate | WebQuality 2, single rural location, but 24 phantom offices → $150K ceiling. |
| 13 | Apex Title and Settlement Services (VA) | G | score_now | 750 | 150K | apex-closings.com | true_positive | Correct, WebQuality 5. (Shares domain with a sibling "Apex Title Solutions" → dup/parent.) |
| 14 | Absolute Title (TN) | O | score_now | 750 | 150K | `atatitle.com` | hierarchy | Same domain + 44 offices as Greco, **but a distinct row** (rank 15, TN). Underwriter-owned (ATA). |
| 15 | Title Guaranty (IA, scored on tghawaii.com) | G\* | score_now | 500 | 150K | tghawaii.com | wrong_website (binding) | Account is **Iowa**; site is **Hawaii**. State conflict — good number, wrong entity. (A legit Iowa "Title Guaranty" exists at rank 708 on `cap.iowatitleguaranty.com`.) |
| 16 | Texas Capital Title (TX) | O | score_now | 500 | 150K | `ctot.com` | hierarchy | `ctot.com` = parent **Capital Title of Texas**; consolidate. |
| 17 | Supreme Title Closings (FL) | G | score_now | 500 | 150K | supremetitlellc.com | true_positive | Correct, Strategic. |
| 18 | Real Estate Title Service (MD) | R | score_now | 500 | 150K | retitleservice.com | non_icp | Abstract-only; no escrow/closing/settlement. |
| 19 | Ionia County Title (MI) | R | score_now | 500 | 150K | `ioniacounty.org` | wrong_website | Bound to a **county-government** portal; WebQuality 2. |

\* Will scored #15 Green because the number off `tghawaii.com` was good, but it is bound to the wrong Account record (Iowa). It is a binding/wrong-website fault, not a value win.

> Note on duplicates beyond the 19: there are **two distinct "Absolute Title" accounts** in the cohort — #14 (TN, `atatitle.com`, MCV 750, rank 15) and a separate one (SC, `absolutetitlesc.net`, MCV 25, rank 1293). The pre-gate must disambiguate by Id/domain, not by name.

### 2.3 Upstream vs value-model split

| Error category | Count (of the 14 R/O) | Accounts |
|---|---|---|
| wrong_website | 6 | Title Companies In, SB. Titles, Northern NY, Master Settlement, Ionia County, Title Guaranty(IA) |
| hierarchy / parent-child | 4 | Greco, Denver Land Title, Absolute Title, Texas Capital Title |
| non_icp (entity class) | 2 | Greenridge, Real Estate Title Service |
| already_customer / dup | 1 | Meridian |
| genuine value_overestimate | 2 | Global Title & Escrow, Clear Title Co. |

**12 of 14 (~86%) of Will's flagged failures are upstream of the value model** — and the 2 value cases are office-count/retrieval artifacts (143 and 24 phantom offices off thin sites), not value math. Will's thesis holds: identify the actually-ICP, actually-sellable account first, then size it.

### 2.4 Root cause — and why it is cheap to fix

The 2k raw SFDC pull was only:
`Id, Name, Website, BillingState, Owner.Name, Account_Segment__c, Final_Monthly_Closing_Volume__c, Monthly_Closing_Volume_Source__c, Legacy_Prospect_Value_Tier__c, Website_Hygiene_Status__c`.

With **no `ParentId`/`Parent.Name`**, every hierarchy case was undetectable; with **no `Account.Type`/`Company_Type__c`**, every customer/entity-class case was undetectable; with **no domain/dup key**, every wrong-website and duplicate case was undetectable. The model was asked to score entities it had no way to disqualify.

**Crucially, those columns are already on local disk** (verified this session — see Appendix C), so restoring them is a join, not a Salesforce dependency:
- `artifacts/account_minimal_company_type_2026-06-15.csv` → `Type`, `Account_Status__c`, `Company_Type__c` for **2000/2000** of the cohort.
- `artifacts/2026-04-16_dedupe/certifid_accounts.csv` → `ParentId`, `Parent.Name`, `Enterprise_ID__c/Name`, `Active_Customer__c`, `Website_Domain__c`, `Email_Domain__c`, `Customer_Heirarchy_Level__c`, revenue/opp counts for **1937/2000**.

A fresh read-only export is therefore **not** a blocker for the overlay or the validation; it is only a Phase-5 production-freshness concern (the ~3% gap + currency).

### 2.5 Two cohort nuances that shape the design (verified locally)

1. **On this cohort, CRM-type filtering is nearly inert; website-binding is load-bearing.** All 2000 are `Type=Prospect` and `Company_Type__c ∈ {Title company, Law firm}`. So `Company_Type__c` will **not** catch SB.Titles (a "Title company" bound to a bank site) on the 2k — the failure is the *website binding*, not the CRM class. `Company_Type__c`'s suppression value (underwriters, banks, RE agents) shows up at **full universe**, and on the 2k it serves mainly as a *cross-check* (website-derived class disagreeing with CRM class → review). The operative gates on the 2k are **Layer 1 (website↔account match)** and **Layer 3 via domain-join** (dedupe + customer domains).
2. **`ParentId` is near-empty on greenfield** (set on **1 of 2000**), and **0 rows are `Type=Customer`.** So hierarchy resolution on the 2k rides on **shared-domain rollup (Rule B)** and **domain-join to the customer set**, not SFDC `ParentId` (Rule A) or `Type=Customer` (Rule E-flag). Those structural rules matter, but they only get real coverage at full universe (Phase 5). Concretely on the 2k: **≈43 score_now rows sit on an existing customer's domain**, and **~25 shared-domain clusters cover ~60 score_now rows** (e.g., `westerntitle.com` ×6, `titlecompanyofflorida.com` ×3, `abstracttitle.com` ×3) — the dup/TAM-inflation pattern.

### 2.6 Cohort-level risk scan (head of the list)

The audit's top-40 score_now scan found **~40% carry an entity-quality smell** (bank, underwriter brand, `.gov`/`.org`/county/association, brokerage/exchange, abstract-only, or name↔domain mismatch). Cohort-wide soft flags from the scan: underwriter-brand domains ~17, bank domains ~5, `.gov`/`.org`/county/association ~57, name-or-site containing "abstract" ~132 (needs triage — many are legitimate full-service "Title & Abstract" cos). A conservative, de-duplicated hard-suspect estimate is **~6–10% of the 1295 score_now**, rising to **~15–20%** once softer name↔domain and abstract cases are triaged. The failures **concentrate at the high-value head** of the list, which is why the $150K ceiling masking them is so damaging.

---

## 3. The decision: gate before scorer (4 layers)

Restructure the per-account flow so the value math runs **only after** an account is proven to be a real, sellable, correctly-bound ICP. This is Will's framing, made concrete:

```
INPUT (restore dropped columns via local join)
   │
   ├─ Layer 1  WEBSITE ↔ ACCOUNT MATCH        Does the site belong to THIS account?
   │            (resolver host/name match + hygiene StateConflict/HostTokenHits)
   │            fail → website_mismatch / suspected_wrong_website  → do NOT score
   │
   ├─ Layer 2  ICP CLASSIFICATION             Is it a sellable title/escrow/settlement/closing-law entity?
   │            (Company_Type__c × website-derived class × registries × non-ICP suppressors)
   │            fail → non_icp_* (underwriter/bank/county/brokerage/abstract/general-law) → do NOT score
   │
   ├─ Layer 3  PARENT / CHILD / SELLABLE       Is THIS the sellable account, or a child/brand/dup/customer?
   │            (ParentId/Enterprise + shared-domain rollup + dedupe clusters + customer-domain join)
   │            fail → parent_child_rollup / owned_direct_operation / duplicate_or_existing_customer → roll up or suppress
   │
   └─ Layer 4  VALUE SCORING                  Only scoreable_icp reaches estimate_mcv → ARR ladder.
                (existing MCV/ARR logic, unchanged — but OfficeCount trusted only after Layers 1–3 pass and retrieval is non-thin)
```

Two non-obvious requirements the audit surfaced:
- **OfficeCount must be subordinate to the gate.** The phantom-office overestimates (Global 143, Clear Title 24, wlta.org 85, nextierbank 64) all come from reading "offices" off the wrong-entity or a thin site. OfficeCount should only drive MCV once Layer 1 passes and retrieval is non-thin.
- **The trusted-MCV / customer-anchor fast-track must move behind the gate.** Today the scorer's `customer_anchor`/`trusted_anchor` branches can return `score_now` / High confidence before any ICP or website check (a mistyped website on a "customer" row still scores). In the new order, the customer/dup gate (Layer 3) and the website/ICP gates (Layers 1–2) run first.

---

## 4. Disposition taxonomy (controlled vocabulary)

Every account receives **exactly one** disposition — the highest-precedence one that fires. Value scoring runs only for `scoreable_icp` and, in walled-off "provisional" form, for the explicit *score-but-suppress* review classes (§4.3).

### 4.1 The 15 dispositions

| Disposition | One-line definition | Primary detection signals | Score? | Suppress from sales? | Ops review? | Hierarchy remediation? |
|---|---|---|---|---|---|---|
| `scoreable_icp` | Title/escrow/settlement (or RE-closing law firm) whose site provably belongs to it and class is ICP | strong host/name match + ICP class + no deny hit; registry corroboration when available | **Yes** | No | No | No |
| `website_mismatch` | Site demonstrably belongs to a *different* entity | host-token overlap ≈0 + name absent from page + site self-IDs as something else; hygiene `StateConflict` | No | Yes | Yes (fix Website) | No |
| `suspected_wrong_website` | Probably wrong site, evidence ambiguous (rebrand/parked/partial) | medium-low match + hygiene flags + thin retrieval + a different `Proposed_Website__c` | Provisional only | Yes | Yes | No |
| `non_icp_underwriter` | Is/owned-by a title underwriter (our counterpart, not a buyer) | `Company_Type__c=Underwriter`; underwriter-brand domain (ctic, chicagotitle, fntg/fidelity, firstam, stewart, oldrepublic, wfg, doma, ata, ltgc) | No | Yes | Conditional | Yes (if branch) |
| `non_icp_bank` | Bank / credit union / mortgage lender | CT bank/lender; `*bank*` domain; FDIC/NMLS language; "offices"=branches | No | Yes | No | No |
| `non_icp_county_government` | Bound site is a county/municipal/state gov property | `.gov` / municipal `*county*.org`; clerk/recorder/register-of-deeds/DMV tokens | No | Yes | Conditional (real co, wrong site) | No |
| `non_icp_real_estate_brokerage` | RE brokerage / agent / builder-residential | `Company_Type__c=Real Estate Agent`; brokerage brand domain; realtor/MLS text dominates, no title service | No (amb→suppress) | Yes | Conditional | No |
| `abstract_only_no_closing` | Abstract / title-search only; no escrow/settlement | "abstract" present **and** no closing/escrow/settlement service signal | No | Yes | Conditional | No |
| `generic_law_firm_non_icp` | Law firm, non-RE-closing practice | legal classifier `non_icp=True`; non-RE practice areas, no closing signal | No | Yes | No | No |
| `legal_real_estate_review` | RE-closing law firm in attorney state — plausibly ICP, needs lane check | legal `is_law_firm` + closing/RE route + favorable market fit | Provisional only | Yes | Conditional (legal reviewer) | No |
| `parent_child_rollup_review` | Child/branch whose TAM rolls up to a sellable parent | `ParentId`/`internal_parent_chain`; dedupe `has_parent_links` + survivor≠self; shared brand domain across siblings | Child suppressed; score parent | Yes (child) | Yes | **Yes** |
| `duplicate_or_existing_customer_review` | Duplicate record, or already a customer (not net-new) | dedupe active-customer/dup `loser_id`; `Type=Customer`; domain ∈ customer-domain set; site self-IDs as a known customer | No | Yes | Yes | Conditional |
| `owned_direct_operation_review` | Underwriter/national "direct" office that does closings — sellable only via the parent | underwriter-brand domain **plus** real closing service + location data; parent link to underwriter enterprise | Provisional only | Yes (→ enterprise motion) | Yes | **Yes** |
| `insufficient_entity_evidence` | Retrieval failed/too thin to disposition | map_failed/not_run/no markdowns; no CT, no registry, no usable text | No | Yes | Conditional | No |
| `manual_ops_review` | Conflicting strong signals (e.g., CT-ICP vs deny hit; class disagreement) | contradiction between two strong sources; anything that doesn't cleanly fit 1–14 | Provisional only | Yes | **Yes** | Conditional |

### 4.2 Precedence (first match wins)

Resolve structural CRM facts that invalidate the whole TAM first, then identity/binding, then entity class, then evidence sufficiency:

1. `duplicate_or_existing_customer_review` — already a customer/dup ⇒ never net-new (fixes the customer-anchor fast-track).
2. `parent_child_rollup_review` — a child is never scored independently even if its own site is clean.
3. `non_icp_underwriter` — underwriter/owned-brand binding outranks "looks like a title site" (the brand domain *is* a title site: Greco/Absolute → atatitle.com).
4. `owned_direct_operation_review` — only after underwriter-affiliation do we ask "is the direct side sellable?"
5. `website_mismatch` — **wrong-website beats ICP classification**: if the site isn't the account's, everything derived from it is meaningless (the single most important ordering rule).
6–9. entity-class non-ICP: `non_icp_bank` → `non_icp_county_government` → `non_icp_real_estate_brokerage` → `abstract_only_no_closing` (crispest domain signals first).
10. `generic_law_firm_non_icp`.
11. `suspected_wrong_website` (soft binding doubt, after hard mismatch ruled out).
12. `legal_real_estate_review`.
13. `manual_ops_review`.
14. `insufficient_entity_evidence`.
15. `scoreable_icp` — the residue that passed every gate; the only disposition that reaches `estimate_mcv`.

Tie-breaks: a **strong** signal outranks a **weak** higher-precedence one; **High-confidence** non-ICP/non-binding auto-applies, **Medium** routes to its review variant; where website-derived class disagrees with `Company_Type__c`, force `manual_ops_review`.

### 4.3 "Score-but-suppress" output contract

Five review classes (`suspected_wrong_website`, `legal_real_estate_review`, `owned_direct_operation_review`, `manual_ops_review`, and the ambiguous-Medium `non_icp_real_estate_brokerage`) compute a **provisional** estimate for reviewer context only. To avoid the contradiction of "compute but blank it out": these write `ProvisionalMCV` / `ProvisionalARR` columns that are **physically separate from** the sales-facing `GatedMCV` / `GatedARR` (which stay null). Only `scoreable_icp` populates the sales-facing value fields. Sales list views filter to `SuppressFromSalesReview = FALSE`.

### 4.4 Will's 19 → disposition the gate should assign

5 → `scoreable_icp` (his 5 Greens: Tennessee, Metro, Apex, Supreme, + corrected-binding); 2 → `scoreable_icp (Low) + value-flag` (Global, Clear Title — they *are* ICP; the fault is OfficeCount, handled by gating OfficeCount behind binding + Low confidence, not by hard-suppressing); the other **12 caught upstream**: `website_mismatch` (Title Companies In, SB.Titles, Title Guaranty-IA, Ionia County→county_gov), `non_icp_underwriter` (Northern NY), `non_icp_bank` (Master Settlement, via wrong-website root), `non_icp_real_estate_brokerage`→`manual_ops_review` (Greenridge), `abstract_only_no_closing` (Real Estate Title Service), `parent_child_rollup_review` (Denver Land Title, Texas Capital Title), `owned_direct_operation_review` (Greco, Absolute), `duplicate_or_existing_customer_review` (Meridian). Every one of Will's 10 Reds is suppressed or routed; all 4 Oranges land in hierarchy/owned-direct review classes that carry parent/child remediation.

---

## 5. Parent/child & sellable-account resolution

**Principle: TAM = Σ ARR over distinct SELLABLE PARENTS, never over children/brands/offices/customers.**

### 5.1 Resolution rules (first match wins; each emits `SuggestedSellableAccountId/Name`, `RollupReason`, `RollupConfidence`)

| Rule | Logic | Confidence | Automatable? | 2k coverage |
|---|---|---|---|---|
| A — Exact ParentId rollup | `ParentId` non-null + parent is Corporate ⇒ sellable = parent | High | Auto | **near-inert on 2k (1/2000)**; real at full universe |
| B — Owned-brand / same-domain rollup | no ParentId but shares registrable domain with a Corporate sibling + class aligns ⇒ roll to survivor | High (exact domain+name token); Med (subdomain only) | Auto (exact) / Review (subdomain) | **operative on 2k** (~25 clusters / ~60 rows) |
| C — Underwriter-owned direct operation | domain resolves to underwriter root + `Company_Type__c=Title company` → split: (c1) corporate underwriter ⇒ non-sellable; (c2) producing direct agency ⇒ roll to direct parent, score once | Med (needs c1/c2 split) | **Review (Will/Amanda)** | Greco, Absolute (atatitle.com), Northern NY (ctic.com) |
| D — Duplicate domain/name | dedupe high-confidence survivor/loser (name sim ≥0.92, single domain/state/phone) ⇒ keep survivor, suppress loser | High | Auto (high-conf tier) / Review (medium) | dedupe `high_confidence` 49 clusters |
| E — Existing-customer suppression | `Type=Customer` OR `Active_Customer__c` OR domain ∈ customer-domain set OR `Active_Subscription_Revenue__c>0` ⇒ suppress from prospect TAM | High (flag match); Med (domain-only) | Auto (flag) / Review (domain-only) | **flag-match 0 on 2k; domain-only ≈43 score_now rows** |

### 5.2 Automatable vs review

| Lane | Rules | Risk | Owner |
|---|---|---|---|
| Auto-apply | A, D-high, E-flag | Low (SFDC structural truth) | Pipeline |
| Auto-suggest, human-confirm | B, D-medium | Medium (false-merge of distinct same-domain offices) | Ops / Amanda (bulk-confirm via dedupe survivor queue) |
| Mandatory review | C (underwriter direct-vs-non-sellable), E-domain-only (the ≈43 rows) | High (mis-states TAM either way) | Will / Amanda |

### 5.3 Will's hierarchy cases (resolved by domain against `certifid_accounts.csv`)

Denver Land Title → **Land Title Guarantee** (`ltgc.com`, B); Texas Capital Title → **Capital Title of Texas** (`ctot.com`, B→A; CTOT itself parents to Shaddock National); Greco & Absolute → **ATA National** (`atatitle.com`, C2 + customer-suppress); Northern NY Title → **Chicago Title** (`*.ctic.com`, C1, non-sellable); Meridian → **Meridian Title Corp.** (customer, E). Verified cluster sizes: `atatitle.com`=3 (1 Customer + 2 Prospect), `meridiantitle.com`=5, `ctic.com`=11, `ltgc.com`=2, `ctot.com`=2.

### 5.4 TAM reconciliation block (so inflation is visible at a glance)

Every gated rerun should publish a two-grain output and this headline:

```
Input accounts scored (raw)            : 2,000
  – rolled into a parent (A/B/C)       :  N_parent
  – suppressed as duplicate (D)        :  N_dup
  – suppressed as existing customer    :  N_cust    (≈43 domain-flagged on score_now + any Type/Active)
  – non-sellable underwriter/bank      :  N_nonsell
= Distinct SELLABLE parents            :  N_sellable   ← TAM denominator
Σ ARR over sellable heads only         :  $X   (vs naive Σ over all rows = $Y; Δ = inflation removed)
```

The value model runs **once per sellable head**; children fold in as office-count evidence for the head, never as separate ARR.

---

## 6. Reusable assets — the gate is ~70% assembly, not new build

| Capability / asset | Where | Powers gate layer | Readiness |
|---|---|---|---|
| Website↔account entity match (`host_token_score`, `exact_name_phrase_match`, `entity_match_level`, `candidate_website`) | `scripts/run_vertical_resolver_poc.py` | Layer 1 (primary engine) | High — 92% precision on VA POC; not yet wired into scorer |
| Hygiene wrong-website screen (`StateConflict`, `HostTokenHits`, `Proposed_Website__c`) | `artifacts/website_hygiene/…final_decisions_20260508.csv` | Layer 1 | High |
| `Company_Type__c` + Type | `artifacts/account_minimal_company_type_2026-06-15.csv` (2000/2000) | Layer 2 (cross-check on 2k; suppressor at full universe) | High — **was simply omitted from the 2k pull** |
| ALTA membership (positive ICP, national) | `artifacts/alta_enrichment/sfdc_alta_enrichment.csv` | Layer 2 | High — join by Account_Id; proven ARR lift |
| VA SCC live registry / CA DFPI list | `scripts/run_va_scc_registry_check.py`; `artifacts/.../dfpi_*` | Layer 2 (authoritative, state-scoped) | High (VA code) / Med-High (CA static) |
| Legal lane classifier | `scripts/legal_entity_scoring.py` | Layer 2 / 4 (already wired) | High |
| Dedupe clusters (`parent_id`, `enterprise_id`, `active_customer`, `suggested_survivor_id`, domain/phone block) | `artifacts/2026-04-16_dedupe/` | Layer 3 (both parent-child & dup-customer) | High — survivor = `SuggestedSellableAccountId`; re-run blocker on full universe to extend beyond ~1,370 clustered |
| Customer-domain suppression set | `artifacts/prospect_value_research/customer_mcv_websites.csv` (1,663 domains) | Layer 3 | High |
| Existing MCV/ARR ladder + anchors | scorer `estimate_mcv` / `mcv_to_band` / anchor fns | Layer 4 (keep; move behind gate) | High |

**The one genuine new build** is a consolidated non-ICP suppressor (`config/icp_gate/non_icp_suppressors.json`): underwriter parent brands + domains (Chicago Title, Fidelity/FNF, First American, Stewart, Old Republic, WFG, Doma, ATA National, Land Title Guarantee, North American, Westcor, Title Resources), bank/lender (mostly derivable from `Company_Type__c`), brokerage brands, `.gov`/county patterns, and the abstract-only inversion (abstract present AND no escrow/closing/settlement signal). Today this exists only as a 6-pattern stub plus a 4-entry bad-domain list.

---

## 7. Metrics & golden set (how we know the gate is good)

This is the part that turns "looks right" into "Will signs off."

### 7.1 Golden labeled sets
- **Tier 0 — Will's 19** (5 G / 4 O / 10 R): the smoke test. Useful but far too small to support any precision claim.
- **Tier 1 — ~200 labeled accounts**, to be built in Phase 0 with a written labeling protocol and minimum per-class counts so each gate is actually exercised: **≥15 wrong_website, ≥15 underwriter/owned-direct, ≥15 dup/existing-customer, ≥15 non-ICP (bank/county/brokerage/abstract), ≥15 clean scoreable_icp, plus legal-lane cases.** Drawn from the top-200 score_now ranks (where risk concentrates) + a random tail sample. Two labelers (Will + Amanda or Ops) with a disagreement-resolution pass; record inter-rater agreement.

### 7.2 Metrics to compute (on the overlay, before any code change)
- **Gate precision** per disposition (of the accounts the gate suppressed/rerouted, how many were truly bad) — target ≥~90% on the crisp classes (wrong_website, underwriter, bank, county, dup/customer), looser on fuzzy classes (brokerage, abstract).
- **False-suppression / "kill-a-Green" recall** — *the highest-risk metric*: of Will's Greens (and Tier-1 clean ICPs), how many would the gate wrongly suppress? **Target 0 Greens suppressed.** This must be a **hard Phase-1 exit artifact**, computed on Will's 19 first, then Tier-1 — not a Phase-4 aspiration.
- **Head-of-list lift**: entity-quality-suspect share of top-50 score_now before vs after the gate.
- **TAM de-duplication**: naive Σ ARR vs sellable-head Σ ARR (the §5.4 block).

---

## 8. No-write QC overlay — output schema

A single CSV joining the 2k review file to the local CRM-structure files and the disposition logic. **No SFDC writes; no scorer change.** Real CSV parser required (the review file has quoted fields with embedded commas — naive split corrupts rows).

| Field | Type | Allowed values / format | Derivation | Source (derivable now?) |
|---|---|---|---|---|
| `AccountId` | id | 18-char | passthrough | review/combined `Id` — **now** |
| `AccountName` | text | | passthrough | review `Name` — **now** |
| `Website` | url | | passthrough | review `Website` — **now** |
| `CurrentAction` | enum | score_now / … | passthrough (the 2k action) | review `Action` — **now** |
| `CurrentRank` | int | | passthrough | review `ReviewRank` — **now** |
| `CurrentMCV` | int | | passthrough | review `EstimatedMCV` — **now** |
| `CurrentARR` | currency | | passthrough | review `EstimatedARR` — **now** |
| `EntityQualityDisposition` | enum(15) | §4 vocabulary | gate logic | computed — **now** |
| `EntityQualityConfidence` | enum | High/Medium/Low | gate logic | computed — **now** |
| `WebsiteAccountMatch` | enum | strong/medium/weak/none | resolver host/name match + hygiene `StateConflict`/`HostTokenHits` | resolver + hygiene file — **now** |
| `ICPDisposition` | enum | scoreable / underwriter / bank / county_gov / brokerage / abstract_only / legal_* / unknown | Layer-2 class (website-derived × `Company_Type__c` × registry) | `Company_Type__c` (2000/2000) + crawled evidence + ALTA/SCC/DFPI — **now** |
| `ParentChildDisposition` | enum | score_as_self / roll_to_parent / suppress_duplicate / suppress_customer / route_review | Layer-3 rules A–E | dedupe + `certifid_accounts.csv` (1937/2000) + customer domains — **now (B/D/E-domain); A/E-flag near-inert on 2k** |
| `SuggestedSellableAccountId` | id | | rule A–C survivor/parent, or dedupe `suggested_survivor_id` | dedupe + `certifid_accounts.csv` — **now** |
| `SuggestedSellableAccountName` | text | | as above | dedupe + `certifid_accounts.csv` — **now** |
| `SuppressFromSalesReview` | bool | TRUE/FALSE | TRUE unless `scoreable_icp` | computed — **now** |
| `NeedsOpsReview` | bool | TRUE/FALSE | per §4 disposition flags | computed — **now** |
| `Evidence` | text | short, human-readable | which signals fired + key tokens/domains | computed — **now** |
| `RecommendedNextAction` | enum | keep_score / hide_from_sales / fix_website / roll_to_parent / confirm_customer / ops_review / enterprise_review | maps from disposition | computed — **now** |
| `ProvisionalMCV` / `ProvisionalARR` | int / currency | populated only for score-but-suppress classes; walled off from sales | §4.3 | computed — **now** |

**Every field is derivable from local artifacts today.** A fresh read-only SFDC export improves only coverage of the ~3% not in `certifid_accounts.csv` and production freshness — it is not required to build or validate the overlay.

---

## 9. Phased plan

Safety rule across all phases: **no SFDC writes, no `Account.Website` changes, no deploys, no scoring jobs** until a specific step is explicitly approved by Will/Amanda. The 2k is already live in prod hidden fields; Phases 0–1 add an *interpretation* layer over it, they do not touch it.

### Phase 0 — No-code artifact diagnosis (this week)
- **Objective:** quantify the problem and produce the per-account disposition for Will's 19 + the full score_now cohort, with zero new dependency.
- **Steps:** join `batch2k_salesforce_review` × `account_minimal_company_type` × `certifid_accounts` × `customer_mcv_websites` × dedupe clusters (real CSV parser); apply a first-cut of §4 logic; compute the §2.3/§2.5 splits; label Will's 19; **measure kill-a-Green on the 19**.
- **Output:** `phase0_2k_entity_quality_diagnosis_2026-06-2x.csv` + a 1-page readout.
- **Reviewer / exit:** Will confirms the 19 dispositions match his read; 0 Greens suppressed on the 19.

### Phase 1 — No-write QC overlay + interim mitigation (1–2 weeks)
- **Objective:** the §8 overlay over all 2000, plus the protective bridge for live prod data.
- **Steps:** harden the gate logic into a reusable, well-parsed module that emits the §8 schema; build **Tier-1 (~200) golden set** (§7.1) and compute gate precision + kill-a-Green (§7.2); produce the **interim suppression list** — the set of currently-live score_now rows the overlay flags as wrong-website/non-ICP/dup-customer — as a deliverable Will can act on manually (hide-from-view / do-not-trust) **without** waiting for a rerun.
- **Output:** overlay CSV; metrics readout; suppression/do-not-trust list; clean review views (filtered to `SuppressFromSalesReview=FALSE` and de-duplicated to sellable heads).
- **Reviewer / exit:** Will/Amanda accept overlay accuracy; kill-a-Green = 0 on Tier-1; suppression list delivered.

### Phase 2 — Pre-scoring gate integrated into the scorer
- **Objective:** move the validated gate into the scoring path so Layers 1–3 run before Layer 4.
- **Steps:** **first reconcile the two scorer copies** — `scripts/run_greenfield_nimble_test.py` and the cloud copy **are not identical** (they differ materially; do not assume identity); pick the authoritative source of truth and port with a tested diff. Restore the dropped input columns (`ParentId/Parent.Name/Type/Company_Type__c/Industry/domain`) to the input/raw builder. Insert the gate: a structural pre-check from inputs (parent/dup/customer/type) **and** an evidence check after `combined_text` is built (resolver website↔name match + entity class), gating `estimate_mcv` so the value ladder runs only on gate=pass. Subordinate OfficeCount and the customer/trusted-anchor branches to the gate. Add gate-provenance output columns. Reuse resolver / dedupe / legal-lane / registry modules.
- **Output:** updated scorer (single source of truth) + unit fixtures covering each disposition; no prod run yet.
- **Reviewer / exit:** gate reproduces the overlay dispositions on the 2k; fixtures green; Codex review of the diff.

### Phase 3 — Parent/child review queue + optional SFDC remediation *proposal*
- **Objective:** operationalize §5 — auto-apply A/D-high/E-flag, queue B/D-medium for Ops/Amanda, route C/E-domain-only to Will/Amanda. Re-run the dedupe blocker over the full scoring universe (not just the ~1,370 already clustered).
- **Output:** a sellable-account map + a *proposed* (not executed) SFDC parent/merge remediation file for human approval.
- **Reviewer / exit:** Ops/Amanda sign-off on auto-merge tier; Will on the underwriter-direct calls. **No SFDC writes without explicit approval.**

### Phase 4 — Gated rerun of the 2k with suppression + clean reports
- **Objective:** rerun the 2k through the gated scorer; only `scoreable_icp` carries sales-facing MCV/ARR; review classes carry provisional/suppressed values; report on sellable-head grain.
- **Steps:** rerun (cached rescore where extraction unchanged); apply the §5.4 TAM reconciliation; build clean report views. If a prod correction/overwrite of the live 2k fields is approved, follow the safe-writeback protocol (handoff §"Safe Writeback Protocol"); **the rollback artifact is the existing `batch2k_sfdc_prewrite_backup_2026-06-19.csv`.**
- **Reviewer / exit:** head-of-list smell rate down; TAM Δ quantified; Will confidence restored on top ranks.

### Phase 5 — Full-universe production readiness
- **Objective:** extend from the 2k validation cohort to the prospect universe.
- **Steps:** a fresh read-only SFDC export for freshness + the ~3% coverage gap + full-universe `ParentId`/`Type` (where Rules A and E-flag finally get real coverage); state-registry expansion beyond VA/CA; scheduled job + audit/rollback discipline; geo-density and customer-history features (separate, already-tracked workstreams).
- **Reviewer / exit:** calibration gates cleared; Amanda/RevOps approve broad refresh.

---

## 10. Recommendation

**Primary path: (c) run a no-write overlay as an interim gate first → then (b) build it into a separate, validated pre-scoring module → then (a) it lands in the scorer.** Sequence: **c → b → a.**

Rationale:
- **Zero risk on a live surface.** The 2k is already in prod hidden fields with ~6–10% hard-suspect rows at the top of the list. The overlay changes nothing in Salesforce, yet it produces the do-not-trust list that protects sales *today* (Phase 1 interim mitigation) — the thing Will will care about most.
- **The overlay is the validation artifact.** It's how Will/Amanda sign off on the gate's precision and (critically) its kill-a-Green recall **before** a single line of scorer logic changes. Patching the scorer first (a) would bury that validation inside a code change on a path that itself needs reconciliation between two divergent copies.
- **The data is already local**, so "overlay first" costs days, not weeks, and carries no Salesforce dependency.
- **Steelman of the alternative** ("skip the overlay, build the module (b) directly, since the data is local anyway"): tempting, but rejected — the overlay *is* the cheapest way to earn Will's sign-off and *is* the interim suppression deliverable; doing (b) first means changing code before the dispositions are human-validated and before the scorer-drift is reconciled. The overlay survives even after we learn the data is local, because its value is **validation + interim mitigation**, not data access.

What we are **not** recommending: patching the existing scorer first (a-first). It's the highest-risk, lowest-feedback option on an already-live, internally-divergent code path.

---

## 11. Open questions

**For Will**
- Confirm the 19-account dispositions in §2.2/§4.4 match your intent (especially the 2 "scoreable-but-flagged" value cases, Global & Clear Title — score with a flag, or suppress?).
- Underwriter-direct calls (Rule C): for Greco / Absolute (ATA National) and similar, is the "direct side" ever sellable to us, or always treated as non-sellable/enterprise?
- Interim mitigation: do you want the Phase-1 do-not-trust list as a manual hide-from-view in the report, or just a reference list?

**For Amanda / Ops**
- Who owns the bulk-confirm of the auto-suggest hierarchy tier (Rule B same-domain), and the dup-merge survivor queue?
- Is `Account.Website` correction (for `website_mismatch` / `suspected_wrong_website`) an Ops data task we can queue, separate from scoring?

**For Cam**
- Legal-lane edge: for `legal_real_estate_review`, the BigLaw-leakage-in-attorney-states blocker is still open — confirm the state/county overrides (Cook County, N/S NJ, Nevada) before legal rows score.

**For Ali / data**
- Eventual fresh read-only Account export for Phase 5 (full-universe `ParentId`, `Type`, `Company_Type__c`, domain keys, customer status) — timing and access. (Not needed for Phases 0–2.)

**For Ops / RevOps**
- What is the canonical "existing customer" definition for suppression — `Type=Customer`, `Active_Customer__c`, `Active_Subscription_Revenue__c>0`, or a union? This decides the ≈43 domain-flagged rows.

---

## Appendix A — Key file paths

- 2k run: `tmp/certifid_scoring_gcp/batch2k-20260619-1645/` (`batch2k_salesforce_review_2026-06-19.csv`, `batch2k_scores_combined_2026-06-19.csv`, `batch2k_sfdc_writeback_summary_2026-06-19.json`, `batch2k_sfdc_prewrite_backup_2026-06-19.csv` ← rollback artifact); inputs in parent dir (`account_scoring_2k_input_2026-06-19.csv`, `account_scoring_2k_raw_2026-06-19.csv`).
- CRM structure (local): `artifacts/account_minimal_company_type_2026-06-15.csv` (Type/Company_Type__c, 2000/2000); `artifacts/2026-04-16_dedupe/certifid_accounts.csv` (ParentId/domain/customer, 1937/2000); `artifacts/2026-04-16_dedupe/dedupe_cluster_summary_v2.csv`, `dedupe_high_confidence_queue.csv`, `dedupe_custom_block_candidates.csv`.
- Reuse modules: `scripts/run_vertical_resolver_poc.py`, `scripts/run_va_scc_registry_check.py`, `scripts/legal_entity_scoring.py`; `artifacts/alta_enrichment/sfdc_alta_enrichment.csv`; `artifacts/prospect_value_research/customer_mcv_websites.csv`; `artifacts/website_hygiene/…final_decisions_20260508.csv`.
- Scorer: `cloud_run_jobs/certifid_account_scoring/certifid_account_scoring/scoring/run_greenfield_nimble_test.py` and `scripts/run_greenfield_nimble_test.py` (**not identical — reconcile in Phase 2**).

## Appendix B — Scorer-drift note

`scripts/run_greenfield_nimble_test.py` and the cloud copy **differ** (104 Compare-Object line groups; different lengths). Earlier analysis citing them as byte-identical and citing specific line numbers is unreliable — refer to functions by name (`process_row`, `customer_mcv_anchor`, `estimate_mcv`, `is_non_icp`, `choose_pages`/`same_registered_domain`) and reconcile the two before inserting the gate. `legal_entity_scoring.py` appears consistent across both.

## Appendix C — Verified numbers (this session)

| Claim | Verified |
|---|---|
| Action split / confidence | score_now 1295, IPE 293, SFN 281, non_icp 118, URL 13; High 478 / Med 945 / Low 577 — correct |
| InputFinalMCV=0 on all score_now | correct (0/1295 nonzero) |
| `Company_Type__c`+`Type` local coverage of 2k | **2000/2000** (all Type=Prospect; Title 1401 / Law 599) |
| `certifid_accounts.csv` coverage / ParentId set | **1937/2000**; ParentId set on **1** |
| score_now rows on an existing-customer domain | **≈43** |
| score_now shared-domain clusters | **~25 real clusters / ~60 rows** (westerntitle.com ×6, titlecompanyofflorida.com ×3, abstracttitle.com ×3) |
| Northern NY Title OfficeCount | **22** (the 85 belongs to Title Companies In) |
| Greco vs Absolute | same `atatitle.com`, both 44 offices, **distinct rows** (rank 9/IL vs 15/TN); a 2nd Absolute Title exists (SC, absolutetitlesc.net, MCV 25, rank 1293) |
| Two scorer copies | **differ** (104 diff groups) |

---

*Prepared via a multi-agent audit (artifact / code / reuse / hierarchy auditors → taxonomy designer → planner → adversarial critic), with all load-bearing numbers re-verified against local artifacts. No Salesforce writes, deploys, or scoring jobs were performed.*
