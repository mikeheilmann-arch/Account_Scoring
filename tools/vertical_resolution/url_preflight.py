#!/usr/bin/env python3
"""Deterministic URL preflight for CertifID prospect/account websites.

This intentionally avoids LLM judgment. It answers: can a normal HTTP client
reach a plausible website, what URL did it resolve to, and what failure bucket
should downstream scoring use?
"""

from __future__ import annotations

import argparse
import csv
import html
import re
import socket
import ssl
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from html.parser import HTMLParser
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0 Safari/537.36"
)

STRONG_PARKED_PATTERNS = [
    "domain for sale",
    "buy this domain",
    "hugedomains",
    "sedo.com",
    "sedoparking",
    "parkingcrew",
    "godaddy.com/forsale",
    "spaceship.com",
    "this domain may be for sale",
]

PLACEHOLDER_PATTERNS = [
    "future home of",
    "website coming soon",
    "this site is currently under construction",
    "site is under construction",
    "website is under construction",
]

SUSPENDED_PATTERNS = [
    "account suspended",
    "cgi-sys/suspendedpage.cgi",
    "this account has been suspended",
]


class TitleParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self.in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)

    @property
    def title(self) -> str:
        return normalize_space(html.unescape(" ".join(self.title_parts)))


@dataclass
class PreflightResult:
    source_row: str
    input_id: str
    input_name: str
    input_url: str
    status: str
    selected_url: str
    final_url: str
    http_status: str
    content_type: str
    title: str
    used_ssl_relaxed: str
    error: str


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def normalized_host(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.netloc or parsed.path.split("/", 1)[0]).lower()
    host = host.split("@")[-1].split(":", 1)[0]
    return host[4:] if host.startswith("www.") else host


def same_host_family(input_url: str, final_url: str) -> bool:
    input_host = normalized_host(input_url)
    final_host = normalized_host(final_url)
    if not input_host or not final_host:
        return True
    return input_host == final_host


def clean_input_url(raw_url: str) -> str:
    url = normalize_space(raw_url)
    if not url or url.lower() in {"(no url)", "no url", "n/a", "na", "none", "-"}:
        return ""
    url = url.strip("<>\"'")
    url = re.sub(r"^\((.*)\)$", r"\1", url)
    return url


def url_variants(raw_url: str) -> list[str]:
    cleaned = clean_input_url(raw_url)
    if not cleaned:
        return []

    if "://" not in cleaned:
        host_path = cleaned.lstrip("/")
        base = [
            f"https://{host_path}",
            f"https://www.{host_path}" if not host_path.lower().startswith("www.") else "",
            f"http://{host_path}",
            f"http://www.{host_path}" if not host_path.lower().startswith("www.") else "",
        ]
        return [u for u in base if u]

    parsed = urlparse(cleaned)
    if not parsed.netloc:
        return [cleaned]

    variants = [cleaned]
    host = parsed.netloc
    if not host.lower().startswith("www."):
        variants.append(urlunparse(parsed._replace(netloc=f"www.{host}")))
    if parsed.scheme == "https":
        variants.append(urlunparse(parsed._replace(scheme="http")))
    elif parsed.scheme == "http":
        variants.insert(0, urlunparse(parsed._replace(scheme="https")))
    return list(dict.fromkeys(variants))


def fetch_url(url: str, timeout: int, relaxed_ssl: bool) -> tuple[int, str, str, bytes]:
    context = ssl._create_unverified_context() if relaxed_ssl else None
    opener = build_opener(HTTPRedirectHandler(), HTTPSHandler(context=context))
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.8"})
    with opener.open(request, timeout=timeout) as response:
        body = response.read(200_000)
        status = getattr(response, "status", response.getcode())
        final_url = response.geturl()
        content_type = response.headers.get("content-type", "")
        return int(status), final_url, content_type, body


def classify_success(status: int, body: bytes, title: str) -> str:
    text = body[:80_000].decode("utf-8", errors="ignore").lower()
    haystack = f"{title.lower()} {text}"
    title_lower = title.lower().strip()
    if any(pattern in haystack for pattern in SUSPENDED_PATTERNS):
        return "suspended_or_inactive"
    if any(pattern in haystack for pattern in STRONG_PARKED_PATTERNS):
        return "parked_or_for_sale"
    if any(pattern in haystack for pattern in PLACEHOLDER_PATTERNS):
        return "parked_or_placeholder"
    if title_lower in {"coming soon", "under construction", "website coming soon"}:
        return "parked_or_placeholder"
    if status in {401, 403}:
        return "blocked"
    if 200 <= status < 400:
        return "ok"
    return f"http_{status}"


def classify_error(exc: BaseException) -> tuple[str, str, str]:
    if isinstance(exc, HTTPError):
        code = str(exc.code)
        if exc.code in {401, 403}:
            return "blocked", code, str(exc)
        if 500 <= exc.code <= 599:
            return "server_error", code, str(exc)
        return f"http_{exc.code}", code, str(exc)

    reason = getattr(exc, "reason", exc)
    message = str(reason)
    lower = message.lower()
    if isinstance(reason, socket.gaierror) or "name or service not known" in lower or "getaddrinfo failed" in lower:
        return "dns_error", "", message
    if isinstance(reason, socket.timeout) or "timed out" in lower or "timeout" in lower:
        return "timeout", "", message
    if isinstance(reason, ssl.SSLError) or "ssl" in lower or "certificate" in lower:
        return "ssl_error", "", message
    if isinstance(exc, URLError):
        return "url_error", "", message
    return "error", "", message


