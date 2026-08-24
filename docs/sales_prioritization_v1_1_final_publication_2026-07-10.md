# Sales Prioritization V1.1 — Final Production Publication

Status: **PASS — production publication and report verified**

Final run ID: `sales_prioritization_v1_1_entity_guardrailed_20260710T220000Z`

## Data publication

- Staged changed population: **16,865**
- Successful canary IDs excluded from the remaining payload: **50**
- Exact remaining candidates re-queried: **16,815**
- Newly quarantined after staging: **43**
- Final remaining rows submitted: **16,772**
- Remaining Bulk API job: `750TP00000mbMSYYA2`
- Remaining successful rows: **16,772**
- Remaining failed rows: **0**
- Missing readback IDs: **0**
- Unexpected readback IDs: **0**
- Normalized value mismatches: **0**
- Account Website writes: **0**
- Field-clear transitions: **0**
- Remaining success-scoped rollback rows: **16,772**
- Final aggregate live count: **16,822**
- Expected live count: **16,865 − 43 = 16,822**

The write and exact readback completed before the first aggregate parser accepted Salesforce's `COUNT()` response shape. Salesforce returned the count in `result.totalSize`; the parser was corrected and only the aggregate query was rerun. No Account update was resubmitted.

## Salesforce report

- Name: **Sales Prioritization V1.1 — Current Run**
- Folder: **Public Reports**
- Report ID: `00OTP00000Fkn582AB`
- Metadata: `unfiled$public/Sales_Prioritization_V1_1_Current`
- Dry-run deployment: `0AfTP000004CnVh0AK`
- Production deployment: `0AfTP000004CnXJ0A0`
- Filter count: **1**
- Locked filter: `AI_Prospect_Value_Run_Id__c = sales_prioritization_v1_1_entity_guardrailed_20260710T220000Z`
- Verified report row count: **16,822**
- URL: https://certifid2022.my.salesforce.com/lightning/r/Report/00OTP00000Fkn582AB/view

## Rollback boundary

The remaining rollback payload contains only the 16,772 successful remaining-write IDs. The original canary package separately contains the 50 successful canary rollback rows. Before executing either rollback, re-query the exact IDs and require both the final Run ID and recorded post-write `SystemModstamp` to match.
