# CertifID SFDC Account Value Scoring Build Plan

Status: Draft v1.0 - Prod California DFPI scoring populated
Created: 2026-05-05
Updated: 2026-06-16
Production org alias: `CertifID`
Sandbox org alias: `CertifID-Sandbox`
Primary goal: Productize and improve Will's Dust `Pipeline_Value_Agent` concept into a calibrated, auditable, Salesforce-native Account scoring pipeline.

## Executive Summary

The first build should be a calibrated shadow pipeline, not a blind Salesforce writeback. Will's current Dust model has the right conceptual core: use public website evidence as a proxy for Monthly Closing Volume, score observable title/escrow/law-firm capacity signals, and convert the estimated MCV into forward pipeline-potential ARR. The production effort should keep that rubric, then make it reliable, calibrated, structured, and Salesforce-native.

The key change from the current Dust approach is ownership of runtime responsibility. Dust/Claude-style reasoning should extract or adjudicate structured evidence. Deterministic code should own URL preflight, hygiene routing, score normalization, MCV/ARR mapping, ranking, backtesting, Salesforce writeback, run logging, and rollback.

The MVP should therefore start with a no-write calibration/backtest harness against Will's curated 76 per-file Closed Won customers plus larger Salesforce calibration sets. Once the model beats or clearly improves on the current Dust output, the Salesforce version creates Account fields for predicted MCV, potential ARR, rank, fit score, timing score, confidence, ICP/disposition, review action, score components, evidence, URL status, source, model version, and run date. AEs and managers consume the results through Account fields and saved Account list views instead of a Google Sheet.

The production version adds a scheduled scoring job, full-prospect refresh, calibration/backtesting against Closed Won per-file booked ARR sanity checks, similar closed-account potential bands, and closed New Business MCV labels, audit history, and optional one-off refresh from the Account page.

## Current Facts

These are based on existing local artifacts and Salesforce checks on 2026-05-05, with 2026-05-12 sandbox/backtest refreshes, 2026-05-14 production metadata/shadow-writeback verification, 2026-05-20 to 2026-05-22 production scoring refinements, the 2026-05-28 to 2026-05-29 greenfield scale-250 test, the 2026-06-09 Virginia ID outreach run, the 2026-06-10 Virginia resolver accepted-only refresh, and the 2026-06-11 California DFPI scoring run where noted.

### Salesforce Org

- CertifID production org alias is connected locally as `CertifID`.
- CertifID sandbox org alias is connected locally as `CertifID-Sandbox`.
- Local Salesforce CLI version is `@salesforce/cli/2.85.7`.
- The CLI reports an update is available; command syntax in this plan was validated against local `sf --help`.
- Current workspace root is not an SFDX project; the focused scoring SFDX project now lives under `sfdc/certifid-prospect-value-sfdc`.
- A focused website hygiene deployment project exists under `tmp/sfdc_website_hygiene_deploy`.
- As of 2026-05-12, the `Website_Hygiene_*` and `Proposed_Website_*` Account fields exist in Salesforce.
- As of 2026-05-12, 21 Account scoring fields and the `Prospect_Value_Scoring_Admin` permission set are deployed in `CertifID-Sandbox`. No score values have been written.
- As of 2026-05-14, 21 Account scoring fields, 5 legacy Dust snapshot fields, and the `Prospect_Value_Scoring_Admin` permission set are deployed in production. The fields are not on Account layouts.
- As of 2026-05-14, a bounded production shadow comparison has been populated for the 183 Accounts with parseable legacy Dust tier notes. This is not a full-prospect production refresh.

### Implementation Progress

