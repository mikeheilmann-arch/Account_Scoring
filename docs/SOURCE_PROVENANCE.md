# Source provenance

This repository was assembled on August 24, 2026 from the working CertifID RevOps workspace after confirming that the scoring source was not present in either the `CertifID` or `CertifID-Internal` GitHub organizations.

## Included

- canonical Cloud Run Account scoring package;
- production V1/V1.1 staging, canary, publication, readback, and guarded rollback code;
- deterministic scorer, entity-resolution, lane, feature, calibration, and publication tests;
- ICP Quality Agent source;
- vertical registry, ALTA matching, calibration, and audit utilities;
- scoring-specific Salesforce field, report, list-view, and permission-set metadata; and
- curated architecture, semantics, validation, and publication documentation.

## Excluded intentionally

- `.env` and all credentials;
- production Salesforce exports and Account-level datasets;
- Nimble request/response caches and raw website content;
- ALTA member exports and other licensed data;
- GCS run artifacts;
- scoring payloads, exact backups, success ledgers, and rollback files;
- local CLI state; and
- unrelated Salesforce metadata, Profiles, and Account layouts.

The repository Git commit establishes source provenance going forward. Historical production image/source equivalence should be recorded separately if CertifID requires an attested match to a particular Artifact Registry image digest.
