"""Phase 6: Dashboards and Reports tests (spec 5.6, FR-21 to FR-25, spec 15).

Tests:
1. Standard CSV & JSON reports: all 7 reports, state filter, 404 for unknown.
2. AICTE national dashboard KPIs & aggregations.
3. Coverage meter calculation & color bands (RED/AMBER/GREEN).
4. Data Health Score computation (0-100, 4 components, rating).
5. Risk ranking formula (0.35 anom + 0.25 dup + 0.20 ring + 0.20 violation).
6. Institution dashboard embedded coverage & health score (no deferred).
7. RBAC isolation (institution cannot access AICTE dashboard, reports, or risk ranking).
8. SSE live event stream (initial ping, token in query param, published event delivery).
"""

from __future__ import annotations

import csv
import io

from app.core.events import event_bus
from app.db.models import Institution, Role
from app.db.session import SessionLocal
from app.services import reports

API = "/api/v1"

ALL_REPORT_NAMES = [
    "enrolment-vs-intake",
    "supernumerary-utilisation",
    "over-cap-flags",
    "cross-institution-duplicates",
    "coverage",
    "risk-ranking",
    "trust-tiers",
]


# -------------------------------------------------- 1. Reports Catalog & RBAC
def test_reports_catalog(client, auth):
    r = client.get(f"{API}/reports", headers=auth("AICTE_ADMIN"))
    assert r.status_code == 200, r.text
    data = r.json()
    assert "reports" in data
    names = [rep["name"] for rep in data["reports"]]
    assert set(names) == set(ALL_REPORT_NAMES)


def test_reports_rbac(client, auth):
    # Institution Maker cannot access regulatory reports
    r = client.get(f"{API}/reports", headers=auth("INST_MAKER"))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"

    # Institution Checker cannot access regulatory reports
    r = client.get(
        f"{API}/reports/enrolment-vs-intake?format=csv",
        headers=auth("INST_CHECKER"),
    )
    assert r.status_code == 403


def test_report_not_found(client, auth):
    r = client.get(f"{API}/reports/nonexistent-report", headers=auth("AICTE_ADMIN"))
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "REPORT_NOT_FOUND"


# -------------------------------------------------- 2. All 7 CSV & JSON Reports
def test_all_seven_csv_reports(client, auth):
    for name in ALL_REPORT_NAMES:
        r = client.get(f"{API}/reports/{name}?format=csv", headers=auth("AICTE_ADMIN"))
        assert r.status_code == 200, f"Report {name} failed: {r.text}"
        assert "text/csv" in r.headers["Content-Type"]
        assert "Content-Disposition" in r.headers
        assert f"{name}_" in r.headers["Content-Disposition"]

        # Parse CSV
        reader = csv.reader(io.StringIO(r.text))
        rows = list(reader)
        assert len(rows) >= 1, f"Report {name} produced no header row"
        assert len(rows[0]) >= 4, f"Report {name} header too short: {rows[0]}"


def test_all_seven_json_reports(client, auth):
    for name in ALL_REPORT_NAMES:
        r = client.get(f"{API}/reports/{name}?format=json", headers=auth("AICTE_ADMIN"))
        assert r.status_code == 200, f"Report {name} failed: {r.text}"
        data = r.json()
        assert data["report"] == name
        assert "headers" in data
        assert "rows" in data
        assert "total_rows" in data
        assert isinstance(data["headers"], list)
        assert isinstance(data["rows"], list)
        assert len(data["headers"]) > 0


def test_report_state_filter(client, auth):
    r = client.get(
        f"{API}/reports/coverage?format=json&state=Delhi",
        headers=auth("AICTE_ADMIN"),
    )
    assert r.status_code == 200
    data = r.json()
    assert data["state"] == "Delhi"
    for row in data["rows"]:
        # State column is index 2
        assert row[2] == "Delhi"


