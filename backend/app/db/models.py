"""SQLAlchemy models (spec section 7.1).

Phase 1: institutions, programmes, institution_programmes, users.
Phase 2: every remaining core table from 7.1 (students, records, versions, trust, uploads,
Seat Ledger, validation, cases, ML feedback, quota rules, API keys, notifications, consent,
verification, signing keys, revocations, audit log) so later phases need no schema churn,
plus two Phase 2 additions outside 7.1 (see docs/decisions.md):
`mock_apaar_registry` (the MOCK APAAR registry) and `ground_truth_labels` (synthetic
scenario labels for evaluation).

Columns ending in `_enc` hold Fernet ciphertext (FR-38, app/core/crypto.py).
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Role(enum.StrEnum):
    """RBAC roles (spec section 4)."""

    AICTE_ADMIN = "AICTE_ADMIN"
    AICTE_REVIEWER = "AICTE_REVIEWER"
    INST_MAKER = "INST_MAKER"
    INST_CHECKER = "INST_CHECKER"
    VERIFIER = "VERIFIER"
    STUDENT = "STUDENT"


class ProgrammeLevel(enum.StrEnum):
    UG = "UG"
    PG = "PG"
    DIPLOMA = "DIPLOMA"
    PGDM = "PGDM"
    MCA = "MCA"
    OTHER = "OTHER"


class Shift(enum.StrEnum):
    REGULAR = "REGULAR"
    SECOND = "SECOND"
    PART_TIME = "PART_TIME"


class Gender(enum.StrEnum):
    M = "M"
    F = "F"
    O = "O"  # noqa: E741  (other / not stated)


class AdmissionCategory(enum.StrEnum):
    GENERAL = "GENERAL"
    SC = "SC"
    ST = "ST"
    OBC = "OBC"
    EWS = "EWS"
    TFW = "TFW"
    NRI = "NRI"
    FN = "FN"
    OCI = "OCI"
    PIO = "PIO"
    CIWGC = "CIWGC"
    OTHER = "OTHER"


class EntryType(enum.StrEnum):
    REGULAR = "REGULAR"
    LATERAL = "LATERAL"


class StudyMode(enum.StrEnum):
    FULL_TIME = "FULL_TIME"
    PART_TIME = "PART_TIME"


class LedgerKey(enum.StrEnum):
    """Seat buckets (spec 7.3)."""

    APPROVED = "APPROVED"
    SUPERNUMERARY_FN_OCI_PIO_CIWGC = "SUPERNUMERARY_FN_OCI_PIO_CIWGC"
    SUPERNUMERARY_TFW = "SUPERNUMERARY_TFW"
    NRI = "NRI"
    LATERAL = "LATERAL"


class RecordStatus(enum.StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    ACTIVE = "ACTIVE"
    NEEDS_CORRECTION = "NEEDS_CORRECTION"
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"
    GRADUATED = "GRADUATED"
    DROPPED = "DROPPED"


class ConfirmationStatus(enum.StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    DISPUTED = "DISPUTED"
    EXPIRED = "EXPIRED"


class BatchStatus(enum.StrEnum):
    DRAFT = "DRAFT"
    DRY_RUN = "DRY_RUN"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class BatchSource(enum.StrEnum):
    """How a batch entered the system: a CSV upload, the single-record form, or a
    correction of a NEEDS_CORRECTION record (Phase 4)."""

    UPLOAD = "UPLOAD"
    FORM = "FORM"
    CORRECTION = "CORRECTION"


class Severity(enum.StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class CaseType(enum.StrEnum):
    RULE_WARNING = "RULE_WARNING"
    SEAT_OVER_CAP = "SEAT_OVER_CAP"
    DUP_EXACT_CROSS = "DUP_EXACT_CROSS"
    DUP_FUZZY = "DUP_FUZZY"
    ANOMALY = "ANOMALY"
    RING = "RING"
    TIMELINE = "TIMELINE"
    STUDENT_DISPUTE = "STUDENT_DISPUTE"


class CaseVisibility(enum.StrEnum):
    INSTITUTION = "INSTITUTION"
    AICTE_ONLY = "AICTE_ONLY"


class CaseStatus(enum.StrEnum):
    OPEN = "OPEN"
    CONFIRMED = "CONFIRMED"
    DISMISSED = "DISMISSED"
    ESCALATED = "ESCALATED"
    RESOLVED = "RESOLVED"


class ConsentStatus(enum.StrEnum):
    PENDING = "PENDING"
    GRANTED = "GRANTED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"


def _enum(e: type[enum.Enum], name: str) -> Enum:
    # VARCHAR + CHECK constraint rather than a native PG enum: easier to extend in migrations.
    return Enum(e, name=name, native_enum=False, create_constraint=True, length=32)


class Institution(Base):
    __tablename__ = "institutions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    state: Mapped[str] = mapped_column(String(64), nullable=False)
    district: Mapped[str | None] = mapped_column(String(64))
    type: Mapped[str | None] = mapped_column(String(64))
    approved_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    programmes: Mapped[list[InstitutionProgramme]] = relationship(back_populates="institution")


class Programme(Base):
    __tablename__ = "programmes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    level: Mapped[ProgrammeLevel] = mapped_column(_enum(ProgrammeLevel, "programme_level"))
    duration_years: Mapped[int] = mapped_column(Integer, nullable=False)


class InstitutionProgramme(Base):
    __tablename__ = "institution_programmes"
    __table_args__ = (
        UniqueConstraint(
            "institution_id",
            "programme_id",
            "academic_year",
            "shift",
            name="uq_inst_prog_year_shift",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    institution_id: Mapped[int] = mapped_column(
        ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    programme_id: Mapped[int] = mapped_column(ForeignKey("programmes.id"), nullable=False)
    academic_year: Mapped[str] = mapped_column(String(9), nullable=False)  # e.g. "2025-26"
    approved_intake: Mapped[int] = mapped_column(Integer, nullable=False)
    # Headcount reported at approval time (used by the coverage meter, FR-21).
    declared_enrolment: Mapped[int | None] = mapped_column(Integer)
    shift: Mapped[Shift] = mapped_column(
        _enum(Shift, "shift"), nullable=False, default=Shift.REGULAR
    )

    institution: Mapped[Institution] = relationship(back_populates="programmes")
    programme: Mapped[Programme] = relationship()


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        # Institution users must belong to an institution (row-level scoping depends on it).
        CheckConstraint(
            "role NOT IN ('INST_MAKER', 'INST_CHECKER') OR institution_id IS NOT NULL",
            name="ck_users_institution_role_has_institution",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(255))
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(_enum(Role, "user_role"), nullable=False)
    institution_id: Mapped[int | None] = mapped_column(
        ForeignKey("institutions.id"), nullable=True, index=True
    )
    # Link to the student's APAAR ID (present in the MOCK APAAR registry).
    student_apaar_id: Mapped[str | None] = mapped_column(String(12))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    institution: Mapped[Institution | None] = relationship()


def _now() -> Any:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


# --------------------------------------------------------------------------- config / access


class QuotaRule(Base):
    """Versioned rules-as-data (spec 7.3). The active row per key is the highest version
    whose `effective_from` is not in the future."""

    __tablename__ = "quota_rules"
    __table_args__ = (UniqueConstraint("key", "version", name="uq_quota_rules_key_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    value_json: Mapped[Any] = mapped_column(JSONB, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    effective_from: Mapped[datetime] = _now()
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    institution_id: Mapped[int] = mapped_column(
        ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    label: Mapped[str | None] = mapped_column(String(128))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _now()


# --------------------------------------------------------------------------- students


class Student(Base):
    """A person as known to AICTE, keyed on APAAR ID.

    `apaar_id` is nullable only so that a submission with a missing APAAR ID can still be
    stored and flagged (FR-6/FR-7); PostgreSQL UNIQUE allows several NULLs.
    `name_norm` / `name_phonetic` are plaintext INSIDE the AICTE trust boundary (needed for
    blocking) and are never returned by institution-facing APIs.
    """

    __tablename__ = "students"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    apaar_id: Mapped[str | None] = mapped_column(String(12), unique=True)
    name_enc: Mapped[str] = mapped_column(Text, nullable=False)
    name_norm: Mapped[str] = mapped_column(String(255), nullable=False)
    name_phonetic: Mapped[str | None] = mapped_column(String(255))
    dob: Mapped[date | None] = mapped_column(Date)
    gender: Mapped[Gender | None] = mapped_column(_enum(Gender, "gender"))
    identity_token: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    created_at: Mapped[datetime] = _now()

    records: Mapped[list[StudentRecord]] = relationship(back_populates="student")


class UploadBatch(Base):
    __tablename__ = "upload_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    institution_id: Mapped[int] = mapped_column(
        ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    maker_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    checker_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[BatchStatus] = mapped_column(
        _enum(BatchStatus, "batch_status"), nullable=False, default=BatchStatus.DRAFT
    )
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    warning_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = _now()
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    # Phase 4 additions (migration 0004, see docs/decisions.md).
    source: Mapped[str] = mapped_column(String(16), nullable=False, default=BatchSource.UPLOAD)
    filename: Mapped[str | None] = mapped_column(String(255))
    academic_year: Mapped[str | None] = mapped_column(String(9))
    total_chunks: Mapped[int | None] = mapped_column(Integer)
    content_sha256: Mapped[str | None] = mapped_column(String(64))
    # Assembled CSV, Fernet-encrypted at rest (it holds phone/email/address, FR-38).
    csv_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    dry_run_json: Mapped[Any | None] = mapped_column(JSONB)
    submit_json: Mapped[Any | None] = mapped_column(JSONB)
    justification: Mapped[str | None] = mapped_column(Text)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dry_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UploadChunk(Base):
    __tablename__ = "upload_chunks"
    __table_args__ = (UniqueConstraint("upload_id", "chunk_index", name="uq_upload_chunk"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    upload_id: Mapped[int] = mapped_column(
        ForeignKey("upload_batches.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    received_at: Mapped[datetime] = _now()


class StudentRecord(Base):
    """One enrolment of a student at an institution-programme (spec 7.1).

    `contact_token` is the HMAC token of the phone number; `email_token` and
    `address_token` are the matching tokens for email and address (FR-37).
    """

    __tablename__ = "student_records"
    __table_args__ = (
        CheckConstraint("trust_tier BETWEEN 0 AND 3", name="ck_student_records_trust_tier"),
        Index("ix_student_records_institution_status", "institution_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    institution_id: Mapped[int] = mapped_column(
        ForeignKey("institutions.id", ondelete="CASCADE"), nullable=False
    )
    institution_programme_id: Mapped[int] = mapped_column(
        ForeignKey("institution_programmes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    student_college_id: Mapped[str | None] = mapped_column(String(64))
    admission_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    admission_category: Mapped[AdmissionCategory] = mapped_column(
        _enum(AdmissionCategory, "admission_category"), nullable=False
    )
    entry_type: Mapped[EntryType] = mapped_column(
        _enum(EntryType, "entry_type"), nullable=False, default=EntryType.REGULAR
    )
    mode: Mapped[StudyMode] = mapped_column(
        _enum(StudyMode, "study_mode"), nullable=False, default=StudyMode.FULL_TIME
    )
    ledger_key: Mapped[LedgerKey] = mapped_column(_enum(LedgerKey, "ledger_key"), nullable=False)
    over_cap: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    over_cap_reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    nationality: Mapped[str | None] = mapped_column(String(64))
    is_nri: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    nri_sponsor_enc: Mapped[str | None] = mapped_column(Text)
    is_oci: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    oci_number_enc: Mapped[str | None] = mapped_column(Text)
    is_foreign_national: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Passport / ID document note for foreign nationals (FR-12; Phase 3 addition, encrypted).
    id_document_note_enc: Mapped[str | None] = mapped_column(Text)
    status: Mapped[RecordStatus] = mapped_column(
        _enum(RecordStatus, "record_status"),
        nullable=False,
        default=RecordStatus.PENDING_APPROVAL,
    )
    trust_tier: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, index=True)
    phone_enc: Mapped[str | None] = mapped_column(Text)
    email_enc: Mapped[str | None] = mapped_column(Text)
    address_enc: Mapped[str | None] = mapped_column(Text)
    contact_token: Mapped[str | None] = mapped_column(String(64), index=True)
    email_token: Mapped[str | None] = mapped_column(String(64), index=True)
    address_token: Mapped[str | None] = mapped_column(String(64), index=True)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("upload_batches.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = _now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    student: Mapped[Student] = relationship(back_populates="records")
    institution_programme: Mapped[InstitutionProgramme] = relationship()


class StudentRecordVersion(Base):
    """Append-only history of a record (FR-4)."""

    __tablename__ = "student_record_versions"
    __table_args__ = (UniqueConstraint("student_record_id", "version", name="uq_record_version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_record_id: Mapped[int] = mapped_column(
        ForeignKey("student_records.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    data_json: Mapped[Any] = mapped_column(JSONB, nullable=False)
    changed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    changed_at: Mapped[datetime] = _now()
    reason: Mapped[str | None] = mapped_column(String(255))


class TrustEvent(Base):
    __tablename__ = "trust_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_record_id: Mapped[int] = mapped_column(
        ForeignKey("student_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    from_tier: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    to_tier: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    reason: Mapped[str] = mapped_column(String(255), nullable=False)
    actor: Mapped[str] = mapped_column(String(64), nullable=False)  # "user:<id>" or "system:<x>"
    ts: Mapped[datetime] = _now()


class EnrolmentConfirmation(Base):
    __tablename__ = "enrolment_confirmations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_record_id: Mapped[int] = mapped_column(
        ForeignKey("student_records.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[ConfirmationStatus] = mapped_column(
        _enum(ConfirmationStatus, "confirmation_status"),
        nullable=False,
        default=ConfirmationStatus.PENDING,
    )
    requested_at: Mapped[datetime] = _now()
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# --------------------------------------------------------------------------- Seat Ledger


class SeatLedgerEntry(Base):
    __tablename__ = "seat_ledger_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    institution_programme_id: Mapped[int] = mapped_column(
        ForeignKey("institution_programmes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ledger_key: Mapped[LedgerKey] = mapped_column(_enum(LedgerKey, "ledger_key"), nullable=False)
    delta: Mapped[int] = mapped_column(Integer, nullable=False)
    student_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("student_records.id", ondelete="SET NULL")
    )
    batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("upload_batches.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = _now()


class SeatLedgerBalance(Base):
    """excess = max(0, used - cap)."""

    __tablename__ = "seat_ledger_balances"

    institution_programme_id: Mapped[int] = mapped_column(
        ForeignKey("institution_programmes.id", ondelete="CASCADE"), primary_key=True
    )
    ledger_key: Mapped[LedgerKey] = mapped_column(_enum(LedgerKey, "ledger_key"), primary_key=True)
    used: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cap: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


# --------------------------------------------------------------------------- validation / cases


class ValidationResult(Base):
    __tablename__ = "validation_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    batch_id: Mapped[int | None] = mapped_column(
        ForeignKey("upload_batches.id", ondelete="CASCADE"), index=True
    )
    student_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("student_records.id", ondelete="CASCADE")
    )
    row_number: Mapped[int | None] = mapped_column(Integer)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[Severity] = mapped_column(_enum(Severity, "severity"), nullable=False)
    field: Mapped[str | None] = mapped_column(String(64))
    message_key: Mapped[str] = mapped_column(String(128), nullable=False)
    message_en: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_json: Mapped[Any | None] = mapped_column(JSONB)
    suggested_fix_json: Mapped[Any | None] = mapped_column(JSONB)
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resolution_note: Mapped[str | None] = mapped_column(Text)


class Case(Base):
    __tablename__ = "cases"
    __table_args__ = (Index("ix_cases_status_type_visibility", "status", "type", "visibility"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    type: Mapped[CaseType] = mapped_column(_enum(CaseType, "case_type"), nullable=False)
    visibility: Mapped[CaseVisibility] = mapped_column(
        _enum(CaseVisibility, "case_visibility"), nullable=False
    )
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=3)
    severity: Mapped[Severity] = mapped_column(
        _enum(Severity, "severity"), nullable=False, default=Severity.WARNING
    )
    status: Mapped[CaseStatus] = mapped_column(
        _enum(CaseStatus, "case_status"), nullable=False, default=CaseStatus.OPEN
    )
    # Owning institution for INSTITUTION-visible cases (scoping); NULL for national cases.
    institution_id: Mapped[int | None] = mapped_column(
        ForeignKey("institutions.id", ondelete="CASCADE"), index=True
    )
    subject_ids_json: Mapped[Any] = mapped_column(JSONB, nullable=False, default=dict)
    score: Mapped[float | None] = mapped_column(Float)
    evidence_json: Mapped[Any | None] = mapped_column(JSONB)
    explanation_json: Mapped[Any | None] = mapped_column(JSONB)
    assigned_to: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _now()


class MlFeedback(Base):
    __tablename__ = "ml_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(
        ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(32), nullable=False)
    features_json: Mapped[Any | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()


class Notification(Base):
    """MOCK email/SMS outbox shown in the UI (spec 2.2). `student_id` lets a notification
    be addressed to a student who has no login account yet."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    student_id: Mapped[int | None] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[Any] = mapped_column(JSONB, nullable=False, default=dict)
    read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _now()


