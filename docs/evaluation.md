# System Evaluation & Benchmark Report

> [!IMPORTANT]
> **Synthetic Benchmark Notice (Spec Section 14):**
> The metrics presented in this evaluation report measure performance against synthetic
> national benchmarks with controlled fault injection. They validate architectural guarantees,
> algorithmic accuracy, and regulatory compliance. Real-world operational performance adapts
> continuously via active reviewer feedback and production label telemetry.

*Report generated at: `2026-11-01T18:02:43.410884+00:00`*

---

## 1. Duplicate Matcher Performance (ML-1)

LightGBM with Platt (Sigmoid) Probability Calibration evaluated on benchmark corruption types:

| Metric | In-Distribution (Train) | Held-Out (Transliterations, Day-Month) | Floor | Status |
| :--- | :--- | :--- | :--- | :--- |
| **Precision** | `0.9992` | `1.0000` | `0.8000` | ✅ PASS |
| **Recall** | `1.0000` | `1.0000` | `0.8000` | ✅ PASS |
| **F1 Score** | `0.9996` | `1.0000` | `0.8500` | ✅ PASS |
| **PR-AUC** | `1.0000` | `1.0000` | `0.8500` | ✅ PASS |
| **Brier Score** | `0.0005` | `0.0001` | `< 0.1500` | ✅ PASS |
| **Calibration Error (ECE)** | `0.0021` | `0.0024` | `< 0.1000` | ✅ PASS |

- **In-distribution corruption types:** Initials expansion, typos, word swapping, erroneous digits.
- **Held-out corruption types:** Indic transliterations, day-month swaps, compound corruptions.

---

## 2. Deterministic Rule Engine Detection Rate

Detection rate for deterministic statutory rules across injected fault vectors:

| Fault Injected | Expected Codes | Detection Status |
| :--- | :--- | :--- |
| **Invalid APAAR Checksum** | `INVALID_APAAR_FORMAT` | ✅ DETECTED |
| **Inconsistent Category / Nationality** | `CATEGORY_NATIONALITY_MISMATCH` | ✅ DETECTED |
| **Age Out of Bounds** | `AGE_OUT_OF_BOUNDS` | ✅ DETECTED |
| **Future Admission Date** | `ADMISSION_DATE_FUTURE` | ✅ DETECTED |

**Overall Fault Detection Rate:** `100.0%` (4/4 detected).

---

## 3. Anomaly and Ring Detectors (ML-2 & ML-3)

| Component | Model & Methodology | Metric Measured | Score | Regulatory Target |
| :--- | :--- | :--- | :--- | :--- |
| **Institution Anomaly Detector** | Isolation Forest (9 metrics, Z-score) | Top 10% Ranking Rate | `87.5%` | `>= 80.0%` |
| **Shared-Identity Ring Detector** | Louvain Community Detection (Bipartite) | Recall of Injected Rings | `100.0%` | `100.0%` |

---

## 4. Seat Ledger Concurrency Verification

Evaluation of atomic seat balance enforcement under parallel race conditions:

- **Contention Scenario:** Seat bucket initialized with 5 seats, 50 parallel concurrent admissions.
- **Clean Admissions:** `5` (exactly matched available capacity).
- **Flagged Over-Cap:** `45` (held for regulatory review).
- **Lost Updates:** `0` (zero lost updates).
- **Atomic Locking:** PostgreSQL FOR UPDATE row-level lock on seat_ledger_balance.

---

## 5. Performance & Scalability Benchmarks

| Benchmark Metric | Measured Latency / Throughput | Target |
| :--- | :--- | :--- |
| **Single-Record Validation (p50)** | `0.1 ms` | `< 50 ms` |
| **Single-Record Validation (p95)** | `0.2 ms` | `< 100 ms` |
| **Projected Bulk Throughput** | `8000 records/sec` | `> 500 records/sec` |
| **Projected 10k-Row Dry-Run Time** | `1.25 seconds` | `< 30 seconds` |

---

## 6. Review-Time Saved Proxy

Estimated administrative time savings enabled by automated validation and triage:

- **Total Records Analyzed:** `28`
- **Automated Clearances:** `25` (89.3%)
- **Flagged for Human Review:** `3`
- **Stated Effort Assumption:** *Manual audit takes 2.0 minutes per student record (stated estimation assumption)*
- **Estimated Human Audit Hours Saved:** **`0.8 hours`**
