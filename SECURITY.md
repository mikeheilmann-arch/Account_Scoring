# Security

This repository must contain source and synthetic fixtures only.

Do not commit:

- API keys, OAuth tokens, cookies, passwords, or database connection strings;
- Salesforce exports, Account data, success/failure ledgers, backups, or rollback payloads;
- raw or cached public website content;
- ALTA or other licensed directory exports; or
- GCS artifacts from scoring runs.

Use GCP Secret Manager for runtime secrets and organization-approved identity mechanisms for Salesforce and warehouse access.

If a secret or restricted dataset is committed, stop using it, notify the repository owner and CertifID Security, rotate the credential where applicable, and remove the material from Git history. Deleting the latest file alone is insufficient.

Salesforce publication code is production-sensitive. Any change to its fields, backup behavior, conflict checks, canary selection, readback verification, or rollback guards requires explicit review and a sandbox rehearsal.