- Will's Dust `Pipeline_Value_Agent` rubric is versioned locally at `config/prospect_value_scoring/will_dust_rubric_v1.json`.
- Read-only production calibration exports were generated under `artifacts/prospect_value_research/calibration_2026-05-12`.
- The account-level calibration dataset contains 20,696 Account-level rows, 4,283 MCV-labeled rows, 1,029 ARR-labeled rows, and 1,018 rows with both labels.
- Sandbox metadata deploy succeeded for the field model and admin permission set. The deploy id was `0AfTH00000Geopi0AB`.
- The `Prospect_Value_Scoring_Admin` permission set is assigned to `mike.heilmann@certifid.com.sandbox` for validation.
- A no-write backtest harness now exists at `scripts/backtest_account_value_predictions.py`.
- A sandbox writeback CSV builder now exists at `scripts/build_account_value_sandbox_writeback.py`.
- A 5-record sandbox-only canary writeback completed successfully on 2026-05-12. Bulk job id: `750TH00000Tq7BCYAZ`; run id: `sandbox_shadow_canary_20260512`; result: 5 successful, 0 failed.
- A 294-record sandbox-only V2 shadow smoke writeback completed successfully on 2026-05-12. Bulk job id: `750TH00000TpsXEYAZ`; run id: `sandbox_shadow_full304_20260512`; result: 294 successful, 0 failed. This overwrote the earlier 5 canary rows with the full smoke run id.
- Canary backup and writeback artifacts live under `artifacts/prospect_value_research/sandbox_writeback_2026-05-12`.
- Full sandbox smoke backup and writeback artifacts also live under `artifacts/prospect_value_research/sandbox_writeback_2026-05-12`.
- Production hidden metadata dry-run succeeded on 2026-05-14 using `sfdc/certifid-prospect-value-sfdc/manifest/package.xml`. Dry-run deploy id: `0AfTP0000041Co10AE`.
- Production hidden metadata deploy succeeded on 2026-05-14. Deploy id: `0AfTP0000041Cpd0AE`. The deployed package included 21 Account fields and the `Prospect_Value_Scoring_Admin` permission set only; no layouts, list views, Apex, or score data were deployed.
- A separate production `RunLocalTests` validation surfaced unrelated existing org test failures with 0 component failures for this package. The declarative-only manifest validation and deploy succeeded with Salesforce skipping tests.
- Production verification found 21 `AI_Prospect...` Account fields and 1 `Prospect_Value_Scoring_Admin` permission set.
- The `Prospect_Value_Scoring_Admin` permission set is assigned in production to Mike Heilmann, Nate Ayers, Reena Jain, Will Looney, and Will Marty for validation/report-building access.
- Production Account layout verification retrieved all 4 Account layouts and found no `AI_Prospect` or `Legacy_Prospect` field references after the shadow comparison writeback.
- Legacy Dust snapshot metadata dry-run succeeded on 2026-05-14. Dry-run deploy id: `0AfTP0000041DH30AM`.
- Legacy Dust snapshot metadata deployed to production on 2026-05-14. Deploy id: `0AfTP0000041DIf0AM`. The deployed package added `Legacy_Prospect_Value_Tier__c`, `Legacy_Prospect_Value_Score__c`, `Legacy_Prospect_Value_Date__c`, `Legacy_Prospect_Value_Source__c`, and `Legacy_Prospect_Value_Notes__c`, plus admin permission-set access.
- Legacy Dust baseline backfill completed on 2026-05-14. Bulk job id: `750TP00000jYTYsYAO`; result: 183 successful, 0 failed. Tier counts: A+ 39, A 31, A- 2, B+ 1, B 90, C 20.
- Legacy Dust shadow comparison scoring artifacts live under `artifacts/prospect_value_research/prod_shadow_compare_2026-05-14`.
- Production AI shadow writeback for the 183 legacy-Dust cohort completed on 2026-05-14. Bulk job id: `750TP00000jYqK4YAK`; run id: `prod_shadow_legacy_dust_20260514`; result: 183 successful, 0 failed.
- Post-write verification found 183 Accounts with both `Legacy_Prospect_Value_Tier__c` and `AI_Prospect_Value_Run_Id__c = prod_shadow_legacy_dust_20260514`.
- The production shadow action split is: `score_now` 139, `non_icp_confirmed` 10, `url_enrichment_needed` 11, `search_fallback_needed` 5, `manual_parent_child_review` 5, `enterprise_sales_review` 1, `insufficient_public_evidence` 12.
- Production side-by-side report deployed on 2026-05-14. Report: `Public Reports / AI Value Shadow Compare`; report id: `00OTP00000DPwnh2AD`; metadata full name: `unfiled$public/AI_Value_Shadow_Compare`; dry-run deploy id: `0AfTP0000041E8H0AU`; deploy id: `0AfTP0000041E9t0AE`. The report exposes legacy Dust fields next to AI shadow scoring fields for the validation cohort.
- Production V2.1 MCV-anchor quick turn completed on 2026-05-20 after Will/Mark review feedback. Script: `scripts/build_account_value_v21_mcv_anchor.py`; run id: `prod_shadow_legacy_dust_v21_20260520`; source: `ProdShadowLegacyDustCohortV21_2026-05-20`; model version: `will_dust_rubric_v2_1_mcv_anchor`.
- V2.1 production shadow writeback completed on 2026-05-20. Bulk job id: `750TP00000jswUwYAI`; result: 183 successful, 0 failed. Preload backup: `artifacts/prospect_value_research/prod_shadow_compare_2026-05-14/v21_ai_shadow_preload_backup_2026-05-20.csv`.
- V2.1 verification found 183 Accounts under run id `prod_shadow_legacy_dust_v21_20260520` and 0 Accounts remaining under the prior `prod_shadow_legacy_dust_20260514` run id. V2.1 action split: `score_now` 147, `non_icp_confirmed` 10, `url_enrichment_needed` 9, `insufficient_public_evidence` 6, `manual_parent_child_review` 5, `search_fallback_needed` 5, `enterprise_sales_review` 1.
- The `AI Value Shadow Compare` report filter was updated to the V2.1 run id on 2026-05-20. Deploy id: `0AfTP0000042Mer0AE`.
- Production V2.2 source-hierarchy quick turn completed on 2026-05-21 after Will's review feedback. Script: `scripts/build_account_value_v21_mcv_anchor.py`; run id: `prod_shadow_legacy_dust_v22_20260521`; source: `ProdShadowLegacyDustCohortV22_2026-05-21`; model version: `will_dust_rubric_v2_2_mcv_source_hierarchy`.
- V2.2 corrected the stale-low-Opportunity failure mode by treating high-trust `Sales Rep` Account MCV as a floor/override against older low closed-lost Opportunity MCV, while keeping `Marketing` Account MCV as a lower-confidence fallback.
- V2.2 production shadow writeback completed on 2026-05-21. The first bulk job (`750TP00000jw4arYAA`) was rejected before row processing because the CSV used LF line endings while the command defaulted to CRLF. The successful rerun used `--line-ending LF`; bulk job id: `750TP00000jw9ieYAA`; result: 183 processed, 0 failed. Preload backup: `artifacts/prospect_value_research/prod_shadow_compare_2026-05-14/v22_preload_backup_2026-05-21.csv`.
- V2.2 verification found 183 Accounts under run id `prod_shadow_legacy_dust_v22_20260521` and 0 Accounts remaining under `prod_shadow_legacy_dust_v21_20260520`. V2.2 action split: `score_now` 148, `non_icp_confirmed` 10, `url_enrichment_needed` 9, `insufficient_public_evidence` 6, `manual_parent_child_review` 5, `search_fallback_needed` 4, `enterprise_sales_review` 1. For scored rows, confidence split is High 107, Medium 32, Low 9.
- The `AI Value Shadow Compare` report filter was updated to the V2.2 run id on 2026-05-21. Deploy id: `0AfTP0000042WpV0AU`.
- Full-prospect production scoring remains gated. A 75-account greenfield Nimble review cohort has been written to hidden production fields for Will feedback; broader production refresh beyond the 183 legacy-Dust comparison Accounts still requires calibration/feedback approval.
- Greenfield validation universe was sized on 2026-05-21: 13,688 Prospect Accounts with confirmed website hygiene, no legacy Dust tier, and no closed New Business Opportunity MCV anchor. Segment split: null 7,259, Core 3,698, Strategic 1,587, `< 10` 1,046, Nationals 98. A 25-row sample export is saved at `artifacts/prospect_value_research/prod_shadow_compare_2026-05-14/greenfield_no_closed_mcv_no_legacy_sample_25_2026-05-21.csv`.
- A Nimble greenfield test was run on 2026-05-21 against the combined 75-account test set, merging Will's null-date report cohort with the supplemental curated greenfield/control set. Script: `scripts/run_greenfield_nimble_test.py`. Final review artifact: `artifacts/prospect_value_research/greenfield_test_2026-05-21/greenfield_v2_will_review_location_scaled.csv`. Result: 75 rows, 59 `score_now`, 7 `hygiene_review`, 5 `non_icp_confirmed`, 4 `insufficient_public_evidence`; retrieval status 68 `ok`, 2 `partial`, 5 `extract_failed`.
- The 75-row greenfield Nimble review cohort was written to hidden production Account scoring fields on 2026-05-21 under run id `greenfield_nimble_v1_20260521`. Two earlier bulk jobs (`750TP00000jwWIeYAM`, `750TP00000jwRu4YAE`) failed at CSV parsing before row processing. The successful safe-LF upload used bulk job id `750TP00000jwjqsYAA`; result: 75 processed, 0 failed. The Salesforce action split is: `score_now` 59, `url_enrichment_needed` 7, `non_icp_confirmed` 5, `insufficient_public_evidence` 4.
- Production greenfield review report deployed on 2026-05-21. Report: `Public Reports / AI Greenfield Nimble Test`; report id: `00OTP00000Dhutt2AB`; metadata full name: `unfiled$public/AI_Greenfield_Nimble_Test`; deploy id: `0AfTP0000042Xon0AE`. Report link: `https://certifid2022.my.salesforce.com/lightning/r/Report/00OTP00000Dhutt2AB/view`.
- Greenfield Nimble V1.1 was run and written to hidden production Account scoring fields on 2026-05-22 after Will feedback. Script updates: `scripts/run_greenfield_nimble_test.py` now prioritizes common location index pages and location-sitemap evidence, validates cached page URLs before reuse, and treats trusted Sales Rep/BDR Account `Final_Monthly_Closing_Volume__c` as a greenfield anchor/floor/cap. Review/writeback builder: `scripts/build_greenfield_nimble_review_writeback.py`.
- V1.1 artifacts live under `artifacts/prospect_value_research/greenfield_test_2026-05-21`: `greenfield_v4_full_results_sales_rep_location_anchor.csv`, `greenfield_v4_will_review_sales_rep_location_anchor.csv`, `greenfield_v4_sfdc_writeback_sales_rep_location_anchor.csv`, and preload backup `greenfield_v4_preload_backup_2026-05-22.csv`.
- V1.1 production writeback completed on 2026-05-22. Bulk job id: `750TP00000jzLvOYAU`; run id: `greenfield_nimble_v11_20260522`; model version: `greenfield_nimble_v1_1_sales_rep_location_anchor`; result: 75 processed, 0 failed. The Salesforce action split is unchanged: `score_now` 59, `url_enrichment_needed` 7, `non_icp_confirmed` 5, `insufficient_public_evidence` 4. The prior greenfield run id `greenfield_nimble_v1_20260521` now has 0 Accounts.
- The `AI Greenfield Nimble Test` report filter was updated to the V1.1 run id on 2026-05-22. Deploy id: `0AfTP0000042jMv0AI`; report id remains `00OTP00000Dhutt2AB`. Report link: `https://certifid2022.my.salesforce.com/lightning/r/Report/00OTP00000Dhutt2AB/view`.
- Greenfield scale-250 test was built from Will's Salesforce report `v2-Greenfield- Test for AI` (`00OTP00000DyfvF2AR`) plus Abbey Waddell backfill. Will's report returned 186 Accounts with null/zero Final Monthly Closings, null Last New Business Opp Created Date, Prospect type, no active opportunity count, Strategic segment, and LeanData ownership. Abbey backfill added 64 confirmed-website Strategic greenfield Accounts using the same no-MCV/no-opportunity filters and excluding overlap from the prior 75-account pilot.
- Scale-250 Nimble run completed locally on 2026-05-28 using `scripts/run_greenfield_nimble_test.py`. Artifacts live under `artifacts/prospect_value_research/greenfield_scale_2026-05-28`, including `greenfield_250_input_2026-05-28.csv`, raw scrape output under `raw_greenfield_250`, full results `greenfield_250_results_2026-05-28.csv`, ARR-calibrated results `greenfield_250_results_arr_calibrated_2026-05-28.csv`, Will review CSV `greenfield_250_will_review_arr_calibrated_2026-05-28.csv`, and writeback CSV `greenfield_250_sfdc_writeback_arr_calibrated_2026-05-28.csv`.
- ARR calibration was tightened after Will's feedback that top-end ARR looked slightly aggressive. The top MCV bands now map to a $150,000 ARR point and `$138K-$165K` range instead of the prior $200,000 / `$175K-$215K` top band. This changed 17 scale-250 rows and is reflected in the local scoring script plus the ARR-calibrated artifacts.
- Scale-250 production writeback to hidden Account scoring fields completed on 2026-05-29 UTC. Bulk job id: `750TP00000kKsrPYAS`; run id: `greenfield_scale250_v12_20260528`; model version: `greenfield_nimble_v1_2_scale250_arr_calibrated`; result: 250 processed, 0 failed. Salesforce action split: `score_now` 193, `search_fallback_needed` 22, `insufficient_public_evidence` 18, `url_enrichment_needed` 12, `non_icp_confirmed` 5. Confidence split: High 107, Medium 126, Low 17.
- Scale-250 production review report deployed on 2026-05-29 UTC. Report: `Public Reports / AI Greenfield Scale 250 Test`; report id: `00OTP00000E0GO62AN`; metadata full name: `unfiled$public/AI_Greenfield_Scale_250_Test`; deploy id: `0AfTP00000445V30AI`. Report link: `https://certifid2022.my.salesforce.com/lightning/r/Report/00OTP00000E0GO62AN/view`.
- Virginia ID outreach real-world run completed on 2026-06-09 using Will's/Jim's source report `Virginia ID Outreach June 2026` (`00OTP00000EDXFp2AP`) and the related account-focused copy (`00OTP00000EEh1r2AD`) for validation. The Contacts & Accounts report returned 726 contact rows, deduped to 236 unique Accounts. Cohort context: 202 confirmed website-hygiene Accounts, 95 Accounts with Account Final MCV above zero, and 106 Accounts with some New Business Opportunity history.
- Virginia run artifacts live under `artifacts/prospect_value_research/virginia_id_outreach_2026-06-09`, including report describe/run JSON, `virginia_id_report_accounts.csv`, `account_context.csv`, `opp_context.csv`, `virginia_id_outreach_input_2026-06-09.csv`, raw Nimble output under `raw_virginia_id_outreach`, no-write results `virginia_id_outreach_results_2026-06-09.csv`, pre-anchor review/writeback CSVs, final writeback `virginia_id_outreach_sfdc_writeback_final_2026-06-09.csv`, and preload backup `virginia_id_outreach_preload_backup_2026-06-09.csv`.
- Virginia run scoring used the Nimble V1.2 ARR calibration plus the MCV source hierarchy. Script updates on 2026-06-09 route missing/unconfirmed websites directly to `url_enrichment_needed`, skip sitemap/XML/feed/blog URLs during page selection, recognize `Sales Rep`/`BDR` Account MCV sources as high trust, and keep the $150K top ARR calibration in the MCV-anchor post-processor.
- Virginia production writeback to hidden Account scoring fields completed on 2026-06-09. Bulk job id: `750TP00000kvcEBYAY`; run id: `virginia_id_outreach_v12_20260609`; model version: `account_value_nimble_v1_2_va_outreach_mcv_anchor`; result: 236 processed, 236 successful, 0 failed. Salesforce action split: `score_now` 126, `insufficient_public_evidence` 54, `url_enrichment_needed` 34, `search_fallback_needed` 16, `non_icp_confirmed` 6. MCV decision-source split: website-implied 133, closed Opportunity anchor 70, Sales Rep Account MCV anchor 20, latest New Business formula anchor 6, Marketing fallback 7.
- Virginia production review report deployed on 2026-06-09. Report: `Public Reports / AI Virginia ID Outreach Scoring`; report id: `00OTP00000ESo8L2AT`; metadata full name: `unfiled$public/AI_Virginia_ID_Outreach_Scoring`; deploy id: `0AfTP0000046Pnp0AE`. Report link: `https://certifid2022.my.salesforce.com/lightning/r/Report/00OTP00000ESo8L2AT/view`.
- Virginia run review caveat: the model still needs explicit law-firm and real-estate-brokerage handling before broad production segmentation use. The report intentionally preserves those cases for Will/Jim/BDR review instead of hiding them from the first real-world validation pass.
- Vertical resolver POC completed on 2026-06-09 against 45 unresolved Virginia Accounts. Script: `scripts/run_vertical_resolver_poc.py`; sample: `artifacts/prospect_value_research/vertical_resolver_poc_2026-06-09/vertical_resolver_poc_sample_2026-06-09.csv`; initial results: `vertical_resolver_poc_results_final_2026-06-09.csv`; entity-gated rerun: `vertical_resolver_poc_results_entity_gated_2026-06-09.csv`; corrected readout: `vertical_resolver_poc_entity_gated_readout_2026-06-09.md`. No Salesforce writeback was performed.
- Vertical resolver POC sample was stratified from the unresolved Virginia population: 13 bad/missing URL, 18 confirmed URL extraction failures, 8 manual/search fallback, and 6 non-ICP suspects. After the entity-match/law-firm classifier correction plus the `LLP` law-signal fix, final output is 7 high-confidence recoveries, 15 reviewer-assist cases, 17 law-firm review cases, and 6 unresolved.
- Virginia SCC registry overlay completed on 2026-06-09 using `scripts/run_va_scc_registry_check.py` against the entity-gated 45-row output. The SCC Agency search found 22 active agency matches: 18 Settlement Agent matches and 4 Title matches. The run now caches raw SCC result pages under `raw_scc_registry`, tries short-initial/domain query variants, and prefers unstarred legal-name records over starred alias rows when scores tie. Artifacts: `vertical_resolver_poc_scc_registry_check_2026-06-09.csv`, `vertical_resolver_poc_scc_registry_summary_2026-06-09.json`, and `vertical_resolver_poc_labeling_template_with_scc_2026-06-09.csv`.
- Reviewer-ready vertical resolver package completed on 2026-06-09 using `scripts/build_vertical_resolver_review_package.py`. It adds conservative provisional labels, manual VSB check fields for law-firm SCC non-matches, and a metrics scaffold. Artifacts: `vertical_resolver_poc_reviewer_ready_labels_2026-06-09.csv`, `vertical_resolver_poc_review_metrics_scaffold_2026-06-09.json`, and `vertical_resolver_poc_review_metrics_scaffold_2026-06-09.md`. Current review work: 26 candidate websites to label, 16 manual VSB/attorney-settlement checks, and 9 SCC-confirmed rows with no candidate website.
- First-pass vertical resolver validation completed on 2026-06-09 using `scripts/build_vertical_resolver_first_pass_validation.py`. It does not overwrite ground truth and no longer computes a self-referential precision or likely-correct rate. Artifacts: `vertical_resolver_poc_first_pass_validation_2026-06-09.csv`, `vertical_resolver_poc_first_pass_metrics_2026-06-09.json`, and `vertical_resolver_poc_first_pass_metrics_2026-06-09.md`. First-pass status: 26 candidate websites, 23 labeled likely correct as triage labels, 3 candidate websites still needing human review, 22 SCC-confirmed ICP rows, 11 website candidates on SCC-confirmed ICP rows, 3 website candidates on SCC-silent rows, 2 SCC-confirmed ICP rows with candidate websites still needing review, 9 SCC-confirmed ICP rows still missing a website, and 16 law-named rows needing manual VSB/attorney-settlement review.
- Final vertical resolver metrics scaffold completed on 2026-06-09 using `scripts/compute_vertical_resolver_final_metrics.py`. It consumes populated `GroundTruth*` columns, annotates SCC/candidate-city corroboration, and computes candidate-website precision, ICP positive precision, and recovery-by-bucket metrics only with controlled labels. The script accepts only exact controlled label values (`Yes`/`No`/`Pending` for website, VSB, and human ratification; fixed ICP labels for class) so notes like `no matches`, `No - no VSB matches`, `no settlement work`, or `ICP - ... - WRONG` cannot be parsed as labels. It also flags any `ManualVSBRegisteredSettlementAgent = Yes` versus `GroundTruthICPClass = Non-ICP` conflict and reports label provenance, human-ratified precision, and computed sensitivity lines.
- Provisional vertical resolver labeling pass completed on 2026-06-09. Artifacts: `vertical_resolver_poc_reviewer_ready_labels_provisional_2026-06-09.csv`, `vertical_resolver_poc_final_metrics_from_first_pass_provisional_2026-06-09.md`, and `vertical_resolver_poc_human_ratification_priority_rows_2026-06-09.csv`. Current-label result is 92.3% candidate-website precision (24/26), 100.0% predicted-ICP precision (26/26), and blended website recovery of exactly 60.0% (18/30). This is not a cleared gate because labels are provisional, 0 candidate website labels are human-ratified, and the 16-row VSB attorney-settlement lane remains `Pending`.
- Vertical resolver POC takeaway: generic website enrichment is not enough, and the gate remains open. SCC is the current workhorse for ICP confirmation, while the website resolver's strategic value is supplying official website and volume/ARR evidence for ICP-confirmed or registry-silent accounts. SCC matches are strong positive ICP confirmation for title/settlement agencies and corrected three original `non_icp_suspect` cases. SCC non-matches are silent for law-named accounts because attorney settlement agents may register through the Virginia State Bar instead of the SCC Bureau of Insurance. Next gate is human ratification of the 14 priority website rows, manual VSB lookup for the 16 law-named rows, and a final metrics rerun before expanding or writing staged evidence.
- Virginia resolver overlay no-write rescore completed on 2026-06-10. Artifacts: `virginia_resolver_overlay_impact_readout_2026-06-10.md`, `virginia_resolver_overlay_rescore_review_2026-06-10.csv`, and `virginia_resolver_overlay_rescore_readout_2026-06-10.md`. The 45-row overlay joined cleanly to the 236-row Virginia outreach run and surfaced 18 rescore candidates: 12 SCC-backed title/settlement rows, 2 SCC-silent/provisional title/settlement rows, 1 attorney-settlement row, and 3 law-firm rows pending VSB. The no-write rescore produced 15 `score_now` candidates and 3 exceptions. No Salesforce fields were updated in this no-write pass.
- Scoring hardening completed on 2026-06-10: the non-ICP `dmv` rule now requires motor-vehicle context, so Northern Virginia companies that use `DMV area` to mean DC/Maryland/Virginia are not incorrectly disqualified.
- Public-source vertical resolver review advanced on 2026-06-10. Artifacts: `vertical_resolver_poc_reviewer_ready_labels_public_source_review_2026-06-10.csv`, `vertical_resolver_poc_final_metrics_public_source_review_2026-06-10.md`, `vertical_resolver_poc_gate_status_public_source_review_2026-06-10.md`, and `vertical_resolver_vsb_lookup_queue_2026-06-10.csv`. Result: 24 of 26 candidate website labels ratified, ratified candidate-website precision 91.7% (22/24), current-label website precision 92.3% (24/26), current-label ICP precision 100.0% (26/26), ratified-label ICP precision 100.0% (16/16), and blended website recovery exactly 60.0% (18/30). Production writeback is still not cleared because Holstrom Law and Kerns & Kastenbaum remain unratified candidate labels, all 16 VSB-required rows remain pending, one Yes-to-No flip fails the precision gate, and recovery is exactly at threshold.
- Accepted-only Virginia resolver refresh completed on 2026-06-10 after explicitly setting aside rows that still require law-firm/manual review. Artifacts: `virginia_resolver_overlay_accepted_refresh_results_2026-06-10.csv`, `virginia_resolver_overlay_excluded_review_queue_2026-06-10.csv`, `virginia_resolver_overlay_accepted_refresh_sfdc_writeback_2026-06-10.csv`, `virginia_resolver_overlay_accepted_prewrite_backup_2026-06-10.csv`, `virginia_resolver_overlay_accepted_postwrite_verify_2026-06-10.csv`, `virginia_report_filter_postdeploy_counts_2026-06-10.json`, and `virginia_resolver_overlay_accepted_refresh_readout_2026-06-10.md`. The accepted subset contained 14 `score_now` rows; 4 rows were parked for follow-up: James Jordan Law, Chaplin and Qureshi PLC, Holstrom Law PLC, and Kerns and Kastenbaum PLC. Production bulk job id: `750TP00000kzV4XYAU`; run id: `va_resolver_overlay_accepted_20260610`; model version: `account_value_v2_2_va_resolver_overlay`; result: 14 processed, 14 successful, 0 failed. Post-write verification found 14 Accounts under the new run id. No `Account.Website` values were changed. The `AI Virginia ID Outreach Scoring` report filter was deployed on 2026-06-10 to include both the original Virginia run id and the accepted refresh run id. Initial report filter deploy id `0AfTP0000046eGb0AI` used boolean filter logic that Salesforce accepted but the report UI rejected; corrected deploy id `0AfTP0000046erh0AA` replaced it with a single comma-separated run-id filter. Verification count: 222 original-run Accounts plus 14 accepted-refresh Accounts, preserving the 236-account report population.
- California DFPI scoring run completed on 2026-06-11 from Amanda's source report `DFPI Escrow Contacts - CA` (`00OTP00000ECwMj2AL`). The Contacts & Accounts report returned 1,907 contact rows, deduped to 477 unique Accounts. Cohort context: report filter already required `CA_DFPI_Licensed__c = True`; account mix was 352 Escrow company, 122 Title company, 2 Law firm, and 1 Underwriter. Only 18 Accounts had Account Final MCV above zero and 27 had any New Business Opportunity history, so the run was mostly website-implied scoring with DFPI as an ICP/source context signal.
- California run artifacts live under `artifacts/prospect_value_research/california_accounts_2026-06-11`, including report describe/run JSON, `california_report_contacts.csv`, `california_report_accounts.csv`, `account_context.csv`, `opp_context.csv`, `california_scoring_input_2026-06-11.csv`, raw Nimble output under `raw_california_scoring_2026-06-11`, no-write results `california_scoring_results_2026-06-11.csv`, final review `california_scoring_final_review_2026-06-11.csv`, final writeback `california_scoring_sfdc_writeback_final_2026-06-11.csv`, preload backup `california_scoring_prewrite_backup_2026-06-11.csv`, post-write verification `california_scoring_postwrite_verify_2026-06-11.csv`, and readout `california_scoring_run_readout_2026-06-11.md`.
- California production writeback to hidden Account scoring fields completed on 2026-06-11. Bulk job id: `750TP00000l4Z7UYAU`; run id: `california_dfpi_v12_20260611`; model version: `account_value_nimble_v1_2_ca_dfpi_mcv_anchor`; result: 477 processed, 477 successful, 0 failed. Salesforce action split: `score_now` 422, `insufficient_public_evidence` 39, `url_enrichment_needed` 6, `search_fallback_needed` 5, `non_icp_confirmed` 5. MCV decision-source split: website-implied 449, closed Opportunity anchor 18, latest New Business formula anchor 4, Account Marketing fallback 4, Account Sales Rep anchor 2. No `Account.Website` values were changed.
- California production review report deployed on 2026-06-11. Report: `Public Reports / AI California DFPI Scoring`; report id: `00OTP00000EbmdV2AR`; metadata full name: `unfiled$public/AI_California_DFPI_Scoring`; deploy id: `0AfTP0000046vHR0AY`. Report link: `https://certifid2022.lightning.force.com/lightning/r/Report/00OTP00000EbmdV2AR/view`. Analytics API verification returned 477 report rows.
- Legal/title/law-firm validation scoring was written to hidden production Account scoring fields on 2026-06-16 for the 200-account dry-run cohort. Prewrite backup: `artifacts/prospect_value_research/legal_law_firm_validation_2026-06-16_batch2/legal_validation_200_prewrite_backup_2026-06-16.csv`. The initial LF upload job `750TP00000lKOSyYAO` failed before row processing due line-ending validation. Successful CRLF bulk job id: `750TP00000lKORLYA4`; result: 200 processed, 200 successful, 0 failed. Follow-up null-clear job id `750TP00000lKSoHYAW` cleared stale MCV/ARR/rank/score fields on 33 non-score rows where Salesforce Bulk API did not clear blank CSV cells; result: 33 processed, 33 successful, 0 failed. Final normalized readback comparison: 200 rows, 0 missing ids, 0 field mismatches. Run ids: `legal_law_firm_validation_anchor_rail_v3_20260616` and `legal_law_firm_validation_batch2_anchor_rail_v3_20260616`; model version: `account_value_legal_lane_anchor_rail_v3_20260616`.
- Legal/title/law-firm anchor-guard correction was written to hidden production Account scoring fields on 2026-06-17 after Will review found several over-aggressive legal estimates. The correction added corroboration-bounded non-customer MCV anchors and a small legal-firm cap. Source staged cohort was 200 rows, but one `manual_review` Account (`001TP00000lyT5iYAE` / Google) was deleted in Salesforce and excluded from upload. First bulk job `750TP00000lNrtSYAS` failed before row processing due malformed line endings; successful corrected CRLF bulk job id: `750TP00000lOG8jYAG`; result: 199 processed, 199 successful, 0 failed. Postwrite readback comparison: 199 rows, 0 missing ids, 0 field mismatches. Run id: `legal_law_firm_validation_200_anchor_guard_v3_20260617`; model version: `account_value_legal_lane_anchor_guard_v3_20260617`. Value changes versus the June 16 writeback were limited to 14 Accounts, all reductions or same-band trims; no `Account.Website` values were changed.
- Legal/title/law-firm production audit report deployed on 2026-06-16. Report: `Public Reports / AI Legal Title Law Scoring`; report id: `00OTP00000Enjrx2AB`; metadata full name: `unfiled$public/AI_Legal_Title_Law_Scoring`; deploy id: `0AfTP0000047x7V0AQ`. Report link: `https://certifid2022.lightning.force.com/lightning/r/Report/00OTP00000Enjrx2AB/view`. Analytics API verification returned 200 report rows.
- ARR field labels/help text were updated in production on 2026-06-16 to prevent booked/forecast ARR misreads in scoring reports. Deploy id: `0AfTP0000047ygH0AQ`. `AI_Prospect_Value_ARR_Point__c` now displays as `AI Prospect Pipeline Potential ARR Point`; `AI_Prospect_Value_ARR_Range__c` now displays as `AI Prospect Pipeline Potential ARR Range`. Help text states the values are directional Pipeline Potential ARR for prioritization, not booked or forecast ARR.

