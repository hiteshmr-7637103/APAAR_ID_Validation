#!/usr/bin/env python3
"""Offline Ed25519 Credential & QR Verifier CLI (FR-34, spec Section 5.7).

Verifies signed student credentials and compact QR payloads offline without
requiring real-time network connectivity or database access.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Add backend directory to path if importing from repository
repo_root = Path(__file__).resolve().parents[1]
backend_dir = repo_root / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.services.signing import verify_offline_payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Offline Ed25519 verification for APAAR credentials"
    )
    parser.add_argument("--payload", help="Signed compact payload string or JSON bundle")
    parser.add_argument("--file", type=Path, help="Path to text file containing QR code payload")
    parser.add_argument("--public-key", type=Path, help="Path to Ed25519 public key PEM file")

    args = parser.parse_args()

    payload_text = ""
    if args.payload:
        payload_text = args.payload.strip()
    elif args.file:
        if not args.file.exists():
            print(f"[-] File not found: {args.file}", file=sys.stderr)
            sys.exit(1)
        payload_text = args.file.read_text(encoding="utf-8").strip()
    else:
        print("[-] Either --payload or --file is required.", file=sys.stderr)
        sys.exit(1)

    pub_pem: str | None = None
    if args.public_key:
        if not args.public_key.exists():
            print(f"[-] Public key file not found: {args.public_key}", file=sys.stderr)
            sys.exit(1)
        pub_pem = args.public_key.read_text(encoding="utf-8")

    result = verify_offline_payload(payload_text, public_key_pem=pub_pem)

    print("==================================================")
    print("      OFFLINE ED25519 CREDENTIAL VERIFICATION      ")
    print("==================================================")
    print(f"Status:        {'[+] VALID' if result['valid'] else '[-] INVALID'}")
    print(f"Payload Type:  {result.get('payload_type', 'UNKNOWN')}")
    print(f"Signed By:     {result.get('key_id', 'N/A')}")
    print(f"Reason:        {result.get('reason', 'N/A')}")

    if result.get("claims"):
        print("\nVerified Claims:")
        for k, v in result["claims"].items():
            print(f"  - {k}: {v}")

    print("==================================================")

    if not result["valid"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
