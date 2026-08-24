from __future__ import annotations

import argparse
import json
from pathlib import Path

from certifid_account_scoring.pipeline.salesforce_v1_1_remaining_publish import publish_remaining_v1_1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--staging-dir", type=Path, required=True)
    parser.add_argument("--canary-dir", type=Path, required=True)
    parser.add_argument("--accounts-snapshot", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--target-org", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    result = publish_remaining_v1_1(
        staging_dir=args.staging_dir,
        canary_dir=args.canary_dir,
        accounts_snapshot_path=args.accounts_snapshot,
        output_dir=args.output_dir,
        org=args.target_org,
        execute=args.execute,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
