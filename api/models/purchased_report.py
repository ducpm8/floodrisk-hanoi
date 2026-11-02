import uuid

from sqlalchemy import Column, DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.sql import func

from api.models.base import Base


class PurchasedReport(Base):
    """
    One record per paid search session. Guest checkout only — no user accounts.

    session_id: UUID stored in the user's localStorage. New device = new session = pay again.
    email: collected at checkout for the payment fallback flow (email report if webhook fails).
    ward_normalized: fast lookup for the background payment verification job.
    report_data_snapshot: flood + price data frozen at purchase time (JSON).
    status: 'pending' until webhook confirms, 'paid' after confirmation, 'failed' on error.
    """

    __tablename__ = "purchased_reports"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    session_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    email = Column(String(200), nullable=False)
    ward_normalized = Column(String(100))
    location_query = Column(Text)
    location_lat = Column(Numeric(9, 6))
    location_lng = Column(Numeric(9, 6))
    payment_gateway = Column(String(20), nullable=True)
    payment_id = Column(String(255), nullable=True, index=True)
    payment_code = Column(String(20), nullable=True, unique=True, index=True)
    sepay_transaction_id = Column(String(64), nullable=True, unique=True)
    amount_vnd = Column(Integer, default=35000)
    status = Column(String(20), default="pending", nullable=False)
    failure_reason = Column(Text, nullable=True)
    report_data_snapshot = Column(JSON, nullable=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