# --------------------------------------------------------------------------- verification


class ConsentRequest(Base):
    __tablename__ = "consent_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True
    )
    verifier_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    requested_fields_json: Mapped[Any] = mapped_column(JSONB, nullable=False)
    status: Mapped[ConsentStatus] = mapped_column(
        _enum(ConsentStatus, "consent_status"), nullable=False, default=ConsentStatus.PENDING
    )
    granted_fields_json: Mapped[Any | None] = mapped_column(JSONB)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _now()


class VerificationEvent(Base):
    __tablename__ = "verification_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    consent_id: Mapped[int | None] = mapped_column(
        ForeignKey("consent_requests.id", ondelete="SET NULL"), index=True
    )
    verifier_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    student_id: Mapped[int | None] = mapped_column(
        ForeignKey("students.id", ondelete="CASCADE"), index=True
    )
    fields_json: Mapped[Any | None] = mapped_column(JSONB)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    signed_record_json: Mapped[Any | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()


class SigningKey(Base):
    __tablename__ = "signing_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    private_key_enc: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = _now()


class Revocation(Base):
    __tablename__ = "revocations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    record_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(255))
    revoked_at: Mapped[datetime] = _now()


class AuditLog(Base):
    """Append-only, hash-chained (FR-35; written from Phase 4)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = _now()
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    actor_role: Mapped[str | None] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    detail_json: Mapped[Any | None] = mapped_column(JSONB)
    prev_hash: Mapped[str | None] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64), nullable=False)


# --------------------------------------------------------------------------- Phase 2 additions


class MockApaarRegistry(Base):
    """MOCK APAAR registry (spec 2.2): synthetic people only. Stands in for the national
    registry, which has no public API. Accessed through `services/apaar_client.py`."""

    __tablename__ = "mock_apaar_registry"

    apaar_id: Mapped[str] = mapped_column(String(12), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    dob: Mapped[date] = mapped_column(Date, nullable=False)
    gender: Mapped[Gender] = mapped_column(_enum(Gender, "gender"), nullable=False)
    created_at: Mapped[datetime] = _now()


class GroundTruthLabel(Base):
    """Ground truth for injected synthetic scenarios (spec 10, 14). AICTE-internal: never
    exposed to institution users; used by evaluation and ML training."""

    __tablename__ = "ground_truth_labels"
    __table_args__ = (Index("ix_ground_truth_scenario_group", "scenario", "group_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    scenario: Mapped[str] = mapped_column(String(32), nullable=False)
    group_id: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str | None] = mapped_column(String(32))
    corruption_types: Mapped[Any] = mapped_column(JSONB, nullable=False, default=list)
    student_record_id: Mapped[int | None] = mapped_column(
        ForeignKey("student_records.id", ondelete="CASCADE"), index=True
    )
    student_id: Mapped[int | None] = mapped_column(ForeignKey("students.id", ondelete="CASCADE"))
    institution_id: Mapped[int | None] = mapped_column(
        ForeignKey("institutions.id", ondelete="CASCADE")
    )
    institution_programme_id: Mapped[int | None] = mapped_column(
        ForeignKey("institution_programmes.id", ondelete="CASCADE")
    )
    detail_json: Mapped[Any | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _now()
