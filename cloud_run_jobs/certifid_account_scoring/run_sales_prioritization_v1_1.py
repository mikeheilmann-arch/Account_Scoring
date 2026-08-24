"""Build the immutable, no-write Sales Prioritization V1.1 audit."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from certifid_account_scoring.pipeline.sales_prioritization_v1_1 import build_v1_1_population


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--accounts", type=Path, required=True)
    parser.add_argument("--overlay", type=Path, required=True)
    parser.add_argument("--combined-scores", type=Path, required=True)
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--sellable", type=Path, required=True)
    parser.add_argument("--lane", type=Path, required=True)
    parser.add_argument("--customer-history", type=Path, required=True)
    parser.add_argument("--retained-audit", type=Path, required=True)
    parser.add_argument("--retained-payload", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    result = build_v1_1_population(
        accounts_path=args.accounts,
        overlay_path=args.overlay,
        combined_scores_path=args.combined_scores,
        binding_path=args.binding,
        sellable_path=args.sellable,
        lane_path=args.lane,
        customer_history_path=args.customer_history,
        retained_audit_path=args.retained_audit,
        retained_payload_path=args.retained_payload,
        output_dir=args.output_dir,
        run_id=args.run_id,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
