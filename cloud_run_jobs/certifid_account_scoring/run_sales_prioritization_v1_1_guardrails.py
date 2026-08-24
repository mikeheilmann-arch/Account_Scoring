"""Apply final top-rank guardrails to the approved V1.1 population."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from certifid_account_scoring.pipeline.sales_prioritization_v1_1_guardrails import (
    apply_v1_1_top_rank_guardrails,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scored-audit", type=Path, required=True)
    parser.add_argument("--full-decisions", type=Path, required=True)
    parser.add_argument("--lane", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--accounts", type=Path, required=True)
    parser.add_argument("--opportunity-labels", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    result = apply_v1_1_top_rank_guardrails(
        scored_audit_path=args.scored_audit,
        full_decisions_path=args.full_decisions,
        lane_path=args.lane,
        evidence_path=args.evidence,
        accounts_path=args.accounts,
        opportunity_labels_path=args.opportunity_labels,
        output_dir=args.output_dir,
        run_id=args.run_id,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