# -------------------------------------------------- 3. AICTE Dashboard KPIs
def test_aicte_dashboard_kpis(client, auth):
    r = client.get(f"{API}/dashboards/aicte", headers=auth("AICTE_ADMIN"))
    assert r.status_code == 200, r.text
    data = r.json()

    assert data["total_institutions"] >= 2
    assert data["total_students"] >= 0

    evi = data["enrolment_vs_intake"]
    assert "approved_intake" in evi
    assert "actual_enrolment" in evi
    assert "fill_ratio" in evi

    oc = data["over_cap"]
    assert "flagged_records" in oc
    assert "total_excess_seats" in oc

    cov = data["coverage"]
    assert "total_declared" in cov
    assert "total_received" in cov
    assert "overall_percentage" in cov
    assert cov["status_band"] in ("RED", "AMBER", "GREEN")
    assert "bands" in cov
    assert "RED" in cov["bands"]
    assert "AMBER" in cov["bands"]
    assert "GREEN" in cov["bands"]

    tt = data["trust_tiers"]
    assert "T0" in tt
    assert "T1" in tt
    assert "T2" in tt
    assert "T3" in tt
    assert "trust_ratio_t3" in tt

    cases = data["cases"]
    assert "total_open" in cases
    assert "by_type" in cases
    assert "cross_institution_duplicates" in cases

    rl = data["risk_leaderboard"]
    assert isinstance(rl, list)
    if len(rl) > 0:
        top = rl[0]
        assert "rank" in top
        assert top["rank"] == 1
        assert "risk_score" in top
        assert "components" in top
        assert "anomaly" in top["components"]
        assert "duplicate_rate" in top["components"]
        assert "ring_flags" in top["components"]
        assert "rule_violations" in top["components"]
        assert "alerts" in top


def test_aicte_dashboard_rbac(client, auth):
    r = client.get(f"{API}/dashboards/aicte", headers=auth("INST_MAKER"))
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"

    r = client.get(f"{API}/dashboards/aicte", headers=auth("INST_CHECKER"))
    assert r.status_code == 403


# -------------------------------------------------- 4. Coverage Meter
def test_coverage_endpoints(client, auth):
    # National coverage as AICTE
    r = client.get(f"{API}/dashboards/coverage", headers=auth("AICTE_ADMIN"))
    assert r.status_code == 200, r.text
    data = r.json()
    assert "total_institutions" in data
    assert "overall_coverage_percentage" in data
    assert "bands_distribution" in data
    assert "institutions" in data

    # Institution scoped coverage as INST_MAKER
    r = client.get(f"{API}/dashboards/coverage", headers=auth("INST_MAKER"))
    assert r.status_code == 200, r.text
    inst_cov = r.json()
    assert "coverage_percentage" in inst_cov
    assert inst_cov["status_band"] in ("RED", "AMBER", "GREEN")
    assert "programmes" in inst_cov
    for p in inst_cov["programmes"]:
        assert p["status_band"] in ("RED", "AMBER", "GREEN")
        assert "coverage_percentage" in p

    # Specific institution coverage
    with SessionLocal() as db:
        inst = db.query(Institution).first()
        inst_id = inst.id

    r = client.get(
        f"{API}/dashboards/institutions/{inst_id}/coverage",
        headers=auth("AICTE_ADMIN"),
    )
    assert r.status_code == 200
    assert r.json()["institution_id"] == inst_id


def test_coverage_band_rules():
    assert reports.get_coverage_band(0.0) == "RED"
    assert reports.get_coverage_band(49.9) == "RED"
    assert reports.get_coverage_band(50.0) == "AMBER"
    assert reports.get_coverage_band(89.9) == "AMBER"
    assert reports.get_coverage_band(90.0) == "GREEN"
    assert reports.get_coverage_band(100.0) == "GREEN"


