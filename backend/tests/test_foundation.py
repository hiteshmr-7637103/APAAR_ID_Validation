"""Phase 1: health, OpenAPI docs, migrations + seed."""

from sqlalchemy import func, select

from app.db.models import Institution, InstitutionProgramme, Programme, Role, User
from app.db.seed import DEMO_STUDENT_APAAR_ID, seed
from app.db.session import SessionLocal
from tests.conftest import API


def test_health_is_public_and_reports_db(client):
    for path in ("/health", f"{API}/health"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.json()["status"] == "ok"
        assert r.json()["db"] == "ok"


def test_docs_load(client):
    r = client.get("/docs")
    assert r.status_code == 200
    assert "swagger" in r.text.lower()
    spec = client.get("/openapi.json").json()
    assert f"{API}/auth/login" in spec["paths"]
    assert f"{API}/auth/me" in spec["paths"]


def test_seed_contents():
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Institution)) == 3
        assert db.scalar(select(func.count()).select_from(Programme)) >= 5
        assert db.scalar(select(func.count()).select_from(InstitutionProgramme)) >= 5
        roles = set(db.scalars(select(User.role).where(User.email.like("%.demo"))))
        assert roles == set(Role)
        student = db.scalar(
            select(User).where(User.role == Role.STUDENT, User.email.like("%.demo"))
        )
        assert student.student_apaar_id == DEMO_STUDENT_APAAR_ID
        assert len(student.student_apaar_id) == 12 and student.student_apaar_id.isdigit()


def test_seed_is_idempotent():
    with SessionLocal() as db:
        created = seed(db)
    assert created == {"institutions": 0, "programmes": 0, "institution_programmes": 0, "users": 0}
