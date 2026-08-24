from __future__ import annotations

import argparse
import json
from pathlib import Path

from certifid_account_scoring.pipeline.salesforce_v1_1_remaining_publish import (
    finalize_completed_remaining_publication,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--publication-dir", type=Path, required=True)
    parser.add_argument("--target-org", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    result = finalize_completed_remaining_publication(
        publication_dir=args.publication_dir,
        org=args.target_org,
        run_id=args.run_id,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
