# Sales Prioritization V1.1 — Final Top-Rank Guardrail Review

Status: **NO WRITE — revised top 20 and canary staged for review**

Run ID: `sales_prioritization_v1_1_guardrailed_20260710T233000Z`

## Outcome

- Approved scored population preserved: **16,924**
- Accounts suppressed by this guardrail: **0**
- Targeted rank/value candidates: **56**
- Demoted: **44**
  - 34 unsupported 200+ `unclear_broad_practice` / `non_closing_legal` retained estimates were capped at **75 MCV**.
  - 10 uncorroborated Final-MCV anchors at the 750 rail with mismatch/insufficient binding were capped at **225 MCV**.
- Preserved with qualifying support: **12**
- Changed-ID-only staged population after live source-conflict quarantine: **16,865**
- Source-conflict IDs quarantined: **59**
- Salesforce writes: **0**

The legal guardrail accepts a recent positive-MCV Closed Won/Closed Lost Opportunity, a trusted rep anchor corroborated by ALTA or strong closing operations, or strong closing operational evidence. Strong operational evidence requires High evidence confidence, a closing-service signal, and at least two relevant staff; a service keyword alone does not qualify.

The Opportunity support check was refreshed live from Salesforce for the guarded candidates. It found a May 26 Closed Lost Opportunity for Summit Settlement Services after the older calibration extract; Summit's 750 rail was therefore preserved as required.

For non-Opportunity Final anchors at 750 with untrusted binding, title-lane corroboration requires a recent Opportunity, strong closing operations, or ALTA plus title/escrow identity evidence. Recent Opportunity anchors are preserved.

## Revised top 20 by directional ARR

ARR ties are ordered by MCV, then Account name and Salesforce ID. The three legal rows remaining in the top 20 are outside the guarded broad/non-closing subtypes: one has a meaningful real-estate-closing arm and two are closing-focused.

| Rank | Account | Lane | Source tier | Confidence | MCV | ARR |
|---:|---|---|---|---|---:|---:|
| 1 | First National Title Co. | title | retained | Low | 1,200 | $150,000 |
| 2 | Citywide Title Corp. | title | retained | High | 1,095 | $150,000 |
| 3 | Baird and Warner Title Services Inc. | title | trusted anchor | Medium | 750 | $150,000 |
| 4 | Closing USA, LLC | title | trusted anchor | Medium | 750 | $150,000 |
| 5 | Cottonwood Title Insurance Agency, Inc. | title | trusted anchor | Medium | 750 | $150,000 |
| 6 | Gimenez and Carrillo LLC | legal | retained | Medium | 750 | $150,000 |
| 7 | Gray and Associates | legal | retained | Medium | 750 | $150,000 |
| 8 | Heritage Title Co. | title | trusted anchor | Medium | 750 | $150,000 |
| 9 | Homeland Title | title | website | High | 750 | $150,000 |
| 10 | Homeland Title Settlement Agency | title | website | High | 750 | $150,000 |
| 11 | KDD Conveyancing | legal | retained | Medium | 750 | $150,000 |
| 12 | Land Title Guarantee Co. | title | trusted anchor | Medium | 750 | $150,000 |
| 13 | MGC Title Agency | title | retained | Medium | 750 | $150,000 |
| 14 | Metro National Title | title | trusted anchor | Medium | 750 | $150,000 |
| 15 | Metro National Title Associates | title | retained | Medium | 750 | $150,000 |
| 16 | Nations Holding Co. | title | trusted anchor | High | 750 | $150,000 |
| 17 | Near North Title Group | title | trusted anchor | Medium | 750 | $150,000 |
| 18 | PGP Title | title | trusted anchor | Medium | 750 | $150,000 |
| 19 | Priority Title Escrow LLC | title | trusted anchor | Medium | 750 | $150,000 |
| 20 | Rocket Close | title | trusted anchor | Medium | 750 | $150,000 |

## Canary and recovery package

The 50-row canary contains the exact top 20 above, followed by 30 rows selected across source tier, lane, confidence, and MCV band. It represents every source tier, both lanes, every confidence level, and every MCV band present in the changed-ID population.

The package includes:

- changed-ID-only payload;
- exact current-state backup;
- 50-row canary payload and selection-role ledger;
- canary exact-current backup;
- normalized exact-ID readback plan;
- all-changed rollback values marked `DO_NOT_EXECUTE`;
- success-ledger template and compare-and-swap rollback instructions;
- fresh Account describe and schema validation;
- exact-query log, artifact hashes, and source-conflict queue.

No Salesforce update job was created or submitted. Publication remains blocked pending review of this revised top-20 list and canary.