### 2026-05-20 Review Feedback

The first production shadow review found a clear under-valuation pattern on some strategic accounts. The model framework is directionally useful, but the initial shadow score over-relied on website-implied evidence and did not anchor strongly enough to reliable Salesforce MCV history.

Examples:

- `Tennessee Title Services, LLC` was shadow-scored at 40 MCV. SFDC has a prior Closed Lost / New Business opportunity with `Monthly_Closing_Volume__c = 450`, and Account-side `Final_Monthly_Closing_Volume__c = 133` from Sales Rep.
- `Crescent Title, LLC` was shadow-scored at 62 MCV. SFDC has a prior Closed Lost / New Business opportunity with `Monthly_Closing_Volume__c = 300`, and Account-side `Final_Monthly_Closing_Volume__c = 166` from Sales Rep.
- `Trident Land Transfer Co.` was shadow-scored at 175 MCV. SFDC Account-side `Final_Monthly_Closing_Volume__c = 196` from Sales Rep, and no related Opportunity MCV was found in the check.

The 2026-05-20 V2.1 quick turn corrected these examples in the hidden production shadow fields:

- `Tennessee Title Services, LLC`: 450 MCV, $150K ARR point, rank 9, High confidence.
- `Crescent Title, LLC`: 300 MCV, $125K ARR point, rank 19, High confidence.
- `Trident Land Transfer Co.`: 196 MCV, $56.5K ARR point, rank 32, Medium confidence.

### 2026-05-21 Review Feedback

Will's second review validated the direction of V2.1 and found several positive examples where the model correctly adjusted up from the legacy Dust score. It also exposed an important source-ordering issue:

- `Miller and Assoc., LLC` had a stale Closed Lost / New Business Opportunity at 10 MCV from 2022-03-02, while Account-side `Final_Monthly_Closing_Volume__c = 100` from `Sales Rep` and a more recent open New Business Opportunity also had 100 MCV. V2.1 chose the stale 10 MCV. V2.2 now chooses 100 MCV using the Sales Rep Account MCV floor/override.
- `Counselors Title` needs follow-up with the exact reviewed Salesforce record. One similarly named production Account has a Closed Lost / New Business Opportunity at 166 MCV, an open New Business Opportunity at 100 MCV, and Marketing Account MCV at 200. The V2.2 hierarchy should prefer the closed New Business MCV before Marketing fallback.
- `Patch Reef Title Co. Inc.` remains a positive website-derived example: 125 MCV based on six locations, multi-county footprint, 11-25 staff signal, tools, and 35+ year establishment signal.
- `Infinity Title Agency` remains a positive SFDC-anchor example: 181 MCV using Sales Rep Account MCV.

V2.2 source hierarchy:

1. Latest closed New Business Opportunity MCV, unless a trusted Sales Rep Account MCV provides a higher floor.
2. Sales Rep Account MCV as high-trust floor/override.
3. Latest New Business Opp MCV account formula or open New Business Opportunity MCV as medium-confidence fallback.
4. Marketing Account MCV as lower-confidence fallback.
5. Website-implied MCV and capacity floor when no reliable Salesforce MCV exists.

### 2026-05-20 Nimbleway Pilot

A local Nimbleway smoke test was run from `C:\Users\Mikeh\Python\Fuzzy_Matcher` using the existing `.env` credential and `g-gremlin nimbleway` CLI.

Pilot target: `https://tennesseetitle.com`.

Artifacts: `artifacts/prospect_value_research/nimble_pilot_2026-05-20`.

