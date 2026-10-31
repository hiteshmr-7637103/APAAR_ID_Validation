"""Phase 1: login, refresh, /auth/me for every role."""

import pytest

from app.core.security import create_token
from app.db.models import Role
from tests.conftest import API, DEMO_ACCOUNTS, login


@pytest.mark.parametrize("role", list(Role), ids=lambda r: r.value)
def test_every_role_can_log_in(client, role):
    r = login(client, DEMO_ACCOUNTS[role])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"] and body["refresh_token"]
    assert body["expires_in"] == 15 * 60
    assert body["user"]["role"] == role.value

    me = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200
    assert me.json()["email"] == DEMO_ACCOUNTS[role]
    assert me.json()["role"] == role.value


def test_institution_users_carry_institution_and_student_carries_apaar(tokens):
    assert tokens["INST_MAKER"]["user"]["institution"]["code"] == "DEMO-INST-001"
    assert tokens["INST_CHECKER"]["user"]["institution"]["code"] == "DEMO-INST-001"
    assert tokens["STUDENT"]["user"]["student_apaar_id"]
    assert tokens["AICTE_ADMIN"]["user"]["institution"] is None


def test_login_is_case_insensitive_on_email(client):
    assert login(client, "ADMIN@aicte.demo").status_code == 200


@pytest.mark.parametrize(
    "email,password",
    [
        ("admin@aicte.demo", "wrong-password"),
        ("nobody@nowhere.test", "whatever"),
        ("inactive@aicte.test", None),
    ],
    ids=["wrong-password", "unknown-email", "inactive-user"],
)
def test_bad_login_rejected_with_uniform_error(client, email, password):
    r = login(client, email, password)
    assert r.status_code == 401
    err = r.json()["error"]
    assert err["code"] == "AUTH_INVALID_CREDENTIALS"
    assert err["message_en"] == "Incorrect email or password."
    assert {"code", "message_en", "message_key", "details"} <= set(err)


def test_refresh_issues_new_working_tokens(client, tokens):
    r = client.post(
        f"{API}/auth/refresh", json={"refresh_token": tokens["VERIFIER"]["refresh_token"]}
    )
    assert r.status_code == 200
    new = r.json()
    me = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {new['access_token']}"})
    assert me.status_code == 200 and me.json()["role"] == "VERIFIER"


def test_access_token_cannot_be_used_to_refresh(client, tokens):
    r = client.post(
        f"{API}/auth/refresh", json={"refresh_token": tokens["VERIFIER"]["access_token"]}
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "AUTH_INVALID_REFRESH"


def test_refresh_token_cannot_be_used_as_access_token(client, tokens):
    headers = {"Authorization": f"Bearer {tokens['AICTE_ADMIN']['refresh_token']}"}
    assert client.get(f"{API}/auth/me", headers=headers).status_code == 401


@pytest.mark.parametrize(
    "token", ["garbage", "a.b.c", ""], ids=["garbage", "malformed-jwt", "empty"]
)
def test_invalid_tokens_rejected(client, token):
    r = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "AUTH_REQUIRED"


def test_token_signed_with_other_secret_rejected(client, monkeypatch):
    from app.core import security

    monkeypatch.setattr(
        security.settings, "jwt_secret", "attacker-controlled-secret-of-32-plus-bytes"
    )
    forged = create_token(user_id=1, role="AICTE_ADMIN", token_type="access")
    monkeypatch.undo()
    r = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401


def test_expired_access_token_rejected(client, monkeypatch, tokens):
    from app.core import security

    monkeypatch.setattr(security.settings, "access_token_minutes", -1)
    expired = create_token(
        user_id=tokens["AICTE_ADMIN"]["user"]["id"], role="AICTE_ADMIN", token_type="access"
    )
    monkeypatch.undo()
    r = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401


def test_role_claim_in_token_is_not_trusted(client, tokens):
    """A token claiming a higher role for a STUDENT user still gets STUDENT permissions."""
    student_id = tokens["STUDENT"]["user"]["id"]
    token = create_token(user_id=student_id, role="AICTE_ADMIN", token_type="access")
    r = client.get(f"{API}/users", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403
