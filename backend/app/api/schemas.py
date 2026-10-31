"""Pydantic v2 response/request schemas shared by the Phase 1 routers."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.db.models import ProgrammeLevel, Role, Shift


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class InstitutionSummary(ORM):
    id: int
    code: str
    name: str


class UserOut(ORM):
    id: int
    email: str
    full_name: str | None
    role: Role
    institution: InstitutionSummary | None
    student_apaar_id: str | None
    active: bool


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until the access token expires
    user: UserOut


class RefreshRequest(BaseModel):
    refresh_token: str


class ProgrammeOut(ORM):
    id: int
    canonical_code: str
    name: str
    level: ProgrammeLevel
    duration_years: int


class InstitutionProgrammeOut(ORM):
    id: int
    academic_year: str
    shift: Shift
    approved_intake: int
    declared_enrolment: int | None
    programme: ProgrammeOut


class InstitutionOut(ORM):
    id: int
    code: str
    name: str
    state: str
    district: str | None
    type: str | None
    approved_flag: bool


class InstitutionDetail(InstitutionOut):
    programmes: list[InstitutionProgrammeOut]