Results:

- `nimbleway doctor` succeeded against the target site. Nimble resolved the final URL as `https://www.tennesseetitle.com/` with HTTP 200.
- `nimbleway map` discovered the relevant internal pages, including `team.html`, `about.html`, `services.html`, `contact.html`, `calculator.html`, `earnest-money.html`, and the ALTA certificate PDF.
- Raw markdown extraction worked quickly for `team.html`, `contact.html`, and `services.html`.
- Rendered/parser extraction timed out on the first test, and no-render parser extraction also timed out on `contact.html`.
- Local deterministic parsing of the raw Nimble markdown produced a usable structured sample:
  - 10 visible office/location blocks, including 2 affiliate-labeled offices.
  - 8 non-affiliate visible offices.
  - 3 visible leadership profiles.
  - 10 visible service/process items.
  - EMD Pay and Fee Calculator digital/process signals.

Recommended implementation direction:

- Use Nimbleway for page discovery and raw page retrieval.
- Do location/staff/service extraction locally with deterministic parsing plus a small adjudication layer where needed.
- Do not rely on slow parser/render calls as the default production path for every prospect page.
- Add parser/render fallback only for pages where raw retrieval is blocked, thin, or structurally ambiguous.

Model changes required before broader refresh:

- Treat reliable Opportunity MCV as a score input/anchor for target prospect scoring when it exists, not only as a backtest label.
- Use Account-side `Final_Monthly_Closing_Volume__c` and `Monthly_Closing_Volume_Source__c` as fallback anchors when no reliable Opportunity MCV exists, with source-specific confidence.
- Preserve a separate website-implied MCV so reviewers can see when the website estimate conflicts with historical Salesforce evidence.
- Index much more heavily on verified physical office count and visible staff/team size. These are the highest-value website extraction signals for this domain.
- Add extraction quality flags for missed locations, weak team extraction, and high-quality/professional website signals.
- Keep BlackKnight/ICE-derived transaction data out of the primary MCV label hierarchy unless a specific field is proven to represent closing-side volume rather than transaction activity.
- Do not expand the production refresh until Tennessee/Crescent-style under-valuation is corrected and backtested.

### Account Universe

- Total Accounts: 21,794 on 2026-05-05
- Prospect Accounts: 19,282 on 2026-05-05; 19,000 on 2026-05-12
- Prospect Accounts with Website populated: 18,868 on 2026-05-05; 18,596 on 2026-05-12 after the website hygiene promotion pass
- Other Account types:
  - Customer: 2,427
  - Partner: 38
  - Other: 18
  - Blank Type: 29

### Website Hygiene Status

Website hygiene V1 is materially complete enough to feed account scoring.

Relevant local artifact:

- `artifacts/website_hygiene/account_website_master_update_final_summary_20260508.csv`

Final promotion pass:

- 460 high-confidence staged candidates reviewed for possible `Account.Website` promotion
- 299 approved by the AI review layer
- 92 left for manual review
- 69 rejected
- 240 safest records promoted to `Account.Website`
- Final `Account.Website` update job: 240 successful, 0 failures

Salesforce status as of 2026-05-12 for Prospect Accounts:

- `Website_Hygiene_Status__c = confirmed`: 16,113
- `Website_Hygiene_Review_Status__c = no_review_needed`: 16,113
- `Website_Hygiene_Review_Status__c = queued`: 1,607
- `Website_Hygiene_Review_Status__c = manual_research_needed`: 820
- `Website_Hygiene_Review_Status__c = auto_update_candidate`: 451

Scoring implication: use confirmed website and final/canonical hygiene fields as primary scoring inputs. Treat unresolved website hygiene as a scoring readiness disposition, not as a generic error.

### Relevant Existing Account Fields

Do not overwrite or repurpose these fields for the AI scoring output:

- `Estimated_Number_of_Closings__c` - Monthly Closing Volume
- `Final_Monthly_Closing_Volume__c` - Final Monthly Closing Volume formula
- `Monthly_Closing_Volume_Source__c` - Monthly Closing Volume Source formula
- `Latest_New_Business_Opp_MCV__c` - Latest New Business Opp MCV formula
- `Latest_New_Business_Opp_of_Closings__c` - Latest New Business Opp # of Closings formula
- `Closings_Segmentation_Test_Field__c` - current segmentation formula
- `ICP_Fit__c` - ICP Fit formula checkbox
- `Segmentation_Tier__c` - current segmentation tier
- `Closing_Activity_Evidence__c`, `Closing_Activity_Evidence_Confidence__c`, `Closing_Activity_Evidence_Reasoning__c`
- `Website_Named_Staff_Count__c`, `Website_Staff_Confidence__c`, `Website_Staff_Count_Reasoning__c`
- `Website_Hygiene_Status__c`, `Website_Hygiene_Final_URL__c`, `Website_Hygiene_Review_Status__c`, `Website_Hygiene_Last_Checked__c`

The new fields should be clearly separated as AI/account-value prediction fields.

As of 2026-05-12, useful existing prospect signal coverage includes:

- Prospect Accounts with `Final_Monthly_Closing_Volume__c > 0`: 3,200
- Prospect Accounts with `Closing_Activity_Evidence__c` populated: 5,440
- Prospect Accounts with `Website_Named_Staff_Count__c` populated: 17,776

### Existing Account Layouts

Current Account layouts:

- `Account Layout`
- `BDR Account Layout`
- `Copy of Account Layout`
- `Testing Admin Account Layout`

Do not update layouts during the hidden shadow-feedback phase. If broader UI exposure is approved, update `Account Layout` and `BDR Account Layout` first unless Amanda/RevOps confirms a different layout assignment map.

### Existing Account List Views Worth Noting

Relevant current list views include:

- `All Accounts`
- `My Accounts`
- `Prospect Accounts - Clay`
- `Clay - Title + Law Firms`
- `Clay State Count & Website Status View`
- `All Law Firms`

New list views should be additive and clearly prefixed, not edits to existing views.

### Current V2 Pipeline Output

Core artifacts:

- `artifacts/prospect_value_research/dust_agent_tc8VC3hlZn_full_2026-05-04.json`
- `artifacts/prospect_value_research/dust_agent_Y14HBFdNBB_full_2026-05-04.json`
- `artifacts/prospect_value_research/v2_scoring_outputs/full_304_inline_2026-05-04/v2_scored_full_304.csv`
- `artifacts/prospect_value_research/v2_scoring_outputs/full_304_inline_2026-05-04/top_200_v2_combined.csv`
- `artifacts/prospect_value_research/v2_scoring_outputs/full_304_inline_2026-05-04/top_accounts_v2_40k_plus.csv`
- `artifacts/prospect_value_research/v2_scoring_outputs/full_304_inline_2026-05-04/certifid_top_accounts_v2_shadow_review_refined_2026-05-04.xlsx`

Will's current Dust model:

- Agent name: `Pipeline_Value_Agent`
- Dust id: `tc8VC3hlZn`
- Description: estimates title companies' and law firms' ARR based on website signals.
- Model/runtime at export: Claude Sonnet 4.6, web search/browse, Salesforce, file generation.
- Conceptual design: score 10 observable website signals, estimate MCV, map MCV to ARR.
- Validated seed: trained from 70+ real US-based CertifID customers; Will's currently discussed curated set is 76 Closed Won per-file customers with booked ARR.

Shadow V2 model:

- Agent name: `Pipeline_Value_Agent_V2_Shadow`
- Dust id: `Y14HBFdNBB`
- Design improvement: preflighted inputs, separated website hygiene routing, ICP disposition, and MCV/ARR estimation.

V2 recovery/scoring results:

- V2 scored rows: 304
- `score_now`: 68
- `non_icp_confirmed`: 128
- `insufficient_public_evidence`: 47
- `url_enrichment_needed`: 24
- `enterprise_sales_review`: 15
- `manual_parent_child_review`: 12
- `search_fallback_needed`: 8
- `duplicate_review`: 2

Production legacy-Dust shadow comparison results:

- Artifact directory: `artifacts/prospect_value_research/prod_shadow_compare_2026-05-14`
- Legacy baseline rows: 183
- New V2 scored rows: 183
- Salesforce run id: `prod_shadow_legacy_dust_20260514`
- Side-by-side export: `artifacts/prospect_value_research/prod_shadow_compare_2026-05-14/legacy_vs_ai_shadow_compare.csv`
- V2 action split:
  - `score_now`: 139
  - `non_icp_confirmed`: 10
  - `url_enrichment_needed`: 11
  - `search_fallback_needed`: 5
  - `manual_parent_child_review`: 5
  - `enterprise_sales_review`: 1
  - `insufficient_public_evidence`: 12

Combined Top 200 output:

- 160 rows from current Top Accounts baseline
- 40 rows from V2 scored recoveries
- 200 total rows in `top_200_v2_combined.csv`

Important caveat: the current Top Accounts carryover rows in the combined CSV do not all have `AccountId` populated. They must be matched back to Salesforce before writeback. Do not write any row to SFDC without a resolved Account Id.

Additional caveat: the Top 200 output is mixed-method. 160 rows carry over from Will's original Dust/rubric output and 40 rows come from the V2 scored recovery run. A fully unified list requires resolving Account IDs and re-running the same V2/production scoring path across the carryover rows.

### Calibration Data

Calibration artifacts:

- `artifacts/prospect_value_research/calibration_2026-05-04/closed_lost_new_business_mcv_calibration.csv`
- `artifacts/prospect_value_research/calibration_2026-05-04/closed_lost_new_business_mcv_calibration_preflight_ok.csv`
- `artifacts/prospect_value_research/calibration_2026-05-04/closed_lost_new_business_mcv_calibration_needs_url_review.csv`

Calibration counts:

- Closed Lost opportunities: 4,281
- Closed Lost opportunities with `Monthly_Closing_Volume__c`: 2,985
- Closed Lost opportunities with `Type = 'New Business'` and MCV: 2,871
- Closed Lost New Business MCV rows with Account Website: 2,845
- Deterministic URL preflight `ok`: 2,648
- Calibration rows needing URL review: 197

Calibration rule: use `Opportunity.Monthly_Closing_Volume__c` from Closed Lost / New Business opportunities as the label. Do not use Account-side ICE/BlackKnight fields as training labels.

2026-05-12 live calibration expansion:

- Closed Won opportunities in the last 365 days with `Per_File_Pricing__c = true` and populated `Contract_Amount__c`: 1,024
- Closed Won New Business opportunities in the last 365 days with `Per_File_Pricing__c = true` and populated `Amount`: 580
- Closed New Business opportunities with populated `Monthly_Closing_Volume__c`: 5,344
- Closed Lost New Business opportunities with populated `Monthly_Closing_Volume__c`: 2,885

Calibration rule update:

- Use Will's 76 curated Closed Won / per-file customers as the gold seed and qualitative validation set.
- Use larger Closed Won per-file cohorts to calibrate ARR/value behavior.
- Use closed New Business MCV cohorts to calibrate MCV prediction and score-to-MCV bucket behavior.
- Keep Account-side `Final_Monthly_Closing_Volume__c`, staff count, website hygiene, and closing activity fields as context/features, not primary labels.

## Relationship To Will's Dust Model

This project should be framed as productizing Will's Dust model, not replacing the idea.

What to preserve:

- The 10-signal website rubric:
  - S1 location count
  - S2 physical geographic reach
  - S3 team/staff size
  - S4 underwriter affiliations
  - S5 digital tool sophistication
  - S6 entity/structure complexity
  - S7 service breadth
  - S8 market establishment
  - S9 builder/institutional partnerships
  - S10 attorney-state multiplier for real-estate-closing law firms
- The concept that public website evidence is a proxy for MCV.
- The separation between MCV estimation and ARR conversion.
- The idea that primary capacity signals S1-S3 should cap over-eager estimates when supporting signals are strong but actual capacity evidence is weak.
- The explanation layer Sales needs: why the account scored high, low, or non-scorable.

What to fix:

- Current Dust owns too many tasks at once: browsing, URL validation, ICP qualification, scoring, ARR mapping, formatting, and error handling.
- Current Dust has a conceptual contradiction: it says it is not qualifying fit, then later checks ICP. Production must explicitly separate ICP/scorability from value estimation.
- Site errors are too blunt. Deterministic preflight has already shown many prior `SITE_ERROR` rows are recoverable or reachable.
- The static ARR table should be recalibrated against current per-file/v3 economics.
- The model output is not structured enough for reliable Salesforce writeback, audit, rollback, or reporting.
- The current Top 200 workbook is mixed-method and partially missing Account IDs.

Production translation:

- Dust/LLM: evidence extraction, ambiguous ICP adjudication, concise rationale.
- Code: URL preflight, hygiene routing, feature normalization, score calculation, MCV/ARR mapping, ranking, backtesting, Salesforce writeback, and run audit.
- Salesforce: canonical operating surface for score, rank, confidence, evidence, disposition, and refresh status.

