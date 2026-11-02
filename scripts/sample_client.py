#!/usr/bin/env python3
"""Sample ERP Client Script (FR-39, spec Section 5.9).

Demonstrates automated Student Information System (SIS) / ERP integration
using an institution API key to upload student records via chunked upload,
dry-run validation, and batch submission.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def _http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    data: bytes | None = None,
) -> dict:
    req = urllib.request.Request(url, data=data, method=method)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req) as resp:
            body = resp.read()
            if not body:
                return {}
            return json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        print(f"[-] HTTP Error {exc.code} for {method} {url}: {err_body}", file=sys.stderr)
        sys.exit(1)


def upload_erp_csv(
    base_url: str,
    api_key: str,
    file_path: Path,
    academic_year: str = "2025-26",
) -> dict:
    """Upload, dry-run, and submit student records CSV using an institution API key."""
    if not file_path.exists():
        print(f"[-] File not found: {file_path}", file=sys.stderr)
        sys.exit(1)

    csv_bytes = file_path.read_bytes()
    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
    }
    api_prefix = f"{base_url.rstrip('/')}/api/v1"

    print(f"[+] Initializing ERP upload for {file_path.name} ({len(csv_bytes)} bytes)...")
    init_res = _http_request(
        f"{api_prefix}/uploads",
        method="POST",
        headers=headers,
        data=json.dumps(
            {
                "filename": file_path.name,
                "total_chunks": 1,
                "academic_year": academic_year,
            }
        ).encode("utf-8"),
    )
    upload_id = init_res["id"]
    print(f"[+] Upload session created: ID={upload_id}")

    # Upload single chunk
    print("[+] Sending file content chunk 0...")
    chunk_headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/octet-stream",
    }
    _http_request(
        f"{api_prefix}/uploads/{upload_id}/chunks/0",
        method="PUT",
        headers=chunk_headers,
        data=csv_bytes,
    )

    # Finalize
    print("[+] Finalizing upload session...")
    _http_request(
        f"{api_prefix}/uploads/{upload_id}/finalize",
        method="POST",
        headers=headers,
        data=json.dumps({"total_chunks": 1}).encode("utf-8"),
    )

    # Dry-run
    print("[+] Executing server-side pre-validation dry run...")
    dry_run_res = _http_request(
        f"{api_prefix}/uploads/{upload_id}/dry-run",
        method="POST",
        headers=headers,
        data=b"{}",
    )
    print(
        f"[+] Dry run complete: Total rows={dry_run_res['total_rows']}, "
        f"Clean={dry_run_res['clean_rows']}, "
        f"Warnings={dry_run_res['warning_rows']}, "
        f"Errors={dry_run_res['error_rows']}"
    )

    # Submit batch
    print("[+] Submitting records for checker approval...")
    submit_res = _http_request(
        f"{api_prefix}/uploads/{upload_id}/submit",
        method="POST",
        headers=headers,
        data=json.dumps(
            {
                "exclude_error_rows": True,
                "justification": "Automated ingestion via ERP integration script",
            }
        ).encode("utf-8"),
    )
    print(
        f"[+] Ingestion batch created successfully! "
        f"Batch ID={submit_res['batch_id']}, Status={submit_res['status']}"
    )
    return submit_res


def main() -> None:
    parser = argparse.ArgumentParser(description="Upload student data via APAAR Ledger API Key")
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL of APAAR API")
    parser.add_argument("--api-key", default=os.getenv("APAAR_API_KEY"), help="Institution API Key")
    parser.add_argument("--file", required=True, type=Path, help="Path to student records CSV")
    parser.add_argument("--academic-year", default="2025-26", help="Target academic year")

    args = parser.parse_args()
    if not args.api_key:
        print(
            "[-] Error: --api-key or APAAR_API_KEY environment variable is required",
            file=sys.stderr,
        )
        sys.exit(1)

    upload_erp_csv(args.url, args.api_key, args.file, args.academic_year)


if __name__ == "__main__":
    main()
