"""Execute the guarded Salesforce release for Sales-prioritization V1."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from certifid_account_scoring.pipeline.salesforce_live_release import publish_live_release


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--accepted-payload", type=Path, required=True)
    parser.add_argument("--accepted-audit", type=Path, required=True)
    parser.add_argument("--accounts-snapshot", type=Path, required=True)
    parser.add_argument("--live-dir", type=Path, required=True)
    parser.add_argument("--target-org", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    result = publish_live_release(
        accepted_payload_path=args.accepted_payload,
        accepted_audit_path=args.accepted_audit,
        accounts_snapshot_path=args.accounts_snapshot,
        live_dir=args.live_dir,
        org=args.target_org,
        execute=args.execute,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