## Target Architecture

### MVP-A Architecture - No-Write Calibration And Shadow Scoring

MVP-A proves the model before any Salesforce score data write. A hidden production metadata shell was deployed on 2026-05-14 so reports and access can be prepared without exposing fields on layouts or populating scores.

1. Version Will's current Dust S1-S10 rubric and score-to-MCV/ARR mapping as code/config.
2. Build calibration datasets from Will's curated Closed Won per-file seed set, Closed Won per-file ARR rows, and closed New Business MCV rows.
3. Join Salesforce Account context, confirmed website hygiene fields, staff-count fields, closing-activity evidence, and relevant opportunity labels.
4. Run deterministic URL preflight and evidence extraction before any model call.
5. Use Dust/LLM-style reasoning only for structured evidence extraction and ambiguous ICP/scorability adjudication.
6. Apply deterministic score normalization, capacity caps, calibrated MCV mapping, ARR mapping, ranking, and confidence scoring in code.
7. Produce no-write shadow outputs and backtest reports for Will/Amanda review.
8. Stop if the calibrated model does not beat or materially clarify the current Dust output.

### MVP-B Architecture - SFDC Metadata And Canary Writeback

MVP-B score writeback starts only after the MVP-A calibration gate is reviewed and accepted.

1. SFDX metadata project generated locally.
2. New Account fields and admin permission set deployed through `sf project deploy start`.
3. Fields remain off Account layouts during the hidden feedback phase.
4. Viewer access, reports, list views, and layouts are added only after the feedback surface is approved.
5. Canary writeback CSV generated from calibrated, AccountId-resolved rows.
6. Canary pushed through `sf data upsert bulk --sobject Account --external-id Id`.
7. Canary validation reviewed against the shadow workbook.
8. Full matched backfill pushed only after canary approval.
9. Sales consumes results through private reports/list views first, then Account layouts only if approved.

2026-05-14 production state:

- A bounded MVP-B shadow writeback was approved and run for the 183 Accounts that had parseable legacy Dust tier notes.
- This run is intentionally scoped for side-by-side feedback, not a full-prospect cutover.
- Fields remain off Account layouts. Use private reports/list views or exports for review.
- Broader production refresh remains gated on feedback and explicit approval.

### Production Architecture

1. Scheduled scoring job exports changed prospect Accounts from SFDC.
2. URL preflight normalizes and routes websites before any model call.
3. Evidence extraction creates structured website evidence.
4. Model scoring runs on structured evidence only.
5. Deterministic ranker computes MCV/ARR/rank fields.
6. Bulk writeback updates Account fields and writes audit history.
7. Calibration/backtest runs against defined MCV and ARR labels with train/test or time-based holdout discipline.
8. Optional Account-level "Refresh Score Now" action queues one-off rescoring.

## MVP Field Model

Use a clear prefix so these fields are not confused with existing operational MCV or segmentation fields.

| Label | API Name | Type | MVP | Notes |
|---|---|---|---|---|
| AI Prospect Value MCV Point | `AI_Prospect_Value_MCV_Point__c` | Number(8,0) | Yes | Numeric midpoint, for sorting and reporting. |
| AI Prospect Value MCV Low | `AI_Prospect_Value_MCV_Low__c` | Number(8,0) | Yes | Lower bound parsed from model range. |
| AI Prospect Value MCV High | `AI_Prospect_Value_MCV_High__c` | Number(8,0) | Yes | Upper bound parsed from model range. |
| AI Prospect Value ARR Point | `AI_Prospect_Value_ARR_Point__c` | Currency(16,0) | Yes | Pipeline-potential ARR point estimate used for ranking; not a booked/forecast ARR prediction. |
| AI Prospect Value ARR Range | `AI_Prospect_Value_ARR_Range__c` | Text(50) | Yes | Pipeline-potential display range such as `$40K-$60K`. |
| AI Prospect Value Rank | `AI_Prospect_Value_Rank__c` | Number(5,0) | Yes | Global prospect rank within the latest model version/run id. Enables `Top 200` list views, but list views should primarily sort by ARR point and filter action/confidence. |
| AI Prospect Value Score | `AI_Prospect_Value_Score__c` | Number(5,2) | Yes | Total S1-S10 score. |
| AI Prospect Fit Score | `AI_Prospect_Fit_Score__c` | Number(5,2) | Yes | Fit/value layer score derived from website/SFDC capacity evidence. |
| AI Prospect Timing Score | `AI_Prospect_Timing_Score__c` | Number(5,2) | Optional | Timing/engagement overlay. Do not populate broadly until timing inputs are validated. |
| AI Prospect Data Confidence Score | `AI_Prospect_Data_Confidence_Score__c` | Number(5,2) | Yes | Hygiene/evidence confidence layer. |
| AI Prospect Value Confidence | `AI_Prospect_Value_Confidence__c` | Picklist | Yes | `High`, `Medium`, `Low`. |
| AI Prospect Value ICP | `AI_Prospect_Value_ICP__c` | Picklist | Yes | Normalized disposition. |
| AI Prospect Value Action | `AI_Prospect_Value_Action__c` | Picklist | Yes | Normalized review action. |
| AI Prospect Value URL Status | `AI_Prospect_Value_URL_Status__c` | Picklist | Yes | Deterministic preflight status. |
| AI Prospect Value Canonical URL | `AI_Prospect_Value_Canonical_URL__c` | URL(255) | Yes | Final/canonical URL used for scoring. |
| AI Prospect Value Evidence | `AI_Prospect_Value_Evidence__c` | Long Text Area(32768) | Yes | Evidence notes visible to Sales/RevOps. |
| AI Prospect Value Components | `AI_Prospect_Value_Components__c` | Long Text Area(32768) | Yes | JSON with S1-S10, model version, notes. |
| AI Prospect Value Model Version | `AI_Prospect_Value_Model_Version__c` | Text(80) | Yes | Example: `will_dust_rubric_v2_calibrated_2026_05`. |
| AI Prospect Value Source | `AI_Prospect_Value_Source__c` | Text(80) | Yes | Example: `TopAccountsV2_2026-05-04`. |
| AI Prospect Value Run Id | `AI_Prospect_Value_Run_Id__c` | Text(80) | Yes | Stable run identifier for rollback/audit. |
| AI Prospect Value Updated At | `AI_Prospect_Value_Updated_At__c` | Date/Time | Yes | Timestamp of scoring/writeback. |

### Legacy Dust Comparison Fields

These fields preserve Will's historical Dust tier output where it was embedded in `Account.Prospecting_Notes__c`. They are for side-by-side comparison only and should not be treated as the canonical scoring runtime.

| Label | API Name | Type | Notes |
|---|---|---|---|
| Legacy Prospect Value Tier | `Legacy_Prospect_Value_Tier__c` | Picklist | Parsed tier from notes such as `[Dust Agent 02/05/26] Tier B`. |
| Legacy Prospect Value Score | `Legacy_Prospect_Value_Score__c` | Number(4,2) | Deterministic tier-to-score mapping used only for comparison. |
| Legacy Prospect Value Date | `Legacy_Prospect_Value_Date__c` | Date | Parsed Dust note date. |
| Legacy Prospect Value Source | `Legacy_Prospect_Value_Source__c` | Text(80) | Source label for the parsed baseline. |
| Legacy Prospect Value Notes | `Legacy_Prospect_Value_Notes__c` | Long Text Area(32768) | Original parsed prospecting note text for audit/review. |

### Picklist Values

`AI_Prospect_Value_Confidence__c`:

- `High`
- `Medium`
- `Low`

`AI_Prospect_Value_ICP__c`:

- `scorable`
- `non_icp`
- `enterprise`
- `hygiene_needed`
- `parent_child_review`
- `duplicate_review`
- `insufficient_public_evidence`
- `unknown`

`AI_Prospect_Value_Action__c`:

- `score_now`
- `non_icp_confirmed`
- `url_enrichment_needed`
- `search_fallback_needed`
- `manual_parent_child_review`
- `enterprise_sales_review`
- `duplicate_review`
- `insufficient_public_evidence`

`AI_Prospect_Value_URL_Status__c`:

- `ok`
- `blocked`
- `dns_error`
- `timeout`
- `ssl_error`
- `server_error`
- `parked_or_placeholder`
- `parked_or_for_sale`
- `suspended_or_inactive`
- `no_url`
- `error`
- `not_run`

### Timing Score Treatment

`AI_Prospect_Timing_Score__c` is optional for MVP-B. The core MVP should not fabricate a timing score just to fill the field.

Acceptable V1 timing inputs, if available and validated:

- Recent Salesforce activity on the Account or associated Leads/Contacts
- Recent CampaignMember response from an associated Lead/Contact
- Recent inbound/demo/form activity reflected in Salesforce
- Recent sales-rep feedback or explicit priority flag
- Recent website visit or intent data only if the source system and Account match logic are confirmed
- Recent new Lead/Contact creation on the Account

Do not use in V1 unless validated:

- ZoomInfo intent, because the Salesforce ZI Intent object had 0 records in the prior check
- HubSpot website visits unless the source, identity resolution, and Account mapping are confirmed
- Sparse Account-level engagement formulas without coverage analysis

If timing inputs are not ready, leave `AI_Prospect_Timing_Score__c` blank and rank primarily by value score, ARR point estimate, confidence, and review action.

### Rank Scope

`AI_Prospect_Value_Rank__c` is not an intrinsic company attribute. It is the global rank for the scored prospect population within one `AI_Prospect_Value_Model_Version__c` and `AI_Prospect_Value_Run_Id__c`.

Rules:

- Recompute rank for every full run.
- Do not compare ranks across model versions without the model version/run id.
- Use rank for list-view convenience, not as the only prioritization field.
- Sales list views should filter on `AI_Prospect_Value_Action__c = score_now` and confidence, then sort by `AI_Prospect_Value_ARR_Point__c` or rank.

## Permission Model

The current hidden rollout creates one additive admin permission set only. A viewer permission set should be added later only when Sales-facing report/list-view access is approved.

### Future `AI_Prospect_Value_Viewer`

Purpose: Sales and Sales Management can read scoring fields.

Field permissions:

- Read all MVP fields.
- No edit access.

Recommended assignments after review:

- `Sales`
- `Sales_User`
- `Sales_Manager`
- `Sales_Management`
- any BDR/ADR permission set Amanda identifies

Status: not deployed.

### `Prospect_Value_Scoring_Admin`

Purpose: RevOps/integration users can write score fields.

Field permissions:

- Read/edit all MVP fields.

Recommended assignments:

- `Sales_Operations_Management`
- `CertifID_Production_API_Access` or the approved integration-user permission set
- selected admins only

Current production assignment:

- `mike.heilmann@certifid.com`
- `nayers@certifid.com`
- `rjain@certifid.com`
- `wlooney@certifid.com`
- `wmarty@certifid.com`

Open decision: confirm whether ongoing production score writeback should run as Mike's CLI user or as the CertifID production API user.

## Account Layout Plan

Do not add the fields to Account layouts during the hidden feedback phase. The first production deploy intentionally left all Account layouts unchanged.

After broader UI exposure is approved, add an `AI Prospect Value` section to the main Account layouts.

Recommended placement:

- On `Account Layout` and `BDR Account Layout`
- Near the existing segmentation / MCV / enrichment fields, not above core account identity fields
- Read-only to Sales through field-level security; editable only for Admin/integration permission set

Recommended fields in the section:

- `AI_Prospect_Value_ARR_Point__c`
- `AI_Prospect_Value_ARR_Range__c`
- `AI_Prospect_Value_Rank__c`
- `AI_Prospect_Value_MCV_Point__c`
- `AI_Prospect_Value_MCV_Low__c`
- `AI_Prospect_Value_MCV_High__c`
- `AI_Prospect_Value_Confidence__c`
- `AI_Prospect_Value_ICP__c`
- `AI_Prospect_Value_Action__c`
- `AI_Prospect_Value_Score__c`
- `AI_Prospect_Value_URL_Status__c`
- `AI_Prospect_Value_Canonical_URL__c`
- `AI_Prospect_Value_Updated_At__c`
- `AI_Prospect_Value_Source__c`
- `AI_Prospect_Value_Evidence__c`

Keep `AI_Prospect_Value_Components__c` visible to admins/RevOps if the layout gets too noisy. It is valuable for audit, but less useful to AEs.

## Account List Views

Create additive Account list views.

### `AI Top 200 Predicted ARR - All`

Filters:

- `Type = Prospect`
- `AI_Prospect_Value_Action__c = score_now`
- `AI_Prospect_Value_Rank__c <= 200`

Columns:

