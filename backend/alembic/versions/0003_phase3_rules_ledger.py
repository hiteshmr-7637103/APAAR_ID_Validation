"""phase3 rule engine and seat ledger

- student_records.id_document_note_enc: Fernet-encrypted passport / ID document note that
  FR-12 requires for foreign nationals (not in spec 7.1; see docs/decisions.md).
- Indexes the Seat Ledger and case upserts use.
- A partial unique index guarantees at most one OPEN case per dedupe key
  (cases.subject_ids_json->>'key'), so concurrent submissions cannot open twin cases.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-31 15:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("student_records", sa.Column("id_document_note_enc", sa.Text(), nullable=True))
    op.create_index(
        "ix_seat_ledger_entries_ip_key",
        "seat_ledger_entries",
        ["institution_programme_id", "ledger_key"],
    )
    op.create_index("ix_seat_ledger_entries_record", "seat_ledger_entries", ["student_record_id"])
    op.create_index("ix_validation_results_record", "validation_results", ["student_record_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_cases_open_key ON cases ((subject_ids_json->>'key')) "
        "WHERE status = 'OPEN' AND subject_ids_json ? 'key'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_cases_open_key")
    op.drop_index("ix_validation_results_record", table_name="validation_results")
    op.drop_index("ix_seat_ledger_entries_record", table_name="seat_ledger_entries")
    op.drop_index("ix_seat_ledger_entries_ip_key", table_name="seat_ledger_entries")
    op.drop_column("student_records", "id_document_note_enc")
