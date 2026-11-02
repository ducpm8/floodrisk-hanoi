"""purchased_reports: add SePay columns (FIR-81)

Adds payment_code and sepay_transaction_id columns for SePay bank-transfer
monitor integration. Updates payment_gateway CHECK to include 'sepay'.

payment_code: short code embedded in VietQR memo (e.g. FLOOD1A2B3C4D).
  SePay extracts this from the bank transfer and sends it in webhook payload.
  UNIQUE so webhook lookups are index-only and idempotency is DB-enforced.

sepay_transaction_id: SePay's own transaction ID (from webhook 'id' field).
  UNIQUE prevents duplicate processing on SePay retries.

Revision ID: 018
Revises: 017
Create Date: 2026-05-16
"""

from alembic import op

revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # PostgreSQL auto-names the CHECK constraint as <table>_<col>_check
    op.execute("""
        ALTER TABLE purchased_reports
            DROP CONSTRAINT IF EXISTS purchased_reports_payment_gateway_check
    """)
    op.execute("""
        ALTER TABLE purchased_reports
            ADD CONSTRAINT purchased_reports_payment_gateway_check
            CHECK (payment_gateway IN ('momo', 'vnpay', 'sepay'))
    """)

    op.execute("""
        ALTER TABLE purchased_reports
            ADD COLUMN IF NOT EXISTS payment_code VARCHAR(20) UNIQUE,
            ADD COLUMN IF NOT EXISTS sepay_transaction_id VARCHAR(64) UNIQUE
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_purchased_reports_payment_code
            ON purchased_reports(payment_code)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_purchased_reports_payment_code")
    op.execute("""
        ALTER TABLE purchased_reports
            DROP COLUMN IF EXISTS sepay_transaction_id,
            DROP COLUMN IF EXISTS payment_code
    """)
    op.execute("""
        ALTER TABLE purchased_reports
            DROP CONSTRAINT IF EXISTS purchased_reports_payment_gateway_check
    """)
    op.execute("""
        ALTER TABLE purchased_reports
            ADD CONSTRAINT purchased_reports_payment_gateway_check
            CHECK (payment_gateway IN ('momo', 'vnpay'))
    """)