- Account Name
- Account Owner
- Website
- Type
- Billing State
- Account Segment
- AI Prospect Value ARR Point
- AI Prospect Value ARR Range
- AI Prospect Value Rank
- AI Prospect Value MCV Point
- AI Prospect Value Confidence
- AI Prospect Value Updated At
- AI Prospect Value Evidence

Sort:

- `AI_Prospect_Value_ARR_Point__c` descending

### `AI $40K+ Predicted ARR - All`

Filters:

- `Type = Prospect`
- `AI_Prospect_Value_Action__c = score_now`
- `AI_Prospect_Value_ARR_Point__c >= 40000`
- `AI_Prospect_Value_Confidence__c IN High, Medium`

Columns:

- Account Name
- Account Owner
- Website
- Type
- Billing State
- Account Segment
- AI Prospect Value ARR Point
- AI Prospect Value ARR Range
- AI Prospect Value Rank
- AI Prospect Value MCV Point
- AI Prospect Value Confidence
- AI Prospect Value Updated At
- AI Prospect Value Evidence

Sort:

- `AI_Prospect_Value_ARR_Point__c` descending

### `AI Top Predicted ARR - My Accounts`

Filters:

- `Type = Prospect`
- `AI_Prospect_Value_Action__c = score_now`
- `AI_Prospect_Value_Confidence__c IN High, Medium`
- Scope: My Accounts

Same columns as the all-account ARR view.

### `AI URL Hygiene Queue`

Filters:

- `AI_Prospect_Value_Action__c IN url_enrichment_needed, search_fallback_needed`

Sort:

- Owner, then Account Name

### `AI Enterprise Review`

Filters:

- `AI_Prospect_Value_Action__c = enterprise_sales_review`

### `AI Parent Child Review`

Filters:

- `AI_Prospect_Value_Action__c = manual_parent_child_review`

### `AI Non ICP Confirmed`

Filters:

- `AI_Prospect_Value_Action__c = non_icp_confirmed`

## SFDX Project Plan

This section belongs to MVP-B. Do not start Salesforce metadata work until the MVP-A no-write calibration gate is reviewed and accepted.

Because the current folder is not an SFDX project, create a focused project under this workspace.

```powershell
sf project generate `
  --name certifid-prospect-value-sfdc `
  --template standard `
  --manifest `
  --api-version 66.0 `
  --output-dir .\sfdc
```

Then:

```powershell
Set-Location .\sfdc\certifid-prospect-value-sfdc
sf config set target-org=CertifID
```

Retrieve baseline Account metadata before editing:

```powershell
sf project retrieve start `
  --target-org CertifID `
  --metadata CustomObject:Account `
  --metadata "Layout:Account-Account Layout" `
  --metadata "Layout:Account-BDR Account Layout" `
  --wait 20
```

Expected metadata paths to create/edit:

```text
sfdc/certifid-prospect-value-sfdc/force-app/main/default/objects/Account/fields/*.field-meta.xml
sfdc/certifid-prospect-value-sfdc/force-app/main/default/permissionsets/Prospect_Value_Scoring_Admin.permissionset-meta.xml
sfdc/certifid-prospect-value-sfdc/manifest/package.xml
```

Future, only after approval:

```text
sfdc/certifid-prospect-value-sfdc/force-app/main/default/objects/Account/listViews/*.listView-meta.xml
sfdc/certifid-prospect-value-sfdc/force-app/main/default/layouts/Account-Account Layout.layout-meta.xml
sfdc/certifid-prospect-value-sfdc/force-app/main/default/layouts/Account-BDR Account Layout.layout-meta.xml
sfdc/certifid-prospect-value-sfdc/force-app/main/default/permissionsets/AI_Prospect_Value_Viewer.permissionset-meta.xml
```

## Metadata Deployment Plan

For the hidden production metadata pass, deploy by manifest and keep the package limited to Account fields plus the admin permission set. Do not include layouts or list views until the feedback surface is approved.

Production `NoTestRun` is not valid. For a declarative-only package with no Apex, use the default production deploy behavior; Salesforce skips tests when allowed. If future packages add Apex, use the required production test level for that package.

Validate first:

```powershell
sf project deploy start `
  --target-org CertifID `
  --manifest .\manifest\package.xml `
  --dry-run `
  --wait 20
```

Deploy after dry-run passes:

```powershell
sf project deploy start `
  --target-org CertifID `
  --manifest .\manifest\package.xml `
  --wait 20
```

Post-deploy smoke checks:

```powershell
sf data query `
  --target-org CertifID `
  --use-tooling-api `
  --result-format csv `
  --query "SELECT QualifiedApiName, Label, DataType FROM FieldDefinition WHERE EntityDefinition.QualifiedApiName = 'Account' AND QualifiedApiName LIKE 'AI_Prospect%' ORDER BY QualifiedApiName"

sf data query `
  --target-org CertifID `
  --query "SELECT Id, Name, Label FROM PermissionSet WHERE Name = 'Prospect_Value_Scoring_Admin'"
```

Assign field access only to approved validation/admin users. This example assigns the permission set to the current target-org user:

```powershell
sf org assign permset `
  --target-org CertifID `
  --name Prospect_Value_Scoring_Admin

sf data query `
  --target-org CertifID `
  --query "SELECT COUNT(Id) populated_run FROM Account WHERE AI_Prospect_Value_Run_Id__c != null"
```

For hidden rollout verification, retrieve Account layouts and confirm there are no `AI_Prospect` references:

```powershell
sf project retrieve start `
  --target-org CertifID `
  --metadata "Layout:Account-*" `
  --output-dir .\tmp\prod_layout_check_YYYYMMDD `
  --wait 20

rg -n "AI_Prospect" .\tmp\prod_layout_check_YYYYMMDD\layouts
```

## MVP-B Backfill Plan

### Backfill Rules

1. Write only to Account records with a resolved `Id`.
2. Do not update existing operational MCV fields.
3. Do not update ICE/BlackKnight fields.
4. Use `AI_Prospect_Value_Run_Id__c` for every row in the batch.
5. Write all 304 V2 scored rows with AccountId so RevOps can see both positive scores and routed review/disposition.
6. For `score_now` rows, populate MCV/ARR/score fields.
7. For non-scorable/review rows, populate disposition/action/evidence and leave MCV/ARR blank.
8. Current Top Accounts carryover rows should be written only after AccountId matching and manual ambiguity review.

### Build Backfill CSV

Create a script:

```text
scripts/build_sfdc_account_score_backfill.py
```

Inputs:

- `artifacts/prospect_value_research/v2_scoring_outputs/full_304_inline_2026-05-04/v2_scored_full_304.csv`
- optional current Top Accounts match output after AccountId resolution

Output:

- `artifacts/prospect_value_research/sfdc_backfill_2026-05-05/account_ai_prospect_value_backfill.csv`
- `artifacts/prospect_value_research/sfdc_backfill_2026-05-05/account_ai_prospect_value_backfill_summary.json`
- `artifacts/prospect_value_research/sfdc_backfill_2026-05-05/account_ai_prospect_value_rejected_rows.csv`

CSV columns:

```text
Id
AI_Prospect_Value_MCV_Point__c
AI_Prospect_Value_MCV_Low__c
AI_Prospect_Value_MCV_High__c
AI_Prospect_Value_ARR_Point__c
AI_Prospect_Value_ARR_Range__c
AI_Prospect_Value_Rank__c
AI_Prospect_Value_Score__c
AI_Prospect_Value_Confidence__c
AI_Prospect_Value_ICP__c
AI_Prospect_Value_Action__c
AI_Prospect_Value_URL_Status__c
AI_Prospect_Value_Canonical_URL__c
AI_Prospect_Value_Evidence__c
AI_Prospect_Value_Components__c
AI_Prospect_Value_Source__c
AI_Prospect_Value_Run_Id__c
AI_Prospect_Value_Updated_At__c
```

Transform requirements:

- Parse `MCVEstimate` values such as `500-750/mo` into low/high/point.
- Parse `ARRRange` values such as `$40K-$60K` into point estimate.
- Preserve deterministic rank from the latest ranked output when available.
- Normalize model `ICPDisposition` into the supported picklist values.
- Preserve original raw action/disposition inside `AI_Prospect_Value_Components__c`.
- Truncate evidence/components safely to fit long-text field limits.
- Reject rows with blank AccountId.
- Reject rows where normalized picklist values are outside the deployed set.

### Back Up Existing Score Fields

After fields are deployed and before every data write, export the current values for records being touched.

```powershell
sf data query `
  --target-org CertifID `
  --result-format csv `
  --output-file .\artifacts\prospect_value_research\sfdc_backfill_2026-05-05\account_ai_prospect_value_preload_backup.csv `
  --query "SELECT Id, AI_Prospect_Value_MCV_Point__c, AI_Prospect_Value_MCV_Low__c, AI_Prospect_Value_MCV_High__c, AI_Prospect_Value_ARR_Point__c, AI_Prospect_Value_ARR_Range__c, AI_Prospect_Value_Rank__c, AI_Prospect_Value_Score__c, AI_Prospect_Value_Confidence__c, AI_Prospect_Value_ICP__c, AI_Prospect_Value_Action__c, AI_Prospect_Value_URL_Status__c, AI_Prospect_Value_Canonical_URL__c, AI_Prospect_Value_Evidence__c, AI_Prospect_Value_Components__c, AI_Prospect_Value_Source__c, AI_Prospect_Value_Run_Id__c, AI_Prospect_Value_Updated_At__c FROM Account WHERE Id IN ('001_SAMPLE_ACCOUNT_ID')"
```

For the real run, generate the `WHERE Id IN (...)` query from the backfill CSV in chunks if needed.

### Canary Data Write

Salesforce CLI bulk upsert does not have a dry-run mode. Run a 5-record canary first.

```powershell
sf data upsert bulk `
  --target-org CertifID `
  --sobject Account `
  --external-id Id `
  --file .\artifacts\prospect_value_research\sfdc_backfill_2026-05-05\account_ai_prospect_value_canary_5.csv `
  --line-ending CRLF `
  --wait 10
```

Verify canary records in SFDC:

```powershell
sf data query `
  --target-org CertifID `
  --result-format human `
  --query "SELECT Id, Name, AI_Prospect_Value_ARR_Point__c, AI_Prospect_Value_Confidence__c, AI_Prospect_Value_Action__c, AI_Prospect_Value_Run_Id__c FROM Account WHERE AI_Prospect_Value_Run_Id__c = 'TopAccountsV2_2026-05-04_canary'"
```

### Full Data Write

```powershell
sf data upsert bulk `
  --target-org CertifID `
  --sobject Account `
  --external-id Id `
  --file .\artifacts\prospect_value_research\sfdc_backfill_2026-05-05\account_ai_prospect_value_backfill.csv `
  --line-ending CRLF `
  --wait 10
```

If the job times out, resume:

```powershell
sf data upsert resume `
  --target-org CertifID `
  --use-most-recent `
  --wait 10
```

### Post-Write Validation

Expected checks:

```powershell
sf data query `
  --target-org CertifID `
  --result-format human `
  --query "SELECT COUNT() FROM Account WHERE AI_Prospect_Value_Run_Id__c = 'TopAccountsV2_2026-05-04'"
```

```powershell
sf data query `
  --target-org CertifID `
  --result-format human `
  --query "SELECT AI_Prospect_Value_Action__c, COUNT(Id) total FROM Account WHERE AI_Prospect_Value_Run_Id__c = 'TopAccountsV2_2026-05-04' GROUP BY AI_Prospect_Value_Action__c ORDER BY COUNT(Id) DESC"
```

```powershell
sf data query `
  --target-org CertifID `
  --result-format human `
  --query "SELECT Id, Name, Owner.Name, Website, AI_Prospect_Value_ARR_Point__c, AI_Prospect_Value_ARR_Range__c, AI_Prospect_Value_Confidence__c, AI_Prospect_Value_Evidence__c FROM Account WHERE AI_Prospect_Value_Action__c = 'score_now' ORDER BY AI_Prospect_Value_ARR_Point__c DESC LIMIT 20"
```

Acceptance criteria:

- Metadata deploy succeeds with no ignored errors.
- Permission sets exist and grant expected field access.
- Account fields are visible on the approved layouts.
- List views render with expected columns.
- Canary write updates exactly 5 records.
- Full write updates the expected number of records.
- `score_now` count in SFDC matches the backfill CSV.
- Top scored records in SFDC match the workbook ranking for rows with AccountId.
- No existing operational MCV fields are changed.

## Rollback Plan

Metadata rollback:

- Preferred: deploy a reverse metadata package if fields/list views must be removed.
- Practical MVP rollback: leave fields in place, remove page layout section/list views, and clear or restore field values.

Data rollback:

