"""Adapters for the immutable July GCS page cache materialized locally."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from .evidence_features import CachedEvidencePage


_PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[ .()-]*)?(\d{3})[ .()-]+(\d{3})[ .-]+(\d{4})(?!\d)")
_LEGAL_SUFFIX_RE = re.compile(
    r"\b(?:llc|l\.l\.c\.?|incorporated|inc\.?|corp(?:oration)?\.?|company|co\.?|"
    r"limited|ltd\.?|pllc|p\.a\.?|pc|l\.p\.?|llp)\b",
    re.I,
)


@dataclass(frozen=True)
class CachedAccountEvidence:
    account_id: str
    raw_reference: str
    local_directory: str
    pages: tuple[CachedEvidencePage, ...]
    structured_summary: Mapping[str, object]
    evidence_hash: str
    missing_reason: str


def _artifact_parts(raw_reference: object) -> tuple[str, str] | None:
    normalized = str(raw_reference or "").replace("\\", "/").rstrip("/")
    match = re.search(r"/(task-[^/]+)/raw/(account_[^/]+)$", normalized)
    return (match.group(1), match.group(2)) if match else None


def _read_json(path: Path) -> Mapping[str, object]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    return value if isinstance(value, Mapping) else {}


def load_cached_account_evidence(
    account_id: str,
    cached_score_row: Mapping[str, object] | None,
    cache_root: str | Path,
) -> CachedAccountEvidence:
    """Read an Account's already-extracted pages; never crawl or fetch."""

    if cached_score_row is None:
        return CachedAccountEvidence(account_id, "", "", (), {}, "", "preflight_held_no_cached_extraction")
    raw_reference = str(cached_score_row.get("RawArtifactDir") or "")
    parts = _artifact_parts(raw_reference)
    if parts is None:
        return CachedAccountEvidence(
            account_id, raw_reference, "", (), {}, "", "cached_raw_reference_unparseable"
        )
    task, account_directory = parts
    local_directory = Path(cache_root) / "tasks" / task / "raw" / account_directory
    if not local_directory.is_dir():
        return CachedAccountEvidence(
            account_id,
            raw_reference,
            str(local_directory),
            (),
            {},
            "",
            "cached_raw_directory_not_materialized",
        )

    summary_path = local_directory / "structured_summary.json"
    summary = _read_json(summary_path) if summary_path.is_file() else {}
    summary_id = str(summary.get("id") or "")
    if summary_id and summary_id != account_id:
        return CachedAccountEvidence(
            account_id,
            raw_reference,
            str(local_directory),
            (),
            summary,
            "",
            f"cached_summary_id_mismatch:{summary_id}",
        )

    pages: list[CachedEvidencePage] = []
    digest = hashlib.sha256()
    for page_path in sorted(local_directory.glob("page_*.json")):
        raw = page_path.read_bytes()
        digest.update(page_path.name.encode("utf-8"))
        digest.update(raw)
        try:
            page = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(page, Mapping) or str(page.get("status", "")).lower() != "success":
            continue
        data = page.get("data")
        markdown = str(data.get("markdown") or "") if isinstance(data, Mapping) else ""
        if not markdown.strip():
            continue
        metadata = page.get("metadata")
        observed_at = str(metadata.get("query_time") or "") if isinstance(metadata, Mapping) else ""
        pages.append(
            CachedEvidencePage(
                url=str(page.get("url") or ""),
                title=page_path.stem.replace("page_", "").replace("_", " "),
                text=markdown,
                observed_at=observed_at,
                evidence_hash=hashlib.sha256(raw).hexdigest(),
            )
        )
    if summary_path.is_file():
        raw = summary_path.read_bytes()
        digest.update(summary_path.name.encode("utf-8"))
        digest.update(raw)
    missing_reason = "" if pages else "cached_pages_absent_or_unusable"
    return CachedAccountEvidence(
        account_id=account_id,
        raw_reference=raw_reference,
        local_directory=str(local_directory),
        pages=tuple(pages),
        structured_summary=summary,
        evidence_hash=digest.hexdigest(),
        missing_reason=missing_reason,
    )


def _organization_assertion(account_name: str, text: str) -> str:
    """Return a site-asserted Account name only when distinctive text says it."""

    normalized = " ".join(_LEGAL_SUFFIX_RE.sub(" ", account_name).split()).strip(" ,.-")
    if len(normalized) < 6 or len(re.findall(r"[A-Za-z0-9]+", normalized)) < 2:
        return ""
    if re.search(rf"(?<![A-Za-z0-9]){re.escape(normalized)}(?![A-Za-z0-9])", text, re.I):
        return account_name
    return ""


def build_resolver_evidence(
    account_row: Mapping[str, object],
    overlay_row: Mapping[str, object],
    alta_row: Mapping[str, object] | None,
    cached: CachedAccountEvidence,
) -> dict[str, object]:
    """Map source-backed cached text/summary fields into resolver inputs."""

    merged: dict[str, object] = {**account_row, **overlay_row}
    if alta_row:
        # The ALTA match export contains copied CRM columns from its own source
        # date.  Never let those overwrite the canonical July Account snapshot.
        for field in (
            "ALTA_ID",
            "ALTA_Company_Name",
            "ALTA_City",
            "ALTA_State",
            "Match_Confidence",
            "Match_Method",
            "Match_Score",
            "Candidate_Count",
            "Candidate_ALTA_IDs",
            "Needs_Human_Review",
        ):
            if field in alta_row:
                merged[field] = alta_row[field]
    page_text = "\n".join(page.text for page in cached.pages)
    summary = cached.structured_summary
    offices = summary.get("office_evidence")
    if not isinstance(offices, list):
        offices = []
    phones = sorted({"".join(match.groups()) for match in _PHONE_RE.finditer(page_text)})
    name = str(account_row.get("Name") or overlay_row.get("AccountName") or "")
    merged.update(
        {
            "CachedPageText": page_text,
            "SiteOrganizationName": _organization_assertion(name, page_text),
            "SiteAddresses": offices,
            "SitePhones": phones,
            "SourceObservedAt": max((page.observed_at for page in cached.pages), default=""),
            "CachedEvidenceHash": cached.evidence_hash,
        }
    )
    return merged


__all__ = [
    "CachedAccountEvidence",
    "build_resolver_evidence",
    "load_cached_account_evidence",
]