# -------------------------------------------------- 5. Data Health Score
def test_data_health_score(client, auth):
    with SessionLocal() as db:
        inst = db.query(Institution).first()
        inst_id = inst.id

    r = client.get(
        f"{API}/dashboards/institutions/{inst_id}/health-score",
        headers=auth("AICTE_ADMIN"),
    )
    assert r.status_code == 200, r.text
    hs = r.json()
    assert "overall_score" in hs
    assert 0 <= hs["overall_score"] <= 100
    assert hs["rating"] in ("EXCELLENT", "GOOD", "NEEDS_ATTENTION", "POOR")
    comps = hs["components"]
    assert 0 <= comps["completeness"]["score"] <= 25
    assert 0 <= comps["validity"]["score"] <= 25
    assert 0 <= comps["timeliness"]["score"] <= 25
    assert 0 <= comps["clean_share"]["score"] <= 25


# -------------------------------------------------- 6. Risk Ranking Formula
def test_risk_ranking(client, auth):
    r = client.get(
        f"{API}/dashboards/institutions/risk-ranking",
        headers=auth("AICTE_ADMIN"),
    )
    assert r.status_code == 200, r.text
    rankings = r.json()
    assert isinstance(rankings, list)

    prev_score = 100.0
    for idx, inst in enumerate(rankings):
        assert inst["rank"] == idx + 1
        score = inst["risk_score"]
        assert 0 <= score <= 100
        # Sorted descending
        assert score <= prev_score + 1e-6
        prev_score = score

        # Check component weights
        comps = inst["components"]
        anom_w = comps["anomaly"]["weighted"]
        dup_w = comps["duplicate_rate"]["weighted"]
        ring_w = comps["ring_flags"]["weighted"]
        viol_w = comps["rule_violations"]["weighted"]
        computed_sum = round(anom_w + dup_w + ring_w + viol_w, 1)
        # Verify within roundoff tolerance
        assert abs(score - computed_sum) <= 0.2

    # Institution user cannot access risk ranking
    r = client.get(
        f"{API}/dashboards/institutions/risk-ranking",
        headers=auth("INST_MAKER"),
    )
    assert r.status_code == 403


# -------------------------------------------------- 7. Institution Dashboard Embedded
def test_institution_dashboard_embedded(client, auth):
    with SessionLocal() as db:
        inst = db.query(Institution).first()
        inst_id = inst.id

    r = client.get(
        f"{API}/dashboards/institution/{inst_id}",
        headers=auth("AICTE_ADMIN"),
    )
    assert r.status_code == 200, r.text
    data = r.json()

    # Deferred must no longer be present
    assert "deferred" not in data

    # Coverage meter and data health score must be embedded
    assert "coverage_meter" in data
    assert "data_health_score" in data
    assert data["coverage_meter"]["institution_id"] == inst_id
    assert data["data_health_score"]["institution_id"] == inst_id


# -------------------------------------------------- 8. Server-Sent Events (SSE)
def test_sse_event_stream(client, auth, tokens):
    # Test connection via Authorization header
    with client.stream(
        "GET", f"{API}/dashboards/events/stream", headers=auth("AICTE_ADMIN")
    ) as stream:
        assert stream.status_code == 200
        assert "text/event-stream" in stream.headers["Content-Type"]

        lines = []
        for line in stream.iter_lines():
            if line:
                lines.append(line)
            if len(lines) >= 2:
                break

        assert "event: ping" in lines[0]
        assert 'data: {"status": "connected"}' in lines[1]

    # Test connection via ?token=... query parameter
    aicte_token = tokens[Role.AICTE_ADMIN.value]["access_token"]
    with client.stream("GET", f"{API}/dashboards/events/stream?token={aicte_token}") as stream:
        assert stream.status_code == 200
        assert "text/event-stream" in stream.headers["Content-Type"]

    # Test event bus publishing
    q = event_bus.subscribe()
    try:
        event_bus.publish("test_event", {"message": "hello"})
        event = q.get_nowait()
        assert event["type"] == "test_event"
        assert event["data"]["message"] == "hello"
    finally:
        event_bus.unsubscribe(q)