1. Use `account_ai_prospect_value_preload_backup.csv` as the source of truth.
2. Generate a rollback CSV with `Id` and all AI Prospect Value fields.
3. For fields that need to be cleared, use Salesforce Bulk API null handling and validate on a canary first.
4. Upsert rollback CSV:

```powershell
sf data upsert bulk `
  --target-org CertifID `
  --sobject Account `
  --external-id Id `
  --file .\artifacts\prospect_value_research\sfdc_backfill_2026-05-05\account_ai_prospect_value_rollback.csv `
  --line-ending CRLF `
  --wait 10
```

## Full Prospect Universe Build

After the MVP-B canary and matched writeback are live, run the same pattern across all prospect Accounts.

### Export Prospect Universe

```powershell
sf data query `
  --target-org CertifID `
  --bulk `
  --wait 20 `
  --result-format csv `
  --output-file .\artifacts\prospect_value_research\full_prospect_run_YYYY-MM-DD\prospect_accounts.csv `
  --query "SELECT Id, Name, Owner.Name, Website, Type, Industry, BillingCity, BillingState, BillingCountry, Account_Segment__c, Account_Segment_v2__c, Segmentation_Tier__c, Final_Monthly_Closing_Volume__c, Monthly_Closing_Volume_Source__c, ParentId, Parent.Name FROM Account WHERE Type = 'Prospect'"
```

### URL Preflight

```powershell
python .\scripts\url_preflight.py `
  --input .\artifacts\prospect_value_research\full_prospect_run_YYYY-MM-DD\prospect_accounts.csv `
  --url-column Website `
  --name-column Name `
  --id-column Id `
  --output .\artifacts\prospect_value_research\full_prospect_run_YYYY-MM-DD\prospect_url_preflight.csv `
  --workers 24 `
  --timeout 8
```

### Build Scoring Input

Create or extend a script:

```text
scripts/build_full_prospect_scoring_input.py
```

Purpose:

- Join Account context to URL preflight output.
- Route rows into:
  - scoring queue
  - URL hygiene queue
  - search fallback queue
  - enterprise review
  - duplicate/parent-child review
  - no-score unchanged rows
- Produce model input with normalized columns expected by the scorer.

### Evidence Extraction

```powershell
python .\scripts\extract_website_evidence.py `
  --input .\artifacts\prospect_value_research\full_prospect_run_YYYY-MM-DD\scoring_input.csv `
  --output .\artifacts\prospect_value_research\full_prospect_run_YYYY-MM-DD\scoring_input_evidence.csv `
  --timeout 10 `
  --text-limit 1800
