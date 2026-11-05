"""Create requests table.

Revision ID: 20261104_0001
Revises:
Create Date: 2026-11-04
"""

from alembic import op
import sqlalchemy as sa


revision = "20261104_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "requests",
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("selected_provider", sa.String(length=16), nullable=True),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("provider_response", sa.JSON(), nullable=True),
        sa.Column("provider_transaction_id", sa.Text(), nullable=True),
        sa.Column("provider_request_id", sa.Text(), nullable=True),
        sa.Column("http_status_code", sa.Integer(), nullable=True),
        sa.Column("upstream_http_status_code", sa.Integer(), nullable=True),
        sa.Column("processing_status", sa.String(length=32), nullable=False),
        sa.Column("total_latency_ms", sa.Float(), nullable=True),
        sa.Column("error_details", sa.JSON(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("request_id"),
    )
    op.create_index("ix_requests_created_at", "requests", ["created_at"])
    op.create_table(
        "provider_attempts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.String(length=36), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("upstream_status_code", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"], ["requests.request_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "request_id", "attempt_number",
            name="uq_provider_attempts_request_attempt",
        ),
    )
    op.create_index(
        "ix_provider_attempts_request_id", "provider_attempts", ["request_id"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_provider_attempts_request_id", table_name="provider_attempts"
    )
    op.drop_table("provider_attempts")
    op.drop_index("ix_requests_created_at", table_name="requests")
    op.drop_table("requests")