def preflight_one(source_row: str, input_id: str, name: str, raw_url: str, timeout: int) -> PreflightResult:
    variants = url_variants(raw_url)
    if not variants:
        return PreflightResult(source_row, input_id, name, raw_url, "no_url", "", "", "", "", "", "false", "No URL provided")

    errors: list[str] = []
    blocked_result: PreflightResult | None = None
    first_server_error: PreflightResult | None = None

    for relaxed_ssl in (False, True):
        for candidate in variants:
            try:
                status, final_url, content_type, body = fetch_url(candidate, timeout, relaxed_ssl)
                parser = TitleParser()
                parser.feed(body[:80_000].decode("utf-8", errors="ignore"))
                title = parser.title
                bucket = classify_success(status, body, title)
                if bucket == "ok" and not same_host_family(candidate, final_url):
                    bucket = "redirects_unrelated"
                result = PreflightResult(
                    source_row,
                    input_id,
                    name,
                    raw_url,
                    bucket,
                    candidate,
                    final_url,
                    str(status),
                    content_type,
                    title,
                    str(relaxed_ssl).lower(),
                    "",
                )
                if bucket in {"ok", "parked_or_placeholder", "parked_or_for_sale", "suspended_or_inactive", "redirects_unrelated"}:
                    return result
                if bucket == "blocked" and blocked_result is None:
                    blocked_result = result
                if bucket == "server_error" and first_server_error is None:
                    first_server_error = result
            except Exception as exc:  # noqa: BLE001 - preserve concrete error in output.
                status, http_status, error = classify_error(exc)
                errors.append(f"{candidate}: {status} {error}")
                if status == "blocked" and blocked_result is None:
                    blocked_result = PreflightResult(
                        source_row, input_id, name, raw_url, status, candidate, "", http_status, "", "", str(relaxed_ssl).lower(), error
                    )
                elif status == "server_error" and first_server_error is None:
                    first_server_error = PreflightResult(
                        source_row, input_id, name, raw_url, status, candidate, "", http_status, "", "", str(relaxed_ssl).lower(), error
                    )

    if blocked_result is not None:
        return blocked_result
    if first_server_error is not None:
        return first_server_error
    last_status = "error"
    last_http = ""
    last_error = "; ".join(errors[-4:])
    if errors:
        for marker in ("dns_error", "timeout", "ssl_error", "url_error"):
            if marker in last_error:
                last_status = marker
                break
    return PreflightResult(source_row, input_id, name, raw_url, last_status, variants[0], "", last_http, "", "", "mixed", last_error)


def read_input_rows(path: str, url_column: str, name_column: str, id_column: str) -> Iterable[tuple[str, str, str, str]]:
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if url_column not in (reader.fieldnames or []):
            raise SystemExit(f"URL column '{url_column}' not found. Available: {reader.fieldnames}")
        for index, row in enumerate(reader, start=2):
            yield (
                str(index),
                row.get(id_column, "") if id_column in row else "",
                row.get(name_column, "") if name_column in row else "",
                row.get(url_column, ""),
            )


def write_results(results: list[PreflightResult], output: str) -> None:
    fieldnames = list(asdict(results[0]).keys()) if results else list(PreflightResult.__dataclass_fields__.keys())
    out_handle = open(output, "w", newline="", encoding="utf-8") if output else sys.stdout
    try:
        writer = csv.DictWriter(out_handle, fieldnames=fieldnames)
        writer.writeheader()
        for result in results:
            writer.writerow(asdict(result))
    finally:
        if output:
            out_handle.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Preflight account website URLs before LLM scoring.")
    parser.add_argument("urls", nargs="*", help="URLs to check when --input is not supplied.")
    parser.add_argument("--input", help="CSV input path.")
    parser.add_argument("--output", help="CSV output path. Defaults to stdout.")
    parser.add_argument("--url-column", default="AccountWebsite", help="CSV column containing URLs.")
    parser.add_argument("--name-column", default="AccountName", help="CSV column containing account names.")
    parser.add_argument("--id-column", default="OpportunityId", help="Optional CSV column containing a stable row ID.")
    parser.add_argument("--timeout", type=int, default=12, help="Per-request timeout in seconds.")
    parser.add_argument("--workers", type=int, default=12, help="Parallel workers.")
    parser.add_argument("--progress-every", type=int, default=0, help="Write progress to stderr every N completed rows.")
    args = parser.parse_args()

    rows = (
        list(read_input_rows(args.input, args.url_column, args.name_column, args.id_column))
        if args.input
        else [("", "", "", u) for u in args.urls]
    )
    if not rows:
        raise SystemExit("No URLs to preflight.")

    results: list[PreflightResult] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(preflight_one, source_row, input_id, name, url, args.timeout) for source_row, input_id, name, url in rows]
        for completed, future in enumerate(as_completed(futures), start=1):
            results.append(future.result())
            if args.progress_every and completed % args.progress_every == 0:
                print(f"completed={completed}/{len(rows)}", file=sys.stderr, flush=True)

    results.sort(key=lambda r: (int(r.source_row) if r.source_row.isdigit() else 0, r.input_name.lower(), r.input_url.lower()))
    write_results(results, args.output or "")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
