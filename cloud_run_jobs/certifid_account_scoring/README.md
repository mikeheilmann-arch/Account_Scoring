# CertifID Account Scoring Cloud Run Job

Isolated Cloud Run extraction/scoring package plus approval-gated Salesforce
publication tooling. The `batch_job` runtime is no-write with respect to
Salesforce; publisher entry points are separate and require explicit review.

## Canonical implementation source

This directory is the sole implementation source for both the frozen legacy
extractor/scorer and the versioned Account Scoring V1 shadow pipeline. Local
files under `scripts/` are compatibility imports only; scoring or resolution
logic must not be added there.

V1 reviewable stages live under `certifid_account_scoring/pipeline/`:

- immutable source snapshot and cached-evidence adapters;
- website binding and sellable-unit resolution;
- explicit ICP lanes and entity-safe features;
- lane-specific MCV and comparable ARR calibration;
- independent evaluation; and
- accepted-only/no-clear Salesforce publication planning.

The local no-write shadow entry point is `run_v1_shadow.py`. It reads the
existing July artifacts and a caller-supplied local cache/fixture/Account
describe; it never calls Nimble, executes a Cloud job, writes Salesforce, or
mutates a database.

Sales Prioritization V1.1 was published to Salesforce on July 10, 2026 using
the staged canary/readback/rollback path in this package. Historical success
does not authorize another release: every run must create fresh inputs,
conflict checks, exact backup, canary, readback, and guarded rollback artifacts.

This folder is intentionally outside `C:\Users\Mikeh\Python\Fuzzy_Matcher` and
does not use FoundryOps Cloud Run services, FoundryOps workers, or
`gs://fmatch-data-artifacts`.

## Isolation Boundary

- GCP project: `certifid-scoring-20260619`
- Region: `us-central1`
- Bucket: `gs://certifid-scoring-artifacts-1095330376491`
- Runtime service account: `certifid-scoring-runtime@certifid-scoring-20260619.iam.gserviceaccount.com`
- Secret: `nimbleway-api-key`
- Job runtime secret env var: `NIMBLEWAY_API_KEY`

The runtime service account should only have access to the dedicated bucket and
the `nimbleway-api-key` secret. Deployment permissions belong to a separate
deployer identity.

## Runtime Contract

Required environment variables:

- `INPUT_URI`: `gs://` URI for the input CSV.
- `OUTPUT_PREFIX`: `gs://` prefix for run artifacts.
- `NIMBLEWAY_API_KEY`: injected from Secret Manager.

Optional environment variables:

- `RUN_ID`: explicit run folder name. Defaults to UTC timestamp.
- `MAX_ROWS`: total rows to keep before sharding. Use for smoke tests.
- `SCORING_WORKERS`: per-task scorer worker count. Default `1`.
- `SCORING_MAX_PAGES`: pages extracted per account. Default `4`.
- `SCORING_MAP_LIMIT`: Nimble map link limit. Default `30`.
- `SCORING_MAP_TIMEOUT`: per-map timeout seconds. Default `45`.
- `SCORING_EXTRACT_TIMEOUT`: per-extract timeout seconds. Default `60`.
- `SCORING_RESUME`: `true` by default.
- `NIMBLE_MAX_RETRIES`: SDK retry count. Default `1`.

Cloud Run supplies `CLOUD_RUN_TASK_INDEX`, `CLOUD_RUN_TASK_COUNT`, and
`CLOUD_RUN_TASK_ATTEMPT`. Rows are assigned by input order modulo task count.

## Outputs

Each task writes:

- `task-00000_scores.csv`
- `task-00000_scores_summary.json`
- `task-00000_manifest.json`
- `raw/` cached Nimble map/extract artifacts

Logs include row counts, task indexes, status codes, review actions, confidence,
and Account ID suffixes only. They intentionally do not print raw client rows,
full Account names, websites, or secret values. Raw artifact directories use
deterministic hashes instead of Account-name slugs.

## Historical Approval-Gated Deploy Sketch

The commands below document the original isolated GCP shape. Do not run them
without confirming the current CertifID hosting decision and receiving explicit
deployment approval.

```powershell
gcloud config set project certifid-scoring-20260619

gcloud artifacts repositories create certifid-scoring `
  --repository-format=docker `
  --location=us-central1 `
  --description="CertifID account scoring batch images"

gcloud builds submit . `
  --tag us-central1-docker.pkg.dev/certifid-scoring-20260619/certifid-scoring/account-scoring:20260619

gcloud run jobs create certifid-account-scoring `
  --region=us-central1 `
  --image=us-central1-docker.pkg.dev/certifid-scoring-20260619/certifid-scoring/account-scoring:20260619 `
  --service-account=certifid-scoring-runtime@certifid-scoring-20260619.iam.gserviceaccount.com `
  --tasks=1 `
  --parallelism=1 `
  --max-retries=0 `
  --task-timeout=3600 `
  --set-env-vars=OUTPUT_PREFIX=gs://certifid-scoring-artifacts-1095330376491/certifid-account-scoring,MAX_ROWS=25,SCORING_WORKERS=1,NIMBLE_MAX_RETRIES=0 `
  --set-secrets=NIMBLEWAY_API_KEY=nimbleway-api-key:latest

gcloud run jobs execute certifid-account-scoring `
  --region=us-central1 `
  --wait `
  --update-env-vars=INPUT_URI=gs://certifid-scoring-artifacts-1095330376491/inputs/smoke_25.csv,RUN_ID=smoke-20260619,MAX_ROWS=25
```

For the first smoke run, upload a 25-row input CSV to the dedicated bucket,
update the job with that `INPUT_URI`, and execute with `--wait`. Do not increase
task count, parallelism, or row count until the smoke output is reviewed.
