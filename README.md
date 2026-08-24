# CertifID Account Scoring

Source-controlled implementation of CertifID's Account value scoring and sales-prioritization system.

The system estimates prospect closing capacity and directional pipeline-potential ARR, applies entity/ICP/data-quality controls, and publishes approved outputs to dedicated Salesforce Account fields. It is a prioritization system, not a forecast, booked-ARR model, or source of buyer intent.

## Repository status

- Production baseline: Sales Prioritization V1.1, published July 10, 2026.
- Salesforce publication at that baseline: 16,822 Accounts in the final current-run report.
- Canonical runtime source: `cloud_run_jobs/certifid_account_scoring/`.
- Canonical Salesforce metadata subset: `salesforce/`.
- Supporting quality, registry, calibration, and audit utilities: `tools/`.
- Customer data, web evidence, ALTA data, Salesforce exports, credentials, and generated run artifacts are intentionally excluded.

This repository was initially created under a personal GitHub account as a staging location. Transfer it to a CertifID-owned organization before treating it as the durable company system of record.

## Decision flow

1. Build an immutable Salesforce Account snapshot.
2. Retrieve or reuse cached public website evidence.
3. Bind the website to the correct entity and resolve the sellable buying unit.
4. Classify the Account into the title/escrow, legal, adjacent, or non-ICP lane.
5. Estimate monthly closing volume (MCV) using trusted CRM anchors or bounded public signals.
6. Translate MCV into directional pipeline-potential ARR.
7. Apply confidence, lifecycle, hierarchy, duplicate, and top-rank guardrails.
8. Stage changed-ID-only Salesforce payloads with exact backup, readback, and guarded rollback artifacts.

The Nimble extraction runtime and Salesforce publisher are deliberately separate. The Cloud Run batch job does not hold Salesforce write credentials. Publication requires an explicit reviewed release step.

## Field semantics

- `AI_Prospect_Value_MCV_Point__c`: point estimate of monthly closing capacity.
- `AI_Prospect_Value_MCV_Low__c` / `High__c`: uncertainty range.
- `AI_Prospect_Value_ARR_Point__c`: directional pipeline-potential ARR, not booked or forecast ARR.
- `AI_Prospect_Value_Confidence__c`: evidence confidence, not lead intent.
- `AI_Prospect_Value_Action__c`: routing disposition such as `score_now`.
- `AI_Prospect_Value_ICP__c`: ICP disposition.
- `AI_Prospect_Value_Run_Id__c` and model/source fields: publication provenance.

See [the model explainer](docs/account_scoring_model_explainer_external_2026-06-10.md) and [production baseline](docs/PRODUCTION_BASELINE.md) for the complete interpretation.

## Layout

```text
cloud_run_jobs/certifid_account_scoring/
  certifid_account_scoring/scoring/   Frozen extraction and scoring logic
  certifid_account_scoring/pipeline/  Entity, lane, calibration, QA, and publication stages
  tests/                              Deterministic unit and control tests
  run_*.py                            Reviewed command-line entry points
salesforce/                           Scoring fields, report, list view, and admin permission metadata
tools/icp_quality_agent/              Pre/post-retrieval quality overlays
tools/vertical_resolution/            Website and Virginia registry validation utilities
tools/alta/                           ALTA matching code; licensed source data is excluded
tools/analysis/                       Backtests, audits, and customer-history analysis
docs/                                 Architecture, operating history, and model semantics
```

## Local setup

Python 3.11 is the supported runtime.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
pytest -m "not private_artifacts"
```

Tests marked `private_artifacts` reconcile the historical full-universe snapshot and live Salesforce describe captured outside Git. Run them only when those approved private artifacts are available.

## Cloud Run batch

Build from the isolated job directory:

```powershell
cd cloud_run_jobs/certifid_account_scoring
docker build -t certifid-account-scoring:local .
```

Runtime secrets must be injected from Secret Manager. Never place a Nimble key, Salesforce credential, warehouse connection, or licensed directory export in this repository.

The batch runtime writes only to its configured artifact store. Salesforce publication is performed by separate approval-gated commands and must follow the backup, conflict-check, canary, readback, and rollback sequence documented in [the production baseline](docs/PRODUCTION_BASELINE.md).

## Salesforce metadata

The `salesforce/` package includes only the scoring-specific fields and surfaces needed to reproduce the feature. Broad Account layouts and Profiles were intentionally excluded because deploying partial copies could overwrite unrelated production configuration.

Always validate and rehearse Salesforce metadata in the CertifID sandbox before production. Do not deploy directly from this repository without an approved change plan.

## Data and security rules

- No `.env` files, tokens, passwords, cookies, or connection strings.
- No Salesforce exports or customer/account-level production data.
- No raw or cached website pages.
- No ALTA or other licensed directory data.
- No generated scoring payloads, backups, success ledgers, or rollback files.
- Test fixtures must be synthetic or explicitly sanitized.
- Never write `Account.Website` from the scoring publisher.

See [SECURITY.md](SECURITY.md) and [CONTRIBUTING.md](CONTRIBUTING.md).
