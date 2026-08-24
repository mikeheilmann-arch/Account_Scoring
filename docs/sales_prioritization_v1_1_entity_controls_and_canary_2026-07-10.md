# Sales Prioritization V1.1 — Entity Controls and Production Canary

Status: **CANARY PASS**

Run ID: `sales_prioritization_v1_1_entity_guardrailed_20260710T220000Z`

## Coverage-neutral controls

- Scored population preserved: **16,924**
- Foreign-domain conflicts: **23** rows received conservative CRM/entity fallback provenance and Low confidence.
- High-value shared-domain review: **13** rows across **4** domains.
- Shared-domain numeric demotions: **11**.
- Resolved survivors:
  - `agltg.com`: Aegis Land Title Group (`0014x00001RsyOqAAJ`)
  - `rattikintitle.com`: Rattikin Title Co. (`0014x00001RsyTxAAJ`)
- Unresolved domains demoted across all high-value members:
  - `homelandtitlesettlement.com`
  - `westerntitle.com`

Known-control results:

- KDD Conveyancing: **750 → 50 MCV**
- Beaumont Legal: **125 → 40 MCV**
- Rattikin child: **300 → 40 MCV**
- Homeland Title and Homeland Title Settlement Agency: neither remains at 200+ MCV
- Top-20 foreign-domain conflicts: **0**
- Repeated unresolved domains in top 20: **0**
- Shared domains retaining multiple 200+ values: **0**

## Production canary

- Target: Salesforce production org alias `CertifID`
- Object: `Account`
- Submitted: **50** rows — exact top 20 plus 30 stratified
- Bulk API job: `750TP00000mal9hYAA`
- Verified successful: **50**
- Failed: **0**
- Exact readback mismatches: **0**
- Website writes: **0**
- Field clears: **0**
- Remaining population written: **0**
- Success-ID-scoped rollback rows: **50**
- Artifact manifest hashes: **verified**
- Automated tests: **102 passed**

The rollback payload is authorized only after re-querying the exact success IDs and revalidating both `AI_Prospect_Value_Run_Id__c` and `SystemModstamp` compare-and-swap guards.
