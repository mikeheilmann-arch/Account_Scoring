# Salesforce metadata

This directory contains the scoring-specific subset of Salesforce metadata:

- 18 current `AI_Prospect_Value_*` Account fields;
- three reserved fit/timing/data-confidence fields that the current publisher
  explicitly leaves blank;
- five legacy Dust comparison fields retained for side-by-side audit history;
- the `Account Scoring` personal list view;
- the historical V1.1 current-run report; and
- the original scorer admin permission set.

The report contains the July 2026 run ID and is retained as a reproducibility baseline. A future run must stage a new report filter through a reviewed metadata change.

Broad Profiles and Account Layouts are intentionally excluded because they contain unrelated production configuration and are unsafe to deploy as partial snapshots. Live field-level access and layout placement must be reviewed separately.

Always pass an explicit Salesforce target org. Validate and rehearse against `CertifID-Sandbox` before production. Merging metadata into this repository does not authorize deployment.
