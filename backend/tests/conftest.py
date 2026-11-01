"""Test fixtures.

Tests run against a real PostgreSQL database given by TEST_DATABASE_URL (the
database name must end in `_test`; it is wiped and rebuilt with Alembic + seed).
In Docker Compose the `db` service creates `apaar_test` automatically.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://apaar:apaar@localhost:5432/apaar_test"
)
_db_name = make_url(TEST_DATABASE_URL).database or ""
if not _db_name.endswith("_test"):
    raise RuntimeError("Refusing to run tests: TEST_DATABASE_URL must point to a *_test database")
# Must be set before the app (and its engine) is imported.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select, text  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.models import Institution, Role, User  # noqa: E402
from app.db.seed import DEMO_USERS, seed  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]
API = settings.api_prefix

# Extra fixture account: a maker at a *different* institution, for cross-institution tests.
OTHER_MAKER_EMAIL = "maker@inst2.test"
OTHER_MAKER_INST = "DEMO-INST-002"

DEMO_ACCOUNTS: dict[Role, str] = {role: email for email, _, role, _, _ in DEMO_USERS}


@pytest.fixture(scope="session", autouse=True)
def _database():
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(cfg, "head")
    with SessionLocal() as db:
        seed(db)
        inst2 = db.scalar(select(Institution).where(Institution.code == OTHER_MAKER_INST))
        db.add(
            User(
                email=OTHER_MAKER_EMAIL,
                full_name="Other Maker",
                role=Role.INST_MAKER,
                institution_id=inst2.id,
                password_hash=hash_password(settings.demo_password),
            )
        )
        db.add(
            User(
                email="inactive@aicte.test",
                full_name="Inactive",
                role=Role.AICTE_ADMIN,
                password_hash=hash_password(settings.demo_password),
                active=False,
            )
        )
        db.commit()
    yield
    engine.dispose()


@pytest.fixture(scope="session")
def client() -> TestClient:
    return TestClient(app)


def login(client: TestClient, email: str, password: str | None = None):
    return client.post(
        f"{API}/auth/login",
        data={"username": email, "password": password or settings.demo_password},
    )


@pytest.fixture(scope="session")
def tokens(client) -> dict[str, dict]:
    """Login response per role name, plus the other-institution maker."""
    out = {}
    for role, email in DEMO_ACCOUNTS.items():
        r = login(client, email)
        assert r.status_code == 200, r.text
        out[role.value] = r.json()
    r = login(client, OTHER_MAKER_EMAIL)
    assert r.status_code == 200, r.text
    out["OTHER_MAKER"] = r.json()
    return out


@pytest.fixture(scope="session")
def auth(tokens):
    """auth("AICTE_ADMIN") -> Authorization header dict."""

    def _auth(who: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {tokens[who]['access_token']}"}

    return _auth
