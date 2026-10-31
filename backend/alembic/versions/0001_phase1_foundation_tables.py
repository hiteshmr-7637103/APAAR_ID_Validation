"""phase1 foundation tables

Revision ID: 0001
Revises:
Create Date: 2026-10-31 13:18:30.121611
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "institutions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("district", sa.String(length=64), nullable=True),
        sa.Column("type", sa.String(length=64), nullable=True),
        sa.Column("approved_flag", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )
    op.create_table(
        "programmes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("canonical_code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "level",
            sa.Enum(
                "UG",
                "PG",
                "DIPLOMA",
                "PGDM",
                "MCA",
                "OTHER",
                name="programme_level",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("duration_years", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("canonical_code"),
    )
    op.create_table(
        "institution_programmes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("institution_id", sa.Integer(), nullable=False),
        sa.Column("programme_id", sa.Integer(), nullable=False),
        sa.Column("academic_year", sa.String(length=9), nullable=False),
        sa.Column("approved_intake", sa.Integer(), nullable=False),
        sa.Column("declared_enrolment", sa.Integer(), nullable=True),
        sa.Column(
            "shift",
            sa.Enum(
                "REGULAR",
                "SECOND",
                "PART_TIME",
                name="shift",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["institution_id"], ["institutions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["programme_id"],
            ["programmes.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "institution_id",
            "programme_id",
            "academic_year",
            "shift",
            name="uq_inst_prog_year_shift",
        ),
    )
    op.create_index(
        op.f("ix_institution_programmes_institution_id"),
        "institution_programmes",
        ["institution_id"],
        unique=False,
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "role",
            sa.Enum(
                "AICTE_ADMIN",
                "AICTE_REVIEWER",
                "INST_MAKER",
                "INST_CHECKER",
                "VERIFIER",
                "STUDENT",
                name="user_role",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=False,
        ),
        sa.Column("institution_id", sa.Integer(), nullable=True),
        sa.Column("student_apaar_id", sa.String(length=12), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role NOT IN ('INST_MAKER', 'INST_CHECKER') OR institution_id IS NOT NULL",
            name="ck_users_institution_role_has_institution",
        ),
        sa.ForeignKeyConstraint(
            ["institution_id"],
            ["institutions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_index(op.f("ix_users_institution_id"), "users", ["institution_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_users_institution_id"), table_name="users")
    op.drop_table("users")
    op.drop_index(
        op.f("ix_institution_programmes_institution_id"), table_name="institution_programmes"
    )
    op.drop_table("institution_programmes")
    op.drop_table("programmes")
    op.drop_table("institutions")
