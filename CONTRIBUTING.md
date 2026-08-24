# Contributing

1. Create a focused branch from `main`.
2. Keep scoring, entity resolution, publication, and Salesforce metadata changes separately reviewable when practical.
3. Add or update deterministic tests for behavioral changes.
4. Run `pytest -m "not private_artifacts"` before opening a PR.
5. Run private-artifact reconciliation tests when changing snapshot or publication contracts.
6. Do not deploy GCP or Salesforce changes from a feature branch without a reviewed release plan and explicit approval.

Model semantics are business contracts. Changes to MCV source hierarchy, ICP routes, confidence, ARR meaning, lifecycle treatment, hierarchy resolution, or publication behavior must update the relevant documentation and model/source version.

Generated payloads and run artifacts belong in approved private storage, not Git.
