"""Cloud Run Job entrypoint for no-write CertifID account scoring."""

from __future__ import annotations

import csv
import json
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from google.cloud import storage

from certifid_account_scoring.scoring import run_greenfield_nimble_test as scorer


WORK_ROOT = Path(os.getenv("WORK_ROOT") or Path(tempfile.gettempdir()) / "certifid-account-scoring")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log_event(event: str, **fields: object) -> None:
    payload = {"event": event, **fields}
    print(json.dumps(payload, sort_keys=True), flush=True)


def redact_configured_secret(text: str) -> str:
    redacted = str(text or "")
    for env_name in ("NIMBLEWAY_API_KEY", "NIMBLE_API_KEY"):
        value = os.getenv(env_name)
        if value and len(value) >= 8:
            redacted = redacted.replace(value, "[REDACTED]")
    return redacted


def parse_gs_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    if parsed.scheme != "gs" or not parsed.netloc:
        raise ValueError(f"Expected gs:// URI, got {uri!r}")
    return parsed.netloc, parsed.path.lstrip("/")


def join_uri(prefix: str, *parts: str) -> str:
    return "/".join([prefix.rstrip("/"), *(part.strip("/") for part in parts if part)])


def storage_client() -> storage.Client:
    return storage.Client()


def download_uri(uri: str, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if uri.startswith("gs://"):
        bucket_name, blob_name = parse_gs_uri(uri)
        storage_client().bucket(bucket_name).blob(blob_name).download_to_filename(destination)
        return
    shutil.copyfile(uri, destination)


def upload_file(source: Path, destination_uri: str) -> None:
    if destination_uri.startswith("gs://"):
        bucket_name, blob_name = parse_gs_uri(destination_uri)
        storage_client().bucket(bucket_name).blob(blob_name).upload_from_filename(source)
        return
    destination = Path(destination_uri)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def upload_tree(source_dir: Path, destination_prefix: str) -> int:
    count = 0
    if not source_dir.exists():
        return count
    for path in sorted(source_dir.rglob("*")):
        if path.is_file():
            relative = path.relative_to(source_dir).as_posix()
            upload_file(path, join_uri(destination_prefix, relative))
            count += 1
    return count


def read_input(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), list(reader)


def write_input(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def positive_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    value = int(raw)
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


def task_context() -> tuple[int, int, int]:
    task_index = int(os.getenv("CLOUD_RUN_TASK_INDEX", os.getenv("TASK_INDEX", "0")))
    task_count = int(os.getenv("CLOUD_RUN_TASK_COUNT", os.getenv("TASK_COUNT", "1")))
    task_attempt = int(os.getenv("CLOUD_RUN_TASK_ATTEMPT", os.getenv("TASK_ATTEMPT", "0")))
    if task_count < 1:
        raise ValueError("task_count must be at least 1")
    if task_index < 0 or task_index >= task_count:
        raise ValueError(f"task_index {task_index} is outside task_count {task_count}")
    return task_index, task_count, task_attempt


def run_scorer(input_path: Path, output_path: Path, raw_dir: Path) -> int:
    argv = [
        "run_greenfield_nimble_test.py",
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--raw-dir",
        str(raw_dir),
        "--workers",
        str(positive_int_env("SCORING_WORKERS", 1)),
        "--max-pages",
        str(positive_int_env("SCORING_MAX_PAGES", 4)),
        "--map-limit",
        str(positive_int_env("SCORING_MAP_LIMIT", 30)),
        "--map-timeout",
        str(positive_int_env("SCORING_MAP_TIMEOUT", 45)),
        "--extract-timeout",
        str(positive_int_env("SCORING_EXTRACT_TIMEOUT", 60)),
    ]
    if os.getenv("SCORING_RESUME", "true").strip().lower() in {"0", "false", "no"}:
        argv.append("--no-resume")

    previous_argv = sys.argv
    sys.argv = argv
    try:
        return scorer.main()
    finally:
        sys.argv = previous_argv


def main() -> int:
    started = time.time()
    started_at = utc_now()
    input_uri = os.environ["INPUT_URI"]
    output_prefix = os.environ["OUTPUT_PREFIX"].rstrip("/")
    run_id = os.getenv("RUN_ID") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    task_index, task_count, task_attempt = task_context()
    task_name = f"task-{task_index:05d}"
    task_prefix = join_uri(output_prefix, "runs", run_id, "tasks", task_name)

    task_root = WORK_ROOT / task_name
    input_path = task_root / "input.csv"
    shard_input_path = task_root / "input_shard.csv"
    output_path = task_root / f"{task_name}_scores.csv"
    raw_dir = task_root / "raw"
    manifest_path = task_root / f"{task_name}_manifest.json"
    task_root.mkdir(parents=True, exist_ok=True)

    manifest: dict[str, object] = {
        "schema_version": 1,
        "started_at": started_at,
        "input_uri": input_uri,
        "output_prefix": output_prefix,
        "run_id": run_id,
        "task_index": task_index,
        "task_count": task_count,
        "task_attempt": task_attempt,
    }
    exit_code = 1
    try:
        log_event("download_input_started", task_index=task_index, task_count=task_count)
        download_uri(input_uri, input_path)
        fieldnames, rows = read_input(input_path)
        max_rows = positive_int_env("MAX_ROWS", 0)
        if max_rows:
            rows = rows[:max_rows]
        shard_rows = [row for ordinal, row in enumerate(rows) if ordinal % task_count == task_index]
        write_input(shard_input_path, fieldnames, shard_rows)
        manifest.update(
            {
                "input_rows_after_limit": len(rows),
                "max_rows_limit": max_rows,
                "shard_rows": len(shard_rows),
            }
        )
        log_event(
            "scoring_started",
            task_index=task_index,
            task_count=task_count,
            input_rows_after_limit=len(rows),
            shard_rows=len(shard_rows),
        )

        exit_code = run_scorer(shard_input_path, output_path, raw_dir)
        summary_path = output_path.with_name(output_path.stem + "_summary.json")
        uploaded_raw_files = upload_tree(raw_dir, join_uri(task_prefix, "raw"))
        output_uri = join_uri(task_prefix, output_path.name)
        upload_file(output_path, output_uri)
        summary_uri = ""
        if summary_path.exists():
            summary_uri = join_uri(task_prefix, summary_path.name)
            upload_file(summary_path, summary_uri)
        manifest.update(
            {
                "exit_code": exit_code,
                "output_uri": output_uri,
                "summary_uri": summary_uri,
                "raw_artifact_prefix": join_uri(task_prefix, "raw"),
                "raw_artifact_file_count": uploaded_raw_files,
            }
        )
        log_event(
            "scoring_finished",
            task_index=task_index,
            exit_code=exit_code,
            shard_rows=len(shard_rows),
            raw_artifact_file_count=uploaded_raw_files,
        )
    except Exception as exc:  # noqa: BLE001 - manifest is still useful on failures.
        manifest.update(
            {
                "exit_code": 1,
                "error_type": type(exc).__name__,
                "error": redact_configured_secret(str(exc))[:1000],
            }
        )
        log_event("scoring_failed", task_index=task_index, error_type=type(exc).__name__)
        exit_code = 1
    finally:
        manifest.update(
            {
                "finished_at": utc_now(),
                "elapsed_seconds": round(time.time() - started, 2),
            }
        )
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        upload_file(manifest_path, join_uri(task_prefix, manifest_path.name))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
