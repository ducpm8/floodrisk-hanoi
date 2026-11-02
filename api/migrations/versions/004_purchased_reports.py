"""Purchased reports table for payment tracking

Revision ID: 004
Revises: 003
Create Date: 2026-04-05

email is NOT NULL — collected at checkout for payment fallback (email delivery
when MoMo/VNPay webhook never arrives).

ward_normalized enables the background job (jobs/verify_pending_payments.py) to
look up the correct report snapshot without re-parsing location_query.

status uses 'paid' (not 'success') to match the frontend polling endpoint and
webhook handler checks.
"""

from alembic import op

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE purchased_reports (
            id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            session_id           UUID NOT NULL,
            email                VARCHAR(200) NOT NULL,
            ward_normalized      VARCHAR(100),
            location_query       TEXT,
            location_lat         DOUBLE PRECISION,
            location_lng         DOUBLE PRECISION,
            payment_gateway      VARCHAR(20) CHECK (payment_gateway IN ('momo', 'vnpay')),
            payment_id           VARCHAR(255),
            amount_vnd           INT DEFAULT 35000,
            status               VARCHAR(20) NOT NULL DEFAULT 'pending'
                                     CHECK (status IN ('pending', 'paid', 'failed')),
            failure_reason       TEXT,
            report_data_snapshot JSONB,
            paid_at              TIMESTAMPTZ,
            created_at           TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    op.execute(
        "CREATE INDEX idx_purchased_reports_session ON purchased_reports(session_id)"
    )
    op.execute(
        "CREATE INDEX idx_purchased_reports_payment ON purchased_reports(payment_id)"
    )
    # Used by Celery job: WHERE status='pending' AND created_at < NOW() - interval '60s'
    op.execute(
        "CREATE INDEX idx_purchased_reports_status ON purchased_reports(status, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_purchased_reports_status")
    op.execute("DROP INDEX IF EXISTS idx_purchased_reports_payment")
    op.execute("DROP INDEX IF EXISTS idx_purchased_reports_session")
    op.execute("DROP TABLE IF EXISTS purchased_reports")
