"""Create the read-only Salesforce staging package for V1.1."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from certifid_account_scoring.pipeline.salesforce_v1_1_staging import stage_v1_1_release


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-payload", type=Path, required=True)
    parser.add_argument("--scored-audit", type=Path, required=True)
    parser.add_argument("--population-summary", type=Path, required=True)
    parser.add_argument("--accounts-snapshot", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target-org", required=True)
    args = parser.parse_args()
    result = stage_v1_1_release(
        candidate_payload_path=args.candidate_payload,
        scored_audit_path=args.scored_audit,
        population_summary_path=args.population_summary,
        accounts_snapshot_path=args.accounts_snapshot,
        output_dir=args.output_dir,
        org=args.target_org,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
