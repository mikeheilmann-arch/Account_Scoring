#!/usr/bin/env python3
"""Run the no-write production-critical V1 shadow over cached July evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from certifid_account_scoring.pipeline.orchestrator import run_shadow


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--describe", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--sellable-fixture", type=Path, required=True)
    parser.add_argument("--lane-feature-fixture", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    manifest = run_shadow(
        args.repository_root,
        args.output_dir,
        cache_root=args.cache_root,
        describe_path=args.describe,
        fixture_path=args.fixture,
        sellable_fixture_path=args.sellable_fixture,
        lane_feature_fixture_path=args.lane_feature_fixture,
        workers=args.workers,
    )
    print(json.dumps({"run_id": manifest["run_id"], "canary_ready": manifest["canary_ready"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