```

### Scoring

MVP-compatible option:

- Continue using the existing Dust inline scoring script for short-term continuity:
  - `scripts/run_dust_inline_scoring_batches.py`

Production-preferred option:

- Replace Dust with a direct model API scoring script that enforces a JSON schema and writes raw request/response artifacts per batch.
- Keep deterministic URL preflight, evidence extraction, routing, ranking, and Salesforce writeback in code.

### Rank and Write Back

For full universe runs, write Account fields for all routed outcomes, not just Top 200:

- `score_now`: full score and ARR/MCV fields
- `non_icp_confirmed`: disposition/action/evidence only
- `url_enrichment_needed`: URL status/action/evidence only
- `enterprise_sales_review`: action/evidence only
- `manual_parent_child_review`: action/evidence only
- `insufficient_public_evidence`: action/evidence only

The Top 200 becomes a Salesforce list view/report, not a separate data object.

## Calibration and Backtesting Plan

Run the scoring pipeline as a no-write calibration job before any metadata deployment or Salesforce score writeback.

### Label Hierarchy

MCV calibration label:

- Primary label: `Opportunity.Monthly_Closing_Volume__c` from closed New Business opportunities.
- Preferred MCV calibration rows: closed New Business opportunities with populated MCV, AccountId, Account website, and usable website hygiene/preflight.
- Closed Lost New Business MCV rows remain especially useful because they represent prospect-like selling motion and are less likely to be biased by customer success data.
- If multiple opportunities exist for the same Account, use the most recent closed New Business opportunity with populated MCV for the primary Account-level label, and retain older rows only for secondary sensitivity analysis.

ARR target and calibration label:

- Business definition: `AI_Prospect_Value_ARR_Point__c` is pipeline-potential ARR based on lookbacks for similar closed accounts, not booked/forecast ARR.
- Booked Closed Won ARR remains a sanity-check label: `Opportunity.Won_Contract_Amount__c` or `Opportunity.Contract_Amount__c` from Closed Won opportunities where `Per_File_Pricing__c = true`.
- Will's 76 curated Closed Won / per-file customers are the gold validation seed for shape, tiering, and similarity lookbacks, even if a larger SFDC cohort is used for tuning.
- If multiple Closed Won per-file opportunities exist for the same Account, prefer the most recent non-renewal/new-business win where available; otherwise use the most recent Closed Won per-file opportunity and flag the row as a possible renewal/expansion case.
- Do not train the ARR mapping on open pipeline value fields or agent-generated `Pipeline_Value__c`.

Feature/context rules:

- For calibration evaluation, `Opportunity.Monthly_Closing_Volume__c` remains the primary label.
- For target prospect scoring, reliable historical Opportunity MCV should also be used as the highest-confidence scoring anchor when present on the Account.
- Account-side `Final_Monthly_Closing_Volume__c`, `Estimated_Number_of_Closings__c`, and `Monthly_Closing_Volume_Source__c` should be fallback scoring anchors when Opportunity MCV is unavailable, weighted by source reliability.
- Website-extracted office count, staff/team count, website quality, website hygiene, closing activity evidence, and segment are prediction features and confidence controls.
- Keep `website_implied_mcv`, `sfdc_anchor_mcv`, and `recommended_mcv` conceptually separate in the scoring artifacts even if the MVP only writes one final MCV point to Account.
- Account-side ICE/BlackKnight or formula MCV fields are not primary labels.
- Website hygiene fields should determine scoring readiness and confidence, not force a value estimate.

### Dedupe And Split Rules

- Deduplicate calibration data at the AccountId level for primary model evaluation.
- Keep Opportunity-level rows for diagnostics only when one Account has materially different historical labels.
- Use a time-based holdout where feasible: tune on older closed records and test on the most recent closed records.
- Always hold out Will's 76 curated seed rows, or a meaningful subset of them, from any rubric tuning pass used to claim validation quality.
- Exclude examples that were explicitly used to design or hand-tune a score threshold from the final test metric.
- Report any overlap between Will's seed examples, current Dust examples, and final test rows.

Inputs:

- Account website and context
- Opportunity MCV label from `Opportunity.Monthly_Closing_Volume__c`
- Closed Won per-file ARR label from `Won_Contract_Amount__c` or `Contract_Amount__c`
- Opportunity close date
- Opportunity stage/type/pricing context
- Website hygiene fields
- Website staff count and staff confidence fields
- Closing activity evidence fields
- Owner and last-modified context for QA only

Metrics:

- Predicted MCV point vs actual Opportunity MCV
- Predicted MCV band hit rate
- Predicted ARR point/range vs Closed Won per-file ARR
- Top-decile and top-quartile lift vs current Dust/rubric baseline
- Over/under prediction by state
- Over/under prediction by entity type
- Over/under prediction by website signal confidence
- Over/under prediction by staff-count confidence
- Over/under prediction by website hygiene status
- False non-ICP rate on labeled rows
- Confidence calibration: High should outperform Medium/Low
- Performance on Will's curated seed/holdout rows

Outputs:

- `calibration_predictions.csv`
- `calibration_account_level_labels.csv`
- `calibration_error_by_bucket.csv`
- `calibration_error_by_state.csv`
- `calibration_error_by_entity_type.csv`
- `calibration_error_by_website_hygiene_status.csv`
- `calibration_confidence_summary.csv`
- `calibration_seed_holdout_summary.csv`
- `calibration_vs_current_dust_baseline.csv`
- `calibration_recommendations.md`

Decision gate:

- Do not treat ARR/MCV as validated forecast fields until this backtest is reviewed.
- Do not deploy Salesforce score metadata or write scores until the MVP-A calibration output is reviewed with Will/Amanda.
- The model must show that high-confidence rows are materially more accurate than medium/low-confidence rows.
- The model must show acceptable false non-ICP behavior on known labeled customers/opportunities.
- The model must be at least directionally better than the current Dust baseline or clearly explain where it differs and why that difference is desired.
- Until then, label SFDC fields as "Predicted" and keep evidence/confidence visible.

## Refresh Strategy

### MVP-B Refresh

Manual/script-assisted:

1. Export changed Account rows.
2. Run URL preflight/evidence/scoring scripts.
3. Generate backfill CSV.
4. Push through `sf data upsert bulk`.

### Production Refresh

Nightly or weekly incremental job:

Refresh if any of these changed:

- Account Website
- Account Name
- Type
- Owner
- Industry
- Billing State/Country
- Parent Account
- relevant segment fields
- score older than refresh threshold
- explicit refresh requested

Skip if:

- no meaningful inputs changed
- prior URL status is dead/parked and no website changed
- account is customer/partner unless explicitly included

Suggested cadence:

- Weekly full preflight on all prospect websites
- Nightly incremental scoring for changed rows only
- Quarterly full model rerun after calibration update

## Optional "Refresh Score Now" Button

Do not block the MVP on this.

Recommended design:

1. Create a custom object `AI_Prospect_Value_Refresh_Request__c` or a lightweight Platform Event.
2. Add a Quick Action or Screen Flow on Account.
3. Button creates a pending request with AccountId, requested by, requested at, and reason.
4. External scoring job picks up pending requests and writes back results.
5. Account shows latest request status.

Avoid doing live website scraping/model scoring inside Apex. It is a poor fit for callout timeouts, retry logic, website quirks, and model response handling.

## Audit History Follow-On

For MVP, `AI_Prospect_Value_Run_Id__c`, source artifacts, and backup CSVs are enough.

For production, add an audit object:

`AI_Prospect_Value_Run__c`

Fields:

- Account lookup
- Run Id
- Run timestamp
- Model/provider
- Prompt/scoring version
- URL status
- Canonical URL
- Input hash
- Evidence snapshot
- Raw score JSON
- Prior ARR/MCV
- New ARR/MCV
- Error/status

This gives RevOps a durable history without bloating the Account record.

## Execution Checklist

### Phase 0 - Model Calibration Readiness

- [x] Preserve Will's Dust `Pipeline_Value_Agent` rubric as the conceptual baseline.
- [x] Extract the current Dust score-to-MCV and MCV-to-ARR mapping into a versioned config artifact.
- [ ] Assemble Will's 76 curated Closed Won / per-file customer seed set with AccountId, website, booked ARR, MCV where available, and current Dust score/tier where available.
- [x] Export larger calibration cohorts from SFDC: Closed Won per-file ARR rows and closed New Business MCV rows.
- [x] Build first no-write backtest harness that compares prediction CSV output to known MCV/ARR labels.
- [ ] Define minimum acceptable backtest gates before any Salesforce writeback.

### Phase 1 - Shadow Scoring Pipeline

- [ ] Run deterministic URL preflight against the calibration and Top Accounts rows.
- [ ] Join confirmed website hygiene fields from Salesforce.
- [ ] Extract structured website evidence.
- [ ] Run LLM evidence/scoring pass with structured inputs and structured outputs.
- [ ] Apply deterministic score calculation, capacity caps, calibrated MCV mapping, ARR mapping, and ranking in code.
- [ ] Produce model comparison outputs: Will/Dust baseline vs V2 shadow vs calibrated production candidate.
- [ ] Review misses with Will/Amanda before broader production refresh.

### Phase 2 - Metadata

- [x] Generate Account field metadata.
- [x] Generate permission sets.
- [ ] Generate Account list views.
- [ ] Retrieve and update approved Account layouts.
- [x] Run metadata dry-run for sandbox field and permission set package.
- [x] Deploy sandbox field and permission set package.
- [x] Verify sandbox fields and permission set access.
- [x] Run hidden metadata dry-run for production field and permission set package.
- [x] Deploy hidden production field and permission set package.
- [x] Assign production admin permission set to Mike, Nate, Reena, Will Looney, and Will Marty for validation/report-building access.
- [x] Verify production fields, permission set, initially empty score values, and no Account layout exposure.
- [x] Deploy legacy Dust comparison fields for side-by-side shadow review.
- [ ] Generate Account list views or reports only after the approved feedback surface is selected.
- [ ] Retrieve and update approved Account layouts only after broader UI exposure is approved.

### Phase 3 - Backfill

- [x] Build sandbox backfill transformation script.
- [ ] Generate full backfill CSV.
- [ ] Generate rejected rows CSV.
- [ ] Generate summary JSON.
- [x] Export sandbox preload backup for 5-record canary.
- [x] Run 5-record sandbox canary.
- [x] Validate sandbox canary in SFDC.
- [x] Run 294-record sandbox smoke backfill from V2 shadow output.
- [x] Validate sandbox smoke counts and top records.
- [ ] Run calibrated full backfill after the calibration model is accepted.

### Phase 3A - Production Legacy Dust Shadow Compare

- [x] Identify parseable legacy Dust tier notes on Account.
- [x] Deploy hidden legacy Dust snapshot fields.
- [x] Backfill legacy Dust tier/score/date/source/notes for 183 Accounts.
- [x] Export exact 183-account cohort context from production.
- [x] Run deterministic URL preflight and evidence extraction for the cohort.
- [x] Score the cohort through the V2 no-tool structured scorer.
- [x] Build AI shadow writeback CSV with 183 output rows and 0 rejected rows.
- [x] Export preload backup for current AI shadow fields.
- [x] Bulk upsert AI shadow fields to production. Bulk job id: `750TP00000jYqK4YAK`.
- [x] Verify 183 side-by-side rows in production under run id `prod_shadow_legacy_dust_20260514`.
- [x] Build Salesforce feedback report for the side-by-side cohort. Report: `Public Reports / AI Value Shadow Compare`.
- [x] Re-run the 183-row production shadow comparison with V2.1 MCV-anchor scoring. Bulk job id: `750TP00000jswUwYAI`; run id: `prod_shadow_legacy_dust_v21_20260520`.
- [x] Update the side-by-side report filter to the V2.1 run id. Deploy id: `0AfTP0000042Mer0AE`.
- [x] Re-run the 183-row production shadow comparison with V2.2 source hierarchy. Bulk job id: `750TP00000jw9ieYAA`; run id: `prod_shadow_legacy_dust_v22_20260521`.
- [x] Update the side-by-side report filter to the V2.2 run id. Deploy id: `0AfTP0000042WpV0AU`.
- [ ] Review V2.2 side-by-side accuracy with Nate/Will/Reena/Marty before broader refresh.

### Phase 4 - Full Prospect Run

- [ ] Define greenfield validation cohort: Prospect Accounts with clean website data and no closed New Business MCV anchor.
- [x] Build combined 75-account greenfield/control test set from Will's null-date report cohort plus supplemental curated candidates.
- [x] Run Nimble greenfield test and produce Will review CSV.
- [x] Write 75-account greenfield Nimble review cohort to hidden production scoring fields. Bulk job id: `750TP00000jwjqsYAA`; run id: `greenfield_nimble_v1_20260521`.
- [x] Deploy production greenfield review report. Report: `Public Reports / AI Greenfield Nimble Test`; report id: `00OTP00000Dhutt2AB`.
- [x] Incorporate Will feedback into greenfield V1.1: Sales Rep/BDR Account MCV anchor, stronger location index/sitemap extraction, American Homeland cap behavior, and corrected ARR range writeback.
- [x] Re-run and write V1.1 75-account greenfield review cohort to hidden production scoring fields. Bulk job id: `750TP00000jzLvOYAU`; run id: `greenfield_nimble_v11_20260522`.
- [x] Update production greenfield review report filter to V1.1. Deploy id: `0AfTP0000042jMv0AI`.
- [x] Build 250-account greenfield scale test from Will's no-MCV/no-opportunity report plus 64 Abbey Waddell confirmed-website backfill Accounts.
- [x] Run Nimble extraction/scoring for the 250-account scale cohort and generate ARR-calibrated Will review/writeback artifacts.
- [x] Write scale-250 greenfield review cohort to hidden production scoring fields. Bulk job id: `750TP00000kKsrPYAS`; run id: `greenfield_scale250_v12_20260528`.
- [x] Deploy production scale-250 review report. Report: `Public Reports / AI Greenfield Scale 250 Test`; report id: `00OTP00000E0GO62AN`.
- [x] Run first real-world Virginia ID outreach scoring request from Will/Jim contact report. Source report id: `00OTP00000EDXFp2AP`; 726 contacts deduped to 236 Accounts.
- [x] Write Virginia ID outreach results to hidden production scoring fields. Bulk job id: `750TP00000kvcEBYAY`; run id: `virginia_id_outreach_v12_20260609`.
- [x] Deploy production Virginia outreach review report. Report: `Public Reports / AI Virginia ID Outreach Scoring`; report id: `00OTP00000ESo8L2AT`.
- [x] Run no-write vertical resolver POC on 45 unresolved Virginia Accounts. Entity-gated rerun output after `LLP` law-signal fix: 7 high-confidence recoveries, 15 reviewer-assist cases, 17 law-firm review cases, 6 unresolved. No Salesforce writeback.
- [x] Run Virginia SCC registry overlay on the 45-row vertical resolver sample. Result: 22 active agency matches, including 18 Settlement Agent and 4 Title matches. Raw SCC result pages cached under `raw_scc_registry`.
- [x] Build reviewer-ready vertical resolver label package with provisional labels, manual VSB fields, and metrics scaffold.
- [x] Define separate law-firm-lane review for SCC non-matches, using manual VSB/attorney RESA evidence where available.
- [x] Build first-pass vertical resolver validation package without overwriting ground truth or computing self-referential precision. Result: 11 website candidates on SCC-confirmed ICP rows, 3 website candidates on SCC-silent rows, 2 SCC-confirmed rows with candidate websites still needing review, 9 SCC-confirmed ICP rows still missing a website, 16 law-named rows pending VSB/manual review.
- [x] Build final metrics script and annotated output scaffold with exact controlled-label parsing, VSB label preservation on package regeneration, VSB/Non-ICP conflict flagging, labeler provenance, human-ratified precision slots, data-driven SCC city-check flags, and computed sensitivity lines.
- [x] Complete provisional labeling pass for the 45-row vertical resolver package. Current-label metrics: 92.3% candidate-website precision, 100.0% predicted-ICP precision, blended website recovery exactly 60.0%. This does not clear the gate because `HumanRatified=Pending` and the 16 VSB rows remain unresolved.
- [x] Build 14-row human ratification worksheet for the two current `No` website labels plus the 12 SCC-silent `Yes` website labels, with Esquire Settlement Services first.
- [x] Build Virginia resolver overlay impact view and no-write 18-account rescore queue. Result: 15 `score_now` candidates, 3 review exceptions, no Salesforce write.
- [x] Harden the non-ICP `dmv` rule so regional `DMV area` language does not disqualify Northern Virginia title/settlement companies.
- [x] Complete public-source review of candidate website labels. Result: 24 of 26 candidate website labels ratified; remaining unratified candidate labels are Holstrom Law and Kerns & Kastenbaum.
- [x] Build accepted-only Virginia resolver refresh set by excluding rows that still require law-firm/manual review. Result: 14 accepted `score_now` rows and 4 parked follow-up rows.
- [x] Write accepted-only Virginia resolver refresh to hidden production scoring fields. Bulk job id: `750TP00000kzV4XYAU`; run id: `va_resolver_overlay_accepted_20260610`; result: 14 processed, 14 successful, 0 failed.
- [x] Verify accepted-only Virginia resolver refresh in production. Result: 14 Accounts found under run id `va_resolver_overlay_accepted_20260610`; no `Account.Website` values changed.
- [x] Update the production `AI Virginia ID Outreach Scoring` report filter to include both the original Virginia run id and the accepted refresh run id. Corrected deploy id: `0AfTP0000046erh0AA`; verification count: 222 original-run Accounts plus 14 accepted-refresh Accounts.
- [x] Run California DFPI scoring request from Amanda's contact report. Source report id: `00OTP00000ECwMj2AL`; 1,907 contacts deduped to 477 Accounts.
- [x] Write California DFPI results to hidden production scoring fields. Bulk job id: `750TP00000l4Z7UYAU`; run id: `california_dfpi_v12_20260611`; result: 477 processed, 477 successful, 0 failed.
- [x] Deploy production California DFPI review report. Report: `Public Reports / AI California DFPI Scoring`; report id: `00OTP00000EbmdV2AR`.
- [ ] Resolve the parked follow-up queue: James Jordan Law, Chaplin and Qureshi PLC, Holstrom Law PLC, and Kerns and Kastenbaum PLC.
- [ ] Resolve the two remaining unratified candidate labels: Holstrom Law and Kerns & Kastenbaum.
- [ ] Complete the 16 manual VSB attorney-settlement-agent lookups and record `ManualVSBRegisteredSettlementAgent` plus evidence notes.
- [ ] Re-run final vertical resolver metrics after the parked law-firm/manual lane is completed; only then decide whether the broader vertical resolver gate is cleared.
- [ ] Export full prospect universe.
- [ ] Run URL preflight.
- [ ] Build scoring input/routing queues.
- [ ] Extract website evidence.
- [ ] Run scorer.
- [ ] Build writeback CSV.
- [ ] Run canary.
- [ ] Bulk write back.
- [ ] Validate list views and counts.
- [ ] Do not run broader production score refresh until calibrated output and feedback audience are explicitly approved.

### Phase 5 - Ongoing Calibration

- [x] Add source hierarchy to the 183-row quick-turn scoring logic: latest closed New Business Opportunity MCV, Sales Rep Account MCV floor/override, latest/open New Business MCV fallback, Marketing Account MCV fallback, website-implied MCV, then final recommended MCV.
- [ ] Build structured website extraction for office count, staff/team count, location density, and website quality.
- [x] Re-score the 183 production shadow comparison rows with the V2.2 source hierarchy. Stronger location/staff extraction remains a follow-on.
- [ ] Run a greenfield no-closed-MCV test cohort through stronger website extraction before any full-prospect refresh.
- [ ] Run scoring against the Closed Lost New Business MCV calibration set.
- [ ] Compare website-implied MCV, SFDC anchor MCV, and final recommended MCV against actual Opportunity MCV.
- [ ] Produce calibration report.
- [ ] Adjust scoring rubric/ranges.
- [ ] Version the scoring formula and rerun full prospect refresh.

### Phase 6 - Productionization

- [ ] Decide runtime location for scheduled job.
- [ ] Add secrets management.
- [ ] Add run logging/audit object.
- [ ] Add retry/resume behavior.
- [ ] Add one-off refresh request path if needed.
- [ ] Document operating runbook.

## Open Decisions

1. What exact 76-account seed set from Will should be treated as the gold validation set?
2. Which current Dust output should be preserved for comparison beyond the parsed tier/score/date/notes now stored on 183 Accounts?
3. What backtest gates must be met before SFDC writeback is allowed?
4. Should the MVP write all calibrated scored/disposition rows to SFDC, or only high-confidence `score_now` rows?
5. Should current Top Accounts carryover rows be matched and rescored before any broader production refresh?
6. Which permission sets should receive read access on day one?
7. Should Sales see the raw evidence/components field on Account layout, or only RevOps/Admin?
8. Should the first full prospect run include only `Type = Prospect`, or include some non-prospect records for routing cleanup?
9. Which runtime should own scheduled scoring after the MVP: local scripts, GitHub Actions, Cloud Run, or another CertifID-approved job runner?
10. Should the side-by-side report stay in `Public Reports` with scoring fields gated by FLS, or move into a restricted folder after the first review group is finalized?

## Recommended Immediate Next Tasks

1. Review the accepted Virginia resolver refresh in `Public Reports / AI Virginia ID Outreach Scoring` using run id `va_resolver_overlay_accepted_20260610`.
2. Resolve the parked Virginia follow-up queue: James Jordan Law, Chaplin and Qureshi PLC, Holstrom Law PLC, and Kerns and Kastenbaum PLC.
3. Complete the manual VSB attorney-settlement-agent lane before treating the vertical resolver as broadly cleared.
4. Assemble Will's 76-account curated seed set with AccountId, website, booked ARR, MCV where available, and current Dust score/tier where available.
5. Produce a calibration report showing MCV error and potential-ARR shape by MCV bucket, ARR bucket, state, entity type, staff-count confidence, and website hygiene status.
6. Stage the next broader production shadow refresh only after the accepted-refresh results and parked-lane handling are reviewed.

## References

- Salesforce CLI overview: https://developer.salesforce.com/tools/salesforcecli
- Salesforce CLI plugin overview, including deploy/retrieve commands: https://developer.salesforce.com/docs/platform/salesforce-cli-plugin/guide/conceptual-overview.html
- Salesforce Bulk API 2.0 update/upsert CSV guidance: https://developer.salesforce.com/docs/marketing/marketing-cloud-growth/guide/mc-manage-objects-update-bulk.html
- Existing V2 pipeline spec: `artifacts/prospect_value_research/dust_pipeline_v2_spec_2026-05-04.md`
- Existing Top 200 generation plan: `artifacts/prospect_value_research/top_200_generation_plan_2026-05-04.md`
- Existing shadow test report: `artifacts/prospect_value_research/e2e_shadow_test_2026-05-04/v2_e2e_shadow_test_report_2026-05-04.md`
