"""Phase 1 RBAC tests (spec sections 4, 12, 13; FR-36, NFR-1).

Positive and negative checks per role, institution row-level scoping, and a
route-inventory test that every non-public endpoint requires authentication.
"""

import re

import pytest

from app.main import PUBLIC_PATHS, app
from tests.conftest import API

ALL = ["AICTE_ADMIN", "AICTE_REVIEWER", "INST_MAKER", "INST_CHECKER", "VERIFIER", "STUDENT"]

# (method, path, roles allowed) - everything not listed as allowed must get 403.
MATRIX = [
    ("GET", f"{API}/auth/me", set(ALL)),
    ("GET", f"{API}/institutions", {"AICTE_ADMIN", "AICTE_REVIEWER", "INST_MAKER", "INST_CHECKER"}),
    ("GET", f"{API}/programmes", {"AICTE_ADMIN", "AICTE_REVIEWER", "INST_MAKER", "INST_CHECKER"}),
    ("GET", f"{API}/users", {"AICTE_ADMIN"}),
]
CASES = [(m, p, role, role in allowed) for m, p, allowed in MATRIX for role in ALL]


@pytest.mark.parametrize(
    "method,path,role,allowed",
    CASES,
    ids=[f"{m} {p.removeprefix(API)} as {r} -> {'allow' if a else 'deny'}" for m, p, r, a in CASES],
)
def test_role_matrix(client, auth, method, path, role, allowed):
    r = client.request(method, path, headers=auth(role))
    if allowed:
        assert r.status_code == 200, r.text
    else:
        assert r.status_code == 403, r.text
        assert r.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.parametrize("method,path", [(m, p) for m, p, _ in MATRIX])
def test_unauthenticated_requests_get_401(client, method, path):
    r = client.request(method, path)
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "AUTH_REQUIRED"


def test_every_non_public_route_requires_auth(client):
    """Route inventory: no endpoint (other than the public allow-list) is reachable anonymously."""
    # Walk the OpenAPI schema (stable across FastAPI versions' internal router layout).
    checked = 0
    for route_path, operations in app.openapi()["paths"].items():
        if route_path in PUBLIC_PATHS:
            continue
        path = re.sub(r"\{[^}]+\}", "1", route_path)
        for method in operations:
            r = client.request(method.upper(), path)
            assert r.status_code == 401, f"{method.upper()} {route_path} is reachable without auth"
            checked += 1
    assert checked >= 5


# --- Institution row-level scoping -------------------------------------------------


def _inst_id(tokens, who):
    return tokens[who]["user"]["institution"]["id"]


@pytest.mark.parametrize("who", ["INST_MAKER", "INST_CHECKER", "OTHER_MAKER"])
def test_institution_users_list_only_their_own_institution(client, auth, tokens, who):
    r = client.get(f"{API}/institutions", headers=auth(who))
    assert r.status_code == 200
    assert [i["id"] for i in r.json()] == [_inst_id(tokens, who)]


@pytest.mark.parametrize("who", ["AICTE_ADMIN", "AICTE_REVIEWER"])
def test_aicte_users_see_all_institutions(client, auth, who):
    r = client.get(f"{API}/institutions", headers=auth(who))
    assert r.status_code == 200
    assert len(r.json()) == 3


def test_institution_a_cannot_read_institution_b(client, auth, tokens):
    own, other = _inst_id(tokens, "INST_MAKER"), _inst_id(tokens, "OTHER_MAKER")
    assert own != other
    ok = client.get(f"{API}/institutions/{own}", headers=auth("INST_MAKER"))
    assert ok.status_code == 200 and ok.json()["programmes"]

    for path in (f"{API}/institutions/{other}", f"{API}/institutions/{other}/programmes"):
        r = client.get(path, headers=auth("INST_MAKER"))
        # 404, not 403: another institution's existence is not disclosed.
        assert r.status_code == 404, path
        assert "DEMO-INST-002" not in r.text and "Bengaluru" not in r.text

    r = client.get(f"{API}/institutions/{own}", headers=auth("OTHER_MAKER"))
    assert r.status_code == 404


def test_aicte_can_read_any_institution(client, auth, tokens):
    other = _inst_id(tokens, "OTHER_MAKER")
    r = client.get(f"{API}/institutions/{other}/programmes", headers=auth("AICTE_REVIEWER"))
    assert r.status_code == 200 and len(r.json()) >= 1


@pytest.mark.parametrize("who", ["VERIFIER", "STUDENT"])
def test_non_staff_cannot_read_institution_detail(client, auth, tokens, who):
    r = client.get(f"{API}/institutions/{_inst_id(tokens, 'INST_MAKER')}", headers=auth(who))
    assert r.status_code == 403


def test_scope_helper_returns_nothing_for_non_staff_roles(tokens):
    """The shared scoping helper fails closed for roles without institution access."""
    from sqlalchemy import select

    from app.core.rbac import scope_to_institution
    from app.db.models import Institution, User
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        for who, expected in [
            ("VERIFIER", 0),
            ("STUDENT", 0),
            ("INST_MAKER", 1),
            ("AICTE_ADMIN", 3),
        ]:
            user = db.get(User, tokens[who]["user"]["id"])
            rows = db.scalars(scope_to_institution(select(Institution), user, Institution.id)).all()
            assert len(rows) == expected, who
