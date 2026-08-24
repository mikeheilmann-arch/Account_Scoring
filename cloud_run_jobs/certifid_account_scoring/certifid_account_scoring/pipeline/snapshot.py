"""Immutable, validated input snapshot for the V1 shadow pipeline.

This module intentionally performs no network access.  It reads the July CRM,
quality-gate, and ALTA artifacts and fails closed when a required source is
missing, duplicated, or inconsistent with the 21,993-Account universe.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from .config import SOURCE_VERSION


EXPECTED_ACCOUNT_COUNT = 21_993
EXPECTED_EXTRACTED_COUNT = 15_384

SOURCE_PATHS = {
    "accounts": Path("tmp/certifid_scoring_gcp/crm_full_20260708/account_context_sfdc_prod_2026-07-08.csv"),
    "cached_scores": Path(
        "tmp/icp_quality_agent_crm_full_20260708/full_run_audit/"
        "crm_full_quality_gated_scores_combined_2026-07-10.csv"
    ),
    "quality_overlay": Path(
        "tmp/icp_quality_agent_crm_full_20260708/icp_quality_agent_crm_full_overlay.csv"
    ),
    "quality_summary": Path(
        "tmp/icp_quality_agent_crm_full_20260708/icp_quality_agent_crm_full_summary.json"
    ),
    "alta_matches": Path("artifacts/alta_enrichment/alta_sfdc_matches.csv"),
    "alta_enrichment": Path("artifacts/alta_enrichment/sfdc_alta_enrichment.csv"),
}

ID_COLUMNS = {
    "accounts": "Id",
    "cached_scores": "Id",
    "quality_overlay": "AccountId",
    "alta_matches": "Account_Id",
    "alta_enrichment": "Account_Id",
}


class SnapshotValidationError(RuntimeError):
    """Raised when an input snapshot is unsafe to score."""


@dataclass(frozen=True)
class SourceFile:
    role: str
    relative_path: str
    sha256: str
    size_bytes: int
    modified_at_utc: str
    row_count: int | None
    id_column: str
    source_version: str = SOURCE_VERSION


@dataclass(frozen=True)
class SourceSnapshot:
    repository_root: str
    sources: tuple[SourceFile, ...]
    accounts: Mapping[str, Mapping[str, str]]
    cached_scores: Mapping[str, Mapping[str, str]]
    quality_overlay: Mapping[str, Mapping[str, str]]
    alta_matches: Mapping[str, Mapping[str, str]]
    alta_enrichment: Mapping[str, Mapping[str, str]]
    quality_summary: Mapping[str, object]
    snapshot_hash: str
    loaded_at_utc: str
    source_version: str = SOURCE_VERSION

    def manifest_dict(self) -> dict[str, object]:
        """Return provenance without embedding source rows."""

        return {
            "repository_root": self.repository_root,
            "source_version": self.source_version,
            "snapshot_hash": self.snapshot_hash,
            "loaded_at_utc": self.loaded_at_utc,
            "sources": [asdict(source) for source in self.sources],
            "counts": {
                "accounts": len(self.accounts),
                "cached_scores": len(self.cached_scores),
                "quality_overlay": len(self.quality_overlay),
                "alta_matches": len(self.alta_matches),
                "alta_enrichment": len(self.alta_enrichment),
            },
        }


def _sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def row_fingerprint(row: Mapping[str, object], fields: Iterable[str] | None = None) -> str:
    """Stable SHA-256 for a source row or selected source columns."""

    keys = sorted(fields if fields is not None else row.keys())
    normalized = {key: "" if row.get(key) is None else str(row.get(key)).strip() for key in keys}
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _read_unique_csv(path: Path, id_column: str, role: str) -> tuple[dict[str, dict[str, str]], int]:
    rows: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or id_column not in reader.fieldnames:
            raise SnapshotValidationError(f"{role} is missing required ID column {id_column!r}")
        for line_number, raw in enumerate(reader, start=2):
            account_id = (raw.get(id_column) or "").strip()
            if not account_id:
                raise SnapshotValidationError(f"{role} has a blank ID at CSV line {line_number}")
            if account_id in rows:
                raise SnapshotValidationError(f"{role} has duplicate ID {account_id}")
            rows[account_id] = {key: (value or "").strip() for key, value in raw.items()}
    return rows, len(rows)


def load_snapshot(repository_root: str | Path, *, enforce_expected_counts: bool = True) -> SourceSnapshot:
    """Load and reconcile all required local July artifacts.

    Missing cached score rows are expected only for preflight-held Accounts;
    they are not treated as blank evidence and must remain held/no-change in the
    orchestrator.
    """

    root = Path(repository_root).resolve()
    resolved = {role: root / relative for role, relative in SOURCE_PATHS.items()}
    missing = [f"{role}: {path}" for role, path in resolved.items() if not path.is_file()]
    if missing:
        raise SnapshotValidationError("Required source files are missing: " + "; ".join(missing))

    tables: dict[str, dict[str, dict[str, str]]] = {}
    sources: list[SourceFile] = []
    for role, id_column in ID_COLUMNS.items():
        path = resolved[role]
        table, row_count = _read_unique_csv(path, id_column, role)
        tables[role] = table
        stat = path.stat()
        sources.append(
            SourceFile(
                role=role,
                relative_path=SOURCE_PATHS[role].as_posix(),
                sha256=_sha256(path),
                size_bytes=stat.st_size,
                modified_at_utc=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                row_count=row_count,
                id_column=id_column,
            )
        )

    summary_path = resolved["quality_summary"]
    with summary_path.open("r", encoding="utf-8-sig") as handle:
        quality_summary = json.load(handle)
    stat = summary_path.stat()
    sources.append(
        SourceFile(
            role="quality_summary",
            relative_path=SOURCE_PATHS["quality_summary"].as_posix(),
            sha256=_sha256(summary_path),
            size_bytes=stat.st_size,
            modified_at_utc=datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            row_count=None,
            id_column="",
        )
    )

    account_ids = set(tables["accounts"])
    overlay_ids = set(tables["quality_overlay"])
    cached_ids = set(tables["cached_scores"])
    if account_ids != overlay_ids:
        raise SnapshotValidationError(
            "Account and quality-overlay universes differ: "
            f"missing_overlay={len(account_ids - overlay_ids)}, unexpected_overlay={len(overlay_ids - account_ids)}"
        )
    if not cached_ids.issubset(account_ids):
        raise SnapshotValidationError(
            f"Cached scores contain {len(cached_ids - account_ids)} IDs outside the Account universe"
        )
    summary_count = int(quality_summary.get("counts", {}).get("accounts", -1))  # type: ignore[union-attr]
    if summary_count != len(account_ids):
        raise SnapshotValidationError(
            f"Quality summary count {summary_count} does not match Account count {len(account_ids)}"
        )
    if enforce_expected_counts and len(account_ids) != EXPECTED_ACCOUNT_COUNT:
        raise SnapshotValidationError(
            f"Expected {EXPECTED_ACCOUNT_COUNT} Accounts, found {len(account_ids)}"
        )
    if enforce_expected_counts and len(cached_ids) != EXPECTED_EXTRACTED_COUNT:
        raise SnapshotValidationError(
            f"Expected {EXPECTED_EXTRACTED_COUNT} cached extracted rows, found {len(cached_ids)}"
        )

    ordered_sources = tuple(sorted(sources, key=lambda source: source.role))
    snapshot_hash = hashlib.sha256(
        json.dumps(
            [(source.role, source.sha256, source.row_count) for source in ordered_sources],
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return SourceSnapshot(
        repository_root=str(root),
        sources=ordered_sources,
        accounts=tables["accounts"],
        cached_scores=tables["cached_scores"],
        quality_overlay=tables["quality_overlay"],
        alta_matches=tables["alta_matches"],
        alta_enrichment=tables["alta_enrichment"],
        quality_summary=quality_summary,
        snapshot_hash=snapshot_hash,
        loaded_at_utc=datetime.now(timezone.utc).isoformat(),
    )
