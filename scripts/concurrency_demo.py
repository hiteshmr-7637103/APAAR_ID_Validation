#!/usr/bin/env python3
"""Seat Ledger Concurrency Race Demonstration CLI (spec Section 13).

Launches 50 parallel racing threads against an atomic seat ledger bucket
with exactly 5 seats remaining, demonstrating zero lost updates and strict
capacity enforcement via PostgreSQL FOR UPDATE row-level locking.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add backend directory to sys.path
repo_root = Path(__file__).resolve().parents[1]
backend_dir = repo_root / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.api.demo import demo_concurrency_test
from app.db.models import User
from app.db.session import SessionLocal


def main() -> None:
    print("==================================================")
    print("   SEAT LEDGER CONCURRENCY STRESS BENCHMARK        ")
    print("==================================================")
    print("[+] Initializing bucket with 5 seats available...")
    print("[+] Arming 50 concurrent admission worker threads...")

    with SessionLocal() as db:
        admin_user = db.query(User).filter_by(email="admin@aicte.demo").first()
        if not admin_user:
            admin_user = db.query(User).first()

        result = demo_concurrency_test(user=admin_user, db=db)

    print("\nBenchmark Execution Results:")
    print(f"  - Concurrent Race Threads:       {result['concurrent_attempts']}")
    print(f"  - Initial Seats Available:       {result['seats_remaining_before_race']}")
    print(f"  - Clean Admissions Allocated:   {result['clean_admissions']}")
    print(f"  - Flagged Over-Cap Admissions:   {result['flagged_over_cap']}")
    print(f"  - Final Used Balance:            {result['final_used']}")
    print(f"  - Zero Lost Updates Invariant:   {result['no_lost_updates']}")
    print(f"  - Concurrency Status:            {result['status']}")
    print("==================================================")

    if result["status"] != "PASSED" or not result["no_lost_updates"]:
        print("[-] Concurrency test FAILED!", file=sys.stderr)
        sys.exit(1)
    print("[+] Concurrency test PASSED strict atomic verification!\n")


if __name__ == "__main__":
    main()
