"""Build the immutable Sales-prioritization V1 population."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from certifid_account_scoring.pipeline.sales_prioritization_release import build_release_population


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--combined-scores", type=Path, required=True)
    parser.add_argument("--accounts", type=Path, required=True)
    parser.add_argument("--r3-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    result = build_release_population(
        combined_scores_path=args.combined_scores,
        accounts_path=args.accounts,
        r3_dir=args.r3_dir,
        output_dir=args.output_dir,
        run_id=args.run_id,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
