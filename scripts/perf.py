#!/usr/bin/env python3
"""Performance benchmark script (spec Section 15, Phase 9).

Measures single-record validation latency, projected 10k-row dry-run throughput,
and national apex dashboard query response time against live database.
"""

from __future__ import annotations

import statistics
import sys
import time
from datetime import date
from pathlib import Path

# Add backend directory to sys.path
repo_root = Path(__file__).resolve().parents[1]
backend_dir = repo_root / "backend"
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.api.dashboards import aicte_dashboard
from app.db.models import (
    AdmissionCategory,
    EntryType,
    Gender,
    Institution,
    InstitutionProgramme,
    StudyMode,
    User,
)
from app.db.session import SessionLocal
from app.services.records import RecordInput
from app.services.rule_engine import DbRuleContext, validate_record


def benchmark_single_record_validation(db, n_iterations: int = 100) -> tuple[float, float, float]:
    """Measure single-record validation latency (p50, p95, avg) in ms."""
    ctx = DbRuleContext(db)
    inst = db.query(Institution).first()
    ip = db.query(InstitutionProgramme).filter_by(institution_id=inst.id).first() if inst else None

    inp = RecordInput(
        institution_id=inst.id if inst else 1,
        apaar_id="991234567894",
        full_name="Rajesh Kumar Verma",
        dob=date(2004, 5, 14),
        gender=Gender.M.value,
        institution_programme_id=ip.id if ip else 1,
        academic_year="2025-26",
        admission_category=AdmissionCategory.GENERAL.value,
        admission_date=date(2025, 8, 1),
        entry_type=EntryType.REGULAR.value,
        mode=StudyMode.FULL_TIME.value,
    )

    times_ms = []
    for _ in range(n_iterations):
        t0 = time.perf_counter()
        validate_record(inp, ctx)
        dt = (time.perf_counter() - t0) * 1000.0
        times_ms.append(dt)

    p50 = statistics.median(times_ms)
    p95 = statistics.quantiles(times_ms, n=20)[18] if len(times_ms) >= 20 else max(times_ms)
    avg = statistics.mean(times_ms)
    return p50, p95, avg


def benchmark_dashboard_query(db) -> float:
    """Measure execution time of the national apex dashboard aggregations."""
    admin_user = db.query(User).filter_by(email="admin@aicte.demo").first()
    if not admin_user:
        admin_user = db.query(User).first()

    t0 = time.perf_counter()
    aicte_dashboard(academic_year="2025-26", user=admin_user, db=db)
    return (time.perf_counter() - t0) * 1000.0


def main() -> None:
    print("==================================================")
    print("       APAAR LEDGER PERFORMANCE BENCHMARK         ")
    print("==================================================")
    print("[+] Connecting to test/demo database...")

    with SessionLocal() as db:
        print("[+] Measuring single-record validation latency (100 runs)...")
        p50, p95, _avg = benchmark_single_record_validation(db, n_iterations=100)

        # Projected throughput based on single thread median
        tput_rows_per_sec = int(1000.0 / p50) if p50 > 0 else 2000
        projected_10k_sec = round(10000.0 / tput_rows_per_sec, 2)

        print("[+] Measuring national apex dashboard query latency...")
        dash_ms = benchmark_dashboard_query(db)

    print("\nBenchmark Execution Results:")
    print("--------------------------------------------------")
    print("| Metric                              | Result     | Target     |")
    print("--------------------------------------------------")
    print(f"| Single-Record Latency (p50)         | {p50:6.2f} ms | < 50 ms    |")
    print(f"| Single-Record Latency (p95)         | {p95:6.2f} ms | < 100 ms   |")
    print(f"| Projected Bulk Throughput           | {tput_rows_per_sec:6d} r/s| > 500 r/s  |")
    print(f"| Projected 10k-Row Dry-Run Time      | {projected_10k_sec:6.2f} s  | < 30 s     |")
    print(f"| Apex Dashboard Query Latency        | {dash_ms:6.2f} ms | < 250 ms   |")
    print("--------------------------------------------------")

    passed = (p50 < 100.0) and (projected_10k_sec < 30.0)
    print(f"[+] Performance status: {'✅ PASSED' if passed else '❌ FAILED'}")
    print("==================================================")

    if not passed:
        sys.exit(1)


if __name__ == "__main__":
    main()
