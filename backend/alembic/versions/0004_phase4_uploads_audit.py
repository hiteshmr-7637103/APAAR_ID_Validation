"""phase4 upload pipeline, maker-checker and hash-chained audit

- upload_batches: source (UPLOAD | FORM | CORRECTION), file metadata, the assembled CSV
  (Fernet-encrypted, FR-38), its SHA-256, academic year, the stored dry-run summary and
  submission result (idempotent replays), justification and lifecycle timestamps.
- audit_log: append-only trigger (UPDATE/DELETE refused) and indexes; the hash chain itself
  is written by app/core/audit.py under an advisory lock.
- Indexes used by the approvals list and the institution dashboard.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-31 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    ts = sa.DateTime(timezone=True)
    op.add_column(
        "upload_batches",
        sa.Column("source", sa.String(16), nullable=False, server_default="UPLOAD"),
    )
    op.create_check_constraint(
        "ck_upload_batches_source", "upload_batches", "source IN ('UPLOAD','FORM','CORRECTION')"
    )
    op.add_column("upload_batches", sa.Column("filename", sa.String(255)))
    op.add_column("upload_batches", sa.Column("academic_year", sa.String(9)))
    op.add_column("upload_batches", sa.Column("total_chunks", sa.Integer()))
    op.add_column("upload_batches", sa.Column("content_sha256", sa.String(64)))
    op.add_column("upload_batches", sa.Column("csv_enc", sa.LargeBinary()))
    op.add_column("upload_batches", sa.Column("dry_run_json", postgresql.JSONB()))
    op.add_column("upload_batches", sa.Column("submit_json", postgresql.JSONB()))
    op.add_column("upload_batches", sa.Column("justification", sa.Text()))
    op.add_column("upload_batches", sa.Column("finalized_at", ts))
    op.add_column("upload_batches", sa.Column("dry_run_at", ts))
    op.add_column("upload_batches", sa.Column("submitted_at", ts))
    op.create_index(
        "ix_upload_batches_institution_status", "upload_batches", ["institution_id", "status"]
    )
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_entity", "audit_log", ["entity_type", "entity_id"])
    op.create_index("ix_record_versions_record", "student_record_versions", ["student_record_id"])

    # Append-only audit log (FR-35). Tamper detection does not rely on this trigger (a DB
    # owner can disable it); the hash chain is what detects changes.
    op.execute("""
        CREATE FUNCTION audit_log_append_only() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'audit_log is append-only (% refused)', TG_OP;
        END;
        $$ LANGUAGE plpgsql
        """)
    op.execute(
        "CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION audit_log_append_only()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_append_only ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_append_only()")
    op.drop_index("ix_record_versions_record", table_name="student_record_versions")
    op.drop_index("ix_audit_log_entity", table_name="audit_log")
    op.drop_index("ix_audit_log_action", table_name="audit_log")
    op.drop_index("ix_upload_batches_institution_status", table_name="upload_batches")
    for col in (
        "submitted_at",
        "dry_run_at",
        "finalized_at",
        "justification",
        "submit_json",
        "dry_run_json",
        "csv_enc",
        "content_sha256",
        "total_chunks",
        "academic_year",
        "filename",
    ):
        op.drop_column("upload_batches", col)
    op.drop_constraint("ck_upload_batches_source", "upload_batches", type_="check")
    op.drop_column("upload_batches", "source")
